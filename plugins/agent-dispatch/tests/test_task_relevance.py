"""Relevance ownership decisions, not source adapters or native closeout proof."""

from dataclasses import replace

import pytest

from agent_dispatch.queue import Status, TaskError
from agent_dispatch.queue_relevance import RelevanceScope
from tests._helpers import RepoDefaultingQueue as TaskQueue, TEST_REPO


SCOPE = RelevanceScope(TEST_REPO, "emitter", "review-source")


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


def task(q, **kwargs):
    return q.create("Review a source object", source=SCOPE.source, origin_ref=SCOPE.origin_ref, **kwargs)


def request(q, task_id, **kwargs):
    snapshot = q.relevance_snapshot(task_id)
    return {
        "request_id": "closed-source", "scope": SCOPE,
        "expected_generation": snapshot["generation"], "expected_revision": snapshot["revision"],
        "verdict": "obsolete", "reason": "Backing object is closed",
        "observation_ref": "provider-object:closed:revision-2", "reporter": "source-evaluator",
        **kwargs,
    }


def report(q, task_id, **kwargs):
    return q.reconcile_task_relevance(task_id, **request(q, task_id, **kwargs))


@pytest.mark.parametrize("status", [Status.PROPOSED, Status.QUEUED])
def test_source_closure_before_claim_abandons_without_execution_or_steering(q, status):
    t = task(q, status=status)
    result = report(q, t.id)
    assert result["decision"] == "abandoned"
    assert q.get(t.id).status == Status.ABANDONED
    assert q.claim_one("host/worker", task_id=t.id) is None
    with pytest.raises(TaskError, match="spawn reservation requires"):
        q.reserve_spawn(t.id)
    assert q.list_reservations(task_id=t.id) == []
    assert q.steer_log(t.id) == q.list_wakes(t.id) == []
    assert q.relevance_receipt(t.id, "closed-source")["acceptance"] == result


@pytest.mark.parametrize("change", ["claim", "reservation", "prepared_workspace"])
def test_claim_or_pending_preparation_prevents_resource_free_abandonment(q, change):
    t = task(q)
    if change == "claim":
        q.claim_one("host/worker", task_id=t.id)
    else:
        reservation, _ = q.reserve_spawn(t.id)
        if change == "prepared_workspace":
            q.record_spawn_worktree(reservation.key, "original-worktree", ownership="created")
    before = q.get(t.id)
    reservations = q.list_reservations(task_id=t.id)
    result = report(q, t.id)
    assert result["decision"] == "closeout_pending"
    assert q.get(t.id) == before
    assert q.list_reservations(task_id=t.id) == reservations
    assert len(q.steer_log(t.id)) == 1
    assert q.list_wakes(t.id) == []
    assert q.relevance_snapshot(t.id)["closeout"]["steer_id"] == result["steer_id"]
    with pytest.raises(TaskError, match="requires owner closeout"):
        q.reserve_spawn(t.id)
    if change == "claim":
        with pytest.raises(TaskError, match="requires owner closeout"):
            q.start(t.id, "host/worker")


def test_released_claim_and_cleared_current_fields_do_not_erase_assignment_history(q):
    t = task(q)
    q.claim_one("host/worker", task_id=t.id)
    q.yield_task(t.id, "host/worker")
    # Legacy recovery could clear mutable counters; the audit still proves a claim.
    with q._connect() as conn:
        conn.execute(
            "UPDATE tasks SET generation=0, attempts=0, claimed_at=NULL, started_at=NULL WHERE id=?",
            (t.id,),
        )
    result = report(q, t.id)
    assert result["decision"] == "closeout_pending"
    assert "assignment_history" in result["custody"]
    assert q.claim_one("host/replacement", task_id=t.id) is None


@pytest.mark.parametrize("status", [Status.STARTED, Status.SUSPENDED, Status.SUBMITTED])
def test_bound_closeout_keeps_original_owner_session_and_actual_lifecycle(q, status):
    t = task(q, require_verification=status == Status.SUBMITTED)
    q.claim_one("host/worker", task_id=t.id)
    q.start(t.id, "host/worker", owner_session_id="original-native")
    if status == Status.SUSPENDED:
        q.suspend(t.id, "host/worker", reason="Waiting on the source")
    elif status == Status.SUBMITTED:
        q.complete(t.id, "host/worker")
    before = q.get(t.id)
    result = report(q, t.id)
    assert result["decision"] == "closeout_pending"
    current = q.get(t.id)
    assert replace(
        current, wake_seq=before.wake_seq, wake_operation_id=before.wake_operation_id,
        wake_status=before.wake_status,
    ) == before
    assert result["owner_session_id"] == "original-native"
    assert result["delivery"] == "pending"
    receipt = q.relevance_receipt(t.id, "closed-source")
    assert receipt["closeout"]["owner_session_id"] == "original-native"
    assert receipt["steer_consumed_at"] is None
    if status == Status.STARTED:
        steer = q.take_steer(t.id, "host/worker", now=1234)
        assert steer["fields"]["task_relevance"]["request_id"] == "closed-source"
        assert q.relevance_receipt(t.id, "closed-source")["steer_consumed_at"] == 1234
        assert q.get(t.id).status == Status.STARTED
    elif status == Status.SUSPENDED:
        with pytest.raises(TaskError, match="requires owner closeout"):
            q.resume(t.id, "host/worker")


def test_pending_closeout_survives_restart_and_requeue_without_restarting_goal(q):
    t = task(q)
    q.claim_one("host/worker", task_id=t.id)
    q.start(t.id, "host/worker", owner_session_id="original-native")
    report(q, t.id)
    q.yield_task(t.id, "host/worker")
    q.reset(t.id)
    q.approve(t.id)
    restarted = TaskQueue(q.db_path)
    assert restarted.relevance_snapshot(t.id)["closeout"]["owner_session_id"] == "original-native"
    assert restarted.claim_one("host/replacement", task_id=t.id) is None
    with pytest.raises(TaskError, match="requires owner closeout"):
        restarted.reserve_spawn(t.id)
    assert len(restarted.steer_log(t.id)) == 1


@pytest.mark.parametrize("claimed", [False, True])
def test_failed_receipt_commit_rolls_back_both_decision_and_steering(q, claimed):
    t = task(q)
    if claimed:
        q.claim_one("host/worker", task_id=t.id)
    before = q.get(t.id), q.events(t.id)
    with q._connect() as conn:
        conn.execute(
            "CREATE TRIGGER reject_relevance_receipt BEFORE INSERT ON task_relevance_requests"
            " BEGIN SELECT RAISE(ABORT, 'injected storage failure'); END"
        )
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError, match="injected storage failure"):
        report(q, t.id)
    assert (q.get(t.id), q.events(t.id)) == before
    assert q.steer_log(t.id) == []
    assert q.relevance_snapshot(t.id)["closeout"] is None


def test_exact_retry_retains_original_acceptance_after_consumption_and_restart(q):
    t = task(q)
    q.claim_one("host/worker", task_id=t.id)
    q.start(t.id, "host/worker", owner_session_id="original-native")
    original_request = request(q, t.id)
    accepted = q.reconcile_task_relevance(t.id, **original_request, now=1000)
    q.take_steer(t.id, "host/worker")
    restarted = TaskQueue(q.db_path)
    before = restarted.events(t.id)
    assert restarted.reconcile_task_relevance(t.id, **original_request, now=2000) == accepted
    assert restarted.events(t.id) == before
    assert len(restarted.steer_log(t.id)) == 1
    with pytest.raises(TaskError, match="request key conflicts"):
        restarted.reconcile_task_relevance(t.id, **{**original_request, "reason": "different"})
    # A separate report must not create another closeout or steer either.
    assert report(restarted, t.id, request_id="second-report")["steer_id"] == accepted["steer_id"]
    assert len(restarted.steer_log(t.id)) == 1


@pytest.mark.parametrize("verdict", ["needed", "unknown"])
def test_nonclosure_never_retires_and_invalidates_older_source_report(q, verdict):
    t = task(q)
    stale = request(q, t.id)
    before = q.get(t.id)
    assert report(q, t.id, verdict=verdict, request_id="newer-source")["decision"] == "unchanged"
    assert q.get(t.id) == before
    with pytest.raises(TaskError, match="revision changed"):
        q.reconcile_task_relevance(t.id, **stale)
    assert q.steer_log(t.id) == []
    assert q.relevance_snapshot(t.id)["closeout"] is None


@pytest.mark.parametrize("field", ["repo", "source", "origin_ref"])
def test_foreign_source_scope_is_rejected_without_receipt_or_effect(q, field):
    t = task(q)
    with pytest.raises(TaskError, match="scope does not match"):
        report(q, t.id, scope=replace(SCOPE, **{field: "different"}))
    assert q.relevance_receipt(t.id, "closed-source") is None
    assert q.get(t.id).status == Status.QUEUED


def test_task_revision_and_generation_are_fenced_under_acceptance_lock(q):
    t = task(q)
    original = request(q, t.id)
    q.claim_one("host/worker", task_id=t.id)
    with pytest.raises(TaskError, match="revision changed"):
        q.reconcile_task_relevance(t.id, **original)
    assert q.relevance_receipt(t.id, "closed-source") is None
    assert report(q, t.id)["decision"] == "closeout_pending"


def test_independent_hold_and_terminal_history_are_not_overridden(q):
    held = task(q)
    q.set_hold(held.id, reason="operator decision", actor="operator")
    before = q.get(held.id)
    result = report(q, held.id)
    assert result["decision"] == "closeout_pending" and result["delivery"] == "held"
    assert q.get(held.id) == before
    assert len(q.steer_log(held.id)) == 1
    q.clear_hold(held.id)
    assert q.claim_one("host/worker", task_id=held.id) is None
    terminal = task(q)
    q.abandon(terminal.id, permitted=True, reason="already retired")
    before = q.get(terminal.id)
    assert report(q, terminal.id)["decision"] == "unchanged"
    assert q.get(terminal.id) == before


def test_needed_does_not_silently_clear_accepted_closeout(q):
    t = task(q)
    q.claim_one("host/worker", task_id=t.id)
    report(q, t.id)
    accepted = q.relevance_snapshot(t.id)["closeout"]
    assert report(q, t.id, verdict="needed", request_id="source-reopened")["decision"] == "closeout_pending"
    assert q.relevance_snapshot(t.id)["closeout"] == accepted
    assert len(q.steer_log(t.id)) == 1


def test_actual_relevance_history_queries_are_indexed_by_task(q, monkeypatch):
    t = task(q)
    queries = []
    connect = q._connect

    def traced_connection():
        conn = connect()
        conn.set_trace_callback(queries.append)
        return conn

    monkeypatch.setattr(q, "_connect", traced_connection)
    q.relevance_snapshot(t.id)
    history = [
        query for query in queries
        if query.startswith("SELECT ") and "WHERE task_id=" in query
    ]
    assert history
    with connect() as conn:
        for query in history:
            plan = conn.execute("EXPLAIN QUERY PLAN " + query).fetchall()
            assert any("task_id=?" in row["detail"] for row in plan), (query, list(map(dict, plan)))
