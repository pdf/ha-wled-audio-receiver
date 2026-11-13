"""Drive Home Assistant lights from the received audio."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval

from .const import DOMAIN, LIGHT_MODE_SPLIT
from .receiver import SyncPacket
from .spectrum import HueRange

_LOGGER = logging.getLogger(__name__)

CHANNELS = 16

ALL_CHANNELS = range(CHANNELS)

DEADBAND_BRIGHTNESS = 3
DEADBAND_HUE = 10.0

RESTORE_TRANSITION = 1.0

LIGHT_DOMAIN = "light"
SERVICE_TURN_ON = "turn_on"
ATTR_BRIGHTNESS_PCT = "brightness_pct"
ATTR_HS_COLOR = "hs_color"
ATTR_TRANSITION = "transition"


@dataclass(frozen=True, slots=True)
class Bands:
    """Volume and the 16 channel levels, each 0-1."""

    level: float
    channels: tuple[float, ...] = ()


@dataclass(frozen=True, slots=True)
class LightTarget:
    """A wanted light state: brightness in percent, hue in degrees or None."""

    brightness: int
    hue: float | None


def bands(packet: SyncPacket) -> Bands:
    """Scale one packet's volume and channels to 0-1."""
    return Bands(
        level=min(max(packet.sample_smth / 255, 0.0), 1.0),
        channels=tuple(v / 255 for v in packet.fft_result),
    )


class Averager:
    """Average the frames received during one update interval."""

    def __init__(self) -> None:
        """Start empty."""
        self.clear()

    def clear(self) -> None:
        """Drop the frames collected so far."""
        self._count = 0
        self._level = 0.0
        self._channels = [0.0] * CHANNELS

    def add(self, packet: SyncPacket) -> None:
        """Add one frame."""
        b = bands(packet)
        self._count += 1
        self._level += b.level
        for i, v in enumerate(b.channels):
            self._channels[i] += v

    def take(self) -> Bands | None:
        """Return the average and start a new interval; None if no frames."""
        if self._count == 0:
            return None
        n = self._count
        result = Bands(self._level / n, tuple(v / n for v in self._channels))
        self.clear()
        return result


def target(
    b: Bands, min_brightness: float, max_brightness: float, hues: HueRange | None = None
) -> LightTarget:
    """Map averaged channels to the one light state of mix mode.

    Brightness follows the volume. The hue is the place along the colour range
    of the level-weighted mean channel, as for a split-mode light covering all
    16 channels; silence keeps the current colour (no hue).
    """
    brightness = min_brightness + b.level * (max_brightness - min_brightness)
    if not any(b.channels):
        return LightTarget(brightness=round(brightness), hue=None)
    return LightTarget(
        brightness=round(brightness), hue=spectrum_hue(ALL_CHANNELS, b.channels, hues)
    )


def split_ranges(lights: int) -> list[range]:
    """Divide the 16 channels among the lights, lowest frequencies first.

    With more than 16 lights, neighbouring lights share a channel.
    """
    if lights <= CHANNELS:
        return [range(i * CHANNELS // lights, (i + 1) * CHANNELS // lights) for i in range(lights)]
    return [range(c, c + 1) for c in (i * CHANNELS // lights for i in range(lights))]


def spectrum_hue(
    channels: range, levels: tuple[float, ...] = (), hues: HueRange | None = None
) -> float:
    """Return the hue for a channel range, from the colour range's bass to treble hue.

    With levels, the hue shifts within the range's colours towards the loudest
    channels (the level-weighted mean channel); without levels, or if all are
    silent, it is the range's centre.
    """
    weights = [levels[i] for i in channels] if levels else []
    total = sum(weights)
    if total > 0:
        position = sum(i * w for i, w in zip(channels, weights, strict=True)) / total
    else:
        position = (channels.start + channels.stop - 1) / 2
    return (hues or HueRange()).at(position / (CHANNELS - 1))


def split_targets(
    b: Bands,
    ranges: list[range],
    min_brightness: float,
    max_brightness: float,
    hues: HueRange | None = None,
) -> list[LightTarget]:
    """Map averaged channels to one light state per channel range."""
    targets = []
    for channels in ranges:
        level = sum(b.channels[i] for i in channels) / len(channels)
        brightness = min_brightness + level * (max_brightness - min_brightness)
        hue = spectrum_hue(channels, b.channels, hues)
        targets.append(LightTarget(brightness=round(brightness), hue=hue))
    return targets


def hue_distance(a: float, b: float) -> float:
    """Return the distance between two hues in degrees, 0-180."""
    d = abs(a - b) % 360
    return 360 - d if d > 180 else d


def within_deadband(previous: LightTarget | None, new: LightTarget) -> bool:
    """Return whether new is too close to previous to be worth sending."""
    if previous is None:
        return False
    if abs(new.brightness - previous.brightness) >= DEADBAND_BRIGHTNESS:
        return False
    if new.hue is None or previous.hue is None:
        return True
    return hue_distance(new.hue, previous.hue) < DEADBAND_HUE


class LightWorker:
    """Send the latest wanted state to one light; intermediate states are skipped."""

    def __init__(self, hass: HomeAssistant, entity_id: str, transition: float) -> None:
        """Initialise for one light entity."""
        self._hass = hass
        self.entity_id = entity_id
        self._transition = transition
        self._wanted: LightTarget | None = None
        self._last: LightTarget | None = None
        self._task: asyncio.Task[None] | None = None

    @property
    def busy(self) -> bool:
        """Return whether a call is running."""
        return self._task is not None and not self._task.done()

    @callback
    def submit(self, wanted: LightTarget) -> None:
        """Ask for a new state."""
        if within_deadband(self._last, wanted):
            return
        self._wanted = wanted
        self._last = wanted
        if not self.busy:
            self._task = self._hass.async_create_background_task(
                self._run(), f"{DOMAIN} light {self.entity_id}"
            )

    @callback
    def cancel(self) -> None:
        """Drop the pending state and cancel a running call."""
        self._wanted = None
        self._last = None
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        while (wanted := self._wanted) is not None:
            self._wanted = None
            data: dict[str, object] = {
                ATTR_ENTITY_ID: self.entity_id,
                ATTR_BRIGHTNESS_PCT: wanted.brightness,
                ATTR_TRANSITION: self._transition,
            }
            if wanted.hue is not None:
                data[ATTR_HS_COLOR] = (round(wanted.hue, 1), 100)
            try:
                await self._hass.services.async_call(
                    LIGHT_DOMAIN, SERVICE_TURN_ON, data, blocking=True
                )
            except HomeAssistantError as err:
                _LOGGER.debug("Updating %s failed: %s", self.entity_id, err)


class LightOutput:
    """Average frames per interval and hand the result to one worker per light."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        lights: list[str],
        min_brightness: float,
        max_brightness: float,
        update_rate: float,
        restore: bool,
        mode: str,
        hues: HueRange | None = None,
    ) -> None:
        """Initialise; nothing is sent until activated."""
        self._hass = hass
        self._interval = 1 / update_rate
        self._min = min_brightness
        self._max = max_brightness
        self._restore = restore and bool(lights)
        self._lights = list(lights)
        self._scene_id = f"{DOMAIN}_{entry_id.lower()}"
        self.workers = [LightWorker(hass, light, self._interval) for light in lights]
        self._ranges = split_ranges(len(lights)) if mode == LIGHT_MODE_SPLIT and lights else None
        self._hues = hues or HueRange()
        self._averager = Averager()
        self._active = False
        self._snapshot_taken = False
        self._unsub_timer: CALLBACK_TYPE | None = None
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

    @property
    def scene_entity_id(self) -> str:
        """Return the entity ID of the snapshot scene."""
        return f"scene.{self._scene_id}"

    @callback
    def packet(self, packet: SyncPacket) -> None:
        """Collect one frame while active."""
        if self._active:
            self._averager.add(packet)

    @callback
    def set_active(self, active: bool) -> None:
        """Start or stop driving the lights."""
        if not self.workers or active == self._active:
            return
        self._active = active
        if not active:
            self._stop_timer()
            for worker in self.workers:
                worker.cancel()
            self._averager.clear()
        self._task = self._hass.async_create_background_task(
            self._transition_to(active), f"{DOMAIN} lights active={active}"
        )

    async def _transition_to(self, active: bool) -> None:
        async with self._lock:
            if active:
                if self._restore and not self._snapshot_taken:
                    await self._snapshot()
                if self._active and self._unsub_timer is None:
                    self._averager.clear()
                    self._unsub_timer = async_track_time_interval(
                        self._hass,
                        self._tick,
                        timedelta(seconds=self._interval),
                        cancel_on_shutdown=True,
                    )
            elif self._snapshot_taken and not self._active:
                await self._restore_snapshot()

    async def _snapshot(self) -> None:
        try:
            await self._hass.services.async_call(
                "scene",
                "create",
                {"scene_id": self._scene_id, "snapshot_entities": self._lights},
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Could not snapshot the lights: %s", err)
            return
        self._snapshot_taken = True

    async def _restore_snapshot(self) -> None:
        self._snapshot_taken = False
        try:
            await self._hass.services.async_call(
                "scene",
                "turn_on",
                {ATTR_ENTITY_ID: self.scene_entity_id, ATTR_TRANSITION: RESTORE_TRANSITION},
                blocking=True,
            )
        except HomeAssistantError as err:
            _LOGGER.warning("Could not restore the lights: %s", err)

    @callback
    def _tick(self, _now: object) -> None:
        averaged = self._averager.take()
        if averaged is None:
            return
        if self._ranges is not None:
            targets = split_targets(averaged, self._ranges, self._min, self._max, self._hues)
            for worker, wanted in zip(self.workers, targets, strict=True):
                worker.submit(wanted)
            return
        wanted = target(averaged, self._min, self._max, self._hues)
        for worker in self.workers:
            worker.submit(wanted)

    @callback
    def _stop_timer(self) -> None:
        if self._unsub_timer is not None:
            self._unsub_timer()
            self._unsub_timer = None

    async def async_shutdown(self) -> None:
        """Stop everything (on unload); restore the lights if a snapshot is pending."""
        self.set_active(False)
        if self._task is not None:
            await self._task
