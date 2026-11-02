"""Prepare original native-launch observations without enrolling legacy creation."""

from __future__ import annotations

import sqlite3

from .creation_launch_custody import LAUNCH_COLUMNS
from .runtime_custody import RuntimeCustodyError, _normalized_schema

COLUMNS = {name: "TEXT" if name.endswith("_json") else "REAL" for name in LAUNCH_COLUMNS}


def schema_triggers() -> dict[str, str]:
    result = {}
    for column in (*COLUMNS, "native_session_id", "worktree_json", "turn_index"):
        name = f"workspace_creation_{column}_retained"
        result[name] = (
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE UPDATE OF {column} ON workspace_embodiments "
            f"WHEN OLD.launch_handoff_json IS NOT NULL AND OLD.{column} IS NOT NULL "
            f"AND NEW.{column} IS NOT OLD.{column} "
            "BEGIN SELECT RAISE(ABORT,'bridge_creation_launch_retained'); END"
        )
    for operation in ("INSERT", "UPDATE"):
        name = f"workspace_creation_launch_shape_{operation.lower()}"
        result[name] = (
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} ON workspace_embodiments BEGIN "
            "SELECT CASE WHEN NEW.launch_handoff_json IS NOT NULL AND ("
            "NEW.runtime_custody_json IS NULL OR NEW.worktree_json IS NULL "
            "OR NOT json_valid(NEW.launch_handoff_json)) "
            "THEN RAISE(ABORT,'bridge_creation_launch_invalid') END;"
            "SELECT CASE WHEN NEW.launch_handoff_json IS NOT NULL AND ("
            "json_extract(NEW.launch_handoff_json,'$.key_digest') IS NOT NEW.key_digest OR "
            "json_extract(NEW.launch_handoff_json,'$.facts_digest') IS NOT NEW.facts_digest OR "
            "json_extract(NEW.launch_handoff_json,'$.admission') IS NOT NEW.runtime_custody_json OR "
            "json_extract(NEW.launch_handoff_json,'$.consumer.session_id') IS NOT NEW.session_id) "
            "THEN RAISE(ABORT,'bridge_creation_launch_invalid') END;"
            "SELECT CASE WHEN NEW.launch_result_json IS NOT NULL AND ("
            "NEW.launch_started_at IS NULL OR NOT json_valid(NEW.launch_result_json)) "
            "THEN RAISE(ABORT,'bridge_creation_launch_invalid') END;"
            "SELECT CASE WHEN NEW.binding_started_at IS NOT NULL AND NEW.launch_result_json IS NULL "
            "OR NEW.binding_accepted_at IS NOT NULL AND NEW.binding_started_at IS NULL "
            "THEN RAISE(ABORT,'bridge_creation_launch_invalid') END;"
            + "".join(
                f"SELECT CASE WHEN NEW.{column} IS NOT NULL AND (NEW.launch_handoff_json IS NULL "
                f"OR typeof(NEW.{column}) NOT IN ('integer','real') "
                f"OR NOT (NEW.{column}>0 AND NEW.{column}<=1.7976931348623157e308)) "
                "THEN RAISE(ABORT,'bridge_creation_launch_invalid') END;"
                for column in COLUMNS if not column.endswith("_json")
            ) + " END"
        )
    return result


def ensure_schema(conn: sqlite3.Connection) -> None:
    _ensure_table_schema(conn, "workspace_embodiments", COLUMNS, schema_triggers())


def _ensure_table_schema(
    conn: sqlite3.Connection, table: str, declarations: dict[str, str], triggers: dict[str, str],
) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, kind in declarations.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    for statement in triggers.values():
        conn.execute(statement)


def validate_schema(conn: sqlite3.Connection) -> None:
    _validate_table_schema(conn, "workspace_embodiments", COLUMNS, schema_triggers())


def _validate_table_schema(
    conn: sqlite3.Connection, table: str, declarations: dict[str, str], triggers: dict[str, str],
) -> None:
    columns = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, kind in declarations.items():
        row = columns.get(name)
        if row is None or row[2].upper() != kind or row[3] != 0 or row[5] != 0:
            raise RuntimeCustodyError(f"Creation launch requires writable preparation: {name}")
    for name, statement in triggers.items():
        row = conn.execute("SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if (
            row is None or row[0] != "trigger" or row[1] != table
            or type(row[2]) is not str or _normalized_schema(row[2]) != _normalized_schema(statement)
        ):
            raise RuntimeCustodyError(f"Creation launch requires writable preparation: {name}")
