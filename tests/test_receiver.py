"""Packet decoding, filtering and frame counter handling."""

from __future__ import annotations

import asyncio
import logging
import socket
import struct
from unittest.mock import MagicMock, patch

import pytest

from custom_components.wled_audio_receiver.receiver import (
    FrameFilter,
    SyncPacket,
    SyncProtocol,
    async_start_receiver,
    create_socket,
    decode,
)

from .conftest import make_packet


def test_decode_fields() -> None:
    """All fields decode at their offsets."""
    fft = list(range(0, 160, 10))
    packet = decode(
        make_packet(
            fft=fft,
            sample_smth=37.5,
            counter=42,
            pressure=(12, 128),
            sample_raw=40.0,
            peak=1,
            zero_crossings=123,
            magnitude=456.25,
            major_peak=1000.0,
        )
    )
    assert packet == SyncPacket(
        pressure=12.5,
        sample_raw=40.0,
        sample_smth=37.5,
        sample_peak=True,
        frame_counter=42,
        fft_result=tuple(fft),
        zero_crossing_count=123,
        fft_magnitude=456.25,
        fft_major_peak=1000.0,
    )


def test_decode_layout() -> None:
    """The packet is 44 bytes, little-endian, with the documented offsets."""
    data = make_packet(fft=[7] * 16, counter=9, zero_crossings=0x0102)
    assert len(data) == 44
    assert data[:6] == b"00002\0"
    assert data[17] == 9
    assert data[18:34] == bytes([7] * 16)
    assert data[34:36] == b"\x02\x01"


@pytest.mark.parametrize(
    "data",
    [
        b"",
        make_packet()[:43],
        make_packet() + b"\0",
        make_packet(header=b"00001\0"),
        make_packet(header=b"00002 "),
        b"x" * 44,
    ],
)
def test_decode_rejects(data: bytes) -> None:
    """Wrong sizes and headers are not packets."""
    assert decode(data) is None


def test_frame_filter_newer_only() -> None:
    """Frames that are not newer are dropped."""
    frames = FrameFilter()
    assert frames.accept(5)
    assert frames.accept(6)
    assert not frames.accept(6)
    assert not frames.accept(4)
    assert frames.accept(10)


def test_frame_filter_wraps() -> None:
    """255 is followed by 1, as the proxy sends it."""
    frames = FrameFilter()
    assert frames.accept(254)
    assert frames.accept(255)
    assert frames.accept(1)
    assert frames.accept(2)
    assert not frames.accept(255)


def test_frame_filter_window() -> None:
    """A jump of 127 is newer; 128 or more is treated as older, as (int8) does."""
    frames = FrameFilter()
    assert frames.accept(1)
    assert frames.accept(128)
    assert not frames.accept(1)


def test_frame_filter_zero_always_accepted() -> None:
    """Counter 0 is a legacy sender and is always accepted."""
    frames = FrameFilter()
    assert frames.accept(0)
    assert frames.accept(0)
    assert frames.accept(100)
    assert frames.accept(0)
    assert frames.accept(0)


def test_frame_filter_reset() -> None:
    """After a reset, a restarted sender is accepted."""
    frames = FrameFilter()
    assert frames.accept(100)
    assert not frames.accept(1)  # (int8)(1 - 100) = -99
    frames.reset()
    assert frames.accept(1)


def test_protocol_forwards_and_drops() -> None:
    """Valid, newer packets are forwarded; others are counted."""
    received: list[SyncPacket] = []
    protocol = SyncProtocol(received.append)
    protocol.datagram_received(make_packet(counter=1), ("192.0.2.1", 1))
    protocol.datagram_received(make_packet(counter=1), ("192.0.2.1", 1))
    protocol.datagram_received(b"junk", ("192.0.2.1", 1))
    protocol.datagram_received(make_packet(counter=2), ("192.0.2.2", 1))
    assert [p.frame_counter for p in received] == [1, 2]
    assert protocol.dropped == 1
    assert protocol.ignored == 1


def test_protocol_sender_filter() -> None:
    """With a sender set, packets from other addresses are ignored."""
    received: list[SyncPacket] = []
    protocol = SyncProtocol(received.append, "192.0.2.1")
    protocol.datagram_received(make_packet(counter=1), ("192.0.2.2", 1))
    protocol.datagram_received(make_packet(counter=2), ("192.0.2.1", 1))
    assert [p.frame_counter for p in received] == [2]
    assert protocol.ignored == 1


def test_protocol_logs_ignored_at_most_once_per_minute(caplog: pytest.LogCaptureFixture) -> None:
    """Ignored packets are rate-limited in the log."""
    protocol = SyncProtocol(lambda p: None)
    caplog.set_level(logging.DEBUG, logger="custom_components.wled_audio_receiver.receiver")
    with patch("custom_components.wled_audio_receiver.receiver.time.monotonic") as now:
        now.return_value = 1000.0
        protocol.datagram_received(b"a", ("192.0.2.1", 1))
        now.return_value = 1030.0
        protocol.datagram_received(b"b", ("192.0.2.1", 1))
        now.return_value = 1061.0
        protocol.datagram_received(b"c", ("192.0.2.1", 1))
    messages = [r.getMessage() for r in caplog.records if "Ignoring" in r.getMessage()]
    assert len(messages) == 2
    assert "3 ignored so far" in messages[1]
    assert protocol.ignored == 3


def test_create_socket_joins_group() -> None:
    """Multicast on joins 239.0.0.1; off does not."""
    sock = MagicMock()
    with patch("socket.socket", return_value=sock):
        create_socket(11988, True)
    sock.bind.assert_called_once_with(("", 11988))
    mreq = struct.pack("4s4s", socket.inet_aton("239.0.0.1"), socket.inet_aton("0.0.0.0"))
    sock.setsockopt.assert_any_call(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

    sock = MagicMock()
    with patch("socket.socket", return_value=sock):
        create_socket(11988, False)
    assert all(c.args[1] != socket.IP_ADD_MEMBERSHIP for c in sock.setsockopt.call_args_list)


def test_create_socket_closes_on_error() -> None:
    """A failed bind closes the socket and raises."""
    sock = MagicMock()
    sock.bind.side_effect = OSError("in use")
    with patch("socket.socket", return_value=sock), pytest.raises(OSError):
        create_socket(11988, False)
    sock.close.assert_called_once()


async def test_receive_over_udp(socket_enabled: None) -> None:
    """A packet sent to the bound port over loopback arrives decoded."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()

    got: asyncio.Future[SyncPacket] = asyncio.get_running_loop().create_future()
    transport, _ = await async_start_receiver(port, False, "127.0.0.1", got.set_result)
    try:
        sender = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sender.sendto(make_packet(counter=3, sample_smth=12.0), ("127.0.0.1", port))
        sender.close()
        packet = await asyncio.wait_for(got, 2)
    finally:
        transport.close()
    assert packet.frame_counter == 3
    assert packet.sample_smth == 12.0
