"""Composed custody/backend protocol, not installed or separate-process proof."""

import asyncio
from contextlib import asynccontextmanager

import pytest

from agent_mcp.backend import BackendSession
from agent_mcp.config import parse_config
from agent_mcp.custody_contract import MAX_APPLICATION
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
async def composed():
    backends, upstream, output = Backends(), Upstream(), []
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
