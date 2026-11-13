"""Send the audio to Hyperion.NG as a small image over its JSON API."""

from __future__ import annotations

import asyncio
import base64
import colorsys
import contextlib
from dataclasses import dataclass
import json
import logging
import time

from homeassistant.core import HomeAssistant, callback

from .const import (
    DEFAULT_HYPERION_LAYOUT,
    DOMAIN,
    HYPERION_DURATION_MS,
    HYPERION_LAYOUT_FOUR_WAY,
    HYPERION_LAYOUTS,
    HYPERION_ORIGIN,
)
from .receiver import SyncPacket
from .spectrum import HueRange

_LOGGER = logging.getLogger(__name__)

WIDTH = 32
HEIGHT = 18
CHANNELS = 16

CONNECT_TIMEOUT = 5.0
PROBE_TIMEOUT = 10.0
BACKOFF_INITIAL = 1.0
BACKOFF_MAX = 30.0

# Rejected commands are logged at most this often (seconds).
ERROR_LOG_INTERVAL = 60.0


def _bottom_up_path() -> tuple[list[tuple[int, int]], bool]:
    """Return the right half of the border from bottom centre to top centre.

    The path is mirrored left and right.
    """
    right = WIDTH - 1
    bottom = HEIGHT - 1
    path = [(x, bottom) for x in range(WIDTH // 2, WIDTH)]
    path += [(right, y) for y in range(bottom - 1, -1, -1)]
    path += [(x, 0) for x in range(right - 1, WIDTH // 2 - 1, -1)]
    return path, False


def _four_way_path() -> tuple[list[tuple[int, int]], bool]:
    """Return the top-right quarter of the border from mid-side to top centre.

    The path is mirrored left and right and top and bottom.
    """
    right = WIDTH - 1
    path = [(right, y) for y in range(HEIGHT // 2 - 1, -1, -1)]
    path += [(x, 0) for x in range(right - 1, WIDTH // 2 - 1, -1)]
    return path, True


def _channel_map(layout: str) -> list[list[int]]:
    """Return the fftResult channel for every pixel of a layout, indexed [y][x]."""
    path, mirror_vertical = (
        _four_way_path() if layout == HYPERION_LAYOUT_FOUR_WAY else _bottom_up_path()
    )
    border: dict[tuple[int, int], int] = {}
    for i, (x, y) in enumerate(path):
        channel = i * CHANNELS // len(path)
        mirrored_x = WIDTH - 1 - x
        border[(x, y)] = border[(mirrored_x, y)] = channel
        if mirror_vertical:
            mirrored_y = HEIGHT - 1 - y
            border[(x, mirrored_y)] = border[(mirrored_x, mirrored_y)] = channel
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


CHANNEL_MAPS = {layout: _channel_map(layout) for layout in HYPERION_LAYOUTS}

Colours = list[tuple[float, float, float]]


def channel_colours(hues: HueRange) -> Colours:
    """Return each channel's full-brightness RGB (0-1) along a colour range."""
    return [
        colorsys.hsv_to_rgb(hues.at(c / (CHANNELS - 1)) / 360, 1.0, 1.0) for c in range(CHANNELS)
    ]


DEFAULT_COLOURS = channel_colours(HueRange())


def render(
    fft_result: tuple[int, ...],
    layout: str = DEFAULT_HYPERION_LAYOUT,
    colours_rgb: Colours | None = None,
) -> bytes:
    """Render the 16 channels as a WIDTH x HEIGHT RGB image in a layout."""
    rgb = colours_rgb or DEFAULT_COLOURS
    colours = []
    for c in range(CHANNELS):
        r, g, b = rgb[c]
        v = fft_result[c]
        colours.append(bytes((round(r * v), round(g * v), round(b * v))))
    return b"".join(colours[c] for row in CHANNEL_MAPS[layout] for c in row)


def image_command(
    fft_result: tuple[int, ...],
    priority: int,
    layout: str = DEFAULT_HYPERION_LAYOUT,
    instances: list[int] | None = None,
    colours_rgb: Colours | None = None,
) -> dict[str, object]:
    """Build Hyperion's image command for one frame.

    Without instances, Hyperion uses its first running instance.
    """
    command: dict[str, object] = {
        "command": "image",
        "imagewidth": WIDTH,
        "imageheight": HEIGHT,
        "imagedata": base64.b64encode(render(fft_result, layout, colours_rgb)).decode("ascii"),
        "priority": priority,
        "origin": HYPERION_ORIGIN,
        "duration": HYPERION_DURATION_MS,
    }
    if instances:
        command["instance"] = list(instances)
    return command


def clear_command(priority: int, instances: list[int] | None = None) -> dict[str, object]:
    """Build Hyperion's clear command."""
    command: dict[str, object] = {"command": "clear", "priority": priority}
    if instances:
        command["instance"] = list(instances)
    return command


def login_command(token: str, tan: int = 0) -> dict[str, object]:
    """Build Hyperion's token login command."""
    return {"command": "authorize", "subcommand": "login", "token": token, "tan": tan}


class HyperionError(Exception):
    """Talking to Hyperion failed."""


class HyperionConnectError(HyperionError):
    """Hyperion could not be reached or did not answer."""


class HyperionAuthError(HyperionError):
    """Hyperion refused the token, or needs one."""


@dataclass(frozen=True, slots=True)
class HyperionInstance:
    """One Hyperion instance (an LED setup) on a server."""

    id: int
    name: str
    running: bool


async def _reply_for(reader: asyncio.StreamReader, tan: int) -> dict:
    """Read replies until the one for this tan."""
    while line := await reader.readline():
        try:
            reply = json.loads(line)
        except ValueError:
            continue
        if isinstance(reply, dict) and reply.get("tan") == tan:
            return reply
    raise HyperionConnectError("Hyperion closed the connection")


def _is_auth_error(reply: dict) -> bool:
    return "authoriz" in str(reply.get("error", "")).lower()


async def async_get_instances(host: str, port: int, token: str | None) -> list[HyperionInstance]:
    """Log in if a token is given and return the server's instances."""
    try:
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port), CONNECT_TIMEOUT
        )
    except (OSError, TimeoutError) as err:
        raise HyperionConnectError(str(err)) from err

    def send(command: dict[str, object]) -> None:
        writer.write(json.dumps(command, separators=(",", ":")).encode() + b"\n")

    try:
        async with asyncio.timeout(PROBE_TIMEOUT):
            if token:
                send(login_command(token, tan=1))
                reply = await _reply_for(reader, 1)
                if not reply.get("success"):
                    raise HyperionAuthError(str(reply.get("error")))
            send({"command": "serverinfo", "tan": 2})
            reply = await _reply_for(reader, 2)
    except TimeoutError as err:
        raise HyperionConnectError("Hyperion did not answer") from err
    except (OSError, ValueError) as err:
        raise HyperionConnectError(str(err)) from err
    finally:
        writer.close()
    if not reply.get("success"):
        if _is_auth_error(reply):
            raise HyperionAuthError(str(reply.get("error")))
        raise HyperionError(str(reply.get("error")))
    info = reply.get("info") or {}
    return [
        HyperionInstance(
            id=int(item["instance"]),
            name=str(item.get("friendly_name") or f"Instance {item['instance']}"),
            running=bool(item.get("running")),
        )
        for item in info.get("instance") or []
        if isinstance(item, dict) and "instance" in item
    ]


class HyperionOutput:
    """Keep one connection to Hyperion's JSON API and send frames while active."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        port: int,
        priority: int,
        layout: str = DEFAULT_HYPERION_LAYOUT,
        token: str | None = None,
        instances: list[int] | None = None,
        hues: HueRange | None = None,
    ) -> None:
        """Initialise; call async_start to connect."""
        self._hass = hass
        self._layout = layout
        self._colours = channel_colours(hues or HueRange())
        self._token = token or None
        self._instances = list(instances or [])
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
            self._write(clear_command(self._priority, self._instances), force=True)

    @callback
    def packet(self, packet: SyncPacket) -> None:
        """Send one frame while active, unless the previous one has not drained."""
        if not self._active:
            return
        if not self._write(
            image_command(
                packet.fft_result, self._priority, self._layout, self._instances, self._colours
            )
        ):
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
            if self._token is not None:
                # Sent before any image; a refused token shows up as a warning.
                self._write(login_command(self._token), force=True)
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
