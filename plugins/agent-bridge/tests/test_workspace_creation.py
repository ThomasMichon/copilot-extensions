"""Standalone creation retains one allocation, native session and first turn."""

import asyncio
import os
import sys
import subprocess
import time
import sqlite3
from dataclasses import replace
from dataclasses import asdict
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs

from agent_bridge import workspace_creation, acquisition_authority
from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.models import SessionStatus
from agent_bridge.session_manager import Session
from agent_bridge.transport import SpawnTarget
from test_acquisition_runtime import rig as _connected_rig

rig = _connected_rig

pytestmark = pytest.mark.contract("agent_bridge.workspace_creation")


@pytest.fixture
def creation_rig(rig, monkeypatch, tmp_path):
    row = {
        "id": "worktree-a", "repo": "test-project", "path": str(tmp_path),
        "creation_nonce": "original-allocation", "session_count": 0, "status": "active",
    }

    async def command(args, project):
        rig.calls.append((args, project))
        assert project == "test-project"
        if args[0] == "create":
            return {"worktree": dict(row), "creation": {"version": 1, "status": "complete"}}
        if args[0] == "list":
            return {"worktrees": [dict(row)]}
        if args[0] == "head-session":
            return {
                "tracked": True, "head_session": rig.head["id"],
                "active": bool(rig.head["id"]), "state": "active" if rig.head["id"] else None,
            }
        assert args[0] == "bind-session"
        rig.head["id"] = args[args.index("--session-id") + 1]
        row["session_count"] = 1
        return {"ok": True}

    monkeypatch.setattr(workspace_creation, "worktree_command", command)
    rig.row = row
    rig.target = SpawnTarget(type="local", project="test-project")
    return rig


@pytest.mark.asyncio
async def test_exact_lost_response_replay_keeps_workspace_session_and_turn(creation_rig):
    rig = creation_rig
    first, result = await workspace_creation.start(
        rig.manager, rig.target, key="logical-work", prompt="Do the work", agent_name="worker",
    )
    second, replay = await workspace_creation.start(
        rig.manager, rig.target, key="logical-work", prompt="Do the work", agent_name="worker",
    )
    assert first is second
    assert result["native_session_id"] == replay["native_session_id"] == rig.head["id"]
    assert result["turn_index"] == replay["turn_index"] == 0
    assert replay["replayed"]
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1
    assert sum(args[0] == "bind-session" for args, _ in rig.calls) == 1
    assert len(rig.manager.list_sessions()) == 1
    assert len(rig.db.execute_read("SELECT * FROM turns")) == 1
    with pytest.raises(AcquisitionRefused, match="different embodiment facts"):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work", prompt="Changed", agent_name="worker")
    await asyncio.sleep(0)

@pytest.mark.asyncio
async def test_drain_after_owner_revalidation_restores_allocated_creation_for_same_key(creation_rig, monkeypatch):
    rig = creation_rig
    command = workspace_creation.worktree_command
    head_reads = 0

    async def drain_during_owner_read(args, project):
        nonlocal head_reads
        result = await command(args, project)
        if args[0] == "head-session":
            head_reads += 1
            if head_reads == 2:
                rig.manager._draining = True
        return result

    monkeypatch.setattr(workspace_creation, "worktree_command", drain_during_owner_read)
    with pytest.raises(AcquisitionRefused) as refused:
        await workspace_creation.start(rig.manager, rig.target, key="logical-work", prompt="Do the work")
    assert refused.value.code == "creation_draining" and refused.value.effect == "not_started"
    saved = rig.db.execute_read("SELECT state,session_id FROM workspace_embodiments")[0]
    assert saved["state"] == "allocated" and saved["session_id"]
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT 1 FROM acquisition_launch_gates")
    rig.manager._draining = False
    session, receipt = await workspace_creation.start(rig.manager, rig.target, key="logical-work", prompt="Do the work")
    assert session.session_id == saved["session_id"] and receipt["status"] == "complete"
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1
    assert len(rig.db.execute_read("SELECT * FROM turns")) == 1
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("holder", ["manager", "ownership"])
async def test_holder_during_owner_revalidation_prevents_keyed_native_start(creation_rig, monkeypatch, holder):
    rig = creation_rig
    command = workspace_creation.worktree_command
    head_reads = 0

    async def claim_during_owner_read(args, project):
        nonlocal head_reads
        result = await command(args, project)
        if args[0] == "head-session":
            head_reads += 1
            if head_reads == 2:
                if holder == "manager":
                    target = replace(
                        rig.target, worktree_id="worktree-a", cwd=rig.row["path"], explicit_cwd=True,
                    )
                    session = Session("ordinary-session", "ordinary", target)
                    session.status = SessionStatus.STARTING
                    rig.manager._sessions[session.session_id] = session
                else:
                    assert rig.db.reserve_worktree_ownership("worktree-a", "ordinary-session", now=time.time())
        return result

    monkeypatch.setattr(workspace_creation, "worktree_command", claim_during_owner_read)
    with pytest.raises(AcquisitionRefused) as refused:
        await workspace_creation.start(rig.manager, rig.target, key="logical-work", prompt="Do the work")
    assert refused.value.code == "live_holder"
    operation = rig.db.execute_read("SELECT state,session_id FROM workspace_embodiments")[0]
    assert operation["state"] == "allocated"
    assert rig.manager.get_session(operation["session_id"]) is None
    assert len(rig.manager.list_sessions()) == (1 if holder == "manager" else 0)
    assert not rig.db.execute_read("SELECT 1 FROM acquisition_launch_gates")
    assert not rig.db.execute_read("SELECT * FROM turns")
    assert not rig.head["id"]
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1
    assert not any(args[0] == "bind-session" for args, _ in rig.calls)
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("holder", ["manager", "live_session"])
async def test_launch_gate_fences_ordinary_registration_during_owner_read(creation_rig, monkeypatch, holder):
    rig = creation_rig
    command = workspace_creation.worktree_command
    head_reads = 0

    async def register_during_owner_read(args, project):
        nonlocal head_reads
        result = await command(args, project)
        if args[0] == "head-session":
            head_reads += 1
            if head_reads == 2:
                with pytest.raises(sqlite3.IntegrityError, match="acquisition launch fence held"):
                    if holder == "manager":
                        await rig.manager.start_session(replace(
                            rig.target, worktree_id="worktree-a", cwd=rig.row["path"], explicit_cwd=True,
                        ))
                    else:
                        rig.db.register_live_session(
                            "ordinary-native", machine="test-machine", cwd=rig.row["path"],
                            worktree_id="worktree-a", repo="test-project", branch="test",
                            pid=os.getpid(), role="worker", now=time.time(),
                        )
        return result

    monkeypatch.setattr(workspace_creation, "worktree_command", register_during_owner_read)
    session, receipt = await workspace_creation.start(
        rig.manager, rig.target, key="logical-work", prompt="Do the work",
    )
    assert receipt["native_session_id"] == session.acp_session_id == rig.head["id"]
    assert rig.manager.list_sessions() == [session]
    assert len(rig.db.execute_read("SELECT * FROM sessions")) == 1
    assert not rig.db.list_fresh_live_sessions("worktree-a", now=time.time())
    assert len(rig.db.execute_read("SELECT * FROM turns")) == 1
    assert not rig.db.execute_read("SELECT 1 FROM acquisition_launch_gates")
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_completed_creation_with_missing_turn_evidence_never_resubmits(creation_rig):
    rig = creation_rig
    _, _ = await workspace_creation.start(rig.manager, rig.target, key="logical-work", prompt="Do the work")
    rig.db.execute_write("DELETE FROM turns")
    with pytest.raises(AcquisitionRefused, match="receipt is missing"):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work", prompt="Do the work")
    assert len(rig.manager.list_sessions()) == 1
    assert not rig.db.execute_read("SELECT * FROM turns")
    await asyncio.sleep(0)

@pytest.mark.asyncio
@pytest.mark.parametrize("foreign_gate", [False, True])
async def test_completed_replay_releases_only_its_stranded_launch_gate(creation_rig, monkeypatch, foreign_gate):
    rig = creation_rig
    release = workspace_creation.AcquisitionStore.release_workspace_gate

    def interrupted(*_args, **_kwargs):
        raise RuntimeError("after completed receipt, before launch-gate release")

    monkeypatch.setattr(workspace_creation.AcquisitionStore, "release_workspace_gate", interrupted)
    with pytest.raises(RuntimeError, match="completed receipt"):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work", prompt="Do the work")
    assert rig.db.execute_read("SELECT state FROM workspace_embodiments")[0]["state"] == "complete"
    assert rig.db.execute_read("SELECT 1 FROM acquisition_launch_gates")
    if foreign_gate:
        rig.db.execute_write("UPDATE acquisition_launch_gates SET delivery_id='another-request'")
    monkeypatch.setattr(workspace_creation.AcquisitionStore, "release_workspace_gate", release)
    _, replay = await workspace_creation.start(rig.manager, rig.target, key="logical-work", prompt="Do the work")
    assert replay["replayed"]
    gates = rig.db.execute_read("SELECT delivery_id FROM acquisition_launch_gates")
    assert bool(gates) is foreign_gate
    if foreign_gate:
        assert gates[0]["delivery_id"] == "another-request"
    assert len(rig.db.execute_read("SELECT * FROM turns")) == 1
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1
    assert sum(args[0] == "bind-session" for args, _ in rig.calls) == 1
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_cold_pending_first_prompt_strictly_resumes_original_native(creation_rig, monkeypatch):
    rig = creation_rig
    original = rig.manager._submit_prompt_locked

    async def interrupted(*_args, **_kwargs):
        raise RuntimeError("before native prompt admission")

    monkeypatch.setattr(rig.manager, "_submit_prompt_locked", interrupted)
    with pytest.raises(RuntimeError):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work", prompt="Do the work")
    session = rig.manager.list_sessions()[0]
    native = session.acp_session_id
    session.status = SessionStatus.STOPPED
    session.client = None
    monkeypatch.setattr(rig.manager, "_submit_prompt_locked", original)
    resume = rig.manager.resume_session
    calls = []

    async def strict(*args, **kwargs):
        calls.append(kwargs)
        return await resume(*args, **kwargs)

    monkeypatch.setattr(rig.manager, "resume_session", strict)
    recovered, result = await workspace_creation.start(
        rig.manager, rig.target, key="logical-work", prompt="Do the work",
    )
    assert recovered is session and result["native_session_id"] == native
    assert calls == [{"drain": False, "allow_recreate": False}]
    assert len(rig.manager.list_sessions()) == len(rig.db.execute_read("SELECT * FROM turns")) == 1
    await asyncio.sleep(0)

@pytest.mark.asyncio
async def test_cold_unprompted_creation_replay_restores_same_ready_native(creation_rig, monkeypatch):
    rig = creation_rig
    session, first = await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    native = session.acp_session_id
    session.status = SessionStatus.STOPPED
    session.client = None
    calls = []
    resume = rig.manager.resume_session

    async def strict(*args, **kwargs):
        calls.append(kwargs)
        return await resume(*args, **kwargs)

    monkeypatch.setattr(rig.manager, "resume_session", strict)
    recovered, replay = await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    assert recovered is session and recovered.status == SessionStatus.IDLE
    assert replay["replayed"] and replay["native_session_id"] == first["native_session_id"] == native
    assert calls == [{"drain": False, "allow_recreate": False}]
    assert not rig.db.execute_read("SELECT * FROM turns")
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_existing_sessionless_allocation_is_embodied_without_create(creation_rig):
    rig = creation_rig
    session, result = await workspace_creation.start(
        rig.manager, replace(rig.target, worktree_id="worktree-a"), key="logical-work", agent_name="worker",
    )
    assert result["worktree"]["id"] == session.target.worktree_id == "worktree-a"
    assert session.target.cwd == rig.row["path"]
    assert not any(args[0] == "create" for args, _ in rig.calls)


@pytest.mark.asyncio
async def test_caller_session_changes_do_not_redefine_the_owner_worktree(creation_rig):
    rig = creation_rig
    first, _ = await workspace_creation.start(
        rig.manager, rig.target, key="logical-work",
        caller_owner_ref="machine/controller/parent-worktree#session-one",
    )
    replay, _ = await workspace_creation.start(
        rig.manager, rig.target, key="logical-work",
        caller_owner_ref="machine/controller/parent-worktree#session-two",
    )
    assert replay is first
    with pytest.raises(AcquisitionRefused, match="different embodiment facts"):
        await workspace_creation.start(
            rig.manager, rig.target, key="logical-work",
            caller_owner_ref="machine/controller/other-worktree#session-two",
        )
    assert len(rig.manager.list_sessions()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("field,value", [
    ("creation_nonce", None), ("session_count", 1), ("execution_leg_live", True),
])
async def test_legacy_or_occupied_allocation_cannot_start_another_conversation(creation_rig, field, value):
    rig = creation_rig
    rig.row[field] = value
    with pytest.raises(AcquisitionRefused):
        await workspace_creation.start(
            rig.manager, replace(rig.target, worktree_id="worktree-a"), key="logical-work",
        )
    assert not rig.manager.list_sessions()


@pytest.mark.asyncio
async def test_interrupted_native_start_recovers_only_its_recorded_session(creation_rig, monkeypatch):
    rig = creation_rig
    original = rig.manager.start_session

    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("response lost")

    monkeypatch.setattr(rig.manager, "start_session", interrupted)
    with pytest.raises(RuntimeError, match="response lost"):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    session, result = await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    assert result["native_session_id"] == session.acp_session_id
    assert len(rig.manager.list_sessions()) == 1
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1


@pytest.mark.asyncio
async def test_interrupted_unproven_start_never_starts_again(creation_rig, monkeypatch):
    rig = creation_rig
    calls = []

    async def interrupted(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("outcome unknown")

    monkeypatch.setattr(rig.manager, "start_session", interrupted)
    with pytest.raises(RuntimeError):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    with pytest.raises(AcquisitionRefused, match="reconciliation"):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    assert len(calls) == 1
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1


@pytest.mark.asyncio
async def test_concurrent_creations_start_once(creation_rig):
    rig = creation_rig
    results = await asyncio.gather(*(
        workspace_creation.start(rig.manager, rig.target, key="logical-work") for _ in range(2)
    ), return_exceptions=True)
    assert sum(isinstance(result, tuple) for result in results) >= 1
    assert all(isinstance(result, (tuple, AcquisitionRefused)) for result in results)
    assert len(rig.manager.list_sessions()) == 1
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1


@pytest.mark.asyncio
async def test_retired_native_result_is_terminal_not_fresh_creation(creation_rig):
    rig = creation_rig
    session, _ = await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    session.status = SessionStatus.ENDED
    with pytest.raises(AcquisitionRefused):
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    assert len(rig.manager.list_sessions()) == 1
    assert sum(args[0] == "create" for args, _ in rig.calls) == 1


@pytest.mark.asyncio
async def test_unsupported_venue_refuses_before_effects(creation_rig):
    rig = creation_rig
    with pytest.raises(AcquisitionRefused) as failure:
        await workspace_creation.start(rig.manager, replace(rig.target, type="ssh"), key="logical-work")
    assert failure.value.effect == "not_started"
    assert not rig.calls and not rig.manager.list_sessions()


def test_client_gates_old_protocol_without_sending_creation(monkeypatch):
    client = BridgeClient(base_url="http://127.0.0.1:1", token="test")
    monkeypatch.setattr(client, "daemon_supports", lambda _floor: False)
    monkeypatch.setattr(client, "_request", lambda *_a, **_k: pytest.fail("must not send"))
    with pytest.raises(BridgeClientError) as failure:
        client.start_session(agent="worker", creation_key="logical-work")
    assert failure.value.status == 426


def test_client_uses_nonfallback_keyed_endpoint(monkeypatch):
    client = BridgeClient(base_url="http://127.0.0.1:1", token="test")
    monkeypatch.setattr(client, "daemon_supports", lambda _floor: True)
    calls = []
    monkeypatch.setattr(client, "_request", lambda *a, **_k: calls.append(a) or {})
    client.start_session(agent="worker", creation_key="logical-work", creation_prompt="Do the work")
    assert calls[0][1] == "/api/v1/sessions/workspace-creations"
    assert calls[0][2]["creation_key"] == "logical-work"
    assert calls[0][2]["creation_prompt"] == "Do the work"


def test_keyed_http_endpoint_replays_one_session(creation_rig):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_bridge.routes.sessions import router

    rig = creation_rig
    app = FastAPI()
    app.state.session_manager = rig.manager

    async def resolve(_name, **_kwargs):
        return replace(rig.target)

    app.state.resolver = SimpleNamespace(resolve_async=resolve)
    app.include_router(router)
    with TestClient(app) as client:
        missing = client.post("/api/v1/sessions/workspace-creations", json={"agent": "worker"})
        assert missing.status_code == 422
        assert not rig.manager.list_sessions()
        first = client.post("/api/v1/sessions/workspace-creations", json={
            "agent": "worker", "creation_key": "logical-work",
        })
        replay = client.post("/api/v1/sessions/workspace-creations", json={
            "agent": "worker", "creation_key": "logical-work",
        })
    assert first.status_code == replay.status_code == 200, (first.text, replay.text)
    assert first.json()["session_id"] == replay.json()["session_id"]
    assert replay.json()["creation"]["replayed"] is True
    assert len(rig.manager.list_sessions()) == 1
    assert rig.manager._active_acquisitions == 0


def test_cli_streams_admitted_creation_prompt_without_resubmitting(monkeypatch, capsys):
    from types import SimpleNamespace
    from agent_bridge import session_targeting_cli

    core = session_targeting_cli._core()
    monkeypatch.setattr(core, "_connection_identity", lambda *_args: {})
    client = SimpleNamespace(submit_prompt=lambda *_a, **_kw: pytest.fail("must not resubmit"))
    session_targeting_cli._submit_and_stream(
        client, SimpleNamespace(json=True), "original-session", "Do the work", caller_id=None,
        submitted_result={"session_id": "original-session", "turn_index": 0, "queued": False},
    )
    assert "original-session" in capsys.readouterr().out


@pytest.fixture
def native_owner(monkeypatch, tmp_path):
    root = tmp_path / "owner"
    anchor = root / "anchor"
    anchor.mkdir(parents=True)
    for args in (
        ["init", "-q", "--initial-branch=main"],
        ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
         "commit", "--allow-empty", "-qm", "fixture"],
        ["remote", "add", "origin", str(anchor)],
    ):
        subprocess.run(
            ["git", "-C", str(anchor), *args], check=True, capture_output=True,
            text=True, timeout=15, **no_window_kwargs(),
        )
    fixture = Path(__file__).resolve().parents[2] / "agent-worktrees" / "tests" / "workspace_owner_fixture.py"
    monkeypatch.setattr(acquisition_authority, "_agent_worktrees_launch_prefix",
        lambda: [sys.executable, str(fixture), str(root)])
    return root, _native_owner_command


@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_CREATION_NATIVE") != "1",
    reason="opt-in connected fresh-owner subprocess boundary",
)
@pytest.mark.timeout(120)
@pytest.mark.asyncio
async def test_native_ground_and_bridge_creation_share_one_fresh_workspace(
    rig, monkeypatch, native_owner,
):
    root, command = native_owner
    # Use the real owner adapter, not the rig's simulated owner responses.
    monkeypatch.setattr(workspace_creation, "worktree_command", command)
    target = SpawnTarget(type="local", project="fixture")
    ground = await command([
        "create", "--creation-key", "logical-work", "--no-owner",
        "--interface", "acp", "--origin", "delegate", "--json",
    ], "fixture")
    assert ground["worktree"]["session_count"] == 0
    first, result = await workspace_creation.start(
        rig.manager, target, key="logical-work", prompt="Do the work",
    )
    replay, receipt = await workspace_creation.start(
        rig.manager, target, key="logical-work", prompt="Do the work",
    )
    assert first is replay
    assert result["worktree"]["id"] == receipt["worktree"]["id"] == ground["worktree"]["id"]
    assert first.target.project == "fixture" and first.target.cwd == ground["worktree"]["path"]
    assert len(list((root / "worktrees").iterdir())) == 1
    assert len(list((root / "state" / "worktrees").glob("*.yaml"))) == 1
    assert len(rig.manager.list_sessions()) == len(rig.db.execute_read("SELECT * FROM turns")) == 1
    head = await command(["head-session", "--worktree-id", first.target.worktree_id, "--json"], "fixture")
    assert head["head_session"] == result["native_session_id"]
    await asyncio.sleep(0)


@pytest.mark.skipif(
    os.environ.get("AGENT_WORKTREES_CREATION_NATIVE") != "1",
    reason="opt-in connected queue/allocation/bridge subprocess boundary",
)
@pytest.mark.timeout(120)
@pytest.mark.asyncio
@pytest.mark.parametrize("new_generation", [False, True])
async def test_native_dispatch_lost_allocation_response_retains_execution_owner(
    rig, monkeypatch, native_owner, new_generation,
):
    from types import SimpleNamespace
    from agent_bridge import acquisition_runtime
    from agent_bridge.acquisition_contract import AcquisitionRequest

    repository = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(repository / "plugins" / "agent-dispatch" / "src"))
    from agent_dispatch import embody
    from agent_dispatch.queue import TaskQueue
    from agent_dispatch.workspace_binding import record_prepared

    root, command = native_owner
    queue = TaskQueue(root / "queue.db")
    task = queue.create(title="work", repo="queue-repo", dedup_key="logical-work")
    client = SimpleNamespace(
        record_spawn_allocation=lambda key, project: asdict(queue.record_spawn_allocation(key, project)),
        record_spawn_worktree=lambda key, worktree, **kwargs:
            asdict(queue.record_spawn_worktree(key, worktree, **kwargs)),
    )
    prefix = acquisition_authority._agent_worktrees_launch_prefix()
    monkeypatch.setattr(embody, "_agent_worktrees_launch_prefix", lambda: prefix)
    run = embody.subprocess.run
    attempts = []

    def lost_response(argv, **kwargs):
        result = run(argv, **kwargs)
        attempts.append(argv)
        if len(attempts) == 1:
            assert result.returncode == 0, result.stderr
            raise subprocess.TimeoutExpired(argv, 1)
        return result

    monkeypatch.setattr(embody.subprocess, "run", lost_response)
    first, _ = queue.reserve_spawn(task.id)
    with pytest.raises(subprocess.TimeoutExpired):
        embody.prepare_reusable_worktree(
            asdict(task), asdict(first), project="fixture", interface="acp",
            driver="agent-dispatch", supervisor="first", allocation_client=client,
        )
    queue.fail_spawn(first.key, detail="owner response lost")
    original_task = task
    if new_generation:
        queue.claim_one("first-worker", task_id=task.id)
        queue.start(task.id, "first-worker")
        queue.complete(task.id, "first-worker")
        task = queue.create(title="next generation", repo="queue-repo", dedup_key="logical-work")
        assert task.id != original_task.id
    restarted = TaskQueue(root / "queue.db")
    retry, _ = restarted.reserve_spawn(task.id)
    prepared = embody.prepare_reusable_worktree(
        asdict(task), asdict(retry), project="wrong-routing", interface="acp",
        driver="agent-dispatch", supervisor="second", allocation_client=client,
    )
    record_prepared(client, asdict(retry), prepared, driver="agent-dispatch", creating_host="fixture")
    recorded = restarted.get_reservation(retry.key)
    assert recorded.creation_key == first.creation_key
    assert recorded.allocation_project == "fixture"
    assert recorded.allocation_reservation_key == first.key
    assert all(argv[argv.index("--dispatch-task-id") + 1] == original_task.id for argv in attempts)
    monkeypatch.setattr(acquisition_authority, "worktree_command", command)
    monkeypatch.setattr(acquisition_runtime, "worktree_command", command)

    async def resolve(name):
        if name == f"dispatch:{task.id}":
            return SpawnTarget(type="session", worktree_id=recorded.worktree, venue={
                "task": asdict(restarted.get(task.id)),
                "attachments": [], "acquisition_reservation": asdict(recorded),
            })
        assert name == "task-worker"
        return SpawnTarget(type="local", project="fixture")

    rig.resolver.resolve_async = resolve
    request = AcquisitionRequest(
        version=2, target="task-worker", mode="acp", task_id=task.id, task_generation=0,
        worktree_id=recorded.worktree, allocation="created", conversation_retired=False,
        reservation_key=retry.key, delivery_id=f"{retry.key}:acquire",
        prompt="Do the work", project=recorded.allocation_project, worktree_path=recorded.worktree_path,
    )
    result = await rig.runtime.acquire(request)
    assert await rig.runtime.acquire(request) == result
    assert result.worktree_id == prepared["worktree"]
    assert len(list((root / "worktrees").iterdir())) == 1
    assert len(rig.manager.list_sessions()) == len(rig.db.execute_read("SELECT * FROM turns")) == 1
    assert attempts[0][attempts[0].index("--dispatch-reservation-key") + 1] == first.key
    assert attempts[1][attempts[1].index("--dispatch-reservation-key") + 1] == first.key
    await asyncio.sleep(0)


_native_owner_command = acquisition_authority.worktree_command
@pytest.mark.parametrize("status,turn_count,ready", [
    ("idle", 0, True), ("idle", 1, False), ("running", 0, False),
])
def test_keyed_cli_no_prompt_preserves_ordinary_creation_receipt(monkeypatch, capsys, status, turn_count, ready):
    import json
    from types import SimpleNamespace
    from agent_bridge import __main__ as core, session_start_cli, session_targeting_cli, workspace_creation_cli

    calls = []
    client = SimpleNamespace(
        start_session=lambda **kw: calls.append(kw) or {
            "session_id": "bridge-a", "status": status,
            "creation": {"status": "complete", "native_session_id": "native-a",
                         "worktree": {"id": "worktree-a", "path": "synthetic-path"}},
        },
        get_session=lambda _sid: {"status": status, "turn_count": turn_count},
    )
    monkeypatch.setattr(session_start_cli, "_read_agent_inventory", lambda _client: {})
    monkeypatch.setattr(session_start_cli, "_prepare_sender_preference", lambda *_a, **_kw: SimpleNamespace(project="example"))
    monkeypatch.setattr(session_targeting_cli, "_hold_protocol_floor", lambda *_a: None)
    monkeypatch.setattr(core, "_get_caller_session_id", lambda: None)
    monkeypatch.setattr(core, "_worktrees_get", lambda _field: None)
    monkeypatch.setattr(core, "_connection_identity", lambda *_a: {"repo": "example", "worktree_id": "worktree-a"})
    workspace_creation_cli.create(
        SimpleNamespace(target="example@local", creation_key="logical-work", json=True),
        client, caller_id="caller",
    )
    receipt = json.loads(capsys.readouterr().out)
    assert calls[0]["creation_key"] == "logical-work" and calls[0]["creation_prompt"] is None
    assert receipt["connection"]["native_session_id"] == "native-a"
    assert receipt["connection"]["target_dir"] == "synthetic-path"
    assert receipt["ready"] is ready
    assert receipt["status"] == ("awaiting_first_prompt" if ready else status)
