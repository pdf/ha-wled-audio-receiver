"""The active state: when the integration drives its outputs."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from homeassistant.const import STATE_PLAYING
from homeassistant.core import CALLBACK_TYPE, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers.event import async_track_state_change_event

from .const import PACKET_TIMEOUT


class Gate:
    """Track the three conditions of the active state.

    Active while the "Music reactive" switch is on, the selected media player
    (if any) is playing, and a valid packet arrived within PACKET_TIMEOUT.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        media_player: str | None,
        on_change: Callable[[bool], None],
        on_stream_lost: Callable[[], None] | None = None,
    ) -> None:
        """Initialise; call async_start to begin tracking the media player."""
        self._hass = hass
        self._media_player = media_player or None
        self._on_change = on_change
        self._on_stream_lost = on_stream_lost
        self._switch_on = False
        self._playing = self._media_player is None
        self._receiving = False
        self._active = False
        self._timeout: asyncio.TimerHandle | None = None
        self._unsub: CALLBACK_TYPE | None = None

    @property
    def active(self) -> bool:
        """Return whether outputs should be driven."""
        return self._active

    @property
    def receiving(self) -> bool:
        """Return whether packets are arriving."""
        return self._receiving

    @property
    def switch_on(self) -> bool:
        """Return the switch condition."""
        return self._switch_on

    @property
    def playing(self) -> bool:
        """Return the media player condition."""
        return self._playing

    @callback
    def async_start(self) -> None:
        """Start tracking the media player."""
        if self._media_player is None:
            return
        state = self._hass.states.get(self._media_player)
        self._playing = state is not None and state.state == STATE_PLAYING
        self._unsub = async_track_state_change_event(
            self._hass, self._media_player, self._media_player_changed
        )

    @callback
    def async_stop(self) -> None:
        """Stop all tracking; outputs are deactivated."""
        if self._unsub is not None:
            self._unsub()
            self._unsub = None
        if self._timeout is not None:
            self._timeout.cancel()
            self._timeout = None
        self._receiving = False
        self._update()

    @callback
    def set_switch(self, on: bool) -> None:
        """Set the switch condition."""
        self._switch_on = on
        self._update()

    @callback
    def packet_received(self) -> None:
        """Record a valid packet and restart the timeout."""
        if self._timeout is not None:
            self._timeout.cancel()
        self._timeout = self._hass.loop.call_later(PACKET_TIMEOUT, self._timed_out)
        if not self._receiving:
            self._receiving = True
            self._update()

    @callback
    def _timed_out(self) -> None:
        self._timeout = None
        self._receiving = False
        if self._on_stream_lost is not None:
            self._on_stream_lost()
        self._update()

    @callback
    def _media_player_changed(self, event: Event[EventStateChangedData]) -> None:
        new_state = event.data["new_state"]
        self._playing = new_state is not None and new_state.state == STATE_PLAYING
        self._update()

    @callback
    def _update(self) -> None:
        active = self._switch_on and self._playing and self._receiving
        if active != self._active:
            self._active = active
            self._on_change(active)
