"""Owner-gone auto-suspension parks the dead body's reservation and frees its lane."""

from __future__ import annotations

import sqlite3

import pytest

from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_common import Task
from agent_dispatch.queue_records import SpawnReservation, SpawnState, Status, TaskError
from tests import test_pool_placement as placement_controls
from tests.test_pool_placement import place, pool, publish, record_native, task

q = placement_controls.q


def _started(q: TaskQueue) -> tuple[Task, SpawnReservation]:
    publish(q, pool())
    t = task(q)
    reservation, _ = place(q, t)
    record_native(q, reservation)
    q.record_spawn(
        reservation.key, session_handle="native", startup_complete=True, startup_revision=1,
    )
    before = q.get(t.id)
    assert before is not None
    started = q.start(
        t.id, "host-a/worktree", owner_session_id="native",
        expected_generation=before.generation, now=101,
    )
    return started, reservation


def test_owner_gone_auto_suspend_parks_the_reservation_and_frees_its_lane(
    q: TaskQueue,
) -> None:
    publish(q, pool())
    t = task(q)
    reservation, _ = place(q, t)
    record_native(q, reservation)
    q.record_spawn(
        reservation.key, session_handle="native", startup_complete=True, startup_revision=1,
    )
    before = q.get(t.id)
    assert before is not None
    started = q.start(
        t.id, "host-a/worktree", owner_session_id="native",
        expected_generation=before.generation, now=101,
    )
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q), now=102)

    counts = q.reconcile_liveness(
        lambda *_args: "gone", headless_local_verdict=lambda _session: "gone", now=103,
    )

    assert counts["suspended"] == 1
    current, parked = q.get(t.id), q.get_reservation(reservation.key)
    assert current is not None and parked is not None
    assert current.status == Status.SUSPENDED and current.generation == started.generation
    assert parked.state == SpawnState.COLD and parked.session_handle == "native"
    assert parked.cooling_token is None and not parked.release_requested
    events: list[dict[str, object]] = q.events(t.id)
    assert "reservation cold" in str(events[-1]["note"])
    place(q, task(q), now=104)


def test_restart_completed_during_the_probe_keeps_its_lane(q: TaskQueue) -> None:
    started, reservation = _started(q)

    def restart_then_report_gone(*_args: object) -> str:
        with q._connect() as conn:
            conn.execute(
                "UPDATE spawn_reservations SET startup_revision = 2 WHERE key = ?",
                (reservation.key,),
            )
        return "gone"

    counts = q.reconcile_liveness(
        restart_then_report_gone, headless_local_verdict=restart_then_report_gone, now=103,
    )

    assert counts["suspended"] == 1
    current, running = q.get(started.id), q.get_reservation(reservation.key)
    assert current is not None and running is not None
    assert current.status == Status.SUSPENDED
    assert running.state == SpawnState.SPAWNED and running.startup_revision == 2
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q), now=104)


def test_replacement_published_mid_snapshot_keeps_its_lane(
    q: TaskQueue, monkeypatch: pytest.MonkeyPatch,
) -> None:
    started, reservation = _started(q)
    original = TaskQueue._active_headless_handle

    def lookup_then_replace(conn: sqlite3.Connection, task_id: str) -> str | None:
        handle = original(conn, task_id)
        with q._connect() as other:
            other.execute(
                "UPDATE spawn_reservations SET startup_revision = 2 WHERE key = ?",
                (reservation.key,),
            )
        return handle

    monkeypatch.setattr(TaskQueue, "_active_headless_handle", staticmethod(lookup_then_replace))
    counts = q.reconcile_liveness(
        lambda *_args: "gone", headless_local_verdict=lambda *_args: "gone", now=103,
    )

    assert counts["suspended"] == 1
    running = q.get_reservation(reservation.key)
    assert running is not None
    assert running.state == SpawnState.SPAWNED and running.startup_revision == 2
    with pytest.raises(TaskError, match="capacity"):
        place(q, task(q), now=104)
