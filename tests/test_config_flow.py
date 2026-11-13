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
from .fake_hyperion import TOKEN, FakeHyperion

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
    "light_hue_start": [255, 0, 0],
    "light_hue_end": [128, 0, 255],
    "light_hue_reverse": False,
    "hyperion_hue_start": [255, 0, 0],
    "hyperion_hue_end": [128, 0, 255],
    "hyperion_hue_reverse": False,
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
        "hyperion_token": "",
        "hyperion_instances": [],
    }
    await hass.async_block_till_done()
    assert receiver.args == (11988, True, None)


def _default(result: dict[str, Any], key: str) -> Any:
    for field in result["data_schema"].schema:
        if field == key:
            return field.default()
    raise KeyError(key)


def _has_field(result: dict[str, Any], key: str) -> bool:
    return any(field == key for field in result["data_schema"].schema)


async def test_user_flow_full(
    hass: HomeAssistant, receiver: FakeReceiver, hyperion: FakeHyperion
) -> None:
    """All fields are stored with their types; Hyperion adds an instance step."""
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
            "hyperion_host": "127.0.0.1",
            "hyperion_port": float(hyperion.port),
            "hyperion_priority": 120.0,
            "hyperion_layout": "four_way",
            "light_hue_start": [255, 0, 255],
            "light_hue_end": [255, 255, 0],
            "hyperion_hue_start": [0, 0, 255],
            "hyperion_hue_end": [255, 0, 0],
            "hyperion_hue_reverse": True,
        },
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "hyperion_instances"
    assert result["description_placeholders"] == {"host": "127.0.0.1"}
    assert _default(result, "hyperion_instances") == ["0"]  # the first running instance
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"hyperion_instances": ["1", "0"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    options = result["options"]
    assert options["hyperion_instances"] == [0, 1]
    assert options["light_hue_start"] == [255, 0, 255]
    assert options["light_hue_end"] == [255, 255, 0]
    assert options["hyperion_hue_reverse"] is True
    assert options["hyperion_port"] == hyperion.port
    assert "hyperion_server" not in options
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


def _hyperion_entry(
    hass: HomeAssistant, server: FakeHyperion, token: str | None
) -> MockConfigEntry:
    """Add a server of Home Assistant's own Hyperion integration."""
    data = {"host": "127.0.0.1", "port": server.port}
    if token:
        data["token"] = token
    entry = MockConfigEntry(domain="hyperion", title=f"127.0.0.1:{server.port}", data=data)
    entry.add_to_hass(hass)
    return entry


async def test_picker_absent_without_hyperion_integration(
    hass: HomeAssistant, receiver: FakeReceiver
) -> None:
    """Without servers in the Hyperion integration, only manual entry is shown."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert not _has_field(result, "hyperion_server")
    assert _has_field(result, "hyperion_host")
    assert _has_field(result, "hyperion_token")


async def test_picker_copies_server_and_token(
    hass: HomeAssistant, receiver: FakeReceiver, hyperion: FakeHyperion
) -> None:
    """Choosing a server from the Hyperion integration copies its host, port and token."""
    hyperion.token = TOKEN
    server = _hyperion_entry(hass, hyperion, TOKEN)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert _default(result, "hyperion_server") == server.entry_id
    options = result["data_schema"].schema
    picker = next(v for k, v in options.items() if k == "hyperion_server")
    assert [o["value"] for o in picker.config["options"]] == [server.entry_id, "manual"]

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DEFAULTS, "hyperion": True, "hyperion_server": server.entry_id}
    )
    assert result["step_id"] == "hyperion_instances"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"hyperion_instances": ["1"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    options = result["options"]
    assert options["hyperion_host"] == "127.0.0.1"
    assert options["hyperion_port"] == hyperion.port
    assert options["hyperion_token"] == TOKEN
    assert options["hyperion_instances"] == [1]
    assert "hyperion_server" not in options


async def test_picker_manual(
    hass: HomeAssistant, receiver: FakeReceiver, hyperion: FakeHyperion
) -> None:
    """Choosing manual uses the host, port and token fields."""
    _hyperion_entry(hass, hyperion, None)
    hyperion.token = TOKEN
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **DEFAULTS,
            "hyperion": True,
            "hyperion_server": "manual",
            "hyperion_host": "127.0.0.1",
            "hyperion_port": hyperion.port,
            "hyperion_token": TOKEN,
        },
    )
    assert result["step_id"] == "hyperion_instances"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"hyperion_instances": ["0"]}
    )
    assert result["options"]["hyperion_token"] == TOKEN


@pytest.mark.parametrize(
    ("token_required", "token", "instances", "error"),
    [
        (True, None, None, "invalid_auth"),
        (True, "f" * 36, None, "invalid_auth"),
        (False, None, [], "no_instances"),
    ],
)
async def test_hyperion_errors(
    hass: HomeAssistant,
    receiver: FakeReceiver,
    hyperion: FakeHyperion,
    token_required: bool,
    token: str | None,
    instances: list | None,
    error: str,
) -> None:
    """Login and instance problems are shown on the form."""
    if token_required:
        hyperion.token = TOKEN
    if instances is not None:
        hyperion.instances = instances
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    user_input = {
        **DEFAULTS,
        "hyperion": True,
        "hyperion_host": "127.0.0.1",
        "hyperion_port": hyperion.port,
    }
    if token:
        user_input["hyperion_token"] = token
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}


async def test_hyperion_unreachable(
    hass: HomeAssistant, receiver: FakeReceiver, socket_enabled: None
) -> None:
    """An unreachable Hyperion is reported; turning Hyperion off gets past it."""
    server = FakeHyperion()
    await server.start()
    port = server.port
    await server.stop()
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    user_input = {**DEFAULTS, "hyperion": True, "hyperion_host": "127.0.0.1", "hyperion_port": port}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], user_input)
    assert result["errors"] == {"base": "cannot_connect"}
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**user_input, "hyperion": False}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_instance_step(
    hass: HomeAssistant, receiver: FakeReceiver, hyperion: FakeHyperion
) -> None:
    """The default is the first running instance; at least one must be chosen."""
    hyperion.instances = [(0, "Off", False), (1, "On", True)]
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **DEFAULTS,
            "hyperion": True,
            "hyperion_host": "127.0.0.1",
            "hyperion_port": hyperion.port,
        },
    )
    assert _default(result, "hyperion_instances") == ["1"]
    picker = next(v for k, v in result["data_schema"].schema.items() if k == "hyperion_instances")
    assert [o["label"] for o in picker.config["options"]] == ["Off (0) - not running", "On (1)"]
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"hyperion_instances": []}
    )
    assert result["errors"] == {"hyperion_instances": "no_instance_chosen"}


async def test_options_flow_hyperion(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, hyperion: FakeHyperion
) -> None:
    """The options flow preselects the matching server and the stored instances."""
    server = _hyperion_entry(hass, hyperion, None)
    runtime = await setup_entry(
        **DEFAULTS
        | {
            "hyperion": True,
            "hyperion_host": "127.0.0.1",
            "hyperion_port": hyperion.port,
            "hyperion_instances": [1],
        }
    )
    result = await hass.config_entries.options.async_init(runtime.entry.entry_id)
    assert _suggested(result, "hyperion_server") == server.entry_id
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {**DEFAULTS, "hyperion": True, "hyperion_server": server.entry_id},
    )
    assert result["step_id"] == "hyperion_instances"
    assert _default(result, "hyperion_instances") == ["1"]
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"hyperion_instances": ["0", "1"]}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert runtime.entry.options["hyperion_instances"] == [0, 1]


@pytest.mark.parametrize("key", ["light_hue_start", "hyperion_hue_end"])
@pytest.mark.parametrize("grey", [[255, 255, 255], [128, 128, 128], [0, 0, 0]])
async def test_grey_colour_refused(
    hass: HomeAssistant, receiver: FakeReceiver, key: str, grey: list[int]
) -> None:
    """White, grey and black have no hue and are refused."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**DEFAULTS, key: grey}
    )
    assert result["errors"] == {key: "grey_colour"}


async def test_colour_pickers(hass: HomeAssistant, receiver: FakeReceiver) -> None:
    """The colour range fields are colour pickers."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    fields = {str(k): v for k, v in result["data_schema"].schema.items()}
    for key in ("light_hue_start", "light_hue_end", "hyperion_hue_start", "hyperion_hue_end"):
        assert fields[key].serialize() == {"selector": {"color_rgb": {}}}


async def test_options_flow_shows_hues_as_colours(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver
) -> None:
    """Hues stored by earlier versions show as their colours in the pickers."""
    legacy = {**DEFAULTS, "light_hue_start": 240, "light_hue_end": 60}
    runtime = await setup_entry(**legacy)
    result = await hass.config_entries.options.async_init(runtime.entry.entry_id)
    assert _suggested(result, "light_hue_start") == [0, 0, 255]
    assert _suggested(result, "light_hue_end") == [255, 255, 0]
