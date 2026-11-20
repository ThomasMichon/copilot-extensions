"""Exhaust and retire an actual project-bound pool attempt before retrying."""

from dataclasses import asdict
from contextlib import closing
import sqlite3
from types import SimpleNamespace

import pytest
from agent_dispatch.queue import TaskQueue
from agent_dispatch.queue_records import SpawnReservation
from tests.test_pool_placement import current_task, reservation_for
from tests.test_pool_startup_policy import exhaust, renew


def retry_after_project(ordinary: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> SpawnReservation:
    q: TaskQueue = ordinary.q
    r = reservation_for(q, ordinary.placed["reservation"]["key"])
    startup = q.pool_startup(r.task_id)
    assert startup and startup.startup_deadline
    deadline = startup.startup_deadline
    q.record_spawn_allocation(r.key, "example")
    queued = exhaust(q, current_task(q, r.task_id), r, now=deadline)
    q.request_spawn_release(r.key, finished_startup_revision=r.startup_revision, now=deadline + 1)
    q.retire_spawn(r.key, exact_absence=True, conclusion_state="skipped", now=deadline + 2)
    monkeypatch.setattr(q, "_now", lambda now=None: queued.not_before if now is None else now)
    renew(q, queued.not_before)
    retry, fresh = q.reserve_spawn(r.task_id, reserved_by=r.reserved_by, driver="ordinary-bridge",
                                   pool_catalog_revision=q.pool_catalog_snapshot().revision)
    assert fresh and retry.attempt == r.attempt + 1 and retry.allocation_reservation_key == r.allocation_reservation_key
    ordinary.placed["reservation"] = asdict(retry)
    ordinary.task.update(asdict(current_task(q, r.task_id)), spawn_reservation_key=retry.key)
    return retry


def check_intermediate_fences(q: TaskQueue, task: str, *, decline: bool) -> None:
    from agent_dispatch.queue_allocation_revisions import _disposed_captured_revision
    from agent_dispatch.relationship_decline_history import _undischarged

    with closing(sqlite3.connect(":memory:", isolation_level=None)) as conn:
        conn.row_factory = sqlite3.Row
        with q._connect() as source:
            source.backup(conn)
        history = "SELECT * FROM spawn_reservations WHERE task_id=? ORDER BY attempt"
        root, middle, prepared = conn.execute(history, (task,)).fetchall()
        assert middle["attempt"] == 2 and middle["allocation_reservation_key"] == root["key"]

        def accepted() -> bool:
            if decline:
                return not conn.execute("SELECT " + _undischarged(":id"), {"id": task}).fetchone()[0]
            return _disposed_captured_revision(conn, conn.execute(history, (task,)).fetchall())

        queries: list[str] = []
        conn.execute("BEGIN IMMEDIATE")
        conn.set_trace_callback(queries.append)
        try:
            assert accepted()
        finally:
            conn.set_trace_callback(None)
            conn.rollback()
        if not decline:
            for table in ("wake_outbox", "run_waiter_wakes"):
                scans = [s for s in queries if f"FROM {table} " in s and "execution_send_state" in s]
                assert len(scans) == 1, (table, len(scans))
        # Isolated prior schemas need not have the modern immutability trigger.
        conn.execute("DROP TRIGGER spawn_allocation_cohort_retained")
        for assignment, value in (
            ("allocation_cohort", "{}"), ("allocation_project", "other"), ("reserved_by", "other"),
            ("worktree", "foreign-tree"), ("session_handle", "local-body:other"),
            ("task_relationship_event_id", prepared["task_relationship_event_id"]),
            ("cleanup_claim_token", "held"), ("native_release_pending", 1),
        ):
            conn.execute("SAVEPOINT control")
            conn.execute(f"UPDATE spawn_reservations SET {assignment}=? WHERE key=?", (value, middle["key"]))
            assert not accepted(), assignment
            conn.execute("ROLLBACK TO control")
            conn.execute("RELEASE control")
        conn.execute("INSERT INTO acquisition_markers VALUES (?)", (middle["key"],))
        assert not accepted()
        conn.execute("DELETE FROM acquisition_markers WHERE reservation_key=?", (middle["key"],))
        conn.execute("INSERT INTO execution_send_custody(scope_key,send_id,repo,source,producer_generation,"
                     "task_id,reservation_key,task_generation,kind,identity,state,detail,created_at,updated_at)"
                     " VALUES ('scope','send','example','source',1,?,?,1,'startup','{}','unknown','{}',1,1)",
                     (task, middle["key"]))
        assert not accepted()
