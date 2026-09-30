"""Durable `run --detach` waiter registrations and event-note journaling.

This mixin owns the narrow Phase 2b/2c state that `run` needs beyond the task
row itself: a durable record of the outstanding detached waiter process, and a
small append-only event-note write path emitters can use without rewriting the
task goal.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from .queue_common import PROGRESS_SUMMARY_MAX, Task, _clip
from .queue_records import Status, TaskError


class QueueRunWaitersMixin:
    """Detached-run waiter and event-note helpers for :class:`TaskQueue`."""

    def register_run_waiter(
        self,
        task_id: str,
        *,
        pid: int,
        host: str | None,
        start_token: str | None,
        resume_worktree: str,
        command: list[str],
        now: float | None = None,
    ) -> dict[str, Any]:
        ts = self._now(now)
        if not resume_worktree:
            raise TaskError("run waiter registration requires a resume_worktree")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status != Status.SUSPENDED:
                conn.execute("COMMIT")
                raise TaskError(f"cannot register a run waiter on a {task.status!r} task")
            previous = conn.execute(
                "SELECT MAX(generation) AS generation FROM run_waiters WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            generation = int(previous["generation"] or 0) + 1
            conn.execute(
                "UPDATE run_waiters SET state = 'superseded', updated_at = ?,"
                " retired_reason = COALESCE(retired_reason, 'superseded by a new waiter')"
                " WHERE task_id = ? AND state = 'active'",
                (ts, task_id),
            )
            conn.execute(
                "INSERT INTO run_waiters ("
                " task_id, generation, pid, host, start_token, resume_worktree,"
                " command_json, state, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
                (
                    task_id,
                    generation,
                    int(pid),
                    host,
                    start_token,
                    resume_worktree,
                    json.dumps(command, separators=(",", ":")),
                    ts,
                    ts,
                ),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=task.owner,
                note=f"run waiter registered (generation {generation})",
            )
            conn.execute("COMMIT")
        return {
            "task_id": task_id,
            "generation": generation,
            "pid": int(pid),
            "host": host,
            "start_token": start_token,
            "resume_worktree": resume_worktree,
            "command": list(command),
            "state": "active",
        }

    def get_active_run_waiter(self, task_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM run_waiters WHERE task_id = ? AND state = 'active'"
                " ORDER BY generation DESC LIMIT 1",
                (task_id,),
            ).fetchone()
        return self._run_waiter_from_row(row) if row else None

    def list_active_run_waiters(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM run_waiters WHERE state = 'active'"
                " ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return [self._run_waiter_from_row(row) for row in rows]

    def supersede_run_waiter(
        self,
        task_id: str,
        *,
        reason: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM run_waiters WHERE task_id = ? AND state = 'active'"
                " ORDER BY generation DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            waiter = self._run_waiter_from_row(row)
            conn.execute(
                "UPDATE run_waiters SET state = 'superseded', updated_at = ?, retired_reason = ?"
                " WHERE id = ? AND state = 'active'",
                (ts, reason, row["id"]),
            )
            task = self._fetch(conn, task_id)
            if task is not None:
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=task.status,
                    to_status=task.status,
                    worker=task.owner,
                    note=f"run waiter superseded ({reason})",
                )
            conn.execute("COMMIT")
        waiter["state"] = "superseded"
        waiter["retired_reason"] = reason
        return waiter

    def retire_run_waiter(
        self,
        task_id: str,
        *,
        pid: int,
        host: str | None,
        start_token: str | None,
        reason: str,
        now: float | None = None,
    ) -> dict[str, Any] | None:
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM run_waiters WHERE task_id = ? AND state = 'active'"
                " AND pid = ? AND COALESCE(host, '') = COALESCE(?, '')"
                " AND ((start_token IS NULL AND ? IS NULL) OR start_token = ?)"
                " ORDER BY generation DESC LIMIT 1",
                (task_id, int(pid), host, start_token, start_token),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                return None
            waiter = self._run_waiter_from_row(row)
            conn.execute(
                "UPDATE run_waiters SET state = 'retired', updated_at = ?, retired_reason = ?"
                " WHERE id = ? AND state = 'active'",
                (ts, reason, row["id"]),
            )
            task = self._fetch(conn, task_id)
            if task is not None:
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=task.status,
                    to_status=task.status,
                    worker=task.owner,
                    note=f"run waiter retired ({reason})",
                )
            conn.execute("COMMIT")
        waiter["state"] = "retired"
        waiter["retired_reason"] = reason
        return waiter

    def append_event_note(
        self,
        task_id: str,
        *,
        sender: str,
        note: str,
        now: float | None = None,
    ) -> Task:
        ts = self._now(now)
        meaningful = _clip(note, PROGRESS_SUMMARY_MAX)
        if meaningful is None:
            raise TaskError("event note requires a non-empty note")
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status in Status.TERMINAL:
                conn.execute("COMMIT")
                raise TaskError(f"cannot append an event note to a {task.status!r} task")
            conn.execute(
                "UPDATE tasks SET updated_at = ? WHERE id = ?",
                (ts, task_id),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=sender,
                note=f"event note: {meaningful}",
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    @staticmethod
    def _run_waiter_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "task_id": row["task_id"],
            "generation": row["generation"],
            "pid": row["pid"],
            "host": row["host"],
            "start_token": row["start_token"],
            "resume_worktree": row["resume_worktree"],
            "command": json.loads(row["command_json"] or "[]"),
            "state": row["state"],
            "retired_reason": row["retired_reason"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
