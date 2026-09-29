"""Live-wiring tests for ``Supervisor.sweep_task_reservation_consistency`` --
prove it genuinely wires
``task_reservation_consistency``'s declared compatibility table against
real, live ``TaskQueue``-backed reservation rows, mirroring
``test_spawn_consistency_sweep.py``'s own template for the sibling sweep.
"""

from __future__ import annotations

import pytest

from agent_dispatch.queue import SpawnState, Status
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


def test_sweep_reports_no_anomalies_for_the_normal_spawned_steady_state(q, client):
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(reservation.key, session_handle="local-body:sess-1")
    q.claim_one("owner", task_id=task.id)
    q.start(task.id, "owner")

    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, labels=["review"])

    result = sup.sweep_task_reservation_consistency()
    assert result == {
        "checked": 1,
        "anomalies": 0,
        "covered": 0,
        "uncovered": 0,
    }


def test_sweep_reports_no_anomalies_for_the_normal_cold_dormant_state(q, client):
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(reservation.key, session_handle="local-body:sess-2")
    q.claim_one("owner", task_id=task.id)
    q.start(task.id, "owner")
    q.suspend(task.id, "owner", reason="turn ended")
    q.record_cold(reservation.key)

    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, labels=["review"])

    result = sup.sweep_task_reservation_consistency()
    assert result == {
        "checked": 1,
        "anomalies": 0,
        "covered": 0,
        "uncovered": 0,
    }


def test_sweep_flags_a_known_uncovered_anomaly(q, client):
    """A COLD reservation should only ever coexist with a SUSPENDED task
    (cool_dormant_bodies's own guard) -- a COLD reservation on a CLAIMED
    task is exactly the shape no sweep in this codebase would ever clear
    (see task_reservation_consistency._UNCOVERED_ANOMALIES). Simulated
    directly (record_cold does not itself gate on task status -- only the
    supervisor's own cool_dormant_bodies call site does), the same way
    test_spawn_consistency_sweep's single-assignment test simulates its
    own anomaly directly, since normal operation should never produce
    this shape."""
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(reservation.key, session_handle="local-body:sess-3")
    q.claim_one("owner", task_id=task.id)
    q.start(task.id, "owner")
    q.record_cold(reservation.key)
    assert q.get(task.id).status == Status.STARTED

    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, labels=["review"])

    result = sup.sweep_task_reservation_consistency()
    assert result == {
        "checked": 1,
        "anomalies": 1,
        "covered": 0,
        "uncovered": 1,
    }
    # Read-only: the anomaly is reported, never silently repaired here.
    assert q.get_reservation(reservation.key).state == SpawnState.COLD
    assert q.get(task.id).status == Status.STARTED


def test_sweep_skips_a_concluded_reservation(q, client):
    """Only the four ACTIVE reservation states are modeled -- a SETTLED/
    FAILED/etc. row is a historical record, not classified at all."""
    task = q.create("review", labels=["review"])
    reservation, _ = q.reserve_spawn(task.id)
    q.record_spawn(reservation.key, session_handle="local-body:sess-4")
    q.settle_spawn(reservation.key, detail="done")

    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, labels=["review"])

    result = sup.sweep_task_reservation_consistency()
    assert result == {"checked": 0, "anomalies": 0, "covered": 0, "uncovered": 0}


def test_poll_once_runs_the_sweep_every_cycle_by_default(q, client, monkeypatch):
    task = q.create("review", labels=["review"])
    sup = Supervisor(client, spawn_fn=_ok_spawn(), repo=TEST_REPO, labels=["review"])
    calls = []
    monkeypatch.setattr(
        sup, "sweep_task_reservation_consistency", lambda: calls.append(1) or {}
    )
    sup.poll_once()
    assert calls == [1]
    assert q.get(task.id).status is not None  # cycle still ran normally


def test_poll_once_skips_the_sweep_when_disabled(q, client, monkeypatch):
    sup = Supervisor(
        client,
        spawn_fn=_ok_spawn(),
        repo=TEST_REPO,
        labels=["review"],
        consistency_sweep=False,
    )
    calls = []
    monkeypatch.setattr(
        sup, "sweep_task_reservation_consistency", lambda: calls.append(1) or {}
    )
    sup.poll_once()
    assert calls == []


def test_poll_once_tolerates_a_sweep_failure(q, client, monkeypatch):
    """A blip in this (read-only, best-effort) sweep must never abort the
    cycle's own reconcile/spawn work."""
    task = q.create("review", labels=["review"])
    spawn = _ok_spawn()
    sup = Supervisor(
        client, spawn_fn=spawn, repo=TEST_REPO, labels=["review"], max_concurrent=5
    )

    def _boom():
        raise RuntimeError("transient sweep failure")

    monkeypatch.setattr(sup, "sweep_task_reservation_consistency", _boom)
    assert sup.poll_once() == [task.id]
    assert spawn.calls == [task.id]
