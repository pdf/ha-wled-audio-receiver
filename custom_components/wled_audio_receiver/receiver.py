"""Receive and decode WLED audio sync V2 packets."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
import logging
import socket
import struct
import time

from .const import MULTICAST_GROUP

_LOGGER = logging.getLogger(__name__)

PACKET_SIZE = 44
HEADER = b"00002\0"
_STRUCT = struct.Struct("<6s2sffBB16sHff")

# Ignored packets are logged at most this often (seconds).
LOG_INTERVAL = 60.0


@dataclass(frozen=True, slots=True)
class SyncPacket:
    """One decoded audio sync V2 packet."""

    pressure: float
    sample_raw: float
    sample_smth: float
    sample_peak: bool
    frame_counter: int
    fft_result: tuple[int, ...]
    zero_crossing_count: int
    fft_magnitude: float
    fft_major_peak: float


def decode(data: bytes) -> SyncPacket | None:
    """Decode a packet, or return None if it is not an audio sync V2 packet."""
    if len(data) != PACKET_SIZE:
        return None
    (
        header,
        pressure,
        sample_raw,
        sample_smth,
        peak,
        counter,
        fft,
        zero_crossings,
        magnitude,
        major_peak,
    ) = _STRUCT.unpack(data)
    if header != HEADER:
        return None
    return SyncPacket(
        pressure=pressure[0] + pressure[1] / 256,
        sample_raw=sample_raw,
        sample_smth=sample_smth,
        sample_peak=peak != 0,
        frame_counter=counter,
        fft_result=tuple(fft),
        zero_crossing_count=zero_crossings,
        fft_magnitude=magnitude,
        fft_major_peak=major_peak,
    )


class FrameFilter:
    """Drop frames that are not newer than the last one, as WLED's receiver does.

    A frame counter of 0 comes from a legacy sender and is always accepted.
    Otherwise a frame is accepted only if (int8)(new - last) > 0, which
    handles the 8-bit rollover.
    """

    def __init__(self) -> None:
        """Initialise with no frame seen."""
        self._last = 0

    def reset(self) -> None:
        """Forget the last frame (after a gap, the sender may have restarted)."""
        self._last = 0

    def accept(self, counter: int) -> bool:
        """Return whether a frame with this counter should be used."""
        if counter == 0 or self._last == 0:
            self._last = counter
            return True
        diff = (counter - self._last) & 0xFF
        if diff >= 0x80:
            diff -= 0x100
        if diff > 0:
            self._last = counter
            return True
        return False


class SyncProtocol(asyncio.DatagramProtocol):
    """Datagram protocol that decodes, filters and forwards packets."""

    def __init__(
        self,
        on_packet: Callable[[SyncPacket], None],
        sender: str | None = None,
    ) -> None:
        """Initialise with the packet callback and an optional sender filter."""
        self._on_packet = on_packet
        self._sender = sender or None
        self.frames = FrameFilter()
        self.ignored = 0
        self.dropped = 0
        self._ignored_logged = 0
        self._last_log: float | None = None

    def datagram_received(self, data: bytes, addr: tuple[str | int, ...]) -> None:
        """Handle one datagram."""
        if self._sender is not None and addr[0] != self._sender:
            self._ignore("sender %s does not match the filter", addr[0])
            return
        packet = decode(data)
        if packet is None:
            self._ignore("%d bytes from %s are not an audio sync V2 packet", len(data), addr[0])
            return
        if not self.frames.accept(packet.frame_counter):
            self.dropped += 1
            return
        self._on_packet(packet)

    def error_received(self, exc: Exception) -> None:
        """Log socket errors."""
        _LOGGER.debug("Receive error: %s", exc)

    def _ignore(self, msg: str, *args: object) -> None:
        self.ignored += 1
        now = time.monotonic()
        if self._last_log is not None and now - self._last_log < LOG_INTERVAL:
            return
        _LOGGER.debug("Ignoring packet: " + msg + " (%d ignored so far)", *args, self.ignored)
        self._last_log = now


def create_socket(port: int, multicast: bool) -> socket.socket:
    """Create a non-blocking UDP socket bound to the port, joined to the group."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("", port))
        if multicast:
            mreq = struct.pack(
                "4s4s", socket.inet_aton(MULTICAST_GROUP), socket.inet_aton("0.0.0.0")
            )
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        sock.setblocking(False)
    except OSError:
        sock.close()
        raise
    return sock


async def async_start_receiver(
    port: int,
    multicast: bool,
    sender: str | None,
    on_packet: Callable[[SyncPacket], None],
) -> tuple[asyncio.DatagramTransport, SyncProtocol]:
    """Open the UDP endpoint and return its transport and protocol."""
    loop = asyncio.get_running_loop()
    sock = create_socket(port, multicast)
    try:
        return await loop.create_datagram_endpoint(
            lambda: SyncProtocol(on_packet, sender), sock=sock
        )
    except OSError:
        sock.close()
        raise
