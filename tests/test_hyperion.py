"""Hyperion output against a fake JSON API server."""

from __future__ import annotations

import asyncio
import base64
from unittest.mock import patch

from homeassistant.core import HomeAssistant
import pytest

from custom_components.wled_audio_receiver.hyperion import (
    CHANNEL_MAPS,
    HEIGHT,
    WIDTH,
    HyperionAuthError,
    HyperionConnectError,
    HyperionInstance,
    async_get_instances,
    clear_command,
    image_command,
    login_command,
    render,
)

from .conftest import FakeReceiver
from .fake_hyperion import TOKEN, FakeHyperion, schema_errors

SWITCH = "switch.wled_audio_reactive_receiver_music_reactive"


async def _wait_connected(
    runtime, connections: int = 1, server: FakeHyperion | None = None
) -> None:
    for _ in range(200):
        if runtime.hyperion.connected and (server is None or server.connections >= connections):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Hyperion did not connect")


def _border(grid: list[list[int]]) -> list[int]:
    """Return every border pixel's channel."""
    return grid[0] + grid[-1] + [row[0] for row in grid] + [row[-1] for row in grid]


def test_image_size() -> None:
    """Both layouts fill the 32 x 18 image and use every channel on the border."""
    for grid in CHANNEL_MAPS.values():
        assert len(grid) == HEIGHT
        assert all(len(row) == WIDTH for row in grid)
        assert set(_border(grid)) == set(range(16))


def test_bottom_up_layout() -> None:
    """Channel 0 sits at the bottom centre, 15 at the top centre, mirrored left and right."""
    grid = CHANNEL_MAPS["bottom_up"]
    assert grid[HEIGHT - 1][WIDTH // 2 - 1] == grid[HEIGHT - 1][WIDTH // 2] == 0
    assert grid[0][WIDTH // 2 - 1] == grid[0][WIDTH // 2] == 15
    for row in grid:
        assert row == row[::-1]
    # Channels rise along each side from bottom to top, and along the top to the centre.
    right = [grid[y][WIDTH - 1] for y in range(HEIGHT - 1, -1, -1)]
    assert right == sorted(right)
    top = grid[0][WIDTH // 2 :]
    assert top == sorted(top, reverse=True)
    # Interior pixels take the nearest edge's channel.
    assert grid[8][1] == grid[8][0]
    assert grid[16][16] == grid[17][16]


def test_four_way_layout() -> None:
    """Channel 0 sits mid-side, 15 at the top and bottom centres, mirrored four ways."""
    grid = CHANNEL_MAPS["four_way"]
    middle = HEIGHT // 2
    for y in (middle - 1, middle):
        assert grid[y][0] == grid[y][WIDTH - 1] == 0
    for y in (0, HEIGHT - 1):
        assert grid[y][WIDTH // 2 - 1] == grid[y][WIDTH // 2] == 15
    for row in grid:
        assert row == row[::-1]
    assert grid == grid[::-1]
    # From the middle of the right side: up to the corner, then along the top to the centre.
    path = [grid[y][WIDTH - 1] for y in range(middle - 1, -1, -1)]
    path += [grid[0][x] for x in range(WIDTH - 2, WIDTH // 2 - 1, -1)]
    assert path == sorted(path)
    assert path[0] == 0 and path[-1] == 15


def _pixel(image: bytes, x: int, y: int) -> tuple[int, ...]:
    i = (y * WIDTH + x) * 3
    return tuple(image[i : i + 3])


def test_render_colours() -> None:
    """Hue steps from red (channel 0) to violet (15); brightness is the level."""
    image = render(tuple([255] + [0] * 14 + [128]))
    assert len(image) == WIDTH * HEIGHT * 3
    assert _pixel(image, WIDTH // 2, HEIGHT - 1) == (255, 0, 0)
    r, g, b = _pixel(image, WIDTH // 2, 0)
    assert g == 0 and b == 128 and 0 < r < b  # violet at half brightness
    assert _pixel(image, WIDTH - 1, HEIGHT // 2) == (0, 0, 0)


def test_render_four_way() -> None:
    """The four-way layout puts bass on the sides and treble at top and bottom."""
    image = render(tuple([255] + [0] * 14 + [128]), "four_way")
    for x, y in ((0, HEIGHT // 2), (WIDTH - 1, HEIGHT // 2 - 1)):
        assert _pixel(image, x, y) == (255, 0, 0)
    for y in (0, HEIGHT - 1):
        assert _pixel(image, WIDTH // 2, y)[2] == 128
    assert _pixel(image, WIDTH // 4, 0) == (0, 0, 0)


def test_commands_pass_hyperion_schema() -> None:
    """Image and clear commands pass Hyperion's schema validation."""
    assert schema_errors(image_command(tuple([255] * 16), 1)) == []
    assert schema_errors(image_command(tuple([0] * 16), 253)) == []
    assert schema_errors(clear_command(150)) == []
    assert schema_errors(image_command(tuple([0] * 16), 150, "four_way", [0, 2])) == []
    assert schema_errors(clear_command(150, [1])) == []
    assert schema_errors(login_command(TOKEN, tan=1)) == []
    # The validator itself catches what Hyperion rejects.
    assert schema_errors({**clear_command(150), "origin": "x"}) == ["origin: not allowed"]
    assert schema_errors({**image_command(tuple([0] * 16), 150), "origin": "x" * 21}) == [
        "origin: above maximum 20"
    ]


def test_image_command() -> None:
    """The image command carries the documented fields."""
    command = image_command(tuple([10] * 16), 150)
    assert command["command"] == "image"
    assert command["imagewidth"] == 32
    assert command["imageheight"] == 18
    assert command["priority"] == 150
    assert command["origin"] == "WLED Audio Receiver"
    assert command["duration"] == 500
    assert len(base64.b64decode(command["imagedata"])) == 32 * 18 * 3


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


async def test_rejected_commands_are_logged(
    hass: HomeAssistant,
    setup_entry,
    receiver: FakeReceiver,
    hyperion: FakeHyperion,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Error replies are logged as warnings, at most once per minute."""
    hyperion.reject_all = True
    runtime = await setup_entry(
        hyperion=True, hyperion_host="127.0.0.1", hyperion_port=hyperion.port
    )
    await _wait_connected(runtime)
    for _ in range(3):
        receiver.feed()
        await asyncio.sleep(0.02)
    for _ in range(200):
        if runtime.hyperion.errors == 3:
            break
        await asyncio.sleep(0.01)
    assert runtime.hyperion.errors == 3
    warnings = [r.getMessage() for r in caplog.records if "Hyperion rejected" in r.getMessage()]
    assert warnings == [
        "Hyperion rejected image: Invalid command (rejected by test) (1 errors so far)"
    ]
    assert runtime.hyperion.connected


async def test_four_way_layout_sent(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, hyperion: FakeHyperion
) -> None:
    """The configured layout is used for the images sent."""
    runtime = await setup_entry(
        hyperion=True,
        hyperion_host="127.0.0.1",
        hyperion_port=hyperion.port,
        hyperion_layout="four_way",
    )
    await _wait_connected(runtime)
    fft = tuple([255] + [0] * 15)
    receiver.feed(fft=list(fft))
    command = await hyperion.next()
    assert base64.b64decode(command["imagedata"]) == render(fft, "four_way")
    assert render(fft, "four_way") != render(fft, "bottom_up")


def test_instances_in_commands() -> None:
    """Instances go in an "instance" array; none means Hyperion's default."""
    assert image_command(tuple([0] * 16), 150, instances=[0, 2])["instance"] == [0, 2]
    assert "instance" not in image_command(tuple([0] * 16), 150)
    assert clear_command(150, [1]) == {"command": "clear", "priority": 150, "instance": [1]}
    assert "instance" not in clear_command(150, [])


async def test_get_instances(hyperion: FakeHyperion) -> None:
    """The probe lists the server's instances with their names and states."""
    instances = await async_get_instances("127.0.0.1", hyperion.port, None)
    assert instances == [
        HyperionInstance(0, "TV backlight", True),
        HyperionInstance(1, "Shelf strip", True),
        HyperionInstance(2, "Spare", False),
    ]


async def test_get_instances_with_token(hyperion: FakeHyperion) -> None:
    """With a token, the probe logs in first; a missing or wrong token is an auth error."""
    hyperion.token = TOKEN
    assert len(await async_get_instances("127.0.0.1", hyperion.port, TOKEN)) == 3
    assert hyperion.received[0] == login_command(TOKEN, tan=1)
    with pytest.raises(HyperionAuthError):
        await async_get_instances("127.0.0.1", hyperion.port, None)
    with pytest.raises(HyperionAuthError):
        await async_get_instances("127.0.0.1", hyperion.port, "f" * 36)


async def test_get_instances_unreachable(socket_enabled: None) -> None:
    """A closed port is a connection error."""
    server = FakeHyperion()
    await server.start()
    port = server.port
    await server.stop()
    with pytest.raises(HyperionConnectError):
        await async_get_instances("127.0.0.1", port, None)


async def test_token_and_instances_used(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, hyperion: FakeHyperion
) -> None:
    """The output logs in before sending and addresses the chosen instances."""
    hyperion.token = TOKEN
    runtime = await setup_entry(
        hyperion=True,
        hyperion_host="127.0.0.1",
        hyperion_port=hyperion.port,
        hyperion_token=TOKEN,
        hyperion_instances=[0, 1],
    )
    await _wait_connected(runtime)
    receiver.feed()
    image = await hyperion.next()
    assert image["instance"] == [0, 1]
    assert hyperion.received[0]["command"] == "authorize"
    await hass.services.async_call("switch", "turn_off", {"entity_id": SWITCH}, blocking=True)
    assert await hyperion.next() == {"command": "clear", "priority": 150, "instance": [0, 1]}
    assert hyperion.rejected == []


async def test_without_token_hyperion_rejects(
    hass: HomeAssistant,
    setup_entry,
    receiver: FakeReceiver,
    hyperion: FakeHyperion,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A Hyperion that needs a token rejects images sent without one, with a warning."""
    hyperion.token = TOKEN
    runtime = await setup_entry(
        hyperion=True, hyperion_host="127.0.0.1", hyperion_port=hyperion.port
    )
    await _wait_connected(runtime)
    receiver.feed()
    for _ in range(200):
        if runtime.hyperion.errors:
            break
        await asyncio.sleep(0.01)
    assert "Hyperion rejected image: No Authorization" in caplog.text
