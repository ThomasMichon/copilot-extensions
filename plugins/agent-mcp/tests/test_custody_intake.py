"""Client-reader progress and explicit bounds, including real custody peers."""

import asyncio
import json

import pytest

from agent_mcp import custody_intake as intake
from agent_mcp.custody_contract import MAX_APPLICATION, MAX_CONTROL
from agent_mcp.custody_wire import MAX_FRAME
from .test_custody_session import composed, settled


def feed(reader, message):
    reader.feed_data((json.dumps(message) + "\n").encode())


def application(mid):
    return {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
            "params": {"name": "echo", "arguments": {}}}


def cancellation(mid):
    return {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": mid}}


class Blocked:
    def __init__(self):
        self.started, self.published, self.control = [], [], []
        self.release = asyncio.Event()
        self.control_ready = asyncio.Event()

    async def submit(self, message, *, wait):
        assert wait
        if "method" in message and message["method"] != "notifications/cancelled":
            identity = message.get("id", message["method"])
            self.started.append(identity)
            await self.release.wait()
            self.published.append(identity)
        else:
            self.control.append(message)
            self.control_ready.set()


async def test_full_application_queue_keeps_independent_controls_readable_and_ordered():
    reader, owner = asyncio.StreamReader(limit=MAX_FRAME), Blocked()
    session = intake.ClientIntake(owner, reader)
    run = asyncio.create_task(session.run())
    for mid in range(MAX_APPLICATION):
        feed(reader, application(mid))
    # This cancellation depends on queued admission, but must not block a reply.
    feed(reader, cancellation(MAX_APPLICATION - 1))
    response = {"jsonrpc": "2.0", "id": "server-1", "result": {}}
    feed(reader, response)
    try:
        await asyncio.wait_for(owner.control_ready.wait(), 1)
        assert owner.started == [0] and not owner.published
        assert owner.control == [response]
        owner.release.set()
        reader.feed_eof()
        await asyncio.wait_for(run, 1)
        assert owner.published == list(range(MAX_APPLICATION))
        assert owner.control == [response, cancellation(MAX_APPLICATION - 1)]
    finally:
        owner.release.set()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


async def test_full_notification_queue_does_not_make_reader_await_dispatch():
    reader, owner = asyncio.StreamReader(limit=MAX_FRAME), Blocked()
    session = intake.ClientIntake(owner, reader)
    run = asyncio.create_task(session.run())
    for _ in range(MAX_APPLICATION):
        feed(reader, {"jsonrpc": "2.0", "method": "notifications/fixture"})
    response = {"jsonrpc": "2.0", "id": "server-1", "result": {}}
    feed(reader, response)
    try:
        await asyncio.wait_for(owner.control_ready.wait(), 1)
        assert owner.started == ["notifications/fixture"] and not owner.published
        assert owner.control == [response]
        owner.release.set()
        reader.feed_eof()
        await asyncio.wait_for(run, 1)
        assert owner.published == ["notifications/fixture"] * MAX_APPLICATION
    finally:
        owner.release.set()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


@pytest.mark.parametrize("lane", ["application", "control"])
async def test_count_overflow_is_terminal_without_restarting_or_replaying(lane):
    reader, owner = asyncio.StreamReader(limit=MAX_FRAME), Blocked()
    session = intake.ClientIntake(owner, reader)
    limit = MAX_APPLICATION if lane == "application" else MAX_CONTROL
    for mid in range(limit + 1):
        feed(reader, application(mid) if lane == "application" else cancellation(mid))
    reader.feed_eof()
    with pytest.raises(intake.IntakeOverflow, match="overflow"):
        await session.run()
    before = list(owner.started), list(owner.published), list(owner.control)
    with pytest.raises(ValueError, match="restarted or replayed"):
        await session.run()
    assert before == (owner.started, owner.published, owner.control)


async def test_byte_saturation_is_independent_of_control_credit(monkeypatch):
    monkeypatch.setattr(intake, "MAX_BUFFERED_BYTES", 200)
    reader, owner = asyncio.StreamReader(limit=MAX_FRAME), Blocked()
    session = intake.ClientIntake(owner, reader)
    run = asyncio.create_task(session.run())
    feed(reader, application(1))
    feed(reader, {"jsonrpc": "2.0", "method": "notify", "params": {"data": "x" * 30}})
    feed(reader, cancellation(999))
    try:
        await asyncio.wait_for(owner.control_ready.wait(), 1)
        assert owner.control == [cancellation(999)]
        feed(reader, application(2))
        with pytest.raises(intake.IntakeOverflow):
            await run
        assert not owner.published
    finally:
        owner.release.set()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


async def test_cancelling_intake_does_not_cancel_an_admitted_mutation():
    async with composed() as (owner, backends, upstream, output):
        backends.hold_application = True
        reader = asyncio.StreamReader(limit=MAX_FRAME)
        session = intake.ClientIntake(owner, reader)
        run = asyncio.create_task(session.run())
        feed(reader, application(1))
        while 1 not in upstream.pending:
            await asyncio.sleep(0)
        run.cancel()
        with pytest.raises(asyncio.CancelledError):
            await run
        assert not backends.instances[0].accepted[1].done()
        assert upstream.starts == 1 and upstream.closes == 0
        backends.release_admission.set()
        await upstream.complete(1)
        await settled(owner)
        assert [item["id"] for item in output] == [1]


async def test_real_capacity_wait_does_not_block_control_or_replay_queued_calls():
    async with composed() as (owner, backends, upstream, output):
        reader = asyncio.StreamReader(limit=MAX_FRAME)
        session = intake.ClientIntake(owner, reader)
        run = asyncio.create_task(session.run())
        for mid in range(MAX_APPLICATION):
            feed(reader, application(mid))
        while len(upstream.pending) != MAX_APPLICATION:
            await asyncio.sleep(0)
        await upstream._emit_message(
            {"jsonrpc": "2.0", "id": "server-1", "method": "sampling/createMessage"},
        )
        feed(reader, application(MAX_APPLICATION))
        feed(reader, {"jsonrpc": "2.0", "id": "server-1", "result": {}})
        feed(reader, cancellation(0))
        try:
            while not any(item.get("method") == "notifications/cancelled" for item in upstream.messages):
                await asyncio.sleep(0)
            assert {"jsonrpc": "2.0", "id": "server-1", "result": {}} in upstream.messages
            assert len(upstream.pending) == MAX_APPLICATION
            await upstream.complete(0)
            while MAX_APPLICATION not in upstream.pending:
                await asyncio.sleep(0)
            reader.feed_eof()
            await asyncio.wait_for(run, 1)
            for mid in tuple(upstream.pending):
                await upstream.complete(mid)
            await settled(owner)
            assert sum(item.get("method") == "tools/call" for item in upstream.messages) == MAX_APPLICATION + 1
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)


async def test_handover_fence_queues_application_but_keeps_server_response_readable():
    async with composed() as (owner, backends, upstream, output):
        await owner.submit(application(1))
        move = asyncio.create_task(owner.handover(backends.connect))
        while not owner.state.fenced:
            await asyncio.sleep(0)
        reader = asyncio.StreamReader(limit=MAX_FRAME)
        session = intake.ClientIntake(owner, reader)
        run = asyncio.create_task(session.run())
        await upstream._emit_message(
            {"jsonrpc": "2.0", "id": "server-1", "method": "sampling/createMessage"},
        )
        feed(reader, application(2))
        feed(reader, {"jsonrpc": "2.0", "id": "server-1", "result": {}})
        try:
            while owner.state.server_requests:
                await asyncio.sleep(0)
            assert 2 not in upstream.pending
            await upstream.complete(1)
            await asyncio.wait_for(move, 1)
            while 2 not in upstream.pending:
                await asyncio.sleep(0)
            reader.feed_eof()
            await asyncio.wait_for(run, 1)
            await upstream.complete(2)
            await settled(owner)
            assert len(backends.instances) == 2 and upstream.starts == 1
            assert [item["id"] for item in output if "method" not in item] == [1, 2]
        finally:
            run.cancel()
            await asyncio.gather(run, return_exceptions=True)
            if not move.done():
                move.cancel()
            await asyncio.gather(move, return_exceptions=True)


@pytest.mark.parametrize("frame", [b'{"jsonrpc":"2.0"', b'[]\n', b'{"jsonrpc":"2.0","id":true,"method":"x"}\n'])
async def test_malformed_input_is_not_published(frame):
    reader, owner = asyncio.StreamReader(limit=MAX_FRAME), Blocked()
    reader.feed_data(frame)
    reader.feed_eof()
    with pytest.raises(ValueError):
        await intake.ClientIntake(owner, reader).run()
    assert not owner.started and not owner.published and not owner.control
