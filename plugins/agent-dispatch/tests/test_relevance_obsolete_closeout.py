"""Every obsolete task has an automatic closeout path, with or without a worker."""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import SpawnReservation, SpawnState, Status, TaskError
from agent_dispatch.queue_relevance import RelevanceScope, require_relevant_goal
from agent_dispatch.queue_relevance_finalize_first import FINALIZE_RUNNER, FINISH_ACTION
from agent_dispatch.queue_relevance_owner import NO_WORKER_FINISH_ACTION
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import current_task, place, pool, publish, reservation_for, task
from tests.test_pool_startup_policy import renew
from tests.test_task_relevance import report as _report

q = placement_controls.q
SCOPE = RelevanceScope("example", "emitter", "review-source")


def report(q: TaskQueue, task_id: str, request_id: str = "closed-source") -> dict[str, Any]:
    result: dict[str, Any] = _report(
        q, task_id, scope=SCOPE, request_id=request_id,
    )
    return result


def source_task(q: TaskQueue, **kwargs: Any) -> Task:
    created = q.create("work", repo="example", source=SCOPE.source, origin_ref=SCOPE.origin_ref, **kwargs)
    assert isinstance(created, Task)
    return created


def workspace(q: TaskQueue, r: SpawnReservation, tree: str) -> None:
    q.record_spawn_worktree(
        r.key, tree, ownership="created", creating_host="host-a", execution_machine="host-a",
        driver="cli", allocation_project="agent-worktrees", worktree_path=f"/work/{tree}",
    )


def evidence(tree: str, **change: str) -> dict[str, Any]:
    return {
        "runner": FINALIZE_RUNNER, "status": "finalized", "worktree_id": tree,
        "project": "agent-worktrees", **change,
    }


def acknowledgement(q: TaskQueue, task_id: str) -> dict[str, Any]:
    with q._connect() as conn:
        row = conn.execute(
            "SELECT acknowledgement FROM task_relevance_closeouts WHERE task_id=?", (task_id,),
        ).fetchone()
    assert row is not None and row[0] is not None
    ack: dict[str, Any] = json.loads(row[0])
    return ack


def gate(q: TaskQueue, task_id: str) -> None:
    with q._connect() as conn:
        require_relevant_goal(conn, task_id, closeout_start=True)


def fence(q: TaskQueue, t: Task, r: SpawnReservation) -> str:
    fenced = q.fence_relevance_finalize(
        t.id, supervisor_id="allocator", generation=current_task(q, t.id).generation,
        reservation_key=r.key,
    )
    token: str = fenced["token"]
    return token


# State A: a claimed pool task whose acquisition failed with an unknown outcome.
def unstarted(q: TaskQueue) -> tuple[Task, SpawnReservation]:
    publish(q, pool())
    t = source_task(q)
    r, _ = place(q, t)
    workspace(q, r, "wt-a")
    result = report(q, t.id)
    assert result["decision"] == "closeout_pending"
    assert result["owner"] is None and result["owner_session_id"] is None
    assert reservation_for(q, r.key).state == SpawnState.RESERVING
    return current_task(q, t.id), r


def test_unstarted_obsolete_claim_is_abandoned_by_accepted_finalize_first(q: TaskQueue) -> None:
    t, r = unstarted(q)
    candidates = q.relevance_finalize_candidates("allocator")
    assert [(c["task_id"], c["phase"]) for c in candidates] == [(t.id, "unstarted")]
    assert q.relevance_finalize_candidates("another-supervisor") == []
    token = fence(q, t, r)
    assert q.relevance_finalize_candidates("allocator") == []
    with pytest.raises(TaskError, match="requires owner closeout"):
        gate(q, t.id)
    with pytest.raises(TaskError, match="does not match"):
        q.settle_relevance_finalize(t.id, token=token, evidence=evidence("another-tree"))
    with pytest.raises(TaskError, match="does not match"):
        q.settle_relevance_finalize(t.id, token=token, evidence=evidence("wt-a", status="blocked"))
    assert current_task(q, t.id).status == Status.CLAIMED

    outcome = q.settle_relevance_finalize(t.id, token=token, evidence=evidence("wt-a"))

    assert outcome.event_type == "task.abandoned"
    assert current_task(q, t.id).status == Status.ABANDONED
    released = reservation_for(q, r.key)
    assert released.state == SpawnState.RELEASING and released.release_requested
    ack = acknowledgement(q, t.id)
    assert ack["finish_action"] == FINISH_ACTION and ack["phase"] == "unstarted"
    assert ack["finalization"]["worktree_id"] == "wt-a"
    assert q.list_wakes(t.id) == []
    with pytest.raises(TaskError):
        q.settle_relevance_finalize(t.id, token=token, evidence=evidence("wt-a"))


def test_refused_finalize_lets_the_accepting_body_close_out(q: TaskQueue) -> None:
    t, r = unstarted(q)
    token = fence(q, t, r)
    refused = q.release_relevance_finalize(t.id, token=token, detail="worktree has unpushed commits")
    assert refused["state"] == "refused" and refused["rearm"] is None
    # A refusal is not retried every cycle.
    assert q.relevance_finalize_candidates("allocator") == []
    gate(q, t.id)

    q.record_spawn(r.key, session_handle="native", worktree="wt-a")
    q.record_spawn(r.key, session_handle="native", startup_complete=True, startup_revision=1)
    started = q.start(
        t.id, "host-a/wt-a", owner_session_id="native", expected_generation=t.generation, now=101,
    )

    assert started.status == Status.STARTED
    closeout = q.relevance_snapshot(t.id)["closeout"]
    assert (closeout["owner"], closeout["owner_session_id"]) == ("host-a/wt-a", "native")
    assert closeout.get("acknowledgement") is None
    assert [w.status for w in q.list_wakes(t.id)] == ["pending"]


def test_retired_acquisition_without_a_worker_abandons_the_closeout(q: TaskQueue) -> None:
    t, r = unstarted(q)
    queued = q.exhaust_pool_startup(r.key, generation=t.generation, startup_revision=1, now=700)
    assert queued.status == Status.QUEUED
    q.request_spawn_release(r.key, finished_startup_revision=1, now=701)
    q.retire_spawn(r.key, exact_absence=True, now=702)

    assert current_task(q, t.id).status == Status.ABANDONED
    assert acknowledgement(q, t.id)["finish_action"] == NO_WORKER_FINISH_ACTION
    renew(q, 703)
    replacement, reserved = place(q, task(q), now=703)
    assert reserved and replacement.key != r.key


# State B: a suspended owner whose delivered closeout wake ended with its session.
def cold(q: TaskQueue) -> tuple[Task, SpawnReservation]:
    publish(q, pool())
    t = source_task(q)
    r, _ = place(q, t)
    workspace(q, r, "wt-b")
    q.record_spawn(r.key, session_handle="native", worktree="wt-b")
    q.record_spawn(r.key, session_handle="native", startup_complete=True, startup_revision=1)
    started = q.start(
        t.id, "host-a/wt-b", owner_session_id="native",
        expected_generation=current_task(q, t.id).generation, now=101,
    )
    q.suspend(t.id, started.owner or "", reason="waiting on the source", cooldown_seconds=None, now=102)
    result = report(q, t.id)
    assert result["decision"] == "closeout_pending" and result["owner_session_id"] == "native"
    wake = q.claim_due_wake(now=104)
    assert wake is not None and wake.task_id == t.id
    q.finish_wake(wake.id, wake.delivery_token or "", delivered=True, now=105)
    with q._connect() as conn:
        conn.execute("UPDATE spawn_reservations SET state=? WHERE key=?", (SpawnState.COLD, r.key))
    current = current_task(q, t.id)
    assert current.status == Status.SUSPENDED
    return current, r


def test_cold_owned_closeout_is_abandoned_by_accepted_finalize_first(q: TaskQueue) -> None:
    t, r = cold(q)
    candidates = q.relevance_finalize_candidates("allocator")
    assert [(c["task_id"], c["phase"]) for c in candidates] == [(t.id, "cold")]
    token = fence(q, t, r)

    outcome = q.settle_relevance_finalize(t.id, token=token, evidence=evidence("wt-b"))

    assert outcome.event_type == "task.abandoned"
    assert current_task(q, t.id).status == Status.ABANDONED
    ack = acknowledgement(q, t.id)
    assert ack["finish_action"] == FINISH_ACTION and ack["phase"] == "cold"
    assert ack["task_attachment"]["owner_session_id"] == "native"
    assert reservation_for(q, r.key).release_requested


def test_refused_cold_closeout_rearms_its_owner_to_close_out(q: TaskQueue) -> None:
    t, r = cold(q)
    token = fence(q, t, r)
    refused = q.release_relevance_finalize(t.id, token=token, detail="finalize refused")

    assert refused["phase"] == "cold" and refused["rearm"] == "pending"
    assert q.relevance_finalize_candidates("allocator") == []
    # The re-armed wake is the owner's linked closeout: it is claimed and admitted.
    wake = q.claim_due_wake(now=200)
    assert wake is not None and wake.task_id == t.id
    with q._connect() as conn:
        from agent_dispatch.queue_relevance_delivery import closeout_wake

        assert closeout_wake(conn, wake)
        require_relevant_goal(conn, t.id, owner_wake=wake)
    assert current_task(q, t.id).status != Status.ABANDONED


def test_a_fence_refuses_the_owner_closeout_wake_until_it_is_lifted(q: TaskQueue) -> None:
    t, r = cold(q)
    token = fence(q, t, r)
    q.release_relevance_finalize(t.id, token=token, detail="finalize refused")
    wake = q.claim_due_wake(now=200)
    assert wake is not None
    with q._connect() as conn:
        # A later fence on the same checkout (the row persists after release) re-closes the gate.
        assert conn.execute(
            "UPDATE task_relevance_finalize_first SET state='fenced', token='other', expires_at=?"
            " WHERE task_id=?", (10**12, t.id),
        ).rowcount == 1
        with pytest.raises(TaskError, match="requires owner closeout"):
            require_relevant_goal(conn, t.id, owner_wake=wake)


def test_a_fence_refuses_a_pending_before_start_closeout_wake(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from agent_dispatch import queue_relevance_delivery

    t, r = unstarted(q)
    # The unstarted cohort admits a merely pending linked wake; the fence must still outrank it.
    monkeypatch.setattr(queue_relevance_delivery, "closeout_wake", lambda conn, wake: True)
    wake: Any = SimpleNamespace(task_id=t.id)
    with q._connect() as conn:
        require_relevant_goal(conn, t.id, owner_wake=wake)
    fence(q, t, r)
    with q._connect() as conn, pytest.raises(TaskError, match="requires owner closeout"):
        require_relevant_goal(conn, t.id, owner_wake=wake)


def test_an_expired_fence_never_settles_a_newly_started_worker(q: TaskQueue) -> None:
    t, r = unstarted(q)
    token = fence(q, t, r)
    with q._connect() as conn:
        conn.execute("UPDATE task_relevance_finalize_first SET expires_at=1 WHERE task_id=?", (t.id,))
    gate(q, t.id)
    q.record_spawn(r.key, session_handle="native", worktree="wt-a")
    q.record_spawn(r.key, session_handle="native", startup_complete=True, startup_revision=1)
    q.start(t.id, "host-a/wt-a", owner_session_id="native",
            expected_generation=current_task(q, t.id).generation, now=101)

    with pytest.raises(TaskError, match="not held"):
        q.settle_relevance_finalize(t.id, token=token, evidence=evidence("wt-a"))
    assert current_task(q, t.id).status == Status.STARTED


def test_an_unknown_finalize_effect_keeps_custody_past_fence_expiry(q: TaskQueue) -> None:
    t, r = unstarted(q)
    token = fence(q, t, r)
    assert q.retain_relevance_finalize(t.id, token=token, detail="finalize timed out")["state"] == "unknown"
    with q._connect() as conn:
        conn.execute("UPDATE task_relevance_finalize_first SET expires_at=1 WHERE task_id=?", (t.id,))
    with pytest.raises(TaskError, match="requires owner closeout"):
        gate(q, t.id)
    assert q.relevance_finalize_candidates("allocator") == []
    assert [row["task_id"] for row in q.relevance_finalize_unknown("allocator")] == [t.id]
    assert q.relevance_finalize_unknown("another-supervisor") == []

    q.settle_relevance_finalize(t.id, token=token, evidence=evidence("wt-a"))
    assert current_task(q, t.id).status == Status.ABANDONED


def test_candidates_page_past_a_full_first_page(q: TaskQueue) -> None:
    publish(q, pool(capacity=6))
    ids = []
    for i in range(6):
        t = source_task(q)
        r, _ = place(q, t)
        workspace(q, r, f"wt-{i}")
        assert report(q, t.id, request_id=f"closed-{i}")["decision"] == "closeout_pending"
        ids.append(t.id)
    first = q.relevance_finalize_candidates("allocator")
    assert len(first) == 4
    rest = q.relevance_finalize_candidates(
        "allocator", after=(first[-1]["cursor"]["updated_at"], first[-1]["cursor"]["task_id"]),
    )
    assert sorted(c["task_id"] for c in first + rest) == sorted(ids)


def test_retire_endpoint_publishes_the_committed_abandonment_once(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from agent_dispatch import handoff_claim_release
    from agent_dispatch.coordinator import create_app

    t, r = unstarted(q)
    q.exhaust_pool_startup(r.key, generation=t.generation, startup_revision=1, now=700)
    q.request_spawn_release(r.key, finished_startup_revision=1, now=701)
    app = create_app(q)
    api = TestClient(app)
    events: list[dict[str, Any]] = []
    released: list[str] = []
    monkeypatch.setattr(app.state.bus, "publish", events.append)
    monkeypatch.setattr(
        handoff_claim_release, "release_if_handoff",
        lambda task, **kwargs: released.append(task["id"]),
    )

    resp = api.post(f"/spawn-reservations/{r.key}/retire", json={"exact_absence": True})
    assert resp.status_code == 200, resp.text
    assert [e["type"] for e in events if e["type"].startswith(("spawn.", "task."))] == [
        "spawn.retired", "task.abandoned",
    ]
    assert released == [t.id]
    assert current_task(q, t.id).status == Status.ABANDONED
    assert api.post(f"/spawn-reservations/{r.key}/retire", json={"exact_absence": True}).status_code == 409
    assert [e["type"] for e in events].count("task.abandoned") == 1


# Case 2: nothing to close out, so an obsolete report abandons directly.
def test_queued_obsolete_task_is_abandoned_and_refuses_supervisor_start(q: TaskQueue) -> None:
    publish(q, pool())
    t = source_task(q)
    assert report(q, t.id)["decision"] == "abandoned"
    with pytest.raises(TaskError):
        place(q, t)
    assert q.list_reservations(task_id=t.id) == []


def test_claim_with_only_settled_history_is_abandoned_directly(q: TaskQueue) -> None:
    publish(q, pool())
    t = source_task(q)
    r, _ = place(q, t)
    workspace(q, r, "wt-c")
    q.exhaust_pool_startup(r.key, generation=current_task(q, t.id).generation, startup_revision=1, now=700)
    q.request_spawn_release(r.key, finished_startup_revision=1, now=701)
    q.retire_spawn(r.key, exact_absence=True, now=702)
    assert current_task(q, t.id).status == Status.QUEUED

    result = report(q, t.id)

    assert result["decision"] == "abandoned" and result["custody"] == []
    assert current_task(q, t.id).status == Status.ABANDONED
    with pytest.raises(TaskError):
        place(q, t)


# Submitted work: the evaluator settles it once the worker has closed out.
def test_obsolete_submission_is_abandoned_after_closeout(q: TaskQueue) -> None:
    t = source_task(q, require_verification=True)
    q.claim_one("host/worker", task_id=t.id)
    q.start(t.id, "host/worker", owner_session_id="native")
    q.complete(t.id, "host/worker")
    assert report(q, t.id)["decision"] == "closeout_pending"
    # A permitted evaluator abandonment is never blocked by the pending closeout.
    abandoned = q.abandon(
        t.id, worker_id="evaluator", permitted=True, reason="source closed",
        expected_status=Status.SUBMITTED,
    )
    assert abandoned.status == Status.ABANDONED


def test_obsolete_report_settles_an_acknowledged_submission(q: TaskQueue) -> None:
    t = source_task(q, require_verification=True)
    q.claim_one("host/worker", task_id=t.id)
    q.start(t.id, "host/worker", owner_session_id="native")
    q.complete(t.id, "host/worker")
    assert report(q, t.id)["decision"] == "closeout_pending"
    with q._connect() as conn:
        conn.execute(
            "UPDATE task_relevance_closeouts SET acknowledgement=? WHERE task_id=?",
            (json.dumps({"finish_action": "worker-closeout"}), t.id),
        )

    assert report(q, t.id, request_id="closed-again")["decision"] == "abandoned"
    assert current_task(q, t.id).status == Status.ABANDONED
