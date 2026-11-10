"""Rebuild execution objects over a retained, context-bound upstream connection."""

import asyncio
import json

import pytest

from agent_mcp import checkpoint as cp
from agent_mcp.config import parse_config
from agent_mcp.pipeline import UpstreamClient
from agent_mcp.session import BridgeSession
from agent_mcp.transports.base import Transport


class Connection(Transport):
    def __init__(self):
        self._emit = None
        self.messages = []
        self.pending = asyncio.Event()
        self.release = asyncio.Event()
        self.starts = 0

    async def start(self):
        self.starts += 1

    async def send(self, message):
        self.messages.append(message)
        if message.get("method") == "tools/call":
            self.pending.set()
            await self.release.wait()
        if "id" in message and "method" in message:
            result = (
                {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
                if message["method"] == "tools/list"
                else {"content": []}
            )
            await self._emit_message({"jsonrpc": "2.0", "id": message["id"], "result": result})


def configuration():
    return parse_config(
        {
            "server": {"type": "stdio", "command": ["fixture"]},
            "decorators": [{"type": "rename", "prefix": "test_"}],
        }
    )


async def test_quiesce_joins_pipeline_then_rebuilds_objects_without_initialize_replay():
    cfg, binding, connection = configuration(), cp.Binding.issue(), Connection()
    outputs = []
    old = BridgeSession(cfg, outputs.append, transport=connection, checkpoint_binding=binding)
    await old.start()
    await old.submit({"jsonrpc": "2.0", "id": 0, "method": "initialize"})
    await old.submit({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    call = old.submit(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "test_echo", "arguments": {}},
        }
    )
    await connection.pending.wait()
    with pytest.raises(cp.CheckpointRefused, match="quiesced"):
        old.checkpoint()
    drain = asyncio.create_task(old.quiesce())
    await asyncio.sleep(0)
    assert not drain.done()
    connection.release.set()
    await call
    await drain
    snapshot = old.checkpoint()
    new = BridgeSession(
        cfg,
        outputs.append,
        transport=connection,
        checkpoint_binding=binding,
        resume=snapshot,
    )
    await new.start()
    assert new._pipeline is not old._pipeline
    assert new._pipeline.decorators[0] is not old._pipeline.decorators[0]
    await new.submit(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "test_echo", "arguments": {}},
        }
    )
    assert connection.messages[-1]["params"]["name"] == "echo"
    assert [message["id"] for message in outputs] == [0, 1, 2, 3]
    assert sum(message["method"] == "initialize" for message in connection.messages) == 1
    assert binding.key not in json.dumps(snapshot)
    await new.aclose()
    await old.aclose()


@pytest.mark.parametrize(
    "change", ["configuration", "version", "state", "session", "cwd", "environment"]
)
async def test_import_refuses_changed_context_before_starting_any_transport(
    change,
    tmp_path,
    monkeypatch,
):
    cfg, binding = configuration(), cp.Binding.issue()
    old = BridgeSession(
        cfg,
        lambda _: None,
        transport=Connection(),
        checkpoint_binding=binding,
    )
    await old.start()
    snapshot = old.checkpoint()
    if change == "configuration":
        cfg.timeout += 1
    elif change == "version":
        snapshot["version"] = True
    elif change == "state":
        snapshot["state"]["client"] = -1
    elif change == "session":
        binding = cp.Binding.issue()
    elif change == "cwd":
        monkeypatch.chdir(tmp_path)
    else:
        monkeypatch.setenv("CUSTODY_FIXTURE_CONTEXT", "different")
    connection = Connection()
    new = BridgeSession(
        cfg,
        lambda _: None,
        transport=connection,
        checkpoint_binding=binding,
        resume=snapshot,
    )
    with pytest.raises(cp.CheckpointRefused):
        await new.start()
    assert connection.starts == 0
    await new.aclose()
    await old.aclose()


async def test_context_is_bound_at_initial_admission_not_recomputed_for_export(monkeypatch):
    cfg, binding = configuration(), cp.Binding.issue()
    old = BridgeSession(
        cfg,
        lambda _: None,
        transport=Connection(),
        checkpoint_binding=binding,
    )
    await old.start()
    monkeypatch.setenv("CUSTODY_FIXTURE_CONTEXT", "changed-after-upstream-spawn")
    with pytest.raises(cp.CheckpointRefused, match="changed after"):
        old.checkpoint()
    await old.aclose()


async def test_resume_cannot_create_a_replacement_upstream():
    cfg, binding = configuration(), cp.Binding.issue()
    snapshot = cp.envelope(cfg, {"pipeline": [], "client": 0}, binding)
    new = BridgeSession(cfg, lambda _: None, checkpoint_binding=binding, resume=snapshot)
    with pytest.raises(cp.CheckpointRefused, match="custody-owned transport"):
        await new.start()
    await new.aclose()


def test_unsettled_correlations_and_opaque_data_cannot_cross_the_boundary():
    client = UpstreamClient(Connection())
    client._pending["mutation"] = object()
    with pytest.raises(cp.CheckpointRefused, match="correlations"):
        cp.client_state(client)
    with pytest.raises(cp.CheckpointRefused, match="JSON"):
        cp.data_copy({"opaque": object()})


async def test_a_rejected_late_decorator_never_partially_restores_earlier_state():
    cfg, binding = configuration(), cp.Binding.issue()
    cfg.decorators *= 2
    session = BridgeSession(
        cfg,
        lambda _: None,
        transport=Connection(),
        checkpoint_binding=binding,
    )
    await session.start()
    snapshot = cp.pipeline_state(session._pipeline)
    snapshot[0]["data"]["reverse"] = {"must_not_be_installed": "echo"}
    snapshot[1]["data"]["reverse"] = {"invalid": False}
    with pytest.raises(cp.CheckpointRefused, match="rename"):
        cp.restore_pipeline(session._pipeline, snapshot)
    assert session._pipeline.decorators[0]._reverse == {}
    await session.aclose()


async def test_cancelled_quiescence_wait_does_not_cancel_accepted_mutation():
    cfg, binding, connection = configuration(), cp.Binding.issue(), Connection()
    session = BridgeSession(
        cfg,
        lambda _: None,
        transport=connection,
        checkpoint_binding=binding,
    )
    await session.start()
    call = session.submit(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {}},
        }
    )
    await connection.pending.wait()
    drain = asyncio.create_task(session.quiesce())
    await asyncio.sleep(0)
    drain.cancel()
    with pytest.raises(asyncio.CancelledError):
        await drain
    assert not call.done()
    connection.release.set()
    await call
    await session.quiesce()
    assert not call.cancelled()
    await session.aclose()


def test_export_budgets_complete_receipt_and_prepare_frames(monkeypatch):
    from agent_mcp import custody_wire
    from agent_mcp.custody_contract import CONTRACT, MAX_SEQUENCE

    cfg, binding = configuration(), cp.Binding.issue()

    def frames(snapshot):
        return (
            {"receipt": MAX_SEQUENCE, "result": snapshot},
            {
                "sequence": MAX_SEQUENCE, "operation": "prepare",
                "data": {
                    "epoch": MAX_SEQUENCE, "contract": CONTRACT,
                    "binding": {"session": binding.session, "key": binding.key},
                    "checkpoint": snapshot,
                },
            },
        )

    empty = cp.envelope(cfg, {"padding": ""}, binding)
    receipt, prepare = frames(empty)
    receipt_size, prepare_size = (
        len(custody_wire.Peer._encode(packet)) for packet in (receipt, prepare)
    )
    assert prepare_size > receipt_size
    monkeypatch.setattr(custody_wire, "MAX_FRAME", prepare_size + 8)
    snapshot = cp.envelope(cfg, {"padding": "x" * 8}, binding)
    assert max(len(custody_wire.Peer._encode(packet)) for packet in frames(snapshot)) == prepare_size + 8
    with pytest.raises(cp.CheckpointRefused, match="complete attachment frame"):
        cp.envelope(cfg, {"padding": "x" * 9}, binding)
    monkeypatch.setattr(custody_wire, "MAX_FRAME", receipt_size)
    assert len(custody_wire.Peer._encode(receipt)) == receipt_size
    with pytest.raises(cp.CheckpointRefused, match="complete attachment frame"):
        cp.envelope(cfg, {"padding": ""}, binding)
