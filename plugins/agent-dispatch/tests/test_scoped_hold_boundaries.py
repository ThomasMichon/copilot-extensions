"""Regression cases for allocator identity, retained sends and bounded queue visibility."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from agent_dispatch import bridge, ordinary_bridge
from agent_dispatch.action_controls import require_worker_admission
from agent_dispatch.client_errors import DispatchError
from agent_dispatch.coordinator import create_app
from agent_dispatch.ordinary_agent_storage import reservation_native_facts
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_execution_custody import PoolLaneUnavailable
from agent_dispatch.queue_pool_recovery import prepare_recovery_wake
from agent_dispatch.queue_records import SpawnReservation, WorkerAdmissionInhibited
from agent_dispatch.supervisor import Supervisor
from tests import test_native_owner_wake as native
from tests import test_ordinary_invocation as ordinary
from tests import test_pool_placement_client as controls
from tests.test_pool_placement import pool, publish, task
from tests.test_pool_suspended_recovery import suspended
from tests.test_scoped_worker_holds import command

q = controls.q
api = controls.api
hosted = native.hosted
intent = ordinary.intent


def test_carried_ordinary_retry_matches_frozen_agent(
    intent: tuple[TaskQueue, Task, SpawnReservation, dict[str, Any], str, dict[str, Any]],
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original, row, first, info, _, invocation = intent
    named = invocation["selection"]["agent"]
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path / "policy"))
    ordinary_bridge.record_created_agent(original, first.key, task_generation=row.generation,
                                         agent_info=info, startup_invocation=invocation)
    original.defer_spawn(first.key)
    queue = TaskQueue(original.db_path, worker_admission=require_worker_admission)
    prior = queue.get_reservation(first.key)
    before = queue.get(row.id)
    command("hold", "--agent", "another-worker", "--reason", "unrelated")
    command("hold", "--agent", named, "--reason", "pause carried worker")
    with pytest.raises(WorkerAdmissionInhibited):
        queue.reserve_spawn(row.id, driver="ordinary-bridge")
    assert queue.get(row.id) == before and list(queue.list_reservations(task_id=row.id)) == [prior]
    command("resume", "--agent", named)
    retry, admitted = queue.reserve_spawn(row.id, driver="ordinary-bridge")
    assert admitted and prior and retry.key != first.key and retry.agent_info == prior.agent_info
    assert (retry.creation_key, retry.session_handle, retry.worktree) == (prior.creation_key, prior.session_handle, prior.worktree)
    assert queue.get_reservation(first.key) == prior


def test_fresh_placement_checks_allocator_as_well_as_winner(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    queue = TaskQueue(tmp_path / "queue.db", worker_admission=require_worker_admission)
    revision = publish(queue, pool("selected"))
    row = task(queue)
    for owner in ("allocator", "selected"):
        command("hold", "--pool", owner, "--reason", "stop")
        with pytest.raises(WorkerAdmissionInhibited):
            queue.place_pool_task(row.id, reserved_by="allocator", catalog_revision=revision)
        assert queue.latest_reservation(row.id) is None and queue.get(row.id) == row
        command("resume", "--pool", owner)
    assert queue.place_pool_task(row.id, reserved_by="allocator", catalog_revision=revision).reserved


def test_omitted_retry_driver_cannot_escape_inherited_cli_hold(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    queue = TaskQueue(tmp_path / "queue.db", worker_admission=require_worker_admission)
    row = task(queue)
    first, _ = queue.reserve_spawn(row.id, reserved_by="allocator", driver="cli")
    queue.record_spawn_worktree(first.key, "original", ownership="created", creating_host="host-a", driver="cli")
    queue.fail_spawn(first.key, detail="startup failed")
    history: tuple[SpawnReservation, ...] = tuple(queue.list_reservations(task_id=row.id))
    before = queue.get(row.id)
    command("hold", "--agent", "cli", "--reason", "stop")
    with pytest.raises(WorkerAdmissionInhibited):
        queue.reserve_spawn(row.id, reserved_by="allocator")
    assert queue.get(row.id) == before and tuple(queue.list_reservations(task_id=row.id)) == history
    command("resume", "--agent", "cli")
    retry, reserved = queue.reserve_spawn(row.id, reserved_by="allocator")
    assert reserved and retry.driver == "cli" and retry.worktree == "original" and retry.attempt == 2


@pytest.mark.parametrize("global_hold", [False, True])
def test_direct_bridge_continuation_is_not_pool_resume_evidence(
    hosted: tuple[TaskQueue, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, global_hold: bool,
) -> None:
    queue, task_id = hosted
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    reservation = queue.latest_reservation(task_id)
    assert reservation and reservation.reserved_by and reservation.session_handle == f"local-body:{native.BRIDGE}"
    queue.record_cold(reservation.key)
    queue.submit_steer(task_id, fields={"continue": True}, sender="producer")
    before: tuple[Task | None, tuple[SpawnReservation, ...]] = (
        queue.get(task_id), tuple(queue.list_reservations(task_id=task_id)),
    )
    if global_hold:
        command("inhibit", "worker-admission", "--reason", "no pool starts or resumes")
    else:
        command("hold", "--pool", reservation.reserved_by, "--reason", "no pool starts or resumes")
    direct_calls: list[list[str]] = []
    def fake_transport(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        direct_calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "", "")
    monkeypatch.setattr(bridge, "run_startup", fake_transport)
    assert bridge.resume_session(native.BRIDGE, "separately authorized continuation")
    assert len(direct_calls) == 1 and native.BRIDGE in direct_calls[0] and "--require-agent-hosting" in direct_calls[0]
    with TestClient(create_app(queue, token="data", control_token="control", enable_mcp=False,
                               verification_interval=0)) as api, controls.connected(api, []) as client:
        lane = Supervisor(client, supervisor_id=reservation.reserved_by, repo="queue-project",
                          spawn_fn=lambda _: pytest.fail("no replacement"), local_body_verdict_fn=lambda _: "live",
                          local_acp_session_fn=lambda _: native.NATIVE, local_body_target_dir_fn=lambda _: str(tmp_path),
                          local_resume_fn=lambda *_: pytest.fail("pool-initiated resume while held"))
        assert lane.release_resumed_cold_tasks() == 0
        assert lane.recover_stranded_cold_reservations() == 0
    assert (queue.get(task_id), tuple(queue.list_reservations(task_id=task_id))) == before


def test_held_pool_keeps_started_worker_but_cannot_resume_an_idle_turn(
    hosted: tuple[TaskQueue, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue, task_id = hosted
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    running = queue.resume(task_id, native.OWNER, reuse_session=True)
    original = queue.latest_reservation(task_id)
    assert running.status == "started" and original and original.reserved_by
    command("hold", "--pool", original.reserved_by, "--reason", "stop")
    with TestClient(create_app(queue, token="data", control_token="control", enable_mcp=False,
                               verification_interval=0)) as api, controls.connected(api, []) as client:
        lane = Supervisor(client, supervisor_id=original.reserved_by, repo="queue-project",
                          spawn_fn=lambda _: pytest.fail("no replacement"), local_body_activity_fn=lambda _: "IDLE",
                          idle_nudge_fn=lambda *_: pytest.fail("no pool-initiated work-bearing resume"))
        assert lane.nudge_idle_headless_tasks() == 0
    queue.heartbeat(task_id, native.OWNER)
    active = queue.get(task_id)
    assert active and (active.status, active.owner_session_id, active.generation) == ("started", native.NATIVE, running.generation)
    assert queue.latest_reservation(task_id) == original


@pytest.mark.parametrize("cold", [True, False])
def test_recovery_send_rechecks_bound_pool_after_request(
    q: TaskQueue, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cold: bool,
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    task_id, key, _ = suspended(q)
    monkeypatch.setattr(q, "_spawn_admission", require_worker_admission.require_spawn)
    if cold:
        q.record_cold(key)
    q.resume(task_id, "host-a/worktree", wake_requested=True, now=103)
    wake = q.claim_due_wake(now=104)
    assert wake
    before = q.get(task_id), q.get_reservation(key), q.pool_startup(task_id)
    command("hold", "--pool", "worker", "--reason", "stop resume")
    with pytest.raises(PoolLaneUnavailable):
        if cold:
            prepare_recovery_wake(q, wake)
        else:
            q.admit_execution_wake(wake, provenance=None, now=104)
    assert (q.get(task_id), q.get_reservation(key), q.pool_startup(task_id)) == before
    command("resume", "--pool", "worker")
    prepare_recovery_wake(q, wake)
    assert q.admit_execution_wake(wake, provenance=None, now=104).send_required


@pytest.mark.parametrize("scope", [("--pool", "bound"), ("--instance", "bound", "host-a", "linux")])
def test_adopted_pool_binding_gates_send_not_observation(
    hosted: tuple[TaskQueue, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: tuple[str, ...],
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    queue, task_id = hosted
    queue.resume(task_id, native.OWNER, reuse_session=True)
    queue.yield_task(task_id, native.OWNER, release_spawn=False)
    queue = TaskQueue(queue.db_path, worker_admission=require_worker_admission)
    original = queue.latest_reservation(task_id)
    assert original and original.reserved_by != "bound"
    with queue._connect() as conn:
        conn.execute("INSERT INTO pool_reservation_bindings(reservation_key,registration_id,machine,env) "
                     "VALUES(?,?,?,?)", (original.key, "bound", "host-a", "linux"))
    assert queue.task_pool_assignment(task_id) is None

    def record(*, observe: bool = False, complete: bool = False) -> None:
        facts = reservation_native_facts(queue, original.key)["ordinary_redrive"]
        queue.record_spawn(original.key, session_handle=original.session_handle, worktree=original.worktree,
                           startup_complete=complete, startup_revision=facts["startup_revision"],
                           ordinary_redrive={**facts, **({"observe_only": True} if observe else {})})

    before = queue.get(task_id), queue.list_reservations(task_id=task_id)
    command("hold", *scope, "--reason", "stop")
    with pytest.raises(WorkerAdmissionInhibited):
        record()
    assert (queue.get(task_id), queue.list_reservations(task_id=task_id)) == before
    command("resume", *scope)
    record()
    pending = queue.latest_reservation(task_id)
    command("hold", *scope, "--reason", "receipt observation only")
    with pytest.raises(WorkerAdmissionInhibited):
        record()
    record(observe=True)
    assert pending and not pending.startup_complete and queue.latest_reservation(task_id) == pending
    record(complete=True)
    settled = queue.latest_reservation(task_id)
    assert settled and settled.startup_complete and settled.startup_revision == pending.startup_revision
    assert settled.key == original.key and settled.agent_info == pending.agent_info and queue.get(task_id) == before[0]


@pytest.mark.parametrize("lane_labels", [None, ["lane"]])
def test_held_full_pages_cannot_hide_older_eligible_task(
    q: TaskQueue, api: TestClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lane_labels: list[str] | None,
) -> None:
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    eligible = task(q, labels=["lane"])
    with q._connect() as conn:
        # Tied timestamps exercise a real keyset boundary, not a time-only cursor.
        cols = [r["name"] for r in conn.execute("PRAGMA table_info(tasks)")]
        bind = ", ".join("?" if c in ("id", "created_at", "labels") else c for c in cols)
        values = {"created_at": 200, "labels": '["lane","paused"]'}
        conn.executemany(f"INSERT INTO tasks SELECT {bind} FROM tasks WHERE id=?", [
            [*({"id": f"!paused-{i:04}", **values}[c] for c in cols if c in ("id", "created_at", "labels")),
             eligible.id] for i in range(405)
        ])
    command("hold", "--label", "paused", "--reason", "stop")
    attempts: list[str] = []
    def observe(_client: object, row: dict[str, str], _owner: str, **_kw: object) -> dict[str, bool]:
        attempts.append(row["id"])
        return {"reserved": False}
    monkeypatch.setattr(ordinary_bridge, "reserve_for_backend", observe)
    requests: list[httpx.Request] = []
    with controls.connected(api, requests) as client:
        lane = Supervisor(client, supervisor_id="workers", repo="example", labels=lane_labels,
                          spawn_fn=lambda _: pytest.fail("no granted reservation"))
        for cycle in range(3):
            requests.clear()
            lane.poll_once()
            pages = [r for r in requests if r.url.path == "/tasks" and "limit" in r.url.params]
            assert all(int(r.url.params["limit"]) <= 200 for r in pages)
            assert len([r for r in requests if r.url.path == "/worker-admission/task-page"]) == 1
            if cycle < 2:
                assert attempts == []
        assert attempts == [eligible.id]
        with q._connect() as conn:
            conn.execute("UPDATE tasks SET labels=?,created_at=? WHERE id=?", ('["lane"]', 300, eligible.id))
        def offline(*_: object) -> None:
            raise httpx.ReadTimeout("supplement unavailable")
        monkeypatch.setattr("agent_dispatch.admission_page.next_page", offline)
        attempts.clear()
        lane.poll_once()
        assert attempts == [eligible.id]


@pytest.mark.parametrize("hold_at", ["supervisor", "coordinator"])
def test_pending_redrive_observes_only_original_receipt(
    hosted: tuple[TaskQueue, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hold_at: str,
) -> None:
    queue, task_id = hosted
    monkeypatch.setenv("AGENT_DISPATCH_INSTALL_DIR", str(tmp_path))
    queue.resume(task_id, native.OWNER, reuse_session=True)
    queue.yield_task(task_id, native.OWNER, release_spawn=False)
    calls: list[list[str]] = []
    receipt_present = False

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        if "--receipt-only" not in argv or not receipt_present:
            return subprocess.CompletedProcess(argv, 70, "", "")
        import json
        receipt = {"version": 1, "session_id": native.BRIDGE, "native_session_id": native.NATIVE,
                   "invocation_key": argv[argv.index("--idempotency-key") + 1], "turn_index": 1,
                   "admission": "accepted", "queued": False}
        return subprocess.CompletedProcess(argv, 0, json.dumps(receipt), "")

    monkeypatch.setattr(subprocess, "run", run)
    with TestClient(create_app(queue, token="data", control_token="control", enable_mcp=False,
                               verification_interval=0)) as api, controls.connected(api, []) as client:
        lane = Supervisor(client, repo="queue-project", machine="host-a", spawn_fn=lambda _: pytest.fail("no spawn"))
        original = queue.latest_reservation(task_id)
        assert original is not None
        fresh = client.get_reservation(original.key)["ordinary_redrive"]
        def observe_only(facts: dict[str, Any]) -> None:
            client.record_spawn(original.key, session_handle=original.session_handle, worktree=original.worktree,
                                startup_complete=False, startup_revision=facts["startup_revision"],
                                ordinary_redrive={**facts, "observe_only": True})

        with pytest.raises(DispatchError):
            observe_only(fresh)
        assert queue.latest_reservation(task_id) == original
        assert lane.redrive_unclaimed_spawns() == 0
        before = queue.latest_reservation(task_id)
        assert before and not before.startup_complete and len(calls) == 1
        if hold_at == "supervisor":
            command("hold", "--pool", lane.supervisor_id, "--reason", "stop intake")
        else:
            def deny(*_: object) -> None:
                raise WorkerAdmissionInhibited()
            monkeypatch.setattr(queue, "_spawn_admission", deny)
        pending = client.get_reservation(before.key)["ordinary_redrive"]
        with pytest.raises(DispatchError):
            observe_only({**pending, "invocation": {**pending["invocation"], "prompt": "not the original prompt"}})
        assert lane.redrive_unclaimed_spawns() == 0
        assert queue.latest_reservation(task_id) == before
        receipt_present = True
        assert lane.redrive_unclaimed_spawns() == 1
        assert len(calls) == 3 and all("--receipt-only" in argv for argv in calls[1:])
        after = queue.latest_reservation(task_id)
        assert after and after.startup_complete and after.startup_revision == before.startup_revision
        assert after.agent_info == before.agent_info and after.session_handle == before.session_handle
