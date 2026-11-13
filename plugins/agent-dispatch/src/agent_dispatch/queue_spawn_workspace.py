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
        creation_nonce: str | None = None,
        worktree_writer_requirement: dict | None = None,
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
            from .queue_pool_placement import pool_assignment, require_pool_workspace
            from .bridge_remote import normalize_host
            task = self._fetch(conn, row["task_id"])
            if row["task_relationship_required"] and creation_nonce is None:
                raise TaskError("governed workspace attachment requires its original creation nonce")
            if creation_nonce is not None and not row["task_relationship_required"]:
                raise TaskError("legacy reservation cannot adopt a new workspace relationship")
            if pool_assignment(conn, task.id) is not None and driver != row["driver"]:
                raise TaskError("pool workspace cannot change its placed startup driver")
            if execution_machine is not None and (
                not isinstance(execution_machine, str) or not execution_machine.strip()
            ):
                raise TaskError("execution machine must be non-empty")
            if (
                row["execution_machine"] is not None and execution_machine is not None
                and normalize_host(row["execution_machine"]) != normalize_host(execution_machine)
            ):
                raise TaskError("recorded execution machine cannot change")
            execution_machine = row["execution_machine"] or execution_machine
            require_pool_workspace(
                conn, task, worktree, execution_machine, driver=driver, reservation_key=key,
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
            from .ordinary_writer_admission import prepared_requirement
            retained_writer = prepared_requirement(
                dict(row), worktree=worktree, project=allocation_project, path=worktree_path,
                creation_nonce=creation_nonce, value=worktree_writer_requirement,
            )
            from .queue_session_origin import seal_preparation
            seal_preparation(conn, row)
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
                "driver = ?, allocation_project = ?, worktree_path = ?, "
                "worktree_writer_requirement = ?, updated_at = ? WHERE key = ?",
                (
                    worktree, worktree, ownership,
                    creating_host if ownership == "created" else row["creating_host"],
                    execution_machine, driver, allocation_project, worktree_path,
                    retained_writer, ts, key,
                ),
            )
            updated = conn.execute(
                "SELECT * FROM spawn_reservations WHERE key = ?", (key,),
            ).fetchone()
            relationship_event = row["task_relationship_event_id"]
            if creation_nonce is not None:
                from .assignment_workspace import AssignmentWorkspace
                workspace = AssignmentWorkspace(
                    execution_machine, allocation_project, worktree, worktree_path, creation_nonce,
                )
                if row["creation_nonce"] not in (None, creation_nonce):
                    raise TaskError("recorded workspace incarnation cannot change")
                if relationship_event is None:
                    relationship_event = self._retain_task_relationship(
                        conn, task, workspace, worker_id=workspace.worker_id,
                        generation=task.generation, ts=ts, reservation_key=key,
                    )
                else:
                    import json
                    retained = conn.execute(
                        "SELECT task_relationship FROM task_events WHERE id=?",
                        (relationship_event,),
                    ).fetchone()
                    if json.loads(retained["task_relationship"])["request"]["workspace"] != {
                        "machine": workspace.machine, "project": workspace.project,
                        "worktree": workspace.worktree, "path": workspace.path,
                        "creation_nonce": workspace.creation_nonce,
                    }:
                        raise TaskError("recorded relationship workspace cannot change")
                conn.execute(
                    "UPDATE spawn_reservations SET creation_nonce=?, task_relationship_event_id=?,"
                    " task_relationship_required=1,"
                    " task_relationship_generation=COALESCE(task_relationship_generation, ?) WHERE key=?",
                    (creation_nonce, relationship_event, task.generation, key),
                )
            elif relationship_event is not None:
                raise TaskError("governed workspace attachment requires its original creation nonce")
            conn.execute("COMMIT")
        if relationship_event is not None:
            self.reconcile_task_relationship(task.id, event_id=relationship_event)
            return self.get_reservation(key)
        return SpawnReservation._from_row(updated)
