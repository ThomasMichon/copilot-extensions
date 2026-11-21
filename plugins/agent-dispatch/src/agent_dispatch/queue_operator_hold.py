"""Transactional operator holds and their pool recovery clock adjustments."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from .queue_common import PROGRESS_SUMMARY_MAX, Task, _check_expected_status, _clip
from .queue_records import Status, TaskError


class OperatorHoldMixin:
    if TYPE_CHECKING:
        def _connect(self) -> sqlite3.Connection: ...
        def _fetch(self, conn: sqlite3.Connection, task_id: str) -> Task | None: ...
        def _now(self, now: float | None) -> float: ...
        def _audit(self, conn: sqlite3.Connection, task_id: str, *, ts: float,
                   from_status: str, to_status: str, worker: str | None, note: str | None,
                   unassigned_worker: str | None = None) -> None: ...

    def set_hold(
        self,
        task_id: str,
        *,
        reason: str,
        actor: str,
        expected_status: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Set a durable, operator-owned hold on ``task_id``."""
        meaningful = _clip(reason, PROGRESS_SUMMARY_MAX)
        if meaningful is None:
            raise TaskError("set_hold requires a non-empty reason")
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            _check_expected_status(task, expected_status)
            if task.status in Status.CONCLUDED:
                conn.execute("COMMIT")
                raise TaskError(f"cannot hold a {task.status!r} task {task_id!r}")
            from .queue_pool_recovery import hold_recovery
            hold_recovery(conn, task_id, held=True, ts=ts)
            conn.execute(
                "UPDATE tasks SET hold_reason = ?, hold_actor = ?, hold_at = ?,"
                " updated_at = ? WHERE id = ?",
                (meaningful, actor, ts, ts, task_id),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=actor,
                note=f"hold: {meaningful}",
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]

    def clear_hold(
        self,
        task_id: str,
        *,
        actor: str | None = None,
        expected_status: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Clear a hold set by :meth:`set_hold`."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.hold_reason is None:
                conn.execute("COMMIT")
                return task
            _check_expected_status(task, expected_status)
            from .queue_pool_recovery import hold_recovery
            hold_recovery(conn, task_id, held=False, ts=ts)
            conn.execute(
                "UPDATE tasks SET hold_reason = NULL, hold_actor = NULL,"
                " hold_at = NULL, updated_at = ? WHERE id = ?",
                (ts, task_id),
            )
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=actor,
                note=f"unhold (was: {task.hold_reason})",
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]
