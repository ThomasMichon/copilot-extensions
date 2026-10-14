"""Transactional worker heartbeats and progress for TaskQueue."""

from __future__ import annotations

import json

from .queue_common import PROGRESS_SUMMARY_MAX, Task, _clip, _progress_snapshot
from .queue_records import Status, TaskError


class QueueProgressMixin:
    """Queue-owned storage helpers enforce worker incarnation inside each write."""

    def heartbeat(
        self, task_id: str, worker_id: str, *, now: float | None = None,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
    ) -> Task:
        """Extend the lease on a held task the worker still owns."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in Status.HELD:
                conn.execute("COMMIT")
                raise TaskError(f"cannot heartbeat a {task.status!r} task")
            if task.owner != worker_id:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}")
            if expected_generation is not None and (
                task.generation != expected_generation
                or task.owner_session_id != expected_owner_session_id
            ):
                raise TaskError(f"task {task_id!r} ownership incarnation changed")
            conn.execute(
                "UPDATE tasks SET lease_expires_at = ?, updated_at = ? WHERE id = ?",
                (ts + self.lease_seconds, ts, task_id),
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]


    def record_progress(
        self,
        task_id: str,
        worker_id: str,
        *,
        phase: str,
        summary: str,
        blocker: str | None = None,
        pr: str | None = None,
        detail: str | None = None,
        extend_lease: bool = True,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
        now: float | None = None,
    ) -> Task:
        """Record a bounded progress beat on a held task the worker owns."""
        ts = self._now(now)
        snapshot = _progress_snapshot(phase, summary, blocker=blocker, pr=pr, ts=ts)
        payload = json.dumps(snapshot, separators=(",", ":"))
        log_detail = _clip(detail, PROGRESS_SUMMARY_MAX)
        if log_detail is None:
            parts = []
            if snapshot.get("blocker"):
                parts.append(f"blocker: {snapshot['blocker']}")
            if snapshot.get("pr"):
                parts.append(f"pr: {snapshot['pr']}")
            log_detail = "; ".join(parts) or None
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            if task.status not in Status.HELD:
                conn.execute("COMMIT")
                raise TaskError(f"cannot record progress on a {task.status!r} task")
            if task.owner != worker_id:
                conn.execute("COMMIT")
                raise TaskError(f"task {task_id!r} owned by {task.owner!r}, not {worker_id!r}")
            if expected_generation is not None and (
                task.generation != expected_generation
                or task.owner_session_id != expected_owner_session_id
            ):
                raise TaskError(f"task {task_id!r} ownership incarnation changed")
            if extend_lease:
                conn.execute(
                    "UPDATE tasks SET latest_progress = ?, lease_expires_at = ?,"
                    " last_seen_at = ?, updated_at = ? WHERE id = ?",
                    (payload, ts + self.lease_seconds, ts, ts, task_id),
                )
            else:
                conn.execute(
                    "UPDATE tasks SET latest_progress = ?, last_seen_at = ?,"
                    " updated_at = ? WHERE id = ?",
                    (payload, ts, ts, task_id),
                )
            conn.execute(
                "INSERT INTO task_progress (task_id, ts, phase, summary, detail, worker) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    task_id,
                    ts,
                    snapshot.get("phase") or None,
                    snapshot["summary"],
                    log_detail,
                    worker_id,
                ),
            )
            phase_tag = f"[{snapshot['phase']}] " if snapshot.get("phase") else ""
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=worker_id,
                note=f"progress: {phase_tag}{snapshot['summary']}",
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]
