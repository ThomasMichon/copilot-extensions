"""Reservation-local evidence of an unqualified CI container launch, not send authority."""

from __future__ import annotations

import json
import sqlite3
import uuid
from typing import TYPE_CHECKING

from .queue_records import SpawnReservation, SpawnState, Status, TaskError

if TYPE_CHECKING:
    from .queue import TaskQueue
    from .queue_common import Task
    from .registrations import RegistrationRecord


class ObservationConflict(TaskError):
    """Do not fail or release a reservation whose physical custody may have changed."""


class ObservationDeferred(ObservationConflict):
    """Known not attempted; only the ordinary new-reservation path may retry."""


class _StaleAdmission(Exception):
    """A read snapshot changed before we wrote anything or entered transport."""


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
    for _ in range(2):
        try:
            return _begin(queue, task, reservation, registration, venue, workspace)
        except _StaleAdmission:
            continue
    _defer_unattempted(queue, task, reservation, registration)
    raise ObservationDeferred("CI launch deferred before transport after conflicting queue writes")


def _begin(queue: TaskQueue, task: Task, reservation: SpawnReservation,
           registration: RegistrationRecord | None, venue: str, workspace: str) -> str | None:
    from .queue_execution_custody import execution_scope_qualified

    with queue._connect() as conn:
        # Reuse the producer classification snapshot. Do not hold a writer lock
        # while checking historical qualification, or decide from a stale read.
        conn.execute("BEGIN")
        if task.repo and task.source and execution_scope_qualified(conn, task.repo, task.source):
            return None
        version = conn.execute("PRAGMA main.data_version").fetchone()[0]
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
        # Compare on the SAME connection. A WAL reader cannot upgrade an old
        # snapshot. Take the write lock fresh before comparing, also on Python
        # 3.10 where sqlite exceptions have no extended result-code attribute.
        conn.rollback()
        conn.execute("BEGIN IMMEDIATE")
        if conn.execute("PRAGMA main.data_version").fetchone()[0] != version:
            raise _StaleAdmission
        token = uuid.uuid4().hex
        conn.execute(
            "INSERT INTO ci_launch_observations(reservation_key,attempt_id,task_id,"
            "task_generation,venue,workspace,state) VALUES (?,?,?,?,?,?,'pending')",
            (reservation.key, token, task.id, task.generation, venue, workspace),
        )
        conn.commit()
        return token


def _defer_unattempted(queue: TaskQueue, task: Task, reservation: SpawnReservation,
                       registration: RegistrationRecord | None) -> None:
    key = reservation.key

    def guard(row: sqlite3.Row) -> None:
        # _update_reservation already holds BEGIN IMMEDIATE. Use its pre-update
        # guard, not a check followed by a separate unconditional deferral.
        with queue._connect() as probe:
            lane = probe.execute("SELECT * FROM registrations WHERE id=?",
                                 (task.origin_ref,)).fetchone()
            competing = probe.execute(
                "SELECT 1 FROM execution_send_custody WHERE reservation_key=:key"
                " UNION ALL SELECT 1 FROM acquisition_receipts WHERE reservation_key=:key"
                " UNION ALL SELECT 1 FROM acquisition_markers WHERE reservation_key=:key"
                " UNION ALL SELECT 1 FROM pool_reservation_bindings WHERE reservation_key=:key"
                " UNION ALL SELECT 1 FROM routing_assignments WHERE task_id=:task AND attempt=:n"
                " LIMIT 1", {"key": key, "task": task.id, "n": reservation.attempt},
            ).fetchone()
            if (
                SpawnReservation._from_row(row) != reservation or reservation.task_id != task.id
                or task.status != Status.QUEUED or task.owner or task.owner_session_id
                or reservation.driver not in (None, "cli") or reservation.release_requested
                or reservation.session_handle or reservation.agent_info or reservation.worktree
                or reservation.allocation_project or reservation.startup_complete
                or reservation.worktree_path or reservation.creation_nonce
                or reservation.native_release_pending or reservation.conclusion_state
                or reservation.inherited_worktree
                or queue._fetch(probe, task.id) != task
                or lane is None or queue._registration_from_row(lane) != registration
                or competing or observe(probe, key) is not None
            ):
                raise ObservationConflict("CI launch reservation now has competing custody")

    try:
        queue._update_reservation(
            key, to_state=SpawnState.DEFERRED, allowed_from=frozenset({SpawnState.RESERVING}),
            detail="CI launch admission contended before transport", guard=guard,
        )
    except ObservationConflict:
        raise
    except TaskError as exc:
        raise ObservationConflict(str(exc)) from exc


def settle(queue: TaskQueue, reservation_key: str, attempt_id: str, handle: str | None) -> None:
    with queue._connect() as conn:
        if conn.execute(
            "UPDATE ci_launch_observations SET state=?,staging_handle=?"
            " WHERE reservation_key=? AND attempt_id=? AND state='pending'",
            ("accepted" if handle else "unknown", handle, reservation_key, attempt_id),
        ).rowcount != 1:
            raise TaskError("CI launch settlement does not match an outstanding attempt")
