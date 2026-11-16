"""Persistent exclusion of settled native-send keys, including older writers."""

from __future__ import annotations

import sqlite3

from .runtime_custody import RuntimeCustodyError, _normalized_schema

TABLE = (
    "CREATE TABLE IF NOT EXISTS native_send_settlements ("
    "delivery_id TEXT PRIMARY KEY NOT NULL, session_id TEXT NOT NULL,"
    "native_session_id TEXT NOT NULL, request_digest TEXT NOT NULL,"
    "receipt_json TEXT NOT NULL)"
)


def schema_triggers() -> dict[str, tuple[str, str]]:
    """Keep the original exclusion effective at the actual turn-write boundary."""
    triggers = {}
    for operation in ("INSERT", "UPDATE"):
        name = f"turns_settled_native_send_{operation.lower()}"
        triggers[name] = ("turns",
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} ON turns "
            "WHEN NEW.acquisition_delivery_id IS NOT NULL AND EXISTS ("
            "SELECT 1 FROM native_send_settlements "
            "WHERE delivery_id=NEW.acquisition_delivery_id) "
            "BEGIN SELECT RAISE(ABORT,'native_send_settled'); END")
    for operation in ("INSERT", "UPDATE", "DELETE"):
        name = f"native_send_settlement_retained_{operation.lower()}"
        condition = (
            "WHEN EXISTS (SELECT 1 FROM native_send_settlements "
            "WHERE delivery_id=NEW.delivery_id) " if operation == "INSERT" else ""
        )
        triggers[name] = ("native_send_settlements",
            f"CREATE TRIGGER IF NOT EXISTS {name} BEFORE {operation} "
            f"ON native_send_settlements {condition}"
            "BEGIN SELECT RAISE(ABORT,'native_send_settlement_retained'); END")
    return triggers


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Prepare additive owner storage; never replace an existing exclusion."""
    conn.execute(TABLE)
    for _, statement in schema_triggers().values():
        conn.execute(statement)


def validate_schema(conn: sqlite3.Connection) -> None:
    """Refuse a damaged exclusion instead of silently recreating its protection."""
    declarations = {"native_send_settlements": ("table", "native_send_settlements", TABLE)}
    declarations.update({
        name: ("trigger", table, statement)
        for name, (table, statement) in schema_triggers().items()
    })
    for name, (kind, table, statement) in declarations.items():
        row = conn.execute(
            "SELECT type,tbl_name,sql FROM sqlite_master WHERE name=?", (name,),
        ).fetchone()
        if (
            row is None or row[0] != kind or row[1] != table or not isinstance(row[2], str)
            or _normalized_schema(row[2]) != _normalized_schema(statement)
        ):
            raise RuntimeCustodyError(
                f"Native send settlement requires writable preparation: {name}",
            )
