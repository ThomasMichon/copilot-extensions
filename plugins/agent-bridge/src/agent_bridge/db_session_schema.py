"""Idempotent session-column preparation and durable stop-gate validation."""

from __future__ import annotations

import sqlite3

from .db_core import _SESSIONS_ENSURE_COLUMNS, log


def ensure_columns(conn: sqlite3.Connection) -> None:
    """Idempotently add any missing post-base ``sessions`` columns.

    Independent of ``schema_version`` (that is the whole point): the
    version-gated migrations only run when the DB is *below* their target
    version, so a table already stamped at/after that version but missing
    the column would never receive it. This ensures the canonical nullable
    column set on every init.
    """
    existing = {r[1] for r in conn.execute("PRAGMA table_info(sessions)")}
    added: list[str] = []
    for col, col_type in _SESSIONS_ENSURE_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE sessions ADD COLUMN {col} {col_type}")
            added.append(col)
    if "background_recovery_enabled" in added:
        # A stamped v20 database can still reach this missing-column repair.
        conn.execute(
            "UPDATE sessions SET background_recovery_enabled = "
            "CASE WHEN status = 'stopped' THEN 0 ELSE 1 END"
        )
    if "cold_recovery_stop_token" in added:
        # Preserve ambiguous legacy dormancy rather than enabling recovery.
        conn.execute(
            "UPDATE sessions SET cold_recovery_stop_token=lower(hex(randomblob(16))) "
            "WHERE status='stopped' AND background_recovery_enabled=0"
        )
    if added:
        conn.commit()
        log.warning(
            "Ensured missing sessions column(s) via safety net: %s "
            "(schema was stamped past their migration gate; see #815)",
            ", ".join(added),
        )


def validate_cold_recovery_schema(conn: sqlite3.Connection) -> None:
    """Require writable preparation of the durable cold-recovery stop gate."""
    column = next(
        (row for row in conn.execute("PRAGMA table_info(sessions)")
         if row[1] == "cold_recovery_stop_token"),
        None,
    )
    if column is None or column[2].upper() != "TEXT" or column[3] != 0:
        raise ValueError("Cold recovery stop gate requires supported writable preparation")
