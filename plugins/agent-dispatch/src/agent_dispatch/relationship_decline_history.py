"""Scope a settled decline exception to a newly recorded, ungoverned pool generation."""

from __future__ import annotations

import sqlite3
from typing import Literal

from .queue_records import TaskError


def _undischarged(task: Literal[":id", "t.id", "NEW.id"]) -> str:
    # Decline, closure and native retirement are independent proofs. A closed
    # audit event on its own must never allow an unprotected reassignment.
    settled = (
        "SELECT 1 FROM pool_worker_decline_closeouts d JOIN spawn_reservations r"
        " ON r.key=d.reservation_key WHERE r.task_id=e.task_id AND r.task_relationship_event_id=e.id"
        " AND json_extract(d.receipt,'$.task_relationship_event_id')=e.id"
        " AND json_extract(d.receipt,'$.task_id')=e.task_id"
        " AND json_extract(e.task_relationship,'$.state')='closed'"
        " AND r.release_requested=1 AND r.native_release_pending=0 AND r.state='settled'"
    )
    return (
        f"EXISTS (SELECT 1 FROM task_events e WHERE e.task_id={task} AND e.task_relationship IS NOT NULL"
        f" AND NOT EXISTS ({settled})) OR EXISTS (SELECT 1 FROM spawn_reservations s"
        f" WHERE s.task_id={task} AND s.task_relationship_required=1 AND NOT EXISTS (SELECT 1"
        " FROM task_events e WHERE e.task_id=s.task_id AND e.id=s.task_relationship_event_id"
        f" AND EXISTS ({settled})))"
    )


def admit_ungoverned_replacement(
    conn: sqlite3.Connection, task_id: str, key: str, generation: int
) -> None:
    if conn.execute("SELECT " + _undischarged(":id"), {"id": task_id}).fetchone()[0]:
        raise TaskError("ordinary capture cannot drop a governed assignment")
    conn.execute(
        "UPDATE spawn_reservations SET task_relationship_generation=? WHERE key=?",
        (generation, key),
    )


def relationship_required_condition(task: Literal["t", "NEW"]) -> str:
    return (
        f"((EXISTS (SELECT 1 FROM task_events e WHERE e.task_id={task}.id AND e.task_relationship IS NOT NULL)"
        f" OR EXISTS (SELECT 1 FROM spawn_reservations s WHERE s.task_id={task}.id AND s.task_relationship_required=1))"
        f" AND (NOT EXISTS (SELECT 1 FROM spawn_reservations n WHERE n.task_id={task}.id"
        f" AND n.task_relationship_required=0 AND n.task_relationship_generation={task}.generation)"
        f" OR ({_undischarged('NEW.id' if task == 'NEW' else 't.id')})))"
    )
