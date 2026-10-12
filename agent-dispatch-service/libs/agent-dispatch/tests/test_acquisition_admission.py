"""Native start identity and activity samples cannot inherit a prior turn's idle."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agent_dispatch.client import DispatchClient
from agent_dispatch import tracking
from agent_dispatch.__main__ import build_parser
from tests._helpers import RepoDefaultingQueue as TaskQueue
from agent_dispatch.queue import TaskError
from agent_dispatch.acquisition_receipts import bind
from agent_dispatch import acquisition_receipts
from agent_dispatch.acquisition_receipts import bridge_evidence as bridge_evidence_real


def evidence(receipt):
    return {"delivery_id": receipt["delivery_id"], "stage": "launched", "state": "pending",
            "delivery_admitted": True,
            "recorded_result": {k: v for k, v in receipt.items() if k != "reservation_key"}}

pytestmark = pytest.mark.contract("agent_dispatch.acquisition_admission")


@pytest.fixture(autouse=True)
def receipt_delivery_authority(monkeypatch):
    def inspect(delivery_id):
        return {"delivery_id": delivery_id, "stage": "delivered", "state": "completed",
                "recorded_result": {"task_id": "unused"}}
    monkeypatch.setattr(acquisition_receipts, "bridge_evidence", inspect)


def test_native_start_argument_constructs_exact_request_without_ambient_identity(monkeypatch):
    args = build_parser().parse_args([
        "start", "task-a", "headless-reservation-a", "--owner-session-id", "native-a",
    ])
    assert args.owner_session_id == "native-a"
    client = DispatchClient.__new__(DispatchClient)
    client._http = MagicMock()
    monkeypatch.setattr(client, "_unwrap", lambda response: response)
    client.start("task-a", "headless-reservation-a", owner_session_id=args.owner_session_id)
    client._http.post.assert_called_once_with(
        "/tasks/task-a/start",
        json={"worker_id": "headless-reservation-a", "owner_session_id": "native-a"},
    )


def test_claim_start_clear_prior_idle_and_reject_prestart_activity_sample(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "tasks.db")
    task = queue.create("review", now=10)
    reservation, _ = queue.reserve_spawn(task.id, now=11)
    queue.record_spawn_worktree(reservation.key, "test-worktree", ownership="created", now=11)
    queue.record_spawn(reservation.key, session_handle="local-body:bridge-a", now=12)
    queue.set_activity(task.id, "IDLE", reservation_key=reservation.key, now=13)
    claimed = queue.claim_one("acquired-reservation-a", task_id=task.id, now=14)
    assert claimed.activity is None
    # A snapshot taken after claim but before start is no longer current once
    # start has established a new native turn's admission.
    queue.set_activity(task.id, "IDLE", reservation_key=reservation.key, now=15)
    with pytest.raises(TaskError, match="exact native"):
        queue.start(task.id, "acquired-reservation-a", now=16)
    assert queue.get(task.id).status == "claimed"
    receipt = {
        "reservation_key": reservation.key, "task_id": task.id,
        "task_generation": claimed.generation, "worktree_id": "test-worktree",
        "session_id": "bridge-a", "native_session_id": "native-a",
        "mode": "acp", "delivery_id": "delivery-a",
    }
    bind(queue, receipt, evidence=evidence(receipt))
    monkeypatch.setattr(acquisition_receipts, "bridge_evidence", lambda _id: {
        **evidence(receipt), "stage": "delivered",
    })
    started = queue.start(task.id, "acquired-reservation-a", owner_session_id="native-a", now=16)
    assert started.owner_session_id == "native-a"
    assert started.activity is None
    stale = queue.set_activity(
        task.id, "IDLE", reservation_key=reservation.key, observed_at=15, now=17,
    )
    assert stale.activity is None
    active = queue.set_activity(
        task.id, "ACTIVE", reservation_key=reservation.key, observed_at=17, now=18,
    )
    assert active.activity == "ACTIVE"
    with pytest.raises(TaskError, match="finite"):
        queue.set_activity(
            task.id, "IDLE", reservation_key=reservation.key, observed_at=float("nan"), now=19,
        )


def test_active_owned_turn_beats_lingering_idle_metadata():
    assert tracking.session_activity({
        "status": "running", "at_rest": False, "liveness": "idle", "turn_state": "idle",
    }) == "ACTIVE"
    assert tracking.session_activity({
        "status": "idle", "at_rest": True, "liveness": "idle", "turn_state": "idle",
    }) == "IDLE"
    assert tracking.session_activity({
        "status": "running", "at_rest": False, "liveness": "disconnected",
    }) is None


def test_coordinator_admission_fence_ignores_supervisor_clock_skew(tmp_path):
    from dataclasses import asdict
    queue = TaskQueue(tmp_path / "clock.db")
    task = queue.create("work", now=10)
    reservation, _ = queue.reserve_spawn(task.id, now=11)
    queue.record_spawn(reservation.key, session_handle="native-a", now=12)
    claimed = queue.claim_one("worker", task_id=task.id, now=13)
    old = {k: asdict(claimed)[k] for k in ("generation", "status", "started_at", "owner_session_id")}
    started = queue.start(task.id, "worker", now=14)
    result = queue.set_activity(task.id, "IDLE", reservation_key=reservation.key,
                                observed_at=9999999999, admission_fence=old, now=15)
    assert result.activity is None
    current = {k: asdict(started)[k] for k in old}
    result = queue.set_activity(task.id, "ACTIVE", reservation_key=reservation.key,
                                observed_at=0, admission_fence=current, now=16)
    assert result.activity == "ACTIVE"


@pytest.mark.parametrize("owner,mode", [("acquired-reservation", "acp"), ("machine/worktree-a", "cli")])
def test_receipt_fences_exact_native_start_for_both_explicit_and_worktree_owners(tmp_path, monkeypatch, owner, mode):
    queue = TaskQueue(tmp_path / "receipt.db")
    task = queue.create("work")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(reservation.key, "worktree-a", ownership="created")
    receipt = {
        "reservation_key": reservation.key, "task_id": task.id,
        "task_generation": task.generation, "worktree_id": "worktree-a",
        "session_id": "bridge-a" if mode == "acp" else "native-a",
        "native_session_id": "native-a", "mode": mode, "delivery_id": "delivery-a",
    }
    bind(queue, receipt, evidence=evidence(receipt))
    monkeypatch.setattr(acquisition_receipts, "bridge_evidence", lambda _id: {
        **evidence(receipt), "stage": "delivered",
    })
    queue.record_spawn(reservation.key, session_handle="local-body:bridge-a" if mode == "acp" else "native-a")
    queue.claim_one(owner, task_id=task.id)
    for wrong in (None, "foreign-native"):
        with pytest.raises(TaskError):
            queue.start(task.id, owner, owner_session_id=wrong)
        assert queue.get(task.id).status == "claimed"
    started = queue.start(task.id, owner, owner_session_id="native-a")
    assert started.owner_session_id == "native-a"
    with pytest.raises(TaskError):
        queue.start(task.id, owner, owner_session_id="foreign-native")


def test_bound_but_unadmitted_receipt_cannot_start_task(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "pending-receipt.db")
    task = queue.create("work")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(reservation.key, "worktree-a", ownership="created")
    receipt = {
        "reservation_key": reservation.key, "task_id": task.id,
        "task_generation": task.generation, "worktree_id": "worktree-a",
        "session_id": "native-a", "native_session_id": "native-a",
        "mode": "cli", "delivery_id": "delivery-a",
    }
    bind(queue, receipt, evidence=evidence(receipt))
    queue.claim_one("machine/worktree-a", task_id=task.id)
    monkeypatch.setattr(acquisition_receipts, "bridge_evidence", lambda _id: evidence(receipt))
    with pytest.raises(TaskError, match="has not established"):
        queue.start(task.id, "machine/worktree-a", owner_session_id="native-a")
    assert queue.get(task.id).status == "claimed"


def test_start_bridge_observation_does_not_hold_queue_write_lock(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "nonblocking-start.db")
    task = queue.create("work")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(reservation.key, "worktree-a", ownership="created")
    receipt = {
        "reservation_key": reservation.key, "task_id": task.id,
        "task_generation": task.generation, "worktree_id": "worktree-a",
        "session_id": "native-a", "native_session_id": "native-a",
        "mode": "cli", "delivery_id": "delivery-a",
    }
    bind(queue, receipt, evidence=evidence(receipt))
    queue.claim_one("machine/worktree-a", task_id=task.id)
    writes = []
    def inspect(_delivery):
        # A second queue write must succeed during the potentially slow bridge
        # observation, rather than wait behind a BEGIN IMMEDIATE start lock.
        writes.append(queue.create("unrelated").id)
        return {**evidence(receipt), "stage": "delivered"}
    monkeypatch.setattr(acquisition_receipts, "bridge_evidence", inspect)
    started = queue.start(task.id, "machine/worktree-a", owner_session_id="native-a")
    assert started.status == "started"
    assert len(writes) == 1


def test_withdrawn_cli_receipt_cannot_fall_back_to_legacy_owner_start(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "withdrawn.db")
    task = queue.create("work")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(reservation.key, "worktree-a", ownership="created")
    receipt = {
        "reservation_key": reservation.key, "task_id": task.id,
        "task_generation": task.generation, "worktree_id": "worktree-a",
        "session_id": "native-a", "native_session_id": "native-a",
        "mode": "cli", "delivery_id": "delivery-a",
    }
    bind(queue, receipt, evidence=evidence(receipt))
    queue.record_spawn(reservation.key, session_handle="native-a")
    queue.claim_one("machine/worktree-a", task_id=task.id)
    monkeypatch.setattr(acquisition_receipts, "bridge_evidence", lambda _id: {
        **evidence(receipt), "stage": "delivered",
    })
    queue.request_spawn_release(reservation.key)
    with pytest.raises(TaskError, match="withdrawn"):
        queue.start(task.id, "machine/worktree-a", owner_session_id="native-a")
    assert queue.get(task.id).status == "claimed"


def test_suspended_acquisition_adoption_requires_exact_native_receipt(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "resume-receipt.db")
    task = queue.create("work")
    queue.claim_one("machine/worktree-a", task_id=task.id)
    queue.start(task.id, "machine/worktree-a", owner_session_id="prior-native")
    queue.suspend(task.id, "machine/worktree-a", reason="resume test")
    task = queue.get(task.id)
    reservation, _ = queue.reserve_spawn(task.id, allow_suspended_reembodiment=True)
    queue.record_spawn_worktree(reservation.key, "worktree-a", ownership="reused")
    receipt = {
        "reservation_key": reservation.key, "task_id": task.id,
        "task_generation": task.generation, "worktree_id": "worktree-a",
        "session_id": "native-a", "native_session_id": "native-a",
        "mode": "cli", "delivery_id": "delivery-a",
    }
    bind(queue, receipt, evidence=evidence(receipt))
    monkeypatch.setattr(acquisition_receipts, "bridge_evidence", lambda _id: {
        **evidence(receipt), "stage": "delivered",
    })
    with pytest.raises(TaskError, match="does not match"):
        queue.resume(task.id, "machine/worktree-a", adopt_owner_session_id="wrong-native")
    assert queue.get(task.id).status == "suspended"
    resumed = queue.resume(task.id, "machine/worktree-a", adopt_owner_session_id="native-a")
    assert resumed.status == "started" and resumed.owner_session_id == "native-a"


def test_cli_start_before_acquisition_receipt_binding_refuses(tmp_path):
    queue = TaskQueue(tmp_path / "pre-receipt.db")
    task = queue.create("work")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(reservation.key, "worktree-a", ownership="created")
    with queue._connect() as conn:
        acquisition_receipts.ensure(conn)
        conn.execute("INSERT INTO acquisition_markers VALUES (?)", (reservation.key,))
        conn.commit()
    queue.claim_one("machine/worktree-a", task_id=task.id)
    with pytest.raises(TaskError, match="bridge acquisition receipt"):
        queue.start(task.id, "machine/worktree-a", owner_session_id="inferred-native")
    assert queue.get(task.id).status == "claimed"


def test_marked_spawned_cli_requires_receipt(tmp_path):
    queue = TaskQueue(tmp_path / "marked-spawned.db")
    task = queue.create("work")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(reservation.key, "worktree-a", ownership="created")
    with queue._connect() as conn:
        acquisition_receipts.ensure(conn)
        conn.execute("INSERT INTO acquisition_markers VALUES (?)", (reservation.key,))
        conn.commit()
    queue.record_spawn(reservation.key, session_handle="native-a")
    queue.claim_one("machine/worktree-a", task_id=task.id)
    with pytest.raises(TaskError, match="bridge acquisition receipt"):
        queue.start(task.id, "machine/worktree-a", owner_session_id="native-a")
    assert queue.get(task.id).status == "claimed"


def test_later_independent_claim_is_not_fenced_by_old_acquisition(tmp_path, monkeypatch):
    queue = TaskQueue(tmp_path / "later-claim.db")
    task = queue.create("work")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(reservation.key, "worktree-a", ownership="created")
    receipt = {
        "reservation_key": reservation.key, "task_id": task.id,
        "task_generation": task.generation, "worktree_id": "worktree-a",
        "session_id": "native-a", "native_session_id": "native-a",
        "mode": "cli", "delivery_id": "delivery-a",
    }
    bind(queue, receipt, evidence=evidence(receipt))
    queue.record_spawn(reservation.key, session_handle="native-a")
    queue.claim_one("machine/worktree-a", task_id=task.id)
    monkeypatch.setattr(acquisition_receipts, "bridge_evidence", lambda _id: {
        **evidence(receipt), "stage": "delivered",
    })
    queue.start(task.id, "machine/worktree-a", owner_session_id="native-a")
    queue.yield_task(task.id, "machine/worktree-a", note="later worker")
    queue.claim_one("manual-worker", task_id=task.id)
    monkeypatch.setattr(acquisition_receipts, "bridge_evidence", lambda _id: pytest.fail("historic receipt must not require bridge"))
    assert queue.start(task.id, "manual-worker", owner_session_id="manual-native").status == "started"


def test_receipt_route_reads_bridge_authority_not_request_supplied_session(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import json
    import subprocess
    from agent_dispatch import procutil
    queue = TaskQueue(tmp_path / "source.db")
    task = queue.create("work")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(reservation.key, "worktree-a", ownership="created")
    receipt = {
        "reservation_key": reservation.key, "task_id": task.id,
        "task_generation": task.generation, "worktree_id": "worktree-a",
        "session_id": "native-a", "native_session_id": "native-a",
        "mode": "cli", "delivery_id": "delivery-a",
    }
    calls = []
    monkeypatch.setattr(procutil, "agent_bridge_launch_prefix", lambda: ["bridge"])
    authority_stage = {"value": "launched"}
    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            **evidence(receipt), "stage": authority_stage["value"],
        }), "")
    monkeypatch.setattr(acquisition_receipts.subprocess, "run", run)
    monkeypatch.setattr(acquisition_receipts, "bridge_evidence", bridge_evidence_real)
    app = FastAPI()
    acquisition_receipts.register_routes(app, queue)
    with TestClient(app) as client:
        forged = {**receipt, "session_id": "foreign", "native_session_id": "foreign"}
        assert client.post("/acquisition-receipts", json=forged).status_code == 409
        assert client.post("/acquisition-receipts", json=receipt).status_code == 200
    assert calls == [["bridge", "acquisition-inspect", "delivery-a"]] * 2
    authority_stage["value"] = "delivered"
    queue.claim_one("machine/worktree-a", task_id=task.id)
    with pytest.raises(TaskError):
        queue.start(task.id, "machine/worktree-a", owner_session_id="foreign")
    assert queue.start(task.id, "machine/worktree-a", owner_session_id="native-a").owner_session_id == "native-a"


@pytest.mark.parametrize("mode", ["cli", "acp"])
@pytest.mark.parametrize("stage", ["delivering", "delivered"])
def test_start_refuses_pre_admission_receipt_even_with_delivery_stage(
    tmp_path, monkeypatch, mode, stage,
):
    queue = TaskQueue(tmp_path / "admission.db")
    task = queue.create("work")
    reservation, _ = queue.reserve_spawn(task.id)
    queue.record_spawn_worktree(reservation.key, "worktree-a", ownership="created")
    claimed = queue.claim_one("machine/worktree-a", task_id=task.id)
    receipt = {
        "reservation_key": reservation.key, "task_id": task.id,
        "task_generation": claimed.generation, "worktree_id": "worktree-a",
        "session_id": "native-a" if mode == "cli" else "bridge-a",
        "native_session_id": "native-a", "mode": mode, "delivery_id": "delivery-a",
    }
    bind(queue, receipt, evidence=evidence(receipt))
    authority = {**evidence(receipt), "stage": stage, "delivery_admitted": False}
    monkeypatch.setattr(acquisition_receipts, "bridge_evidence", lambda _id: authority)
    with pytest.raises(TaskError, match="delivery has not"):
        queue.start(task.id, "machine/worktree-a", owner_session_id="native-a")
    assert queue.get(task.id).status == "claimed"
    authority["delivery_admitted"] = True
    assert queue.start(task.id, "machine/worktree-a", owner_session_id="native-a").status == "started"
