"""SQLite-backed persistent task queue for agent-index indexing.

Each public method opens its own connection (WAL mode) so callers can
safely use ``asyncio.to_thread()`` without sharing state across threads.

Modelled after analysis-feed's ``engine/job_store.py``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS tasks (
    id              TEXT PRIMARY KEY,
    source          TEXT NOT NULL DEFAULT 'all',
    full            INTEGER NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'queued',
    progress_stage  TEXT NOT NULL DEFAULT '',
    progress_pct    REAL NOT NULL DEFAULT 0.0,
    progress_msg    TEXT NOT NULL DEFAULT '',
    error           TEXT,
    trigger_source  TEXT NOT NULL DEFAULT 'unknown',
    result_stats    TEXT,
    attempt_count   INTEGER NOT NULL DEFAULT 1,
    created_at      REAL NOT NULL,
    started_at      REAL,
    finished_at     REAL,
    updated_at      REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_created
    ON tasks(status, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_finished_at
    ON tasks(finished_at);
"""


class TaskStatus(StrEnum):
    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETE = "complete"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


TERMINAL = {TaskStatus.COMPLETE, TaskStatus.FAILED, TaskStatus.CANCELLED, TaskStatus.INTERRUPTED}


@dataclass
class TaskRecord:
    """Flat representation of a stored task row."""

    id: str
    source: str
    full: bool
    status: str
    progress_stage: str
    progress_pct: float
    progress_msg: str
    error: str | None
    trigger_source: str
    result_stats: dict[str, Any] | None
    attempt_count: int
    created_at: float
    started_at: float | None
    finished_at: float | None
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "source": self.source,
            "full": self.full,
            "status": self.status,
            "progress": {
                "stage": self.progress_stage,
                "percent": round(self.progress_pct, 1),
                "message": self.progress_msg,
            },
            "error": self.error,
            "trigger_source": self.trigger_source,
            "result_stats": self.result_stats,
            "attempt_count": self.attempt_count,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "updated_at": self.updated_at,
        }


def _row_to_record(row: sqlite3.Row) -> TaskRecord:
    # Parse result_stats JSON if present
    raw_stats = row["result_stats"] if "result_stats" in row.keys() else None
    result_stats: dict[str, Any] | None = None
    if raw_stats:
        try:
            result_stats = json.loads(raw_stats)
        except (json.JSONDecodeError, TypeError):
            pass

    return TaskRecord(
        id=row["id"],
        source=row["source"],
        full=bool(row["full"]),
        status=row["status"],
        progress_stage=row["progress_stage"],
        progress_pct=row["progress_pct"],
        progress_msg=row["progress_msg"],
        error=row["error"],
        trigger_source=row["trigger_source"] if "trigger_source" in row.keys() else "unknown",
        result_stats=result_stats,
        attempt_count=row["attempt_count"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        updated_at=row["updated_at"],
    )


class TaskStore:
    """SQLite-backed persistent task queue.

    Thread-safe: each method opens a fresh connection with WAL mode.
    """

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
            # Migrate: add columns if missing (existing DBs)
            cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
            if "trigger_source" not in cols:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN trigger_source TEXT NOT NULL DEFAULT 'unknown'"
                )
                conn.commit()
                log.info("Migrated tasks table: added trigger_source column")
            if "result_stats" not in cols:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN result_stats TEXT"
                )
                conn.commit()
                log.info("Migrated tasks table: added result_stats column")
        finally:
            conn.close()

    # ── Enqueue ──────────────────────────────────────────────

    def enqueue(self, source: str = "all", *, full: bool = False, trigger_source: str = "unknown") -> TaskRecord:
        """Create a new queued task and return it.

        Deduplication: if an identical task is already queued (not
        processing), returns that task instead of creating a duplicate.
        Uses BEGIN IMMEDIATE to prevent concurrent duplicate inserts.

        ``trigger_source`` records what initiated this task (e.g.
        'api:agent_index_reindex', 'webhook:forge:push', 'cli', 'mcp').
        """
        now = time.time()
        task_id = uuid.uuid4().hex[:12]

        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")

            # Dedup only against queued tasks (not processing — see design doc)
            existing = conn.execute(
                "SELECT * FROM tasks WHERE status = 'queued' AND source = ? AND full = ?",
                (source, int(full)),
            ).fetchone()
            if existing:
                conn.rollback()
                record = _row_to_record(existing)
                log.info("Deduped: existing task %s covers source=%s full=%s", record.id, source, full)
                return record

            conn.execute(
                """INSERT INTO tasks
                   (id, source, full, status, trigger_source, created_at, updated_at)
                   VALUES (?, ?, ?, 'queued', ?, ?, ?)""",
                (task_id, source, int(full), trigger_source, now, now),
            )
            conn.commit()
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            record = _row_to_record(row)
            log.info("Enqueued task %s: source=%s full=%s trigger=%s", task_id, source, full, trigger_source)
            return record
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Dequeue ──────────────────────────────────────────────

    def dequeue_next(self) -> TaskRecord | None:
        """Atomically claim the next queued task for processing.

        Returns ``None`` if the queue is empty.
        """
        now = time.time()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT id FROM tasks WHERE status = 'queued' ORDER BY created_at LIMIT 1",
            ).fetchone()
            if row is None:
                conn.rollback()
                return None

            task_id = row["id"]
            affected = conn.execute(
                """UPDATE tasks
                   SET status = 'processing', started_at = ?, updated_at = ?
                   WHERE id = ? AND status = 'queued'""",
                (now, now, task_id),
            ).rowcount
            conn.commit()

            if affected == 0:
                return None

            fresh = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return _row_to_record(fresh)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ── Status updates ───────────────────────────────────────

    def update_status(
        self,
        task_id: str,
        status: str,
        error: str | None = None,
    ) -> bool:
        """Transition a task to a new status. Returns True if updated."""
        now = time.time()
        finished = now if status in {s.value for s in TERMINAL} else None

        conn = self._connect()
        try:
            affected = conn.execute(
                """UPDATE tasks
                   SET status = ?, error = ?, finished_at = COALESCE(?, finished_at),
                       updated_at = ?
                   WHERE id = ?""",
                (status, error, finished, now, task_id),
            ).rowcount
            conn.commit()
            return affected > 0
        finally:
            conn.close()

    def update_progress(
        self,
        task_id: str,
        stage: str,
        pct: float,
        msg: str,
    ) -> None:
        """Persist progress for the active task."""
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                """UPDATE tasks
                   SET progress_stage = ?, progress_pct = ?, progress_msg = ?,
                       updated_at = ?
                   WHERE id = ? AND status = 'processing'""",
                (stage, pct, msg, now, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    def set_result_stats(self, task_id: str, stats: dict[str, Any]) -> None:
        """Store final crawl stats on a completed task."""
        now = time.time()
        conn = self._connect()
        try:
            conn.execute(
                """UPDATE tasks SET result_stats = ?, updated_at = ? WHERE id = ?""",
                (json.dumps(stats), now, task_id),
            )
            conn.commit()
        finally:
            conn.close()

    # ── Cancel ───────────────────────────────────────────────

    def cancel(self, task_id: str) -> bool:
        """Cancel a queued task. Running tasks are cancelled via the runner."""
        now = time.time()
        conn = self._connect()
        try:
            affected = conn.execute(
                """UPDATE tasks
                   SET status = 'cancelled', finished_at = ?, updated_at = ?
                   WHERE id = ? AND status = 'queued'""",
                (now, now, task_id),
            ).rowcount
            conn.commit()
            if affected:
                log.info("Task %s cancelled (was queued)", task_id)
            return affected > 0
        finally:
            conn.close()

    # ── Retry ────────────────────────────────────────────────

    def retry(self, task_id: str) -> TaskRecord | None:
        """Re-queue a failed, cancelled, or interrupted task."""
        now = time.time()
        conn = self._connect()
        try:
            affected = conn.execute(
                """UPDATE tasks
                   SET status = 'queued',
                       error = NULL,
                       progress_stage = '',
                       progress_pct = 0.0,
                       progress_msg = '',
                       started_at = NULL,
                       finished_at = NULL,
                       attempt_count = attempt_count + 1,
                       updated_at = ?
                   WHERE id = ? AND status IN ('failed', 'cancelled', 'interrupted')""",
                (now, task_id),
            ).rowcount
            conn.commit()

            if affected == 0:
                return None

            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            record = _row_to_record(row)
            log.info("Task %s retried (attempt %d)", task_id, record.attempt_count)
            return record
        finally:
            conn.close()

    # ── Queries ──────────────────────────────────────────────

    def get_task(self, task_id: str) -> TaskRecord | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
            return _row_to_record(row) if row else None
        finally:
            conn.close()

    def get_queue(self) -> list[TaskRecord]:
        """Return all queued and processing tasks, ordered by creation time."""
        conn = self._connect()
        try:
            rows = conn.execute(
                """SELECT * FROM tasks
                   WHERE status IN ('queued', 'processing')
                   ORDER BY created_at""",
            ).fetchall()
            return [_row_to_record(r) for r in rows]
        finally:
            conn.close()

    def get_active_task(self) -> TaskRecord | None:
        """Return the currently processing task, if any."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM tasks WHERE status = 'processing' LIMIT 1",
            ).fetchone()
            return _row_to_record(row) if row else None
        finally:
            conn.close()

    def get_history(
        self,
        limit: int = 20,
        offset: int = 0,
        status: str | None = None,
    ) -> list[TaskRecord]:
        """Return tasks, newest first. Optionally filter by status."""
        conn = self._connect()
        try:
            if status:
                rows = conn.execute(
                    """SELECT * FROM tasks
                       WHERE status = ?
                       ORDER BY COALESCE(finished_at, created_at) DESC
                       LIMIT ? OFFSET ?""",
                    (status, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    """SELECT * FROM tasks
                       ORDER BY COALESCE(finished_at, created_at) DESC
                       LIMIT ? OFFSET ?""",
                    (limit, offset),
                ).fetchall()
            return [_row_to_record(r) for r in rows]
        finally:
            conn.close()

    def get_pending_count(self) -> int:
        """Count of queued tasks (not including processing)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS cnt FROM tasks WHERE status = 'queued'",
            ).fetchone()
            return row["cnt"]
        finally:
            conn.close()

    def get_all_tasks(self) -> dict[str, Any]:
        """Full queue state for API: active, queued, and recent history."""
        conn = self._connect()
        try:
            active_row = conn.execute(
                "SELECT * FROM tasks WHERE status = 'processing' LIMIT 1",
            ).fetchone()
            queued_rows = conn.execute(
                "SELECT * FROM tasks WHERE status = 'queued' ORDER BY created_at",
            ).fetchall()
            history_rows = conn.execute(
                """SELECT * FROM tasks
                   WHERE status IN ('complete', 'failed', 'cancelled', 'interrupted')
                   ORDER BY finished_at DESC LIMIT 20""",
            ).fetchall()
            return {
                "active": _row_to_record(active_row).to_dict() if active_row else None,
                "queued": [_row_to_record(r).to_dict() for r in queued_rows],
                "history": [_row_to_record(r).to_dict() for r in history_rows],
            }
        finally:
            conn.close()

    # ── Startup recovery ─────────────────────────────────────

    def mark_interrupted(self) -> int:
        """Mark any 'processing' tasks as 'interrupted' (crash recovery).

        Returns the number of tasks marked.
        """
        now = time.time()
        conn = self._connect()
        try:
            affected = conn.execute(
                """UPDATE tasks
                   SET status = 'interrupted',
                       error = 'Interrupted by service restart',
                       finished_at = ?,
                       updated_at = ?
                   WHERE status = 'processing'""",
                (now, now),
            ).rowcount
            conn.commit()
            if affected:
                log.warning("Marked %d interrupted task(s) from previous run", affected)
            return affected
        finally:
            conn.close()
