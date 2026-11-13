"""Send the audio to Hyperion.NG as a small image over its JSON API."""

from __future__ import annotations

import asyncio
import base64
import colorsys
import contextlib
import json
import logging
import time

from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN, HYPERION_DURATION_MS, HYPERION_ORIGIN, SPECTRUM_HUE_END
from .receiver import SyncPacket

_LOGGER = logging.getLogger(__name__)

WIDTH = 16
HEIGHT = 9
CHANNELS = 16

CONNECT_TIMEOUT = 5.0
BACKOFF_INITIAL = 1.0
BACKOFF_MAX = 30.0

# Rejected commands are logged at most this often (seconds).
ERROR_LOG_INTERVAL = 60.0


def _border_path() -> list[tuple[int, int]]:
    """Return the right half of the border from bottom centre to top centre."""
    right = WIDTH - 1
    bottom = HEIGHT - 1
    path = [(x, bottom) for x in range(WIDTH // 2, WIDTH)]
    path += [(right, y) for y in range(bottom - 1, -1, -1)]
    path += [(x, 0) for x in range(right - 1, WIDTH // 2 - 1, -1)]
    return path


def _channel_map() -> list[list[int]]:
    """Return the fftResult channel for every pixel, indexed [y][x]."""
    path = _border_path()
    border: dict[tuple[int, int], int] = {}
    for i, (x, y) in enumerate(path):
        channel = i * CHANNELS // len(path)
        border[(x, y)] = channel
        border[(WIDTH - 1 - x, y)] = channel
    grid: list[list[int]] = []
    for y in range(HEIGHT):
        row: list[int] = []
        for x in range(WIDTH):
            # Project interior pixels onto the nearest edge.
            distances = {
                (0, y): x,
                (WIDTH - 1, y): WIDTH - 1 - x,
                (x, 0): y,
                (x, HEIGHT - 1): HEIGHT - 1 - y,
            }
            nearest = min(distances, key=lambda p: distances[p])
            row.append(border[nearest])
        grid.append(row)
    return grid


CHANNEL_MAP = _channel_map()

CHANNEL_RGB = [
    colorsys.hsv_to_rgb(SPECTRUM_HUE_END * c / (CHANNELS - 1) / 360, 1.0, 1.0)
    for c in range(CHANNELS)
]


def render(fft_result: tuple[int, ...]) -> bytes:
    """Render the 16 channels as a WIDTH x HEIGHT RGB image."""
    colours = []
    for c in range(CHANNELS):
        r, g, b = CHANNEL_RGB[c]
        v = fft_result[c]
        colours.append(bytes((round(r * v), round(g * v), round(b * v))))
    return b"".join(colours[c] for row in CHANNEL_MAP for c in row)


def image_command(fft_result: tuple[int, ...], priority: int) -> dict[str, object]:
    """Build Hyperion's image command for one frame."""
    return {
        "command": "image",
        "imagewidth": WIDTH,
        "imageheight": HEIGHT,
        "imagedata": base64.b64encode(render(fft_result)).decode("ascii"),
        "priority": priority,
        "origin": HYPERION_ORIGIN,
        "duration": HYPERION_DURATION_MS,
    }


def clear_command(priority: int) -> dict[str, object]:
    """Build Hyperion's clear command."""
    return {"command": "clear", "priority": priority}


class HyperionOutput:
    """Keep one connection to Hyperion's JSON API and send frames while active."""

    def __init__(self, hass: HomeAssistant, host: str, port: int, priority: int) -> None:
        """Initialise; call async_start to connect."""
        self._hass = hass
        self._host = host
        self._port = port
        self._priority = priority
        self._writer: asyncio.StreamWriter | None = None
        self._task: asyncio.Task[None] | None = None
        self._active = False
        self.sent = 0
        self.skipped = 0
        self.errors = 0
        self._last_error_log: float | None = None

    @property
    def connected(self) -> bool:
        """Return whether a connection is open."""
        return self._writer is not None

    @callback
    def async_start(self) -> None:
        """Start the connection task."""
        self._task = self._hass.async_create_background_task(
            self._connection_loop(), f"{DOMAIN} hyperion {self._host}:{self._port}"
        )

    async def async_stop(self) -> None:
        """Clear the priority if active, then close the connection."""
        self.set_active(False)
        writer = self._writer
        if writer is not None:
            with contextlib.suppress(OSError, TimeoutError):
                await asyncio.wait_for(writer.drain(), 1.0)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    @callback
    def set_active(self, active: bool) -> None:
        """Start or stop sending; stopping clears the priority."""
        if active == self._active:
            return
        self._active = active
        if not active:
            self._write(clear_command(self._priority), force=True)

    @callback
    def packet(self, packet: SyncPacket) -> None:
        """Send one frame while active, unless the previous one has not drained."""
        if not self._active:
            return
        if not self._write(image_command(packet.fft_result, self._priority)):
            self.skipped += 1
        else:
            self.sent += 1

    def _write(self, command: dict[str, object], force: bool = False) -> bool:
        writer = self._writer
        if writer is None or writer.is_closing():
            return False
        if not force and writer.transport.get_write_buffer_size() > 0:
            return False
        writer.write(json.dumps(command, separators=(",", ":")).encode() + b"\n")
        return True

    def _reply(self, line: bytes) -> None:
        """Log failed commands; successful replies are discarded."""
        try:
            reply = json.loads(line)
        except ValueError:
            return
        if not isinstance(reply, dict) or reply.get("success", True):
            return
        self.errors += 1
        now = time.monotonic()
        if self._last_error_log is not None and now - self._last_error_log < ERROR_LOG_INTERVAL:
            return
        self._last_error_log = now
        details = "; ".join(
            str(d.get("description", d))
            for d in reply.get("errorData") or []
            if isinstance(d, dict)
        )
        _LOGGER.warning(
            "Hyperion rejected %s: %s%s (%d errors so far)",
            reply.get("command", "a command"),
            reply.get("error", "unknown error"),
            f" ({details})" if details else "",
            self.errors,
        )

    async def _connection_loop(self) -> None:
        backoff = BACKOFF_INITIAL
        while True:
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._host, self._port), CONNECT_TIMEOUT
                )
            except (OSError, TimeoutError) as err:
                _LOGGER.debug(
                    "Connecting to Hyperion at %s:%s failed: %s; retrying in %.0f s",
                    self._host,
                    self._port,
                    err,
                    backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, BACKOFF_MAX)
                continue
            _LOGGER.debug("Connected to Hyperion at %s:%s", self._host, self._port)
            backoff = BACKOFF_INITIAL
            self._writer = writer
            try:
                # EOF means the server went away.
                while line := await reader.readline():
                    self._reply(line)
            except (OSError, ValueError) as err:
                _LOGGER.debug("Hyperion connection lost: %s", err)
            finally:
                self._writer = None
                writer.close()
            _LOGGER.debug("Hyperion closed the connection; reconnecting")
            await asyncio.sleep(backoff)
