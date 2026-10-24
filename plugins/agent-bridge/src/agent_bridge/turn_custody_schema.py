"""Additive native-turn custody storage, prepared only by the schema owner."""

from __future__ import annotations

import sqlite3

from .runtime_custody import RuntimeCustodyError, _normalized_schema


COLUMNS = {
    "native_handoff_json": "TEXT",
    "native_handoff_started_at": "REAL",
    "native_accepted_at": "REAL",
    "native_consumed_at": "REAL",
    "native_outcome": "TEXT",
}


def schema_triggers() -> dict[str, str]:
    triggers = {
        "turns_native_no_retroactive_handoff":
            "CREATE TRIGGER IF NOT EXISTS turns_native_no_retroactive_handoff "
            "BEFORE UPDATE OF native_handoff_json ON turns "
            "WHEN OLD.native_handoff_json IS NULL AND NEW.native_handoff_json IS NOT NULL "
            "BEGIN SELECT RAISE(ABORT,'bridge_native_turn_retroactive_handoff_unsupported'); END",
        "turns_native_handoff_immutable":
            "CREATE TRIGGER IF NOT EXISTS turns_native_handoff_immutable "
            "BEFORE UPDATE OF native_handoff_json ON turns "
            "WHEN OLD.native_handoff_json IS NOT NULL "
            "AND NEW.native_handoff_json IS NOT OLD.native_handoff_json "
            "BEGIN SELECT RAISE(ABORT,'bridge_native_turn_handoff_immutable'); END",
        "turns_native_input_immutable":
            "CREATE TRIGGER IF NOT EXISTS turns_native_input_immutable "
            "BEFORE UPDATE OF session_id,turn_index,prompt,acquisition_delivery_id,"
            "native_admission_json ON turns WHEN OLD.native_handoff_json IS NOT NULL AND ("
            "NEW.session_id IS NOT OLD.session_id OR NEW.turn_index IS NOT OLD.turn_index OR "
            "NEW.prompt IS NOT OLD.prompt OR "
            "NEW.acquisition_delivery_id IS NOT OLD.acquisition_delivery_id OR "
            "NEW.native_admission_json IS NOT OLD.native_admission_json) "
            "BEGIN SELECT RAISE(ABORT,'bridge_native_turn_input_immutable'); END",
        "turns_native_history_retained":
            "CREATE TRIGGER IF NOT EXISTS turns_native_history_retained BEFORE DELETE ON turns "
            "WHEN OLD.native_handoff_json IS NOT NULL "
            "BEGIN SELECT RAISE(ABORT,'bridge_native_turn_custody_retained'); END",
    }
    for column in ("native_handoff_started_at", "native_accepted_at",
                   "native_consumed_at", "native_outcome"):
        name = f"turns_{column}_monotonic"
        triggers[name] = (
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE UPDATE OF {column} ON turns "
            f"WHEN OLD.native_handoff_json IS NOT NULL AND OLD.{column} IS NOT NULL "
            f"AND NEW.{column} IS NOT OLD.{column} "
            "BEGIN SELECT RAISE(ABORT,'bridge_native_turn_outcome_retained'); END"
        )
    for operation in ("INSERT", "UPDATE"):
        name = f"turns_native_shape_{operation.lower()}"
        invalid_times = " OR ".join(
            f"(NEW.{column} IS NOT NULL AND (typeof(NEW.{column}) NOT IN ('integer','real') "
            f"OR NOT (NEW.{column}>0 AND NEW.{column}<=1.7976931348623157e308)))"
            for column in ("native_handoff_started_at", "native_accepted_at", "native_consumed_at")
        )
        triggers[name] = (
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} ON turns BEGIN "
            "SELECT CASE WHEN NEW.native_handoff_json IS NOT NULL "
            "AND (NEW.runtime_custody_json IS NULL OR NOT json_valid(NEW.native_handoff_json)) "
            "THEN RAISE(ABORT,'bridge_native_turn_handoff_invalid') END;"
            "SELECT CASE WHEN NEW.native_handoff_json IS NOT NULL AND ("
            "json_extract(NEW.native_handoff_json,'$.session_id') IS NOT NEW.session_id OR "
            "json_extract(NEW.native_handoff_json,'$.turn_index') IS NOT NEW.turn_index OR "
            "json_extract(NEW.native_handoff_json,'$.admission') IS NOT NEW.runtime_custody_json) "
            "THEN RAISE(ABORT,'bridge_native_turn_handoff_invalid') END;"
            "SELECT CASE WHEN (NEW.native_handoff_started_at IS NOT NULL "
            "AND NEW.native_handoff_json IS NULL) OR "
            "((NEW.native_accepted_at IS NOT NULL OR NEW.native_consumed_at IS NOT NULL "
            "OR NEW.native_outcome IS NOT NULL) AND NEW.native_handoff_started_at IS NULL) "
            "OR (NEW.native_outcome IS NOT NULL AND NEW.native_outcome IS NOT 'unknown') "
            "THEN RAISE(ABORT,'bridge_native_turn_outcome_invalid') END;"
            f"SELECT CASE WHEN {invalid_times} "
            "THEN RAISE(ABORT,'bridge_native_turn_time_invalid') END; END"
        )
    return triggers


def ensure_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(turns)")}
    for name, kind in COLUMNS.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE turns ADD COLUMN {name} {kind}")
    for statement in schema_triggers().values():
        conn.execute(statement)


def validate_schema(conn: sqlite3.Connection) -> None:
    columns = {row[1]: row for row in conn.execute("PRAGMA table_info(turns)")}
    for name, kind in COLUMNS.items():
        row = columns.get(name)
        if row is None or row[2].upper() != kind or row[3] != 0 or row[5] != 0:
            raise RuntimeCustodyError(f"Native turn custody requires writable preparation: {name}")
    for name, statement in schema_triggers().items():
        row = conn.execute(
            "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,),
        ).fetchone()
        if (
            row is None or row[0] != "trigger" or row[1] != "turns" or type(row[2]) is not str
            or _normalized_schema(row[2]) != _normalized_schema(statement)
        ):
            raise RuntimeCustodyError(f"Native turn custody requires writable preparation: {name}")
