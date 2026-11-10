"""Composed custody/backend protocol, not installed or separate-process proof."""

import asyncio
from contextlib import asynccontextmanager
import json

import pytest

from agent_mcp.backend import BackendSession
from agent_mcp import custody_wire
from agent_mcp.config import parse_config
from agent_mcp.custody_contract import MAX_APPLICATION, MAX_CONTROL
from agent_mcp.custody_session import CustodySession
from agent_mcp.custody_wire import MAX_FRAME
from agent_mcp.transports.base import Transport


def configuration():
    return parse_config(
        {"server": {"type": "stdio", "command": ["fixture"]},
         "decorators": [{"type": "rename", "prefix": "test_"}]},
    )


class Upstream(Transport):
    def __init__(self):
        self._emit = None
        self.starts = self.closes = self.ends = 0
        self.messages = []
        self.pending = {}

    async def start(self):
        self.starts += 1

    async def send(self, message):
        self.messages.append(message)
        if message.get("method") == "tools/call":
            self.pending[message["id"]] = message
        elif "method" in message and "id" in message:
            result = (
                {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
                if message["method"] == "tools/list" else {}
            )
            await self._emit_message({"jsonrpc": "2.0", "id": message["id"], "result": result})

    async def complete(self, mid):
        self.pending.pop(mid)
        await self._emit_message({"jsonrpc": "2.0", "id": mid, "result": {"content": []}})

    async def end_input(self):
        self.ends += 1

    async def aclose(self):
        self.closes += 1


class Backends:
    def __init__(self):
        self.instances, self.tasks = [], []
        self.refuse = self.lose_activation = None
        self.server = None
        self.hold_application = False
        self.release_admission = asyncio.Event()

    async def accept(self, reader, writer):
        epoch = len(self.instances) + 1

        def qualify():
            if epoch == self.refuse:
                raise ValueError("fixture preparation refused")
            return configuration()

        instance = BackendSession(reader, writer, {"epoch": epoch}, qualify)
        original = instance.peer.handler

        async def handler(operation, data):
            result = await original(operation, data)
            if (
                operation == "input" and self.hold_application
                and "method" in data["message"]
                and data["message"]["method"] != "notifications/cancelled"
            ):
                await self.release_admission.wait()
            if operation == "activate" and epoch == self.lose_activation:
                await instance.peer.close()
            return result

        instance.peer.handler = handler
        self.instances.append(instance)
        task = asyncio.create_task(instance.run())
        self.tasks.append(task)
        await asyncio.gather(task, return_exceptions=True)
        await instance.peer.close()

    async def connect(self, epoch):
        assert epoch == len(self.instances) + 1
        return await asyncio.open_connection(
            "127.0.0.1", self.server.sockets[0].getsockname()[1], limit=MAX_FRAME,
        )


@asynccontextmanager
async def composed(*, upstream=None):
    backends, upstream, output = Backends(), upstream or Upstream(), []
    backends.server = await asyncio.start_server(
        backends.accept, "127.0.0.1", 0, limit=MAX_FRAME,
    )
    owner = CustodySession(configuration(), output.append, transport=upstream)
    try:
        await owner.start(backends.connect)
        yield owner, backends, upstream, output
    finally:
        backends.release_admission.set()
        if not owner.state.uncertain:
            for mid in tuple(upstream.pending):
                await upstream.complete(mid)
        await owner.close()
        await asyncio.gather(*backends.tasks, return_exceptions=True)
        backends.server.close()
        await backends.server.wait_closed()


async def settled(owner):
    while owner.state.accepted or owner._admissions:
        await asyncio.sleep(0)


async def call(owner, mid):
    await owner.submit(
        {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
         "params": {"name": "test_echo", "arguments": {}}},
    )


async def test_handover_waits_for_mutation_and_server_response_then_uses_new_pipeline():
    async with composed() as (owner, backends, upstream, output):
        await owner.submit({"jsonrpc": "2.0", "id": 0, "method": "initialize"})
        await owner.submit({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        await settled(owner)
        old = backends.instances[0].session
        await call(owner, 2)
        move = asyncio.create_task(owner.handover(backends.connect))
        while not owner.state.fenced:
            await asyncio.sleep(0)
        await upstream._emit_message(
            {"jsonrpc": "2.0", "id": "server-1", "method": "sampling/createMessage"},
        )
        await owner.submit({"jsonrpc": "2.0", "id": "server-1", "result": {}})
        await upstream.complete(2)
        await asyncio.wait_for(move, 2)
        new = backends.instances[1].session
        assert new._pipeline is not old._pipeline
        assert new._pipeline.decorators[0] is not old._pipeline.decorators[0]
        await call(owner, 3)
        while 3 not in upstream.pending:
            await asyncio.sleep(0)
        assert upstream.messages[-1]["params"]["name"] == "echo"
        await upstream.complete(3)
        await settled(owner)
        assert sum(message.get("method") == "initialize" for message in upstream.messages) == 1
        assert upstream.starts == 1 and upstream.ends == upstream.closes == 0
        assert [message["id"] for message in output if "method" not in message] == [0, 1, 2, 3]
        assert owner.state.buffered_bytes == 0
    assert upstream.ends == upstream.closes == 1


async def test_empty_string_request_ids_survive_reply_and_handover():
    async with composed() as (owner, backends, upstream, output):
        await owner.submit({"jsonrpc": "2.0", "id": "", "method": "initialize"})
        await settled(owner)
        await upstream._emit_message(
            {"jsonrpc": "2.0", "id": "", "method": "sampling/createMessage"},
        )
        response = {"jsonrpc": "2.0", "id": "", "result": {}}
        await owner.submit(response)
        await settled(owner)
        await owner.handover(backends.connect)
        await owner.submit({"jsonrpc": "2.0", "id": "", "method": "tools/list"})
        await settled(owner)
        assert [item["id"] for item in output] == ["", "", ""]
        assert upstream.messages.count(response) == 1
        assert sum(item.get("method") == "initialize" for item in upstream.messages) == 1
        assert upstream.starts == 1 and not owner.state.uncertain


async def test_capacity_one_replenishes_backend_credit_before_next_input(monkeypatch):
    from agent_mcp import backend, custody_session, custody_state

    for module in (backend, custody_session, custody_state):
        monkeypatch.setattr(module, "MAX_APPLICATION", 1)
    async with composed() as (owner, backends, upstream, output):
        await call(owner, 1)
        for mid in range(2, 10):
            waiting = asyncio.create_task(owner.submit(
                {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                 "params": {"name": "test_echo", "arguments": {}}}, wait=True,
            ))
            await asyncio.sleep(0)
            assert not waiting.done()
            await upstream.complete(mid - 1)
            await asyncio.wait_for(waiting, 1)
            while mid not in upstream.pending:
                await asyncio.sleep(0)
            assert backends.instances[0]._application == 1
        await upstream.complete(9)
        await settled(owner)
        assert [item["id"] for item in output] == list(range(1, 10))
        assert upstream.starts == 1 and not owner.state.uncertain


@pytest.mark.parametrize("lane", ["application", "control"])
async def test_coalesced_settlement_receipt_preserves_backend_input_credit(monkeypatch, lane):
    from agent_mcp import backend, custody_session, custody_state

    for module in (backend, custody_session, custody_state):
        monkeypatch.setattr(module, "MAX_APPLICATION" if lane == "application" else "MAX_CONTROL", 1)
    async with composed() as (owner, backends, upstream, output):
        release = asyncio.Event()
        original_send = upstream.send

        async def send(message):
            if lane == "control" and message.get("params", {}).get("requestId") == 1:
                await release.wait()
            await original_send(message)

        upstream.send = send

        def message(mid):
            if lane == "control":
                return {"jsonrpc": "2.0", "method": "notifications/cancelled",
                        "params": {"requestId": mid}}
            return {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                    "params": {"name": "test_echo", "arguments": {}}}

        await owner.submit(message(1))
        link = owner._links[owner.state.current]
        original = link.peer.writer

        class CoalescedWriter:
            pending = b""
            combined = False

            def write(self, wire):
                packet = json.loads(wire)
                if packet.get("result") == {"settled": 1}:
                    self.pending = wire
                    return
                if self.pending and packet.get("operation") == "input":
                    wire, self.pending = self.pending + wire, b""
                    self.combined = True
                original.write(wire)

            def __getattr__(self, name):
                return getattr(original, name)

        writer = CoalescedWriter()
        link.peer.writer = writer
        waiting = asyncio.create_task(owner.submit(message(2), wait=True))
        try:
            await asyncio.sleep(0)
            assert not waiting.done()
            if lane == "application":
                await upstream.complete(1)
            else:
                release.set()
            await asyncio.wait_for(waiting, 1)
            while len(upstream.messages) < 2:
                await asyncio.sleep(0)
            assert writer.combined and not writer.pending
            if lane == "application":
                await upstream.complete(2)
            await settled(owner)
            assert [item["id"] for item in output] == ([1, 2] if lane == "application" else [])
            if lane == "control":
                assert upstream.messages == [message(1), message(2)]
            else:
                assert [item["id"] for item in upstream.messages] == [1, 2]
            assert not owner.state.uncertain
        finally:
            release.set()
            await asyncio.gather(waiting, return_exceptions=True)


async def test_failed_preparation_restores_owned_epoch_without_reopening_or_replaying():
    async with composed() as (owner, backends, upstream, output):
        backends.refuse = 2
        old = owner.state.current
        with pytest.raises(RuntimeError, match="preparation refused"):
            await owner.handover(backends.connect)
        assert owner.state.current is old and not owner.state.fenced
        await owner.submit({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        await settled(owner)
        assert len(upstream.messages) == len(output) == 1
        assert upstream.starts == 1 and upstream.closes == 0


async def test_lost_activation_ack_keeps_new_fence_and_never_resumes_old_epoch():
    async with composed() as (owner, backends, upstream, output):
        old = owner.state.current
        backends.lose_activation = 2
        with pytest.raises(ConnectionError, match="unknown"):
            await owner.handover(backends.connect)
        assert owner.state.uncertain and owner.state.current is not old
        with pytest.raises(ConnectionError, match="unknown"):
            await owner.submit({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert not upstream.messages and not output
        assert upstream.starts == 1 and upstream.closes == 0


async def test_cancelled_handover_resumes_old_epoch_without_cancelling_accepted_mutation():
    async with composed() as (owner, backends, upstream, output):
        await call(owner, 1)
        move = asyncio.create_task(owner.handover(backends.connect))
        while backends.instances[0].state != "quiescing":
            await asyncio.sleep(0)
        move.cancel()
        with pytest.raises(asyncio.CancelledError):
            await move
        assert not owner.state.fenced and not owner.state.uncertain
        assert not backends.instances[0].accepted[1].done()
        await upstream.complete(1)
        await settled(owner)
        assert len(upstream.messages) == len(output) == 1
        assert backends.instances[0].state == "active"


@pytest.mark.parametrize("resume_failure", [False, True])
async def test_cancelled_handover_before_quiesce_settles_fence(resume_failure):
    async with composed() as (owner, backends, upstream, output):
        backends.hold_application = True
        admission = asyncio.create_task(owner.submit(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        ))
        move = None
        try:
            while not output or not owner._admissions:
                await asyncio.sleep(0)
            instance = backends.instances[0]
            original = instance.peer.handler

            async def handler(operation, data):
                if operation == "resume" and resume_failure:
                    raise ValueError("fixture resumption refused")
                return await original(operation, data)

            instance.peer.handler = handler
            move = asyncio.create_task(owner.handover(backends.connect))
            while not owner.state.fenced:
                await asyncio.sleep(0)
            assert instance.state == "active"
            move.cancel()
            with pytest.raises((asyncio.CancelledError, RuntimeError)):
                await move
            if resume_failure:
                assert owner.state.uncertain
                with pytest.raises(ConnectionError, match="unknown"):
                    await owner.submit({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
            else:
                assert not owner.state.fenced and not owner.state.uncertain
                backends.hold_application = False
                await owner.submit({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
                backends.release_admission.set()
                await admission
                await settled(owner)
                assert [item["id"] for item in output] == [1, 2]
            assert upstream.starts == 1 and len(backends.instances) == 1
        finally:
            backends.release_admission.set()
            await asyncio.gather(admission, return_exceptions=True)
            if move is not None:
                await asyncio.gather(move, return_exceptions=True)


async def test_repeated_cancelled_handovers_invalidate_remote_quiesce_waiters(monkeypatch):
    monkeypatch.setattr(custody_wire, "MAX_PENDING", 3)
    async with composed() as (owner, backends, upstream, output):
        await call(owner, 1)
        instance = backends.instances[0]
        original = instance.peer.handler
        entered, finished = [], []

        async def handler(operation, data):
            if operation != "quiesce":
                return await original(operation, data)
            entered.append(None)
            try:
                return await original(operation, data)
            finally:
                finished.append(None)

        instance.peer.handler = handler
        for count in (1, 2, 3):
            move = asyncio.create_task(owner.handover(backends.connect))
            while len(entered) < count:
                await asyncio.sleep(0)
            move.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(move, 1)
            assert instance.state == "active" and not owner.state.fenced
            assert not instance.accepted[1].done()
        await upstream.complete(1)
        await settled(owner)
        while len(finished) < 3:
            await asyncio.sleep(0)
        assert instance.state == "active"
        await owner.submit({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        await settled(owner)
        await owner.handover(backends.connect)
        await owner.submit({"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        await settled(owner)
        assert [item["id"] for item in output] == [1, 2, 3]
        assert sum(item.get("method") == "tools/call" for item in upstream.messages) == 1
        assert upstream.starts == 1 and not owner.state.uncertain


async def test_unacknowledged_publications_are_bounded_without_spending_control_credit():
    async with composed() as (owner, backends, upstream, output):
        backends.hold_application = True
        admissions = [
            asyncio.create_task(
                owner.submit({"jsonrpc": "2.0", "id": mid, "method": "tools/list"}),
            )
            for mid in range(MAX_APPLICATION)
        ]
        while len(output) != MAX_APPLICATION or owner.state.accepted:
            await asyncio.sleep(0)
        assert len(owner._admissions) == MAX_APPLICATION
        with pytest.raises(ValueError, match="publication window"):
            await owner.submit({"jsonrpc": "2.0", "id": 1000, "method": "tools/list"})
        await owner.submit(
            {"jsonrpc": "2.0", "method": "notifications/cancelled", "params": {"requestId": 0}},
        )
        backends.release_admission.set()
        await asyncio.gather(*admissions)
        await settled(owner)
        assert len(upstream.messages) == MAX_APPLICATION + 1


@pytest.mark.parametrize("failure", ["invalid-message", "server-request-overflow"])
async def test_upstream_reader_failure_fences_and_wakes_migration_and_admission(failure):
    async with composed() as (owner, backends, upstream, output):
        await call(owner, 1)
        move = asyncio.create_task(owner.handover(backends.connect))
        while not owner.state.fenced:
            await asyncio.sleep(0)
        waiting = asyncio.create_task(owner.submit(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, wait=True,
        ))
        await asyncio.sleep(0)
        assert not waiting.done()
        if failure == "server-request-overflow":
            for mid in range(MAX_CONTROL):
                await upstream._emit_message({
                    "jsonrpc": "2.0", "id": f"server-{mid}", "method": "sampling/createMessage",
                })
            message = {"jsonrpc": "2.0", "id": "overflow", "method": "sampling/createMessage"}
        else:
            message = {"jsonrpc": "invalid", "method": "notifications/invalid"}
        with pytest.raises(ValueError):
            await upstream._emit_message(message)
        assert owner.state.uncertain
        with pytest.raises(ConnectionError, match="unknown"):
            await asyncio.wait_for(waiting, 2)
        with pytest.raises(ConnectionError, match="unknown"):
            await asyncio.wait_for(move, 2)
        assert len(upstream.messages) == 1 and upstream.starts == 1
    assert upstream.ends == upstream.closes == 1


async def test_clean_close_keeps_backend_attached_through_final_upstream_notification():
    async with composed() as (owner, backends, upstream, output):
        async def end_input():
            upstream.ends += 1
            await upstream._emit_message(
                {"jsonrpc": "2.0", "method": "notifications/final", "params": {"data": "done"}},
            )
        upstream.end_input = end_input
    assert [message["method"] for message in output] == ["notifications/final"]
    assert not owner.state.uncertain
    assert upstream.ends == upstream.closes == 1


async def test_unexpected_upstream_reader_eof_terminates_waiting_backend_without_replay():
    upstream = Upstream()
    upstream._reader_task = asyncio.get_running_loop().create_future()
    async with composed(upstream=upstream) as (owner, backends, upstream, output):
        await call(owner, 1)
        move = asyncio.create_task(owner.handover(backends.connect))
        while not owner.state.fenced:
            await asyncio.sleep(0)
        upstream._reader_task.set_result(None)
        with pytest.raises(ConnectionError, match="unknown"):
            await asyncio.wait_for(move, 2)
        assert owner.state.uncertain and upstream.starts == 1
        assert len(upstream.messages) == 1 and not output
    assert upstream.ends == upstream.closes == 1


async def test_oversized_checkpoint_refuses_before_commit_and_preserves_owned_backend(monkeypatch):
    from agent_mcp import custody_wire

    async with composed() as (owner, backends, upstream, output):
        monkeypatch.setattr(custody_wire, "MAX_FRAME", 1024)
        old = owner.state.current
        backends.instances[0].session._pipeline.decorators[0]._reverse = {"large": "x" * 1000}
        with pytest.raises(RuntimeError, match="frame budget"):
            await owner.handover(backends.connect)
        assert owner.state.current is old and not owner.state.uncertain
        assert not owner.state.fenced and len(backends.instances) == 1
        await owner.submit({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        await settled(owner)
        assert output[-1]["id"] == 1 and upstream.starts == 1
