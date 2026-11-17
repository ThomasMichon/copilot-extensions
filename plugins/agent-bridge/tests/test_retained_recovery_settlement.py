"""Original-operation evidence can settle initialization, never unknown native load."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_bridge import acquisition_authority
from agent_bridge import retained_metadata_recovery as metadata
from agent_bridge import retained_recovery_settlement as settlement
from agent_bridge import retained_session_recovery as recovery
from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.acquisition_executor import executor
from agent_bridge.acp_client import AcpClient
from agent_bridge.session_host.host_index import HostIndex, HostRecord
from agent_bridge.session_host_connection import _SessionHostConnectionMixin
from test_creation_worktree_writer import writer_creation  # noqa: F401
from test_retained_metadata_recovery import missing_record
from test_workspace_creation import creation_rig, rig as _rig  # noqa: F401

rig = _rig
pytestmark = pytest.mark.contract("agent_bridge.retained_native_settlement")


async def failed_epoch(
    fixture: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *,
    marker: bool = False, fault: str | None = None,
) -> SimpleNamespace:
    manager = fixture.rig.manager
    old, request = await missing_record(fixture, monkeypatch)
    if fault == "prior-operation":
        prior = request.model_copy(update={"operation_id": "predecessor-operation"})
        manager.db.execute_write(
            "UPDATE workspace_embodiments SET creation_recoveries_json=? WHERE key_digest=?",
            (json.dumps({prior.operation_id: {"request": prior.model_dump(), "state": "complete"}}),
             request.journal_key),
        )
    session, boundary = await metadata.prepare(
        manager, old.session_id, request, fixture.requirement, lambda: True,
    )
    if fault == "foreign-epoch":
        request = request.model_copy(update={"operation_id": "different-operation"})
    operations = recovery._operations(dict(manager.db.execute_read(
        "SELECT * FROM workspace_embodiments",
    )[0]))
    operations[request.operation_id] = {"request": request.model_dump(), "state": "unknown"}
    manager.db.execute_write(
        "UPDATE workspace_embodiments SET creation_recoveries_json=? WHERE key_digest=?",
        (json.dumps(operations), request.journal_key),
    )
    manager.db.execute_write(
        "INSERT INTO acquisition_launch_gates VALUES (?,?,'cli',?,?,'retained-recovery')",
        (session.target.worktree_id, f"retained-recovery:{request.operation_id}",
         session.session_id, request.native_session_id),
    )
    if marker:
        session.event_log.append(settlement.START_EVENT, {
            "version": 1, "request": request.model_dump(),
        })
    for event_type, data in [
        ("startup_progress", {"phase": "resume", "state": "started"}),
        ("startup_progress", {"phase": "acp-initialize", "state": "started"}),
        ("host_child_exit", {"exit_code": 3}),
        ("startup_progress", {"phase": "acp-initialize", "state": "failed"}),
        ("connect_checkpoint", {"stage_name": "LAUNCH_ACP", "status": "failed", "retryable": False}),
        ("startup_progress", {"phase": "resume", "state": "failed"}),
    ]:
        session.event_log.append(event_type, data)
    index = HostIndex(tmp_path / "hosts" / "index.json")
    host = HostRecord(
        session_id=session.session_id, port=12345, host_pid=90000001, child_pid=90000002,
        extra={"host_process_start": "original-host", "child_process_start": "original-child"},
    )
    index.register(host)
    manager._host_index = index
    monkeypatch.setattr("zdd.process_exit.observe_original_exit", lambda _pid, _birth: True)
    command = fixture.command

    async def owner(args: Any, project: Any, **kwargs: Any) -> Any:
        if args[0] == "head-session":
            return {
                "tracked": True, "state": "active", "head_session": request.native_session_id,
                "head_revision": request.head_revision, "pending_handoffs": [],
            }
        return await command(args, project, **kwargs)

    monkeypatch.setattr(acquisition_authority, "worktree_command", owner)
    return SimpleNamespace(
        manager=manager, session=session, request=request, boundary=boundary,
        index=index, host=host, owner=owner,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", [False, True])
async def test_exact_status_settles_original_failure_without_native_or_history_effects(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, marker: bool,  # noqa: F811
) -> None:
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient
    from agent_bridge.routes.session_resume import router

    bound = await failed_epoch(writer_creation, monkeypatch, tmp_path, marker=marker)
    manager, session, request = bound.manager, bound.session, bound.request
    original = dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    events = manager.db.get_events(session.session_id)
    host_bytes = bound.index._path.read_bytes()
    manager.db.enqueue_prompt(session.session_id, "Retained queued work", now=100)
    prompts = manager.db.list_pending_prompts(session.session_id)
    launch = AsyncMock(side_effect=AssertionError("inspection must not start native work"))
    monkeypatch.setattr(manager, "resume_session", launch)
    app = FastAPI()
    app.include_router(router, prefix="/sessions")
    app.state.session_manager, app.state.ready = manager, True
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://fixture") as client:
        url = f"/sessions/{session.session_id}/retained-recovery/{request.operation_id}"
        response = await client.get(url)
        assert response.status_code == 200, response.text
        outcome = response.json()
        assert outcome["state"] == "not-started"
        assert (await client.get(url)).json() == outcome
    assert outcome["native_session_id"] == request.native_session_id
    saved = dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    assert {key: value for key, value in saved.items() if key != "creation_recoveries_json"} == {
        key: value for key, value in original.items() if key != "creation_recoveries_json"
    }
    assert recovery._operations(saved)[request.operation_id] == {
        "request": request.model_dump(), "state": "not-started",
    }
    assert not manager.db.execute_read("SELECT * FROM acquisition_launch_gates")
    assert manager.db.get_events(session.session_id) == events
    assert manager.db.list_pending_prompts(session.session_id) == prompts
    assert bound.index._path.read_bytes() == host_bytes
    assert not manager.db.execute_read("SELECT * FROM turns")
    launch.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "native-load", "missing-event", "incomplete", "second-resume", "foreign-epoch",
    "prior-operation", "live-host", "unknown-child", "missing-host", "foreign-host",
    "live-transport", "stop-token", "head-revision", "nonce", "not-ready", "executor",
    "changed-history", "foreign-gate",
])
async def test_unconfirmed_or_live_custody_stays_held(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fault: str,  # noqa: F811
) -> None:
    bound = await failed_epoch(writer_creation, monkeypatch, tmp_path, fault=fault)
    manager, session, request = bound.manager, bound.session, bound.request
    if fault in {"native-load", "second-resume"}:
        session.event_log.append("startup_progress", {
            "phase": "native-session-load" if fault == "native-load" else "resume",
            "state": "started",
        })
    elif fault == "missing-event":
        manager.db.flush()
        manager.db.execute_write(
            "DELETE FROM events WHERE session_id=? AND event_id=3", (session.session_id,),
        )
    elif fault == "incomplete":
        manager.db.flush()
        manager.db.execute_write(
            "DELETE FROM events WHERE session_id=? AND event_id=6", (session.session_id,),
        )
    elif fault in {"live-host", "unknown-child"}:
        def exited(pid: int, _birth: str) -> bool:
            return bool(pid != (bound.host.host_pid if fault == "live-host" else bound.host.child_pid))
        monkeypatch.setattr("zdd.process_exit.observe_original_exit", exited)
    elif fault == "missing-host":
        bound.index.remove(session.session_id)
    elif fault == "foreign-host":
        bound.host.boundary = "ssh"
        bound.index.register(bound.host)
    elif fault == "live-transport":
        session.client = SimpleNamespace(is_running=True)
    elif fault == "stop-token":
        manager.db.execute_write(
            "UPDATE sessions SET cold_recovery_stop_token='explicit-stop' WHERE id=?",
            (session.session_id,),
        )
    elif fault in {"head-revision", "changed-history"}:
        async def changed(args: Any, project: Any, **kwargs: Any) -> Any:
            result = await bound.owner(args, project, **kwargs)
            if args[0] == "head-session":
                if fault == "head-revision":
                    result["head_revision"] += 1
                else:
                    session.event_log.append("permission_request", {"request_id": "pending"})
            return result
        monkeypatch.setattr(acquisition_authority, "worktree_command", changed)
    elif fault == "nonce":
        writer_creation.rig.row["creation_nonce"] = "different-incarnation"
    elif fault == "foreign-gate":
        manager.db.execute_write(
            "UPDATE acquisition_launch_gates SET delivery_id='another-operation' WHERE worktree_id=?",
            (session.target.worktree_id,),
        )
    before = dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    gates = manager.db.execute_read("SELECT * FROM acquisition_launch_gates")
    try:
        if fault == "executor":
            with executor(manager.db, f"retained-recovery:{session.target.worktree_id}"):
                with pytest.raises(AcquisitionRefused, match="another acquisition"):
                    await settlement.inspect(manager, session.session_id, request.operation_id, lambda: True)
        else:
            try:
                result = await settlement.inspect(
                    manager, session.session_id, request.operation_id, lambda: fault != "not-ready",
                )
                assert result["state"] == "unknown"
            except AcquisitionRefused:
                pass
        assert dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0]) == before
        assert manager.db.execute_read("SELECT * FROM acquisition_launch_gates") == gates
        assert not manager.db.execute_read("SELECT * FROM turns")
        manager._connect_via_session_host.assert_not_awaited()
    finally:
        session.client = None


@pytest.mark.asyncio
@pytest.mark.parametrize("restart", [False, True])
async def test_real_connector_failure_settles_before_and_after_startup_pruning(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, restart: bool,  # noqa: F811
) -> None:
    fixture, manager = writer_creation, writer_creation.rig.manager
    old, request = await missing_record(fixture, monkeypatch)
    session, _boundary = await metadata.prepare(
        manager, old.session_id, request, fixture.requirement, lambda: True,
    )
    index = HostIndex(tmp_path / "hosts" / "index.json")
    with index._locked_reload():
        pass
    manager._host_index = index
    monkeypatch.setattr(manager, "_connect_via_session_host",
                        _SessionHostConnectionMixin._connect_via_session_host.__get__(manager))
    monkeypatch.setattr("agent_bridge.transport.resolve_local_launch", AsyncMock(
        return_value=(["copilot", "--acp", "--stdio"], session.target.cwd, session.target.env),
    ))
    monkeypatch.setattr("agent_bridge.local_static_preparation.prepare_for_launch", AsyncMock())
    spawned = SimpleNamespace(
        port=12345, host_pid=90000001, child_pid=90000002, protocol_version=1,
        state_file="fixture-state", nonce="fixture-nonce", boundary="local", endpoint={},
        proc=SimpleNamespace(pid=90000001, poll=lambda: 0),
    )
    monkeypatch.setattr("zdd.diagnostics.process_start_time",
                        lambda pid: "original-host" if pid == spawned.host_pid else "original-child")

    def launch(_args: Any, *, acquisition_launch: Any, **_kwargs: Any) -> Any:
        acquisition_launch.begin_host()
        acquisition_launch.retain_process(spawned.proc, spawned.state_file, spawned.nonce)
        return spawned

    monkeypatch.setattr("agent_bridge.session_host.launcher._core.launch_session_host", launch)
    sock = SimpleNamespace(
        attach=AsyncMock(return_value=SimpleNamespace(preference_receipt=None)),
        close=AsyncMock(), terminate=AsyncMock(),
    )
    streams = SimpleNamespace(
        reader=object(), writer=object(), child_exit_code=None, aclose=AsyncMock(),
    )
    monkeypatch.setattr("agent_bridge.session_host.client.SessionHostClient.connect",
                        AsyncMock(return_value=sock))
    monkeypatch.setattr("agent_bridge.session_host.acp_adapter.open_acp_streams",
                        AsyncMock(return_value=streams))
    clients: list[Any] = []

    def client_factory(**kwargs: Any) -> Any:
        client: Any = AcpClient(**kwargs)
        client._host_mode = True

        async def initialize(*_args: Any, **_kwargs: Any) -> None:
            client.mark_host_child_exited(3)
            raise ConnectionError("controlled initialization failure before native load")

        client.start_streams = AsyncMock(side_effect=initialize)
        client.load_session = AsyncMock(side_effect=AssertionError("native load must not run"))
        clients.append(client)
        return client

    monkeypatch.setattr("agent_bridge.session_manager.AcpClient", client_factory)
    with pytest.raises(Exception, match="controlled initialization failure"):
        await recovery.resume(manager, session.session_id, request, fixture.requirement, lambda: True)
    assert session.client is clients[0] and not clients[0].is_running
    host = index.get(session.session_id)
    assert host is not None
    clients[0].load_session.assert_not_awaited()
    before_events = manager.db.get_events(session.session_id)
    original = dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    assert settlement._failure_proven(original, session.session_id, request, before_events)
    monkeypatch.setattr("zdd.process_exit.observe_original_exit", lambda _pid, _birth: True)
    monkeypatch.setattr(manager, "_rec_host_alive", lambda _host: False)
    if restart:
        from agent_bridge.session_manager import SessionManager

        manager = SessionManager(manager.db, read_only_rehydrate=True)
        manager._host_index = index
        monkeypatch.setattr(manager, "_rec_host_alive", lambda _host: False)
        session = manager.get_session(session.session_id)
        assert session is not None
        assert session.client is None
    manager._prune_dead_hosts()
    assert index.get(session.session_id) == host
    command = fixture.command

    async def owner(args: Any, project: Any, **kwargs: Any) -> Any:
        if args[0] == "head-session":
            return {
                "tracked": True, "state": "active", "head_session": request.native_session_id,
                "head_revision": request.head_revision, "pending_handoffs": [],
            }
        return await command(args, project, **kwargs)

    monkeypatch.setattr(acquisition_authority, "worktree_command", owner)
    assert (await settlement.inspect(
        manager, session.session_id, request.operation_id, lambda: True,
    ))["state"] == "not-started"
    assert session.client is None
    assert manager.db.get_events(session.session_id) == before_events
    assert index.get(session.session_id) == host
    if not restart:
        streams.aclose.assert_awaited_once()
        sock.close.assert_awaited_once()
    sock.terminate.assert_not_awaited()
    clients[0].load_session.assert_not_awaited()
