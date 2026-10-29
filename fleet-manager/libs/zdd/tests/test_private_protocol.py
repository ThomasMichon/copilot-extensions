"""Framing refuses malformed input and shares, rather than renews, deadlines."""

from __future__ import annotations

import math
import struct
import threading
import time

import pytest

from zdd.private_protocol import (
    MAX_FRAME_BYTES,
    Deadline,
    PeerIdentity,
    PrivateIPCError,
    Refusal,
    read_frame,
    write_frame,
)
from zdd.private_unix import UnixChannel
from zdd.private_windows import WindowsChannel


class MemoryChannel:
    peer = PeerIdentity(1, "1", "fixture")

    def __init__(self, data: bytes = b"") -> None:
        self.data = data
        self.reads: list[int] = []
        self.deadlines: list[Deadline] = []

    def read_exact(self, count: int, deadline: Deadline) -> bytes:
        deadline.remaining()
        self.reads.append(count)
        self.deadlines.append(deadline)
        if len(self.data) < count:
            raise PrivateIPCError(Refusal.CLOSED)
        result, self.data = self.data[:count], self.data[count:]
        return result

    def write_all(self, data: bytes, deadline: Deadline) -> None:
        deadline.remaining()
        self.deadlines.append(deadline)
        self.data += data

    def close(self) -> None:
        pass


@pytest.mark.parametrize("channel_type", [UnixChannel, WindowsChannel])
@pytest.mark.parametrize("count", [-1, True, 1.5, "1"])
def test_native_read_size_refusal_precedes_io(channel_type: type, count: object) -> None:
    channel = object.__new__(channel_type)
    with pytest.raises(PrivateIPCError) as caught:
        channel.read_exact(count, Deadline(1))
    assert caught.value.reason is Refusal.SIZE


@pytest.mark.parametrize("channel_type", [UnixChannel, WindowsChannel])
@pytest.mark.parametrize("data", [None, "secret", bytearray(b"secret"), memoryview(b"secret")])
def test_native_write_type_refusal_precedes_io(channel_type: type, data: object) -> None:
    channel = object.__new__(channel_type)
    with pytest.raises(PrivateIPCError) as caught:
        channel.write_all(data, Deadline(1))
    assert caught.value.reason is Refusal.PROTOCOL
    assert "secret" not in str(caught.value)


def test_roundtrip_and_one_shared_deadline() -> None:
    channel = MemoryChannel()
    deadline = Deadline(1)
    value = {"settings": {"override": "fixture-value"}, "count": 3}
    write_frame(channel, value, deadline)
    body_size = len(channel.data) - 5
    assert read_frame(channel, deadline) == value
    assert channel.reads == [5, body_size]
    assert all(item is deadline for item in channel.deadlines)


@pytest.mark.parametrize("timeout", [0, -1, math.inf, math.nan, True])
def test_invalid_deadline(timeout: float) -> None:
    with pytest.raises(ValueError):
        Deadline(timeout)


def test_deadline_is_not_renewed() -> None:
    deadline = Deadline(0.01)
    time.sleep(0.02)
    with pytest.raises(PrivateIPCError) as caught:
        deadline.remaining()
    assert caught.value.reason is Refusal.DEADLINE


def test_cancellation_precedes_io() -> None:
    cancelled = threading.Event()
    deadline = Deadline(1, cancelled=cancelled)
    cancelled.set()
    channel = MemoryChannel()
    with pytest.raises(PrivateIPCError) as caught:
        write_frame(channel, {"value": "not-sent"}, deadline)
    assert caught.value.reason is Refusal.CANCELLED
    assert not channel.data


@pytest.mark.parametrize(
    "body",
    [
        b"",
        b"[]",
        b"null",
        b'{"duplicate":1,"duplicate":2}',
        b'{"nested":{"x":1,"x":2}}',
        b'{"x":NaN}',
        b'{"x":Infinity}',
        b'{"x":-Infinity}',
        b"\xff",
        b'{"x":"fixture-secret",',
    ],
)
def test_malformed_payload_has_only_fixed_reason(body: bytes) -> None:
    channel = MemoryChannel(struct.pack("!BI", 1, len(body)) + body)
    with pytest.raises(PrivateIPCError) as caught:
        read_frame(channel, Deadline(1))
    assert str(caught.value) == Refusal.PROTOCOL.value
    assert "fixture-secret" not in repr(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "version,size,reason",
    [(2, 2, Refusal.PROTOCOL), (1, MAX_FRAME_BYTES + 1, Refusal.SIZE)],
)
def test_header_refusal_precedes_body_read(version: int, size: int, reason: Refusal) -> None:
    channel = MemoryChannel(struct.pack("!BI", version, size))
    with pytest.raises(PrivateIPCError) as caught:
        read_frame(channel, Deadline(1))
    assert caught.value.reason is reason
    assert channel.reads == [5]


def test_write_size_and_non_json_refusals() -> None:
    for value, reason in [
        ({"large": "x" * MAX_FRAME_BYTES}, Refusal.SIZE),
        ({"value": math.nan}, Refusal.PROTOCOL),
        ({"value": object()}, Refusal.PROTOCOL),
        ([], Refusal.PROTOCOL),
    ]:
        channel = MemoryChannel()
        with pytest.raises(PrivateIPCError) as caught:
            write_frame(channel, value, Deadline(1))
        assert caught.value.reason is reason
        assert channel.data == b""
