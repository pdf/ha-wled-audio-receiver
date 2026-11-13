"""Config and options flows for the WLED Audio-Reactive Receiver."""

from __future__ import annotations

import ipaddress
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
)
import voluptuous as vol

from .const import (
    CONF_HYPERION,
    CONF_HYPERION_HOST,
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
    DEFAULT_HYPERION_PORT,
    DEFAULT_HYPERION_PRIORITY,
    DEFAULT_LIGHT_MODE,
    DEFAULT_MAX_BRIGHTNESS,
    DEFAULT_MIN_BRIGHTNESS,
    DEFAULT_MULTICAST,
    DEFAULT_PORT,
    DEFAULT_RESTORE,
    DEFAULT_UPDATE_RATE,
    DOMAIN,
    LIGHT_MODES,
    NAME,
)


def _box(
    minimum: float, maximum: float, step: float = 1, unit: str | None = None
) -> NumberSelector:
    config = NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX)
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _schema() -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(CONF_PORT, default=DEFAULT_PORT): _box(1, 65535),
            vol.Required(CONF_MULTICAST, default=DEFAULT_MULTICAST): BooleanSelector(),
            vol.Optional(CONF_SENDER): TextSelector(),
            vol.Optional(CONF_MEDIA_PLAYER): EntitySelector(
                EntitySelectorConfig(domain="media_player")
            ),
            vol.Optional(CONF_LIGHTS): EntitySelector(
                EntitySelectorConfig(domain="light", multiple=True)
            ),
            vol.Required(CONF_LIGHT_MODE, default=DEFAULT_LIGHT_MODE): SelectSelector(
                SelectSelectorConfig(
                    options=LIGHT_MODES,
                    translation_key=CONF_LIGHT_MODE,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            vol.Required(CONF_MIN_BRIGHTNESS, default=DEFAULT_MIN_BRIGHTNESS): _box(
                0, 100, unit="%"
            ),
            vol.Required(CONF_MAX_BRIGHTNESS, default=DEFAULT_MAX_BRIGHTNESS): _box(
                0, 100, unit="%"
            ),
            vol.Required(CONF_UPDATE_RATE, default=DEFAULT_UPDATE_RATE): _box(0.5, 10, 0.5, "/s"),
            vol.Required(CONF_RESTORE, default=DEFAULT_RESTORE): BooleanSelector(),
            vol.Required(CONF_HYPERION, default=DEFAULT_HYPERION): BooleanSelector(),
            vol.Optional(CONF_HYPERION_HOST): TextSelector(),
            vol.Required(CONF_HYPERION_PORT, default=DEFAULT_HYPERION_PORT): _box(1, 65535),
            vol.Required(CONF_HYPERION_PRIORITY, default=DEFAULT_HYPERION_PRIORITY): _box(1, 253),
        }
    )


def _normalise(user_input: dict[str, Any]) -> dict[str, Any]:
    """Fill missing optional fields and convert numbers to their types."""
    data = dict(user_input)
    for key in (
        CONF_PORT,
        CONF_MIN_BRIGHTNESS,
        CONF_MAX_BRIGHTNESS,
        CONF_HYPERION_PORT,
        CONF_HYPERION_PRIORITY,
    ):
        data[key] = int(data[key])
    data[CONF_UPDATE_RATE] = float(data[CONF_UPDATE_RATE])
    for key in (CONF_SENDER, CONF_HYPERION_HOST):
        data[key] = (data.get(key) or "").strip()
    data[CONF_MEDIA_PLAYER] = data.get(CONF_MEDIA_PLAYER) or None
    data[CONF_LIGHTS] = list(data.get(CONF_LIGHTS) or [])
    return data


def _validate(data: dict[str, Any], ports_in_use: set[int]) -> dict[str, str]:
    errors: dict[str, str] = {}
    if data[CONF_PORT] in ports_in_use:
        errors[CONF_PORT] = "port_in_use"
    if data[CONF_SENDER]:
        try:
            ipaddress.IPv4Address(data[CONF_SENDER])
        except ValueError:
            errors[CONF_SENDER] = "invalid_sender"
    if data[CONF_MIN_BRIGHTNESS] > data[CONF_MAX_BRIGHTNESS]:
        errors[CONF_MIN_BRIGHTNESS] = "min_above_max"
    if data[CONF_HYPERION] and not data[CONF_HYPERION_HOST]:
        errors[CONF_HYPERION_HOST] = "hyperion_host_required"
    return errors


def _ports_in_use(entries: list[ConfigEntry], exclude: str | None = None) -> set[int]:
    return {
        int({**e.data, **e.options}.get(CONF_PORT, DEFAULT_PORT))
        for e in entries
        if e.entry_id != exclude
    }


class WledAudioReceiverConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a receiver."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> WledAudioReceiverOptionsFlow:
        """Return the options flow."""
        return WledAudioReceiverOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for all settings."""
        errors: dict[str, str] = {}
        schema = _schema()
        if user_input is not None:
            data = _normalise(user_input)
            errors = _validate(data, _ports_in_use(self._async_current_entries()))
            if not errors:
                return self.async_create_entry(title=NAME, data={}, options=data)
            schema = self.add_suggested_values_to_schema(schema, user_input)
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)


class WledAudioReceiverOptionsFlow(OptionsFlowWithReload):
    """Change the settings; the entry reloads afterwards."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the same fields as the config flow."""
        errors: dict[str, str] = {}
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            data = _normalise(user_input)
            ports = _ports_in_use(
                self.hass.config_entries.async_entries(DOMAIN), self.config_entry.entry_id
            )
            errors = _validate(data, ports)
            if not errors:
                return self.async_create_entry(data=data)
            current = user_input
        schema = self.add_suggested_values_to_schema(_schema(), current)
        return self.async_show_form(step_id="init", data_schema=schema, errors=errors)
