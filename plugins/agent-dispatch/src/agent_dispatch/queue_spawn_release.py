"""Durable release intent for an existing spawn reservation."""

from __future__ import annotations

from .queue_records import SpawnReservation, SpawnState, TaskError
from .queue_spawn_cooling import require_no_cooling
from .queue_execution_wakes import require_no_wake_for_release


class SpawnReleaseMixin:
    def retire_spawn(
        self,
        key: str,
        *,
        exact_absence: bool,
        detail: str | None = None,
        conclusion_state: str | None = None,
        conclusion_detail: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Retire a release on exact absence without reviving historical ownership."""
        from .queue_spawn_reservations import _newer_worktree_reservation, _validate_conclusion_claim

        if not exact_absence:
            raise TaskError("spawn retirement requires exact absence proof")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            historical = bool(row["native_release_pending"]) and row["state"] not in SpawnState.ACTIVE
            from .queue_pool_decline import require_no_pool_decline
            require_no_pool_decline(conn, row["task_id"], reservation_key=key)
            if row["state"] != SpawnState.RELEASING and not historical:
                conn.execute("COMMIT")
                raise TaskError(f"reservation {key} is {row['state']!r}, not releasing")
            require_no_cooling(row)
            if not row["startup_complete"] and not row["startup_fenced"]:
                raise TaskError("spawn retirement requires completed startup; original caller remains unresolved")
            if historical and (conclusion_state is not None or conclusion_detail is not None):
                if _newer_worktree_reservation(conn, row, key) is not None:
                    raise TaskError("historical native retirement cannot mutate carried workspace cleanup")
                _validate_conclusion_claim(
                    row["cleanup_claim_token"], row["cleanup_claim_expires_at"], None,
                    now=ts, required=row["conclusion_state"] == "pending",
                )
            to_state = row["state"] if historical else (
                SpawnState.SETTLED if row["release_disposition"] == "settled" else SpawnState.FAILED
            )
            conn.execute(
                "UPDATE spawn_reservations SET state = ?, updated_at = ?, "
                "native_release_pending = 0, "
                "conversation_retired = CASE WHEN ? THEN 1 ELSE conversation_retired END, "
                "detail = COALESCE(?, detail), "
                "conclusion_state = CASE WHEN ? IS NOT NULL THEN ? "
                "ELSE conclusion_state END, "
                "conclusion_detail = CASE WHEN ? IS NOT NULL THEN ? "
                "ELSE conclusion_detail END WHERE key = ?",
                (
                    to_state,
                    ts,
                    historical,
                    detail,
                    conclusion_state,
                    conclusion_state,
                    conclusion_detail,
                    conclusion_detail,
                    key,
                ),
            )
            latest = conn.execute(
                "SELECT key FROM spawn_reservations WHERE task_id = ? "
                "ORDER BY attempt DESC LIMIT 1",
                (row["task_id"],),
            ).fetchone()
            if latest is not None and latest["key"] == key:
                conn.execute(
                    "UPDATE tasks SET activity = NULL, activity_updated_at = ? WHERE id = ?",
                    (ts, row["task_id"]),
                )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?",
                (key,),
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(updated)

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
            from .queue_pool_decline import require_no_pool_decline
            require_no_pool_decline(conn, row["task_id"], reservation_key=key)
            require_no_cooling(row)
            require_no_wake_for_release(self, conn, row)
            terminal_pool = row["state"] in {
                SpawnState.FAILED, SpawnState.SETTLED, SpawnState.REARMED, SpawnState.DEFERRED,
            } and conn.execute(
                "SELECT 1 FROM pool_reservation_bindings WHERE reservation_key = ?", (key,),
            ).fetchone() is not None
            if row["state"] not in SpawnState.ACTIVE and not terminal_pool:
                conn.execute("ROLLBACK")
                raise TaskError(
                    f"reservation {key} is {row['state']!r}, not active or a terminal pool attempt (cannot request release)"
                )
            if worktree and row["worktree"] and row["worktree"] != worktree:
                conn.execute("ROLLBACK")
                raise TaskError(
                    f"reservation {key} worktree is {row['worktree']!r}, "
                    f"not {worktree!r}"
                )
            clear_retirement = False
            if session_handle is not None:
                from .queue_pool_placement import pool_native_publication
                task = self._fetch(conn, row["task_id"])
                clear_retirement = pool_native_publication(
                    conn, task, row, session_handle, releasing=True,
                )
            from .ordinary_native_bindings import is_native_retired, reservation_native_identity
            retired_replay = bool(
                terminal_pool and row["release_requested"] and not row["native_release_pending"]
                and (row["startup_complete"] or row["startup_fenced"])
                and is_native_retired(row) and not clear_retirement
                and (
                    session_handle in (None, row["session_handle"])
                    or session_handle == reservation_native_identity(conn, row)
                )
                and worktree in (None, row["worktree"])
            )
            if finished_startup_revision is not None:
                if row["startup_revision"] != finished_startup_revision:
                    raise TaskError("startup revision changed before failed startup closure")
                # The original caller has returned definitively. Fence future
                # startup without pretending its failed launch was successful.
                conn.execute(
                    "UPDATE spawn_reservations SET startup_fenced = 1 WHERE key = ?", (key,),
                )
            if row["state"] == SpawnState.RELEASING or terminal_pool:
                release_disposition = (
                    "failed"
                    if "failed" in {row["release_disposition"], disposition}
                    else row["release_disposition"] or disposition
                )
                conn.execute(
                    "UPDATE spawn_reservations SET release_requested = 1, "
                    "native_release_pending = ?, "
                    "conversation_retired = CASE WHEN ? THEN 0 ELSE conversation_retired END, "
                    "release_disposition = ?, detail = COALESCE(?, detail), "
                    "session_handle = COALESCE(?, session_handle), "
                    "worktree = COALESCE(?, worktree), "
                    "updated_at = ? WHERE key = ?",
                    (
                        int(terminal_pool and not retired_replay),
                        clear_retirement,
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
                    "conversation_retired = CASE WHEN ? THEN 0 ELSE conversation_retired END, "
                    "detail = COALESCE(?, detail), "
                    "session_handle = COALESCE(?, session_handle), "
                    "worktree = COALESCE(?, worktree), "
                    "conclusion_state = COALESCE(conclusion_state, ?), "
                    "updated_at = ? WHERE key = ?",
                    (
                        SpawnState.RELEASING,
                        disposition,
                        clear_retirement,
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
