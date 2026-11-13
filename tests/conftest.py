"""Shared fixtures for the WLED Audio-Reactive Receiver tests."""

from __future__ import annotations

from collections.abc import Callable
import struct
from typing import Any
from unittest.mock import MagicMock, patch

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.wled_audio_receiver import WledAudioReceiver
from custom_components.wled_audio_receiver.const import DOMAIN
from custom_components.wled_audio_receiver.receiver import SyncProtocol

SENDER = ("192.0.2.10", 40000)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Load custom integrations in every test."""


def make_packet(
    fft: list[int] | None = None,
    sample_smth: float = 0.0,
    counter: int = 1,
    header: bytes = b"00002\0",
    pressure: tuple[int, int] = (0, 0),
    sample_raw: float = 0.0,
    peak: int = 0,
    zero_crossings: int = 0,
    magnitude: float = 0.0,
    major_peak: float = 1.0,
) -> bytes:
    """Encode an audio sync V2 packet."""
    return struct.pack(
        "<6s2sffBB16sHff",
        header,
        bytes(pressure),
        sample_raw,
        sample_smth,
        peak,
        counter,
        bytes(fft or [0] * 16),
        zero_crossings,
        magnitude,
        major_peak,
    )


class FakeReceiver:
    """Stands in for the UDP endpoint; feed() delivers datagrams."""

    def __init__(self) -> None:
        """Start without a protocol."""
        self.protocol: SyncProtocol | None = None
        self.counter = 0
        self.args: tuple[Any, ...] = ()

    async def start(
        self, port: int, multicast: bool, sender: str | None, on_packet: Callable[..., None]
    ) -> tuple[MagicMock, SyncProtocol]:
        """Replace async_start_receiver."""
        self.args = (port, multicast, sender)
        self.protocol = SyncProtocol(on_packet, sender)
        return MagicMock(), self.protocol

    def feed(
        self, data: bytes | None = None, addr: tuple[str, int] = SENDER, **kwargs: Any
    ) -> None:
        """Deliver one datagram; without data, a packet with the next frame counter."""
        if data is None:
            self.counter = self.counter % 255 + 1
            kwargs.setdefault("counter", self.counter)
            data = make_packet(**kwargs)
        assert self.protocol is not None
        self.protocol.datagram_received(data, addr)


@pytest.fixture
def receiver() -> FakeReceiver:
    """Patch the UDP endpoint with a fake."""
    fake = FakeReceiver()
    with patch(
        "custom_components.wled_audio_receiver.async_start_receiver", side_effect=fake.start
    ):
        yield fake


@pytest.fixture
def setup_entry(hass: HomeAssistant, receiver: FakeReceiver) -> Callable[..., Any]:
    """Return a coroutine that sets up an entry with the given options."""

    async def _setup(**options: Any) -> WledAudioReceiver:
        entry = MockConfigEntry(
            domain=DOMAIN, title="WLED Audio-Reactive Receiver", options=options
        )
        entry.add_to_hass(hass)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        return entry.runtime_data

    return _setup
