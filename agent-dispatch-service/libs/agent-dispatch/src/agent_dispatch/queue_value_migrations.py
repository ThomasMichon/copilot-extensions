"""One-time status-value migrations for the ``tasks``/``task_events`` tables.

Extracted from ``queue.py`` (tools/check-module-size.py's module-size cap)
as each new status-rename/retirement migration would otherwise keep growing
that module indefinitely. Each migration here is idempotent and guarded by
the ``queue_migrations`` table exactly as it was when inlined in
``TaskQueue._migrate``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable


def apply_status_value_migrations(
    conn: sqlite3.Connection, now_fn: Callable[[float | None], float]
) -> None:
    """Apply every queued one-time status-value migration, in order."""
    status_rename = "2026-09-29-status-rename-submitted-completed"
    conn.execute("BEGIN IMMEDIATE")
    try:
        renamed = conn.execute(
            "SELECT 1 FROM queue_migrations WHERE name = ?",
            (status_rename,),
        ).fetchone()
        if renamed is None:
            conn.execute(
                "UPDATE tasks SET status = CASE"
                " WHEN status = 'completed' THEN 'submitted'"
                " WHEN status = 'confirmed' THEN 'completed'"
                " ELSE status END"
                " WHERE status IN ('completed', 'confirmed')"
            )
            conn.execute(
                "UPDATE task_events SET"
                " from_status = CASE"
                "   WHEN from_status = 'completed' THEN 'submitted'"
                "   WHEN from_status = 'confirmed' THEN 'completed'"
                "   ELSE from_status END,"
                " to_status = CASE"
                "   WHEN to_status = 'completed' THEN 'submitted'"
                "   WHEN to_status = 'confirmed' THEN 'completed'"
                "   ELSE to_status END"
                " WHERE from_status IN ('completed', 'confirmed')"
                "    OR to_status IN ('completed', 'confirmed')"
            )
            conn.execute(
                "INSERT INTO queue_migrations(name, applied_at) VALUES (?, ?)",
                (status_rename, now_fn(None)),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    dead_letter_retirement = "2026-09-30-retire-dead-letter-status"
    conn.execute("BEGIN IMMEDIATE")
    try:
        migrated = conn.execute(
            "SELECT 1 FROM queue_migrations WHERE name = ?",
            (dead_letter_retirement,),
        ).fetchone()
        if migrated is None:
            conn.execute(
                "UPDATE tasks SET status = CASE"
                " WHEN status = 'dead_letter' THEN 'abandoned'"
                " ELSE status END,"
                " owner = CASE WHEN status = 'dead_letter' THEN NULL"
                " ELSE owner END,"
                " owner_session_id = CASE WHEN status = 'dead_letter' THEN NULL"
                " ELSE owner_session_id END,"
                " lease_expires_at = CASE WHEN status = 'dead_letter' THEN NULL"
                " ELSE lease_expires_at END,"
                " completed_at = CASE WHEN status = 'dead_letter'"
                " THEN COALESCE(completed_at, updated_at) ELSE completed_at END"
                " WHERE status = 'dead_letter'"
            )
            conn.execute(
                "UPDATE task_events SET"
                " from_status = CASE WHEN from_status = 'dead_letter'"
                " THEN 'abandoned' ELSE from_status END,"
                " to_status = CASE WHEN to_status = 'dead_letter'"
                " THEN 'abandoned' ELSE to_status END"
                " WHERE from_status = 'dead_letter' OR to_status = 'dead_letter'"
            )
            conn.execute(
                "INSERT INTO queue_migrations(name, applied_at) VALUES (?, ?)",
                (dead_letter_retirement, now_fn(None)),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise

    verification_flag = "2026-09-29-require-verification-flag"
    conn.execute("BEGIN IMMEDIATE")
    try:
        migrated = conn.execute(
            "SELECT 1 FROM queue_migrations WHERE name = ?",
            (verification_flag,),
        ).fetchone()
        if migrated is None:
            try:
                conn.execute(
                    "ALTER TABLE tasks ADD COLUMN require_verification "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            except sqlite3.OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
            conn.execute(
                "INSERT INTO queue_migrations(name, applied_at) VALUES (?, ?)",
                (verification_flag, now_fn(None)),
            )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
