"""Session manager -- lifecycle, persistence, and event routing.

Manages all active sessions. Each session wraps one agent subprocess
and an EventLog for SSE streaming. State is persisted to SQLite so
sessions survive service restarts.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from typing import Any

from .db import Database
from .events import EventLog
from .models import SessionStatus
from .transport import AgentProcess, SpawnTarget, spawn_local

log = logging.getLogger("agent-bridge")

# -- Name generator ----------------------------------------------------------

_ADJECTIVES = [
    "swift", "bright", "calm", "deft", "eager", "fair", "keen", "bold",
    "warm", "wise", "neat", "glad", "true", "pure", "crisp", "clear",
]
_NOUNS = [
    "falcon", "cedar", "river", "spark", "forge", "bloom", "ridge", "crest",
    "grove", "haven", "quest", "drift", "flame", "stone", "brook", "dawn",
]


def _generate_name() -> str:
    return f"{random.choice(_ADJECTIVES)}-{random.choice(_NOUNS)}"  # noqa: S311


class Session:
    """In-memory state for a single agent-bridge session."""

    def __init__(
        self,
        session_id: str,
        name: str,
        target: SpawnTarget,
        agent_name: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.name = name
        self.agent_name = agent_name
        self.target = target
        self.process: AgentProcess | None = None
        self.status = SessionStatus.CREATED
        self.turn_count = 0
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.event_log: EventLog | None = None

    @property
    def pid(self) -> int | None:
        if self.process and self.process.alive:
            return self.process.pid
        return None

    def touch(self) -> None:
        self.updated_at = time.time()


class SessionManager:
    """Manages all agent-bridge sessions with SQLite persistence."""

    MAX_SESSIONS = 100

    def __init__(self, db: Database) -> None:
        self._db = db
        self._sessions: dict[str, Session] = {}
        self._rehydrate()

    def _rehydrate(self) -> None:
        """Reload session metadata from DB on startup.

        Running processes are gone after a restart, so any session that
        was RUNNING/IDLE/STARTING gets marked STOPPED (resumable).
        Sessions that were ENDED get cleaned up.
        """
        rows = self._db.list_sessions()
        now = time.time()
        for row in rows:
            sid = row["id"]
            status = row["status"]

            if status == SessionStatus.ENDED.value:
                self._db.delete_session(sid)
                continue

            target = SpawnTarget(
                type=row.get("target_type", "local"),
                cwd=row.get("target_dir", "."),
            )

            session = Session(
                session_id=sid,
                name=row["name"],
                target=target,
                agent_name=row.get("agent_name"),
            )
            session.created_at = row["created_at"]
            session.updated_at = row["updated_at"]

            # Mark formerly-active sessions as stopped
            if status in (
                SessionStatus.RUNNING.value,
                SessionStatus.IDLE.value,
                SessionStatus.STARTING.value,
            ):
                session.status = SessionStatus.STOPPED
                self._db.update_session_status(sid, SessionStatus.STOPPED.value, now)
                log.info("Session %s (%s) marked STOPPED after restart", sid, session.name)
            else:
                session.status = SessionStatus(status)

            # Restore event log from DB
            session.event_log = EventLog.from_db(self._db, sid)
            session.turn_count = len(self._db.get_turns(sid))

            self._sessions[sid] = session

        log.info("Rehydrated %d sessions from DB", len(self._sessions))

    async def start_session(
        self,
        target: SpawnTarget,
        agent_name: str | None = None,
    ) -> Session:
        """Create and start a new agent session."""
        session_id = str(uuid.uuid4())[:12]
        name = _generate_name()
        now = time.time()

        session = Session(session_id, name, target, agent_name)
        session.event_log = EventLog(db=self._db, session_id=session_id)

        # Persist to DB
        self._db.create_session(
            session_id=session_id,
            name=name,
            agent_name=agent_name,
            target_dir=target.cwd,
            target_type=target.type,
            status=SessionStatus.STARTING.value,
            now=now,
        )

        session.status = SessionStatus.STARTING
        self._sessions[session_id] = session

        # Spawn the agent process
        try:
            session.process = await spawn_local(target)
            session.status = SessionStatus.IDLE
            self._db.update_session_status(
                session_id, SessionStatus.IDLE.value, time.time(), pid=session.pid
            )
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.IDLE.value,
            })
            log.info(
                "Session %s (%s) started, pid=%s",
                session_id, name, session.pid,
            )
        except Exception as exc:
            session.status = SessionStatus.FAILED
            self._db.update_session_status(
                session_id, SessionStatus.FAILED.value, time.time()
            )
            session.event_log.append("error", {"message": str(exc)})
            log.error("Failed to start session %s: %s", session_id, exc)

        session.touch()
        return session

    async def submit_prompt(self, session_id: str, prompt: str) -> int:
        """Submit a prompt to a session, returning the turn index."""
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        if session.status not in (SessionStatus.IDLE,):
            raise ValueError(
                f"Session {session_id} is {session.status.value}, not idle"
            )
        if not session.process or not session.process.alive:
            raise RuntimeError(f"Session {session_id} has no running process")

        turn_index = session.turn_count
        session.turn_count += 1
        now = time.time()

        # Persist turn
        self._db.create_turn(session_id, turn_index, prompt, now)

        # Update status
        session.status = SessionStatus.RUNNING
        self._db.update_session_status(session_id, SessionStatus.RUNNING.value, now)

        session.event_log.append("session_state_changed", {
            "status": SessionStatus.RUNNING.value,
            "turn_index": turn_index,
        })

        # TODO: Actually send the prompt via ACP protocol to the subprocess.
        # Phase 1 scaffolds the lifecycle; ACP protocol integration is next.
        session.event_log.append("turn_complete", {
            "turn_index": turn_index,
            "note": "ACP protocol integration pending",
        })

        session.status = SessionStatus.IDLE
        self._db.update_session_status(session_id, SessionStatus.IDLE.value, time.time())

        session.touch()
        return turn_index

    async def stop_session(self, session_id: str) -> None:
        """Stop a session -- kill process, preserve state for resume."""
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        if session.process:
            await session.process.kill()
            session.process = None

        session.status = SessionStatus.STOPPED
        now = time.time()
        self._db.update_session_status(session_id, SessionStatus.STOPPED.value, now)
        session.event_log.append("session_state_changed", {
            "status": SessionStatus.STOPPED.value,
        })
        session.touch()
        log.info("Session %s (%s) stopped", session_id, session.name)

    async def end_session(self, session_id: str) -> None:
        """End a session -- stop process and clean up all state."""
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        if session.process:
            await session.process.kill()
            session.process = None

        session.status = SessionStatus.ENDED
        self._db.delete_session(session_id)
        del self._sessions[session_id]
        log.info("Session %s (%s) ended and cleaned up", session_id, session.name)

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    def list_sessions(self, status: str | None = None) -> list[Session]:
        sessions = list(self._sessions.values())
        if status:
            sessions = [s for s in sessions if s.status.value == status]
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)
