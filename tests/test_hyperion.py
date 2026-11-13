"""Hyperion output against a fake JSON API server."""

from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.wled_audio_receiver.hyperion import (
    CHANNEL_MAP,
    HEIGHT,
    WIDTH,
    image_command,
    render,
)

from .conftest import FakeReceiver

SWITCH = "switch.wled_audio_reactive_receiver_music_reactive"


class FakeHyperion:
    """Accepts JSON API connections and records the commands."""

    def __init__(self) -> None:
        """Start empty."""
        self.commands: asyncio.Queue[dict] = asyncio.Queue()
        self.connections = 0
        self.writers: list[asyncio.StreamWriter] = []
        self.server: asyncio.Server | None = None
        self.port = 0

    async def start(self) -> None:
        """Listen on a free loopback port."""
        self.server = await asyncio.start_server(self._client, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self) -> None:
        """Close the server and its connections."""
        self.drop()
        assert self.server is not None
        self.server.close()
        await self.server.wait_closed()

    def drop(self) -> None:
        """Close all client connections."""
        for writer in self.writers:
            writer.close()
        self.writers.clear()

    async def next(self) -> dict:
        """Return the next command."""
        return await asyncio.wait_for(self.commands.get(), 2)

    async def _client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self.connections += 1
        self.writers.append(writer)
        try:
            while line := await reader.readline():
                command = json.loads(line)
                await self.commands.put(command)
                writer.write(b'{"success":true}\n')
        except ConnectionError, asyncio.CancelledError:
            pass


@pytest.fixture
async def hyperion(socket_enabled: None) -> FakeHyperion:
    """Run a fake Hyperion server."""
    server = FakeHyperion()
    await server.start()
    yield server
    await server.stop()


async def _wait_connected(
    runtime, connections: int = 1, server: FakeHyperion | None = None
) -> None:
    for _ in range(200):
        if runtime.hyperion.connected and (server is None or server.connections >= connections):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Hyperion did not connect")


def test_channel_map() -> None:
    """Channel 0 sits at the bottom centre, 15 at the top centre, mirrored."""
    assert len(CHANNEL_MAP) == HEIGHT
    assert all(len(row) == WIDTH for row in CHANNEL_MAP)
    bottom, top = CHANNEL_MAP[HEIGHT - 1], CHANNEL_MAP[0]
    assert bottom[WIDTH // 2 - 1] == bottom[WIDTH // 2] == 0
    assert top[WIDTH // 2 - 1] == top[WIDTH // 2] == 15
    for row in CHANNEL_MAP:
        assert row == row[::-1]
    # Channels rise along each side from bottom to top.
    right = [CHANNEL_MAP[y][WIDTH - 1] for y in range(HEIGHT - 1, -1, -1)]
    assert right == sorted(right)
    assert set(c for row in CHANNEL_MAP for c in row) == set(range(16))
    # Interior pixels take the nearest edge's channel.
    assert CHANNEL_MAP[4][1] == CHANNEL_MAP[4][0]
    assert CHANNEL_MAP[7][7] == CHANNEL_MAP[8][7]


def test_render_colours() -> None:
    """Hue steps from red (channel 0) to violet (15); brightness is the level."""
    image = render(tuple([255] + [0] * 14 + [128]))
    assert len(image) == WIDTH * HEIGHT * 3

    def pixel(x: int, y: int) -> tuple[int, ...]:
        i = (y * WIDTH + x) * 3
        return tuple(image[i : i + 3])

    assert pixel(8, HEIGHT - 1) == (255, 0, 0)
    r, g, b = pixel(8, 0)
    assert g == 0 and b == 128 and 0 < r < b  # violet at half brightness
    assert pixel(WIDTH - 1, 4) == (0, 0, 0)


def test_image_command() -> None:
    """The image command carries the documented fields."""
    command = image_command(tuple([10] * 16), 150)
    assert command["command"] == "image"
    assert command["imagewidth"] == 16
    assert command["imageheight"] == 9
    assert command["priority"] == 150
    assert command["origin"] == "WLED Audio-Reactive Receiver"
    assert command["duration"] == 500
    assert len(base64.b64decode(command["imagedata"])) == 16 * 9 * 3


async def test_images_and_clear(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, hyperion: FakeHyperion
) -> None:
    """Every packet while active becomes an image; deactivation sends clear."""
    runtime = await setup_entry(
        hyperion=True, hyperion_host="127.0.0.1", hyperion_port=hyperion.port, hyperion_priority=140
    )
    await _wait_connected(runtime)
    receiver.feed(fft=[200] * 16)
    receiver.feed(fft=[100] * 16)
    first, second = await hyperion.next(), await hyperion.next()
    assert first["command"] == second["command"] == "image"
    assert first["priority"] == 140
    assert first["imagedata"] != second["imagedata"]

    await hass.services.async_call("switch", "turn_off", {"entity_id": SWITCH}, blocking=True)
    assert await hyperion.next() == {"command": "clear", "priority": 140}

    # Nothing is sent while inactive.
    receiver.feed()
    await asyncio.sleep(0.05)
    assert hyperion.commands.empty()


async def test_clear_on_unload(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, hyperion: FakeHyperion
) -> None:
    """Unloading while active clears the priority."""
    runtime = await setup_entry(
        hyperion=True, hyperion_host="127.0.0.1", hyperion_port=hyperion.port
    )
    await _wait_connected(runtime)
    receiver.feed()
    assert (await hyperion.next())["command"] == "image"
    assert await hass.config_entries.async_unload(runtime.entry.entry_id)
    assert await hyperion.next() == {"command": "clear", "priority": 150}


async def test_reconnect(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, hyperion: FakeHyperion
) -> None:
    """A dropped connection is re-established."""
    with patch("custom_components.wled_audio_receiver.hyperion.BACKOFF_INITIAL", 0.01):
        runtime = await setup_entry(
            hyperion=True, hyperion_host="127.0.0.1", hyperion_port=hyperion.port
        )
        await _wait_connected(runtime, 1, hyperion)
        hyperion.drop()
        for _ in range(200):
            if not runtime.hyperion.connected:
                break
            await asyncio.sleep(0.01)
        await _wait_connected(runtime, 2, hyperion)
    receiver.feed()
    assert (await hyperion.next())["command"] == "image"
    assert hyperion.connections == 2


async def test_connect_retries_until_server_starts(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, socket_enabled: None
) -> None:
    """A server that is down at set-up is reached once it comes up."""
    server = FakeHyperion()
    await server.start()
    port = server.port
    await server.stop()
    with patch("custom_components.wled_audio_receiver.hyperion.BACKOFF_INITIAL", 0.01):
        runtime = await setup_entry(hyperion=True, hyperion_host="127.0.0.1", hyperion_port=port)
        await asyncio.sleep(0.05)
        assert not runtime.hyperion.connected
        server.server = await asyncio.start_server(server._client, "127.0.0.1", port)
        await _wait_connected(runtime)
    await server.stop()


async def test_skips_frames_under_back_pressure(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, hyperion: FakeHyperion
) -> None:
    """If the previous write hasn't drained, the frame is skipped."""
    runtime = await setup_entry(
        hyperion=True, hyperion_host="127.0.0.1", hyperion_port=hyperion.port
    )
    await _wait_connected(runtime)
    transport = runtime.hyperion._writer.transport
    with patch.object(transport, "get_write_buffer_size", return_value=1024):
        receiver.feed()
        receiver.feed()
    assert runtime.hyperion.skipped == 2
    assert runtime.hyperion.sent == 0
    receiver.feed()
    assert runtime.hyperion.sent == 1
    assert (await hyperion.next())["command"] == "image"


async def test_without_connection_frames_are_skipped(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, hyperion: FakeHyperion
) -> None:
    """Packets while disconnected are dropped, not queued."""
    with patch("custom_components.wled_audio_receiver.hyperion.HyperionOutput.async_start"):
        runtime = await setup_entry(
            hyperion=True, hyperion_host="127.0.0.1", hyperion_port=hyperion.port
        )
    receiver.feed()
    assert runtime.hyperion.skipped == 1
