"""Writable preparation of custody on the original acquisition journal."""

from __future__ import annotations

import sqlite3

from .runtime_custody import RuntimeCustodyError, _normalized_schema, operation_triggers


ATTEMPTS = """
CREATE TABLE IF NOT EXISTS acquisition_attempts (
    delivery_id TEXT PRIMARY KEY,
    target TEXT NOT NULL,
    worktree_id TEXT NOT NULL,
    request_digest TEXT NOT NULL,
    token TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'completed', 'refused')),
    result_json TEXT,
    refusal_code TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
)
"""
EFFECTS = (
    "CREATE TABLE IF NOT EXISTS acquisition_effects ("
    "delivery_id TEXT PRIMARY KEY, stage TEXT NOT NULL, data_json TEXT NOT NULL)"
)
COLUMNS = {
    "runtime_custody_json": "TEXT",
    "launch_handoff_json": "TEXT",
    "launch_started_at": "REAL",
    "launch_result_json": "TEXT",
}
INDEXES = {
    "idx_acquisition_pending":
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_acquisition_pending "
        "ON acquisition_attempts(target, worktree_id) WHERE state='pending'",
    "idx_acquisition_history":
        "CREATE INDEX IF NOT EXISTS idx_acquisition_history "
        "ON acquisition_attempts(worktree_id, state)",
}


def schema_triggers() -> dict[str, tuple[str, str]]:
    triggers = operation_triggers(("acquisition_attempts",))
    for column in ("launch_handoff_json", "launch_started_at", "launch_result_json"):
        name = f"acquisition_{column}_retained"
        triggers[name] = ("acquisition_attempts",
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE UPDATE OF {column} "
            f"ON acquisition_attempts WHEN OLD.{column} IS NOT NULL "
            f"AND NEW.{column} IS NOT OLD.{column} "
            "BEGIN SELECT RAISE(ABORT,'bridge_acquisition_launch_retained'); END")
    triggers["acquisition_original_input_retained"] = ("acquisition_attempts",
        "CREATE TRIGGER IF NOT EXISTS acquisition_original_input_retained "
        "BEFORE UPDATE OF delivery_id,target,worktree_id,request_digest,token "
        "ON acquisition_attempts WHEN OLD.runtime_custody_json IS NOT NULL AND ("
        "NEW.delivery_id IS NOT OLD.delivery_id OR NEW.target IS NOT OLD.target OR "
        "NEW.worktree_id IS NOT OLD.worktree_id OR NEW.request_digest IS NOT OLD.request_digest OR "
        "NEW.token IS NOT OLD.token) "
        "BEGIN SELECT RAISE(ABORT,'bridge_acquisition_input_retained'); END")
    for operation in ("INSERT", "UPDATE"):
        name = f"acquisition_launch_shape_{operation.lower()}"
        triggers[name] = ("acquisition_attempts",
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} "
            "ON acquisition_attempts BEGIN "
            "SELECT CASE WHEN NEW.launch_handoff_json IS NOT NULL AND ("
            "NEW.runtime_custody_json IS NULL OR NOT json_valid(NEW.launch_handoff_json)) "
            "THEN RAISE(ABORT,'bridge_acquisition_launch_invalid') END;"
            "SELECT CASE WHEN NEW.launch_handoff_json IS NOT NULL AND ("
            "json_extract(NEW.launch_handoff_json,'$.delivery_id') IS NOT NEW.delivery_id OR "
            "json_extract(NEW.launch_handoff_json,'$.request_digest') IS NOT NEW.request_digest OR "
            "json_extract(NEW.launch_handoff_json,'$.admission') IS NOT NEW.runtime_custody_json) "
            "THEN RAISE(ABORT,'bridge_acquisition_launch_invalid') END;"
            "SELECT CASE WHEN (NEW.launch_started_at IS NOT NULL AND ("
            "NEW.launch_handoff_json IS NULL OR typeof(NEW.launch_started_at) "
            "NOT IN ('integer','real') OR NOT (NEW.launch_started_at>0 "
            "AND NEW.launch_started_at<=1.7976931348623157e308))) "
            "OR (NEW.launch_result_json IS NOT NULL AND (NEW.launch_started_at IS NULL "
            "OR NOT json_valid(NEW.launch_result_json))) "
            "THEN RAISE(ABORT,'bridge_acquisition_launch_outcome_invalid') END; END")
    triggers["acquisition_effect_history_retained"] = ("acquisition_effects",
        "CREATE TRIGGER IF NOT EXISTS acquisition_effect_history_retained "
        "BEFORE DELETE ON acquisition_effects WHEN EXISTS ("
        "SELECT 1 FROM acquisition_attempts a WHERE a.delivery_id=OLD.delivery_id "
        "AND a.runtime_custody_json IS NOT NULL) "
        "BEGIN SELECT RAISE(ABORT,'bridge_acquisition_history_retained'); END")
    triggers["acquisition_effect_request_retained"] = ("acquisition_effects",
        "CREATE TRIGGER IF NOT EXISTS acquisition_effect_request_retained "
        "BEFORE UPDATE ON acquisition_effects WHEN EXISTS ("
        "SELECT 1 FROM acquisition_attempts a WHERE a.delivery_id=OLD.delivery_id "
        "AND a.runtime_custody_json IS NOT NULL) AND ("
        "NEW.delivery_id IS NOT OLD.delivery_id OR "
        "json_extract(NEW.data_json,'$._request') IS NOT json_extract(OLD.data_json,'$._request')) "
        "BEGIN SELECT RAISE(ABORT,'bridge_acquisition_request_retained'); END")
    for operation in ("INSERT", "UPDATE", "DELETE"):
        name = f"acquisition_effect_revision_{operation.lower()}"
        triggers[name] = ("acquisition_effects",
            f"CREATE TRIGGER IF NOT EXISTS {name} AFTER {operation} ON acquisition_effects "
            "BEGIN UPDATE bridge_runtime_epoch SET revision=revision+1 WHERE singleton=1; END")
    return triggers


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(ATTEMPTS)
    conn.execute(EFFECTS)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(acquisition_attempts)")}
    for name, kind in COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE acquisition_attempts ADD COLUMN {name} {kind}")
    for statement in INDEXES.values():
        conn.execute(statement)
    for _table, statement in schema_triggers().values():
        conn.execute(statement)


def validate_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(acquisition_attempts)")}
    for name, kind in COLUMNS.items():
        row = columns.get(name)
        if row is None or row[2].upper() != kind or row[3] != 0 or row[5] != 0:
            raise RuntimeCustodyError(f"Acquisition custody requires writable preparation: {name}")
    for name, statement in INDEXES.items():
        row = conn.execute(
            "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,),
        ).fetchone()
        if (
            row is None or row[0] != "index" or row[1] != "acquisition_attempts"
            or type(row[2]) is not str
            or _normalized_schema(row[2]) != _normalized_schema(statement)
        ):
            raise RuntimeCustodyError(f"Acquisition custody requires writable preparation: {name}")
    for name, (table, statement) in schema_triggers().items():
        row = conn.execute(
            "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,),
        ).fetchone()
        if (
            row is None or row[0] != "trigger" or row[1] != table or type(row[2]) is not str
            or _normalized_schema(row[2]) != _normalized_schema(statement)
        ):
            raise RuntimeCustodyError(f"Acquisition custody requires writable preparation: {name}")
