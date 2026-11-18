"""Root-turn evidence used when both local Host processes have exited."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING

from .events import is_nested_event_data

if TYPE_CHECKING:
    from .session_host.host_index import HostRecord


def stopped_event(record: HostRecord, native: str, created: float) -> str:
    # Bind the retry to this session incarnation and the exact locked record.
    # Do not put its endpoint or attachment credentials in observable history.
    identity = json.dumps([native, created, asdict(record)], sort_keys=True, separators=(",", ":"))
    return json.dumps({"status": "stopped", "trigger": "host_exit",
                       "settlement": hashlib.sha256(identity.encode()).hexdigest()}, sort_keys=True)


def terminal_outcome(
    conn: sqlite3.Connection, sid: str, turn: sqlite3.Row,
) -> tuple[str, float] | None:
    """Return the root outcome, None if unfinished, or refuse ambiguous evidence."""
    outcome: tuple[str, float] | None = None
    for row in conn.execute(
        "SELECT event_type,data_json,timestamp FROM events WHERE session_id=? "
        "AND event_type IN ('session_state_changed','turn_complete') ORDER BY event_id DESC", (sid,),
    ):
        data = json.loads(row["data_json"])
        if is_nested_event_data(data):
            continue
        if row["event_type"] == "session_state_changed":
            if data.get("status") != "running" or "turn_index" not in data:
                continue
            if data["turn_index"] == turn["turn_index"]:
                return outcome
            break
        reason = data.get("stop_reason")
        if outcome is not None or not isinstance(reason, str) or not reason:
            raise ValueError("Ambiguous persisted root completion")
        outcome = (reason, row["timestamp"])
    if outcome is not None:
        raise ValueError("Root completion lacks a matching turn start")
    return None
