"""Exact live-message provenance and additive unresolved-original page indexing."""

from __future__ import annotations

import sqlite3

from .runtime_custody import RuntimeCustodyError, _normalized_schema

INDEX = "live_messages_unresolved_original"
PREDICATE = "consumed_at IS NULL AND disposition_json IS NULL AND kind NOT LIKE 'control:%'"
_SQL = f"CREATE INDEX IF NOT EXISTS {INDEX} ON live_messages(session_id,id) WHERE {PREDICATE}"


def validate_exact_live_schema(conn: sqlite3.Connection) -> None:
    """Require durable exact-native provenance before exposing its contract."""
    column = next(
        (
            row
            for row in conn.execute("PRAGMA table_info(live_messages)")
            if row[1] == "exact_session"
        ),
        None,
    )
    if column is None or column[2].upper() != "INTEGER" or column[3] != 1 or column[4] != "0":
        raise ValueError("Exact live targeting requires supported writable preparation")


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Prepare a derived index without changing durable receipts or schema version."""
    conn.execute(_SQL)


def require_schema(conn: sqlite3.Connection) -> None:
    """Refuse an unprepared optional page rather than scan settled history."""
    row = conn.execute(
        "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?",
        (INDEX,),
    ).fetchone()
    if (
        row is None
        or row[0] != "index"
        or row[1] != "live_messages"
        or not isinstance(row[2], str)
        or _normalized_schema(row[2]) != _normalized_schema(_SQL)
    ):
        raise RuntimeCustodyError(
            "Pending queue index is unavailable; writable preparation is required"
        )
