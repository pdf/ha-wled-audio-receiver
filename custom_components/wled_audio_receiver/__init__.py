"""The WLED Audio-Reactive Receiver integration."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryNotReady

from .const import (
    CONF_HYPERION,
    CONF_HYPERION_HOST,
    CONF_HYPERION_LAYOUT,
    CONF_HYPERION_PORT,
    CONF_HYPERION_PRIORITY,
    CONF_LIGHT_MODE,
    CONF_LIGHTS,
    CONF_MAX_BRIGHTNESS,
    CONF_MEDIA_PLAYER,
    CONF_MIN_BRIGHTNESS,
    CONF_MULTICAST,
    CONF_PORT,
    CONF_RESTORE,
    CONF_SENDER,
    CONF_UPDATE_RATE,
    DEFAULT_HYPERION,
    DEFAULT_HYPERION_LAYOUT,
    DEFAULT_HYPERION_PORT,
    DEFAULT_HYPERION_PRIORITY,
    DEFAULT_LIGHT_MODE,
    DEFAULT_MAX_BRIGHTNESS,
    DEFAULT_MIN_BRIGHTNESS,
    DEFAULT_MULTICAST,
    DEFAULT_PORT,
    DEFAULT_RESTORE,
    DEFAULT_UPDATE_RATE,
)
from .gate import Gate
from .hyperion import HyperionOutput
from .lights import LightOutput
from .receiver import SyncPacket, SyncProtocol, async_start_receiver

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH]

type WledAudioReceiverConfigEntry = ConfigEntry[WledAudioReceiver]


@dataclass
class WledAudioReceiver:
    """Runtime state of one config entry: receiver, gate and outputs."""

    hass: HomeAssistant
    entry: ConfigEntry
    gate: Gate = field(init=False)
    lights: LightOutput = field(init=False)
    hyperion: HyperionOutput | None = field(init=False, default=None)
    transport: asyncio.DatagramTransport | None = field(init=False, default=None)
    protocol: SyncProtocol | None = field(init=False, default=None)
    _listeners: list[Callable[[], None]] = field(init=False, default_factory=list)

    def __post_init__(self) -> None:
        """Build the gate and outputs from the entry's options."""
        options = self.options
        self.gate = Gate(
            self.hass,
            options.get(CONF_MEDIA_PLAYER),
            self._active_changed,
            self._stream_lost,
        )
        self.lights = LightOutput(
            self.hass,
            self.entry.entry_id,
            options.get(CONF_LIGHTS) or [],
            options.get(CONF_MIN_BRIGHTNESS, DEFAULT_MIN_BRIGHTNESS),
            options.get(CONF_MAX_BRIGHTNESS, DEFAULT_MAX_BRIGHTNESS),
            options.get(CONF_UPDATE_RATE, DEFAULT_UPDATE_RATE),
            options.get(CONF_RESTORE, DEFAULT_RESTORE),
            options.get(CONF_LIGHT_MODE, DEFAULT_LIGHT_MODE),
        )
        if options.get(CONF_HYPERION, DEFAULT_HYPERION) and options.get(CONF_HYPERION_HOST):
            self.hyperion = HyperionOutput(
                self.hass,
                options[CONF_HYPERION_HOST],
                int(options.get(CONF_HYPERION_PORT, DEFAULT_HYPERION_PORT)),
                int(options.get(CONF_HYPERION_PRIORITY, DEFAULT_HYPERION_PRIORITY)),
                options.get(CONF_HYPERION_LAYOUT, DEFAULT_HYPERION_LAYOUT),
            )

    @property
    def options(self) -> dict[str, Any]:
        """Return the effective configuration."""
        return {**self.entry.data, **self.entry.options}

    async def async_start(self) -> None:
        """Open the receiver and start tracking."""
        options = self.options
        self.transport, self.protocol = await async_start_receiver(
            int(options.get(CONF_PORT, DEFAULT_PORT)),
            options.get(CONF_MULTICAST, DEFAULT_MULTICAST),
            options.get(CONF_SENDER) or None,
            self.packet,
        )
        self.gate.async_start()
        if self.hyperion is not None:
            self.hyperion.async_start()

    async def async_stop(self) -> None:
        """Close the receiver and stop the outputs."""
        if self.transport is not None:
            self.transport.close()
            self.transport = None
        self.gate.async_stop()
        await self.lights.async_shutdown()
        if self.hyperion is not None:
            await self.hyperion.async_stop()

    @callback
    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Register a callback for active state changes."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    @callback
    def packet(self, packet: SyncPacket) -> None:
        """Handle one accepted packet."""
        self.gate.packet_received()
        if not self.gate.active:
            return
        self.lights.packet(packet)
        if self.hyperion is not None:
            self.hyperion.packet(packet)

    @callback
    def _stream_lost(self) -> None:
        # After a gap the sender may have restarted its frame counter.
        if self.protocol is not None:
            self.protocol.frames.reset()

    @callback
    def _active_changed(self, active: bool) -> None:
        _LOGGER.debug("Active: %s", active)
        self.lights.set_active(active)
        if self.hyperion is not None:
            self.hyperion.set_active(active)
        for listener in list(self._listeners):
            listener()


async def async_setup_entry(hass: HomeAssistant, entry: WledAudioReceiverConfigEntry) -> bool:
    """Set up the integration from a config entry."""
    runtime = WledAudioReceiver(hass, entry)
    try:
        await runtime.async_start()
    except OSError as err:
        await runtime.async_stop()
        raise ConfigEntryNotReady(f"Cannot listen on UDP port: {err}") from err
    entry.runtime_data = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WledAudioReceiverConfigEntry) -> bool:
    """Unload a config entry."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop()
    return unloaded
