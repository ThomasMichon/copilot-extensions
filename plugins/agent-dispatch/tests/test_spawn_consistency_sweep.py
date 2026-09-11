"""Phase 10 (``review-automation-reliability`` effort) item 5 tests:
prove ``Supervisor.sweep_spawn_consistency`` genuinely wires
``spawn_reservation_machine``'s declared single-assignment invariant and
reservation<->bridge consistency relation against real, live
``TaskQueue``-backed reservation rows -- not just the standalone
declared-table tests already covering the pure functions in isolation.
"""

from __future__ import annotations

import pytest

from agent_dispatch.queue import SpawnState
from agent_dispatch.supervisor import Supervisor
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_supervisor import QueueBackedClient, _ok_spawn


@pytest.fixture
def q(tmp_path):
    return TaskQueue(tmp_path / "tasks.db")


@pytest.fixture
def client(q):
    return QueueBackedClient(q)


def test_sweep_reports_no_anomalies_for_a_consistent_live_reservation(q, client):
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:sess-1",
        worktree="worktree-a",
    )
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "live",
    )
    result = sup.sweep_spawn_consistency()
    assert result == {"checked": 1, "anomalies": 0, "assignment_violations": 0}


def test_sweep_detects_a_spawned_reservation_whose_body_is_actually_gone(q, client):
    """The headline reconcile_reserving-class anomaly: SPAWNED claims a
    live process, but the fresh liveness read says gone."""
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:sess-2",
        worktree="worktree-b",
    )
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "gone",
    )
    result = sup.sweep_spawn_consistency()
    assert result == {"checked": 1, "anomalies": 1, "assignment_violations": 0}


def test_sweep_skips_reservations_with_no_local_body_handle(q, client):
    """A worktree-backed (non-local-body) or handle-less reservation
    contributes no liveness-checkable evidence -- it must not be counted
    or misclassified."""
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="worktree-handle-not-local-body",
        worktree="worktree-c",
    )
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "live",
    )
    result = sup.sweep_spawn_consistency()
    assert result == {"checked": 0, "anomalies": 0, "assignment_violations": 0}


def test_sweep_skips_an_unknown_verdict_rather_than_guessing(q, client):
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:sess-3",
        worktree="worktree-d",
    )
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "unknown",
    )
    result = sup.sweep_spawn_consistency()
    assert result == {"checked": 0, "anomalies": 0, "assignment_violations": 0}


def test_sweep_never_mutates_the_reservation_it_classifies(q, client):
    """Read-only, additive: the sweep must never change a reservation's
    real state, even when it detects an anomaly."""
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(
        reservation.key,
        session_handle="local-body:sess-4",
        worktree="worktree-e",
    )
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "gone",
    )
    sup.sweep_spawn_consistency()
    assert q.get_reservation(reservation.key).state == SpawnState.SPAWNED


def test_sweep_detects_a_single_assignment_violation(q, client, monkeypatch):
    """``reserve_spawn`` itself prevents two simultaneously-ACTIVE
    reservations for the same exclusive-key group through the public API
    -- that is exactly the invariant this sweep exists to double-check,
    not something normal usage can trigger. Simulate the anomaly
    directly (e.g. as a data-migration bug or manual tamper would
    produce it) by returning two ACTIVE rows sharing an exclusive key
    from the pool query, and confirm the sweep still catches it."""
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        local_body_verdict_fn=lambda _sid: "unknown",
    )
    tampered_rows = [
        {
            "key": reservation.key,
            "task_id": task.id,
            "state": SpawnState.SPAWNED,
            "exclusive_key": "shared-key",
            "session_handle": None,
            "worktree_ownership": None,
        },
        {
            "key": "dispatch-task:other-task:1",
            "task_id": "other-task",
            "state": SpawnState.RESERVING,
            "exclusive_key": "shared-key",
            "session_handle": None,
            "worktree_ownership": None,
        },
    ]
    monkeypatch.setattr(sup, "_pool_reservations", lambda **_kw: tampered_rows)
    result = sup.sweep_spawn_consistency()
    assert result["assignment_violations"] == 1
