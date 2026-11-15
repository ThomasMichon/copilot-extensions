"""Reconstruct the pre-47 guard when a test explicitly downgrades its database."""

from agent_bridge.inbox_custody import schema_triggers
from agent_bridge.inbox_consumer_schema import definitions


def restore_prior_inbox_guards(conn, *, version: int) -> None:
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
