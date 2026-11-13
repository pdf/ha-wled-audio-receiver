"""The active state: switch, media player and packet timeout."""

from __future__ import annotations

from datetime import timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
    STATE_OFF,
    STATE_ON,
)
from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import (
    async_fire_time_changed_exact,
    mock_restore_cache,
)

from .conftest import FakeReceiver

SWITCH = "switch.wled_audio_reactive_receiver_music_reactive"
PLAYER = "media_player.kodi"


async def _advance(hass: HomeAssistant, freezer: FrozenDateTimeFactory, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed_exact(hass)
    await hass.async_block_till_done()


async def test_switch_on_by_default_and_packets_activate(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver
) -> None:
    """Without a media player, packets alone activate when the switch is on."""
    runtime = await setup_entry()
    assert hass.states.get(SWITCH).state == STATE_ON
    assert not runtime.gate.active
    receiver.feed()
    assert runtime.gate.active
    await hass.async_block_till_done()
    assert hass.states.get(SWITCH).attributes["active"] is True


async def test_switch_turns_off_and_on(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver
) -> None:
    """Turning the switch off ends the active state; on resumes it."""
    runtime = await setup_entry()
    receiver.feed()
    assert runtime.gate.active
    await hass.services.async_call(
        "switch", SERVICE_TURN_OFF, {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )
    assert hass.states.get(SWITCH).state == STATE_OFF
    assert not runtime.gate.active
    receiver.feed()
    assert not runtime.gate.active
    await hass.services.async_call(
        "switch", SERVICE_TURN_ON, {ATTR_ENTITY_ID: SWITCH}, blocking=True
    )
    assert runtime.gate.active


async def test_switch_state_restored(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver
) -> None:
    """The switch comes back off after a restart if it was off."""
    mock_restore_cache(hass, [State(SWITCH, STATE_OFF)])
    runtime = await setup_entry()
    assert hass.states.get(SWITCH).state == STATE_OFF
    receiver.feed()
    assert not runtime.gate.active


async def test_media_player_gating(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver
) -> None:
    """Only `playing` lets the outputs run; paused and idle stop them at once."""
    hass.states.async_set(PLAYER, "idle")
    runtime = await setup_entry(media_player=PLAYER)
    receiver.feed()
    assert not runtime.gate.active

    hass.states.async_set(PLAYER, "playing")
    await hass.async_block_till_done()
    assert runtime.gate.active

    hass.states.async_set(PLAYER, "paused")
    await hass.async_block_till_done()
    assert not runtime.gate.active

    hass.states.async_set(PLAYER, "playing")
    await hass.async_block_till_done()
    assert runtime.gate.active

    hass.states.async_set(PLAYER, "idle")
    await hass.async_block_till_done()
    assert not runtime.gate.active

    hass.states.async_remove(PLAYER)
    await hass.async_block_till_done()
    assert not runtime.gate.active


async def test_media_player_already_playing(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver
) -> None:
    """A player that is playing at set-up counts immediately."""
    hass.states.async_set(PLAYER, "playing")
    runtime = await setup_entry(media_player=PLAYER)
    receiver.feed()
    assert runtime.gate.active


async def test_packet_timeout(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, freezer: FrozenDateTimeFactory
) -> None:
    """One second without packets ends the active state; packets restart it."""
    runtime = await setup_entry()
    receiver.feed()
    await _advance(hass, freezer, 0.6)
    receiver.feed()
    await _advance(hass, freezer, 0.6)
    assert runtime.gate.active, "each packet restarts the timeout"
    await _advance(hass, freezer, 0.5)
    assert not runtime.gate.active
    assert not runtime.gate.receiving
    receiver.feed()
    assert runtime.gate.active


async def test_timeout_resets_frame_counter(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver, freezer: FrozenDateTimeFactory
) -> None:
    """After a gap, a sender that restarted its counter is accepted."""
    runtime = await setup_entry()
    receiver.feed(counter=100)
    receiver.feed(counter=1)
    assert receiver.protocol.dropped == 1
    await _advance(hass, freezer, 1.1)
    assert not runtime.gate.active
    receiver.feed(counter=1)
    assert runtime.gate.active


async def test_invalid_packets_do_not_activate(
    hass: HomeAssistant, setup_entry, receiver: FakeReceiver
) -> None:
    """Junk and filtered senders don't count as packets."""
    runtime = await setup_entry(sender="192.0.2.99")
    receiver.feed(b"junk")
    receiver.feed()
    assert not runtime.gate.active
    receiver.feed(addr=("192.0.2.99", 1))
    assert runtime.gate.active
    assert receiver.args == (11988, True, "192.0.2.99")


async def test_unload(hass: HomeAssistant, setup_entry, receiver: FakeReceiver) -> None:
    """Unloading closes the receiver and ends the active state."""
    runtime = await setup_entry()
    receiver.feed()
    transport = runtime.transport
    assert await hass.config_entries.async_unload(runtime.entry.entry_id)
    await hass.async_block_till_done()
    transport.close.assert_called_once()
    assert not runtime.gate.active


async def test_setup_retries_when_port_busy(hass: HomeAssistant) -> None:
    """A bind error makes set-up retry later."""
    from unittest.mock import patch

    from homeassistant.config_entries import ConfigEntryState
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(domain="wled_audio_receiver", options={})
    entry.add_to_hass(hass)
    with patch(
        "custom_components.wled_audio_receiver.async_start_receiver",
        side_effect=OSError("Address in use"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_RETRY
