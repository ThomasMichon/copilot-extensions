"""Writable preparation and validation of ordinary creation's writer custody."""

from __future__ import annotations

import sqlite3

from .runtime_custody import RuntimeCustodyError, _normalized_schema

COLUMNS = ("creation_writer_json", "creation_handoffs_json")
TRIGGERS = {
    "workspace_writer_retained": (
        "BEFORE UPDATE OF key_digest,facts_digest,session_id,worktree_json,creation_writer_json "
        "ON workspace_embodiments WHEN OLD.creation_writer_json IS NOT NULL AND ("
        "NEW.key_digest IS NOT OLD.key_digest OR NEW.facts_digest IS NOT OLD.facts_digest OR "
        "NEW.session_id IS NOT OLD.session_id OR NEW.worktree_json IS NOT OLD.worktree_json OR "
        "NEW.creation_writer_json IS NOT OLD.creation_writer_json)"
    ),
    "workspace_writer_no_retrofit": (
        "BEFORE UPDATE OF creation_writer_json ON workspace_embodiments "
        "WHEN OLD.creation_writer_json IS NULL AND NEW.creation_writer_json IS NOT NULL"
    ),
    "workspace_writer_native_retained": (
        "BEFORE UPDATE OF native_session_id ON workspace_embodiments "
        "WHEN OLD.creation_writer_json IS NOT NULL AND OLD.native_session_id IS NOT NULL "
        "AND NEW.native_session_id IS NOT OLD.native_session_id"
    ),
    "workspace_writer_row_retained": (
        "BEFORE DELETE ON workspace_embodiments WHEN OLD.creation_writer_json IS NOT NULL"
    ),
    "workspace_writer_no_replace": (
        "BEFORE INSERT ON workspace_embodiments WHEN EXISTS ("
        "SELECT 1 FROM workspace_embodiments WHERE key_digest=NEW.key_digest "
        "AND creation_writer_json IS NOT NULL)"
    ),
}


def statements() -> dict[str, str]:
    return {
        name: f"CREATE TRIGGER IF NOT EXISTS {name} {body} "
        "BEGIN SELECT RAISE(ABORT,'bridge_creation_writer_retained'); END"
        for name, body in TRIGGERS.items()
    }


def ensure_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(workspace_embodiments)")}
    for column in COLUMNS:
        if column not in columns:
            conn.execute(f"ALTER TABLE workspace_embodiments ADD COLUMN {column} TEXT")
    for statement in statements().values():
        conn.execute(statement)


def validate_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(workspace_embodiments)")}
    for column in COLUMNS:
        row = columns.get(column)
        if row is None or row[2].upper() != "TEXT" or row[3] != 0 or row[5] != 0:
            raise RuntimeCustodyError(f"Creation writer requires writable preparation: {column}")
    for name, statement in statements().items():
        row = conn.execute(
            "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,),
        ).fetchone()
        if (
            row is None or row[0] != "trigger" or row[1] != "workspace_embodiments"
            or type(row[2]) is not str or _normalized_schema(row[2]) != _normalized_schema(statement)
        ):
            raise RuntimeCustodyError(f"Creation writer requires writable preparation: {name}")
