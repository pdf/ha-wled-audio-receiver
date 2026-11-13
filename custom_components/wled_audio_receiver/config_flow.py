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
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)
import voluptuous as vol

from .const import (
    CONF_HYPERION,
    CONF_HYPERION_HOST,
    CONF_HYPERION_HUE_END,
    CONF_HYPERION_HUE_REVERSE,
    CONF_HYPERION_HUE_START,
    CONF_HYPERION_INSTANCES,
    CONF_HYPERION_LAYOUT,
    CONF_HYPERION_PORT,
    CONF_HYPERION_PRIORITY,
    CONF_HYPERION_SERVER,
    CONF_HYPERION_TOKEN,
    CONF_LIGHT_HUE_END,
    CONF_LIGHT_HUE_REVERSE,
    CONF_LIGHT_HUE_START,
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
    DEFAULT_HUE_END,
    DEFAULT_HUE_REVERSE,
    DEFAULT_HUE_START,
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
    DOMAIN,
    HYPERION_CORE_DOMAIN,
    HYPERION_LAYOUTS,
    HYPERION_SERVER_MANUAL,
    LIGHT_MODES,
    NAME,
)
from .hyperion import (
    HyperionAuthError,
    HyperionConnectError,
    HyperionError,
    HyperionInstance,
    async_get_instances,
)

# Shown for the "enter host and port" choice of the server picker.
MANUAL_LABEL = "Enter host, port and token below"


def _box(
    minimum: float, maximum: float, step: float = 1, unit: str | None = None
) -> NumberSelector:
    config = NumberSelectorConfig(min=minimum, max=maximum, step=step, mode=NumberSelectorMode.BOX)
    if unit is not None:
        config["unit_of_measurement"] = unit
    return NumberSelector(config)


def _hue() -> NumberSelector:
    return NumberSelector(
        NumberSelectorConfig(min=0, max=360, step=1, mode=NumberSelectorMode.SLIDER)
    )


def _hue_fields(start: str, end: str, reverse: str) -> dict[vol.Marker, Any]:
    return {
        vol.Required(start, default=int(DEFAULT_HUE_START)): _hue(),
        vol.Required(end, default=int(DEFAULT_HUE_END)): _hue(),
        vol.Required(reverse, default=DEFAULT_HUE_REVERSE): BooleanSelector(),
    }


def _hyperion_servers(hass: HomeAssistant) -> list[ConfigEntry]:
    """Return the servers set up in Home Assistant's own Hyperion integration."""
    return hass.config_entries.async_entries(HYPERION_CORE_DOMAIN)


def _server_for(servers: list[ConfigEntry], host: str, port: int) -> str:
    """Return the picker value matching a stored host and port."""
    for entry in servers:
        if entry.data.get("host") == host and int(entry.data.get("port", 0)) == port:
            return entry.entry_id
    return HYPERION_SERVER_MANUAL


def _schema(servers: list[ConfigEntry]) -> vol.Schema:
    hyperion_server: dict[vol.Marker, Any] = {}
    if servers:
        options = [SelectOptionDict(value=e.entry_id, label=e.title) for e in servers]
        options.append(SelectOptionDict(value=HYPERION_SERVER_MANUAL, label=MANUAL_LABEL))
        hyperion_server[vol.Required(CONF_HYPERION_SERVER, default=servers[0].entry_id)] = (
            SelectSelector(SelectSelectorConfig(options=options, mode=SelectSelectorMode.DROPDOWN))
        )
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
            **_hue_fields(CONF_LIGHT_HUE_START, CONF_LIGHT_HUE_END, CONF_LIGHT_HUE_REVERSE),
            vol.Required(CONF_MIN_BRIGHTNESS, default=DEFAULT_MIN_BRIGHTNESS): _box(
                0, 100, unit="%"
            ),
            vol.Required(CONF_MAX_BRIGHTNESS, default=DEFAULT_MAX_BRIGHTNESS): _box(
                0, 100, unit="%"
            ),
            vol.Required(CONF_UPDATE_RATE, default=DEFAULT_UPDATE_RATE): _box(0.5, 10, 0.5, "/s"),
            vol.Required(CONF_RESTORE, default=DEFAULT_RESTORE): BooleanSelector(),
            vol.Required(CONF_HYPERION, default=DEFAULT_HYPERION): BooleanSelector(),
            **hyperion_server,
            vol.Optional(CONF_HYPERION_HOST): TextSelector(),
            vol.Required(CONF_HYPERION_PORT, default=DEFAULT_HYPERION_PORT): _box(1, 65535),
            vol.Optional(CONF_HYPERION_TOKEN): TextSelector(
                TextSelectorConfig(type=TextSelectorType.PASSWORD)
            ),
            vol.Required(CONF_HYPERION_PRIORITY, default=DEFAULT_HYPERION_PRIORITY): _box(1, 253),
            vol.Required(CONF_HYPERION_LAYOUT, default=DEFAULT_HYPERION_LAYOUT): SelectSelector(
                SelectSelectorConfig(
                    options=HYPERION_LAYOUTS,
                    translation_key=CONF_HYPERION_LAYOUT,
                    mode=SelectSelectorMode.LIST,
                )
            ),
            **_hue_fields(
                CONF_HYPERION_HUE_START, CONF_HYPERION_HUE_END, CONF_HYPERION_HUE_REVERSE
            ),
        }
    )


def _normalise(user_input: dict[str, Any], servers: list[ConfigEntry]) -> dict[str, Any]:
    """Fill missing optional fields, convert numbers and resolve the server picker."""
    data = dict(user_input)
    server = data.pop(CONF_HYPERION_SERVER, HYPERION_SERVER_MANUAL)
    for entry in servers:
        if entry.entry_id == server:
            data[CONF_HYPERION_HOST] = entry.data.get("host", "")
            data[CONF_HYPERION_PORT] = entry.data.get("port", DEFAULT_HYPERION_PORT)
            data[CONF_HYPERION_TOKEN] = entry.data.get("token") or ""
    for key in (
        CONF_PORT,
        CONF_MIN_BRIGHTNESS,
        CONF_MAX_BRIGHTNESS,
        CONF_HYPERION_PORT,
        CONF_HYPERION_PRIORITY,
        CONF_LIGHT_HUE_START,
        CONF_LIGHT_HUE_END,
        CONF_HYPERION_HUE_START,
        CONF_HYPERION_HUE_END,
    ):
        data[key] = int(data[key])
    data[CONF_UPDATE_RATE] = float(data[CONF_UPDATE_RATE])
    for key in (CONF_SENDER, CONF_HYPERION_HOST, CONF_HYPERION_TOKEN):
        data[key] = (data.get(key) or "").strip()
    data[CONF_MEDIA_PLAYER] = data.get(CONF_MEDIA_PLAYER) or None
    data[CONF_LIGHTS] = list(data.get(CONF_LIGHTS) or [])
    data[CONF_HYPERION_INSTANCES] = list(data.get(CONF_HYPERION_INSTANCES) or [])
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


class _FlowSteps:
    """Steps shared by the config and options flows."""

    hass: HomeAssistant
    _data: dict[str, Any]
    _instances: list[HyperionInstance]

    def _finish(self, data: dict[str, Any]) -> ConfigFlowResult:
        raise NotImplementedError

    def _ports_in_use(self) -> set[int]:
        raise NotImplementedError

    def _main_form(
        self, step_id: str, values: dict[str, Any], errors: dict[str, str]
    ) -> ConfigFlowResult:
        servers = _hyperion_servers(self.hass)
        values = dict(values)
        if servers and CONF_HYPERION_SERVER not in values and values.get(CONF_HYPERION_HOST):
            values[CONF_HYPERION_SERVER] = _server_for(
                servers,
                values[CONF_HYPERION_HOST],
                int(values.get(CONF_HYPERION_PORT, DEFAULT_HYPERION_PORT)),
            )
        schema = self.add_suggested_values_to_schema(_schema(servers), values)  # type: ignore[attr-defined]
        return self.async_show_form(step_id=step_id, data_schema=schema, errors=errors)  # type: ignore[attr-defined]

    async def _submit_main(
        self, step_id: str, user_input: dict[str, Any], previous: dict[str, Any]
    ) -> ConfigFlowResult:
        data = _normalise(user_input, _hyperion_servers(self.hass))
        errors = _validate(data, self._ports_in_use())
        if not errors and data[CONF_HYPERION]:
            try:
                self._instances = await async_get_instances(
                    data[CONF_HYPERION_HOST],
                    data[CONF_HYPERION_PORT],
                    data[CONF_HYPERION_TOKEN] or None,
                )
            except HyperionAuthError:
                errors["base"] = "invalid_auth"
            except HyperionConnectError:
                errors["base"] = "cannot_connect"
            except HyperionError:
                errors["base"] = "hyperion_error"
            else:
                if not self._instances:
                    errors["base"] = "no_instances"
        if errors:
            return self._main_form(step_id, user_input, errors)
        if not data[CONF_HYPERION]:
            data[CONF_HYPERION_INSTANCES] = []
            return self._finish(data)
        data[CONF_HYPERION_INSTANCES] = previous.get(CONF_HYPERION_INSTANCES) or []
        self._data = data
        return await self.async_step_hyperion_instances()

    async def async_step_hyperion_instances(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Choose the Hyperion instances that show the music."""
        errors: dict[str, str] = {}
        known = {i.id for i in self._instances}
        if user_input is not None:
            chosen = sorted(int(v) for v in user_input.get(CONF_HYPERION_INSTANCES) or [])
            if chosen:
                return self._finish({**self._data, CONF_HYPERION_INSTANCES: chosen})
            errors[CONF_HYPERION_INSTANCES] = "no_instance_chosen"
        current = [i for i in self._data.get(CONF_HYPERION_INSTANCES) or [] if i in known]
        if not current:
            running = [i.id for i in self._instances if i.running]
            current = running[:1] or [self._instances[0].id]
        options = [
            SelectOptionDict(
                value=str(i.id),
                label=f"{i.name} ({i.id})" + ("" if i.running else " - not running"),
            )
            for i in self._instances
        ]
        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_HYPERION_INSTANCES, default=[str(i) for i in current]
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=options, multiple=True, mode=SelectSelectorMode.LIST
                    )
                )
            }
        )
        return self.async_show_form(  # type: ignore[attr-defined]
            step_id="hyperion_instances",
            data_schema=schema,
            errors=errors,
            description_placeholders={"host": self._data[CONF_HYPERION_HOST]},
        )


class WledAudioReceiverConfigFlow(_FlowSteps, ConfigFlow, domain=DOMAIN):
    """Set up a receiver."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> WledAudioReceiverOptionsFlow:
        """Return the options flow."""
        return WledAudioReceiverOptionsFlow()

    def _finish(self, data: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(title=NAME, data={}, options=data)

    def _ports_in_use(self) -> set[int]:
        return _ports_in_use(self._async_current_entries())

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for all settings."""
        if user_input is not None:
            return await self._submit_main("user", user_input, {})
        return self._main_form("user", {}, {})


class WledAudioReceiverOptionsFlow(_FlowSteps, OptionsFlowWithReload):
    """Change the settings; the entry reloads afterwards."""

    def _finish(self, data: dict[str, Any]) -> ConfigFlowResult:
        return self.async_create_entry(data=data)

    def _ports_in_use(self) -> set[int]:
        return _ports_in_use(
            self.hass.config_entries.async_entries(DOMAIN), self.config_entry.entry_id
        )

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the same fields as the config flow."""
        current = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            return await self._submit_main("init", user_input, current)
        return self._main_form("init", current, {})
