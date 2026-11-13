"""Band grouping, colour mapping, deadband and the light workers."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant, ServiceCall
import pytest
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed_exact,
    async_mock_service,
)

from custom_components.wled_audio_receiver.lights import (
    Averager,
    Bands,
    LightTarget,
    LightWorker,
    bands,
    hue,
    hue_distance,
    spectrum_hue,
    split_ranges,
    split_targets,
    target,
    within_deadband,
)
from custom_components.wled_audio_receiver.receiver import decode

from .conftest import FakeReceiver, make_packet

LIGHTS = ["light.one", "light.two"]


async def _advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed_exact(hass)
    await hass.async_block_till_done()


def test_band_grouping() -> None:
    """Channels 0-3, 4-10 and 11-15 make bass, mid and treble."""
    fft = [255] * 4 + [51] * 7 + [102] * 5
    b = bands(decode(make_packet(fft=fft, sample_smth=127.5)))
    assert b == Bands(
        level=0.5, bass=1.0, mid=0.2, treble=0.4, channels=tuple(v / 255 for v in fft)
    )


def test_band_grouping_boundaries() -> None:
    """Channel 3 is bass, 4 is mid, 10 is mid, 11 is treble."""

    def one(channel: int) -> Bands:
        fft = [0] * 16
        fft[channel] = 255
        return bands(decode(make_packet(fft=fft)))

    assert one(3).bass > 0 and one(3).mid == 0
    assert one(4).mid > 0 and one(4).bass == 0
    assert one(10).mid > 0 and one(10).treble == 0
    assert one(11).treble > 0 and one(11).mid == 0


def test_averager() -> None:
    """Frames in an interval are averaged; an empty interval gives None."""
    avg = Averager()
    assert avg.take() is None
    avg.add(decode(make_packet(fft=[0] * 16, sample_smth=0)))
    avg.add(decode(make_packet(fft=[255] * 16, sample_smth=255)))
    assert avg.take() == Bands(0.5, 0.5, 0.5, 0.5, (0.5,) * 16)
    assert avg.take() is None


@pytest.mark.parametrize(
    ("b", "expected"),
    [
        (Bands(1, 1, 0, 0), 0.0),
        (Bands(1, 0, 1, 0), 120.0),
        (Bands(1, 0, 0, 1), 240.0),
        (Bands(1, 1, 1, 0), 60.0),
        (Bands(1, 0, 1, 1), 180.0),
        (Bands(1, 1, 0, 1), 300.0),
    ],
)
def test_hue(b: Bands, expected: float) -> None:
    """Hue is the level-weighted circular mean of red, green and blue."""
    assert hue(b) == pytest.approx(expected, abs=1e-6)


def test_hue_undefined() -> None:
    """Silence, or equal bands, has no hue."""
    assert hue(Bands(0, 0, 0, 0)) is None
    assert hue(Bands(1, 0.5, 0.5, 0.5)) is None


def test_target_brightness() -> None:
    """Brightness runs from min at silence to max at full level."""
    assert target(Bands(0, 1, 0, 0), 10, 90).brightness == 10
    assert target(Bands(1, 1, 0, 0), 10, 90).brightness == 90
    assert target(Bands(0.5, 1, 0, 0), 10, 90) == LightTarget(50, 0.0)


@pytest.mark.parametrize(
    ("lights", "expected"),
    [
        (1, [range(0, 16)]),
        (2, [range(0, 8), range(8, 16)]),
        (3, [range(0, 5), range(5, 10), range(10, 16)]),
        (5, [range(0, 3), range(3, 6), range(6, 9), range(9, 12), range(12, 16)]),
        (16, [range(c, c + 1) for c in range(16)]),
    ],
)
def test_split_ranges(lights: int, expected: list[range]) -> None:
    """Channels are divided into contiguous ranges, lowest first, covering all 16."""
    assert split_ranges(lights) == expected


def test_split_ranges_more_lights_than_channels() -> None:
    """With more than 16 lights, neighbours share a channel and every channel is used."""
    ranges = split_ranges(20)
    assert len(ranges) == 20
    assert all(len(r) == 1 for r in ranges)
    starts = [r.start for r in ranges]
    assert starts == sorted(starts)
    assert set(starts) == set(range(16))


def test_spectrum_hue() -> None:
    """Hue runs from red at channel 0 to violet at channel 15, by range centre."""
    assert spectrum_hue(range(0, 1)) == 0
    assert spectrum_hue(range(15, 16)) == 270
    assert spectrum_hue(range(0, 16)) == 135
    assert spectrum_hue(range(0, 8)) == pytest.approx(63)


def test_split_targets() -> None:
    """Each light's brightness follows its own channels; its hue is fixed by position."""
    channels = (1.0,) * 4 + (0.0,) * 8 + (0.5,) * 4
    b = Bands(level=0.2, bass=0, mid=0, treble=0, channels=channels)
    targets = split_targets(b, split_ranges(4), 10, 90)
    assert [t.brightness for t in targets] == [90, 10, 10, 50]
    assert [t.hue for t in targets] == pytest.approx([27, 99, 171, 243])


def test_hue_distance() -> None:
    """Hue distance wraps around 360."""
    assert hue_distance(355, 5) == 10
    assert hue_distance(5, 355) == 10
    assert hue_distance(0, 180) == 180


@pytest.mark.parametrize(
    ("previous", "new", "skip"),
    [
        (None, LightTarget(50, 0), False),
        (LightTarget(50, 0), LightTarget(52, 9), True),
        (LightTarget(50, 0), LightTarget(53, 0), False),
        (LightTarget(50, 0), LightTarget(47, 0), False),
        (LightTarget(50, 0), LightTarget(50, 10), False),
        (LightTarget(50, 355), LightTarget(50, 4), True),
        (LightTarget(50, None), LightTarget(51, 100), True),
        (LightTarget(50, 100), LightTarget(51, None), True),
    ],
)
def test_deadband(previous: LightTarget | None, new: LightTarget, skip: bool) -> None:
    """Changes under 3 brightness points and 10 degrees of hue are skipped."""
    assert within_deadband(previous, new) is skip


class SlowLight:
    """A light.turn_on service that blocks until released."""

    def __init__(self, hass: HomeAssistant) -> None:
        """Register the service."""
        self.calls: list[dict] = []
        self.release = asyncio.Event()
        hass.services.async_register("light", "turn_on", self._handle)

    async def _handle(self, call: ServiceCall) -> None:
        self.calls.append(dict(call.data))
        await self.release.wait()
        self.release.clear()


async def test_worker_latest_wins(hass: HomeAssistant) -> None:
    """While a call runs, only the latest wanted state is sent next."""
    light = SlowLight(hass)
    worker = LightWorker(hass, "light.one", 0.5)
    worker.submit(LightTarget(20, 0))
    await hass.async_block_till_done(wait_background_tasks=False)
    await asyncio.sleep(0)
    worker.submit(LightTarget(40, 120))
    worker.submit(LightTarget(60, 240))
    assert len(light.calls) == 1
    light.release.set()
    await asyncio.sleep(0.01)
    assert [c["brightness_pct"] for c in light.calls] == [20, 60]
    assert light.calls[1]["hs_color"] == (240.0, 100)
    assert light.calls[1]["transition"] == 0.5
    light.release.set()
    await asyncio.sleep(0.01)
    assert not worker.busy
    assert len(light.calls) == 2


async def test_worker_cancel(hass: HomeAssistant) -> None:
    """Cancelling drops the pending state and the running call."""
    light = SlowLight(hass)
    worker = LightWorker(hass, "light.one", 0.5)
    worker.submit(LightTarget(20, 0))
    await asyncio.sleep(0)
    worker.submit(LightTarget(60, 0))
    worker.cancel()
    assert not worker.busy
    light.release.set()
    await asyncio.sleep(0.01)
    assert len(light.calls) == 1
    # After a cancel the deadband starts afresh.
    worker.submit(LightTarget(20, 0))
    await asyncio.sleep(0.01)
    assert len(light.calls) == 2
    light.release.set()
    await asyncio.sleep(0.01)


async def test_worker_without_hue(hass: HomeAssistant) -> None:
    """Without a hue only brightness is sent."""
    calls = async_mock_service(hass, "light", "turn_on")
    worker = LightWorker(hass, "light.one", 0.5)
    worker.submit(LightTarget(20, None))
    await hass.async_block_till_done()
    assert "hs_color" not in calls[0].data


async def test_lights_follow_packets(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, freezer: FrozenDateTimeFactory
) -> None:
    """Each interval the averaged frames go to every light, with a deadband."""
    calls = async_mock_service(hass, "light", "turn_on")
    await setup_entry(lights=LIGHTS, min_brightness=0, max_brightness=100, update_rate=4.0)
    receiver.feed(fft=[255] * 4 + [0] * 12, sample_smth=255)
    receiver.feed(fft=[255] * 4 + [0] * 12, sample_smth=0)
    await hass.async_block_till_done()
    await _advance(hass, freezer, 0.25)
    assert sorted(c.data["entity_id"] for c in calls) == LIGHTS
    assert calls[0].data["brightness_pct"] == 50
    assert calls[0].data["hs_color"] == (0.0, 100)
    assert calls[0].data["transition"] == 0.25

    # Within the deadband: nothing new.
    receiver.feed(fft=[255] * 4 + [0] * 12, sample_smth=133)
    await _advance(hass, freezer, 0.25)
    assert len(calls) == 2

    # No frames in an interval: nothing sent.
    await _advance(hass, freezer, 0.25)
    assert len(calls) == 2

    receiver.feed(fft=[0] * 11 + [255] * 5, sample_smth=255)
    await _advance(hass, freezer, 0.25)
    assert len(calls) == 4
    assert calls[-1].data["brightness_pct"] == 100
    assert calls[-1].data["hs_color"] == (240.0, 100)


async def test_lights_stop_when_inactive(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, freezer: FrozenDateTimeFactory
) -> None:
    """Pending light commands are cancelled when the active state ends."""
    light = SlowLight(hass)
    runtime = await setup_entry(lights=["light.one"], update_rate=4.0)
    receiver.feed(sample_smth=255)
    await _advance(hass, freezer, 0.25)
    assert len(light.calls) == 1
    assert runtime.lights.workers[0].busy
    await hass.services.async_call(
        "switch",
        "turn_off",
        {"entity_id": "switch.wled_audio_reactive_receiver_music_reactive"},
        blocking=True,
    )
    assert not runtime.lights.workers[0].busy
    receiver.feed(sample_smth=0)
    await _advance(hass, freezer, 0.25)
    assert len(light.calls) == 1


async def test_no_snapshot_by_default(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, freezer: FrozenDateTimeFactory
) -> None:
    """Without "restore lights afterwards", no scene is created or restored."""
    async_mock_service(hass, "light", "turn_on")
    created = async_mock_service(hass, "scene", "create")
    restored = async_mock_service(hass, "scene", "turn_on")
    await setup_entry(lights=LIGHTS)
    receiver.feed(sample_smth=100)
    await _advance(hass, freezer, 1.5)
    assert not created
    assert not restored


async def test_snapshot_and_restore(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, freezer: FrozenDateTimeFactory
) -> None:
    """The lights are snapshot before the first command and restored at the end."""
    order: list[str] = []

    async def record(call: ServiceCall) -> None:
        order.append(f"{call.domain}.{call.service}")

    created = async_mock_service(hass, "scene", "create")
    restored = async_mock_service(hass, "scene", "turn_on")
    hass.services.async_register("light", "turn_on", record)

    runtime = await setup_entry(lights=LIGHTS, restore=True, update_rate=4.0)
    receiver.feed(sample_smth=100)
    await hass.async_block_till_done()
    assert len(created) == 1
    assert order == [], "snapshot comes before any light command"
    assert created[0].data == {
        "scene_id": f"wled_audio_receiver_{runtime.entry.entry_id.lower()}",
        "snapshot_entities": LIGHTS,
    }
    await _advance(hass, freezer, 0.25)
    assert order.count("light.turn_on") == 2

    # Packets stop: the active state ends after 1 s and the scene is restored.
    await _advance(hass, freezer, 1.0)
    assert not runtime.gate.active
    assert len(restored) == 1
    assert restored[0].data == {
        "entity_id": f"scene.wled_audio_receiver_{runtime.entry.entry_id.lower()}",
        "transition": 1.0,
    }

    # The next active period takes a fresh snapshot.
    receiver.feed(sample_smth=100)
    await hass.async_block_till_done()
    assert len(created) == 2


async def test_split_mode(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, freezer: FrozenDateTimeFactory
) -> None:
    """In split mode each light gets its own part of the spectrum, in selection order."""
    calls = async_mock_service(hass, "light", "turn_on")
    lights = ["light.low", "light.mid", "light.high"]
    await setup_entry(
        lights=lights, light_mode="split", min_brightness=0, max_brightness=100, update_rate=4.0
    )
    # Channels 0-4 loud, 5-9 silent, 10-15 at half.
    receiver.feed(fft=[255] * 5 + [0] * 5 + [128] * 6, sample_smth=10)
    await _advance(hass, freezer, 0.25)
    by_light = {c.data["entity_id"]: c.data for c in calls}
    assert by_light["light.low"]["brightness_pct"] == 100
    assert by_light["light.mid"]["brightness_pct"] == 0
    assert by_light["light.high"]["brightness_pct"] == 50
    assert by_light["light.low"]["hs_color"] == (36.0, 100)
    assert by_light["light.mid"]["hs_color"] == (126.0, 100)
    assert by_light["light.high"]["hs_color"] == (225.0, 100)

    # Only the light whose band changed is updated.
    receiver.feed(fft=[255] * 5 + [255] * 5 + [128] * 6, sample_smth=10)
    await _advance(hass, freezer, 0.25)
    assert [c.data["entity_id"] for c in calls[3:]] == ["light.mid"]
