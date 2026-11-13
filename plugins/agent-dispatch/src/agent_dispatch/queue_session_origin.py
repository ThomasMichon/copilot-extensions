"""Retain session-birth evidence separately from reusable workspace provenance."""

from __future__ import annotations

import json
import sqlite3
from .queue_assignment_cohort import capture_cohort
from .queue_common import Task
from .queue_records import SpawnReservation, TaskError


def migrate(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(spawn_reservations)")}
    for name, declaration in (
        ("session_origin", "TEXT"),
        ("session_origin_reservation_key", "TEXT"),
        ("session_origin_transport", "TEXT DEFAULT 'baseline'"),
    ):
        if name not in columns:
            try:
                conn.execute(f"ALTER TABLE spawn_reservations ADD COLUMN {name} {declaration}")
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS spawn_session_origin_retained"
        " BEFORE UPDATE OF session_origin, session_origin_reservation_key ON spawn_reservations"
        " WHEN OLD.session_origin_reservation_key IS NOT NULL AND"
        " (NEW.session_origin IS NOT OLD.session_origin OR"
        " NEW.session_origin_reservation_key IS NOT OLD.session_origin_reservation_key)"
        " BEGIN SELECT RAISE(ABORT, 'original session origin is immutable'); END"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS spawn_session_transport_retained"
        " BEFORE UPDATE OF session_origin_transport ON spawn_reservations"
        " WHEN OLD.session_origin_transport IS NOT NULL AND"
        " NEW.session_origin_transport IS NOT OLD.session_origin_transport"
        " BEGIN SELECT RAISE(ABORT, 'session origin transport is immutable'); END"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_spawn_session_creation"
        " ON spawn_reservations(creation_key, driver)"
    )


def retain(conn: sqlite3.Connection, task: Task, *, key: str, requested: str | None) -> None:
    """Run after the matching pool claim, inside the original reservation commit."""
    row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?", (key,)).fetchone()
    prior = None
    if row["driver"] == "ordinary-bridge":
        # Ordinary create replays the allocation's creation key, even after a
        # lost reply. Acquisition instead owns a distinct delivery per attempt.
        prior = conn.execute(
            "SELECT * FROM spawn_reservations WHERE creation_key=?"
            " AND driver='ordinary-bridge' AND key<>? ORDER BY rowid LIMIT 1",
            (row["creation_key"], key),
        ).fetchone()
    if prior is None:
        original_key = key
        encoded = json.dumps({
            "task_id": task.id, "reservation_key": key,
            "cohort": capture_cohort(conn, task, reservation_key=key),
        }, sort_keys=True)
        original = row
        transport = requested or "baseline"
    else:
        original_key = prior["session_origin_reservation_key"] or prior["key"]
        original = conn.execute(
            "SELECT * FROM spawn_reservations WHERE key=?", (original_key,),
        ).fetchone()
        if original is None:
            raise TaskError("original session reservation is unavailable")
        encoded, transport = original["session_origin"], _known_transport(conn, row)
    if requested not in {None, "baseline", "origin-v1"}:
        raise TaskError("unsupported session origin transport")
    if requested is not None and requested != transport:
        raise TaskError("original session origin transport cannot change")
    if transport == "origin-v1" and not _representable(encoded, original):
        raise TaskError("session origin transport requires representable original evidence")
    conn.execute(
        "UPDATE spawn_reservations SET session_origin=?, session_origin_reservation_key=?,"
        " session_origin_transport=? WHERE key=?", (encoded, original_key, transport, key),
    )


def _known_transport(conn: sqlite3.Connection, row: sqlite3.Row) -> str:
    history = conn.execute(
        "SELECT DISTINCT session_origin_transport FROM spawn_reservations"
        " WHERE creation_key=? AND driver='ordinary-bridge' AND key<>?",
        (row["creation_key"], row["key"]),
    ).fetchall()
    choices = {item["session_origin_transport"] or "baseline" for item in history}
    if choices - {"baseline", "origin-v1"} or len(choices) > 1:
        raise TaskError("conflicting session origin transport history requires reconciliation")
    return next(iter(choices), "baseline")


def _representable(encoded: str | None, row: sqlite3.Row) -> bool:
    from .worktree_cohort import _carrier_json

    value = json.loads(encoded) if encoded else None
    if (
        not isinstance(value, dict) or set(value) != {"task_id", "reservation_key", "cohort"}
        or value["task_id"] != row["task_id"] or value["reservation_key"] != row["key"]
        or _carrier_json(value["cohort"]) is None
    ):
        return False
    cohort = value["cohort"]
    return (
        (cohort["emitter"] is None or cohort["emitter"]["task_id"] == value["task_id"])
        and (cohort["pool"] is None or (
            cohort["pool"]["task_id"] == value["task_id"]
            and cohort["pool"]["reservation_key"] == value["reservation_key"]
        ))
    )


def reservation_record(row: sqlite3.Row, requested: str | None) -> SpawnReservation:
    """An active reservation replay cannot revise a previously sealed request."""
    if requested not in {None, "baseline", "origin-v1"}:
        raise TaskError("unsupported session origin transport")
    if requested is not None and (row["session_origin_transport"] or "baseline") != requested:
        raise TaskError("original session origin transport cannot change")
    return SpawnReservation._from_row(row)
