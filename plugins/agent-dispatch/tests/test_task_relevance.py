"""Relevance ownership decisions, not source adapters or native closeout proof."""

import sqlite3
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from agent_dispatch.queue import TaskQueue as BaseQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import Status, TaskError
from agent_dispatch.queue_relevance import RelevanceScope
from tests._helpers import RepoDefaultingQueue as TaskQueue, TEST_REPO


SCOPE = RelevanceScope(TEST_REPO, "emitter", "review-source")


@pytest.fixture
def q(tmp_path: Path) -> TaskQueue:
    return TaskQueue(tmp_path / "tasks.db")


def task(q: BaseQueue, **kwargs: Any) -> Task:
    created = q.create("Review a source object", source=SCOPE.source, origin_ref=SCOPE.origin_ref, **kwargs)
    assert isinstance(created, Task)
    return created


def current(q: BaseQueue, task_id: str) -> Task:
    found = q.get(task_id)
    assert found is not None
    return found


def receipt(q: BaseQueue, task_id: str, request_id: str = "closed-source") -> dict[str, Any]:
    found = q.relevance_receipt(task_id, request_id)
    assert found is not None
    return found


def request(q: BaseQueue, task_id: str, **kwargs: Any) -> dict[str, Any]:
    snapshot = q.relevance_snapshot(task_id)
    return {
        "request_id": "closed-source", "scope": SCOPE,
        "expected_generation": snapshot["generation"], "expected_revision": snapshot["revision"],
        "verdict": "obsolete", "reason": "Backing object is closed",
        "observation_ref": "provider-object:closed:revision-2", "reporter": "source-evaluator",
        **kwargs,
    }


def report(q: BaseQueue, task_id: str, **kwargs: Any) -> dict[str, Any]:
    return q.reconcile_task_relevance(task_id, **request(q, task_id, **kwargs))


@pytest.mark.parametrize("status", [Status.PROPOSED, Status.QUEUED])
def test_source_closure_before_claim_abandons_without_execution_or_steering(q: BaseQueue, status: str) -> None:
    t = task(q, status=status)
    result = report(q, t.id)
    assert result["decision"] == "abandoned"
    assert current(q, t.id).status == Status.ABANDONED
    assert q.claim_one("host/worker", task_id=t.id) is None
    with pytest.raises(TaskError, match="spawn reservation requires"):
        q.reserve_spawn(t.id)
    assert q.list_reservations(task_id=t.id) == []
    assert q.steer_log(t.id) == []
    assert q.list_wakes(t.id) == []
    assert receipt(q, t.id)["acceptance"] == result


@pytest.mark.parametrize("change", ["reservation", "prepared_workspace"])
def test_pending_preparation_prevents_resource_free_abandonment(q: BaseQueue, change: str) -> None:
    t = task(q)
    reservation, _ = q.reserve_spawn(t.id)
    if change == "prepared_workspace":
        q.record_spawn_worktree(reservation.key, "original-worktree", ownership="created")
    before = current(q, t.id)
    reservations = q.list_reservations(task_id=t.id)
    result = report(q, t.id)
    assert result["decision"] == "closeout_pending"
    assert q.get(t.id) == before
    assert [*q.list_reservations(task_id=t.id)] == [*reservations]
    assert len(q.steer_log(t.id)) == 1
    assert q.list_wakes(t.id) == []
    assert q.relevance_snapshot(t.id)["closeout"]["steer_id"] == result["steer_id"]
    with pytest.raises(TaskError, match="requires owner closeout"):
        q.reserve_spawn(t.id)


def test_claiming_worker_starts_only_to_close_out(q: BaseQueue) -> None:
    t = task(q)
    q.claim_one("host/worker", task_id=t.id)
    result = report(q, t.id)
    assert result["decision"] == "closeout_pending"
    assert "worker_assigned" in result["custody"]
    started = q.start(t.id, "host/worker", owner_session_id="closing-native")
    assert started.status == Status.STARTED
    closeout = receipt(q, t.id)["closeout"]
    assert closeout["owner_session_id"] == "closing-native"


def test_released_claim_history_is_not_live_custody(q: BaseQueue) -> None:
    t = task(q)
    q.claim_one("host/worker", task_id=t.id)
    q.yield_task(t.id, "host/worker")
    result = report(q, t.id)
    assert result["decision"] == "abandoned"
    assert current(q, t.id).status == Status.ABANDONED
    assert q.claim_one("host/replacement", task_id=t.id) is None


@pytest.mark.parametrize("status", [Status.STARTED, Status.SUSPENDED, Status.SUBMITTED])
def test_bound_closeout_keeps_original_owner_session_and_actual_lifecycle(q: BaseQueue, status: str) -> None:
    t = task(q, require_verification=status == Status.SUBMITTED)
    q.claim_one("host/worker", task_id=t.id)
    q.start(t.id, "host/worker", owner_session_id="original-native")
    if status == Status.SUSPENDED:
        q.suspend(t.id, "host/worker", reason="Waiting on the source")
    elif status == Status.SUBMITTED:
        q.complete(t.id, "host/worker")
    before = current(q, t.id)
    result = report(q, t.id)
    assert result["decision"] == "closeout_pending"
    now_task = current(q, t.id)
    assert replace(
        now_task, wake_seq=before.wake_seq, wake_operation_id=before.wake_operation_id,
        wake_status=before.wake_status,
    ) == before
    assert result["owner_session_id"] == "original-native"
    assert result["delivery"] == "pending"
    accepted_receipt = receipt(q, t.id)
    assert accepted_receipt["closeout"]["owner_session_id"] == "original-native"
    assert accepted_receipt["steer_consumed_at"] is None
    if status == Status.STARTED:
        steer = q.take_steer(t.id, "host/worker", now=1234)
        assert isinstance(steer, dict)
        assert steer["fields"]["task_relevance"]["request_id"] == "closed-source"
        assert receipt(q, t.id)["steer_consumed_at"] == 1234
        assert current(q, t.id).status == Status.STARTED
    elif status == Status.SUSPENDED:
        # Relevance never blocks the owner's resume: it resumes only to close out.
        assert q.resume(t.id, "host/worker").status == Status.STARTED
        assert receipt(q, t.id)["closeout"]["owner_session_id"] == "original-native"


def test_pending_closeout_survives_restart_and_requeue_without_restarting_goal(q: BaseQueue) -> None:
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
def test_failed_receipt_commit_rolls_back_both_decision_and_steering(q: BaseQueue, claimed: bool) -> None:
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


def test_exact_retry_retains_original_acceptance_after_consumption_and_restart(q: BaseQueue) -> None:
    t = task(q)
    q.claim_one("host/worker", task_id=t.id)
    q.start(t.id, "host/worker", owner_session_id="original-native")
    original_request = request(q, t.id)
    accepted = q.reconcile_task_relevance(t.id, **original_request, now=1000)
    q.take_steer(t.id, "host/worker")
    restarted = TaskQueue(q.db_path)
    before = restarted.events(t.id)
    assert restarted.reconcile_task_relevance(t.id, **original_request, now=2000) == accepted
    assert [*restarted.events(t.id)] == [*before]
    assert len(restarted.steer_log(t.id)) == 1
    with pytest.raises(TaskError, match="request key conflicts"):
        restarted.reconcile_task_relevance(t.id, **{**original_request, "reason": "different"})
    # A separate report must not create another closeout or steer either.
    assert report(restarted, t.id, request_id="second-report")["steer_id"] == accepted["steer_id"]
    assert len(restarted.steer_log(t.id)) == 1


@pytest.mark.parametrize("verdict", ["needed", "unknown"])
def test_nonclosure_never_retires_and_invalidates_older_source_report(q: BaseQueue, verdict: str) -> None:
    t = task(q)
    stale = request(q, t.id)
    before = current(q, t.id)
    assert report(q, t.id, verdict=verdict, request_id="newer-source")["decision"] == "unchanged"
    assert q.get(t.id) == before
    with pytest.raises(TaskError, match="revision changed"):
        q.reconcile_task_relevance(t.id, **stale)
    assert q.steer_log(t.id) == []
    assert q.relevance_snapshot(t.id)["closeout"] is None


@pytest.mark.parametrize("field", ["repo", "source", "origin_ref"])
def test_foreign_source_scope_is_rejected_without_receipt_or_effect(q: BaseQueue, field: str) -> None:
    t = task(q)
    with pytest.raises(TaskError, match="scope does not match"):
        report(q, t.id, scope=replace(SCOPE, **{field: "different"}))
    assert q.relevance_receipt(t.id, "closed-source") is None
    assert current(q, t.id).status == Status.QUEUED


def test_task_revision_and_generation_are_fenced_under_acceptance_lock(q: BaseQueue) -> None:
    t = task(q)
    original = request(q, t.id)
    q.claim_one("host/worker", task_id=t.id)
    with pytest.raises(TaskError, match="revision changed"):
        q.reconcile_task_relevance(t.id, **original)
    assert q.relevance_receipt(t.id, "closed-source") is None
    assert report(q, t.id)["decision"] == "closeout_pending"


def test_independent_hold_and_terminal_history_are_not_overridden(q: BaseQueue) -> None:
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


def test_needed_does_not_silently_clear_accepted_closeout(q: BaseQueue) -> None:
    t = task(q)
    q.claim_one("host/worker", task_id=t.id)
    report(q, t.id)
    accepted = q.relevance_snapshot(t.id)["closeout"]
    assert report(q, t.id, verdict="needed", request_id="source-reopened")["decision"] == "closeout_pending"
    assert q.relevance_snapshot(t.id)["closeout"] == accepted
    assert len(q.steer_log(t.id)) == 1


def test_actual_relevance_history_queries_are_indexed_by_task(q: BaseQueue, monkeypatch: pytest.MonkeyPatch) -> None:
    t = task(q)
    queries: list[str] = []
    connect = q._connect

    def traced_connection() -> sqlite3.Connection:
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
