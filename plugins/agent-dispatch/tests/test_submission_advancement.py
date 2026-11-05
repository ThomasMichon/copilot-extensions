"""Guarded submission retains the task claim and original native return binding."""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_dispatch import submission_readiness_observation as observation
from agent_dispatch import queue_submission_advancement as advancement
from agent_dispatch.procutil import run_background_capture
from agent_dispatch.queue import Status, TaskError, TaskQueue
from tests import test_submission_readiness as readiness_controls

ready = readiness_controls.ready
retained = readiness_controls.retained


@pytest.mark.parametrize("evaluator", ["review", None])
def test_fresh_guarded_submission_retains_custody_and_replays_after_restart(ready, evaluator):
    queue, owner, task, proposal, _, _, _, calls = ready
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET evaluator_ref=? WHERE id=?", (evaluator, task.id))
    relationship = queue.task_relationship(task.id)
    reservations = queue.list_reservations(task_id=task.id)
    assignment = queue.task_pool_assignment(task.id)
    outcome = queue.advance_prepared_submission(task.id, request_id="proposal")
    submitted = outcome.task
    assert outcome.event_type == "task.submitted" and submitted.status == Status.SUBMITTED
    assert submitted.owner is None and submitted.lease_expires_at is None
    assert (submitted.completed_by, submitted.owner_session_id, submitted.generation) == (
        task.owner, task.owner_session_id, task.generation,
    )
    assert submitted.result == proposal["request"]["result"]
    assert queue.task_relationship(task.id) == relationship
    assert queue.list_reservations(task_id=task.id) == reservations
    assert queue.task_pool_assignment(task.id) == assignment and len(owner.claims) == 1
    assert len(calls) == 3
    receipt = queue.submission_preparation(task.id)
    assert receipt["request"] == proposal["request"]
    assert receipt["advancement"]["state"] == "submitted"
    with queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM verification_requests").fetchone()[0] == (
            1 if evaluator else 0
        )
    restarted = TaskQueue(queue.db_path)
    before = retained(restarted, submitted)
    replay = restarted.advance_prepared_submission(task.id, request_id="proposal")
    assert replay.task == submitted and replay.event_type is None
    assert retained(restarted, submitted) == before and len(calls) == 3
    confirmed = restarted.confirm(task.id, actor="independent reviewer")
    assert confirmed.status == Status.COMPLETED
    assert restarted.advance_prepared_submission(task.id, request_id="proposal").event_type is None


def test_self_attestation_policy_is_not_silently_upgraded_or_used(ready):
    queue, _, task, _, _, _, _, calls = ready
    with queue._connect() as conn:
        conn.execute("UPDATE tasks SET require_verification=0 WHERE id=?", (task.id,))
    before = retained(queue, task)
    with pytest.raises(TaskError, match="independent review policy"):
        queue.advance_prepared_submission(task.id, request_id="proposal")
    assert retained(queue, task) == before and not calls


@pytest.mark.parametrize("field,value", [
    ("ready", False), ("task_release", True), ("head_revision", 8),
])
def test_unqualified_owner_read_never_arms_submission(ready, field, value):
    queue, _, task, _, _, _, reply, _ = ready
    reply[field] = value
    before = retained(queue, task)
    with pytest.raises(TaskError, match="outcome changed"):
        queue.advance_prepared_submission(task.id, request_id="proposal")
    assert retained(queue, task) == before


@pytest.mark.parametrize("change", ["hold", "steer", "native", "policy", "result", "allocation"])
def test_owner_read_is_outside_lock_and_semantic_change_refuses_atomically(
    ready, monkeypatch, change,
):
    queue, _, task, _, _, _, _, calls = ready
    run = observation.procutil.run_background_capture

    def changed(argv, **kwargs):
        response = run(argv, **kwargs)
        if "--readiness-operation" in argv:
            with queue._connect() as conn:
                statement = {
                    "hold": "UPDATE tasks SET hold_reason='inspection' WHERE id=?",
                    "steer": "UPDATE tasks SET awaiting_steer=1 WHERE id=?",
                    "native": "UPDATE tasks SET owner_session_id='replacement' WHERE id=?",
                    "policy": "UPDATE tasks SET evaluator_ref='other' WHERE id=?",
                    "result": "UPDATE tasks SET result_ref='changed' WHERE id=?",
                    "allocation": "UPDATE spawn_reservations SET reserved_by='other' WHERE task_id=?",
                }[change]
                conn.execute(statement, (task.id,))
        return response

    monkeypatch.setattr(observation.procutil, "run_background_capture", changed)
    with pytest.raises(TaskError):
        queue.advance_prepared_submission(task.id, request_id="proposal")
    assert len(calls) == 3 and queue.get(task.id).status == Status.STARTED
    assert "advancement" not in queue.submission_preparation(task.id)
    with queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM verification_requests").fetchone()[0] == 0


@pytest.mark.parametrize("fault", ["before_write", "after_update", "verification"])
def test_failed_transaction_leaves_no_committed_permission_or_partial_submission(
    ready, monkeypatch, fault,
):
    queue, _, task, _, _, _, _, _ = ready
    before = retained(queue, task)
    method = {
        "before_write": "_write_submission",
        "after_update": "_audit",
        "verification": "_insert_verification_request",
    }[fault]

    def fail(*args, **kwargs):
        raise RuntimeError("interrupted transaction")

    with monkeypatch.context() as patch:
        patch.setattr(queue, method, fail)
        with pytest.raises(RuntimeError, match="interrupted transaction"):
            queue.advance_prepared_submission(task.id, request_id="proposal")
    restarted = TaskQueue(queue.db_path)
    assert retained(restarted, task) == before
    with restarted._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="readiness"):
        conn.execute("UPDATE tasks SET status='submitted' WHERE id=?", (task.id,))
    assert restarted.advance_prepared_submission(
        task.id, request_id="proposal",
    ).task.status == Status.SUBMITTED


def test_consumed_receipt_cannot_be_rearmed_or_reused_after_rejection(ready):
    queue, _, task, _, _, _, _, calls = ready
    submitted = queue.advance_prepared_submission(task.id, request_id="proposal").task
    receipt = queue.submission_preparation(task.id)
    receipt["advancement"]["state"] = "admitting"
    with queue._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute(
            "UPDATE task_events SET submission_preparation=?"
            " WHERE task_id=? AND submission_preparation IS NOT NULL",
            (json.dumps(receipt), task.id),
        )
    rejected = queue.reject_submission(
        task.id, reason="More work required",
        expected_generation=submitted.generation,
        expected_owner_session_id=submitted.owner_session_id,
        expected_completed_by=submitted.completed_by,
        expected_updated_at=submitted.updated_at,
    ).task
    assert rejected.status == Status.STARTED and rejected.owner == task.owner
    before = retained(queue, rejected)
    with pytest.raises(TaskError, match="no longer matches"):
        queue.advance_prepared_submission(task.id, request_id="proposal")
    with queue._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="readiness"):
        conn.execute("UPDATE tasks SET status='submitted' WHERE id=?", (task.id,))
    assert retained(queue, rejected) == before and len(calls) == 3


@pytest.mark.parametrize("ready", [None], indirect=True)
def test_prepared_absent_result_cannot_be_enriched_by_legacy_completion(ready, tmp_path):
    queue, _, task, _, _, _, _, _ = ready
    legacy = TaskQueue(tmp_path / "legacy.db")
    other = legacy.create("legacy control", repo="example")
    legacy.claim_one("legacy", task_id=other.id)
    legacy.start(other.id, "legacy")
    legacy.complete(other.id, "legacy")
    legacy.complete(other.id, "legacy", result={"answer": "legacy retry"})
    assert legacy.get(other.id).result == {"answer": "legacy retry"}
    submitted = queue.advance_prepared_submission(task.id, request_id="proposal").task
    assert submitted.result is None
    with pytest.raises(TaskError, match="authoritative readiness"):
        queue.complete(task.id, task.owner, result={"late": "replacement"})
    with queue._connect() as conn, pytest.raises(sqlite3.IntegrityError, match="retained"):
        conn.execute("UPDATE tasks SET result='{}' WHERE id=?", (task.id,))
    with pytest.raises(TaskError, match="request changed"):
        queue.advance_prepared_submission(task.id, request_id="replacement")


def test_old_migrator_remains_safe_and_current_reopen_restores_guarded_path(ready):
    queue, _, task, _, _, _, _, _ = ready
    with queue._connect() as conn:
        conn.execute(
            "CREATE TRIGGER submission_preparation_completion_guard"
            " BEFORE UPDATE OF status ON tasks"
            " WHEN NEW.status IN ('submitted','completed')"
            " AND OLD.status NOT IN ('submitted','completed')"
            " AND EXISTS (SELECT 1 FROM task_events e WHERE e.task_id=NEW.id"
            " AND e.submission_preparation IS NOT NULL)"
            " BEGIN SELECT RAISE(ABORT, 'prior readiness guard'); END"
        )
    before = retained(queue, task)
    with pytest.raises(sqlite3.IntegrityError, match="prior readiness guard"):
        queue.advance_prepared_submission(task.id, request_id="proposal")
    assert retained(queue, task) == before
    restarted = TaskQueue(queue.db_path)
    assert restarted.advance_prepared_submission(
        task.id, request_id="proposal",
    ).task.status == Status.SUBMITTED


def test_concurrent_submissions_have_one_transition_and_one_review_request(ready, monkeypatch):
    queue, _, task, _, _, _, _, _ = ready
    barrier = threading.Barrier(2)
    run = observation.procutil.run_background_capture

    def observed(argv, **kwargs):
        response = run(argv, **kwargs)
        if "--readiness-operation" in argv:
            barrier.wait(timeout=5)
        return response

    monkeypatch.setattr(observation.procutil, "run_background_capture", observed)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(
            lambda _: queue.advance_prepared_submission(task.id, request_id="proposal"),
            range(2),
        ))
    assert sorted(str(result.event_type) for result in results) == ["None", "task.submitted"]
    assert results[0].task == results[1].task
    with queue._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM verification_requests").fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM task_events WHERE task_id=? AND to_status='submitted'",
            (task.id,),
        ).fetchone()[0] == 1


def test_process_exit_after_arming_rolls_back_the_admission(ready):
    queue, _, task, _, _, _, _, _ = ready
    before = retained(queue, task)
    result = run_background_capture(
        [
            sys.executable, "-c",
            """
import json, os, sys
from agent_dispatch.queue import TaskQueue
from agent_dispatch import queue_submission_advancement as advancement
queue = TaskQueue(sys.argv[1])
advancement.observe_owner_state = lambda *args: {}
def exit_after_arm(conn, task, **kwargs):
    row = conn.execute(
        "SELECT submission_preparation FROM task_events"
        " WHERE task_id=? AND submission_preparation IS NOT NULL", (task.id,),
    ).fetchone()
    assert json.loads(row[0])["advancement"]["state"] == "admitting"
    os._exit(23)
queue._write_submission = exit_after_arm
queue.advance_prepared_submission(sys.argv[2], request_id="proposal")
""",
            str(queue.db_path), task.id,
        ],
        timeout=15,
        env=dict(os.environ),
    )
    assert result.returncode == 23
    restarted = TaskQueue(queue.db_path)
    assert retained(restarted, task) == before
    assert restarted.advance_prepared_submission(
        task.id, request_id="proposal",
    ).task.status == Status.SUBMITTED


def test_suspended_submission_retires_waiter_but_not_task_claim_or_native(ready):
    queue, owner, task, _, _, _, _, _ = ready
    queue.suspend(task.id, task.owner, reason="external wait")
    queue.register_run_waiter(
        task.id, pid=101, host="host-a", start_token="private-fixture",
        resume_worktree=task.owner, command=["wait"],
    )
    relationship = queue.task_relationship(task.id)
    notifications = []
    queue._wake_notifier = lambda: notifications.append("wake")
    queue._verification_notifier = lambda: notifications.append("verification")
    submitted = queue.advance_prepared_submission(task.id, request_id="proposal").task
    assert submitted.status == Status.SUBMITTED and submitted.owner_session_id == "native"
    assert queue.task_relationship(task.id) == relationship and len(owner.claims) == 1
    with queue._connect() as conn:
        assert conn.execute("SELECT state FROM run_waiters").fetchone()[0] == "retired"
    assert len(queue.list_run_waiter_wakes(task.id)) == 1
    assert notifications == ["verification", "wake"]


def test_heartbeat_during_advance_is_not_a_different_proposal(ready, monkeypatch):
    queue, _, task, _, _, _, _, _ = ready
    run = observation.procutil.run_background_capture

    def heartbeat(argv, **kwargs):
        response = run(argv, **kwargs)
        if "--readiness-operation" in argv:
            queue.heartbeat(task.id, task.owner, now=task.updated_at + 1)
        return response

    monkeypatch.setattr(observation.procutil, "run_background_capture", heartbeat)
    assert queue.advance_prepared_submission(
        task.id, request_id="proposal",
    ).task.status == Status.SUBMITTED


@pytest.mark.parametrize("field,value", [
    ("encoded_result", '{"other":true}'), ("result_ref", "other"),
    ("worker_id", "other"), ("ts", 1.0),
])
def test_persisted_admission_is_bound_to_the_exact_submission(ready, monkeypatch, field, value):
    queue, _, task, _, _, _, _, _ = ready
    before = retained(queue, task)
    write = queue._write_submission

    def changed(conn, task, **kwargs):
        kwargs[field] = value
        return write(conn, task, **kwargs)

    monkeypatch.setattr(queue, "_write_submission", changed)
    with pytest.raises(sqlite3.IntegrityError, match="authoritative readiness"):
        queue.advance_prepared_submission(task.id, request_id="proposal")
    assert retained(queue, task) == before


def test_missing_consumption_never_commits_a_reusable_permission(ready):
    queue, _, task, _, _, _, _, _ = ready
    before = retained(queue, task)
    with queue._connect() as conn:
        conn.execute("DROP TRIGGER submission_preparation_consume_admission")
    with pytest.raises(TaskError, match="not consumed"):
        queue.advance_prepared_submission(task.id, request_id="proposal")
    assert retained(queue, task) == before
    restarted = TaskQueue(queue.db_path)
    assert restarted.advance_prepared_submission(
        task.id, request_id="proposal",
    ).task.status == Status.SUBMITTED


@pytest.mark.parametrize("owner_failure", [False, True])
def test_identical_commit_between_replay_and_snapshot_is_a_replay_not_an_owner_error(
    ready, monkeypatch, owner_failure,
):
    queue, _, task, _, _, _, _, calls = ready
    replay = advancement._replay
    observe = advancement.observe_owner_state
    concurrent = []

    def commit_between_reads(queue, conn, task_id, request_id):
        result = replay(queue, conn, task_id, request_id)
        if result is None and not concurrent:
            concurrent.append(None)
            concurrent[0] = queue.advance_prepared_submission(task_id, request_id=request_id)
        return result

    def owner_read(task_id, original):
        if owner_failure and concurrent and concurrent[0] is not None:
            raise TaskError("owner stopped after concurrent submission")
        return observe(task_id, original)

    monkeypatch.setattr(advancement, "_replay", commit_between_reads)
    monkeypatch.setattr(advancement, "observe_owner_state", owner_read)
    outcome = queue.advance_prepared_submission(task.id, request_id="proposal")
    assert outcome.task == concurrent[0].task
    assert outcome.event_type is None and concurrent[0].event_type == "task.submitted"
    assert len(calls) == (3 if owner_failure else 6)
    assert len(queue.list_verification_requests(task.id)) == 1


@pytest.mark.parametrize("confirm_first", [False, True])
@pytest.mark.parametrize("prior_guard", [False, True])
def test_qualified_retirement_detachment_preserves_original_receipt_and_output(
    ready, confirm_first, prior_guard,
):
    queue, owner, task, _, _, _, _, _ = ready
    submitted = queue.advance_prepared_submission(task.id, request_id="proposal").task
    if confirm_first:
        queue.confirm(task.id)
    original = queue.submission_preparation(task.id)
    relationship = queue.task_relationship(task.id)
    snapshot = queue.task_diagnostics(task.id)
    assert not snapshot["actions"]["detach_agent"]["allowed"]
    with pytest.raises(TaskError, match="retained reservation|native"):
        queue.recover_task(
            task.id, operation="detach_agent", expected_revision=snapshot["revision"],
            request_id="too-early", actor="operator", reason="not yet retired",
        )
    reservation = queue.latest_reservation(task.id)
    queue.request_spawn_release(reservation.key, disposition="settled")
    queue.retire_spawn(reservation.key, exact_absence=True)
    if prior_guard:
        with queue._connect() as conn:
            conn.execute(
                "CREATE TRIGGER submission_preparation_result_retained"
                " BEFORE UPDATE ON tasks WHEN NEW.owner_session_id IS NOT OLD.owner_session_id"
                " OR NEW.generation IS NOT OLD.generation"
                " BEGIN SELECT RAISE(ABORT, 'prior result guard'); END"
            )
        queue = TaskQueue(queue.db_path, relationship_writer=owner, relationship_authority="local")
        owner.queue = queue
    snapshot = queue.task_diagnostics(task.id)
    assert snapshot["actions"]["detach_agent"]["allowed"]
    detached = queue.recover_task(
        task.id, operation="detach_agent", expected_revision=snapshot["revision"],
        request_id="detach-retired-native", actor="operator", reason="qualified retirement",
    )["task"]
    assert detached["generation"] == submitted.generation + 1
    assert detached["owner_session_id"] is None
    assert queue.get(task.id).result == submitted.result
    assert queue.submission_preparation(task.id) == original
    assert queue.task_relationship(task.id) == relationship and len(owner.claims) == 1
    if not confirm_first:
        assert queue.get(task.id).status == Status.SUBMITTED
        assert queue.reconcile_task_relationship(task.id)["state"] == "bound"
        queue.confirm(task.id)
    assert queue.reconcile_task_relationship(task.id)["state"] == "closed"
    with pytest.raises(TaskError, match="no longer matches"):
        queue.advance_prepared_submission(task.id, request_id="proposal")
