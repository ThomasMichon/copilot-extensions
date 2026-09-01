"""Tests for the agent-dispatch queue engine."""

from __future__ import annotations

import concurrent.futures
import json
import sqlite3
import threading

import pytest

from agent_dispatch.queue import (
    DEFAULT_LEASE_SECONDS,
    DEFAULT_RESULT_MAX_BYTES,
    LEGACY_REPO,
    ResultTooLargeError,
    ResultValidationError,
    SpawnState,
    Status,
    TaskError,
    machine_matches,
    worker_id_for,
)
from agent_dispatch.queue import TaskQueue as RealTaskQueue
from tests._helpers import OTHER_REPO, TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


# -- basic lifecycle ---------------------------------------------------------


def test_create_defaults_to_queued(q):
    t = q.create("do a thing", prompt="go")
    assert t.status == Status.QUEUED
    assert t.title == "do a thing"
    assert t.prompt == "go"
    assert t.attempts == 0


def test_full_happy_path(q):
    t = q.create("work")
    claimed = q.claim_one("w1")
    assert claimed is not None
    assert claimed.id == t.id
    assert claimed.status == Status.CLAIMED
    assert claimed.owner == "w1"
    assert claimed.attempts == 1
    started = q.start(t.id, "w1")
    assert started.status == Status.STARTED
    done = q.complete(t.id, "w1", result_ref="pr/42")
    assert done.status == Status.COMPLETED
    assert done.result_ref == "pr/42"
    assert done.owner is None
    assert done.completed_by == "w1"


def test_complete_persists_schema_neutral_structured_result(q):
    t = q.create("work")
    q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1")
    result = {
        "outcome": "accepted",
        "counts": {"passed": 4, "failed": 0},
        "items": ["alpha", {"id": 2, "enabled": True}],
    }

    done = q.complete(t.id, "w1", result_ref="artifact/42", result=result)

    assert done.status == Status.COMPLETED
    assert done.result_ref == "artifact/42"
    assert done.result == result
    assert done.has_result is True
    assert q.get(t.id).result == result
    listed = q.list()[0]
    assert listed.result is None
    assert listed.has_result is True


def test_bulk_reads_skip_result_bodies_while_full_reads_decode_them(q):
    large_task = q.create("bulk large")
    q.claim_one("worker-1", task_id=large_task.id)
    q.start(large_task.id, "worker-1")
    large_result = {"data": "x" * 60_000}
    q.complete(large_task.id, "worker-1", result=large_result)

    invalid_task = q.create("bulk invalid")
    q.claim_one("worker-2", task_id=invalid_task.id)
    q.start(invalid_task.id, "worker-2")
    q.complete(invalid_task.id, "worker-2", result={"valid": True})

    assigned_task = q.create("bulk inbox", target_worktree="wt-1")
    with sqlite3.connect(q.db_path) as conn:
        conn.execute(
            "UPDATE tasks SET result = ? WHERE id IN (?, ?)",
            ("{not-json", invalid_task.id, assigned_task.id),
        )

    for tasks in (q.list(), q.find("bulk"), q.sweep()):
        by_id = {task.id: task for task in tasks}
        assert by_id[large_task.id].result is None
        assert by_id[large_task.id].has_result is True
        assert by_id[invalid_task.id].result is None
        assert by_id[invalid_task.id].has_result is True

    inbox = q.mine("host-a", "wt-1")
    assigned = {task.id: task for task in inbox["assigned"]}
    assert assigned[assigned_task.id].result is None
    assert assigned[assigned_task.id].has_result is True

    assert q.get(large_task.id).result == large_result
    assert q.read_result(large_task.id) == large_result
    with pytest.raises(json.JSONDecodeError):
        q.get(invalid_task.id)
    with pytest.raises(json.JSONDecodeError):
        q.read_result(invalid_task.id)


@pytest.mark.parametrize("invalid", ["{}", 7, True])
def test_complete_rejects_explicit_non_structured_result(q, invalid):
    t = q.create("work")
    q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1")

    with pytest.raises(ResultValidationError, match="JSON object or array"):
        q.complete(t.id, "w1", result=invalid)


def test_complete_result_persists_across_reopen(tmp_path):
    db = tmp_path / "tasks.db"
    q1 = TaskQueue(db)
    t = q1.create("persist result")
    q1.claim_one("w1", task_id=t.id)
    q1.start(t.id, "w1")
    q1.complete(t.id, "w1", result={"nested": {"value": 7}})

    q2 = TaskQueue(db)

    assert q2.get(t.id).result == {"nested": {"value": 7}}


@pytest.mark.parametrize("invalid", [{"value": {1, 2}}, {"value": float("nan")}])
def test_invalid_complete_result_is_atomic(q, invalid):
    t = q.create("work")
    q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1")

    with pytest.raises(ResultValidationError, match="not JSON-compatible"):
        q.complete(t.id, "w1", result_ref="artifact/invalid", result=invalid)

    unchanged = q.get(t.id)
    assert unchanged.status == Status.STARTED
    assert unchanged.result_ref is None
    assert unchanged.result is None
    assert unchanged.completed_at is None


def test_oversized_complete_result_is_atomic(tmp_path):
    q = TaskQueue(tmp_path / "tasks.db", result_max_bytes=32)
    t = q.create("work")
    q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1")

    with pytest.raises(ResultTooLargeError, match="32-byte encoded limit"):
        q.complete(t.id, "w1", result_ref="artifact/large", result={"data": "x" * 40})

    unchanged = q.get(t.id)
    assert unchanged.status == Status.STARTED
    assert unchanged.result_ref is None
    assert unchanged.result is None


def test_complete_without_result_remains_backward_compatible(q):
    t = q.create("work")
    q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1")

    done = q.complete(t.id, "w1", result_ref="artifact/legacy")

    assert done.status == Status.COMPLETED
    assert done.result_ref == "artifact/legacy"
    assert done.result is None


def test_same_owner_can_retry_completed_task_to_fill_missing_result(q):
    t = q.create("work")
    q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1")
    q.complete(t.id, "w1", result_ref="artifact/1")

    result = {"outcome": "recorded"}
    retried = q.complete_with_outcome(
        t.id, "w1", result_ref="artifact/1", result=result
    )

    assert retried.event_type == "task.result_recorded"
    assert retried.task.status == Status.COMPLETED
    assert retried.task.result == result
    assert q.events(t.id)[-1]["note"] == "complete retry: result recorded"
    repeated = q.complete_with_outcome(t.id, "w1", result=result)
    assert repeated.task.result == result
    assert repeated.event_type is None


def test_retry_fill_recovers_owner_from_completion_after_migration(tmp_path):
    db = tmp_path / "cutover.db"
    q = TaskQueue(db)
    task = q.create("old generation completion")
    q.claim_one("worker-1", task_id=task.id)
    q.start(task.id, "worker-1")

    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE tasks SET status = ?, completed_at = ?, result_ref = ?,"
            " result = NULL, completed_by = NULL, owner = NULL"
            " WHERE id = ?",
            (Status.COMPLETED, 10, "artifact/old", task.id),
        )
        conn.execute(
            "INSERT INTO task_events"
            " (task_id, ts, from_status, to_status, worker, note)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                task.id,
                10,
                Status.STARTED,
                Status.COMPLETED,
                "worker-1",
                "complete",
            ),
        )

    assert q.get(task.id).completed_by is None
    events_before = q.events(task.id)
    with pytest.raises(TaskError, match="completed by 'worker-1', not 'worker-2'"):
        q.complete(task.id, "worker-2", result={"outcome": "wrong owner"})
    assert q.get(task.id).completed_by is None
    assert q.get(task.id).result is None

    outcome = q.complete_with_outcome(
        task.id,
        "worker-1",
        result_ref="artifact/old",
        result={"outcome": "recorded"},
    )

    assert outcome.event_type == "task.result_recorded"
    assert outcome.task.completed_by == "worker-1"
    assert outcome.task.result == {"outcome": "recorded"}
    events_after = q.events(task.id)
    assert len(events_after) == len(events_before) + 1
    assert events_after[-1] == {
        "ts": events_after[-1]["ts"],
        "from_status": Status.COMPLETED,
        "to_status": Status.COMPLETED,
        "worker": "worker-1",
        "note": "complete retry: result recorded",
    }
    listed = next(item for item in q.list() if item.id == task.id)
    assert listed.result is None
    assert listed.has_result is True

    with pytest.raises(TaskError, match="completed by 'worker-1', not 'worker-2'"):
        q.complete(task.id, "worker-2", result={"outcome": "recorded"})
    assert q.get(task.id).result == {"outcome": "recorded"}


def test_completion_retry_never_overwrites_result_or_crosses_owner(q):
    t = q.create("work")
    q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1")
    q.complete(t.id, "w1", result={"outcome": "first"})

    with pytest.raises(TaskError, match="different result"):
        q.complete(t.id, "w1", result={"outcome": "second"})
    with pytest.raises(TaskError, match="completed by"):
        q.complete(t.id, "w2", result={"outcome": "first"})

    assert q.get(t.id).result == {"outcome": "first"}


def test_resume_atomically_persists_wake_outbox(q):
    t = q.create("wake me")
    q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1", owner_session_id="session-1")
    q.suspend(t.id, "w1", reason="waiting")

    task = q.resume(
        t.id,
        "w1",
        wake_requested=True,
        wake_message="continue",
        now=1000.0,
    )

    [wake] = q.list_wakes(t.id)
    assert task.status == Status.STARTED
    assert task.wake_status == "pending"
    assert task.wake_operation_id == wake.id
    assert wake.id == f"wake:{t.id}:{task.generation}:1"
    assert wake.owner == "w1"
    assert wake.owner_session_id == "session-1"
    assert wake.message == "continue"
    assert wake.status == "pending"


def test_suspend_resume_preserves_durable_owner_context(q):
    t = q.create(
        "wait for input",
        goal="Ship the change",
        done_criteria="Checks pass",
        target_machine="host-a",
        target_worktree="wt-1",
    )
    claimed = q.claim_one(
        "host-a/wt-1",
        machine="host-a",
        worktree="wt-1",
        task_id=t.id,
        now=1000.0,
    )
    started = q.start(
        t.id, "host-a/wt-1", owner_session_id="session-1", now=1010.0
    )
    q.record_progress(
        t.id,
        "host-a/wt-1",
        phase="waiting",
        summary="submitted the request",
        now=1020.0,
    )
    q.set_card(
        t.id,
        "host-a/wt-1",
        card={"status": "waiting", "request_input": [{"name": "answer"}]},
        now=1030.0,
    )

    suspended = q.suspend(
        t.id,
        "host-a/wt-1",
        reason="Waiting for an external decision",
        now=1040.0,
    )

    assert suspended.status == Status.SUSPENDED
    assert suspended.owner == claimed.owner
    assert suspended.owner_session_id == started.owner_session_id
    assert suspended.generation == claimed.generation
    assert suspended.target_machine == "host-a"
    assert suspended.target_worktree == "wt-1"
    assert suspended.goal == "Ship the change"
    assert suspended.latest_progress is not None
    assert suspended.card["status"] == "waiting"
    assert suspended.lease_expires_at is None
    assert suspended.activity is None
    assert q.claim_one("replacement", task_id=t.id) is None
    assert q.events(t.id)[-1]["note"] == (
        "suspend: Waiting for an external decision"
    )

    resumed = q.resume(t.id, "host-a/wt-1", now=1050.0)
    assert resumed.status == Status.STARTED
    assert resumed.owner == "host-a/wt-1"
    assert resumed.owner_session_id == "session-1"
    assert resumed.generation == claimed.generation
    assert resumed.lease_expires_at == pytest.approx(
        1050.0 + DEFAULT_LEASE_SECONDS
    )


def test_suspend_resume_are_owner_gated_and_state_checked(q):
    t = q.create("wait")
    q.claim_one("w1", task_id=t.id)
    with pytest.raises(TaskError, match="started"):
        q.suspend(t.id, "w1", reason="not started")
    q.start(t.id, "w1")
    with pytest.raises(TaskError, match="non-empty reason"):
        q.suspend(t.id, "w1", reason="  ")
    with pytest.raises(TaskError, match="owned by"):
        q.suspend(t.id, "w2", reason="wrong owner")
    q.suspend(t.id, "w1", reason="waiting")
    with pytest.raises(TaskError, match="owned by"):
        q.resume(t.id, "w2")
    with pytest.raises(TaskError, match="owned by"):
        q.complete(t.id, "w2")


def test_suspended_successor_adopts_session_and_advances_generation(q):
    t = q.create("continue after handoff")
    claimed = q.claim_one("host-a/wt-1", task_id=t.id)
    q.start(t.id, "host-a/wt-1", owner_session_id="session-old")
    snapshot = q.suspend(t.id, "host-a/wt-1", reason="handoff")

    resumed = q.resume(
        t.id,
        "host-a/wt-1",
        adopt_owner_session_id="session-new",
        expected_owner_session_id=snapshot.owner_session_id,
        expected_generation=snapshot.generation,
    )

    assert resumed.status == Status.STARTED
    assert resumed.owner == claimed.owner
    assert resumed.owner_session_id == "session-new"
    assert resumed.generation == snapshot.generation + 1


def test_resume_with_wake_reembodies_headless_owner(q):
    t = q.create("continue headless work")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:bridge-session-1"
    )
    q.claim_one("headless-owner", task_id=t.id)
    q.start(t.id, "headless-owner")
    q.suspend(t.id, "headless-owner", reason="waiting")

    resumed = q.resume(t.id, "headless-owner", wake_requested=True)

    assert resumed.status == Status.SUSPENDED
    assert resumed.owner == "headless-owner"
    assert resumed.resume_requested is True
    assert q.get_reservation(reservation.key).state == SpawnState.SPAWNED


def test_resume_without_wake_reembodies_cold_headless_owner(q):
    t = q.create("continue headless work")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(
        reservation.key, session_handle="local-body:bridge-session-1"
    )
    q.claim_one("headless-owner", task_id=t.id)
    q.start(t.id, "headless-owner")
    q.suspend(t.id, "headless-owner", reason="waiting")

    resumed = q.resume(t.id, "headless-owner", wake_requested=False)

    assert resumed.status == Status.SUSPENDED
    assert resumed.owner == "headless-owner"
    assert resumed.resume_requested is True
    assert q.get_reservation(reservation.key).state == SpawnState.SPAWNED


def test_resume_worktree_owner_without_session_id_is_not_headless(q):
    t = q.create("continue worktree work")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(
        reservation.key, session_handle="session-1", worktree="wt-1"
    )
    q.claim_one("machine/wt-1", task_id=t.id)
    q.start(t.id, "machine/wt-1")
    q.suspend(t.id, "machine/wt-1", reason="waiting")

    resumed = q.resume(t.id, "machine/wt-1")

    assert resumed.status == Status.STARTED
    assert resumed.resume_requested is False
    assert q.get_reservation(reservation.key).state == SpawnState.SPAWNED
    assert q.list_wakes(t.id) == []


def test_suspended_successor_adoption_rejects_stale_snapshot(q):
    t = q.create("continue once")
    q.claim_one("host-a/wt-1", task_id=t.id)
    q.start(t.id, "host-a/wt-1", owner_session_id="session-old")
    snapshot = q.suspend(t.id, "host-a/wt-1", reason="handoff")
    q.resume(
        t.id,
        "host-a/wt-1",
        adopt_owner_session_id="session-new",
        expected_owner_session_id=snapshot.owner_session_id,
        expected_generation=snapshot.generation,
    )

    with pytest.raises(TaskError, match="started"):
        q.resume(
            t.id,
            "host-a/wt-1",
            adopt_owner_session_id="session-other",
            expected_owner_session_id=snapshot.owner_session_id,
            expected_generation=snapshot.generation,
        )


def test_suspended_task_can_complete_without_resume(q):
    t = q.create("wait for merge")
    claimed = q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1", owner_session_id="session-1")
    q.suspend(t.id, "w1", reason="waiting for merge")

    done = q.complete(t.id, "w1", result_ref="change/42")

    assert done.status == Status.COMPLETED
    assert done.result_ref == "change/42"
    assert done.owner is None
    assert done.generation == claimed.generation
    assert [event["to_status"] for event in q.events(t.id)][-2:] == [
        Status.SUSPENDED,
        Status.COMPLETED,
    ]


def test_fenced_suspended_completion_loses_concurrent_resume(q):
    t = q.create("consume baton once")
    q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1", owner_session_id="session-1")
    snapshot = q.suspend(t.id, "w1", reason="waiting")
    q.resume(t.id, "w1")

    with pytest.raises(TaskError, match="started"):
        q.complete(
            t.id,
            "w1",
            expected_status=Status.SUSPENDED,
            expected_owner_session_id=snapshot.owner_session_id,
            expected_generation=snapshot.generation,
        )

    assert q.get(t.id).status == Status.STARTED


def test_release_suspended_clears_owner_and_spawn_reservation(q):
    t = q.create("replace me")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(
        reservation.key, session_handle="session-1", worktree="wt-1"
    )
    q.claim_one("host-a/wt-1", task_id=t.id)
    q.start(t.id, "host-a/wt-1", owner_session_id="session-1")
    q.suspend(t.id, "host-a/wt-1", reason="parked")

    released = q.release_suspended(
        t.id, "host-a/wt-1", reason="replace the dormant worker"
    )

    assert released.status == Status.QUEUED
    assert released.owner is None
    assert released.owner_session_id is None
    assert released.claimed_at is None
    assert q.get_reservation(reservation.key).state == "settled"
    replacement = q.claim_one("host-b/wt-2", task_id=t.id)
    assert replacement is not None
    assert replacement.owner == "host-b/wt-2"


# -- proposed is not claimable ----------------------------------------------


def test_proposed_is_not_claimable(q):
    p = q.propose("draft idea")
    assert p.status == Status.PROPOSED
    assert q.claim_one("w1") is None
    approved = q.approve(p.id)
    assert approved.status == Status.QUEUED
    assert q.claim_one("w1") is not None


# -- atomic claim race -------------------------------------------------------


def test_concurrent_claim_single_winner(q):
    q.create("only one")
    barrier = threading.Barrier(8)

    def worker(i):
        barrier.wait()
        return q.claim_one(f"w{i}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(worker, range(8)))

    winners = [r for r in results if r is not None]
    assert len(winners) == 1
    assert winners[0].status == Status.CLAIMED


def test_two_queued_two_workers_no_double_claim(q):
    a = q.create("a")
    b = q.create("b")
    r1 = q.claim_one("w1")
    r2 = q.claim_one("w2")
    assert {r1.id, r2.id} == {a.id, b.id}
    assert q.claim_one("w3") is None


# -- liveness GC / recovery --------------------------------------------------


def test_gone_owner_requeues(q):
    t = q.create("leased")
    q.claim_one("m/wt", machine="m", worktree="wt", now=1000.0)
    # owner still live -> never requeued, no matter how much time passes
    assert q.reconcile_liveness(lambda wt, mc, sid: "live", now=9999.0)["requeued"] == 0
    assert q.get(t.id).status == Status.CLAIMED
    # resolver can't tell (bridge down) -> leave it alone (degrade safe)
    assert q.reconcile_liveness(lambda wt, mc, sid: "unknown", now=9999.0)["requeued"] == 0
    assert q.get(t.id).status == Status.CLAIMED
    # owner confirmed gone -> requeued (fenced on owner identity; here owner_session_id
    # is NULL since the task was claimed-not-started, and the fence matches NULL)
    counts = q.reconcile_liveness(lambda wt, mc, sid: "gone", now=2000.0)
    assert counts["checked"] == 1 and counts["gone"] == 1 and counts["requeued"] == 1
    back = q.get(t.id)
    assert back.status == Status.QUEUED
    assert back.owner is None
    # a second worker can now reclaim it
    assert q.claim_one("w2", now=2001.0).owner == "w2"


def test_suspended_task_is_excluded_from_liveness_gc(q):
    t = q.create("dormant")
    q.claim_one("m/wt", machine="m", worktree="wt", task_id=t.id)
    q.start(t.id, "m/wt", owner_session_id="session-1")
    q.suspend(t.id, "m/wt", reason="waiting")
    calls = []

    counts = q.reconcile_liveness(
        lambda wt, mc, sid: calls.append((wt, mc, sid)) or "gone"
    )

    assert calls == []
    assert counts["checked"] == 0
    assert counts["requeued"] == 0
    assert counts["dead_lettered"] == 0
    assert q.get(t.id).status == Status.SUSPENDED
    assert q.get(t.id).attempts == 1


def test_cooperative_redundancy_after_worker_death(q):
    """A capable second worker reclaims a dead worker's task once it is gone."""
    q.create("review", requires=["review"])
    first = q.claim_one("m/wt", capabilities=["review"], machine="m", worktree="wt", now=1000.0)
    assert first is not None and first.owner == "m/wt"
    # w2 can't claim while the first worker still holds it
    assert q.claim_one("w2", capabilities=["review"], now=1010.0) is None
    q.reconcile_liveness(lambda wt, mc, sid: "gone", now=2000.0)
    second = q.claim_one("w2", capabilities=["review"], now=2001.0)
    assert second is not None and second.owner == "w2"


def test_heartbeat_refreshes_last_seen(q):
    """heartbeat/progress no longer govern recovery (liveness does), but still
    refresh the informational ``lease_expires_at`` (a ``last_seen`` beat)."""
    t = q.create("long")
    q.claim_one("w1", now=1000.0, lease_seconds=60)
    q.heartbeat(t.id, "w1", now=1050.0)
    assert q.get(t.id).lease_expires_at == pytest.approx(1050.0 + DEFAULT_LEASE_SECONDS)


def test_heartbeat_wrong_owner_rejected(q):
    t = q.create("x")
    q.claim_one("w1")
    with pytest.raises(TaskError):
        q.heartbeat(t.id, "w2")


def test_set_activity_persists_independently_from_task_updated_at(q):
    t = q.create("observed", now=1000.0)
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(reservation.key, session_handle="local-body:s1")
    observed = q.set_activity(
        t.id, "ACTIVE", reservation_key=reservation.key, now=1010.0
    )
    assert observed.activity == "ACTIVE"
    assert observed.activity_updated_at == 1010.0
    assert observed.updated_at == 1000.0
    cleared = q.set_activity(
        t.id, None, reservation_key=reservation.key, now=1020.0
    )
    assert cleared.activity is None
    assert cleared.activity_updated_at == 1020.0


def test_set_activity_rejects_unknown_value(q):
    t = q.create("observed")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(reservation.key, session_handle="local-body:s1")
    with pytest.raises(TaskError, match="invalid task activity"):
        q.set_activity(t.id, "IDLE", reservation_key=reservation.key)


def test_set_activity_cannot_restore_activity_on_suspended_task(q):
    t = q.create("dormant")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(reservation.key, session_handle="local-body:s1")
    q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1")
    q.set_activity(t.id, "ACTIVE", reservation_key=reservation.key)
    q.suspend(t.id, "w1", reason="waiting")

    with pytest.raises(TaskError, match="non-null activity on suspended task"):
        q.set_activity(t.id, "STALLED", reservation_key=reservation.key)

    dormant = q.get(t.id)
    assert dormant.status == Status.SUSPENDED
    assert dormant.activity is None
    assert q.set_activity(
        t.id, None, reservation_key=reservation.key
    ).activity is None


def test_state_transition_clears_activity(q):
    t = q.create("observed")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(reservation.key, session_handle="local-body:s1")
    q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1")
    q.set_activity(
        t.id, "ACTIVE", reservation_key=reservation.key, now=1000.0
    )
    done = q.complete(t.id, "w1", now=1010.0)
    assert done.activity is None
    assert done.activity_updated_at == 1010.0


def test_set_activity_rejects_stale_or_wrong_reservation(q):
    t = q.create("observed")
    other = q.create("other")
    reservation, _ = q.reserve_spawn(t.id)
    q.record_spawn(reservation.key, session_handle="local-body:s1")
    wrong, _ = q.reserve_spawn(other.id)
    q.record_spawn(wrong.key, session_handle="local-body:s2")
    with pytest.raises(TaskError, match="active spawned reservation"):
        q.set_activity(t.id, "ACTIVE", reservation_key=wrong.key)
    q.settle_spawn(reservation.key)
    assert q.get(t.id).activity is None
    assert q.get(t.id).activity_updated_at is not None
    with pytest.raises(TaskError, match="active spawned reservation"):
        q.set_activity(t.id, "ACTIVE", reservation_key=reservation.key)


# -- progress beats ----------------------------------------------------------


def test_record_progress_stores_latest_snapshot(q):
    import json

    t = q.create("work")
    q.claim_one("w1")
    q.start(t.id, "w1")
    q.record_progress(
        t.id, "w1", phase="implementing", summary="wired the verb", now=2000.0
    )
    snap = json.loads(q.get(t.id).latest_progress)
    assert snap["phase"] == "implementing"
    assert snap["summary"] == "wired the verb"
    assert snap["ts"] == pytest.approx(2000.0)


def test_record_progress_latest_only_overwrites(q):
    import json

    t = q.create("work")
    q.claim_one("w1")
    q.record_progress(t.id, "w1", phase="planning", summary="first")
    q.record_progress(t.id, "w1", phase="implementing", summary="second")
    snap = json.loads(q.get(t.id).latest_progress)
    assert snap["phase"] == "implementing" and snap["summary"] == "second"


def test_record_progress_caps_summary(q):
    import json

    from agent_dispatch.queue import PROGRESS_SUMMARY_MAX

    t = q.create("work")
    q.claim_one("w1")
    q.record_progress(t.id, "w1", phase="p", summary="x" * 500)
    snap = json.loads(q.get(t.id).latest_progress)
    assert len(snap["summary"]) <= PROGRESS_SUMMARY_MAX
    assert snap["summary"].endswith("\u2026")


def test_record_progress_optional_fields(q):
    import json

    t = q.create("work")
    q.claim_one("w1")
    q.record_progress(t.id, "w1", phase="pr", summary="opened", pr="pr/42", blocker=None)
    snap = json.loads(q.get(t.id).latest_progress)
    assert snap["pr"] == "pr/42"
    assert "blocker" not in snap  # empty/None optional fields are dropped


def test_record_progress_refreshes_last_seen(q):
    t = q.create("work")
    q.claim_one("w1", now=1000.0, lease_seconds=60)
    q.record_progress(t.id, "w1", phase="p", summary="alive", now=1050.0)
    # progress doubles as a last-seen beat: refreshes the informational timestamp
    assert q.get(t.id).lease_expires_at == pytest.approx(1050.0 + DEFAULT_LEASE_SECONDS)


def test_record_progress_wrong_owner_rejected(q):
    t = q.create("work")
    q.claim_one("w1")
    with pytest.raises(TaskError):
        q.record_progress(t.id, "w2", phase="p", summary="nope")


def test_record_progress_requires_held(q):
    t = q.create("work")  # queued, not held
    with pytest.raises(TaskError):
        q.record_progress(t.id, "w1", phase="p", summary="too early")


def test_record_progress_appends_audit(q):
    t = q.create("work")
    q.claim_one("w1")
    q.record_progress(t.id, "w1", phase="planning", summary="settled the plan")
    notes = [e.get("note") for e in q.events(t.id)]
    assert any(n and "progress:" in n and "settled the plan" in n for n in notes)


# -- durable goal + append-only progress log ---------------------------------


def test_goal_and_done_criteria_round_trip(q):
    t = q.create(
        "improve one thing",
        prompt="pick something and improve it",
        goal="raise test coverage of module X",
        done_criteria="coverage >= 90% and CI green",
    )
    assert t.goal == "raise test coverage of module X"
    assert t.done_criteria == "coverage >= 90% and CI green"
    # Re-read from the store: fields persist on the row.
    got = q.get(t.id)
    assert got.goal == "raise test coverage of module X"
    assert got.done_criteria == "coverage >= 90% and CI green"


def test_create_without_goal_defaults_to_none(q):
    t = q.create("plain one-shot")
    assert t.goal is None
    assert t.done_criteria is None
    assert q.progress_log(t.id) == []


def test_record_progress_appends_to_progress_log(q):
    import json

    t = q.create("work", goal="reach the goal", done_criteria="it is done")
    q.claim_one("w1")
    q.start(t.id, "w1")
    q.record_progress(t.id, "w1", phase="planning", summary="first pass", now=1000.0)
    q.record_progress(t.id, "w1", phase="implementing", summary="second pass", now=2000.0)

    # latest-only beat still overwrites (no regression).
    snap = json.loads(q.get(t.id).latest_progress)
    assert snap["phase"] == "implementing" and snap["summary"] == "second pass"

    # append-only log accumulates BOTH beats, in chronological order.
    log = q.progress_log(t.id)
    assert [(r["phase"], r["summary"], r["worker"]) for r in log] == [
        ("planning", "first pass", "w1"),
        ("implementing", "second pass", "w1"),
    ]
    assert log[0]["ts"] == pytest.approx(1000.0)
    assert log[1]["ts"] == pytest.approx(2000.0)


def test_progress_log_carries_detail_and_blocker(q):
    t = q.create("work")
    q.claim_one("w1")
    # An explicit detail wins.
    q.record_progress(t.id, "w1", phase="p", summary="s", detail="a longer note")
    # Otherwise the beat's blocker/pr context becomes the log detail.
    q.record_progress(t.id, "w1", phase="pr", summary="opened", pr="pr/42")
    log = q.progress_log(t.id)
    assert log[0]["detail"] == "a longer note"
    assert log[1]["detail"] == "pr: pr/42"


def test_progress_log_empty_for_untouched_task(q):
    t = q.create("work")
    assert q.progress_log(t.id) == []


# -- capability gating -------------------------------------------------------


def test_requires_gates_claim(q):
    q.create("logging", requires=["logger"])
    assert q.claim_one("plain") is None
    assert q.claim_one("plain", capabilities=["logger"]) is not None


def test_identity_pin_via_requires(q):
    q.create("review", requires=["agent:review-bot"])
    assert q.claim_one("random", capabilities=["review"]) is None
    got = q.claim_one("review-bot", capabilities=["agent:review-bot"])
    assert got is not None


def test_affinity_orders_but_does_not_exclude(q):
    generic = q.create("generic")
    preferred = q.create("preferred", affinity={"agent": "w1"})
    # w1 prefers the affinity task even though the generic one is older
    got = q.claim_one("w1")
    assert got.id == preferred.id
    # a different worker still gets the remaining task (affinity never excludes)
    other = q.claim_one("w2")
    assert other.id == generic.id


# -- not_before scheduling ---------------------------------------------------


def test_not_before_defers_claim(q):
    q.create("later", not_before=5000.0)
    assert q.claim_one("w1", now=4000.0) is None
    assert q.claim_one("w1", now=5001.0) is not None


# -- dedup -------------------------------------------------------------------


def test_dedup_key_prevents_duplicate(q):
    a = q.create("dup", dedup_key="k1")
    b = q.create("dup again", dedup_key="k1")
    assert a.id == b.id
    assert len(q.list()) == 1


def test_dedup_key_allows_new_generation_after_terminal(q):
    first = q.create("first", dedup_key="k1")
    q.abandon(first.id, permitted=True, reason="superseded")
    second = q.create("second", dedup_key="k1")
    assert second.id != first.id
    assert len([t for t in q.list() if t.dedup_key == "k1"]) == 2


def test_stable_reviewer_dedup_collides_with_active_legacy_key(q):
    legacy = q.create(
        "legacy review",
        source="recipe",
        origin_ref="reviewer",
        dedup_key="recipe:reviewer:base=release:v2:pr=7:repo=o/n",
    )
    current = q.create(
        "current review",
        source="recipe",
        origin_ref="reviewer",
        dedup_key="recipe:reviewer:target=github.com/o/n#7",
    )
    assert current.id == legacy.id


def test_stable_reviewer_dedup_ignores_terminal_legacy_key(q):
    legacy = q.create(
        "legacy review",
        source="recipe",
        origin_ref="reviewer",
        dedup_key="recipe:reviewer:base=main:pr=7:repo=o/n",
    )
    q.abandon(legacy.id, permitted=True, reason="done")
    current = q.create(
        "current review",
        source="recipe",
        origin_ref="reviewer",
        dedup_key="recipe:reviewer:target=github.com/o/n#7",
    )
    assert current.id != legacy.id


def test_legacy_reviewer_dedup_collides_with_active_stable_key(q):
    stable = q.create(
        "stable review",
        source="recipe",
        origin_ref="reviewer",
        dedup_key="recipe:reviewer:target=github.com/o/n#7",
    )
    legacy = q.create(
        "legacy client review",
        source="recipe",
        origin_ref="reviewer",
        dedup_key="recipe:reviewer:base=release:pr=7:repo=o/n",
    )
    assert legacy.id == stable.id


# -- yield / abandon ---------------------------------------------------------


def test_yield_returns_to_queued_with_updates(q):
    t = q.create("conflict")
    q.claim_one("w1")
    q.start(t.id, "w1")
    y = q.yield_task(t.id, "w1", note="merge conflict")
    assert y.status == Status.QUEUED
    assert y.owner is None
    assert q.claim_one("w2") is not None


def test_abandon_requires_permission(q):
    t = q.create("bad")
    with pytest.raises(TaskError):
        q.abandon(t.id)
    done = q.abandon(t.id, permitted=True, reason="duplicate")
    assert done.status == Status.ABANDONED


def test_terminal_states_reject_transitions(q):
    t = q.create("x")
    q.claim_one("w1")
    q.start(t.id, "w1")
    q.complete(t.id, "w1")
    with pytest.raises(TaskError):
        q.start(t.id, "w1")


def test_start_wrong_owner_rejected(q):
    t = q.create("x")
    q.claim_one("w1")
    with pytest.raises(TaskError):
        q.start(t.id, "w2")


# -- detach (worktree portability) ------------------------------------------


def test_detach_demotes_hard_worktree_pin(q):
    t = q.create("handoff", requires=["worktree:wt-1"], target_worktree="wt-1")
    d = q.detach(t.id)
    assert "worktree:wt-1" not in d.requires
    assert d.affinity.get("worktree") == "wt-1"
    # now claimable by any worker (pin demoted to a soft preference)
    assert q.claim_one("anyone") is not None


# -- migration idempotency ---------------------------------------------------


def test_reopen_existing_db_is_idempotent(tmp_path):
    db = tmp_path / "tasks.db"
    q1 = TaskQueue(db)
    t = q1.create("persist")
    q2 = TaskQueue(db)  # re-run migrations on an existing DB
    assert q2.get(t.id).title == "persist"


def test_migration_adds_nullable_result_column_to_existing_db(tmp_path):
    db = tmp_path / "legacy.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE tasks (id TEXT PRIMARY KEY)")

    RealTaskQueue(db)

    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    assert {"result", "completed_by"} <= columns


def test_migration_backfills_stable_completing_owner(tmp_path):
    db = tmp_path / "legacy-completed.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE tasks ("
            "id TEXT PRIMARY KEY, status TEXT, result TEXT, result_ref TEXT)"
        )
        conn.execute(
            "INSERT INTO tasks VALUES ('t1', 'completed', NULL, 'artifact/1')"
        )
        conn.execute(
            "CREATE TABLE task_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,"
            "ts REAL NOT NULL, from_status TEXT, to_status TEXT,"
            "worker TEXT, note TEXT)"
        )
        conn.execute(
            "INSERT INTO task_events "
            "(task_id, ts, from_status, to_status, worker, note)"
            " VALUES ('t1', 1, 'started', 'completed', 'worker-1', 'complete')"
        )

    q = RealTaskQueue(db)

    assert q.get("t1").completed_by == "worker-1"
    assert q.complete("t1", "worker-1", result={"ok": True}).result == {
        "ok": True
    }


def test_legacy_completion_without_owner_fails_retry_fill_closed(tmp_path):
    db = tmp_path / "legacy-unowned.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, result TEXT)"
        )
        conn.execute("INSERT INTO tasks VALUES ('t1', 'completed', NULL)")

    q = RealTaskQueue(db)

    with pytest.raises(TaskError, match="no unambiguous completing owner"):
        q.complete("t1", "worker-1", result={"ok": True})


def test_legacy_completion_with_ambiguous_owners_fails_retry_fill_closed(tmp_path):
    db = tmp_path / "legacy-ambiguous.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "CREATE TABLE tasks (id TEXT PRIMARY KEY, status TEXT, result TEXT)"
        )
        conn.execute("INSERT INTO tasks VALUES ('t1', 'completed', NULL)")
        conn.execute(
            "CREATE TABLE task_events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL,"
            "ts REAL NOT NULL, from_status TEXT, to_status TEXT,"
            "worker TEXT, note TEXT)"
        )
        conn.executemany(
            "INSERT INTO task_events"
            " (task_id, ts, from_status, to_status, worker, note)"
            " VALUES ('t1', ?, 'started', 'completed', ?, 'complete')",
            [(1, "worker-1"), (2, "worker-2")],
        )

    q = RealTaskQueue(db)

    assert q.get("t1").completed_by is None
    with pytest.raises(TaskError, match="ambiguous completing owners"):
        q.complete("t1", "worker-1", result={"ok": True})
    assert q.get("t1").completed_by is None
    assert q.get("t1").result is None


def test_default_result_limit_is_conservative():
    assert DEFAULT_RESULT_MAX_BYTES == 64 * 1024


# -- audit trail -------------------------------------------------------------


def test_events_record_transitions(q):
    t = q.create("audited")
    q.claim_one("w1")
    q.start(t.id, "w1")
    q.complete(t.id, "w1")
    trail = [e["to_status"] for e in q.events(t.id)]
    assert trail == [Status.QUEUED, Status.CLAIMED, Status.STARTED, Status.COMPLETED]


# -- worker identity + targeting-in-claim ------------------------------------


def test_worker_id_for():
    assert worker_id_for("host-a", "wt-1") == "host-a/wt-1"


def test_claim_gated_by_target_machine(q):
    q.create("m1-only", target_machine="m1")
    assert q.claim_one("a", machine="m2", worktree="w") is None
    got = q.claim_one("a", machine="m1", worktree="w")
    assert got is not None and got.target_machine == "m1"


def test_machine_matches_case_insensitive():
    # Unset target -> machine-agnostic, matches anyone (including no machine).
    assert machine_matches(None, "anomalous-potato") is True
    assert machine_matches(None, None) is True
    # Same name in different case still matches (display_name vs identity).
    assert machine_matches("anomalous-potato", "Anomalous-Potato") is True
    assert machine_matches("Anomalous-Potato", "anomalous-potato") is True
    # Genuinely different machines don't match; a set target needs a machine.
    assert machine_matches("emancipation-cube", "anomalous-potato") is False
    assert machine_matches("anomalous-potato", None) is False


def test_claim_gated_by_target_machine_case_insensitive(q):
    # A task stored with a display-cased target_machine is still claimable by the
    # agent whose (canonical, lowercase) identity names the same machine.
    q.create("cased", target_machine="Anomalous-Potato")
    got = q.claim_one("a", machine="anomalous-potato", worktree="w")
    assert got is not None and got.target_machine == "Anomalous-Potato"


def test_list_target_machine_filter_case_insensitive(q):
    q.create("t", target_machine="anomalous-potato")
    assert len(q.list(target_machine="Anomalous-Potato")) == 1
    assert len(q.list(target_machine="anomalous-potato")) == 1
    assert len(q.list(target_machine="emancipation-cube")) == 0


def test_claim_gated_by_target_worktree(q):
    q.create("wtX-only", target_worktree="wtX")
    assert q.claim_one("a", machine="m", worktree="other") is None
    assert q.claim_one("a", machine="m", worktree="wtX") is not None


def test_untargeted_task_claimable_by_any_identity(q):
    q.create("open")
    assert q.claim_one("a", machine="m", worktree="w") is not None


def test_machineless_claimer_gets_only_untargeted(q):
    q.create("targeted", target_machine="m1")
    q.create("open")
    got = q.claim_one("a")  # no machine/worktree declared
    assert got.title == "open"


def test_claim_stamps_composite_owner(q):
    t = q.create("x")
    owner = worker_id_for("host-a", "wt-9")
    got = q.claim_one(owner, machine="host-a", worktree="wt-9", task_id=t.id)
    assert got.owner == "host-a/wt-9"


def test_mine_returns_assigned_and_owned(q):
    assigned = q.create("for-wt1", target_worktree="wt-1")
    machine_wide = q.create("for-machine", target_machine="host-a")
    to_own = q.create("to-own")
    q.claim_one(
        worker_id_for("host-a", "wt-1"),
        machine="host-a",
        worktree="wt-1",
        task_id=to_own.id,
    )
    q.create("open-to-all")  # untargeted -- not "assigned to me"

    inbox = q.mine("host-a", "wt-1")
    assigned_ids = {t.id for t in inbox["assigned"]}
    owned_ids = {t.id for t in inbox["owned"]}
    assert assigned.id in assigned_ids
    assert machine_wide.id in assigned_ids  # machine-wide, no worktree pin
    assert to_own.id in owned_ids
    assert all(t.title != "open-to-all" for t in inbox["assigned"])


def test_mine_owned_includes_suspended(q):
    t = q.create("dormant")
    q.claim_one(
        "host-a/wt-1",
        machine="host-a",
        worktree="wt-1",
        task_id=t.id,
    )
    q.start(t.id, "host-a/wt-1")
    q.suspend(t.id, "host-a/wt-1", reason="waiting")

    assert [task.id for task in q.mine("host-a", "wt-1")["owned"]] == [t.id]


def test_mine_matches_machine_wide_assignment_case_insensitively(q):
    # A machine-wide assignment stored display-cased is still "mine" when my
    # identity names the same machine in canonical (lowercase) form.
    machine_wide = q.create("for-machine", target_machine="Anomalous-Potato")
    inbox = q.mine("anomalous-potato", "wt-1")
    assert machine_wide.id in {t.id for t in inbox["assigned"]}


# -- browse: multi-status list + dedup sweep ---------------------------------


def _seed_all_states(q):
    """Create one task in each original non-dead-letter state."""
    proposed = q.propose("proposed one", prompt="p")
    queued = q.create("queued one", prompt="q")

    claimed_t = q.create("claimed one", prompt="c")
    q.claim_one("w", task_id=claimed_t.id)

    started_t = q.create("started one", prompt="s")
    q.claim_one("w", task_id=started_t.id)
    q.start(started_t.id, "w")

    completed_t = q.create("completed one", prompt="done")
    q.claim_one("w", task_id=completed_t.id)
    q.start(completed_t.id, "w")
    q.complete(completed_t.id, "w")

    abandoned_t = q.create("abandoned one", prompt="x")
    q.abandon(abandoned_t.id, permitted=True)

    return {
        Status.PROPOSED: proposed,
        Status.QUEUED: queued,
        Status.CLAIMED: claimed_t,
        Status.STARTED: started_t,
        Status.COMPLETED: completed_t,
        Status.ABANDONED: abandoned_t,
    }


def test_list_single_status_still_works(q):
    seed = _seed_all_states(q)
    got = q.list(status=Status.QUEUED)
    assert [t.id for t in got] == [seed[Status.QUEUED].id]


def test_list_accepts_multiple_statuses(q):
    seed = _seed_all_states(q)
    got = q.list(status=[Status.QUEUED, Status.STARTED])
    assert {t.id for t in got} == {seed[Status.QUEUED].id, seed[Status.STARTED].id}


def test_list_empty_status_sequence_matches_all(q):
    _seed_all_states(q)
    # An empty sequence adds no clause -> behaves like an unfiltered list.
    assert len(q.list(status=[])) == 6


def test_sweep_spans_all_states_except_abandoned(q):
    seed = _seed_all_states(q)
    swept = {t.id for t in q.sweep()}
    assert swept == {
        seed[s].id
        for s in (
            Status.PROPOSED,
            Status.QUEUED,
            Status.CLAIMED,
            Status.STARTED,
            Status.COMPLETED,
        )
    }
    assert seed[Status.ABANDONED].id not in swept


def test_sweep_includes_suspended(q):
    t = q.create("dormant")
    q.claim_one("w1", task_id=t.id)
    q.start(t.id, "w1")
    q.suspend(t.id, "w1", reason="waiting")
    assert t.id in {task.id for task in q.sweep()}


def test_sweep_is_newest_first(q):
    q.create("first")
    q.create("second")
    titles = [t.title for t in q.sweep()]
    assert titles[:2] == ["second", "first"]


# -- repo lane (scoping / isolation) -----------------------------------------


def test_create_requires_repo(tmp_path):
    q = RealTaskQueue(tmp_path / "t.db")  # no defaulting -- repo is mandatory
    with pytest.raises(TaskError):
        q.create("no lane")


def test_list_find_sweep_are_lane_scoped(q):
    a = q.create("alpha task", repo=TEST_REPO)
    b = q.create("beta task", repo=OTHER_REPO)
    assert [t.id for t in q.list(repo=TEST_REPO)] == [a.id]
    assert [t.id for t in q.sweep(repo=OTHER_REPO)] == [b.id]
    assert {t.id for t in q.find("task", repo=TEST_REPO)} == {a.id}
    # unscoped list still sees both (engine default; the CLI always scopes)
    assert {t.id for t in q.list()} == {a.id, b.id}


def test_claim_never_crosses_lanes(q):
    a = q.create("in my lane", repo=TEST_REPO)
    b = q.create("other lane", repo=OTHER_REPO)
    got = q.claim_one("w", repo=OTHER_REPO)
    assert got is not None and got.id == b.id  # never the TEST_REPO task
    assert q.get(a.id).status == Status.QUEUED  # untouched


def test_claim_by_id_respects_lane(q):
    a = q.create("mine", repo=TEST_REPO)
    # a worker in another lane can't claim it even by explicit id
    assert q.claim_one("w", repo=OTHER_REPO, task_id=a.id) is None
    # same lane succeeds
    got = q.claim_one("w", repo=TEST_REPO, task_id=a.id)
    assert got is not None and got.id == a.id


def test_mine_is_lane_scoped(q):
    here = q.create("for wt in my lane", repo=TEST_REPO, target_worktree="wt-1")
    other = q.create("for wt other lane", repo=OTHER_REPO, target_worktree="wt-1")
    inbox = q.mine("m", "wt-1", repo=TEST_REPO)
    ids = {t.id for t in inbox["assigned"]}
    assert here.id in ids and other.id not in ids


def test_sentinel_backfill_on_migration(tmp_path):
    import sqlite3

    db = tmp_path / "legacy.db"
    q = RealTaskQueue(db)
    t = q.create("legacy row", repo="temp")
    # Simulate a pre-repo row by nulling the lane, then reopen to re-migrate.
    con = sqlite3.connect(db)
    con.execute("UPDATE tasks SET repo = NULL WHERE id = ?", (t.id,))
    con.commit()
    con.close()
    q2 = RealTaskQueue(db)
    assert q2.get(t.id).repo == LEGACY_REPO
