"""Reconstruct the pre-47 guard when a test explicitly downgrades its database."""

from __future__ import annotations

import sqlite3

from agent_bridge.inbox_custody import schema_triggers
from agent_bridge.inbox_consumer_schema import definitions


def restore_prior_inbox_guards(conn: sqlite3.Connection, *, version: int) -> None:
    guards = schema_triggers(exact_native=version >= 40, compatible_messages=False)
    for name in ("live_messages_runtime_admission", "live_messages_handoff_input_immutable"):
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.execute(guards[name][1])
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bridge_inbox_consumers'",
    ).fetchone() is not None:
        name = "live_sessions_retire_inbox_consumer_update"
        conn.execute(f"DROP TRIGGER IF EXISTS {name}")
        conn.execute(definitions(compatible_messages=False)[name][2])


def restore_v49_inbox(conn: sqlite3.Connection) -> None:
    """Reconstruct the last schema without durable producer metadata or guards."""
    for name in schema_triggers().keys() - schema_triggers(durable_messages=False).keys():
        conn.execute(f"DROP TRIGGER {name}")
    conn.execute("DROP TRIGGER live_messages_runtime_admission")
    conn.execute(schema_triggers(durable_messages=False)["live_messages_runtime_admission"][1])
    conn.execute("ALTER TABLE live_messages DROP COLUMN producer_target_json")
    conn.execute("UPDATE schema_version SET version=49")
