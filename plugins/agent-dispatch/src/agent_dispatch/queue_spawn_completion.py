"""Publish startup identity and completion through the reservation transaction."""

from __future__ import annotations

import sqlite3

from .queue_records import SpawnReservation, SpawnState, Status, TaskError
from .queue_spawn_cooling import SpawnCoolingMixin, require_no_cooling


class SpawnCompletionMixin(SpawnCoolingMixin):
    def fence_spawn_startup(
        self, key: str, *, startup_revision: int, now: float | None = None,
    ) -> SpawnReservation:
        """Close terminal startup admission without releasing resource custody."""
        if type(startup_revision) is not int or startup_revision < 1:
            raise TaskError("startup fence requires the current startup revision")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key=?", (key,),
            ).fetchone()
            task = self._fetch(conn, row["task_id"]) if row else None
            if (
                row is None or task is None or task.status not in Status.CONCLUDED
                or row["startup_revision"] != startup_revision
                or row["state"] not in {SpawnState.SPAWNED, SpawnState.COLD}
                or not row["startup_complete"]
            ):
                raise TaskError("terminal startup changed or remains incomplete")
            require_no_cooling(row)
            if not row["startup_fenced"]:
                conn.execute(
                    "UPDATE spawn_reservations SET startup_fenced=1, updated_at=? WHERE key=?",
                    (self._now(now), key),
                )
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key=?", (key,),
            ).fetchone()
            conn.commit()
        return SpawnReservation._from_row(row)

    def record_staging_spawn(self, key: str, session_handle: str) -> SpawnReservation:
        """Persist an idle body before admission without making it claimable."""
        return self._update_reservation(
            key, to_state=SpawnState.RESERVING,
            allowed_from=frozenset({SpawnState.RESERVING}),
            session_handle=session_handle,
        )

    def record_spawn(
        self,
        key: str,
        *,
        session_handle: str | None = None,
        worktree: str | None = None,
        startup_complete: bool | None = None,
        startup_revision: int | None = None,
        now: float | None = None,
        authority_expected: tuple | None = None,
    ) -> SpawnReservation:
        """Publish a body identity, optionally closing the launch caller.

        Only a successful launch return establishes ``startup_complete``;
        recovery promotion alone leaves the original caller unproven.
        A concurrent release retains its state and intent when that caller returns.
        """
        if startup_complete is not None and type(startup_complete) is not bool:
            raise TaskError("startup_complete must be a boolean")
        if startup_complete is not None and (
            type(startup_revision) is not int or startup_revision < 1
        ):
            raise TaskError("startup completion requires the current startup revision")
        if startup_complete and not (isinstance(session_handle, str) and session_handle.strip()):
            raise TaskError("startup completion requires an exact session handle")

        def authority_guard(row: sqlite3.Row) -> None:
            if row["state"] == SpawnState.RELEASING and (
                row["startup_fenced"]
                or row["startup_complete"] and session_handle != row["session_handle"]
            ):
                raise TaskError("released startup is fenced or already completed")
            if authority_expected is not None:
                from .ci_repair_spawn import authority_snapshot

                with self._connect() as probe:
                    task = self._fetch(probe, row["task_id"])
                    if authority_snapshot(probe, task) != authority_expected:
                        raise TaskError("CI repair authority changed before spawn publication")

        return self._update_reservation(
            key,
            to_state=SpawnState.SPAWNED,
            allowed_from=frozenset({SpawnState.RESERVING, SpawnState.SPAWNED, SpawnState.COLD})
            | (frozenset({SpawnState.RELEASING}) if startup_complete is True else frozenset()),
            preserve_releasing=startup_complete is True,
            session_handle=session_handle,
            startup_complete=startup_complete,
            expected_startup_revision=startup_revision,
            worktree=worktree,
            exclusive_released=False,
            now=now,
            guard=authority_guard,
        )
