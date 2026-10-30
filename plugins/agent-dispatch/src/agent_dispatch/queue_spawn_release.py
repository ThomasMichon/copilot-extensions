"""Durable release intent for an existing spawn reservation."""

from __future__ import annotations

from .queue_records import SpawnReservation, SpawnState, TaskError
from .queue_spawn_cooling import require_no_cooling
from .queue_execution_wakes import require_no_wake_for_release


class SpawnReleaseMixin:
    def request_spawn_release(
        self,
        key: str,
        *,
        detail: str | None = None,
        disposition: str = "failed",
        session_handle: str | None = None,
        worktree: str | None = None,
        finished_startup_revision: int | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Fence an attempt and atomically retain any failed body identity."""
        if disposition not in {"failed", "settled"}:
            raise TaskError(f"invalid spawn release disposition: {disposition!r}")
        if finished_startup_revision is not None and (
            type(finished_startup_revision) is not int or finished_startup_revision < 1
        ):
            raise TaskError("finished startup requires its current revision")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
            if row is None:
                conn.execute("ROLLBACK")
                raise TaskError(f"no such reservation: {key}")
            require_no_cooling(row)
            require_no_wake_for_release(self, conn, row)
            if row["state"] not in SpawnState.ACTIVE:
                conn.execute("ROLLBACK")
                raise TaskError(
                    f"reservation {key} is {row['state']!r}, not active (cannot request release)"
                )
            if worktree and row["worktree"] and row["worktree"] != worktree:
                conn.execute("ROLLBACK")
                raise TaskError(
                    f"reservation {key} worktree is {row['worktree']!r}, "
                    f"not {worktree!r}"
                )
            if finished_startup_revision is not None:
                if row["startup_revision"] != finished_startup_revision:
                    raise TaskError("startup revision changed before failed startup closure")
                # The original caller has returned definitively. Fence future
                # startup without pretending its failed launch was successful.
                conn.execute(
                    "UPDATE spawn_reservations SET startup_fenced = 1 WHERE key = ?", (key,),
                )
            if row["state"] == SpawnState.RELEASING:
                release_disposition = (
                    "failed"
                    if "failed" in {row["release_disposition"], disposition}
                    else row["release_disposition"] or disposition
                )
                conn.execute(
                    "UPDATE spawn_reservations SET release_requested = 1, "
                    "release_disposition = ?, detail = COALESCE(?, detail), "
                    "session_handle = COALESCE(?, session_handle), "
                    "worktree = COALESCE(?, worktree), "
                    "updated_at = ? WHERE key = ?",
                    (
                        release_disposition,
                        detail,
                        session_handle,
                        worktree,
                        ts,
                        key,
                    ),
                )
            else:
                conn.execute(
                    "UPDATE spawn_reservations SET state = ?, "
                    "release_requested = 1, release_disposition = ?, "
                    "detail = COALESCE(?, detail), "
                    "session_handle = COALESCE(?, session_handle), "
                    "worktree = COALESCE(?, worktree), "
                    "conclusion_state = COALESCE(conclusion_state, ?), "
                    "updated_at = ? WHERE key = ?",
                    (
                        SpawnState.RELEASING,
                        disposition,
                        detail,
                        session_handle,
                        worktree,
                        "pending",
                        ts,
                        key,
                    ),
                )
            row = conn.execute("SELECT * FROM spawn_reservations WHERE key = ?", (key,)).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(row)
