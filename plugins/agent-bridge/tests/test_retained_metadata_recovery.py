"""Metadata adoption retains the native series without reopening lost history."""

from __future__ import annotations

from dataclasses import replace
import json
import sqlite3
from typing import Any
from unittest.mock import AsyncMock

import pytest

from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge import retained_metadata_recovery as metadata
from agent_bridge import creation_worktree_writer as creation
from agent_bridge.session_status import SessionStatus
from test_creation_worktree_writer import recovery_fixture, writer_creation  # noqa: F401
from test_workspace_creation import creation_rig, rig as _rig  # noqa: F401

rig = _rig
pytestmark = pytest.mark.contract("agent_bridge.retained_metadata_recovery")


async def missing_record(fixture: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    session, request, _command = await recovery_fixture(fixture, monkeypatch)
    fixture.rig.manager._sessions.pop(session.session_id)
    fixture.rig.db.delete_session(session.session_id)
    fixture.rig.manager._connect_via_session_host = AsyncMock(
        side_effect=AssertionError("metadata preparation cannot start a native process"),
    )
    command = _command

    async def exact_owner(args: Any, project: Any, **kwargs: Any) -> Any:
        result = await command(args, project, **kwargs)
        if args[0] == "head-session":
            result = {**result, "head_revision": request.head_revision, "pending_handoffs": []}
        return result

    monkeypatch.setattr("agent_bridge.acquisition_authority.worktree_command", exact_owner)
    monkeypatch.setattr("agent_bridge.workspace_creation.worktree_command", exact_owner)
    from agent_bridge import creation_writer_inspection

    monkeypatch.setattr(
        "agent_bridge.acquisition_writer_inspection._instance", getattr(creation_writer_inspection, "_instance"),
    )
    manager = fixture.rig.manager
    monkeypatch.setattr(manager._host_index, "get", lambda _id: None, raising=False)
    return session, request


@pytest.mark.asyncio
async def test_missing_metadata_adopts_distinct_control_epoch_once(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    fixture, manager = writer_creation, writer_creation.rig.manager
    old, request = await missing_record(fixture, monkeypatch)
    before = dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    adopted, outcome = await metadata.prepare(
        manager, old.session_id, request, fixture.requirement, lambda: True,
    )
    assert adopted.session_id != old.session_id
    assert adopted.acp_session_id == old.acp_session_id
    assert adopted.status == SessionStatus.STOPPED
    assert not adopted.background_recovery_enabled and adopted.client is None
    assert adopted.target.venue[creation.JOURNAL_KEY] == request.journal_key
    assert outcome["history"] == {
        "status": "unavailable", "predecessor_id": old.session_id,
        "event_epoch": adopted.session_id, "turn_epoch": adopted.session_id,
    }
    replay, replay_outcome = await metadata.prepare(
        manager, old.session_id, request, fixture.requirement, lambda: True,
    )
    assert replay is adopted and replay_outcome == outcome
    assert manager.get_session(old.session_id) is None
    assert manager.get_session(old.acp_session_id) is None
    assert manager.db.get_session(old.session_id) is None
    after = dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    assert {key: value for key, value in after.items() if key != metadata.COLUMN} == {
        key: value for key, value in before.items() if key != metadata.COLUMN
    }
    assert not manager.db.execute_read("SELECT * FROM turns")
    assert not manager.db.execute_read("SELECT * FROM acquisition_launch_gates")
    manager._connect_via_session_host.assert_not_awaited()
    with pytest.raises(sqlite3.IntegrityError, match="metadata"):
        manager.db.create_session(
            old.session_id, "delayed-old-record", None, old.target.cwd,
            "local", "stopped", 1,
        )
    with pytest.raises(sqlite3.IntegrityError, match="metadata"):
        manager.db.execute_write(
            "UPDATE workspace_embodiments SET creation_adoption_json=NULL WHERE key_digest=?",
            (request.journal_key,),
        )
    with pytest.raises(AcquisitionRefused, match="owner-qualified"):
        await manager.resume_session(adopted.session_id, drain=False)
    with pytest.raises(ValueError, match="predecessor event stream"):
        await manager.resync_session(adopted.session_id)
    with pytest.raises(sqlite3.IntegrityError, match="metadata"):
        manager.db.update_session_target(adopted.session_id, old.target.to_json())
    from agent_bridge.runtime_custody import RuntimeCustodyError

    with pytest.raises(RuntimeCustodyError):
        await manager.end_session(adopted.session_id, force=True)
    assert manager.get_session(adopted.session_id) is adopted
    target = adopted.target
    try:
        adopted.target = replace(target, venue={
            key: value for key, value in target.venue.items() if key != creation.JOURNAL_KEY
        })
        with pytest.raises(AcquisitionRefused, match="lost its keyed creation pointer"):
            await manager.resume_session(adopted.session_id, drain=False)
    finally:
        adopted.target = target


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["present", "unknown-hosts", "live-host", "pending", "head", "draining"])
async def test_ambiguous_metadata_is_held_without_new_control_record(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch, fault: str,  # noqa: F811
) -> None:
    fixture, manager = writer_creation, writer_creation.rig.manager
    old, request = await missing_record(fixture, monkeypatch)
    if fault == "present":
        manager._sessions[old.session_id] = old
    elif fault == "unknown-hosts":
        manager._host_index.locked_inventory = lambda: None
    elif fault == "live-host":
        from types import SimpleNamespace

        manager._host_index.locked_inventory = lambda: SimpleNamespace(
            storage_complete=True, records=[SimpleNamespace(session_id=old.session_id)],
        )
    elif fault == "pending":
        manager.db.execute_write(
            "UPDATE workspace_embodiments SET creation_recoveries_json=? WHERE key_digest=?",
            (json.dumps({request.operation_id: {"request": request.model_dump(), "state": "unknown"}}),
             request.journal_key),
        )
    elif fault == "head":
        original = fixture.command

        async def changed(args: Any, project: Any, **kwargs: Any) -> Any:
            if args[0] == "head-session":
                return {"tracked": True, "state": "active", "occupied": True, "head_session": "other"}
            return await original(args, project, **kwargs)

        monkeypatch.setattr("agent_bridge.acquisition_authority.worktree_command", changed)
        monkeypatch.setattr("agent_bridge.workspace_creation.worktree_command", changed)
    else:
        manager._draining = True
    before = dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    with pytest.raises(AcquisitionRefused):
        await metadata.prepare(manager, old.session_id, request, fixture.requirement, lambda: True)
    assert not manager.db.list_sessions()
    assert dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0]) == before
    manager._connect_via_session_host.assert_not_awaited()


@pytest.mark.asyncio
async def test_atomic_import_rollback_never_leaves_an_empty_or_duplicate_epoch(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    fixture, manager = writer_creation, writer_creation.rig.manager
    old, request = await missing_record(fixture, monkeypatch)
    original = manager.import_native_session

    def interrupted(*args: Any, **kwargs: Any) -> Any:
        owner = kwargs["_metadata"]

        def fail(conn: sqlite3.Connection) -> None:
            owner.commit(conn)
            raise RuntimeError("interrupted atomic commit")

        return original(*args, **{**kwargs, "_metadata": replace(owner, commit=fail)})

    monkeypatch.setattr(manager, "import_native_session", interrupted)
    with pytest.raises(RuntimeError, match="interrupted atomic"):
        await metadata.prepare(manager, old.session_id, request, fixture.requirement, lambda: True)
    assert not manager.db.list_sessions() and not manager.list_sessions()
    assert manager.db.execute_read("SELECT creation_adoption_json FROM workspace_embodiments")[0][0] is None
    monkeypatch.setattr(manager, "import_native_session", original)
    adopted, boundary = await metadata.prepare(
        manager, old.session_id, request, fixture.requirement, lambda: True,
    )
    from agent_bridge.session_manager import SessionManager

    restarted = SessionManager(manager.db, read_only_rehydrate=True)
    recovered, observed = await metadata.prepare(
        restarted, old.session_id, request, fixture.requirement, lambda: True,
    )
    assert recovered.session_id == adopted.session_id and observed == boundary
    assert recovered.acp_session_id == old.acp_session_id
    assert recovered.target.venue[metadata.EPOCH_KEY] == adopted.session_id
    assert len(manager.db.list_sessions()) == 1


@pytest.mark.asyncio
async def test_adopted_record_uses_existing_retained_native_recovery_without_replaying_old_operations(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from agent_bridge import retained_session_recovery as recovery
    from agent_bridge import creation_writer_inspection

    fixture, manager = writer_creation, writer_creation.rig.manager
    connect = manager._connect_via_session_host
    old, request = await missing_record(fixture, monkeypatch)
    prior = request.model_copy(update={"operation_id": "predecessor-recovery"})
    manager.db.execute_write(
        "UPDATE workspace_embodiments SET creation_recoveries_json=? WHERE key_digest=?",
        (json.dumps({prior.operation_id: {"request": prior.model_dump(), "state": "complete"}}),
         request.journal_key),
    )
    adopted, boundary = await metadata.prepare(
        manager, old.session_id, request, fixture.requirement, lambda: True,
    )
    assert boundary["prior_operations"] == [prior.operation_id]
    with pytest.raises(AcquisitionRefused, match="predecessor epoch"):
        await recovery.resume(manager, adopted.session_id, prior, fixture.requirement, lambda: True)
    with pytest.raises(AcquisitionRefused, match="owner-qualified"):
        await manager.resume_session(adopted.session_id, drain=False)
    manager._connect_via_session_host.assert_not_awaited()
    monkeypatch.setattr(manager, "_connect_via_session_host", connect)
    result, outcome = await recovery.resume(
        manager, adopted.session_id, request, fixture.requirement, lambda: True,
    )
    assert result is adopted and result.acp_session_id == old.acp_session_id
    assert outcome["state"] == "complete"
    assert manager.db.get_session(old.session_id) is None
    observed = await creation_writer_inspection.inspect_writer(manager, adopted, lambda: True)
    assert observed["key_digest"] == request.journal_key
    assert observed["native_session_id"] == old.acp_session_id
    assert recovery.permission_held(manager.db, adopted.target)
    assert not manager.db.retained_recovery_held(adopted.session_id)
    await manager.end_session(adopted.session_id, force=True)
    assert manager.db.get_session(adopted.session_id) is None
    with pytest.raises(AcquisitionRefused, match="epoch remains excluded"):
        await metadata.prepare(manager, old.session_id, request, fixture.requirement, lambda: True)
    assert not manager.db.list_sessions()


@pytest.mark.asyncio
async def test_original_launch_config_and_assignment_are_retained_not_rebound(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from agent_bridge import workspace_creation
    from agent_bridge.dispatch_origin import inspect

    fixture, manager = writer_creation, writer_creation.rig.manager
    original = workspace_creation.start
    origin = {
        "task_id": "original-task", "reservation_key": "original-reservation",
        "cohort": {"schema": "dispatch-assignment-cohort", "version": 1, "emitter": None, "pool": None},
    }
    tools = [{"name": "fixture", "command": "fixture-tool", "args": [], "env": {}}]

    async def configured(*args: Any, **kwargs: Any) -> Any:
        return await original(
            *args, **kwargs, agent_name="bounded-worker", model="selected-model", effort="high",
            mcp_servers=tools, copilot_args=["--fixture-argument"], dispatch_origin=origin,
        )

    monkeypatch.setattr(workspace_creation, "start", configured)
    old, request = await missing_record(fixture, monkeypatch)
    adopted, boundary = await metadata.prepare(
        manager, old.session_id, request, fixture.requirement, lambda: True,
    )
    saved = manager.db.get_session(adopted.session_id)
    assert inspect(saved) == origin
    assert adopted.agent_name == old.agent_name == "bounded-worker"
    assert adopted.mcp_servers == old.mcp_servers == tools
    assert adopted.model_override == old.model_override == "selected-model"
    assert adopted.effort_override == old.effort_override == "high"
    assert adopted.target.copilot_args == old.target.copilot_args
    assert json.loads(saved["config_json"])[metadata.CONFIG_KEY] == boundary
    assert not manager.db.execute_read("SELECT * FROM acquisition_attempts")


@pytest.mark.asyncio
async def test_actual_http_negotiation_and_lost_response_keep_old_send_and_cursor_namespaces_closed(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_bridge import client_retained_metadata
    from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
    from agent_bridge.routes import health, sessions

    fixture, manager = writer_creation, writer_creation.rig.manager
    old, request = await missing_record(fixture, monkeypatch)
    app = FastAPI()
    app.include_router(sessions.router)
    app.include_router(health.router)
    app.state.session_manager, app.state.ready = manager, True
    monkeypatch.setattr(health, "observe_worktree_writer", lambda: fixture.requirement.identity.model_dump())
    monkeypatch.setattr(health, "slot_descriptor", lambda *_a, **_kw: {})
    monkeypatch.setattr(health, "get_default_manager", lambda: SimpleNamespace(carrier_diagnostics=lambda: {}))
    with TestClient(app) as http:
        client = BridgeClient("http://fixture", token="fixture", connect_grace=0)
        calls: list[tuple[str, str]] = []
        state = {"support": False, "lost": False}

        def send(method: str, path: str, body: Any = None, **_kwargs: Any) -> Any:
            calls.append((method, path))
            response = http.request(method, path, json=body)
            if response.status_code >= 400:
                raise BridgeClientError(response.status_code, response.json().get("detail"))
            result = response.json()
            if path == "/health" and not state["support"]:
                result["capabilities"].pop(metadata.CAPABILITY)
            if method == "POST" and not state["lost"]:
                state["lost"] = True
                raise BridgeConnectionError("accepted metadata response was lost")
            return result

        monkeypatch.setattr(client, "_request", send)
        with pytest.raises(BridgeClientError) as unsupported:
            client_retained_metadata.prepare(
                client, old.session_id, request.model_dump(), fixture.requirement.model_dump(),
            )
        assert unsupported.value.status == 426
        assert calls == [("GET", "/health")] and not manager.db.list_sessions()
        state["support"] = True
        with pytest.raises(BridgeConnectionError, match="response was lost"):
            client_retained_metadata.prepare(
                client, old.session_id, request.model_dump(), fixture.requirement.model_dump(),
            )
        assert len([item for item in calls if item[0] == "POST"]) == 1
        observed = client_retained_metadata.inspect(client, old.session_id, request.journal_key)
        replayed = client_retained_metadata.prepare(
            client, old.session_id, request.model_dump(), fixture.requirement.model_dump(),
        )
        assert replayed == observed and len(manager.db.list_sessions()) == 1
        for field, value in (
            ("version", True), ("session_id", old.session_id),
            ("predecessor_id", "unrelated"), ("history", {"status": "complete"}),
            ("request", {**request.model_dump(), "journal_key": "a" * 64}),
        ):
            def corrupt(method: str, path: str, body: Any = None, **kwargs: Any) -> Any:
                result = send(method, path, body, **kwargs)
                return result if path == "/health" else {**result, field: value}

            monkeypatch.setattr(client, "_request", corrupt)
            for operation in ("inspect", "prepare"):
                with pytest.raises(BridgeClientError) as malformed:
                    if operation == "inspect":
                        client_retained_metadata.inspect(client, old.session_id, request.journal_key)
                    else:
                        client_retained_metadata.prepare(
                            client, old.session_id, request.model_dump(), fixture.requirement.model_dump(),
                        )
                assert malformed.value.status == 502
        monkeypatch.setattr(client, "_request", send)
        assert client_retained_metadata.inspect(client, old.session_id, request.journal_key) == observed
        assert len(manager.db.list_sessions()) == 1
        for excluded in (old.session_id, old.acp_session_id):
            assert http.get(f"/api/v1/sessions/{excluded}/events").status_code == 404
            assert http.post(
                f"/api/v1/sessions/{excluded}/turns",
                json={"prompt": "delayed predecessor turn"},
            ).status_code == 404
            for operation_path in ("send", "send/receipt"):
                assert http.post(
                    f"/api/v1/sessions/{excluded}/turns/native-bound/{operation_path}",
                    json={
                        "invocation_key": "old-epoch-invocation",
                        "expected_native_session_id": old.acp_session_id,
                        "prompt": "delayed predecessor turn", "no_resume": True,
                    },
                ).status_code == 404
            assert http.post(
                f"/api/v1/sessions/{excluded}/resume",
                json={"worktree_writer": fixture.requirement.model_dump()},
            ).status_code in {404, 409}
        assert not manager.db.execute_read("SELECT * FROM turns")
        manager._connect_via_session_host.assert_not_awaited()


@pytest.mark.asyncio
async def test_prior_passive_receiver_refuses_adopted_schema_before_writable_cutover(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from agent_bridge import db_core
    from agent_bridge.db import Database

    fixture, manager = writer_creation, writer_creation.rig.manager
    old, request = await missing_record(fixture, monkeypatch)
    _session, boundary = await metadata.prepare(
        manager, old.session_id, request, fixture.requirement, lambda: True,
    )
    before = dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    # The supported cutover owner opens passively and checks this before any migration.
    monkeypatch.setattr(db_core, "SCHEMA_VERSION", 48)
    with pytest.raises(ValueError, match="current schema"):
        Database(manager.db.db_path, read_only=True)
    assert dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0]) == before
    assert json.loads(before[metadata.COLUMN]) == boundary
    assert manager.db.execute_read("SELECT version FROM schema_version")[0][0] == 49
