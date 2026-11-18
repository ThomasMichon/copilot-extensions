"""Prepare inherited session history without borrowing any live frontend handles."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from .db import Database
from .events import EventLog
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
        log = EventLog.from_db(db, sid, acp_session_id=row.get("acp_session_id"),
                               worktree_id=stored_target(row).worktree_id)
        progress: dict[str, str] = {}
        last_output = None
        for event in log.get_events(0):
            if event.data.get("history_replay") is True:
                continue
            last_output = time.time()
            if event.event == "agent_message":
                progress.update(_parse_progress_markers(event.data.get("text", "")))
        turns = len(db.get_turns(sid))
        if db.get_session(sid) != row:
            return None
        return cls(row, host, log, turns, progress, last_output)
