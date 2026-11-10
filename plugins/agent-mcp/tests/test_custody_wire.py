"""Cancellation and malformed frames cannot create gaps or replay mutations."""

import asyncio
from contextlib import asynccontextmanager
import json

import pytest

from agent_mcp.custody_wire import MAX_FRAME, MAX_PENDING, Peer
from agent_mcp.custody_contract import MAX_SEQUENCE


@asynccontextmanager
async def connected(handler, *, pump=True):
    peers, tasks = [], []

    async def accept(reader, writer):
        peer = Peer(reader, writer, handler)
        peers.append(peer)
        task = asyncio.create_task(peer.run())
        tasks.append(task)
        await asyncio.gather(task, return_exceptions=True)
        await peer.close()

    server = await asyncio.start_server(accept, "127.0.0.1", 0, limit=MAX_FRAME)
    reader, writer = await asyncio.open_connection(
        "127.0.0.1",
        server.sockets[0].getsockname()[1],
        limit=MAX_FRAME,
    )
    peer = Peer(reader, writer, handler)
    pump_task = asyncio.create_task(peer.run()) if pump else None
    try:
        yield peer, peers, tasks
    finally:
        await peer.close()
        for remote in peers:
            await remote.close()
        await asyncio.gather(
            *([pump_task] if pump_task is not None else []),
            *tasks,
            return_exceptions=True,
        )
        server.close()
        await server.wait_closed()


async def echo(operation, data):
    return data


async def test_cancel_before_publication_does_not_consume_a_sequence():
    async with connected(echo) as (peer, remotes, _):
        await peer._write_lock.acquire()
        request = asyncio.create_task(peer.request("mutate", value="not-sent"))
        await asyncio.sleep(0)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        peer._write_lock.release()
        assert await peer.request("probe", value="first") == {"value": "first"}
        assert peer._counter == remotes[0]._received == 1


async def test_cancel_after_acceptance_retains_credit_until_late_receipt():
    started, release = asyncio.Event(), asyncio.Event()
    mutations = 0

    async def handler(operation, data):
        nonlocal mutations
        if operation == "mutate":
            mutations += 1
            started.set()
            await release.wait()
        return data

    async with connected(handler) as (peer, _, _):
        request = asyncio.create_task(peer.request("mutate", value="accepted"))
        await started.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        assert len(peer._pending) == 1
        assert peer._credits._value == MAX_PENDING - 1
        assert await peer.request("probe", value="live") == {"value": "live"}
        release.set()

        async def settled():
            while peer._pending:
                await asyncio.sleep(0)

        await asyncio.wait_for(settled(), 2)
        assert await peer.request("probe", value="settled") == {"value": "settled"}
        assert mutations == 1


async def test_cancel_during_drain_does_not_forget_published_receipt():
    async with connected(echo) as (peer, _, _):
        original = peer.writer.drain
        entered, release = asyncio.Event(), asyncio.Event()

        async def blocked():
            entered.set()
            await release.wait()
            await original()

        peer.writer.drain = blocked
        request = asyncio.create_task(peer.request("mutate", value="once"))
        await entered.wait()
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        peer.writer.drain = original
        assert await peer.request("probe", value="second") == {"value": "second"}
        assert peer._counter == 2
        assert not peer._pending


@pytest.mark.parametrize("overflow", [False, True])
async def test_published_receipt_releases_receive_credit_but_keeps_cleanup_ownership(
    monkeypatch, overflow,
):
    from agent_mcp import custody_wire

    monkeypatch.setattr(custody_wire, "MAX_PENDING", 1)
    calls = []
    second_started, release = asyncio.Event(), asyncio.Event()

    async def handler(operation, data):
        calls.append(operation)
        if operation == "second":
            second_started.set()
        return data

    async with connected(handler) as (peer, remotes, _):
        while not remotes:
            await asyncio.sleep(0)
        remote = remotes[0]
        original = remote.writer.drain

        async def blocked():
            await release.wait()
            await original()

        remote.writer.drain = blocked
        second = None
        try:
            assert await asyncio.wait_for(peer.request("first", value=1), 1) == {"value": 1}
            assert len(remote._tasks) == 1 and not next(iter(remote._tasks)).done()
            second = asyncio.create_task(peer.request("second", value=2))
            await asyncio.wait_for(second_started.wait(), 1)
            assert len(remote._tasks) == 2
            if overflow:
                peer.writer.write(
                    b'{"sequence":3,"operation":"overflow","data":{}}\n',
                )
                await peer.writer.drain()
                with pytest.raises(ConnectionError, match="unknown"):
                    await asyncio.wait_for(second, 1)
            else:
                release.set()
                assert await asyncio.wait_for(second, 1) == {"value": 2}
                assert not peer._closed and not remote._closed
            assert calls == ["first", "second"]
        finally:
            release.set()
            if second is not None:
                await asyncio.gather(second, return_exceptions=True)


async def test_invalid_outgoing_frame_does_not_consume_a_sequence():
    async with connected(echo) as (peer, remotes, _):
        with pytest.raises(ValueError):
            await peer.request("mutate", value=float("nan"))
        assert await peer.request("probe") == {}
        assert peer._counter == remotes[0]._received == 1


@pytest.mark.parametrize("bad_receipt", [True, 0, -1, "1", MAX_SEQUENCE + 1])
async def test_invalid_receipt_identity_cannot_acknowledge_work(bad_receipt):
    peer = Peer(None, None, echo)
    future = asyncio.get_running_loop().create_future()
    peer._pending[1] = future
    with pytest.raises(ValueError, match="invalid custody receipt"):
        peer._receipt({"receipt": bad_receipt, "result": "not-an-ack"})
    assert not future.done()


async def test_partial_utf8_frame_and_duplicate_sequence_do_not_replay():
    calls = []

    async def handler(operation, data):
        calls.append(data)
        return data

    async with connected(handler, pump=False) as (peer, _, tasks):
        frame = (
            json.dumps(
                {"sequence": 1, "operation": "mutate", "data": {"text": "\u00e9"}},
                ensure_ascii=False,
            )
            + "\n"
        ).encode()
        split = frame.index("\u00e9".encode()) + 1
        peer.writer.write(frame[:split])
        await peer.writer.drain()
        await asyncio.sleep(0)
        assert not calls
        peer.writer.write(frame[split:])
        await peer.writer.drain()
        assert json.loads(await asyncio.wait_for(peer.reader.readline(), 2)) == {
            "receipt": 1,
            "result": {"text": "\u00e9"},
        }
        peer.writer.write(frame)
        await peer.writer.drain()
        assert await asyncio.wait_for(peer.reader.read(), 2) == b""
        await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 2)
        assert calls == [{"text": "\u00e9"}]


async def test_disconnect_reports_unknown_effect_without_replay():
    accepted = asyncio.Event()
    calls = 0

    async def handler(operation, data):
        nonlocal calls
        calls += 1
        accepted.set()
        await asyncio.Event().wait()

    async with connected(handler) as (peer, remotes, _):
        request = asyncio.create_task(peer.request("mutate"))
        await accepted.wait()
        await remotes[0].close()
        with pytest.raises(ConnectionError, match="effects are unknown; no replay"):
            await asyncio.wait_for(request, 2)
        assert calls == 1
        assert not peer._pending


async def test_closed_attachment_does_not_dispatch_buffered_application_work():
    calls = []

    async def handler(operation, data):
        calls.append(operation)

    class ClosedWriter:
        def close(self):
            pass

        async def wait_closed(self):
            pass

    reader = asyncio.StreamReader()
    reader.feed_data(b'{"sequence":1,"operation":"mutate","data":{}}\n')
    reader.feed_eof()
    peer = Peer(reader, ClosedWriter(), handler)
    await peer.close()
    await peer.run()
    assert not calls


async def test_concurrent_requests_publish_contiguous_sequences():
    async with connected(echo) as (peer, remotes, _):
        responses = await asyncio.gather(
            *(peer.request("probe", number=number) for number in range(32)),
        )
        assert responses == [{"number": number} for number in range(32)]
        assert peer._counter == remotes[0]._received == 32
        assert not peer._pending


async def test_oversized_handler_result_refuses_without_losing_the_attachment(monkeypatch):
    from agent_mcp import custody_wire

    async def handler(operation, data):
        return "x" * 1000 if operation == "large" else data

    async with connected(handler) as (peer, _, _):
        monkeypatch.setattr(custody_wire, "MAX_FRAME", 256)
        with pytest.raises(RuntimeError, match="frame exceeds"):
            await peer.request("large")
        assert await peer.request("probe", live=True) == {"live": True}
        assert not peer._closed and not peer._pending


async def test_sequence_exhaustion_refuses_before_publication_without_losing_credit():
    async with connected(echo) as (peer, remotes, _):
        await peer.request("probe")
        peer._counter = remotes[0]._received = MAX_SEQUENCE - 1
        assert await peer.request("last", value=1) == {"value": 1}
        with pytest.raises(ValueError, match="sequence exhausted"):
            await peer.request("overflow")
        assert peer._counter == remotes[0]._received == MAX_SEQUENCE
        assert peer._credits._value == MAX_PENDING and not peer._closed
