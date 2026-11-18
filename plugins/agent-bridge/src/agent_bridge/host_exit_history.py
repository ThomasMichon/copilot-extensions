"""Root-turn evidence used when both local Host processes have exited."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json
import sqlite3
from typing import TYPE_CHECKING, NamedTuple

from .events import is_nested_event_data
from .status_producers import owned_event_identity, source_key
from .status_store import StatusStore

if TYPE_CHECKING:
    from .db import Database
    from .session_host.host_index import HostRecord


def stopped_event(record: HostRecord, native: str, created: float) -> str:
    # Bind the retry to this session incarnation and the exact locked record.
    # Do not put its endpoint or attachment credentials in observable history.
    identity = json.dumps([native, created, asdict(record)], sort_keys=True, separators=(",", ":"))
    return json.dumps({"status": "stopped", "trigger": "host_exit",
                       "settlement": hashlib.sha256(identity.encode()).hexdigest()}, sort_keys=True)


class Completion(NamedTuple):
    reason: str
    timestamp: float
    event_id: int


def matches_stopped_receipt(conn: sqlite3.Connection, sid: str, receipt: str, updated_at: float) -> bool:
    last = conn.execute(
        "SELECT data_json,timestamp FROM events WHERE session_id=? AND event_type='session_state_changed' "
        "ORDER BY event_id DESC LIMIT 1", (sid,),
    ).fetchone()
    return last is not None and (last["data_json"], last["timestamp"]) == (receipt, updated_at)


def reconcile_idle(db: Database, conn: sqlite3.Connection, sid: str, completion: Completion) -> bool:
    origin = conn.execute("SELECT timestamp FROM events WHERE session_id=? AND event_id=1", (sid,)).fetchone()
    epoch = db._event_continuity(sid, origin[0] if origin else None)
    if not epoch:
        return False
    key = source_key(sid, owned_event_identity(sid, epoch, completion.event_id))
    store = StatusStore(db)
    if conn.execute(
        "SELECT 1 FROM bridge_status_events WHERE source='local' AND source_key=?", (key,),
    ).fetchone():
        return True
    # An absent key in a truncated feed may have been published and acknowledged.
    if store.bounds(conn)["floor"] > 1:
        return False
    store.publish(sid, "idle", key, transaction=conn, observed_at=completion.timestamp,
                  evidence="historical_transition")
    return True


def terminal_outcome(
    conn: sqlite3.Connection, sid: str, turn: sqlite3.Row,
) -> Completion | None:
    """Return the root outcome, None if unfinished, or refuse ambiguous evidence."""
    outcome: Completion | None = None
    for row in conn.execute(
        "SELECT event_id,event_type,data_json,timestamp FROM events WHERE session_id=? "
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
        outcome = Completion(reason, row["timestamp"], row["event_id"])
    if outcome is not None:
        raise ValueError("Root completion lacks a matching turn start")
    return None
