"""Reservation-local evidence of an unqualified CI container launch, not send authority."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import TYPE_CHECKING

from .queue_records import SpawnReservation, SpawnState, TaskError

if TYPE_CHECKING:
    from .queue import TaskQueue
    from .queue_common import Task
    from .registrations import RegistrationRecord


class ObservationConflict(TaskError):
    """Do not fail or release a reservation whose physical custody may have changed."""


def migrate(conn: sqlite3.Connection) -> None:
    # Separate from qualified sends: those rows themselves establish scope qualification.
    conn.execute(
        "CREATE TABLE IF NOT EXISTS ci_launch_observations ("
        "reservation_key TEXT PRIMARY KEY NOT NULL, attempt_id TEXT NOT NULL UNIQUE,"
        "task_id TEXT NOT NULL, task_generation INTEGER NOT NULL,"
        "venue TEXT NOT NULL, workspace TEXT NOT NULL, state TEXT NOT NULL,"
        "staging_handle TEXT, CHECK(state IN ('pending','accepted','unknown')),"
        "CHECK((state='accepted')=(staging_handle IS NOT NULL)))"
    )
    conn.execute(
        "CREATE TRIGGER IF NOT EXISTS ci_launch_observation_settle_only"
        " BEFORE UPDATE ON ci_launch_observations WHEN OLD.state!='pending' OR NEW.state='pending'"
        " OR (OLD.reservation_key,OLD.attempt_id,OLD.task_id,OLD.task_generation,"
        "OLD.venue,OLD.workspace) IS NOT (NEW.reservation_key,NEW.attempt_id,NEW.task_id,"
        "NEW.task_generation,NEW.venue,NEW.workspace)"
        " BEGIN SELECT RAISE(ABORT,'immutable CI launch observation'); END"
    )


def observe(conn: sqlite3.Connection, reservation_key: str) -> sqlite3.Row | None:
    """Missing means no observation, never evidence of transport absence."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM ci_launch_observations WHERE reservation_key=?", (reservation_key,),
    ).fetchone()
    return row


def begin_if_unqualified(queue: TaskQueue, task: Task, reservation: SpawnReservation,
                         registration: RegistrationRecord | None,
                         venue: str, workspace: str) -> str | None:
    from .queue_execution_custody import execution_scope_qualified

    with queue._connect() as conn:
        # Reuse the producer classification snapshot. Do not hold a writer lock
        # while checking historical qualification, or decide from a stale read.
        conn.execute("BEGIN")
        if task.repo and task.source and execution_scope_qualified(conn, task.repo, task.source):
            return None
        row = conn.execute("SELECT * FROM spawn_reservations WHERE key=?",
                           (reservation.key,)).fetchone()
        lane = conn.execute("SELECT * FROM registrations WHERE id=?",
                            (task.origin_ref,)).fetchone()
        if (
            not task.repo or task.source != "ci-repair"
            or reservation.task_id != task.id or row is None
            or SpawnReservation._from_row(row) != reservation
            or reservation.state != SpawnState.RESERVING or reservation.session_handle
            or reservation.release_requested or reservation.startup_fenced
            or queue._fetch(conn, task.id) != task or not registration or lane is None
            or registration != queue._registration_from_row(lane)
            or registration.status != "active" or registration.kind != "supervised-lane"
            or registration.id != reservation.reserved_by
        ):
            raise ObservationConflict("CI launch reservation, registration or producer changed")
        selected = json.loads(task.prompt or "")["execution"]
        if (selected not in registration.spec.get("ci_repair_bindings", [])
                or (selected["venue"], selected["workspace"]) != (venue, workspace)):
            raise ObservationConflict("CI launch destination changed")
        if observe(conn, reservation.key) is not None:
            raise ObservationConflict("CI launch already observed; cannot authorize a resend")
        token = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO ci_launch_observations(reservation_key,attempt_id,task_id,"
            "task_generation,venue,workspace,state) VALUES (?,?,?,?,?,?,'pending')",
            (reservation.key, token, task.id, task.generation, venue, workspace),
        )
        conn.commit()
        return token


def settle(queue: TaskQueue, reservation_key: str, attempt_id: str, handle: str | None) -> None:
    with queue._connect() as conn:
        if conn.execute(
            "UPDATE ci_launch_observations SET state=?,staging_handle=?"
            " WHERE reservation_key=? AND attempt_id=? AND state='pending'",
            ("accepted" if handle else "unknown", handle, reservation_key, attempt_id),
        ).rowcount != 1:
            raise TaskError("CI launch settlement does not match an outstanding attempt")
