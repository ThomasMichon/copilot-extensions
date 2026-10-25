"""Writable preparation of original custody on the existing creation rows."""

from __future__ import annotations

import sqlite3

from .creation_custody import TABLES
from .runtime_custody import RuntimeCustodyError, _normalized_schema, operation_triggers

COLUMNS = {"runtime_custody_json": "TEXT", "creation_facts_json": "TEXT"}


def schema_triggers() -> dict[str, tuple[str, str]]:
    triggers = operation_triggers(tuple(TABLES.values()))
    for table in TABLES.values():
        triggers[f"{table}_original_row_retained"] = (table,
            f"CREATE TRIGGER IF NOT EXISTS {table}_original_row_retained "
            f"BEFORE INSERT ON {table} WHEN EXISTS (SELECT 1 FROM {table} "
            "WHERE key_digest=NEW.key_digest AND runtime_custody_json IS NOT NULL) "
            "BEGIN SELECT RAISE(ABORT,'bridge_creation_row_retained'); END")
        triggers[f"{table}_original_destination_retained"] = (table,
            f"CREATE TRIGGER IF NOT EXISTS {table}_original_destination_retained "
            f"BEFORE UPDATE OF key_digest ON {table} "
            "WHEN NEW.key_digest IS NOT OLD.key_digest AND EXISTS ("
            f"SELECT 1 FROM {table} WHERE key_digest=NEW.key_digest "
            "AND runtime_custody_json IS NOT NULL) "
            "BEGIN SELECT RAISE(ABORT,'bridge_creation_row_retained'); END")
        triggers[f"{table}_original_input_retained"] = (table,
            f"CREATE TRIGGER IF NOT EXISTS {table}_original_input_retained "
            f"BEFORE UPDATE OF key_digest,facts_digest,session_id,creation_facts_json ON {table} "
            "WHEN (OLD.runtime_custody_json IS NOT NULL OR OLD.creation_facts_json IS NOT NULL) AND ("
            "NEW.key_digest IS NOT OLD.key_digest OR NEW.facts_digest IS NOT OLD.facts_digest OR "
            "NEW.session_id IS NOT OLD.session_id OR NEW.creation_facts_json IS NOT OLD.creation_facts_json) "
            "BEGIN SELECT RAISE(ABORT,'bridge_creation_input_retained'); END")
        for operation in ("INSERT", "UPDATE"):
            name = f"{table}_original_input_shape_{operation.lower()}"
            triggers[name] = (table,
                f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} ON {table} BEGIN "
                "SELECT CASE WHEN (NEW.runtime_custody_json IS NULL) "
                "IS NOT (NEW.creation_facts_json IS NULL) "
                "THEN RAISE(ABORT,'bridge_creation_input_invalid') END;"
                "SELECT CASE WHEN NEW.creation_facts_json IS NOT NULL AND ("
                "NOT json_valid(NEW.creation_facts_json) OR "
                "json_type(NEW.creation_facts_json) IS NOT 'object') "
                "THEN RAISE(ABORT,'bridge_creation_input_invalid') END; END")
    return triggers


def ensure_schema(conn: sqlite3.Connection) -> None:
    for table in TABLES.values():
        columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in COLUMNS.items():
            if name not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    for _table, statement in schema_triggers().values():
        conn.execute(statement)


def validate_schema(conn: sqlite3.Connection) -> None:
    for table in TABLES.values():
        columns = {row[1]: row for row in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in COLUMNS.items():
            row = columns.get(name)
            if row is None or row[2].upper() != kind or row[3] != 0 or row[5] != 0:
                raise RuntimeCustodyError(f"Creation custody requires writable preparation: {table}.{name}")
    for name, (table, statement) in schema_triggers().items():
        row = conn.execute(
            "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,),
        ).fetchone()
        if (
            row is None or row[0] != "trigger" or row[1] != table or type(row[2]) is not str
            or _normalized_schema(row[2]) != _normalized_schema(statement)
        ):
            raise RuntimeCustodyError(f"Creation custody requires writable preparation: {name}")
