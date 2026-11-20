"""Prepare inherited session history without borrowing any live frontend handles."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .db import Database
from .events import EventLog, SseEvent
from .session_host.host_index import HostRecord
from .transport import SpawnTarget

if TYPE_CHECKING:
    from .session_manager import Session


_CURRENT = (
    "SELECT s.*, (SELECT timestamp FROM events WHERE session_id=s.id AND event_id=1) AS history_origin, "
    "(SELECT COALESCE(MAX(event_id),0) FROM events WHERE session_id=s.id) AS history_head, "
    "(SELECT COUNT(*) FROM turns WHERE session_id=s.id) AS history_turns FROM sessions s WHERE s.id=?"
)


@dataclass(frozen=True)
class RefreshTarget:
    session: Session
    log: EventLog
    cursor: tuple[str | None, int, int]
    state: tuple[object, ...]

    @staticmethod
    def _state(session: Session) -> tuple[object, ...]:
        return (session._stop_notices, session.status, session.background_recovery_enabled,
                session.updated_at, session.acp_session_id)

    @classmethod
    def capture(cls, session: Session) -> RefreshTarget:
        log = session.event_log
        if log is None:
            raise ValueError("No inherited session log")
        return cls(session, log, log.refresh_position(), cls._state(session))

    def matches(self, session: Session) -> bool:
        return self.session is session and self.log is session.event_log and self.state == self._state(session)


def stored_target(row: dict[str, Any]) -> SpawnTarget:
    if row.get("target_json"):
        return SpawnTarget.from_json(row["target_json"])
    return SpawnTarget(type=row.get("target_type", "local"), cwd=row.get("target_dir", "."))


def _read_session_events(db: Database, sid: str) -> list[SseEvent]:
    db.flush(session_id=sid)
    rows = db.execute_read(
        "SELECT event_id, event_type, data_json, timestamp FROM events WHERE session_id=? ORDER BY event_id", (sid,),
    )
    return [SseEvent(row[0], row[1], json.loads(row[2]), row[3]) for row in rows]


@dataclass(frozen=True)
class InheritedRefresh:
    row: dict[str, Any] | None
    host: HostRecord | None
    history: EventLog | None = None
    turns: int = 0
    progress: dict[str, str] | None = None
    last_output: float | None = None
    origin: float | None = None
    head: int = 0
    target: RefreshTarget | None = None

    @contextmanager
    def if_current(self, db: Database) -> Iterator[bool]:
        """Exclude this database owner's pending admissions as well as SQLite commits.

        A different frontend's in-memory queue is private to that frontend:
        its caller must establish retirement before admitting this refresh.
        """
        if self.target is None or db.read_only or not db._write_lock.acquire(blocking=False):
            yield False
            return
        try:
            with db._event_write_q.if_settled(self.target.session.session_id) as settled:
                if not settled:
                    yield False
                    return
                with self._if_durable_current(db) as current:
                    yield current
        finally:
            db._write_lock.release()

    @contextmanager
    def _if_durable_current(self, db: Database) -> Iterator[bool]:
        assert self.target is not None
        conn = db._get_conn()
        if conn.in_transaction:
            yield False
            return
        timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        conn.execute("PRAGMA busy_timeout=0")
        try:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower():
                    raise
                yield False
                return
            try:
                record = conn.execute(_CURRENT, (self.target.session.session_id,)).fetchone()
                if record is None or self.row is None:
                    yield record is None and self.row is None and self.host is None
                    return
                current = dict(record)
                position = current.pop("history_origin"), current.pop("history_head"), current.pop("history_turns")
                yield current == self.row and position == (self.origin, self.head, self.turns)
            finally:
                conn.rollback()
        finally:
            conn.execute(f"PRAGMA busy_timeout={timeout}")

    @classmethod
    def read(cls, db: Database, row: dict[str, Any], host: HostRecord | None,
             *, target: RefreshTarget | None = None) -> InheritedRefresh | None:
        from .session_manager import _parse_progress_markers

        sid = row["id"]
        events = _read_session_events(db, sid)
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
        db.flush(session_id=sid)
        state = db.execute_read(_CURRENT, (sid,))
        if not state:
            return None
        current = dict(state[0])
        turns = current.pop("history_turns")
        position = current.pop("history_origin"), current.pop("history_head")
        if current != row or position != (origin, head):
            return None
        return cls(row, host, log, turns, progress, last_output, origin, head, target)
