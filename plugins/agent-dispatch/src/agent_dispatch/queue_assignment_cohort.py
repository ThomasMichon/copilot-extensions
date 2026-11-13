"""Capture original cohort evidence in the assignment owner's existing audit."""

from __future__ import annotations

from dataclasses import asdict
import json
import sqlite3

from .queue_common import Task
from .queue_records import TaskError
from .queue_pool_placement import pool_assignment
from .queue_producer_receipts import task_producer_receipt


def migrate_allocation_cohorts(conn: sqlite3.Connection) -> None:
    """Add an optional reservation-owned snapshot without backfilling history."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(spawn_reservations)")}
    if "allocation_cohort" not in columns:
        try:
            conn.execute("ALTER TABLE spawn_reservations ADD COLUMN allocation_cohort TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
    if "allocation_cohort_transport" not in columns:
        try:
            conn.execute("ALTER TABLE spawn_reservations ADD COLUMN allocation_cohort_transport TEXT DEFAULT 'baseline'")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS spawn_cohort_transport_retained"
        " BEFORE UPDATE OF allocation_cohort_transport ON spawn_reservations"
        " WHEN OLD.allocation_cohort_transport IS NOT NULL"
        " AND NEW.allocation_cohort_transport IS NOT OLD.allocation_cohort_transport"
        " BEGIN SELECT RAISE(ABORT, 'allocation cohort transport is immutable'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS spawn_allocation_cohort_retained"
        " BEFORE UPDATE OF allocation_cohort, allocation_reservation_key, creation_key"
        " ON spawn_reservations WHEN OLD.allocation_cohort IS NOT NULL AND"
        " (NEW.allocation_cohort IS NOT OLD.allocation_cohort"
        " OR NEW.allocation_reservation_key IS NOT OLD.allocation_reservation_key"
        " OR NEW.creation_key IS NOT OLD.creation_key)"
        " BEGIN SELECT RAISE(ABORT, 'original allocation cohort is immutable'); END"
    )


def retain_allocation_cohort(
    conn: sqlite3.Connection, task: Task, *, key: str, original_key: str,
) -> None:
    """Seal a new reservation's original allocation evidence before its commit."""
    if key == original_key:
        encoded = json.dumps(capture_cohort(conn, task, reservation_key=key), sort_keys=True)
    else:
        original = conn.execute(
            "SELECT allocation_cohort, allocation_cohort_transport FROM spawn_reservations WHERE key=?", (original_key,),
        ).fetchone()
        encoded = original["allocation_cohort"] if original is not None else None
        conn.execute(
            "UPDATE spawn_reservations SET allocation_cohort_transport=? WHERE key=?",
            (original["allocation_cohort_transport"] if original is not None else "baseline", key),
        )
    conn.execute(
        "UPDATE spawn_reservations SET allocation_cohort=? WHERE key=?", (encoded, key),
    )


def bind_transport(conn: sqlite3.Connection, row: sqlite3.Row, requested: str | None) -> None:
    """Seal the original wire choice before any creation can take effect."""
    if requested not in {None, "baseline", "cohort-v1"}:
        raise TaskError("unsupported allocation cohort transport")
    key = row["allocation_reservation_key"] or row["key"]
    original = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
    if original is None:
        raise TaskError("original allocation transport is unavailable")
    retained = original["allocation_cohort_transport"]
    if requested is None and retained == "cohort-v1" and not row["worktree"]:
        raise TaskError("unrecorded cohort creation requires explicit transport confirmation")
    choice = requested if requested is not None else retained or "baseline"
    if retained is not None and retained != choice:
        raise TaskError("original allocation cohort transport cannot change")
    if choice == "cohort-v1" and not original["allocation_cohort"]:
        raise TaskError("cohort transport requires original allocation evidence")
    for selected in {key, row["key"]}:
        conn.execute(
            "UPDATE spawn_reservations SET allocation_cohort_transport=? WHERE key=?", (choice, selected),
        )


def bind_allocation_facts(
    conn: sqlite3.Connection, row: sqlite3.Row, project: str, *, cohort_transport: str | None,
    worktree_writer_requirement: dict | None, now: float,
) -> sqlite3.Row:
    """Keep target, writer and wire selection inside the same allocation transaction."""
    from .ordinary_writer_admission import allocation_requirement

    retained = allocation_requirement(dict(row), project, worktree_writer_requirement)
    bind_transport(conn, row, cohort_transport)
    from .queue_session_origin import seal_preparation
    seal_preparation(conn, row)
    conn.execute(
        "UPDATE spawn_reservations SET allocation_project=?, worktree_writer_requirement=?, updated_at=? WHERE key=?",
        (project, retained, now, row["key"]),
    )
    return conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (row["key"],)).fetchone()


def capture_cohort(
    conn: sqlite3.Connection, task: Task, *, reservation_key: str | None,
) -> dict:
    """Snapshot known original references under the caller's assignment transaction."""
    assignment = pool_assignment(conn, task.id, include_released=True)
    return {
        "schema": "dispatch-assignment-cohort",
        "version": 1,
        "emitter": task_producer_receipt(conn, task),
        "pool": (
            asdict(assignment)
            if assignment is not None and reservation_key is not None
            and assignment.reservation_key == reservation_key else None
        ),
    }
