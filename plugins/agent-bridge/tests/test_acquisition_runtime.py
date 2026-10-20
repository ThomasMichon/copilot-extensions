"""Connected acquisition contracts over real SessionManager/SQLite boundaries."""

from __future__ import annotations

import time
import json
import asyncio
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge import acquisition_authority, acquisition_runtime, session_manager as core
from agent_bridge.acquisition_cli import ACQUISITION_PROTOCOL_VERSION, acquire as client_acquire
from agent_bridge.acquisition_contract import AcquisitionRefused, AcquisitionRequest
from agent_bridge.acquisition_store import AcquisitionStore
from agent_bridge.acquisition_executor import executor
from agent_bridge.acquisition_runtime import AcquisitionRuntime
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.models import SessionStatus
from agent_bridge.routes.acquisitions import router
from agent_bridge.session_manager import SessionManager
from agent_bridge.transport import SpawnTarget

pytestmark = pytest.mark.contract("agent_bridge.acquisition.runtime")


def req(**changes):
    return AcquisitionRequest.model_validate({
        "target": "task-worker", "mode": "acp", "task_id": "task-a",
        "task_generation": 0, "worktree_id": "worktree-a", "allocation": "created",
        "conversation_retired": False, "reservation_key": "reservation-a",
        "delivery_id": "delivery-a", "prompt": "Perform the assigned task",
        "project": "test-project", **changes,
    })


@pytest.fixture
def rig(tmp_db, session_manager, mock_acp_client, monkeypatch, tmp_path):
    head = {"id": None}
    calls = []
    source = {
        "task": {"id": "task-a", "generation": 0, "status": "queued"},
        "attachments": [],
        "acquisition_reservation": {
            "key": "reservation-a", "worktree": "worktree-a",
            "task_id": "task-a", "attempt": 1,
            "worktree_ownership": "created", "conversation_retired": False,
            "state": "reserving",
        },
    }

    async def resolve(name):
        if name == "dispatch:task-a":
            return SpawnTarget(type="session", worktree_id="worktree-a", venue=source)
        assert name == "task-worker"
        return SpawnTarget(type="local", cwd=str(tmp_path))

    async def provider_run(argv):
        assert argv[0] in {"acquisition-bind", "acquisition-mark"}
        assert argv[1] == "--request-json"
        return 0, argv[2], ""
    resolver = SimpleNamespace(
        resolve_async=resolve,
        namespace_resolvers={"dispatch": SimpleNamespace(_run=provider_run)},
    )

    async def command(args, project):
        calls.append((args, project))
        if args[0] == "list":
            assert args == [
                "list", "--json", "--cache-only", "--worktree-id", "worktree-a",
            ]
            return {"worktrees": [{"id": "worktree-a", "path": str(tmp_path), "status": "active"}]}
        if args[0] == "record":
            return {"version": 1, "ok": True, "worktree": {
                "id": "worktree-a", "repo": "test-project", "path": str(tmp_path),
                "status": "active", "creation_nonce": "native-fixture-allocation",
                "session_count": 1 if head["id"] else 0, "last_session_id": head["id"],
                "execution_leg": None, "execution_leg_opaque": False,
                "session_backend": None, "session_backend_opaque": False,
                "sessions_indexed": True,
            }}
        if args[0] == "head-session":
            return {"tracked": True, "head_session": head["id"],
                    "active": bool(head["id"]), "state": "active" if head["id"] else None}
        if args[0] == "bind-session":
            head["id"] = args[args.index("--session-id") + 1]
            return {"ok": True}
        assert args[0] == "embody"
        intent = args[args.index("--bridge-acquisition") + 1]
        if "--dry-run" in args:
            return {
                "dry_run": True, "worktree_id": "worktree-a",
                "resume_session": None if intent == "fresh" else intent,
            }
        native = "cli-native-new" if intent == "fresh" else intent
        driver = args[args.index("--driver") + 1]
        assert "--seed" not in args
        assert "--resume-head" not in args
        status = tmp_db.register_live_session(
            native, machine="test-machine", cwd=str(tmp_path), worktree_id="worktree-a",
            repo="test-project", branch="test", pid=99999999, role="worker",
            now=time.time(), driven_by=driver,
        )
        assert status == "live"
        tmp_db.execute_write("UPDATE live_sessions SET turn_state='idle' WHERE session_id=?", (native,))
        head["id"] = native
        return {"session": "wt-worktree-a", "ok": True}

    monkeypatch.setattr(acquisition_authority, "worktree_command", command)
    monkeypatch.setattr(acquisition_runtime, "worktree_command", command)
    monkeypatch.setattr(acquisition_runtime, "local_pid_alive", lambda _pid: False)

    async def host_connect(self, target, **kwargs):
        return mock_acp_client, mock_acp_client.acp_session_id

    monkeypatch.setattr(SessionManager, "_connect_via_session_host", host_connect)
    monkeypatch.setattr(core, "AcpClient", lambda **kw: mock_acp_client)
    proc = MagicMock()
    proc.proc.pid = 99999999
    proc.proc.returncode = None
    proc.proc.stderr.readline = AsyncMock(return_value=b"")
    monkeypatch.setattr(core, "spawn", AsyncMock(return_value=proc))
    monkeypatch.setattr(session_manager, "_try_reattach_live_host", AsyncMock(return_value=False))
    monkeypatch.setattr(session_manager, "_run_prompt", AsyncMock())
    # The connected boundary performs and verifies registration itself; keep
    # legacy fail-open auxiliary writes from invoking another process here.
    from agent_bridge import worktree_lineage
    monkeypatch.setattr(worktree_lineage, "register_session", lambda *a, **kw: False)
    runtime = AcquisitionRuntime(session_manager, resolver, tmp_db)
    return SimpleNamespace(
        runtime=runtime, db=tmp_db, manager=session_manager, client=mock_acp_client,
        head=head, source=source, calls=calls, resolver=resolver,
    )


@pytest.mark.asyncio
async def test_default_cli_worker_needs_no_acp_registry_profile(rig):
    resolve = rig.resolver.resolve_async

    async def without_worker_profile(name):
        if name == "task-worker":
            raise AssertionError("default CLI worker must not resolve an ACP profile")
        return await resolve(name)

    rig.resolver.resolve_async = without_worker_profile
    result = await rig.runtime.acquire(req(mode="cli"))
    assert result.target == "task-worker"
    assert result.native_session_id == "cli-native-new" == rig.head["id"]
    assert len(rig.db.execute_read("SELECT * FROM live_messages")) == 1
    assert await rig.runtime.acquire(req(mode="cli")) == result
    assert len(rig.db.execute_read("SELECT * FROM live_messages")) == 1


@pytest.mark.asyncio
async def test_exact_prepared_refusal_reports_not_started_on_replay(rig, monkeypatch):
    async def missing(_request):
        raise AcquisitionRefused("worktree_unknown", "owner did not resolve target")
    monkeypatch.setattr(acquisition_runtime, "verify_worktree", missing)
    for _ in range(2):
        with pytest.raises(AcquisitionRefused) as failure:
            await rig.runtime.acquire(req())
        assert failure.value.effect == "not_started"
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM acquisition_bindings")


@pytest.mark.asyncio
async def test_launching_refusal_never_claims_not_started(rig, monkeypatch):
    async def uncertain(_request, *_args):
        raise AcquisitionRefused("owner_operation_pending", "launch outcome unknown")
    monkeypatch.setattr(rig.runtime, "_launch", uncertain)
    with pytest.raises(AcquisitionRefused) as failure:
        await rig.runtime.acquire(req())
    assert failure.value.effect is None
    assert not rig.runtime.store.journal.refused_before_effect(req())


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cli", "acp"])
async def test_real_dispatch_allocation_qualifies_bridge_acquisition(rig, tmp_path, monkeypatch, mode):
    from pathlib import Path
    from dataclasses import asdict
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "agent-dispatch" / "src"))
    from agent_dispatch.queue import TaskQueue
    from agent_dispatch.session_acquisition import spawn_snapshot

    queue = TaskQueue(tmp_path / "dispatch.db")
    task = queue.create(title="work", repo="queue-repo")
    reservation, _ = queue.reserve_spawn(task.id)
    recorded = queue.record_spawn_worktree(
        reservation.key, "worktree-a", ownership="created", creating_host="fixture-host",
        allocation_project="test-project", worktree_path=str(tmp_path),
    )
    snapshot = spawn_snapshot(asdict(task), asdict(recorded), "worktree-a", {
        "project": recorded.allocation_project, "path": recorded.worktree_path,
    }, "created", False)
    rig.source["task"] = asdict(task)
    rig.source["acquisition_reservation"] = asdict(recorded)
    original_resolve = rig.resolver.resolve_async
    async def resolve(name):
        if name == f"dispatch:{task.id}":
            return SpawnTarget(type="session", worktree_id="worktree-a", venue=rig.source)
        return await original_resolve(name)
    rig.resolver.resolve_async = resolve
    original_command = acquisition_authority.worktree_command
    async def command(args, project):
        result = await original_command(args, project)
        if args[0] == "list":
            result["worktrees"][0]["repo"] = "test-project"
        return result
    monkeypatch.setattr(acquisition_authority, "worktree_command", command)
    request = req(
        version=2, mode=mode, task_id=task.id, task_generation=task.generation,
        reservation_key=recorded.key, delivery_id=f"{recorded.key}:acquire",
        project=snapshot["spawn_allocation_project"], worktree_path=snapshot["spawn_worktree_path"],
    )
    with pytest.raises(AcquisitionRefused, match="fence changed"):
        await rig.runtime.acquire(request.model_copy(update={"project": "queue-repo"}))
    result = await rig.runtime.acquire(request)
    assert result.native_session_id == rig.head["id"]
    assert await rig.runtime.acquire(request) == result
    assert len(rig.db.execute_read("SELECT * FROM acquisition_attempts")) == 1
    assert all(project == "test-project" for _, project in rig.calls)


@pytest.mark.asyncio
async def test_default_cli_retry_reconciles_legacy_missing_profile_before_creation(rig, monkeypatch):
    resolve = rig.resolver.resolve_async

    async def missing_worker_profile(name):
        if name == "task-worker":
            raise KeyError(name)
        return await resolve(name)

    async def legacy_preparation(request, _row):
        return await rig.resolver.resolve_async(request.target), {}

    rig.resolver.resolve_async = missing_worker_profile
    with monkeypatch.context() as patch:
        patch.setattr(rig.runtime, "_prepare_target", legacy_preparation)
        with pytest.raises(KeyError):
            await rig.runtime.acquire(req(mode="cli"))
    assert rig.db.execute_read(
        "SELECT state FROM acquisition_attempts WHERE delivery_id='delivery-a'",
    )[0]["state"] == "pending"
    original = {**rig.source["acquisition_reservation"], "state": "deferred"}
    current = {
        **original, "key": "reservation-b", "attempt": 2,
        "state": "reserving", "worktree_ownership": "reused",
    }
    rig.source["acquisition_reservation"] = current
    rig.source["acquisition_reservation_history"] = {
        "version": 1, "reservations": [current, original],
    }
    retry = req(
        mode="cli", allocation="reused",
        reservation_key="reservation-b", delivery_id="delivery-b",
    )
    result = await rig.runtime.acquire(retry)
    assert result.acquired == "created"
    assert result.target == "task-worker"
    receipt = rig.db.execute_read(
        "SELECT state,refusal_code FROM acquisition_attempts WHERE delivery_id='delivery-a'",
    )[0]
    assert receipt["state"] == "refused"
    assert receipt["refusal_code"] == "interrupted_before_launch"
    assert await rig.runtime.acquire(retry) == result
    assert len(rig.db.execute_read("SELECT * FROM live_messages")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,target", [
    ("acp", "task-worker"), ("cli", "named-missing"), ("acp", "named-missing"),
])
async def test_missing_registry_target_is_typed_and_refuses_prepared_journal(rig, mode, target):
    resolve = rig.resolver.resolve_async

    async def missing_target(name):
        if name == target:
            raise KeyError(name)
        return await resolve(name)

    rig.resolver.resolve_async = missing_target
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req(mode=mode, target=target))
    assert error.value.code == "target_unavailable"
    assert error.value.effect == "not_started"
    receipt = rig.db.execute_read(
        "SELECT state,refusal_code FROM acquisition_attempts WHERE delivery_id='delivery-a'",
    )[0]
    assert receipt["state"] == "refused"
    assert receipt["refusal_code"] == "target_unavailable"
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM live_messages")
    assert not rig.db.execute_read("SELECT * FROM turns")
    assert not any(args[0] == "embody" for args, _ in rig.calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("venue", ["local", "ssh"])
async def test_explicit_named_cli_target_retains_resolver_and_local_venue_checks(rig, venue):
    resolve = rig.resolver.resolve_async
    names = []

    async def named_target(name):
        names.append(name)
        if name == "named-worker":
            return SpawnTarget(type=venue, cwd="/named")
        return await resolve(name)

    rig.resolver.resolve_async = named_target
    if venue == "local":
        result = await rig.runtime.acquire(req(mode="cli", target="named-worker"))
        assert result.target == "named-worker"
        assert result.native_session_id == rig.head["id"]
    else:
        with pytest.raises(AcquisitionRefused) as error:
            await rig.runtime.acquire(req(mode="cli", target="named-worker"))
        assert error.value.code == "venue_unsupported"
        assert not rig.db.execute_read("SELECT * FROM live_messages")
    assert "named-worker" in names


@pytest.mark.asyncio
async def test_missing_registry_target_http_refusal_is_not_a_500(rig):
    resolve = rig.resolver.resolve_async

    async def missing_target(name):
        if name == "named-missing":
            raise KeyError(name)
        return await resolve(name)

    rig.resolver.resolve_async = missing_target
    app = FastAPI()
    app.include_router(router)
    app.state.session_manager = rig.manager
    app.state.resolver = rig.resolver
    app.state.db = rig.db
    app.state.acquisition_runtime = rig.runtime
    with TestClient(app) as http:
        response = http.post("/api/v1/acquisitions", json={
            **req(mode="cli", target="named-missing").model_dump(),
            "protocol_version": ACQUISITION_PROTOCOL_VERSION,
        })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "target_unavailable"
    assert not rig.db.execute_read("SELECT * FROM live_messages")


def test_workspace_request_cannot_downgrade_its_protocol():
    app = FastAPI()
    app.include_router(router)
    request = req(version=2, worktree_path="workspace-path")
    with TestClient(app) as http:
        response = http.post("/api/v1/acquisitions", json={
            **request.model_dump(), "protocol_version": 32,
        })
    assert response.status_code == 426
    assert response.json()["detail"]["effect"] == "not_started"


@pytest.mark.asyncio
@pytest.mark.parametrize("binding", ["task-worker", " task-worker \n"])
async def test_prepare_target_does_not_layer_same_padded_binding_twice(rig, binding):
    original_args = ["--agent", "task-worker"]
    target = SpawnTarget(type="local", cwd="/synthetic", copilot_args=original_args)
    resolver = MagicMock()
    resolver.resolve_async = AsyncMock(return_value=target)
    resolver.canonical_agent_name.return_value = "task-worker"
    resolver.agents = {
        "task-worker": SimpleNamespace(
            managed=False, copilot_path="", copilot_args=original_args,
            mcp_servers=[], env={},
        )
    }
    rig.runtime.resolver = resolver
    row = {"bound_agent": binding}
    result, _env = await rig.runtime._prepare_target(req(), row)
    assert result is target
    assert result.copilot_args == original_args
    assert row["bound_agent"] == binding
    resolver.canonical_agent_name.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("history", ["absent", "journal", "spawned"])
async def test_missing_owner_preflight_precedes_mark_and_preserves_ambiguity(rig, monkeypatch, history):
    if history == "journal":
        await rig.runtime.acquire(req())
    if history == "spawned":
        rig.source["acquisition_reservation"]["state"] = "spawned"
    marker = AsyncMock()
    monkeypatch.setattr(acquisition_runtime, "mark_dispatch_acquisition", marker)
    monkeypatch.setattr(
        acquisition_runtime, "verify_worktree",
        AsyncMock(side_effect=AcquisitionRefused("worktrees_unavailable", "owner missing")),
    )
    before = rig.db.execute_read("SELECT COUNT(*) AS n FROM acquisition_attempts")[0]["n"]
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req(delivery_id="delivery-next"))
    assert error.value.code == "worktrees_unavailable"
    assert error.value.effect is None
    marker.assert_not_awaited()
    after = rig.db.execute_read("SELECT * FROM acquisition_attempts WHERE delivery_id='delivery-next'")
    assert len(after) == 1
    assert after[0]["state"] == "refused"
    assert rig.db.execute_read("SELECT COUNT(*) AS n FROM acquisition_attempts")[0]["n"] == before + 1


async def preflight_retry(rig, monkeypatch, mode="acp"):
    first = req(mode=mode)
    with monkeypatch.context() as patch:
        patch.setattr(
            acquisition_runtime, "verify_worktree",
            AsyncMock(side_effect=AcquisitionRefused("worktrees_unavailable", "owner missing")),
        )
        with pytest.raises(AcquisitionRefused, match="owner missing"):
            await rig.runtime.acquire(first)
    original = {**rig.source["acquisition_reservation"], "state": "deferred"}
    current = {
        **original, "key": "reservation-b", "attempt": 2,
        "state": "reserving", "worktree_ownership": "reused",
    }
    rig.source["acquisition_reservation"] = current
    rig.source["acquisition_reservation_history"] = {
        "version": 1, "reservations": [current, original],
    }
    return req(
        mode=mode, allocation="reused", reservation_key="reservation-b", delivery_id="delivery-b",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cli", "acp"])
async def test_receipt_proven_preflight_retry_creates_first_conversation_once(rig, monkeypatch, mode):
    retry = await preflight_retry(rig, monkeypatch, mode)
    assert rig.db.execute_read(
        "SELECT state,refusal_code FROM acquisition_attempts WHERE delivery_id='delivery-a'",
    )[0]["refusal_code"] == "worktrees_unavailable"
    created = await rig.runtime.acquire(retry)
    assert created.acquired == "created"
    assert created.native_session_id == rig.head["id"]
    assert await rig.runtime.acquire(retry) == created
    table = "live_messages" if mode == "cli" else "turns"
    assert rig.db.execute_read(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 1


@pytest.mark.asyncio
async def test_every_preflight_retry_retains_a_bound_refusal_receipt(rig, monkeypatch):
    second = await preflight_retry(rig, monkeypatch)
    with monkeypatch.context() as patch:
        patch.setattr(
            acquisition_runtime, "verify_worktree",
            AsyncMock(side_effect=AcquisitionRefused("worktrees_unavailable", "owner missing")),
        )
        with pytest.raises(AcquisitionRefused):
            await rig.runtime.acquire(second)
    previous = {**rig.source["acquisition_reservation"], "state": "deferred"}
    current = {**previous, "key": "reservation-c", "attempt": 3, "state": "reserving"}
    root = rig.source["acquisition_reservation_history"]["reservations"][1]
    rig.source["acquisition_reservation"] = current
    rig.source["acquisition_reservation_history"] = {
        "version": 1, "reservations": [current, previous, root],
    }
    result = await rig.runtime.acquire(req(
        allocation="reused", reservation_key="reservation-c", delivery_id="delivery-c",
    ))
    assert result.acquired == "created"
    assert len(rig.db.execute_read("SELECT * FROM acquisition_attempts WHERE state='refused'")) == 2
    assert len(rig.db.execute_read("SELECT * FROM turns")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", [
    "missing_receipt", "missing_history", "history_gap", "spawned", "wrong_task",
    "digest", "launched", "native_handle", "not_created", "database_replaced",
    "stale_current", "retired", "future_generation", "strict_continuation", "existing_head",
    "active_predecessor",
])
async def test_initial_creation_refuses_unproven_or_historical_launch(rig, monkeypatch, defect):
    retry = await preflight_retry(rig, monkeypatch)
    history = rig.source["acquisition_reservation_history"]
    previous = history["reservations"][1]
    if defect in {"missing_receipt", "database_replaced"}:
        rig.db.execute_write("DELETE FROM acquisition_attempts WHERE delivery_id='delivery-a'")
    elif defect == "missing_history":
        del rig.source["acquisition_reservation_history"]
    elif defect == "history_gap":
        history["reservations"].pop()
    elif defect == "spawned":
        previous["state"] = "spawned"
    elif defect == "wrong_task":
        previous["task_id"] = "other-task"
    elif defect == "digest":
        rig.db.execute_write(
            "UPDATE acquisition_attempts SET request_digest='invalid' WHERE delivery_id='delivery-a'",
        )
    elif defect == "launched":
        rig.db.execute_write(
            "UPDATE acquisition_effects SET stage='launching' WHERE delivery_id='delivery-a'",
        )
    elif defect == "native_handle":
        previous["session_handle"] = "native-old"
    elif defect == "not_created":
        previous["worktree_ownership"] = "targeted"
    elif defect == "stale_current":
        history["reservations"][0] = {**history["reservations"][0], "key": "other-key"}
    elif defect == "retired":
        previous["conversation_retired"] = True
    elif defect == "future_generation":
        rig.db.execute_write(
            "UPDATE acquisition_effects SET data_json=json_set(data_json, '$._request.task_generation', 2) "
            "WHERE delivery_id='delivery-a'",
        )
    elif defect == "strict_continuation":
        retry = retry.model_copy(update={"intent": "continue"})
    elif defect == "existing_head":
        rig.head["id"] = "native-prior"
    elif defect == "active_predecessor":
        previous["state"] = "reserving"
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(retry)
    assert error.value.code == ("authority_unknown" if defect == "existing_head" else "continuity_ambiguous")
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM turns")


@pytest.mark.asyncio
async def test_first_conversation_revalidates_authority_after_target_preparation(rig, monkeypatch):
    retry = await preflight_retry(rig, monkeypatch)
    prepare = rig.runtime._prepare_target
    async def changed(*args):
        target = await prepare(*args)
        rig.source["acquisition_reservation_history"]["reservations"][1]["state"] = "spawned"
        return target
    monkeypatch.setattr(rig.runtime, "_prepare_target", changed)
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(retry)
    assert error.value.code == "initial_creation_fence_changed"
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM acquisition_launch_gates")


@pytest.mark.asyncio
@pytest.mark.parametrize("blocker", ["none", "unknown_head", "live_holder", "revoked"])
async def test_explicit_retirement_recovery_still_checks_native_holder_and_owner(rig, monkeypatch, blocker):
    rig.source["task"]["generation"] = 1
    rig.source["acquisition_reservation"].update(
        attempt=167, worktree_ownership="reused", conversation_retired=True,
    )
    request = req(allocation="reused", conversation_retired=True, task_generation=1)
    if blocker == "unknown_head":
        rig.head["id"] = "unknown-native"
    elif blocker == "live_holder":
        await rig.runtime.acquire(request)
        return_request = request.model_copy(update={"delivery_id": "second-delivery"})
        with pytest.raises(AcquisitionRefused) as error:
            await rig.runtime.acquire(return_request)
        assert error.value.code == "live_holder"
        assert len(rig.db.execute_read("SELECT * FROM turns")) == 1
        return
    elif blocker == "revoked":
        original = rig.runtime._prepare_target
        async def revoke(*args):
            result = await original(*args)
            rig.source["acquisition_reservation"]["conversation_retired"] = False
            return result
        monkeypatch.setattr(rig.runtime, "_prepare_target", revoke)
    if blocker == "none":
        result = await rig.runtime.acquire(request)
        assert result.acquired == "created"
        assert len(rig.db.execute_read("SELECT * FROM turns")) == 1
        return
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(request)
    assert error.value.code == (
        "authority_unknown" if blocker == "unknown_head" else "dispatch_fence_changed"
    )
    assert not rig.manager.list_sessions()


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cli", "acp"])
async def test_new_live_and_stopped_resume_preserve_native_id(rig, mode):
    first = req(mode=mode)
    created = await rig.runtime.acquire(first)
    assert created.acquired == "created"
    assert created.native_session_id == rig.head["id"]
    replay = await rig.runtime.acquire(first)
    assert replay == created
    if mode == "acp":
        assert rig.db.execute_read("SELECT COUNT(*) AS n FROM turns")[0]["n"] == 1
        prompt = rig.db.execute_read("SELECT prompt FROM turns")[0]["prompt"]
        assert f"--owner-session-id {created.native_session_id}" in prompt
        assert f"--owner-session-id {created.session_id}" not in prompt
        session = rig.manager.get_session(created.session_id)
        if session._prompt_task:
            await session._prompt_task
        session.status = SessionStatus.IDLE
        rig.db.update_session_status(session.session_id, "idle", time.time())
    else:
        assert rig.db.execute_read("SELECT COUNT(*) AS n FROM live_messages")[0]["n"] == 1
        assert f"--owner-session-id {created.native_session_id}" in rig.db.execute_read(
            "SELECT body FROM live_messages",
        )[0]["body"]
        rig.db.execute_write(
            "UPDATE live_messages SET delivered_at=?, consumed_at=?, outcome='applied'",
            (time.time(), time.time()),
        )
    rig.source["task"]["generation"] = 1
    rig.source["acquisition_reservation"].update(
        key="reservation-b", worktree_ownership="reused",
        session_handle=f"local-body:{created.session_id}" if mode == "acp" else created.native_session_id,
    )
    follow = req(
        mode=mode, task_generation=1, allocation="reused",
        reservation_key="reservation-b", delivery_id="delivery-b",
        prior_session_id=created.session_id, prior_native_session_id=created.native_session_id,
    )
    reused = await rig.runtime.acquire(follow)
    assert reused.acquired == "reused"
    assert reused.native_session_id == created.native_session_id
    if mode == "acp":
        session = rig.manager.get_session(created.session_id)
        if session._prompt_task:
            await session._prompt_task
        session.status = SessionStatus.STOPPED
        session.client = None
        rig.db.update_session_status(session.session_id, "stopped", time.time())
        rig.db.release_worktree_ownership(worktree_id="worktree-a")
    else:
        rig.db.execute_write(
            "UPDATE live_messages SET delivered_at=?, consumed_at=?, outcome='applied'",
            (time.time(), time.time()),
        )
        rig.db.execute_write("UPDATE live_sessions SET status='expired',updated_at=0")
    rig.source["acquisition_reservation"]["key"] = "reservation-c"
    resumed = await rig.runtime.acquire(follow.model_copy(update={
        "reservation_key": "reservation-c", "delivery_id": "delivery-c",
    }))
    assert resumed.acquired == "resumed"
    assert resumed.native_session_id == created.native_session_id
    if mode == "acp":
        rig.client.load_session.assert_awaited_once()
        assert rig.client.load_session.await_args.kwargs["session_id"] == created.native_session_id
        if session._prompt_task:
            await session._prompt_task
    else:
        launches = [args for args, _ in rig.calls if args[0] == "embody" and "--dry-run" not in args]
        assert len(launches) == 2
        assert launches[-1][launches[-1].index("--bridge-acquisition") + 1] == created.native_session_id


@pytest.mark.asyncio
async def test_current_generation_refuses_stale_admission(rig):
    rig.source["task"]["generation"] = 1
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req())
    assert error.value.code == "dispatch_fence_changed"
    assert not rig.manager.list_sessions()


@pytest.mark.asyncio
async def test_real_constructed_client_envelope_and_http_response(rig):
    app = FastAPI()
    app.include_router(router)
    app.state.session_manager = rig.manager
    app.state.resolver = rig.resolver
    app.state.db = rig.db
    app.state.acquisition_runtime = rig.runtime
    with TestClient(app) as http:
        rig.source["task"]["id"] = "other"
        foreign = http.post("/api/v1/acquisitions", json={
            **req(mode="cli").model_dump(), "protocol_version": 30,
            "force": True,
        })
        assert foreign.status_code == 409, foreign.text
        assert foreign.json()["detail"]["code"] == "dispatch_fence_changed"
        assert not rig.db.execute_read("SELECT * FROM live_messages")
        rig.source["task"]["id"] = "task-a"
        response = http.post("/api/v1/acquisitions", json={
            **req(mode="cli").model_dump(), "protocol_version": 30,
            "future_observation": {"status": "ready"},
        })
        assert response.status_code == 200, response.text
        assert response.json()["native_session_id"] == "cli-native-new"
        assert http.post("/api/v1/acquisitions", json={
            **req(mode="cli").model_dump(), "protocol_version": 29,
            "force": True,
        }).status_code == 426
        refused = http.post("/api/v1/acquisitions", json={
            **req(mode="cli", charter="unsupported").model_dump(), "protocol_version": 30,
        })
        assert refused.status_code == 409
        assert refused.json()["detail"]["code"] == "cli_options_unsupported"
        assert refused.json()["detail"]["effect"] == "not_started"
    client = BridgeClient("http://unused", "unused", connect_grace=0)
    client._daemon_proto = (ACQUISITION_PROTOCOL_VERSION, 1)
    sent = []

    def request(method, path, body, **kw):
        sent.append((method, path, body, kw))
        return response.json() | {"future_observation": {"status": "ready"}}

    client._request = request
    result = client_acquire(client, req(mode="cli"))
    assert result.native_session_id == response.json()["native_session_id"]
    assert sent[0][:2] == ("POST", "/api/v1/acquisitions")
    assert sent[0][2]["protocol_version"] == ACQUISITION_PROTOCOL_VERSION
    assert sent[0][2]["mode"] == "cli"
    for old_protocol in (30, 31):
        client._daemon_proto = (old_protocol, 1)
        with pytest.raises(BridgeClientError) as error:
            client_acquire(client, req(mode="cli"))
        assert error.value.status == 426
        assert error.value.detail["code"] == "unsupported_acquisition_protocol"
        assert error.value.detail["effect"] == "not_started"
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_reconciliation_after_delivery_does_not_redeliver(rig):
    first = req(mode="cli")
    original = rig.runtime.store.journal.complete
    rig.runtime.store.journal.complete = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("caller lost"))
    with pytest.raises(RuntimeError, match="caller lost"):
        await rig.runtime.acquire(first)
    rig.runtime.store.journal.complete = original
    recovered = await rig.runtime.acquire(first)
    assert recovered.native_session_id == "cli-native-new"
    assert rig.db.execute_read("SELECT COUNT(*) AS n FROM live_messages")[0]["n"] == 1
    assert not rig.db.execute_read("SELECT * FROM acquisition_launch_gates")


@pytest.mark.asyncio
@pytest.mark.parametrize("tracked", [False, True])
async def test_cli_busy_evidence_does_not_retain_consumed_input_or_legacy_history(rig, tracked):
    request = req(mode="cli")
    result = await rig.runtime.acquire(request)
    native = result.native_session_id
    message = rig.db.list_pending_live_messages(native)[0]
    if not tracked:
        rig.db.execute_write(
            "UPDATE live_messages SET consumption_tracked=0 WHERE id=?", (message["id"],),
        )
    assert rig.runtime._evidence(request, rig.source, native).conversations[0].busy
    assert rig.db.ack_live_messages(native, [message["id"]], time.time()) == 1
    assert rig.runtime._evidence(request, rig.source, native).conversations[0].busy is tracked
    assert rig.db.consume_live_message(native, message["id"], time.time()) == 1
    evidence = rig.runtime._evidence(request, rig.source, native)
    assert not evidence.conversations[0].busy
    assert rig.db.live_message_receipt(native, message["id"])["sdk_accepted_at"] is not None
    rig.db.enqueue_live_message(native, "peer", "new work", time.time())
    assert rig.runtime._evidence(request, rig.source, native).conversations[0].busy


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cli", "acp"])
async def test_inspection_requires_actual_durable_prompt_admission(rig, monkeypatch, mode):
    captured = {}
    if mode == "cli":
        original = rig.db.enqueue_live_message_if_fresh
        def interrupted(*args, **kwargs):
            captured.update(args=args, kwargs=kwargs)
            raise RuntimeError("before durable admission")
        monkeypatch.setattr(rig.db, "enqueue_live_message_if_fresh", interrupted)
    else:
        async def interrupted(session_id, prompt, **kwargs):
            captured.update(session_id=session_id, prompt=prompt, kwargs=kwargs)
            raise RuntimeError("before durable admission")
        monkeypatch.setattr(rig.manager, "_submit_prompt_locked", interrupted)
    with pytest.raises(RuntimeError, match="before durable admission"):
        await rig.runtime.acquire(req(mode=mode))
    before = await rig.runtime.inspect_pending("delivery-a")
    assert before["stage"] == "delivering"
    assert before["delivery_admitted"] is False
    if mode == "cli":
        wrong = {**captured["kwargs"], "body": "another prompt"}
        message, _ = original(*captured["args"], **wrong)
        assert message is not None
        assert (await rig.runtime.inspect_pending("delivery-a"))["delivery_admitted"] is False
        rig.db.execute_write(
            "UPDATE live_messages SET body=? WHERE idempotency_key=?",
            (captured["kwargs"]["body"], "acquisition:delivery-a"),
        )
    else:
        rig.db.create_turn(
            captured["session_id"], 0, "another prompt", time.time(),
            delivery_id=captured["kwargs"]["delivery_id"],
        )
        assert (await rig.runtime.inspect_pending("delivery-a"))["delivery_admitted"] is False
        rig.db.execute_write(
            "UPDATE turns SET prompt=? WHERE acquisition_delivery_id=?",
            (captured["prompt"], "delivery-a"),
        )
    admitted = await rig.runtime.inspect_pending("delivery-a")
    assert admitted["stage"] == "delivering"
    assert admitted["delivery_admitted"] is True
    assert admitted["recorded_result"] == before["recorded_result"]


@pytest.mark.asyncio
@pytest.mark.parametrize("race", ["message", "turn"])
async def test_cli_admission_atomically_refuses_competing_work(rig, monkeypatch, race):
    original = rig.db.enqueue_live_message_if_fresh
    captured = {}
    def competing(session_id, **kwargs):
        captured.update(session_id=session_id, kwargs=kwargs)
        if race == "message":
            rig.db.enqueue_live_message(session_id, "other", "competing work", time.time())
        else:
            rig.db.execute_write(
                "UPDATE live_sessions SET turn_state='running' WHERE session_id=?", (session_id,),
            )
        return original(session_id, **kwargs)
    monkeypatch.setattr(rig.db, "enqueue_live_message_if_fresh", competing)
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req(mode="cli"))
    assert error.value.code == "session_busy"
    assert not (await rig.runtime.inspect_pending("delivery-a"))["delivery_admitted"]
    assert not rig.db.execute_read(
        "SELECT id FROM live_messages WHERE idempotency_key='acquisition:delivery-a'",
    )
    rig.db.execute_write("DELETE FROM live_messages WHERE sender='other'")
    rig.db.execute_write("UPDATE live_sessions SET turn_state='idle'")
    first, reason = original(captured["session_id"], **captured["kwargs"])
    assert first is not None and reason is None
    rig.db.execute_write("UPDATE live_sessions SET turn_state='running'")
    repeated, reason = original(captured["session_id"], **captured["kwargs"])
    assert repeated == first and reason is None
    assert len(rig.db.execute_read(
        "SELECT id FROM live_messages WHERE idempotency_key='acquisition:delivery-a'",
    )) == 1


@pytest.mark.asyncio
async def test_cli_overrides_are_refused_not_dropped(rig):
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req(mode="cli", model="requested-model"))
    assert error.value.code == "cli_options_unsupported"
    assert error.value.effect == "not_started"
    assert not rig.db.execute_read("SELECT * FROM acquisition_attempts")


@pytest.mark.asyncio
async def test_cli_actual_launch_native_rename_is_refused_without_delivery(rig, monkeypatch):
    original = acquisition_runtime.worktree_command
    async def renamed(args, project):
        result = await original(args, project)
        if args[0] == "embody" and "--dry-run" not in args:
            rig.head["id"] = "renamed-native"
        return result
    monkeypatch.setattr(acquisition_runtime, "worktree_command", renamed)
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req(mode="cli", verify_timeout=0.01))
    assert error.value.code == "native_head_pending"
    assert not rig.db.execute_read("SELECT * FROM live_messages")


@pytest.mark.asyncio
async def test_existing_cli_reservation_refuses_before_installing_acquisition_gate(rig):
    rig.db.create_cli_mode_reservation("worktree-a", now=time.time())
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req(mode="cli"))
    assert error.value.code == "cli_launch_pending"
    assert not rig.db.execute_read("SELECT * FROM acquisition_launch_gates")
    assert rig.db.execute_read("SELECT state FROM acquisition_attempts")[0]["state"] == "refused"


@pytest.mark.asyncio
async def test_drain_during_preflight_refuses_native_launch(rig, monkeypatch):
    original = rig.runtime._prepare_target
    async def prepare(*args):
        output = await original(*args)
        rig.manager.set_draining(True)
        return output
    monkeypatch.setattr(rig.runtime, "_prepare_target", prepare)
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req())
    assert error.value.code == "acquisition_draining"
    assert not rig.manager.list_sessions()
    assert rig.manager._active_acquisitions == 0


@pytest.mark.asyncio
async def test_drain_waits_for_active_acquisition_executor(rig, monkeypatch):
    monkeypatch.setattr(rig.manager, "graceful_cancel_for_redeploy", AsyncMock())
    monkeypatch.setattr(rig.manager, "_live_host_records", lambda: [])
    rig.manager._active_acquisitions = 1
    pending = asyncio.create_task(rig.manager.drain(timeout=1, poll=0.01))
    await asyncio.sleep(0.03)
    assert not pending.done()
    rig.manager._active_acquisitions = 0
    assert (await pending)["drained"] is True


@pytest.mark.asyncio
async def test_exact_owned_native_recovers_after_crash_before_ledger_registration(rig, monkeypatch):
    original = acquisition_runtime.worktree_command
    async def interrupted(args, project):
        if args[0] == "bind-session":
            raise RuntimeError("crash before register")
        return await original(args, project)
    monkeypatch.setattr(acquisition_runtime, "worktree_command", interrupted)
    with pytest.raises(RuntimeError, match="crash before register"):
        await rig.runtime.acquire(req())
    assert rig.head["id"] is None
    native = rig.manager.list_sessions()[0].acp_session_id
    monkeypatch.setattr(acquisition_runtime, "worktree_command", original)
    recovered = await rig.runtime.acquire(req())
    assert recovered.native_session_id == native
    assert rig.head["id"] == native
    session = rig.manager.get_session(recovered.session_id)
    if session._prompt_task:
        await session._prompt_task


@pytest.mark.asyncio
async def test_new_attempt_refreshes_native_head_after_prior_reconciliation(rig, monkeypatch):
    original = acquisition_runtime.worktree_command
    async def interrupted(args, project):
        if args[0] == "bind-session":
            raise RuntimeError("crash before register")
        return await original(args, project)
    monkeypatch.setattr(acquisition_runtime, "worktree_command", interrupted)
    with pytest.raises(RuntimeError):
        await rig.runtime.acquire(req())
    native = rig.manager.list_sessions()[0].acp_session_id
    monkeypatch.setattr(acquisition_runtime, "worktree_command", original)
    rig.source["acquisition_reservation"].update(key="reservation-b", worktree_ownership="reused")
    result = await rig.runtime.acquire(req(
        allocation="reused", reservation_key="reservation-b", delivery_id="delivery-b",
    ))
    assert result.native_session_id == native
    session = rig.manager.get_session(result.session_id)
    if session._prompt_task:
        await session._prompt_task


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cli", "acp"])
@pytest.mark.parametrize("retired", [False, True])
async def test_interrupted_launch_reconciles_before_new_attempt_without_second_process(rig, mode, retired):
    if retired:
        rig.source["task"]["generation"] = 1
        rig.source["acquisition_reservation"].update(
            worktree_ownership="reused", conversation_retired=True,
        )
    original_request = req(
        mode=mode, task_generation=1 if retired else 0,
        allocation="reused" if retired else "created",
        conversation_retired=retired,
    )
    original = rig.runtime._deliver
    rig.runtime._deliver = AsyncMock(side_effect=RuntimeError("delivery not begun"))
    with pytest.raises(RuntimeError, match="delivery not begun"):
        await rig.runtime.acquire(original_request)
    rig.runtime._deliver = original
    rig.source["task"]["generation"] = 1
    rig.source["acquisition_reservation"].update(key="reservation-b", worktree_ownership="reused")
    retry_request = req(
        mode=mode, task_generation=1, allocation="reused",
        reservation_key="reservation-b", delivery_id="delivery-b",
        conversation_retired=retired,
    )
    recovered = await rig.runtime.acquire(retry_request)
    assert recovered.acquired == "reused"
    assert await rig.runtime.acquire(retry_request) == recovered
    assert rig.db.execute_read(
        "SELECT state FROM acquisition_attempts WHERE delivery_id='delivery-a'",
    )[0]["state"] == "refused"
    assert not rig.db.execute_read("SELECT * FROM acquisition_launch_gates")
    if mode == "cli":
        assert len([args for args, _ in rig.calls if args[0] == "embody" and "--dry-run" not in args]) == 1
        assert rig.db.execute_read("SELECT COUNT(*) AS n FROM live_messages")[0]["n"] == 1
    else:
        assert len(rig.manager.list_sessions()) == 1
        assert rig.db.execute_read("SELECT COUNT(*) AS n FROM turns")[0]["n"] == 1
        session = rig.manager.get_session(recovered.session_id)
        if session._prompt_task:
            await session._prompt_task


@pytest.mark.asyncio
@pytest.mark.parametrize("defect", ["new_generation", "retired_binding", "foreign_binding", "no_binding", "explicit_fresh"])
async def test_fresh_retry_never_adopts_unproven_or_reauthorized_holder(rig, defect):
    rig.source["task"]["generation"] = 1
    rig.source["acquisition_reservation"].update(
        worktree_ownership="reused", conversation_retired=True,
    )
    original = rig.runtime._deliver
    rig.runtime._deliver = AsyncMock(side_effect=RuntimeError("delivery not begun"))
    sample = req(allocation="reused", conversation_retired=True, task_generation=1)
    with pytest.raises(RuntimeError, match="delivery not begun"):
        await rig.runtime.acquire(sample)
    rig.runtime._deliver = original
    # Reconcile the original launch before changing its authoritative binding.
    rig.source["acquisition_reservation"]["key"] = "reservation-b"
    await rig.runtime._reconcile_prior(
        sample.model_copy(update={"reservation_key": "reservation-b", "delivery_id": "delivery-b"}),
        {"path": "unused"}, rig.head["id"],
    )
    changes = {}
    if defect == "new_generation":
        rig.source["task"]["generation"] = 2
        changes["task_generation"] = 2
    elif defect == "retired_binding":
        rig.db.execute_write("UPDATE acquisition_bindings SET retired=1")
    elif defect == "foreign_binding":
        rig.db.execute_write("UPDATE acquisition_bindings SET task_id='task-other'")
    elif defect == "no_binding":
        rig.db.execute_write("DELETE FROM acquisition_bindings")
    else:
        changes["intent"] = "fresh"
    with pytest.raises(AcquisitionRefused):
        await rig.runtime.acquire(sample.model_copy(update={
            "reservation_key": "reservation-b", "delivery_id": "delivery-b", **changes,
        }))
    assert len(rig.manager.list_sessions()) == 1
    assert not rig.db.execute_read("SELECT * FROM turns")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cli", "acp"])
@pytest.mark.parametrize("new_attempt", [False, True])
async def test_retirement_recovery_settles_admitted_turn_without_duplicate(rig, mode, new_attempt):
    rig.source["task"]["generation"] = 1
    rig.source["acquisition_reservation"].update(
        worktree_ownership="reused", conversation_retired=True,
    )
    sample = req(mode=mode, allocation="reused", conversation_retired=True, task_generation=1)
    original_deliver = rig.runtime._deliver
    rig.runtime._deliver = AsyncMock(side_effect=RuntimeError("before delivery"))
    with pytest.raises(RuntimeError):
        await rig.runtime.acquire(sample)
    rig.runtime._deliver = original_deliver
    rig.source["acquisition_reservation"]["key"] = "reservation-b"
    retry = sample.model_copy(update={"reservation_key": "reservation-b", "delivery_id": "delivery-b"})
    original_complete = rig.runtime.store.journal.complete
    def interrupted_complete(request, *args, **kwargs):
        if request.delivery_id == "delivery-b":
            raise RuntimeError("after admission")
        return original_complete(request, *args, **kwargs)
    rig.runtime.store.journal.complete = interrupted_complete
    with pytest.raises(RuntimeError, match="after admission"):
        await rig.runtime.acquire(retry)
    rig.runtime.store.journal.complete = original_complete
    if new_attempt:
        # Settling a superseded receipt is distinct from admitting another turn.
        rig.source["acquisition_reservation"]["key"] = "reservation-c"
        current = retry.model_copy(update={"reservation_key": "reservation-c", "delivery_id": "delivery-c"})
        with pytest.raises(AcquisitionRefused):
            await rig.runtime.acquire(current)
    else:
        await rig.runtime.acquire(retry)
    table = "live_messages" if mode == "cli" else "turns"
    assert rig.db.execute_read(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 1
    assert rig.db.execute_read(
        "SELECT state FROM acquisition_attempts WHERE delivery_id='delivery-b'",
    )[0]["state"] == "completed"
    for session in rig.manager.list_sessions():
        if session._prompt_task:
            await session._prompt_task


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cli", "acp"])
@pytest.mark.parametrize("defect", ["none", "caller", "binding", "generation", "retired", "head", "authority", "fresh"])
async def test_legacy_superseded_launch_requires_exact_positive_native_provenance(rig, mode, defect):
    rig.source["task"]["generation"] = 1
    rig.source["acquisition_reservation"].update(
        worktree_ownership="reused", conversation_retired=True,
    )
    first = req(mode=mode, allocation="reused", conversation_retired=True, task_generation=1)
    deliver = rig.runtime._deliver
    rig.runtime._deliver = AsyncMock(side_effect=RuntimeError("before delivery"))
    with pytest.raises(RuntimeError):
        await rig.runtime.acquire(first)
    rig.runtime._deliver = deliver
    token, _, data = rig.runtime.store.recovery(first)
    rig.runtime.store.stage(
        first, "launching", acquired="created", session_id=None, native_session_id=None,
        retired_native_ids=data.get("retired_native_ids", []),
        creation_authorized=defect != "authority",
    )
    rig.runtime.store.journal.refuse(first, token, "superseded_before_delivery")
    rig.runtime.store.release_gate(first)
    if defect == "caller":
        if mode == "acp":
            rig.db.execute_write("UPDATE sessions SET caller_id='acquisition:another-delivery'")
        else:
            rig.db.execute_write("UPDATE live_sessions SET driven_by='another-driver'")
    elif defect == "binding":
        rig.db.execute_write("UPDATE acquisition_bindings SET task_id='another-task'")
    elif defect == "generation":
        rig.db.execute_write("UPDATE acquisition_bindings SET task_generation=0")
    elif defect == "retired":
        rig.db.execute_write("UPDATE acquisition_bindings SET retired=1")
    elif defect == "head":
        rig.head["id"] = "another-native"
    rig.source["acquisition_reservation"]["key"] = "reservation-b"
    second = first.model_copy(update={
        "reservation_key": "reservation-b", "delivery_id": "delivery-b",
        "intent": "fresh" if defect == "fresh" else "automatic",
    })
    if defect != "none":
        with pytest.raises(AcquisitionRefused):
            await rig.runtime.acquire(second)
        table = "live_messages" if mode == "cli" else "turns"
        assert not rig.db.execute_read(f"SELECT * FROM {table}")
        return
    result = await rig.runtime.acquire(second)
    assert result.native_session_id == rig.head["id"]
    assert await rig.runtime.acquire(second) == result
    rig.source["acquisition_reservation"]["key"] = "reservation-c"
    third = first.model_copy(update={"reservation_key": "reservation-c", "delivery_id": "delivery-c"})
    assert not rig.runtime.store.retirement_recovery_proven(
        third, result.session_id, result.native_session_id,
    )
    legacy = rig.db.execute_read(
        "SELECT stage,data_json FROM acquisition_effects WHERE delivery_id='delivery-a'",
    )[0]
    assert legacy["stage"] == "launching"
    assert "result" not in json.loads(legacy["data_json"])
    table = "live_messages" if mode == "cli" else "turns"
    assert rig.db.execute_read(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 1
    for session in rig.manager.list_sessions():
        if session._prompt_task:
            await session._prompt_task


def test_retirement_proof_uses_persistent_history_index(tmp_db, monkeypatch):
    store = AcquisitionStore(tmp_db)
    tmp_db.execute_write("DROP INDEX idx_acquisition_history")
    store = AcquisitionStore(tmp_db)
    original_read = tmp_db.execute_read
    queries = []
    def capture(sql, parameters=()):
        queries.append((sql, parameters))
        return original_read(sql, parameters)
    monkeypatch.setattr(tmp_db, "execute_read", capture)
    assert not store.retirement_recovery_proven(req(), "session-a", "native-a")
    sql, parameters = queries[0]
    plan = original_read("EXPLAIN QUERY PLAN " + sql, parameters)
    assert any("SEARCH a USING INDEX idx_acquisition_history" in row["detail"] for row in plan)
    assert not any("SCAN a" in row["detail"] for row in plan)
    indexes = original_read("PRAGMA index_list(acquisition_attempts)")
    assert any(row["name"] == "idx_acquisition_history" and not row["partial"] for row in indexes)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cli", "acp"])
async def test_completed_retirement_recovery_cannot_authorize_another_delivery(rig, mode):
    rig.source["task"]["generation"] = 1
    rig.source["acquisition_reservation"].update(
        worktree_ownership="reused", conversation_retired=True,
    )
    first = req(mode=mode, allocation="reused", conversation_retired=True, task_generation=1)
    deliver = rig.runtime._deliver
    rig.runtime._deliver = AsyncMock(side_effect=RuntimeError("before delivery"))
    with pytest.raises(RuntimeError):
        await rig.runtime.acquire(first)
    rig.runtime._deliver = deliver
    rig.source["acquisition_reservation"]["key"] = "reservation-b"
    second = first.model_copy(update={"reservation_key": "reservation-b", "delivery_id": "delivery-b"})
    result = await rig.runtime.acquire(second)
    for session in rig.manager.list_sessions():
        if session._prompt_task:
            await session._prompt_task
    rig.db.execute_write("UPDATE live_messages SET delivered_at=?, outcome='applied'", (time.time(),))
    rig.source["acquisition_reservation"]["key"] = "reservation-c"
    third = first.model_copy(update={"reservation_key": "reservation-c", "delivery_id": "delivery-c"})
    assert not rig.runtime.store.retirement_recovery_proven(
        third, result.session_id, result.native_session_id,
    )
    with pytest.raises(AcquisitionRefused):
        await rig.runtime.acquire(third)
    assert await rig.runtime.acquire(second) == result
    table = "live_messages" if mode == "cli" else "turns"
    assert rig.db.execute_read(f"SELECT COUNT(*) AS n FROM {table}")[0]["n"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("new_attempt", [False, True])
async def test_cli_retirement_recovery_survives_launching_with_previous_driver(rig, monkeypatch, new_attempt):
    rig.source["task"]["generation"] = 1
    rig.source["acquisition_reservation"].update(
        worktree_ownership="reused", conversation_retired=True,
    )
    first = req(mode="cli", allocation="reused", conversation_retired=True, task_generation=1)
    deliver = rig.runtime._deliver
    rig.runtime._deliver = AsyncMock(side_effect=RuntimeError("before delivery"))
    with pytest.raises(RuntimeError):
        await rig.runtime.acquire(first)
    rig.runtime._deliver = deliver
    stage = rig.runtime.store.stage
    def interrupted(request, name, **data):
        if request.delivery_id == "delivery-b" and name == "launched":
            raise RuntimeError("after reuse before receipt")
        stage(request, name, **data)
    monkeypatch.setattr(rig.runtime.store, "stage", interrupted)
    rig.source["acquisition_reservation"]["key"] = "reservation-b"
    second = first.model_copy(update={"reservation_key": "reservation-b", "delivery_id": "delivery-b"})
    with pytest.raises(RuntimeError, match="after reuse before receipt"):
        await rig.runtime.acquire(second)
    monkeypatch.setattr(rig.runtime.store, "stage", stage)
    current = second
    if new_attempt:
        rig.source["acquisition_reservation"]["key"] = "reservation-c"
        current = first.model_copy(update={"reservation_key": "reservation-c", "delivery_id": "delivery-c"})
    result = await rig.runtime.acquire(current)
    assert result.native_session_id == rig.head["id"]
    assert await rig.runtime.acquire(current) == result
    assert rig.db.execute_read("SELECT COUNT(*) AS n FROM live_messages")[0]["n"] == 1
    assert len([args for args, _ in rig.calls if args[0] == "embody" and "--dry-run" not in args]) == 1
    assert not rig.db.execute_read("SELECT * FROM acquisition_launch_gates")


@pytest.mark.asyncio
async def test_new_reservation_recovers_interrupted_retirement_registration(rig, monkeypatch):
    rig.source["task"]["generation"] = 1
    rig.source["acquisition_reservation"].update(
        worktree_ownership="reused", conversation_retired=True,
    )
    sample = req(allocation="reused", conversation_retired=True, task_generation=1)
    deliver = rig.runtime._deliver
    rig.runtime._deliver = AsyncMock(side_effect=RuntimeError("before delivery"))
    with pytest.raises(RuntimeError):
        await rig.runtime.acquire(sample)
    rig.runtime._deliver = deliver
    register = acquisition_runtime.worktree_command
    async def interrupted(args, project):
        if args[0] == "bind-session":
            raise RuntimeError("registration interrupted")
        return await register(args, project)
    monkeypatch.setattr(acquisition_runtime, "worktree_command", interrupted)
    rig.source["acquisition_reservation"]["key"] = "reservation-b"
    second = sample.model_copy(update={"reservation_key": "reservation-b", "delivery_id": "delivery-b"})
    with pytest.raises(RuntimeError, match="registration interrupted"):
        await rig.runtime.acquire(second)
    monkeypatch.setattr(acquisition_runtime, "worktree_command", register)
    rig.source["acquisition_reservation"]["key"] = "reservation-c"
    third = sample.model_copy(update={"reservation_key": "reservation-c", "delivery_id": "delivery-c"})
    result = await rig.runtime.acquire(third)
    assert len(rig.manager.list_sessions()) == 1
    assert rig.db.execute_read("SELECT COUNT(*) AS n FROM turns")[0]["n"] == 1
    session = rig.manager.get_session(result.session_id)
    if session._prompt_task:
        await session._prompt_task


@pytest.mark.asyncio
async def test_dropped_retired_handle_cannot_reuse_authoritative_native_id(rig):
    first = await rig.runtime.acquire(req())
    session = rig.manager.get_session(first.session_id)
    if session._prompt_task:
        await session._prompt_task
    session.status = SessionStatus.STOPPED
    session.client = None
    rig.db.update_session_status(first.session_id, "stopped", time.time())
    rig.db.release_worktree_ownership(worktree_id="worktree-a")
    rig.source["acquisition_reservation"].update(
        key="reservation-b", worktree_ownership="reused", conversation_retired=True,
    )
    # Deliberately broken host reports the retired native ID for session/new.
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req(
            allocation="reused", conversation_retired=True,
            reservation_key="reservation-b", delivery_id="delivery-b",
        ))
    assert error.value.code == "retired_identity_reused"
    assert rig.db.execute_read("SELECT COUNT(*) AS n FROM turns")[0]["n"] == 1


@pytest.mark.asyncio
async def test_unknown_historic_registration_is_not_a_current_holder(rig):
    rig.db.register_live_session(
        "historic-native", machine="test-machine", cwd="/example", worktree_id="worktree-a",
        repo="test-project", branch="old", pid=None, role="worker", now=0,
    )
    rig.db.execute_write("UPDATE live_sessions SET status='expired'")
    result = await rig.runtime.acquire(req())
    assert result.acquired == "created"
    session = rig.manager.get_session(result.session_id)
    if session._prompt_task:
        await session._prompt_task


def test_launch_gate_blocks_both_legacy_and_duplicate_owned_starts(tmp_db):
    store = AcquisitionStore(tmp_db)
    sample = req()
    store.gate(sample, None, None)
    with pytest.raises(sqlite3.IntegrityError, match="acquisition launch fence"):
        tmp_db.create_session(
            "foreign", "foreign", "worker", "/example", "local", "starting", time.time(),
            target_json=json.dumps({"worktree_id": "worktree-a"}), caller_id="other",
        )
    tmp_db.create_session(
        "owned", "owned", "worker", "/example", "local", "starting", time.time(),
        target_json=json.dumps({"worktree_id": "worktree-a"}), caller_id="acquisition:delivery-a",
    )
    with pytest.raises(sqlite3.IntegrityError, match="acquisition launch fence"):
        tmp_db.create_session(
            "duplicate", "duplicate", "worker", "/example", "local", "starting", time.time(),
            target_json=json.dumps({"worktree_id": "worktree-a"}), caller_id="acquisition:delivery-a",
        )


@pytest.mark.asyncio
async def test_executor_exclusion_prevents_reconciling_an_operation_still_running(rig):
    with executor(rig.db, "worktree-a"):
        with pytest.raises(AcquisitionRefused) as error:
            await rig.runtime.acquire(req())
    assert error.value.code == "executor_live"
    assert not rig.db.execute_read("SELECT * FROM acquisition_attempts")


@pytest.mark.asyncio
async def test_first_targeted_task_creates_without_inheriting_history(rig):
    rig.source["acquisition_reservation"].update(worktree_ownership="targeted", attempt=1)
    output = await rig.runtime.acquire(req(mode="cli", allocation="targeted"))
    assert output.acquired == "created"
    assert await rig.runtime.acquire(req(mode="cli", allocation="targeted")) == output


@pytest.mark.asyncio
async def test_missing_marker_retained_task_never_creates(rig):
    rig.source["acquisition_reservation"].update(worktree_ownership="reused", conversation_retired=None)
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req(allocation="reused", conversation_retired=None))
    assert error.value.code == "retirement_unknown"
    assert not rig.manager.list_sessions()


@pytest.mark.asyncio
async def test_foreign_binding_cannot_be_overridden_by_current_carried_handle(rig):
    output = await rig.runtime.acquire(req(mode="cli"))
    rig.db.execute_write("UPDATE live_messages SET delivered_at=?, outcome='applied'", (time.time(),))
    rig.db.execute_write("UPDATE acquisition_bindings SET task_id='foreign-task'")
    rig.source["acquisition_reservation"].update(
    key="reservation-b", worktree_ownership="reused", session_handle=output.session_id,
    )
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req(
            mode="cli", allocation="reused", reservation_key="reservation-b", delivery_id="delivery-b",
        ))
    assert error.value.code == "continuity_ambiguous"
    assert rig.db.execute_read("SELECT COUNT(*) AS n FROM live_messages")[0]["n"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["cli", "acp"])
async def test_foreign_target_binding_refuses_before_any_resume_or_delivery(rig, mode):
    output = await rig.runtime.acquire(req(mode=mode))
    if mode == "acp":
        session = rig.manager.get_session(output.session_id)
        if session._prompt_task:
            await session._prompt_task
        session.status = SessionStatus.STOPPED
        session.client = None
        rig.db.update_session_status(session.session_id, "stopped", time.time())
        rig.db.release_worktree_ownership(worktree_id="worktree-a")
    else:
        rig.db.execute_write("UPDATE live_messages SET delivered_at=?", (time.time(),))
        rig.db.execute_write("UPDATE live_sessions SET status='expired',updated_at=0")
    rig.db.execute_write("UPDATE acquisition_bindings SET target='different-target'")
    rig.source["acquisition_reservation"].update(key="reservation-b", worktree_ownership="reused")
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req(
            mode=mode, allocation="reused", reservation_key="reservation-b", delivery_id="delivery-b",
        ))
    assert error.value.code == "evidence_scope_mismatch"
    rig.client.load_session.assert_not_awaited()
    assert rig.db.execute_read("SELECT COUNT(*) AS n FROM acquisition_attempts")[0]["n"] == 2


@pytest.mark.asyncio
async def test_unacknowledged_cli_delivery_blocks_a_second_turn(rig):
    await rig.runtime.acquire(req(mode="cli"))
    rig.source["acquisition_reservation"].update(key="reservation-b", worktree_ownership="reused")
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req(
            mode="cli", allocation="reused", reservation_key="reservation-b", delivery_id="delivery-b",
        ))
    assert error.value.code == "session_busy"
    assert rig.db.execute_read("SELECT COUNT(*) AS n FROM live_messages")[0]["n"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("persist_failed_record", [False, True])
async def test_missing_native_after_host_attach_failure_retains_ambiguity(rig, persist_failed_record):
    sample = req()
    token = rig.runtime.store.journal.claim(sample).token
    rig.runtime.store.gate(sample, None, None)
    rig.runtime.store.stage(
        sample, "launching", acquired="created", session_id=None,
        native_session_id=None, retired_native_ids=[], creation_authorized=True,
    )
    if persist_failed_record:
        # Simulate host launch followed by failed client attachment. The host
        # is independently owned and may still be alive despite these fields.
        session = core.Session("failed-attach", "failed", SpawnTarget(
            type="local", cwd="/example", worktree_id="worktree-a",
        ), caller_id="acquisition:delivery-a")
        session.status = SessionStatus.FAILED
        session.client = None
        rig.manager._sessions[session.session_id] = session
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(sample)
    assert error.value.code == "launch_ambiguous"
    rig.source["acquisition_reservation"].update(key="reservation-b", worktree_ownership="reused")
    with pytest.raises(AcquisitionRefused) as error:
        await rig.runtime.acquire(req(
            allocation="reused", reservation_key="reservation-b", delivery_id="delivery-b",
        ))
    assert error.value.code == "prior_launch_ambiguous"
    assert rig.runtime.store.recovery(sample)[0] == token
    assert rig.db.execute_read("SELECT state FROM acquisition_attempts")[0]["state"] == "pending"
    assert rig.db.execute_read("SELECT * FROM acquisition_launch_gates")
    assert not rig.db.execute_read("SELECT * FROM turns")


@pytest.mark.asyncio
async def test_preflight_interrupt_persists_prepared_request_for_next_reservation(rig, monkeypatch):
    async def interrupted(*args):
        raise RuntimeError("preflight interrupted")
    original = rig.runtime._prepare_target
    monkeypatch.setattr(rig.runtime, "_prepare_target", interrupted)
    with pytest.raises(RuntimeError, match="preflight interrupted"):
        await rig.runtime.acquire(req())
    token, stage, facts = rig.runtime.store.recovery(req())
    assert token and stage == "prepared"
    assert AcquisitionRequest.model_validate(facts["_request"]) == req()
    rig.source["acquisition_reservation"]["key"] = "reservation-b"
    monkeypatch.setattr(rig.runtime, "_prepare_target", original)
    result = await rig.runtime.acquire(req(
        reservation_key="reservation-b", delivery_id="delivery-b",
    ))
    assert result.acquired == "created"
    assert rig.db.execute_read(
        "SELECT refusal_code FROM acquisition_attempts WHERE delivery_id='delivery-a'",
    )[0]["refusal_code"] == "interrupted_before_launch"
    session = rig.manager.get_session(result.session_id)
    if session._prompt_task:
        await session._prompt_task


@pytest.mark.asyncio
async def test_launched_receipt_after_restart_strictly_resumes_before_delivery(rig, monkeypatch):
    original = rig.runtime._deliver
    monkeypatch.setattr(rig.runtime, "_deliver", AsyncMock(side_effect=RuntimeError("restart")))
    with pytest.raises(RuntimeError, match="restart"):
        await rig.runtime.acquire(req())
    session = rig.manager.list_sessions()[0]
    native = session.acp_session_id
    session.status = SessionStatus.STOPPED
    session.client = None
    rig.db.update_session_status(session.session_id, "stopped", time.time())
    monkeypatch.setattr(rig.runtime, "_deliver", original)
    resumed = await rig.runtime.acquire(req())
    assert resumed.native_session_id == native
    rig.client.load_session.assert_awaited_once()
    assert rig.client.load_session.await_args.kwargs["session_id"] == native
    assert rig.db.execute_read("SELECT COUNT(*) AS n FROM turns")[0]["n"] == 1
    assert await rig.runtime.acquire(req()) == resumed
    assert rig.db.execute_read("SELECT COUNT(*) AS n FROM turns")[0]["n"] == 1
    if session._prompt_task:
        await session._prompt_task


@pytest.mark.asyncio
@pytest.mark.parametrize("boundary", ["resume", "turn_lock"])
@pytest.mark.parametrize("change", ["generation", "hold", "reservation", "cancel", "head"])
async def test_recovery_revalidates_admission_after_every_lifecycle_wait(
    rig, monkeypatch, boundary, change,
):
    original = rig.runtime._deliver
    monkeypatch.setattr(rig.runtime, "_deliver", AsyncMock(side_effect=RuntimeError("interrupted")))
    with pytest.raises(RuntimeError, match="interrupted"):
        await rig.runtime.acquire(req())
    monkeypatch.setattr(rig.runtime, "_deliver", original)
    session = rig.manager.list_sessions()[0]
    session.status = SessionStatus.STOPPED
    session.client = None
    rig.db.update_session_status(session.session_id, "stopped", time.time())

    def move_authority():
        if change == "generation":
            rig.source["task"]["generation"] += 1
        elif change == "hold":
            rig.source["task"]["hold_at"] = time.time()
        elif change == "reservation":
            rig.source["acquisition_reservation"]["key"] = "replacement-reservation"
        elif change == "cancel":
            rig.source["task"]["status"] = "abandoned"
        else:
            rig.head["id"] = "other-native"

    if boundary == "resume":
        resume = rig.manager.resume_session
        async def racing_resume(*args, **kwargs):
            resumed = await resume(*args, **kwargs)
            move_authority()
            return resumed
        monkeypatch.setattr(rig.manager, "resume_session", racing_resume)
    else:
        lock = asyncio.Lock()
        await lock.acquire()
        waiting = asyncio.Event()
        class RacingLock:
            async def __aenter__(self):
                waiting.set()
                await lock.acquire()
            async def __aexit__(self, *args):
                lock.release()
                return False
        session._turn_start_lock = RacingLock()
    pending = asyncio.create_task(rig.runtime.acquire(req()))
    if boundary == "turn_lock":
        await waiting.wait()
        move_authority()
        lock.release()
    with pytest.raises(AcquisitionRefused) as error:
        await pending
    assert error.value.code == ("head_changed" if change == "head" else "dispatch_fence_changed")
    assert not rig.db.execute_read("SELECT * FROM turns")
    assert rig.runtime.store.recovery(req())[1] == "launched"
