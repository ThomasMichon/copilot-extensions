"""Authenticated current execution epochs over a retained private connection."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

from agent_mcp import backend, checkpoint as cp, execution_context as ec, incoming
from agent_mcp.config import parse_config
from agent_mcp.custody_contract import CONTRACT, CHECKPOINT_TRACE_CAPABILITY, supports
from agent_mcp.custody_wire import MAX_FRAME, Peer
from agent_mcp.request_context import capture_context


def configuration():
    return parse_config(
        {
            "server": {"type": "stdio", "command": ["fixture"]},
            "decorators": [{"type": "rename", "prefix": "test_"}],
        }
    )


class Custody:
    def __init__(self):
        self.messages, self.outputs, self.settled = [], [], []
        self.opened = []
        self.inflight = asyncio.Event()
        self.peer = None
        self.epoch = 1
        self.origin = 0

    async def upstream(self, message):
        return await self.peer.request("upstream", epoch=self.epoch, message=message)

    async def handle(self, operation, data):
        assert data["epoch"] == self.epoch
        if operation == "open":
            self.opened.append(data["identity"])
            return {"retained": len(self.opened) > 1}
        if operation == "send":
            message = data["message"]
            self.messages.append(message)
            if message.get("method") == "tools/call":
                self.inflight.set()
            elif "method" in message and "id" in message:
                result = (
                    {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
                    if message["method"] == "tools/list" else {}
                )
                await self.upstream({"jsonrpc": "2.0", "id": message["id"], "result": result})
            return {"sent": True}
        if operation == "output":
            self.outputs.append(data)
            return {"delivered": True}
        if operation == "settled":
            self.settled.append(data["origin"])
            return {"settled": data["origin"]}
        raise AssertionError(operation)

    async def input(self, message, *, origin=None):
        self.origin += 1
        return await self.peer.request(
            "input", epoch=self.epoch,
            origin=self.origin if origin is None else origin, message=message,
        )


@asynccontextmanager
async def attached(
    custody, binding, *, snapshot=None, qualify=None, activate=True, binding_data=None,
):
    instances, tasks = [], []
    cfg = configuration()

    async def accept(reader, writer):
        instance = backend.BackendSession(
            reader, writer, {"epoch": custody.epoch}, qualify or (lambda: cfg),
        )
        instances.append(instance)
        task = asyncio.create_task(instance.run())
        tasks.append(task)
        await asyncio.gather(task, return_exceptions=True)
        await instance.peer.close()

    server = await asyncio.start_server(accept, "127.0.0.1", 0, limit=MAX_FRAME)
    reader, writer = await asyncio.open_connection(
        "127.0.0.1", server.sockets[0].getsockname()[1], limit=MAX_FRAME,
    )
    custody.peer = Peer(reader, writer, custody.handle)
    pump = asyncio.create_task(custody.peer.run())
    try:
        await custody.peer.request(
            "prepare", epoch=custody.epoch, contract=CONTRACT,
            binding=binding.wire() if binding_data is None else binding_data,
            checkpoint=snapshot,
        )
        if activate:
            await custody.peer.request("activate", epoch=custody.epoch)
        yield instances[0]
    finally:
        await custody.peer.close()
        await asyncio.gather(pump, *tasks, return_exceptions=True)
        server.close()
        await server.wait_closed()


async def test_new_epoch_rebuilds_pipeline_and_restores_data_without_initialize_replay():
    custody, binding = Custody(), cp.Binding.issue()
    async with attached(custody, binding) as old:
        await custody.input({"jsonrpc": "2.0", "id": 0, "method": "initialize"})
        await custody.input({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        await custody.peer.request("quiesce", epoch=1)
        snapshot = await custody.peer.request("checkpoint", epoch=1)
        old_pipeline = old.session._pipeline
        assert custody.settled == [1, 2]
    custody.epoch = 2
    async with attached(custody, binding, snapshot=snapshot) as new:
        assert new.session._pipeline is not old_pipeline
        assert new.session._pipeline.decorators[0] is not old_pipeline.decorators[0]
        await custody.input(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": "test_echo", "arguments": {}}},
        )
        await custody.inflight.wait()
        assert custody.messages[-1]["params"]["name"] == "echo"
        await custody.upstream({"jsonrpc": "2.0", "id": 2, "result": {"content": []}})
        await custody.peer.request("quiesce", epoch=2)
    assert custody.opened == [binding.identity(configuration())] * 2
    assert sum(message.get("method") == "initialize" for message in custody.messages) == 1
    assert [item["message"]["id"] for item in custody.outputs] == [0, 1, 2]


async def test_opt_in_trace_binding_restores_across_three_backend_epochs(monkeypatch):
    custody, binding = Custody(), cp.Binding.issue(trace_bookkeeping=1)
    snapshot, pipelines = None, []
    for epoch, trace in enumerate(("outgoing", "incoming", "rollback"), start=1):
        custody.epoch = epoch
        monkeypatch.setenv("COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS", trace)
        async with attached(custody, binding, snapshot=snapshot) as current:
            pipelines.append(current.session._pipeline)
            await custody.input({
                "jsonrpc": "2.0", "id": epoch,
                "method": "initialize" if epoch == 1 else "tools/list",
            })
            await custody.peer.request("quiesce", epoch=epoch)
            snapshot = await custody.peer.request("checkpoint", epoch=epoch)
            assert snapshot["trace_bookkeeping"] == 1
    assert len({id(pipeline) for pipeline in pipelines}) == 3
    assert len(set(custody.opened)) == 1
    assert sum(item.get("method") == "initialize" for item in custody.messages) == 1
    assert [item["message"]["id"] for item in custody.outputs] == [1, 2, 3]


@pytest.mark.parametrize("mode", [True, False, -1, 2, "1", None])
async def test_invalid_binding_mode_is_refused_before_configuration_or_upstream(mode):
    custody, binding, loaded = Custody(), cp.Binding.issue(), []

    def qualify():
        loaded.append(True)
        return configuration()

    with pytest.raises(RuntimeError, match="identity mode"):
        async with attached(
            custody, binding, qualify=qualify,
            binding_data={**binding.wire(), "trace_bookkeeping": mode},
        ):
            pass
    assert loaded == custody.opened == custody.messages == []


async def test_full_application_window_keeps_cancellation_and_server_responses_readable():
    custody, binding = Custody(), cp.Binding.issue()
    async with attached(custody, binding) as epoch:
        for mid in range(backend.MAX_APPLICATION):
            await custody.input(
                {"jsonrpc": "2.0", "id": mid, "method": "tools/call",
                 "params": {"name": "echo", "arguments": {}}},
            )
        assert epoch._application == backend.MAX_APPLICATION
        with pytest.raises(RuntimeError, match="window is full"):
            await custody.input({"jsonrpc": "2.0", "id": 1000, "method": "tools/list"})
        drain = asyncio.create_task(custody.peer.request("quiesce", epoch=1))
        while epoch.state != "quiescing":
            await asyncio.sleep(0)
        with pytest.raises(RuntimeError, match="not admitting"):
            await custody.input({"jsonrpc": "2.0", "id": 1001, "method": "tools/list"})
        await custody.upstream({"jsonrpc": "2.0", "id": "server-1", "method": "sampling/createMessage"})
        await custody.upstream({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        await custody.input(
            {"jsonrpc": "2.0", "method": "notifications/cancelled",
             "params": {"requestId": 0}},
        )
        await custody.input({"jsonrpc": "2.0", "id": "server-1", "result": {}})
        assert not drain.done()
        for mid in range(backend.MAX_APPLICATION):
            await custody.upstream({"jsonrpc": "2.0", "id": mid, "result": {"content": []}})
        await asyncio.wait_for(drain, 2)
        assert not epoch.accepted
        assert any(message.get("method") == "notifications/cancelled" for message in custody.messages)
        assert {"jsonrpc": "2.0", "id": "server-1", "result": {}} in custody.messages
        assert sum("method" in item["message"] for item in custody.outputs) == 2


async def test_uncommitted_duplicate_and_foreign_epoch_input_have_no_effects():
    custody, binding = Custody(), cp.Binding.issue()
    message = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    async with attached(custody, binding, activate=False):
        with pytest.raises(RuntimeError, match="not admitting"):
            await custody.input(message)
        with pytest.raises(RuntimeError, match="epoch differs"):
            await custody.peer.request("activate", epoch=2)
        assert not custody.messages
        await custody.peer.request("activate", epoch=1)
        with pytest.raises(RuntimeError, match="twice"):
            await custody.peer.request("activate", epoch=1)
        await custody.input(message)
        with pytest.raises(RuntimeError, match="duplicate"):
            await custody.input(message, origin=custody.origin)
        await custody.peer.request("quiesce", epoch=1)
        assert custody.messages == [message]


async def test_activation_rechecks_physical_admission_before_any_send():
    custody, binding = Custody(), cp.Binding.issue()
    selected = True

    def qualify():
        if not selected:
            raise incoming.AdmissionRefused("physical selection changed")
        return configuration()

    async with attached(custody, binding, qualify=qualify, activate=False):
        selected = False
        with pytest.raises(RuntimeError, match="selection changed"):
            await custody.peer.request("activate", epoch=1)
        assert not custody.messages


async def test_context_drift_before_activation_is_refused(monkeypatch):
    custody, binding = Custody(), cp.Binding.issue()
    async with attached(custody, binding, activate=False):
        monkeypatch.setenv("CUSTODY_FIXTURE_CONTEXT", "different")
        with pytest.raises(RuntimeError, match="context changed"):
            await custody.peer.request("activate", epoch=1)
        assert not custody.messages


async def test_disconnect_does_not_close_or_replay_the_retained_mutation():
    custody, binding = Custody(), cp.Binding.issue()
    async with attached(custody, binding):
        await custody.input(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "echo", "arguments": {}}},
        )
        await custody.inflight.wait()
    assert len(custody.messages) == 1
    assert custody.settled == []
    assert custody.messages[0]["method"] == "tools/call"


async def test_cancelled_quiescence_receipt_does_not_cancel_accepted_work():
    custody, binding = Custody(), cp.Binding.issue()
    async with attached(custody, binding) as epoch:
        await custody.input(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "echo", "arguments": {}}},
        )
        await custody.inflight.wait()
        drain = asyncio.create_task(custody.peer.request("quiesce", epoch=1))
        while epoch.state != "quiescing":
            await asyncio.sleep(0)
        drain.cancel()
        with pytest.raises(asyncio.CancelledError):
            await drain
        assert not epoch.accepted[1].done()
        await custody.upstream({"jsonrpc": "2.0", "id": 1, "result": {"content": []}})
        await custody.peer.request("quiesce", epoch=1)
        assert custody.settled == [1]
        assert len(custody.messages) == 1
        assert not epoch.accepted


async def test_control_after_first_quiescence_invalidates_checkpoint_until_it_settles():
    custody, binding = Custody(), cp.Binding.issue()
    async with attached(custody, binding) as epoch:
        await custody.peer.request("quiesce", epoch=1)
        await custody.upstream({"jsonrpc": "2.0", "id": "server-1", "method": "sampling/createMessage"})
        entered, release = asyncio.Event(), asyncio.Event()
        original = custody.handle

        async def hold_reply(operation, data):
            if operation == "send":
                entered.set()
                await release.wait()
            return await original(operation, data)

        custody.peer.handler = hold_reply
        await custody.input({"jsonrpc": "2.0", "id": "server-1", "result": {}})
        await entered.wait()
        assert epoch.state == "quiescing"
        with pytest.raises(RuntimeError, match="has not quiesced"):
            await custody.peer.request("checkpoint", epoch=1)
        release.set()
        await custody.peer.request("quiesce", epoch=1)
        await custody.peer.request("checkpoint", epoch=1)


async def test_lost_output_ack_preserves_uncertainty_without_replaying_response_or_call():
    custody, binding = Custody(), cp.Binding.issue()
    entered, release = asyncio.Event(), asyncio.Event()
    original = custody.handle

    async def lose_ack(operation, data):
        result = await original(operation, data)
        if operation == "output":
            entered.set()
            await release.wait()
        return result

    custody.handle = lose_ack
    async with attached(custody, binding) as epoch:
        await custody.input({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        await entered.wait()
        assert list(epoch.accepted) == [1]
        assert custody.settled == []
    assert len(custody.outputs) == len(custody.messages) == 1
    assert custody.settled == []


async def test_failed_checkpoint_preparation_never_opens_a_transport():
    custody, binding = Custody(), cp.Binding.issue()
    snapshot = cp.envelope(configuration(), {"pipeline": [], "client": -1}, binding)
    with pytest.raises(RuntimeError, match="correlation"):
        async with attached(custody, binding, snapshot=snapshot):
            pass
    assert custody.opened == custody.messages == []


@pytest.mark.parametrize(
    "change",
    ["token", "physical", "selection", "root", "configuration-root", "context", "draining",
     "epoch", "contract"],
)
async def test_backend_admission_refuses_before_creating_an_execution_epoch(
    change, tmp_path, monkeypatch,
):
    cfg = configuration()
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "runtime"
    root.mkdir()
    (root / "current-version").write_text("physical-new", encoding="utf-8")
    request = {
        "token": "fixture-token", "bridge": "fixture", "epoch": 1,
        "contract": deepcopy(CONTRACT), "generation": "physical-new", "runtime_root": str(root),
        "configuration_context": capture_context("fixture"),
        "execution_context": {"contract": ec.descriptor()["contract"], "digest": ec._identity(cfg, ec._KEY)},
    }
    replies, executed, loaded = [], [], []

    async def send(writer, reply):
        replies.append(reply)

    server = SimpleNamespace(
        _token="fixture-token", _version="physical-new", _runtime_root=root,
        _draining=False, _closing=False, _attached=0, _send=send, _touch=lambda: None,
    )
    monkeypatch.setattr(incoming, "launch_generation", lambda: "physical-new")

    def loader(bridge, *, repository_root):
        loaded.append(repository_root)
        return cfg

    monkeypatch.setattr(backend, "load_config", loader)

    class Epoch:
        def __init__(self, *args):
            executed.append(True)

        async def run(self):
            pass

    monkeypatch.setattr(backend, "BackendSession", Epoch)
    if change == "token":
        request["token"] = "foreign"
    elif change == "physical":
        monkeypatch.setattr(incoming, "launch_generation", lambda: "physical-old")
    elif change == "selection":
        (root / "current-version").write_text("physical-other", encoding="utf-8")
    elif change == "root":
        request["runtime_root"] = str(tmp_path / "foreign")
    elif change == "configuration-root":
        request.pop("configuration_context")
    elif change == "context":
        request["execution_context"]["digest"] = "foreign"
    elif change == "draining":
        server._draining = True
    elif change == "epoch":
        request["epoch"] = True
    else:
        request["contract"]["capabilities"] = []
    await backend.run_backend(server, request, None, None)
    assert replies[0]["ok"] is False
    assert executed == []
    assert server._attached == 0
    if change in {"token", "physical", "selection", "root", "configuration-root", "draining", "epoch", "contract"}:
        assert not loaded


async def test_backend_admission_uses_incoming_physical_generation_and_caller_root(
    tmp_path, monkeypatch,
):
    cfg = configuration()
    monkeypatch.chdir(tmp_path)
    root = tmp_path / "incoming-runtime"
    root.mkdir()
    (root / "current-version").write_text("physical-new", encoding="utf-8")
    request = {
        "token": "fixture-token", "bridge": "fixture", "epoch": 2, "contract": CONTRACT,
        "generation": "physical-new", "runtime_root": str(root),
        "configuration_context": capture_context("fixture"),
        "execution_context": {"contract": ec.descriptor()["contract"], "digest": ec._identity(cfg, ec._KEY)},
        "keeper_version": "physical-old", "keeper_runtime_root": None,
    }
    loaded, replies = [], []

    def loader(bridge, *, repository_root):
        loaded.append((bridge, repository_root))
        return cfg

    async def send(writer, reply):
        replies.append(reply)

    class Epoch:
        def __init__(self, reader, writer, request, qualify):
            assert qualify() == cfg

        async def run(self):
            pass

    server = SimpleNamespace(
        _token="fixture-token", _version="physical-new", _runtime_root=root,
        _draining=False, _closing=False, _attached=0, _send=send, _touch=lambda: None,
    )
    monkeypatch.setattr(incoming, "launch_generation", lambda: "physical-new")
    monkeypatch.setattr(backend, "load_config", loader)
    monkeypatch.setattr(backend, "BackendSession", Epoch)
    await backend.run_backend(server, request, None, None)
    assert loaded == [("fixture", tmp_path)] * 2
    descriptor = backend.capability(server)["custody_backend"]
    assert supports(descriptor) and supports(CONTRACT)
    assert CHECKPOINT_TRACE_CAPABILITY in descriptor["capabilities"]
    assert CHECKPOINT_TRACE_CAPABILITY not in CONTRACT["capabilities"]
    assert replies == [
        {"ok": True, "contract": descriptor, "generation": "physical-new",
         "runtime_root": str(root), "epoch": 2},
    ]
    assert server._attached == 0
    (root / "current-version").write_text("replacement", encoding="utf-8")
    assert backend.capability(server) == {}
