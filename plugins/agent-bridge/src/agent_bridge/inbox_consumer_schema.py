"""Durable current-consumer observations, prepared by the existing DB owner."""

from __future__ import annotations

import sqlite3

from .db_live_session_aliases import PROCESS_START_TOLERANCE_SECONDS
from .runtime_custody import RuntimeCustodyError, _normalized_schema


def definitions() -> dict[str, tuple[str, str, str]]:
    table = "bridge_inbox_consumers"
    result = {
        table: ("table", table,
            f"CREATE TABLE IF NOT EXISTS {table} ("
            "incarnation TEXT PRIMARY KEY, installation TEXT NOT NULL, epoch INTEGER NOT NULL,"
            "session_id TEXT NOT NULL, consumer_json TEXT NOT NULL, predecessor TEXT,"
            "current INTEGER NOT NULL CHECK(current IN (0,1)), enrolled_at REAL NOT NULL)"),
        "bridge_inbox_consumers_current": ("index", table,
            f"CREATE UNIQUE INDEX IF NOT EXISTS bridge_inbox_consumers_current ON {table}"
            "(session_id) WHERE current=1"),
        "bridge_inbox_consumers_immutable": ("trigger", table,
            "CREATE TRIGGER IF NOT EXISTS bridge_inbox_consumers_immutable "
            f"BEFORE UPDATE ON {table} WHEN "
            "NEW.incarnation IS NOT OLD.incarnation OR NEW.installation IS NOT OLD.installation "
            "OR NEW.epoch IS NOT OLD.epoch OR NEW.session_id IS NOT OLD.session_id "
            "OR NEW.consumer_json IS NOT OLD.consumer_json OR NEW.predecessor IS NOT OLD.predecessor "
            "OR NEW.enrolled_at IS NOT OLD.enrolled_at OR NEW.current > OLD.current "
            "BEGIN SELECT RAISE(ABORT,'bridge_inbox_consumer_immutable'); END"),
        "bridge_inbox_consumers_retained": ("trigger", table,
            "CREATE TRIGGER IF NOT EXISTS bridge_inbox_consumers_retained "
            f"BEFORE DELETE ON {table} "
            "BEGIN SELECT RAISE(ABORT,'bridge_inbox_consumer_retained'); END"),
        "live_sessions_retire_inbox_consumer_delete": ("trigger", "live_sessions",
            "CREATE TRIGGER IF NOT EXISTS live_sessions_retire_inbox_consumer_delete "
            "AFTER DELETE ON live_sessions BEGIN "
            f"UPDATE {table} SET current=0 WHERE session_id=OLD.session_id AND current=1; END"),
        "live_sessions_retire_inbox_consumer_update": ("trigger", "live_sessions",
            "CREATE TRIGGER IF NOT EXISTS live_sessions_retire_inbox_consumer_update "
            "AFTER UPDATE ON live_sessions WHEN "
            "NEW.session_id IS NOT OLD.session_id OR NEW.pid IS NOT OLD.pid "
            "OR (NEW.process_started_at IS NOT OLD.process_started_at AND ("
            "NEW.process_started_at IS NULL OR OLD.process_started_at IS NULL OR "
            "ABS(NEW.process_started_at - OLD.process_started_at) "
            f">= {PROCESS_START_TOLERANCE_SECONDS})) "
            "OR NEW.machine IS NOT OLD.machine OR NEW.venue IS NOT OLD.venue "
            "OR NEW.status != 'live' BEGIN "
            f"UPDATE {table} SET current=0 WHERE session_id=OLD.session_id AND current=1; END"),
    }
    for operation in ("INSERT", "UPDATE", "DELETE"):
        name = f"bridge_inbox_consumers_revision_{operation.lower()}"
        result[name] = ("trigger", table,
            f"CREATE TRIGGER IF NOT EXISTS {name} AFTER {operation} ON {table} BEGIN "
            "UPDATE bridge_runtime_epoch SET revision=revision+1 WHERE singleton=1; END")
    return result


def ensure_schema(conn: sqlite3.Connection) -> None:
    for _kind, _table, statement in definitions().values():
        conn.execute(statement)


def validate_schema(conn: sqlite3.Connection) -> None:
    """Never repair missing enrollment history on a prepared database."""
    for name, (kind, table, statement) in definitions().items():
        row = conn.execute(
            "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,),
        ).fetchone()
        if (
            row is None or row[0] != kind or row[1] != table or not isinstance(row[2], str)
            or _normalized_schema(row[2]) != _normalized_schema(statement)
        ):
            raise RuntimeCustodyError(f"Inbox consumer requires writable preparation: {name}")
