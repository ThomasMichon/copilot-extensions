"""Durable exclusion between physical cooling and startup/resume publication."""

from __future__ import annotations

import sqlite3
import uuid

from .queue_records import SpawnReservation, SpawnState, Status, TaskError


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


class SpawnCoolingMixin:
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
            conn.execute(
                "UPDATE spawn_reservations SET cooling_token=?, updated_at=? WHERE key=?",
                (uuid.uuid4().hex, self._now(now), key),
            )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key=?", (key,),
            ).fetchone()
            conn.commit()
        return SpawnReservation._from_row(updated)
