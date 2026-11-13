"""Config and options flows."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wled_audio_receiver.const import DOMAIN

from .conftest import FakeReceiver

DEFAULTS = {
    "port": 11988,
    "multicast": True,
    "light_mode": "mix",
    "min_brightness": 10,
    "max_brightness": 100,
    "update_rate": 3.0,
    "restore": False,
    "hyperion": False,
    "hyperion_port": 19444,
    "hyperion_priority": 150,
    "hyperion_layout": "bottom_up",
}


def _suggested(result: dict[str, Any], key: str) -> Any:
    for field in result["data_schema"].schema:
        if field == key:
            return (field.description or {}).get("suggested_value")
    raise KeyError(key)


async def test_user_flow_defaults(hass: HomeAssistant, receiver: FakeReceiver) -> None:
    """Submitting the defaults creates an entry that accepts any sender."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {}
    # The form's defaults fill every required field.
    user_input = result["data_schema"]({})
    assert user_input == DEFAULTS

    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "WLED Audio-Reactive Receiver"
    assert result["data"] == {}
    assert result["options"] == {
        **DEFAULTS,
        "sender": "",
        "media_player": None,
        "lights": [],
        "hyperion_host": "",
    }
    await hass.async_block_till_done()
    assert receiver.args == (11988, True, None)


async def test_user_flow_full(hass: HomeAssistant, receiver: FakeReceiver) -> None:
    """All fields are stored with their types."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **DEFAULTS,
            "port": 12000.0,
            "multicast": False,
            "sender": " 192.0.2.5 ",
            "media_player": "media_player.kodi",
            "lights": ["light.a", "light.b"],
            "light_mode": "split",
            "min_brightness": 20.0,
            "max_brightness": 80.0,
            "update_rate": 2.5,
            "restore": True,
            "hyperion": True,
            "hyperion_host": "hyperion.local",
            "hyperion_port": 19445.0,
            "hyperion_priority": 120.0,
            "hyperion_layout": "four_way",
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    options = result["options"]
    assert options["port"] == 12000 and isinstance(options["port"], int)
    assert options["sender"] == "192.0.2.5"
    assert options["lights"] == ["light.a", "light.b"]
    assert options["light_mode"] == "split"
    assert options["hyperion_priority"] == 120
    assert options["hyperion_layout"] == "four_way"
    await hass.async_block_till_done()
    assert receiver.args == (12000, False, "192.0.2.5")


@pytest.mark.parametrize(
    ("changes", "errors"),
    [
        ({"sender": "kodi.local"}, {"sender": "invalid_sender"}),
        ({"sender": "2001:db8::1"}, {"sender": "invalid_sender"}),
        ({"min_brightness": 90, "max_brightness": 50}, {"min_brightness": "min_above_max"}),
        ({"hyperion": True}, {"hyperion_host": "hyperion_host_required"}),
        ({"hyperion": True, "hyperion_host": "  "}, {"hyperion_host": "hyperion_host_required"}),
    ],
)
async def test_user_flow_errors(
    hass: HomeAssistant, receiver: FakeReceiver, changes: dict[str, Any], errors: dict[str, str]
) -> None:
    """Invalid input shows errors and keeps what was entered."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DEFAULTS, **changes}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == errors
    for key, value in changes.items():
        assert _suggested(result, key) == value


async def test_user_flow_port_in_use(hass: HomeAssistant, receiver: FakeReceiver) -> None:
    """A second receiver on the same port is refused; another port works."""
    MockConfigEntry(domain=DOMAIN, options={**DEFAULTS}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], DEFAULTS)
    assert result["errors"] == {"port": "port_in_use"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DEFAULTS, "port": 11989}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_options_flow(hass: HomeAssistant, setup_entry, receiver: FakeReceiver) -> None:
    """The options flow shows the current values and reloads with the new ones."""
    runtime = await setup_entry(**DEFAULTS, sender="192.0.2.5", lights=["light.a"])
    entry = runtime.entry

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"
    assert _suggested(result, "sender") == "192.0.2.5"
    assert _suggested(result, "lights") == ["light.a"]

    # Clearing the sender filter and the lights.
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**DEFAULTS, "port": 11990, "media_player": "media_player.tv"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["sender"] == ""
    assert entry.options["lights"] == []
    assert entry.options["media_player"] == "media_player.tv"
    # Reloaded with the new port and no filter.
    assert entry.runtime_data is not runtime
    assert receiver.args == (11990, True, None)


async def test_options_flow_errors(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver
) -> None:
    """The options flow validates like the config flow; its own port is not "in use"."""
    MockConfigEntry(domain=DOMAIN, options={**DEFAULTS, "port": 12000}).add_to_hass(hass)
    runtime = await setup_entry(**DEFAULTS)
    result = await hass.config_entries.options.async_init(runtime.entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {**DEFAULTS, "port": 12000, "sender": "nope"}
    )
    assert result["errors"] == {"port": "port_in_use", "sender": "invalid_sender"}
    assert _suggested(result, "sender") == "nope"
    result = await hass.config_entries.options.async_configure(result["flow_id"], DEFAULTS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
