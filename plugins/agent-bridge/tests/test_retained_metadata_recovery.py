"""Metadata adoption retains the native series without reopening lost history."""

from __future__ import annotations

from dataclasses import replace
import asyncio
import json
import sqlite3
import time
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock

import pytest
from fastapi.responses import JSONResponse

from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge import retained_metadata_recovery as metadata
from agent_bridge import creation_worktree_writer as creation
from agent_bridge.session_status import SessionStatus
from test_creation_worktree_writer import recovery_fixture, writer_creation  # noqa: F401
from test_workspace_creation import creation_rig, rig as _rig  # noqa: F401

rig = _rig
pytestmark = pytest.mark.contract("agent_bridge.retained_metadata_recovery")


async def ordinary_resume_fixture(fixture: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from agent_bridge.routes import worktrees
    from agent_bridge.routes.worktree_entry import WorktreeEntry

    monkeypatch.delenv("AGENT_BRIDGE_INSTALL_DIR", raising=False)
    manager = fixture.rig.manager
    connect = manager._connect_via_session_host
    old, recovery = await missing_record(fixture, monkeypatch)
    monkeypatch.setattr(manager, "_connect_via_session_host", connect)
    row = fixture.rig.row
    entry = WorktreeEntry(
        id=row["id"], agent_name="task-worker", machine="local",
        path=row["path"], branch="worktree/test", status="active",
    )

    def unknown_ssh_environment(host: str, _environment: str | None) -> Any:
        raise KeyError(host)

    resolver = SimpleNamespace(
        agents={"task-worker": SimpleNamespace(
            project=old.target.project, host=None, managed=False, spawn_command=None,
            ssh_environment=None,
        )},
        resolve=lambda _owner: old.target,
        _is_local_loopback_agent=lambda config: config.host == "loopback",
        resolve_ssh_environment=unknown_ssh_environment,
    )
    cache = worktrees.WorktreeDiscoveryCache()
    cache._cache = {"task-worker": [entry]}
    cache._refresh.bind(("fixture",), ["task-worker"], available=True)
    cache._refresh.published(cache._refresh.epoch, {"task-worker": None})
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        db=manager.db, session_manager=manager, resolver=resolver, ready=True,
        worktree_discovery_cache=cache,
    )))
    monkeypatch.setattr(worktrees, "_find_cached_worktree_entry", AsyncMock(
        return_value=("task-worker", entry),
    ))
    command = fixture.command

    async def owner(_owner: Any, _config: Any, _resolver: Any, args: Any) -> str:
        if args[0] == "list-sessions":
            value = {"head_session": old.acp_session_id, "sessions": []}
        elif args[0] == "embody":
            value = {
                "ok": True, "dry_run": True, "native_admission_version": 1,
                "worktree_id": row["id"], "work_dir": row["path"],
                "resume_session": old.acp_session_id,
            }
        else:
            value = await command(
                args, old.target.project, owner_prefix=("retained-owner", "original-build"),
            )
            if args[0] == "head-session":
                value.update(
                    worktree_id=row["id"], head_revision=recovery.head_revision,
                    pending_handoffs=[],
                )
            elif args[0] == "record":
                value["worktree"]["bound_agent"] = None
        return json.dumps(value)

    monkeypatch.setattr(worktrees, "_run_for_agent", owner)
    return SimpleNamespace(
        fixture=fixture, manager=manager, row=row, request=request, old=old, recovery=recovery,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("loopback", [False, True])
async def test_worktree_head_hydration_adopts_metadata_without_native_execution(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch, loopback: bool,  # noqa: F811
) -> None:
    from agent_bridge import worktree_native_resume

    bound = await ordinary_resume_fixture(writer_creation, monkeypatch)
    resolver = bound.request.app.state.resolver
    if loopback:
        resolver.agents["task-worker"].host = "loopback"

    def refuse_launch_discovery(_owner: str) -> Any:
        raise AssertionError("Metadata hydration must not resolve launch plugins")

    monkeypatch.setattr(resolver, "resolve", refuse_launch_discovery)
    launches = len(bound.fixture.starts)
    results = await asyncio.gather(*(
        worktree_native_resume.hydrate_worktree(bound.row["id"], bound.request)
        for _ in range(2)
    ))
    values = [json.loads(bytes(result.body)) for result in results]
    assert values[0] == values[1]
    assert values[0]["session_id"] != bound.old.session_id
    assert values[0]["acp_session_id"] == bound.old.acp_session_id
    assert values[0]["status"] == SessionStatus.STOPPED.value
    assert values[0]["retained_metadata_adoption"]["predecessor_id"] == bound.old.session_id
    assert len(bound.fixture.starts) == launches
    assert len(bound.manager.list_sessions()) == 1
    assert not bound.manager.db.execute_read("SELECT * FROM turns")
    assert not bound.manager.db.execute_read("SELECT * FROM acquisition_launch_gates")
    assert bound.manager.db.execute_read(
        "SELECT creation_recoveries_json FROM workspace_embodiments",
    )[0][0] is None


@pytest.mark.asyncio
async def test_worktree_resume_recovers_original_keyed_native_without_a_repair_caller(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from agent_bridge import worktree_native_resume

    bound = await ordinary_resume_fixture(writer_creation, monkeypatch)
    fixture, manager, row = bound.fixture, bound.manager, bound.row
    request, old, recovery = bound.request, bound.old, bound.recovery
    result = await worktree_native_resume.resume_native_worktree(row["id"], request, "acp", strict=True)
    assert isinstance(result, JSONResponse)
    first = json.loads(bytes(result.body))
    assert first["session_id"] != old.session_id
    assert first["acp_session_id"] == first["durable_session_id"] == old.acp_session_id
    assert first["status"] == SessionStatus.IDLE.value
    assert manager.get_session(old.session_id) is None
    assert manager.db.get_session(old.session_id) is None
    assert len(manager.list_sessions()) == 1
    assert not manager.db.execute_read("SELECT * FROM turns")


    resumed = manager.get_session(first["session_id"])
    assert resumed.target.venue[creation.JOURNAL_KEY] == recovery.journal_key
    boundary = metadata.adoption(dict(manager.db.execute_read(
        "SELECT * FROM workspace_embodiments",
    )[0]))
    assert boundary is not None
    assert boundary["history"]["predecessor_id"] == old.session_id
    assert first["retained_recovery"]["state"] == "complete"
    assert first["retained_metadata_adoption"] == boundary
    assert first["worktree_writer_observation"]["native_session_id"] == old.acp_session_id
    before = dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    launches = len(fixture.starts)
    repeated = await worktree_native_resume.resume_native_worktree(row["id"], request, "acp", strict=True)
    assert isinstance(repeated, JSONResponse)
    assert json.loads(bytes(repeated.body))["session_id"] == first["session_id"]
    assert len(fixture.starts) == launches
    assert dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0]) == before
    assert manager._active_acquisitions == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("matches", [False, True])
async def test_exact_worktree_resume_preserves_retained_native_owner(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch, matches: bool,  # noqa: F811
) -> None:
    from fastapi import HTTPException
    from agent_bridge.worktree_exact_resume import ExactResumeBody, resume_exact_worktree

    bound = await ordinary_resume_fixture(writer_creation, monkeypatch)
    before = len(bound.fixture.starts)
    body = ExactResumeBody(native_session_id=bound.old.acp_session_id if matches else "other-native")
    if not matches:
        with pytest.raises(HTTPException) as refused:
            await resume_exact_worktree(bound.row["id"], bound.request, "acp", body)
        assert refused.value.status_code == 409
        assert not bound.manager.list_sessions()
        assert len(bound.fixture.starts) == before
        assert not bound.manager.db.execute_read("SELECT * FROM acquisition_launch_gates")
        return
    result = await resume_exact_worktree(bound.row["id"], bound.request, "acp", body)
    assert isinstance(result, JSONResponse)
    value = json.loads(bytes(result.body))
    assert value["acp_session_id"] == value["durable_session_id"] == bound.old.acp_session_id
    assert value["session_id"] != bound.old.session_id


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", [
    "unknown-operation", "head-changed", "not-ready", "cli", "foreign-transport",
])
async def test_ordinary_retained_resume_preserves_held_custody(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch, fault: str,  # noqa: F811
) -> None:
    from fastapi import HTTPException
    from agent_bridge import worktree_native_resume

    bound = await ordinary_resume_fixture(writer_creation, monkeypatch)
    manager, old, recovery = bound.manager, bound.old, bound.recovery
    adopted, _boundary = await metadata.prepare(
        manager, old.session_id, recovery, bound.fixture.requirement, lambda: True,
    )
    if fault == "unknown-operation":
        manager.db.execute_write(
            "UPDATE workspace_embodiments SET creation_recoveries_json=? WHERE key_digest=?",
            (json.dumps({recovery.operation_id: {"request": recovery.model_dump(), "state": "unknown"}}),
             recovery.journal_key),
        )
    elif fault == "head-changed":
        bound.fixture.rig.head["id"] = "different-native"
    elif fault == "not-ready":
        bound.request.app.state.ready = False
    elif fault == "foreign-transport":
        bound.request.app.state.resolver.agents["task-worker"].host = "different-host"
        bound.request.app.state.resolver.resolve = lambda _owner: replace(
            old.target, type="ssh", host="different-host",
        )
    mode: Literal["acp", "cli"] = "cli" if fault == "cli" else "acp"
    before = dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    launches = len(bound.fixture.starts)
    with pytest.raises(HTTPException):
        await worktree_native_resume.resume_native_worktree(bound.row["id"], bound.request, mode, strict=True)
    assert dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0]) == before
    assert manager.get_session(adopted.session_id) is adopted
    assert adopted.status == SessionStatus.STOPPED and adopted.client is None
    assert len(bound.fixture.starts) == launches
    assert not manager.db.execute_read("SELECT * FROM turns")


@pytest.mark.asyncio
@pytest.mark.parametrize("ready", [False, True])
async def test_ordinary_resume_refuses_changed_workspace_incarnation(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch, ready: bool,  # noqa: F811
) -> None:
    from fastapi import HTTPException
    from agent_bridge import worktree_native_resume

    bound = await ordinary_resume_fixture(writer_creation, monkeypatch)
    if ready:
        result = await worktree_native_resume.resume_native_worktree(
            bound.row["id"], bound.request, "acp", strict=True,
        )
        assert isinstance(result, JSONResponse)
    bound.row["creation_nonce"] = "replacement-allocation"
    before = dict(bound.manager.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    launches = len(bound.fixture.starts)
    with pytest.raises(HTTPException):
        await worktree_native_resume.resume_native_worktree(
            bound.row["id"], bound.request, "acp", strict=True,
        )
    assert dict(bound.manager.db.execute_read("SELECT * FROM workspace_embodiments")[0]) == before
    assert len(bound.fixture.starts) == launches
    assert not bound.manager.db.execute_read("SELECT * FROM turns")


@pytest.mark.asyncio
async def test_concurrent_worktree_resume_recovers_a_lost_reply_without_another_native_load(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from fastapi import HTTPException
    from agent_bridge import worktree_native_resume
    from agent_bridge.routes import sessions

    bound = await ordinary_resume_fixture(writer_creation, monkeypatch)
    render = sessions._session_info

    def lost_reply(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("controlled response loss after native completion")

    monkeypatch.setattr(sessions, "_session_info", lost_reply)
    with pytest.raises(HTTPException, match="controlled response loss"):
        await worktree_native_resume.resume_native_worktree(
            bound.row["id"], bound.request, "acp", strict=True,
        )
    launches = len(bound.fixture.starts)
    assert launches == 2  # Initial creation and the one retained native load.
    before = dict(bound.manager.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    monkeypatch.setattr(sessions, "_session_info", render)
    responses = await asyncio.gather(*(
        worktree_native_resume.resume_native_worktree(bound.row["id"], bound.request, "acp", strict=True)
        for _ in range(2)
    ))
    assert all(isinstance(item, JSONResponse) for item in responses)
    identities = {
        json.loads(bytes(item.body))["session_id"]
        for item in responses if isinstance(item, JSONResponse)
    }
    assert len(identities) == 1
    assert len(bound.fixture.starts) == launches
    assert dict(bound.manager.db.execute_read("SELECT * FROM workspace_embodiments")[0]) == before
    assert not bound.manager.db.execute_read("SELECT * FROM acquisition_launch_gates")


@pytest.mark.asyncio
async def test_later_stopped_activation_does_not_reuse_a_historical_native_completion(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from agent_bridge import worktree_native_resume

    bound = await ordinary_resume_fixture(writer_creation, monkeypatch)
    first = await worktree_native_resume.resume_native_worktree(
        bound.row["id"], bound.request, "acp", strict=True,
    )
    assert isinstance(first, JSONResponse)
    original = json.loads(bytes(first.body))
    session = bound.manager.get_session(original["session_id"])
    session.status, session.client = SessionStatus.STOPPED, None
    bound.manager.db.update_session_status(session.session_id, SessionStatus.STOPPED.value, time.time())
    second = await worktree_native_resume.resume_native_worktree(
        bound.row["id"], bound.request, "acp", strict=True,
    )
    assert isinstance(second, JSONResponse)
    current = json.loads(bytes(second.body))
    assert current["session_id"] == original["session_id"]
    assert current["acp_session_id"] == original["acp_session_id"] == bound.old.acp_session_id
    assert current["retained_metadata_adoption"] == original["retained_metadata_adoption"]
    assert current["retained_recovery"]["operation_id"] != original["retained_recovery"]["operation_id"]
    assert current["retained_recovery"]["state"] == "complete"
    operations = json.loads(bound.manager.db.execute_read(
        "SELECT creation_recoveries_json FROM workspace_embodiments",
    )[0][0])
    assert len(operations) == 2 and all(item["state"] == "complete" for item in operations.values())
    assert {item["request"]["head_revision"] for item in operations.values()} == {bound.recovery.head_revision}
    assert len(bound.fixture.starts) == 3
    assert not bound.manager.db.execute_read("SELECT * FROM turns")


async def missing_record(fixture: Any, monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    monkeypatch.delenv("AGENT_BRIDGE_INSTALL_DIR", raising=False)
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
@pytest.mark.parametrize("mode", ["acp", "cli"])
async def test_generic_worktree_resume_cannot_claim_a_gate_before_metadata_recovery(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch, mode: Literal["acp", "cli"],  # noqa: F811
) -> None:
    from fastapi import HTTPException, Request
    from agent_bridge import worktree_native_resume
    from agent_bridge.routes import worktrees
    from agent_bridge.routes.worktree_entry import WorktreeEntry

    fixture, manager = writer_creation, writer_creation.rig.manager
    old, request = await missing_record(fixture, monkeypatch)
    adopted, _boundary = await metadata.prepare(
        manager, old.session_id, request, fixture.requirement, lambda: True,
    )
    target = adopted.target
    entry = WorktreeEntry(
        id=target.worktree_id, path=target.cwd, agent_name="fixture", machine="fixture",
        branch="worktree/fixture", status="active",
    )
    monkeypatch.setattr(worktrees, "_find_cached_worktree_entry", AsyncMock(
        return_value=("fixture", entry),
    ))

    async def owner(_owner: Any, _config: Any, _resolver: Any, args: Any) -> str:
        if args[0] == "head-session":
            return json.dumps({
                "tracked": True, "worktree_id": target.worktree_id,
                "head_session": request.native_session_id, "head_revision": request.head_revision,
                "state": "active", "pending_handoffs": [],
            })
        if args[0] == "record":
            return json.dumps({"ok": True, "worktree": {
                **fixture.rig.row, "last_session_id": request.native_session_id,
                "execution_leg_opaque": False, "session_backend_opaque": False, "bound_agent": None,
            }})
        if args[0] == "list-sessions":
            return json.dumps({"sessions": []})
        raise AssertionError("generic resume reached native preparation before its metadata hold")

    monkeypatch.setattr(worktrees, "_run_for_agent", owner)
    launch = AsyncMock(side_effect=AssertionError("generic CLI launch bypassed metadata custody"))
    monkeypatch.setattr(worktree_native_resume, "run_owner_operation", launch)
    resolver = SimpleNamespace(
        agents={"fixture": SimpleNamespace(
            project=target.project, host=None, managed=False, spawn_command=None,
        )},
        resolve=lambda _owner: target,
    )
    http = Request({"type": "http", "app": SimpleNamespace(state=SimpleNamespace(
        db=manager.db, session_manager=manager, resolver=resolver,
    ))})
    with pytest.raises(HTTPException) as held:
        await worktree_native_resume.resume_native_worktree(target.worktree_id, http, mode)
    assert held.value.status_code == 409
    detail: Any = held.value.detail
    assert detail["reason"] == "retained_metadata_held"
    assert not manager.db.execute_read("SELECT * FROM acquisition_launch_gates")
    assert not manager.db.get_cli_mode_reservation(target.worktree_id)
    launch.assert_not_awaited()
    manager._connect_via_session_host.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", [False, True])
async def test_adopted_record_keeps_epoch_through_later_stop_resume_and_provider_refresh(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch, provider: bool,  # noqa: F811
) -> None:
    from agent_bridge import retained_session_recovery as recovery
    from agent_bridge import workspace_creation
    from agent_bridge.spawn_target import REQUEST_OVERRIDES_KEY

    fixture, manager = writer_creation, writer_creation.rig.manager
    if provider:
        fixture.rig.target = replace(
            fixture.rig.target, venue={"provider": "original"},
            env={"REQUEST_SETTING": "original-request"}, copilot_args=["--original-default"],
        )
        start = workspace_creation.start

        async def configured(*args: Any, **kwargs: Any) -> Any:
            return await start(
                *args, **kwargs, agent_name="fixture", copilot_args=["--request-argument"],
                env_overrides={"REQUEST_SETTING": "original-request"},
            )

        monkeypatch.setattr(workspace_creation, "start", configured)
    connect = manager._connect_via_session_host
    old, request = await missing_record(fixture, monkeypatch)
    adopted, _boundary = await metadata.prepare(
        manager, old.session_id, request, fixture.requirement, lambda: True,
    )
    monkeypatch.setattr(manager, "_connect_via_session_host", connect)
    if provider:
        assert adopted.target.venue[REQUEST_OVERRIDES_KEY] == old.target.venue[REQUEST_OVERRIDES_KEY]
    await recovery.resume(manager, adopted.session_id, request, fixture.requirement, lambda: True)
    await manager.stop_session(adopted.session_id, force=True)
    adopted.agent_name = "fixture"
    resolve = AsyncMock(
        return_value=replace(
            adopted.target, venue={"provider": "refreshed"}, env={"PROVIDER_DEFAULT": "new-default"},
            copilot_args=["--new-default"],
        ),
        side_effect=None if provider else AssertionError("internal epoch was classified as a provider"),
    )
    manager._resolver = SimpleNamespace(resolve_async=resolve)
    resumed = await manager.resume_session(adopted.session_id, drain=False)
    assert resumed is adopted
    if provider:
        resolve.assert_awaited_once_with("fixture")
        assert adopted.target.venue["provider"] == "refreshed"
        assert adopted.target.copilot_args == ["--new-default", "--request-argument"]
        assert adopted.target.env == {
            "PROVIDER_DEFAULT": "new-default", "REQUEST_SETTING": "original-request",
        }
    else:
        resolve.assert_not_awaited()
    assert adopted.target.venue[metadata.EPOCH_KEY] == adopted.session_id
    assert adopted.target.venue[creation.JOURNAL_KEY] == request.journal_key
    assert json.loads(manager.db.get_session(adopted.session_id)["target_json"])["venue"] == adopted.target.venue


@pytest.mark.asyncio
async def test_completed_metadata_recovery_admits_its_journaled_handoff_successor(
    writer_creation: Any, monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    from agent_bridge import retained_session_recovery as recovery, worktree_lineage

    fixture, manager = writer_creation, writer_creation.rig.manager
    connect = manager._connect_via_session_host
    old, request = await missing_record(fixture, monkeypatch)
    adopted, boundary = await metadata.prepare(
        manager, old.session_id, request, fixture.requirement, lambda: True,
    )
    monkeypatch.setattr(manager, "_connect_via_session_host", connect)
    await recovery.resume(manager, adopted.session_id, request, fixture.requirement, lambda: True)
    fixture.rig.client.acp_session_id = "native-successor"

    def link(_worktree: Any, _predecessor: Any, successor: Any, **_kwargs: Any) -> None:
        fixture.rig.head["id"] = successor

    monkeypatch.setattr(worktree_lineage, "link_succession", link)
    for name in ("note_handoff", "session_role", "history_digest"):
        monkeypatch.setattr(worktree_lineage, name, lambda *_a, **_kw: None)
    seed = AsyncMock(return_value=0)
    monkeypatch.setattr(manager, "submit_prompt", seed)
    successor = await manager.handoff_session(
        adopted.session_id, reason="fixture", seed_text="Continue the retained work",
    )
    assert successor.session_id != adopted.session_id
    assert successor.acp_session_id == "native-successor"
    assert successor.target.venue[creation.JOURNAL_KEY] == request.journal_key
    seed.assert_awaited_once()
    from agent_bridge import retained_worktree_resume

    passthrough = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(session_manager=manager)))
    assert await retained_worktree_resume.resume(
        passthrough, successor.target.worktree_id, "acp", expected_native=successor.acp_session_id,
    ) is None
    assert manager.get_session(successor.acp_session_id) is successor
    assert manager.get_session(request.native_session_id) is None
    resync = AsyncMock(return_value=0)
    monkeypatch.setattr(manager, "_resync_session_admitted", resync)
    assert await manager.resync_session(successor.acp_session_id) == 0
    assert resync.await_args is not None
    assert resync.await_args.args == (successor.session_id,)
    with pytest.raises(ValueError, match="predecessor event stream"):
        await manager.resync_session(adopted.session_id)
    resync.assert_awaited_once()
    row = dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0])
    assert json.loads(row[metadata.COLUMN]) == boundary
    handoff = json.loads(row["creation_handoffs_json"])[adopted.session_id]
    assert handoff["successor_id"] == successor.session_id and handoff["stage"] == "completed"
    with pytest.raises(sqlite3.IntegrityError, match="metadata"):
        manager.db.create_session(
            "unassociated-import", "unassociated", None, adopted.target.cwd,
            "local", "stopped", 1, target_json=adopted.target.to_json(),
        )


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
    schema_before = manager.db.execute_read("SELECT version FROM schema_version")[0][0]
    assert schema_before == db_core.SCHEMA_VERSION
    # The supported cutover owner opens passively and checks this before any migration.
    monkeypatch.setattr(db_core, "SCHEMA_VERSION", 48)
    with pytest.raises(ValueError, match="current schema"):
        Database(manager.db.db_path, read_only=True)
    assert dict(manager.db.execute_read("SELECT * FROM workspace_embodiments")[0]) == before
    assert json.loads(before[metadata.COLUMN]) == boundary
    assert manager.db.execute_read("SELECT version FROM schema_version")[0][0] == schema_before
