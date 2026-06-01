"""Session manager -- lifecycle, persistence, and event routing.

Manages all active sessions. Each session wraps one ACP client (which
owns the subprocess) and an EventLog for SSE streaming. State is
persisted to SQLite so sessions survive service restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
import time
import uuid
from typing import Any

from .acp_client import AcpClient
from .db import Database
from .events import EventLog
from .models import SessionStatus
from .transport import SpawnTarget, spawn

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
        self.client: AcpClient | None = None
        self.status = SessionStatus.CREATED
        self.turn_count = 0
        self.created_at = time.time()
        self.updated_at = self.created_at
        self.event_log: EventLog | None = None
        self.acp_session_id: str | None = None
        self._prompt_task: asyncio.Task | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def pid(self) -> int | None:
        if self.client and self.client.is_running:
            return self.client.pid
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
        Sessions that were ENDED get cleaned up. Incomplete turns are
        marked as interrupted.
        """
        rows = self._db.list_sessions()
        now = time.time()
        for row in rows:
            sid = row["id"]
            status = row["status"]

            if status == SessionStatus.ENDED.value:
                self._db.delete_session(sid)
                continue

            target_json = row.get("target_json")
            if target_json:
                target = SpawnTarget.from_json(target_json)
            else:
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
            session.acp_session_id = row.get("acp_session_id")

            # Mark formerly-active sessions as stopped
            if status in (
                SessionStatus.RUNNING.value,
                SessionStatus.IDLE.value,
                SessionStatus.STARTING.value,
            ):
                session.status = SessionStatus.STOPPED
                self._db.update_session_status(sid, SessionStatus.STOPPED.value, now)
                log.info("Session %s (%s) marked STOPPED after restart", sid, session.name)

                # Mark incomplete turns as interrupted
                for turn in self._db.get_turns(sid):
                    if turn.get("completed_at") is None:
                        self._db.update_turn(
                            sid, turn["turn_index"],
                            stop_reason="interrupted",
                            completed_at=now,
                        )
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
        permission_callback: Any | None = None,
    ) -> Session:
        """Create and start a new agent session.

        Spawns a copilot --acp --stdio subprocess, initializes the ACP
        protocol, and creates a new ACP session. The session is ready
        to receive prompts when this returns.

        Args:
            target: Where/how to spawn the agent.
            agent_name: Optional display name for the agent.
            permission_callback: Optional async callback for permission
                requests. Signature: (session_id, options, tool_call) ->
                RequestPermissionResponse. If set, auto_approve is disabled.
        """
        session_id = str(uuid.uuid4())[:12]
        name = _generate_name()
        now = time.time()

        session = Session(session_id, name, target, agent_name)
        session.event_log = EventLog(db=self._db, session_id=session_id)

        # Wire ACP events into the session's event log
        def on_acp_event(event_type: str, data: dict[str, Any]) -> None:
            if session.event_log:
                session.event_log.append(event_type, data)

        # Persist to DB
        self._db.create_session(
            session_id=session_id,
            name=name,
            agent_name=agent_name,
            target_dir=target.cwd,
            target_type=target.type,
            status=SessionStatus.STARTING.value,
            now=now,
            target_json=target.to_json(),
        )

        session.status = SessionStatus.STARTING
        self._sessions[session_id] = session

        try:
            # Spawn the subprocess (local or SSH)
            agent_proc = await spawn(target)

            # Initialize ACP protocol on the subprocess
            client = AcpClient(
                on_event=on_acp_event,
                on_permission=permission_callback,
            )
            if permission_callback:
                client.auto_approve = False
            await client.start(agent_proc.proc)

            # Create ACP session -- binstub agents resolve CWD remotely,
            # so target.cwd may be None; fall back to "." (the agent's
            # actual CWD is set by the launch script, not by this value).
            acp_sid = await client.new_session(cwd=target.cwd or ".")

            session.client = client
            session.acp_session_id = acp_sid
            session.status = SessionStatus.IDLE
            self._db.update_session_acp_id(session_id, acp_sid)
            self._db.update_session_status(
                session_id, SessionStatus.IDLE.value, time.time(), pid=session.pid
            )
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.IDLE.value,
                "acp_session_id": acp_sid,
            })
            log.info(
                "Session %s (%s) started, pid=%s, acp=%s",
                session_id, name, session.pid, acp_sid,
            )
        except Exception as exc:
            session.status = SessionStatus.FAILED
            self._db.update_session_status(
                session_id, SessionStatus.FAILED.value, time.time()
            )
            session.event_log.append("error", {"message": str(exc)})
            log.error("Failed to start session %s: %s", session_id, exc, exc_info=True)

        session.touch()
        return session

    async def resume_session(
        self,
        session_id: str,
        permission_callback: Any | None = None,
    ) -> Session:
        """Resume a stopped session by spawning a new process.

        Uses AcpClient.load_session() to reattach to the persisted ACP
        session. The session is ready to receive prompts when this returns.
        """
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        async with session._lifecycle_lock:
            if session.status != SessionStatus.STOPPED:
                raise ValueError(
                    f"Session {session_id} is {session.status.value}, not stopped"
                )
            if not session.acp_session_id:
                raise RuntimeError(
                    f"Session {session_id} has no ACP session ID -- cannot resume"
                )

            session.status = SessionStatus.STARTING
            self._db.update_session_status(
                session_id, SessionStatus.STARTING.value, time.time()
            )

            def on_acp_event(event_type: str, data: dict[str, Any]) -> None:
                if session.event_log:
                    session.event_log.append(event_type, data)

            client: AcpClient | None = None
            try:
                agent_proc = await spawn(session.target)
                client = AcpClient(
                    on_event=on_acp_event,
                    on_permission=permission_callback,
                )
                if permission_callback:
                    client.auto_approve = False
                await client.start(agent_proc.proc)
                await client.load_session(
                    cwd=session.target.cwd or ".",
                    session_id=session.acp_session_id,
                )

                session.client = client
                session.status = SessionStatus.IDLE
                self._db.update_session_status(
                    session_id, SessionStatus.IDLE.value, time.time(),
                    pid=session.pid,
                )
                if session.event_log:
                    session.event_log.append("session_state_changed", {
                        "status": SessionStatus.IDLE.value,
                        "resumed": True,
                        "acp_session_id": session.acp_session_id,
                    })
                log.info(
                    "Session %s (%s) resumed, pid=%s",
                    session_id, session.name, session.pid,
                )
            except Exception as exc:
                # Clean up the client/process on failure
                if client:
                    try:
                        await client.shutdown()
                    except Exception:
                        pass
                session.client = None
                session.status = SessionStatus.STOPPED
                self._db.update_session_status(
                    session_id, SessionStatus.STOPPED.value, time.time()
                )
                if session.event_log:
                    session.event_log.append("error", {
                        "message": f"Resume failed: {exc}",
                    })
                log.error("Failed to resume session %s: %s", session_id, exc)
                raise

        session.touch()
        return session

    async def submit_prompt(self, session_id: str, prompt: str) -> int:
        """Submit a prompt to a session, returning the turn index.

        The prompt is sent to the ACP subprocess. Streaming events
        (agent_message, tool_call_start, etc.) flow to the EventLog in
        real time. The prompt runs as a background task so the HTTP
        request can return immediately -- callers consume output via SSE.
        """
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        if session.status not in (SessionStatus.IDLE,):
            raise ValueError(
                f"Session {session_id} is {session.status.value}, not idle"
            )
        if not session.client or not session.client.is_running:
            raise RuntimeError(f"Session {session_id} has no running process")

        turn_index = session.turn_count
        session.turn_count += 1
        now = time.time()

        # Persist turn skeleton
        self._db.create_turn(session_id, turn_index, prompt, now)

        # Update status
        session.status = SessionStatus.RUNNING
        self._db.update_session_status(session_id, SessionStatus.RUNNING.value, now)

        if session.event_log:
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.RUNNING.value,
                "turn_index": turn_index,
            })

        # Run the prompt as a background task
        session._prompt_task = asyncio.create_task(
            self._run_prompt(session, turn_index, prompt)
        )

        session.touch()
        return turn_index

    async def _run_prompt(
        self, session: Session, turn_index: int, prompt: str
    ) -> None:
        """Background task: send prompt via ACP and persist the result."""
        try:
            result = await session.client.send_prompt(prompt)

            # Persist completed turn
            self._db.update_turn(
                session.session_id,
                turn_index,
                response_text=result.get("response_text", ""),
                thought_text=result.get("thought_text", ""),
                stop_reason=result.get("stop_reason"),
                tool_calls_json=json.dumps(result.get("tool_calls", [])),
                completed_at=time.time(),
            )

            session.status = SessionStatus.IDLE
            self._db.update_session_status(
                session.session_id, SessionStatus.IDLE.value, time.time()
            )

        except Exception as exc:
            log.error(
                "Prompt failed for session %s turn %d: %s",
                session.session_id, turn_index, exc,
            )
            self._db.update_turn(
                session.session_id,
                turn_index,
                stop_reason=f"error: {exc}",
                completed_at=time.time(),
            )
            session.status = SessionStatus.IDLE
            self._db.update_session_status(
                session.session_id, SessionStatus.IDLE.value, time.time()
            )

        session.touch()

    async def stop_session(self, session_id: str) -> None:
        """Stop a session -- shut down ACP client, preserve state for resume."""
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        # Cancel in-flight prompt if any
        if session._prompt_task and not session._prompt_task.done():
            if session.client:
                await session.client.cancel_prompt()
            session._prompt_task.cancel()

        if session.client:
            await session.client.shutdown()
            session.client = None

        session.status = SessionStatus.STOPPED
        now = time.time()
        self._db.update_session_status(session_id, SessionStatus.STOPPED.value, now)
        if session.event_log:
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.STOPPED.value,
            })
        session.touch()
        log.info("Session %s (%s) stopped", session_id, session.name)

    async def end_session(self, session_id: str) -> None:
        """End a session -- shut down client and clean up all state."""
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        if session._prompt_task and not session._prompt_task.done():
            session._prompt_task.cancel()

        if session.client:
            await session.client.shutdown()
            session.client = None

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
