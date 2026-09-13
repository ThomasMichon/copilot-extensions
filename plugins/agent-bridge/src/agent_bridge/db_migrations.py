"""Additive session-column migration primitives."""

from __future__ import annotations

import logging
import sqlite3
from collections.abc import Sequence

log = logging.getLogger("agent-bridge")


def ensure_session_columns(
    conn: sqlite3.Connection, columns: Sequence[tuple[str, str]],
) -> None:
    """Ensure post-base columns even when a database was stamped past their gate."""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    added = []
    for column, column_type in columns:
        if column not in existing:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {column} {column_type}")
            added.append(column)
    if added:
        conn.commit()
        log.warning(
            "Ensured missing sessions column(s) via safety net: %s "
            "(schema was stamped past their migration gate; see #815)",
            ", ".join(added),
        )


def migrate_restart_status(conn: sqlite3.Connection, from_version: int) -> None:
    """Add nullable restart provenance without inferring intent for legacy stops."""
    if from_version < 18:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
        if "restart_status" not in columns:
            conn.execute("ALTER TABLE sessions ADD COLUMN restart_status TEXT")
        conn.execute("UPDATE schema_version SET version=?", (18,))
        conn.commit()
        log.info("Schema migrated to version 18: restart recovery provenance")
