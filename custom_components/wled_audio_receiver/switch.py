"""The "Music reactive" switch."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_OFF
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from . import WledAudioReceiverConfigEntry
from .const import DOMAIN, NAME


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WledAudioReceiverConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add the switch."""
    async_add_entities([MusicReactiveSwitch(entry)])


class MusicReactiveSwitch(SwitchEntity, RestoreEntity):
    """Turn the whole integration on or off; the state survives restarts."""

    _attr_has_entity_name = True
    _attr_translation_key = "music_reactive"
    _attr_should_poll = False

    def __init__(self, entry: WledAudioReceiverConfigEntry) -> None:
        """Initialise for one config entry."""
        self._runtime = entry.runtime_data
        self._attr_unique_id = f"{entry.entry_id}_music_reactive"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=entry.title,
            manufacturer=NAME,
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def is_on(self) -> bool:
        """Return the switch state."""
        return self._runtime.gate.switch_on

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose whether the outputs are being driven."""
        return {"active": self._runtime.gate.active}

    async def async_added_to_hass(self) -> None:
        """Restore the last state (on by default) and follow the active state."""
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self._runtime.gate.set_switch(last is None or last.state != STATE_OFF)
        self.async_on_remove(self._runtime.add_listener(self.async_write_ha_state))

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on."""
        self._runtime.gate.set_switch(True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off."""
        self._runtime.gate.set_switch(False)
        self.async_write_ha_state()
