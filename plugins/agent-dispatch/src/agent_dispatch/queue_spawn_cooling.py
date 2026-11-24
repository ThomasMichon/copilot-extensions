"""Durable exclusion between physical cooling and startup/resume publication."""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from .queue_common import Task
from .queue_records import SpawnReservation, SpawnState, Status, TaskError

if TYPE_CHECKING:
    from .queue import TaskQueue


def require_no_cooling(row: sqlite3.Row) -> None:
    if row["cooling_token"] is not None:
        raise TaskError("spawn cooling remains unresolved")


def validate_cooling_publication(
    row: sqlite3.Row, token: str | None, startup_revision: int | None,
) -> None:
    if token is None:
        require_no_cooling(row)
    elif (
        row["cooling_token"] != token
        or type(startup_revision) is not int
        or row["startup_revision"] != startup_revision
        or not row["startup_complete"]
    ):
        raise TaskError("cooling operation or startup revision changed")


_BODY_SQL = (
    "SELECT * FROM spawn_reservations WHERE task_id = ? AND state = ?"
    " AND startup_complete = 1 AND startup_fenced = 0 AND release_requested = 0"
    " AND conversation_retired = 0 AND cooling_token IS NULL"
)


def _body(row: sqlite3.Row) -> tuple[str, str | None, int | None]:
    return (row["key"], row["session_handle"], row["startup_revision"])


def probed_bodies(
    conn: sqlite3.Connection, task_id: str,
) -> frozenset[tuple[str, str | None, int | None]]:
    """Snapshot the exact startups a liveness probe is about to observe."""
    return frozenset(
        _body(row)
        for row in conn.execute(_BODY_SQL, (task_id, SpawnState.SPAWNED)).fetchall()
    )


def cool_gone_owner_bodies(
    queue: TaskQueue, conn: sqlite3.Connection, task_id: str, ts: float, *,
    probed_handle: bool, probed: frozenset[tuple[str, str | None, int | None]],
) -> int:
    """Publish ``cold`` for a just-auto-suspended task's body proven gone.

    Runs inside the caller's owner-gone suspension transaction so the task and
    its reservation commit together. Without it, a suspended task kept a
    ``spawned`` reservation whose body no longer exists, and that reservation
    held its pool lane indefinitely because no stop would ever be recorded.

    The gone verdict covers a reservation only when the probe targeted its own
    body handle (``probed_handle``) or its retained native identity is the
    owner session that was probed. Rows owned by another operation (an
    in-flight cooling guard, a pending release, a fenced or incomplete
    startup, a retired conversation, or an unresolved owner wake) are left for
    that operation's own reconciliation, and so is any startup not captured in
    ``probed`` before the probe ran: a restart that completed during the probe
    is a newer body the gone verdict never observed.
    """
    from .ordinary_native_bindings import reservation_native_identity
    from .queue_execution_wakes import require_no_wake_for_cooling

    task = queue._fetch(conn, task_id)
    if task is None or task.status != Status.SUSPENDED:
        return 0
    try:
        require_no_wake_for_cooling(queue, conn, task)
    except TaskError:
        return 0
    cooled = 0
    for row in conn.execute(_BODY_SQL, (task_id, SpawnState.SPAWNED)).fetchall():
        if _body(row) not in probed:
            continue
        if not probed_handle:
            try:
                native = reservation_native_identity(conn, row)
            except TaskError:
                continue
            if native is None or native != task.owner_session_id:
                continue
        cooled += conn.execute(
            "UPDATE spawn_reservations SET state = ?, updated_at = ?,"
            " exclusive_released = CASE WHEN ? THEN 1 ELSE exclusive_released END"
            " WHERE key = ? AND state = ? AND startup_revision IS ?",
            (
                SpawnState.COLD, ts, 1 if task.awaiting_steer else 0, row["key"],
                SpawnState.SPAWNED, row["startup_revision"],
            ),
        ).rowcount
    return cooled


class SpawnCoolingMixin:
    if TYPE_CHECKING:
        def _connect(self) -> sqlite3.Connection: ...
        def _fetch(self, conn: sqlite3.Connection, task_id: str) -> Task | None: ...
        def _now(self, now: float | None) -> float: ...
        _update_reservation: Callable[..., SpawnReservation]

    def record_cold(
        self, key: str, *, release_exclusive: bool = False,
        startup_revision: int | None = None, cooling_token: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Publish exact stop completion and consume its operation guard.

        Legacy callers without a guard cannot supersede a guarded stop.
        ``release_exclusive`` frees the resource while cold; resume reacquires it.
        """
        if startup_revision is not None and (
            type(startup_revision) is not int or startup_revision < 1
        ):
            raise TaskError("cold transition requires the current startup revision")
        return self._update_reservation(
            key, to_state=SpawnState.COLD,
            allowed_from=frozenset({SpawnState.SPAWNED, SpawnState.COLD}),
            expected_startup_revision=startup_revision, cooling_token=cooling_token,
            exclusive_released=release_exclusive or None, now=now,
        )

    def claim_spawn_cooling(
        self, key: str, *, startup_revision: int, session_handle: str,
        state: str, now: float | None = None,
    ) -> SpawnReservation:
        """Hold stop/resume exclusion until exact cold/deferral publication.

        There is deliberately no lease: a timed-out stop can still take effect.
        A lost response or ambiguous stop retains the blocker for reconciliation.
        """
        if type(startup_revision) is not int or startup_revision < 1:
            raise TaskError("cooling requires the current startup revision")
        if state not in {SpawnState.SPAWNED, SpawnState.COLD} or not session_handle:
            raise TaskError("cooling requires an exact dormant body")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key=?", (key,),
            ).fetchone()
            task = self._fetch(conn, row["task_id"]) if row else None
            if (
                row is None or task is None or row["state"] != state
                or row["startup_revision"] != startup_revision
                or row["session_handle"] != session_handle
                or not row["startup_complete"] or row["startup_fenced"]
                or row["release_requested"] or row["conversation_retired"]
                or not (
                    state == SpawnState.SPAWNED and task.status == Status.SUSPENDED
                    or state == SpawnState.COLD and task.status == Status.QUEUED
                    and task.owner is None and task.owner_session_id is None
                )
            ):
                raise TaskError("dormant startup changed or remains incomplete")
            require_no_cooling(row)
            from .queue_execution_wakes import require_no_wake_for_cooling
            require_no_wake_for_cooling(cast("TaskQueue", self), conn, task)
            conn.execute(
                "UPDATE spawn_reservations SET cooling_token=?, updated_at=? WHERE key=?",
                (uuid.uuid4().hex, self._now(now), key),
            )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key=?", (key,),
            ).fetchone()
            conn.commit()
        return SpawnReservation._from_row(updated)
