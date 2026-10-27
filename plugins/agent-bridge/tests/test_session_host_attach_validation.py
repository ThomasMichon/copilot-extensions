"""Unadmitted legacy attachments must not mutate the current host connection."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest

from agent_bridge.session_host import protocol as proto
from agent_bridge.session_host.host import SessionHost
from test_session_host import _FakeChild


@dataclass
class Frontend:
    incoming: asyncio.StreamReader
    outgoing: asyncio.StreamReader
    writer: MagicMock

    async def receive(self) -> tuple[proto.MsgType | None, bytes] | None:
        return await asyncio.wait_for(proto.read_message(self.outgoing), 1)


def connect(host: SessionHost) -> Frontend:
    incoming, outgoing = asyncio.StreamReader(), asyncio.StreamReader()
    writer = MagicMock(spec=asyncio.StreamWriter)
    writer.write.side_effect = outgoing.feed_data
    writer.drain = AsyncMock()
    writer.wait_closed = AsyncMock()
    writer.close.side_effect = outgoing.feed_eof
    host._accept_front(incoming, writer)
    return Frontend(incoming, outgoing, writer)


async def until(predicate: Callable[[], bool]) -> None:
    async def wait() -> None:
        while not predicate():
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), 1)


async def attach(host: SessionHost) -> Frontend:
    front = connect(host)
    front.incoming.feed_data(
        proto.encode(proto.MsgType.ATTACH, proto.pack_attach(0, b"correct"))
    )
    assert (await front.receive())[0] is proto.MsgType.HELLO
    return front


def rejected_packet(kind: str) -> bytes:
    if kind == "wrong-nonce":
        return proto.encode(proto.MsgType.ATTACH, proto.pack_attach(99, b"wrong"))
    if kind == "missing-nonce":
        return proto.encode(proto.MsgType.ATTACH, proto.pack_attach(99))
    if kind == "wrong-type":
        return proto.encode(proto.MsgType.ACK, proto.pack_u64(99))
    if kind == "short-attach":
        return proto.encode(proto.MsgType.ATTACH, b"short")
    if kind == "oversized":
        return proto._U32.pack(proto.MAX_MESSAGE_BYTES + 1)
    raise AssertionError(kind)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind", ["wrong-nonce", "missing-nonce", "wrong-type", "short-attach", "oversized"],
)
async def test_rejected_attach_preserves_active_frontend(kind: str) -> None:
    child = _FakeChild()
    host = SessionHost(child, nonce="correct")
    host._reader_task = asyncio.create_task(host._reader_loop())
    try:
        good = await attach(host)
        original = host._front
        bad = connect(host)
        bad.incoming.feed_data(rejected_packet(kind))
        assert await bad.receive() is None
        assert host._front is original
        assert not original.closed
        assert host._attachment_epoch == 1
        assert host.ack_cursor == 0
        good.writer.close.assert_not_called()

        good.incoming.feed_data(proto.encode(proto.MsgType.WRITE, b"request\n"))
        await until(lambda: child.stdin.buffer == b"request\n")
        child.feed_frame(b"response\n")
        message = await good.receive()
        assert message[0] is proto.MsgType.FRAME
        assert proto.unpack_frame(message[1]) == (1, b"response\n")
        assert child.killed is False
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_incomplete_attach_preserves_front_until_validated_replacement() -> None:
    child = _FakeChild()
    host = SessionHost(child, nonce="correct")
    host._reader_task = asyncio.create_task(host._reader_loop())
    try:
        good = await attach(host)
        original = host._front
        replacement = connect(host)
        packet = proto.encode(proto.MsgType.ATTACH, proto.pack_attach(0, b"correct"))
        replacement.incoming.feed_data(packet[:-1])
        await asyncio.sleep(0)
        assert host._front is original and not original.closed
        good.writer.close.assert_not_called()
        child.feed_frame(b"before replacement\n")
        assert proto.unpack_frame((await good.receive())[1]) == (1, b"before replacement\n")

        replacement.incoming.feed_data(packet[-1:])
        assert (await replacement.receive())[0] is proto.MsgType.HELLO
        assert original.closed
        good.writer.close.assert_called()
        assert host._front.writer is replacement.writer
        assert host._attachment_epoch == 2
        assert proto.unpack_frame((await replacement.receive())[1]) == (1, b"before replacement\n")
        child.feed_frame(b"after replacement\n")
        assert proto.unpack_frame((await replacement.receive())[1]) == (2, b"after replacement\n")
        replacement.incoming.feed_data(proto.encode(proto.MsgType.WRITE, b"still connected\n"))
        await until(lambda: child.stdin.buffer == b"still connected\n")
    finally:
        await host.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("timer", ["_reap_timer", "_active_reap_timer"])
async def test_rejected_attach_preserves_pending_lifetime_state(timer: str) -> None:
    host = SessionHost(_FakeChild(), nonce="correct")
    pending = asyncio.get_running_loop().call_later(60, lambda: None)
    setattr(host, timer, pending)
    host._graceful_detach = True
    host._ack_cursor = 7
    try:
        bad = connect(host)
        bad.incoming.feed_data(rejected_packet("wrong-nonce"))
        assert await bad.receive() is None
        assert getattr(host, timer) is pending
        assert not pending.cancelled()
        assert host._graceful_detach is True
        assert host.ack_cursor == 7
        assert host._attachment_epoch == 0
        await attach(host)
        assert getattr(host, timer) is None
        assert pending.cancelled()
        assert host._graceful_detach is False
        assert host.ack_cursor == 7
        assert host._attachment_epoch == 1
    finally:
        await host.close()
