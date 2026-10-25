"""Prepare provider native handoffs without adopting legacy provider effects."""

from __future__ import annotations

import sqlite3

from .creation_launch_custody import PROVIDER_LAUNCH_COLUMNS
from .creation_launch_schema import _ensure_table_schema, _validate_table_schema

COLUMNS = {name: "TEXT" if name.endswith("_json") else "REAL" for name in PROVIDER_LAUNCH_COLUMNS}
TABLE = "provider_embodiments"


def schema_triggers() -> dict[str, str]:
    result = {}
    for column in (*COLUMNS, "native_session_id"):
        name = f"provider_creation_{column}_retained"
        result[name] = (
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE UPDATE OF {column} ON {TABLE} "
            f"WHEN OLD.launch_handoff_json IS NOT NULL AND OLD.{column} IS NOT NULL "
            f"AND NEW.{column} IS NOT OLD.{column} "
            "BEGIN SELECT RAISE(ABORT,'bridge_creation_launch_retained'); END"
        )
    for operation in ("INSERT", "UPDATE"):
        name = f"provider_creation_launch_shape_{operation.lower()}"
        result[name] = (
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} ON {TABLE} BEGIN "
            "SELECT CASE WHEN NEW.launch_handoff_json IS NOT NULL AND ("
            "NEW.runtime_custody_json IS NULL OR NEW.creation_facts_json IS NULL "
            "OR NOT json_valid(NEW.launch_handoff_json)) "
            "THEN RAISE(ABORT,'bridge_creation_launch_invalid') END;"
            "SELECT CASE WHEN NEW.launch_handoff_json IS NOT NULL AND ("
            "json_extract(NEW.launch_handoff_json,'$.key_digest') IS NOT NEW.key_digest OR "
            "json_extract(NEW.launch_handoff_json,'$.facts_digest') IS NOT NEW.facts_digest OR "
            "json_extract(NEW.launch_handoff_json,'$.admission') IS NOT NEW.runtime_custody_json OR "
            "json_extract(NEW.launch_handoff_json,'$.consumer.session_id') IS NOT NEW.session_id) "
            "THEN RAISE(ABORT,'bridge_creation_launch_invalid') END;"
            "SELECT CASE WHEN NEW.launch_started_at IS NOT NULL AND ("
            "NEW.launch_handoff_json IS NULL OR "
            "typeof(NEW.launch_started_at) NOT IN ('integer','real') OR "
            "NOT (NEW.launch_started_at>0 AND NEW.launch_started_at<=1.7976931348623157e308)) "
            "THEN RAISE(ABORT,'bridge_creation_launch_invalid') END;"
            "SELECT CASE WHEN NEW.launch_result_json IS NOT NULL AND ("
            "NEW.launch_started_at IS NULL OR NOT json_valid(NEW.launch_result_json)) "
            "THEN RAISE(ABORT,'bridge_creation_launch_invalid') END; END"
        )
    return result


def ensure_schema(conn: sqlite3.Connection) -> None:
    _ensure_table_schema(conn, TABLE, COLUMNS, schema_triggers())


def validate_schema(conn: sqlite3.Connection) -> None:
    _validate_table_schema(conn, TABLE, COLUMNS, schema_triggers())
