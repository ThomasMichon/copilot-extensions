"""Session manager -- lifecycle, persistence, and event routing.

Manages all active sessions. Each session wraps one ACP client (which
owns the subprocess) and an EventLog for SSE streaming. State is
persisted to SQLite so sessions survive service restarts.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import sys
import time
import uuid
from typing import Any

from .acp_client import AcpClient
from .db import Database
from .events import EventLog
from .models import ContextThresholds, SessionStatus
from .transport import SpawnTarget, spawn

log = logging.getLogger("agent-bridge")

# Session states that "occupy" a workspace -- a workspace with a session
# in any of these states cannot accept a second concurrent session.
# STOPPED is included because it is resumable (the ACP session persists),
# so it still owns the workspace until explicitly ended.
_ACTIVE_STATES = frozenset({
    SessionStatus.STARTING,
    SessionStatus.RUNNING,
    SessionStatus.IDLE,
    SessionStatus.STOPPING,
    SessionStatus.STOPPED,
})


class SessionConflictError(Exception):
    """Raised when an agent already has an active session and concurrent
    sessions are not allowed.

    CodeSpace (command-type) agents share a single checkout that cannot be
    safely multiplexed, so only one active session is permitted per agent.
    """

    def __init__(self, agent_name: str, existing_session_id: str) -> None:
        self.agent_name = agent_name
        self.existing_session_id = existing_session_id
        super().__init__(
            f"Agent '{agent_name}' already has an active session "
            f"{existing_session_id}; only one session per CodeSpace is "
            "allowed. Reuse it (send to the session id) or end it first."
        )


def _workspace_key(
    agent_name: str | None,
    target: SpawnTarget,
    caller_id: str | None,
) -> tuple | None:
    """Compute the concurrency key for a session, or None if unguarded.

    A "workspace" is a checkout that can hold at most one active session.

    - Command-type (CodeSpace / provider) agents share one checkout that
      cannot be multiplexed, so the key is the agent name alone -- every
      caller maps to the same single session regardless of worktree.
    - Local / SSH / worktree agents can run concurrent sessions against
      separate checkouts (each local worktree has its own caller_id), so
      they are not hard-guarded here (returns None).
    """
    if agent_name and target.type == "command":
        return ("agent", agent_name)
    return None

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


async def _cleanup_worktree(target: SpawnTarget, turn_count: int) -> None:
    """Attempt to clean up the worktree associated with a session.

    For 0-turn sessions (unused worktrees), runs agent-worktrees cleanup
    with --include-unused to remove worktrees that have no commits. For
    sessions with turns, logs a notice -- manual finalization is required.
    """
    worktree_id = target.worktree_id
    if not worktree_id or not target.project:
        return

    if turn_count > 0:
        log.info(
            "Worktree %s has %d turn(s) -- skipping automatic cleanup "
            "(manual finalization required)",
            worktree_id, turn_count,
        )
        return

    # 0-turn session: run cleanup --clean --include-unused to remove
    # all accumulated unused worktrees (including this one)
    home = os.path.expanduser("~")
    aw_venv = os.path.join(home, ".agent-worktrees", ".venv")
    aw_lib = os.path.join(home, ".agent-worktrees", "lib")

    if sys.platform == "win32":
        python = os.path.join(aw_venv, "Scripts", "python.exe")
    else:
        python = os.path.join(aw_venv, "bin", "python")

    if not os.path.exists(python):
        log.warning("Cannot cleanup worktree %s: agent-worktrees venv not found", worktree_id)
        return

    env = os.environ.copy()
    env["PYTHONPATH"] = aw_lib
    env["PYTHONUTF8"] = "1"
    env["WORKTREE_PROJECT"] = target.project

    cmd = [python, "-m", "agent_worktrees", "cleanup", "--clean", "--include-unused"]
    log.info("Cleaning up unused worktrees (session %s was 0-turn): %s", worktree_id, " ".join(cmd))

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode == 0:
            log.info("Worktree cleanup completed successfully")
            if stdout:
                for line in stdout.decode(errors="replace").strip().splitlines():
                    log.debug("cleanup: %s", line)
        else:
            err = stderr.decode(errors="replace").strip()
            log.warning("Worktree cleanup failed (exit %d): %s", proc.returncode, err)
    except Exception as exc:
        log.warning("Worktree cleanup error: %s", exc)


def _default_cwd(target: SpawnTarget) -> str:
    """Derive a plausible default CWD for a spawn target.

    Binstub SSH agents resolve CWD remotely, so target.cwd is None.
    The ACP spec requires an absolute path for new_session/load_session.
    The actual working directory is set by the remote launch script --
    this value is only used to satisfy the ACP protocol requirement.
    """
    user = target.user or "root"
    # PowerShell/cmd targets are Windows -- home is C:\Users\<user>
    if target.ssh_shell in ("pwsh", "powershell", "cmd"):
        return f"C:\\Users\\{user}"
    return f"/home/{user}"


class Session:
    """In-memory state for a single agent-bridge session."""

    def __init__(
        self,
        session_id: str,
        name: str,
        target: SpawnTarget,
        agent_name: str | None = None,
        caller_id: str | None = None,
    ) -> None:
        self.session_id = session_id
        self.name = name
        self.agent_name = agent_name
        self.caller_id = caller_id
        self.target = target
        self.client: AcpClient | None = None
        self.status = SessionStatus.CREATED
        self.turn_count = 0
        self.context_size: int | None = None
        self.context_used: int | None = None
        self.usage_model: str | None = None
        self.last_usage_at: float | None = None
        self._crossed_thresholds: set[str] = set()
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

    @property
    def context_pct(self) -> float | None:
        """Context usage as a percentage, or None if unknown."""
        if self.context_size and self.context_used is not None:
            return round(self.context_used / self.context_size * 100, 1)
        return None

    def touch(self) -> None:
        self.updated_at = time.time()


class SessionManager:
    """Manages all agent-bridge sessions with SQLite persistence."""

    MAX_SESSIONS = 100

    def __init__(
        self,
        db: Database,
        *,
        context_thresholds: ContextThresholds | None = None,
    ) -> None:
        self._db = db
        self._sessions: dict[str, Session] = {}
        self._thresholds = context_thresholds or ContextThresholds()
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
                caller_id=row.get("caller_id"),
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

            # Restore context usage from DB
            session.context_size = row.get("context_size")
            session.context_used = row.get("context_used")
            session.usage_model = row.get("usage_model")
            session.last_usage_at = row.get("last_usage_at")

            self._sessions[sid] = session

        log.info("Rehydrated %d sessions from DB", len(self._sessions))

    def _find_active_session(self, ws_key: tuple) -> Session | None:
        """Return an existing session that occupies the given workspace key.

        A session occupies a workspace when its status is in _ACTIVE_STATES.
        Used by the concurrency guard to enforce one session per CodeSpace.
        """
        for s in self._sessions.values():
            if s.status not in _ACTIVE_STATES:
                continue
            if _workspace_key(s.agent_name, s.target, s.caller_id) == ws_key:
                return s
        return None

    async def start_session(
        self,
        target: SpawnTarget,
        agent_name: str | None = None,
        caller_id: str | None = None,
        permission_callback: Any | None = None,
    ) -> Session:
        """Create and start a new agent session.

        Spawns a copilot --acp --stdio subprocess, initializes the ACP
        protocol, and creates a new ACP session. The session is ready
        to receive prompts when this returns.

        Args:
            target: Where/how to spawn the agent.
            agent_name: Optional display name for the agent.
            caller_id: Optional caller identity (e.g. worktree ID) for
                session affinity.  Sessions with matching (agent_name,
                caller_id) are reused instead of creating new ones.
            permission_callback: Optional async callback for permission
                requests. Signature: (session_id, options, tool_call) ->
                RequestPermissionResponse. If set, auto_approve is disabled.
        """
        session_id = str(uuid.uuid4())[:12]
        name = _generate_name()
        now = time.time()

        # Concurrency guard: command-type (CodeSpace) agents allow only one
        # active session at a time, since they share a single checkout. This
        # check and the self._sessions registration below run synchronously
        # (no await in between), so concurrent start_session calls cannot
        # race past the guard.
        ws_key = _workspace_key(agent_name, target, caller_id)
        if ws_key is not None:
            existing = self._find_active_session(ws_key)
            if existing is not None:
                raise SessionConflictError(
                    agent_name=agent_name or "",
                    existing_session_id=existing.session_id,
                )

        session = Session(session_id, name, target, agent_name, caller_id=caller_id)
        session.event_log = EventLog(db=self._db, session_id=session_id)

        # Wire ACP events into the session's event log
        def on_acp_event(event_type: str, data: dict[str, Any]) -> None:
            if session.event_log:
                session.event_log.append(event_type, data)
            if event_type == "usage_update":
                self._handle_usage_update(session, data)

        # Persist to DB
        self._db.create_session(
            session_id=session_id,
            name=name,
            agent_name=agent_name,
            caller_id=caller_id,
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
            # so target.cwd may be None.  The ACP spec requires an absolute
            # path.  Derive a plausible home-dir default from the target.
            session_cwd = target.cwd or _default_cwd(target)
            acp_sid = await client.new_session(cwd=session_cwd)

            session.client = client
            session.acp_session_id = acp_sid
            session.status = SessionStatus.IDLE
            self._db.update_session_acp_id(session_id, acp_sid)
            # Persist target with resolved values (worktree_id, cwd from plan)
            self._db.update_session_target(
                session_id, target.to_json(), target.cwd
            )
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
        session_id = self._resolve_ref(session_id) or session_id
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
                if event_type == "usage_update":
                    self._handle_usage_update(session, data)

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
                    cwd=session.target.cwd or _default_cwd(session.target),
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

        If the session process has died (e.g. after a server restart)
        but the ACP session ID is available, the process is
        automatically re-spawned and the session resumed before
        delivering the prompt.
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        if session.status not in (SessionStatus.IDLE, SessionStatus.STOPPED):
            raise ValueError(
                f"Session {session_id} is {session.status.value}, not idle"
            )

        # Auto-resume if the process is dead but session is recoverable
        if not session.client or not session.client.is_running:
            if not session.acp_session_id:
                raise RuntimeError(
                    f"Session {session_id} has no running process and no "
                    "ACP session ID -- cannot auto-resume"
                )
            log.info(
                "Session %s (%s) process is dead -- auto-resuming",
                session_id, session.name,
            )
            # Mark as STOPPED so resume_session accepts it
            session.status = SessionStatus.STOPPED
            await self.resume_session(session_id)
            # resume_session sets status to IDLE and attaches a new client

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

    def _handle_usage_update(
        self, session: Session, data: dict[str, Any]
    ) -> None:
        """Persist context usage and emit threshold warnings."""
        now = time.time()
        ctx_size = data.get("context_size")
        ctx_used = data.get("context_used")
        model = data.get("model")

        session.context_size = ctx_size
        session.context_used = ctx_used
        session.usage_model = model
        session.last_usage_at = now

        self._db.update_session_usage(
            session.session_id,
            context_size=ctx_size,
            context_used=ctx_used,
            usage_model=model,
            now=now,
        )

        # Check thresholds and emit warnings
        if ctx_size and ctx_used is not None and ctx_size > 0:
            pct = ctx_used / ctx_size * 100
            thresholds = self._thresholds

            if pct >= thresholds.critical and "critical" not in session._crossed_thresholds:
                session._crossed_thresholds.add("critical")
                if session.event_log:
                    session.event_log.append("context_critical", {
                        "context_size": ctx_size,
                        "context_used": ctx_used,
                        "context_pct": round(pct, 1),
                        "threshold": thresholds.critical,
                        "message": "Context window usage critical -- consider handoff",
                    })

            elif pct >= thresholds.warning and "warning" not in session._crossed_thresholds:
                session._crossed_thresholds.add("warning")
                if session.event_log:
                    session.event_log.append("context_warning", {
                        "context_size": ctx_size,
                        "context_used": ctx_used,
                        "context_pct": round(pct, 1),
                        "threshold": thresholds.warning,
                        "message": "Context window usage elevated -- prepare for handoff",
                    })

    async def stop_session(self, session_id: str) -> None:
        """Stop a session -- shut down ACP client, preserve state for resume."""
        session_id = self._resolve_ref(session_id) or session_id
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

        # Clean up unused worktrees (0-turn sessions from crash-loops)
        await _cleanup_worktree(session.target, session.turn_count)

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
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        if session._prompt_task and not session._prompt_task.done():
            session._prompt_task.cancel()

        if session.client:
            await session.client.shutdown()
            session.client = None

        # Clean up unused worktrees (0-turn sessions from crash-loops)
        await _cleanup_worktree(session.target, session.turn_count)

        session.status = SessionStatus.ENDED
        self._db.delete_session(session_id)
        del self._sessions[session_id]
        log.info("Session %s (%s) ended and cleaned up", session_id, session.name)

    def _resolve_ref(self, ref: str) -> str | None:
        """Resolve a session reference to the canonical bridge session_id.

        Accepts either the bridge session_id (the internal uuid) or the
        ACP-sourced session id (``acp_session_id``).  Returns the bridge
        session_id, or None if no session matches.  This lets HTTP/CLI
        callers address sessions by the durable ACP id without knowing the
        bridge's internal handle.
        """
        if ref in self._sessions:
            return ref
        for sid, session in self._sessions.items():
            if session.acp_session_id == ref:
                return sid
        return None

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(self._resolve_ref(session_id) or session_id)

    def list_sessions(self, status: str | None = None) -> list[Session]:
        sessions = list(self._sessions.values())
        if status:
            sessions = [s for s in sessions if s.status.value == status]
        return sorted(sessions, key=lambda s: s.updated_at, reverse=True)
