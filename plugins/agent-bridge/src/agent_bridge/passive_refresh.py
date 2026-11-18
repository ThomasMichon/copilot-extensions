"""Prepare inherited session history without borrowing any live frontend handles."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .db import Database
from .events import EventLog, SseEvent
from .session_host.host_index import HostRecord
from .transport import SpawnTarget


def stored_target(row: dict[str, Any]) -> SpawnTarget:
    if row.get("target_json"):
        return SpawnTarget.from_json(row["target_json"])
    return SpawnTarget(type=row.get("target_type", "local"), cwd=row.get("target_dir", "."))


@dataclass(frozen=True)
class InheritedRefresh:
    row: dict[str, Any] | None
    host: HostRecord | None
    history: EventLog | None = None
    turns: int = 0
    progress: dict[str, str] | None = None
    last_output: float | None = None

    @classmethod
    def read(cls, db: Database, row: dict[str, Any], host: HostRecord | None) -> InheritedRefresh | None:
        from .session_manager import _parse_progress_markers

        sid = row["id"]
        events = [SseEvent(e["event_id"], e["event_type"], e["data"], e["timestamp"])
                  for e in db.get_events(sid, after=0)]
        head = events[-1].id if events else 0
        if any(event.id != expected for expected, event in enumerate(events, 1)):
            return None
        origin = events[0].timestamp if events else None
        log = EventLog.from_captured_events(events, head + 1, db=db, session_id=sid,
                                            acp_session_id=row.get("acp_session_id"),
                                            worktree_id=stored_target(row).worktree_id)
        progress: dict[str, str] = {}
        last_output = None
        for event in events:
            if event.data.get("history_replay") is True:
                continue
            last_output = event.timestamp
            if event.event == "agent_message":
                progress.update(_parse_progress_markers(event.data.get("text", "")))
        # An append or rebuild can commit without changing the session row.
        db.flush()
        state = db.execute_read(
            "SELECT s.*, (SELECT timestamp FROM events WHERE session_id=s.id AND event_id=1) AS history_origin, "
            "(SELECT COALESCE(MAX(event_id),0) FROM events WHERE session_id=s.id) AS history_head, "
            "(SELECT COUNT(*) FROM turns WHERE session_id=s.id) AS history_turns FROM sessions s WHERE s.id=?", (sid,),
        )
        if not state:
            return None
        current = dict(state[0])
        turns = current.pop("history_turns")
        position = current.pop("history_origin"), current.pop("history_head")
        if current != row or position != (origin, head):
            return None
        return cls(row, host, log, turns, progress, last_output)
