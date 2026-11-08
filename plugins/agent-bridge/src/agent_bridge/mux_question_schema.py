"""Writable preparation and migration-free admission of mux answer storage."""

from __future__ import annotations

import sqlite3

from .runtime_custody import _normalized_schema

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS mux_question_answers (
    inspection_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    sender TEXT NOT NULL,
    answer TEXT NOT NULL,
    state TEXT NOT NULL,
    detail TEXT,
    created_at REAL NOT NULL,
    UNIQUE(session_id, fingerprint)
);
"""


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(SCHEMA_SQL)


def validate_schema(conn: sqlite3.Connection) -> None:
    """Require the full audit/admission table without provisioning it."""
    row = conn.execute(
        "SELECT type,tbl_name,sql FROM sqlite_master WHERE name='mux_question_answers'",
    ).fetchone()
    if (
        row is None or row[0] != "table" or row[1] != "mux_question_answers"
        or not isinstance(row[2], str)
        or _normalized_schema(row[2]) != _normalized_schema(SCHEMA_SQL.strip().rstrip(";"))
    ):
        raise ValueError("Mux question answers require supported writable schema preparation")
