"""Bind prepared workspace facts through the spawn reservation owner."""

from __future__ import annotations

from .queue_records import SpawnReservation, SpawnState, TaskError


class SpawnWorkspaceMixin:
    def record_spawn_worktree(
        self,
        key: str,
        worktree: str,
        *,
        ownership: str = "unknown",
        creating_host: str | None = None,
        execution_machine: str | None = None,
        driver: str | None = None,
        allocation_project: str | None = None,
        worktree_path: str | None = None,
        now: float | None = None,
    ) -> SpawnReservation:
        """Record a reusable worktree while the reservation is still reserving.

        Replacing a carried worktree clears its carried session handle: a
        confirmed-missing worktree cannot safely retain a session binding from
        the vanished checkout.
        """
        worktree = worktree.strip()
        if not worktree:
            raise TaskError("spawn worktree must be non-empty")
        if ownership not in {"created", "targeted", "reused", "unknown"}:
            raise TaskError(f"invalid spawn worktree ownership: {ownership!r}")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?", (key,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {key}")
            if row["state"] != SpawnState.RESERVING:
                conn.execute("COMMIT")
                raise TaskError(
                    f"reservation {key} is {row['state']!r}, not {SpawnState.RESERVING!r}"
                )
            from .queue_pool_placement import require_pool_workspace
            task = self._fetch(conn, row["task_id"])
            if execution_machine is not None and (
                not isinstance(execution_machine, str) or not execution_machine.strip()
            ):
                raise TaskError("execution machine must be non-empty")
            if (
                row["execution_machine"] is not None and execution_machine is not None
                and row["execution_machine"] != execution_machine
            ):
                raise TaskError("recorded execution machine cannot change")
            execution_machine = execution_machine or row["execution_machine"]
            require_pool_workspace(
                conn, task, worktree, execution_machine, driver=driver,
            )
            if row["allocation_project"] or row["worktree_path"]:
                if (
                    row["worktree"] is not None and row["worktree"] != worktree
                    or row["allocation_project"] is not None and row["allocation_project"] != allocation_project
                    or row["worktree_path"] is not None and row["worktree_path"] != worktree_path
                ):
                    raise TaskError("recorded workspace identity cannot change")
            cleanup_claims = conn.execute(
                "SELECT key FROM spawn_reservations "
                "WHERE key <> ? AND worktree = ? AND state IN (?, ?) "
                "AND conclusion_state = ?",
                (key, worktree, SpawnState.FAILED, SpawnState.SETTLED, "pending"),
            ).fetchall()
            for cleanup_claim in cleanup_claims:
                conn.execute("COMMIT")
                raise TaskError(
                    f"worktree {worktree!r} has in-flight cleanup {cleanup_claim['key']}"
                )
            conn.execute(
                "UPDATE spawn_reservations SET "
                "session_handle = CASE "
                "WHEN worktree IS NULL OR worktree <> ? THEN NULL "
                "ELSE session_handle END, "
                "inherited_worktree = CASE "
                "WHEN inherited_worktree IS NOT NULL THEN inherited_worktree "
                "WHEN worktree_ownership = 'reused' THEN worktree "
                "ELSE inherited_worktree END, "
                "worktree = ?, worktree_ownership = ?, creating_host = ?, execution_machine = ?, "
                "driver = ?, allocation_project = ?, worktree_path = ?, updated_at = ? WHERE key = ?",
                (
                    worktree, worktree, ownership,
                    creating_host if ownership == "created" else row["creating_host"],
                    execution_machine, driver, allocation_project, worktree_path, ts, key,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?", (key,),
            ).fetchone()
            conn.execute("COMMIT")
        return SpawnReservation._from_row(updated)
