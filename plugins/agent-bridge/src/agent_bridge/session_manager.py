"""Session manager -- lifecycle, persistence, and event routing.

Manages all active sessions. Each session wraps one ACP client (which
owns the subprocess) and an EventLog for SSE streaming. State is
persisted to SQLite so sessions survive service restarts.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import random
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from .acp_client import AcpClient
from .connect import ConnectError, ConnectStage, ConnectTracker
from .db import Database
from .events import EventLog
from .models import ContextThresholds, PhasedTimeouts, RetentionConfig, SessionStatus
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


class SessionBusyError(Exception):
    """Raised when a stop/end is refused because the session is hosting active
    background sub-agents.

    Tearing the Copilot process down would kill the in-process background
    agents it is running (e.g. the PR daemon, or another agent session a
    conversation is waiting on). Callers that genuinely intend to abandon that
    work pass ``force=True`` to override.
    """

    def __init__(self, session_id: str, active_background_tasks: list[str]) -> None:
        self.session_id = session_id
        self.active_background_tasks = active_background_tasks
        tasks = ", ".join(active_background_tasks) or "(unknown)"
        super().__init__(
            f"Session {session_id} has active background tasks [{tasks}]; "
            "tearing it down would kill them. Wait for them to finish or pass "
            "force=true to override."
        )


class DaemonDrainingError(Exception):
    """Raised when new work is refused because the daemon is draining.

    During a zero-downtime handoff the daemon stops accepting new sessions and
    new turns so in-flight work can settle before it exits. Callers should
    retry against the routing-table endpoint -- by the time they retry, the
    successor daemon owns the route and answers.
    """

    def __init__(self, what: str = "request") -> None:
        self.what = what
        super().__init__(
            f"agent-bridge is draining for a redeploy and is not accepting a "
            f"new {what}; retry shortly (the successor daemon will answer)."
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


# Structured milestone markers: a dispatched agent reports progress with lines
# like ``PROGRESS: build=ok`` or ``PROGRESS commit=<sha> pr=123`` (the colon is
# optional, matching the dispatch skill's documented convention). The bridge
# captures the latest value per key and exposes it in status, so a watcher gets
# ground-truth milestones (did it build? push? open a PR?) without grepping the
# free-text feed or shelling into the host (#46.3 / #46.4).
_PROGRESS_LINE_RE = re.compile(r"\bPROGRESS:?\s+(.+)")
_PROGRESS_KV_RE = re.compile(r"(\w+)=(\S+)")


def _parse_progress_markers(text: str) -> dict[str, str]:
    """Extract ``PROGRESS: key=value`` milestone markers from agent text."""
    found: dict[str, str] = {}
    if not text or "PROGRESS" not in text:
        return found
    for line in text.splitlines():
        m = _PROGRESS_LINE_RE.search(line)
        if not m:
            continue
        for key, value in _PROGRESS_KV_RE.findall(m.group(1)):
            found[key] = value
    return found


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
        # Structured milestone markers the dispatched agent has reported via
        # `PROGRESS: key=value` lines (e.g. build=ok, commit=<sha>, pr=<id>) --
        # captured from agent_message text and surfaced in status (#46.3).
        self.progress: dict[str, str] = {}
        self._prompt_task: asyncio.Task | None = None
        self._lifecycle_lock = asyncio.Lock()

    @property
    def pid(self) -> int | None:
        if self.client and self.client.is_running:
            return self.client.pid
        return None

    @property
    def active_background_tasks(self) -> list[str]:
        """Copilot agent_ids of background sub-agents this session is hosting.

        Empty when the client is gone or no sub-agents are running. Surfaced in
        status and used to gate teardown (see SessionBusyError).
        """
        if self.client:
            return self.client.active_background_tasks
        return []

    @property
    def has_active_background_tasks(self) -> bool:
        return bool(self.client and self.client.has_active_background_tasks)

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

    # A drain that outlives this many seconds with no handoff completing is
    # treated as stuck/aborted and auto-released so the daemon self-heals
    # instead of returning 503 forever (#1757). Generous enough to cover a slow
    # real cutover (health probe + full drain_timeout), short enough that an
    # aborted cutover does not strand the daemon for hours.
    DRAIN_AUTO_RELEASE_S = 900.0
    # How often the watchdog logs a "still draining" WARN while the gate is open.
    DRAIN_WARN_INTERVAL_S = 60.0

    def __init__(
        self,
        db: Database,
        *,
        context_thresholds: ContextThresholds | None = None,
        timeouts: PhasedTimeouts | None = None,
        retention: RetentionConfig | None = None,
        drain_auto_release_s: float | None = None,
        drain_warn_interval_s: float | None = None,
        session_host_enabled: bool = False,
        session_host_state_dir: str | None = None,
    ) -> None:
        self._db = db
        self._sessions: dict[str, Session] = {}
        # Session-Host mode (experimental, default off): local children live in
        # a survivable Session Host that outlives a frontend restart. The host
        # index is the durable session_id -> host-endpoint map used to reattach.
        self._session_host_enabled = session_host_enabled
        self._host_index: Any = None
        if session_host_enabled:
            from pathlib import Path as _Path

            from .session_host.host_index import HostIndex
            sd = _Path(session_host_state_dir or "~/.agent-bridge/hosts").expanduser()
            sd.mkdir(parents=True, exist_ok=True)
            self._host_index = HostIndex(sd / "index.json")
        self._thresholds = context_thresholds or ContextThresholds()
        self._timeouts = timeouts or PhasedTimeouts()
        self._retention = retention or RetentionConfig()
        # Drain gate: when True the daemon refuses *new* sessions and *new*
        # turns so in-flight work can settle before a zero-downtime handoff.
        # Set via drain()/set_draining(); never persisted (a fresh daemon
        # starts un-drained). Teardown (stop/end) is *never* gated -- it is the
        # operation the drain is waiting for (#1755).
        self._draining = False
        # Drain observability + bounded lifetime (#1757). When the gate opens we
        # record when/why/by-whom and arm a watchdog that WARNs on an interval
        # and finally auto-releases the gate if no cutover ever retires this
        # daemon -- so a stuck/aborted drain self-heals rather than 503'ing new
        # work (including the operator's own diagnosis session) forever.
        self._draining_since: float | None = None
        self._drain_reason: str | None = None
        self._drain_source: str | None = None
        self._drain_watchdog: asyncio.Task[None] | None = None
        self._drain_auto_release_s = (
            self.DRAIN_AUTO_RELEASE_S if drain_auto_release_s is None
            else float(drain_auto_release_s)
        )
        self._drain_warn_interval_s = (
            self.DRAIN_WARN_INTERVAL_S if drain_warn_interval_s is None
            else float(drain_warn_interval_s)
        )
        self._rehydrate()

    @property
    def is_draining(self) -> bool:
        """True once drain() has begun -- new sessions/turns are refused."""
        return self._draining

    def set_draining(
        self,
        value: bool,
        *,
        reason: str | None = None,
        source: str | None = None,
    ) -> None:
        """Open (True) or release (False) the drain gate.

        Logs the transition (with ``source``/``reason``) so a drained daemon is
        never invisible, and -- on open -- arms a watchdog that bounds how long
        the daemon may sit drained before auto-releasing (#1757). Idempotent: a
        call that does not change the gate state is a quiet no-op (the existing
        watchdog and its ``since`` timestamp are preserved).
        """
        value = bool(value)
        if value == self._draining:
            return
        self._draining = value
        if value:
            self._draining_since = time.time()
            self._drain_reason = reason
            self._drain_source = source
            log.info(
                "Drain gate OPENED (source=%s reason=%s) -- refusing new "
                "sessions/turns; reads and teardown still served",
                source or "?", reason or "?",
            )
            self._arm_drain_watchdog()
        else:
            held = (
                time.time() - self._draining_since
                if self._draining_since is not None else 0.0
            )
            log.info(
                "Drain gate RELEASED (source=%s) after %.0fs -- accepting new "
                "work", source or "?", held,
            )
            self._draining_since = None
            self._drain_reason = None
            self._drain_source = None
            self._cancel_drain_watchdog()

    def drain_status(self) -> dict[str, Any]:
        """Snapshot of the drain gate for /health and monitoring (#1757).

        Exposes *how long* the daemon has been drained and when the watchdog
        will auto-release, so a stuck drain is visible without grepping logs.
        """
        now = time.time()
        since = self._draining_since
        held = (now - since) if since is not None else None
        auto_at = (
            since + self._drain_auto_release_s
            if since is not None and self._drain_auto_release_s > 0 else None
        )
        return {
            "draining": self._draining,
            "since": (
                datetime.fromtimestamp(since, tz=timezone.utc).isoformat()
                if since is not None else None
            ),
            "held_s": round(held, 1) if held is not None else None,
            "reason": self._drain_reason,
            "source": self._drain_source,
            "auto_release_at": (
                datetime.fromtimestamp(auto_at, tz=timezone.utc).isoformat()
                if auto_at is not None else None
            ),
        }

    def _arm_drain_watchdog(self) -> None:
        """Start the bounded-drain watchdog if an event loop is running."""
        self._cancel_drain_watchdog()
        if self._drain_auto_release_s <= 0:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No event loop (e.g. a synchronous unit test toggling the gate).
            # The bounded-lifetime backstop is a no-op here; the gate can still
            # be released manually or by the next drain() call under a loop.
            return
        self._drain_watchdog = loop.create_task(self._drain_watchdog_loop())

    def _cancel_drain_watchdog(self) -> None:
        wd = self._drain_watchdog
        self._drain_watchdog = None
        if wd is not None and not wd.done():
            wd.cancel()

    async def _drain_watchdog_loop(self) -> None:
        """Bound how long the daemon may sit drained (#1757).

        WARNs on an interval while the gate is open, then auto-releases it once
        the drain outlives ``_drain_auto_release_s`` with no cutover retiring
        the daemon. A completed handoff shuts the process down before this
        fires; a manual undrain cancels it. This is the self-heal for an
        aborted cutover (or a diagnosis session that can't get in because it is
        itself 503'd) that would otherwise leave the daemon drained forever.
        """
        interval = max(1.0, self._drain_warn_interval_s)
        deadline = (
            (self._draining_since or time.time()) + self._drain_auto_release_s
        )
        try:
            while self._draining:
                await asyncio.sleep(interval)
                if not self._draining:
                    return
                held = (
                    time.time() - self._draining_since
                    if self._draining_since is not None else 0.0
                )
                if time.time() >= deadline:
                    log.warning(
                        "Drain gate open %.0fs (source=%s reason=%s) with no "
                        "handoff completing -- auto-releasing to self-heal (a "
                        "cutover likely aborted)",
                        held, self._drain_source or "?",
                        self._drain_reason or "?",
                    )
                    self.set_draining(False, source="watchdog-auto-release")
                    return
                log.warning(
                    "Still draining after %.0fs (source=%s reason=%s); "
                    "auto-release at %.0fs",
                    held, self._drain_source or "?", self._drain_reason or "?",
                    self._drain_auto_release_s,
                )
        except asyncio.CancelledError:
            return

    def busy_sessions(self) -> list[str]:
        """Session IDs that must not be torn down: actively streaming a turn
        (RUNNING) or hosting active background sub-agents (the dev57 busy
        oracle). This is the signal drain() waits on."""
        busy: list[str] = []
        for sid, session in self._sessions.items():
            if session.status == SessionStatus.RUNNING or \
                    session.has_active_background_tasks:
                busy.append(sid)
        return busy

    async def drain(
        self,
        *,
        timeout: float = 300.0,
        poll: float = 1.0,
        force: bool = False,
        reason: str | None = None,
        source: str = "drain-endpoint",
    ) -> dict[str, Any]:
        """Open the drain gate and wait for in-flight work to settle.

        Refuses new sessions/turns immediately, then blocks until no session is
        busy (see busy_sessions) or ``timeout`` seconds elapse. The OS service
        manager (systemd ExecStop / the Windows pre-stop hook) and the cutover
        orchestrator call this *before* the process exits so an active turn is
        never hard-killed. Returns a summary; ``drained`` is False on timeout
        unless ``force`` is set (the caller accepts interrupting the laggards).

        ``source``/``reason`` are recorded for observability (#1757). Note the
        gate stays open after this returns (the successor retires this daemon);
        the watchdog armed here auto-releases it if that handoff never lands.
        Teardown (stop/end) stays permitted throughout -- it is what lets the
        busy sessions this loop waits on settle (#1755).
        """
        import asyncio as _asyncio

        self.set_draining(True, reason=reason, source=source)
        deadline = time.monotonic() + max(0.0, timeout)
        busy = self.busy_sessions()
        log.info(
            "Drain started: %d session(s) busy, timeout=%.0fs%s",
            len(busy), timeout, " (force)" if force else "",
        )
        while busy and time.monotonic() < deadline:
            await _asyncio.sleep(poll)
            busy = self.busy_sessions()

        drained = not busy
        if drained:
            log.info("Drain complete: no busy sessions remain")
        elif force:
            log.warning(
                "Drain timed out after %.0fs with %d busy session(s) -- "
                "forcing past: %s", timeout, len(busy), ", ".join(busy),
            )
        else:
            log.warning(
                "Drain timed out after %.0fs; %d session(s) still busy: %s",
                timeout, len(busy), ", ".join(busy),
            )
        return {
            "drained": drained or force,
            "clean": drained,
            "forced": bool(force and not drained),
            "busy_sessions": busy,
            "timeout": timeout,
        }


    @property
    def db(self) -> Database:
        """The backing database (used by routes for cursor persistence)."""
        return self._db

    @staticmethod
    def _capture_progress(session: Session, event_type: str, data: dict) -> None:
        """Update a session's structured progress from a captured event (#46.3)."""
        if event_type == "agent_message":
            markers = _parse_progress_markers(data.get("text", ""))
            if markers:
                session.progress.update(markers)

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
                # Defense-in-depth: a single session's cleanup must never brick
                # daemon startup -- log and skip on failure rather than aborting
                # rehydrate (and thus the whole service).
                try:
                    self._db.delete_session(sid)
                except Exception:
                    log.warning(
                        "Failed to clean up ENDED session %s on startup",
                        sid, exc_info=True,
                    )
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

            # Rebuild structured progress from the restored agent messages so a
            # daemon restart preserves reported milestones (#46.3).
            for ev in session.event_log.get_events(0):
                self._capture_progress(session, ev.event, ev.data)

            # Restore context usage from DB
            session.context_size = row.get("context_size")
            session.context_used = row.get("context_used")
            session.usage_model = row.get("usage_model")
            session.last_usage_at = row.get("last_usage_at")

            self._sessions[sid] = session

        log.info("Rehydrated %d sessions from DB", len(self._sessions))

        # Startup GC: prune aged terminal/disconnected sessions and compact
        # the DB so a long-lived daemon's sessions.db doesn't grow without
        # bound (a single big dispatch can otherwise leave tens of GB of
        # freelist pages -- see RetentionConfig).
        try:
            self.gc(reason="startup")
        except Exception:
            log.warning("Startup GC failed", exc_info=True)

    def gc(self, *, now: float | None = None, reason: str = "manual") -> dict[str, Any]:
        """Garbage-collect terminal/disconnected sessions and compact the DB.

        Prunes the bridge's relay metadata (session row + turns + events +
        delivery cursors) for sessions in a terminal state (per
        ``RetentionConfig.statuses``) whose last update is older than the
        retention window, then optionally VACUUMs to return freed pages to the
        OS. Live sessions -- and any whose ACP client is still running -- are
        never touched. The canonical Copilot session history lives outside
        this DB and is unaffected.

        Returns a summary dict: ``enabled``, ``pruned`` (ids), ``pruned_count``,
        ``vacuumed`` (bool), ``reclaimed_bytes``.
        """
        ret = self._retention
        result: dict[str, Any] = {
            "enabled": ret.enabled,
            "pruned": [],
            "pruned_count": 0,
            "vacuumed": False,
            "reclaimed_bytes": 0,
        }
        if not ret.enabled:
            return result

        now = now if now is not None else time.time()
        cutoff = now - ret.max_age_hours * 3600.0
        eligible = self._db.gc_eligible_session_ids(ret.statuses, cutoff)

        pruned: list[str] = []
        for sid in eligible:
            # Safety: never prune a session whose client is still running,
            # even if its persisted status looks terminal.
            sess = self._sessions.get(sid)
            if sess is not None and sess.client and sess.client.is_running:
                continue
            try:
                self._db.delete_session(sid)
            except Exception:
                log.warning("GC: failed to prune session %s", sid, exc_info=True)
                continue
            self._sessions.pop(sid, None)
            pruned.append(sid)

        result["pruned"] = pruned
        result["pruned_count"] = len(pruned)

        if ret.vacuum:
            try:
                info = self._db.db_size_info()
                if info["free_bytes"] >= ret.vacuum_min_free_mb * 1024 * 1024:
                    before = info["total_bytes"]
                    self._db.vacuum()
                    after = self._db.db_size_info()["total_bytes"]
                    result["vacuumed"] = True
                    result["reclaimed_bytes"] = max(0, before - after)
            except Exception:
                # A locked DB (concurrent reader) just defers compaction to the
                # next sweep -- never fatal.
                log.warning("GC: VACUUM skipped/failed", exc_info=True)

        if pruned or result["vacuumed"]:
            log.info(
                "GC (%s): pruned %d session(s), reclaimed %.1f MB%s",
                reason,
                len(pruned),
                result["reclaimed_bytes"] / 1e6,
                " (vacuumed)" if result["vacuumed"] else "",
            )
        return result

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

    async def _connect_via_session_host(
        self,
        target: SpawnTarget,
        *,
        tracker: Any,
        session_id: str,
        on_acp_event: Any,
        permission_callback: Any | None,
    ) -> tuple[AcpClient, str]:
        """Spawn a local child inside a survivable Session Host and drive ACP
        over the reattachable loopback endpoint (Session-Host mode).

        Registers the durable host index so a restarted frontend can reattach.
        Teardown DETACHES (host-mode ``AcpClient.shutdown``), never reaping the
        child inadvertently -- goal 1.
        """
        from . import __version__
        from .session_host.acp_adapter import open_acp_streams
        from .session_host.client import SessionHostClient
        from .session_host.host_index import HostRecord
        from .session_host.launcher import launch_session_host
        from .transport import resolve_local_launch

        args, work_dir, env = await resolve_local_launch(
            target, tracker=tracker, session_id=session_id,
        )
        if work_dir and not target.cwd:
            target.cwd = work_dir

        with tracker.stage(ConnectStage.LAUNCH_ACP):
            # launch_session_host blocks briefly on host readiness; keep it off
            # the event loop.
            handle = await asyncio.to_thread(
                launch_session_host, args, cwd=work_dir, env=env,
            )
            sock = await SessionHostClient.connect(port=handle.port)
            await sock.attach(0)
            streams = await open_acp_streams(sock)

            async def _closer() -> None:
                await streams.aclose()
                await sock.close()

            client = AcpClient(
                on_event=on_acp_event,
                on_permission=permission_callback,
            )
            if permission_callback:
                client.auto_approve = False
            try:
                await asyncio.wait_for(
                    client.start_streams(
                        streams.reader, streams.writer,
                        child_pid=handle.child_pid, closer=_closer,
                    ),
                    timeout=self._timeouts.session_start,
                )
                session_cwd = target.cwd or _default_cwd(target)
                acp_sid = await asyncio.wait_for(
                    client.new_session(cwd=session_cwd),
                    timeout=self._timeouts.session_start,
                )
            except (TimeoutError, asyncio.TimeoutError) as exc:
                raise ConnectError(
                    ConnectStage.LAUNCH_ACP,
                    f"Copilot ACP launch (session host) timed out after "
                    f"{self._timeouts.session_start}s",
                    retryable=False,
                    cause=exc,
                ) from exc

        if self._host_index is not None:
            self._host_index.register(HostRecord(
                session_id=session_id,
                port=handle.port,
                host_pid=handle.host_pid,
                child_pid=handle.child_pid,
                host_version=__version__,
                protocol_version=handle.protocol_version,
                state_file=handle.state_file,
                created_at=time.time(),
            ))
        return client, acp_sid

    async def reattach_session_hosts(self) -> int:
        """Reconnect to every surviving Session Host on startup (goal 3).

        After an agent-bridge restart, ``_rehydrate`` has marked host-backed
        sessions STOPPED. This reads the durable host index and, for each host
        whose process is still alive, re-establishes the ACP connection over the
        reattached loopback endpoint and **adopts** the existing ACP session --
        no child respawn, no lost session. Dead hosts are pruned. Returns the
        count reattached. No-op unless ``session_host_enabled``.

        **Version-mux (Phase 4).** A host advertising a wire-envelope protocol
        this frontend no longer speaks (a rare breaking host-layer change) is
        *not* driven with incompatible client code. Per :func:`plan_host`:
        a compatible host is reattached; an incompatible host whose child is
        still alive is **left running** so it keeps its child until the child's
        own stop (goal 1 -- never reap mid-turn); an incompatible host whose
        child has already stopped is reaped so it stops pinning its old install.
        """
        if not self._session_host_enabled or self._host_index is None:
            return 0
        from .session_host.acp_adapter import open_acp_streams
        from .session_host.client import SessionHostClient
        from .session_host.osutil import kill_pid, pid_alive
        from .session_host.version_mux import HostDisposition, plan_host

        self._host_index.prune_dead(pid_alive)
        reattached = 0
        now = time.time()
        for rec in self._host_index.live_records(pid_alive):
            plan = plan_host(
                protocol_version=rec.protocol_version,
                child_alive=pid_alive(rec.child_pid),
                age_seconds=(now - rec.created_at) if rec.created_at else None,
                # The opt-in sprawl age bound is wired by a Phase-4 follow-up;
                # for now an immortal incompatible session strands (never
                # reaped mid-turn) rather than being force-reaped.
                stale_reap_seconds=None,
            )
            if plan.disposition is HostDisposition.STRAND:
                log.info(
                    "Session %s pinned to incompatible Session Host "
                    "(proto=%s, build=%s, pid=%s); %s",
                    rec.session_id, rec.protocol_version, rec.host_version,
                    rec.host_pid, plan.reason,
                )
                continue
            if plan.disposition in (HostDisposition.REAP_STOPPED,
                                    HostDisposition.FORCE_REAP):
                log.info(
                    "Reaping stranded Session Host for session %s (pid=%s): %s",
                    rec.session_id, rec.host_pid, plan.reason,
                )
                kill_pid(rec.host_pid)
                with contextlib.suppress(Exception):
                    self._host_index.remove(rec.session_id)
                continue
            session = self._sessions.get(rec.session_id)
            if session is None or not session.acp_session_id:
                continue

            def _on_acp_event(event_type: str, data: dict[str, Any],
                              _session: Session = session) -> None:
                if _session.event_log:
                    _session.event_log.append(event_type, data)
                self._capture_progress(_session, event_type, data)
                if event_type == "usage_update":
                    self._handle_usage_update(_session, data)

            try:
                sock = await SessionHostClient.connect(port=rec.port)
                await sock.attach(0)
                streams = await open_acp_streams(sock)

                async def _closer(_streams: Any = streams, _sock: Any = sock) -> None:
                    await _streams.aclose()
                    await _sock.close()

                client = AcpClient(on_event=_on_acp_event)
                await asyncio.wait_for(
                    client.start_streams(
                        streams.reader, streams.writer,
                        child_pid=rec.child_pid, closer=_closer,
                    ),
                    timeout=self._timeouts.session_start,
                )
                client.adopt_session(session.acp_session_id)
                session.client = client
                session.status = SessionStatus.IDLE
                self._db.update_session_status(
                    rec.session_id, SessionStatus.IDLE.value, time.time(),
                    pid=session.pid,
                )
                reattached += 1
                log.info(
                    "Reattached session %s to live Session Host (pid=%s, port=%s)",
                    rec.session_id, rec.host_pid, rec.port,
                )
            except Exception:
                log.warning(
                    "Failed to reattach session %s to host pid=%s; pruning",
                    rec.session_id, rec.host_pid, exc_info=True,
                )
                with contextlib.suppress(Exception):
                    self._host_index.remove(rec.session_id)
        if reattached:
            log.info("Reattached %d session(s) to surviving Session Hosts", reattached)
        return reattached

    def stranded_host_records(self) -> list[Any]:
        """Live Session Hosts this frontend can no longer speak to (version-mux).

        Returns the ``HostRecord``s for hosts whose process is alive but whose
        wire-envelope protocol is not one this build supports -- i.e. old-version
        hosts still keeping their children until each stops. Useful for
        observability and for a deploy layer to know which old on-disk installs
        are still pinned. Empty (the common case) unless a breaking host-layer
        change has left older hosts running.
        """
        if not self._session_host_enabled or self._host_index is None:
            return []
        from .session_host.osutil import pid_alive
        from .session_host.version_mux import is_compatible

        return [
            rec for rec in self._host_index.live_records(pid_alive)
            if not is_compatible(rec.protocol_version)
        ]

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
        if self._draining:
            raise DaemonDrainingError("session")
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
            self._capture_progress(session, event_type, data)
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

        tracker = ConnectTracker(session.event_log.append, session_id=session_id)
        # Stage 3 (SSH connect) is patient for codespace boot, else the
        # general ssh_connect budget.
        connect_timeout = (
            self._timeouts.codespace_boot
            if target.type == "command" or target.spawn_command
            else self._timeouts.ssh_connect
        )

        try:
            if self._session_host_enabled and target.type == "local":
                # Session-Host mode: the child lives in a survivable host that
                # outlives this frontend (goal 1/3). resolve->launch host->
                # reattach over loopback->drive ACP.
                client, acp_sid = await self._connect_via_session_host(
                    target,
                    tracker=tracker,
                    session_id=session_id,
                    on_acp_event=on_acp_event,
                    permission_callback=permission_callback,
                )
            else:
                # Spawn the subprocess (local/SSH/command). Emits per-stage
                # checkpoints (auth-env, ssh-connect, worktree) into the event log.
                agent_proc = await spawn(
                    target,
                    tracker=tracker,
                    connect_timeout=connect_timeout,
                    session_id=session_id,
                )

                # Stage 7: launch + initialize Copilot in ACP mode. Should be
                # fast; bound it so a hung launch fails fast.
                with tracker.stage(ConnectStage.LAUNCH_ACP):
                    client = AcpClient(
                        on_event=on_acp_event,
                        on_permission=permission_callback,
                    )
                    if permission_callback:
                        client.auto_approve = False
                    try:
                        await asyncio.wait_for(
                            client.start(agent_proc.proc),
                            timeout=self._timeouts.session_start,
                        )
                        # Create ACP session -- binstub agents resolve CWD
                        # remotely, so target.cwd may be None.  The ACP spec
                        # requires an absolute path.  Derive a home-dir default.
                        session_cwd = target.cwd or _default_cwd(target)
                        acp_sid = await asyncio.wait_for(
                            client.new_session(cwd=session_cwd),
                            timeout=self._timeouts.session_start,
                        )
                    except (TimeoutError, asyncio.TimeoutError) as exc:
                        raise ConnectError(
                            ConnectStage.LAUNCH_ACP,
                            f"Copilot ACP launch timed out after "
                            f"{self._timeouts.session_start}s",
                            retryable=False,
                            cause=exc,
                        ) from exc

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
        except ConnectError as exc:
            # Structured failure: we know exactly which stage failed and
            # whether a retry could help -- never an opaque "agent died".
            session.status = SessionStatus.FAILED
            self._db.update_session_status(
                session_id, SessionStatus.FAILED.value, time.time()
            )
            session.event_log.append("connect_failed", {
                "stage": int(exc.stage),
                "stage_name": exc.stage.name,
                "retryable": exc.retryable,
                "message": exc.detail,
            })
            session.event_log.append("error", {"message": str(exc)})
            log.error(
                "Session %s failed at stage %d/%s: %s",
                session_id, int(exc.stage), exc.stage.name, exc.detail,
                exc_info=True,
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
                self._capture_progress(session, event_type, data)
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

    async def resync_session(self, session_id: str) -> int:
        """Rebuild a session's event log from the agent's authoritative replay.

        Reattaches to the persisted ACP session and captures the full
        conversation history the agent streams back during load (per the ACP
        spec), then replaces the event log with it. This heals logs that were
        truncated by a mid-session disconnect (e.g. an oversized ACP frame
        that crashed the read loop): the agent always holds the complete
        history, so its replay is the source of truth.

        Idempotent: resyncing an already-complete session rebuilds the same
        log. Leaves the session IDLE with a live client, ready for prompts.
        Returns the number of events in the rebuilt log.
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        if not session.acp_session_id:
            raise RuntimeError(
                f"Session {session_id} has no ACP session ID -- cannot resync"
            )

        async with session._lifecycle_lock:
            if session.status == SessionStatus.RUNNING:
                raise ValueError(
                    f"Session {session_id} is running a turn -- cannot resync"
                )

            # Tear down any live client so we can reattach cleanly.
            if session.client:
                with contextlib.suppress(Exception):
                    await session.client.shutdown()
                session.client = None

            session.status = SessionStatus.STARTING
            self._db.update_session_status(
                session_id, SessionStatus.STARTING.value, time.time()
            )

            captured: list[tuple[str, dict[str, Any]]] = []

            def on_capture(event_type: str, data: dict[str, Any]) -> None:
                captured.append((event_type, data))
                if event_type == "usage_update":
                    self._handle_usage_update(session, data)

            client: AcpClient | None = None
            try:
                agent_proc = await spawn(session.target)
                client = AcpClient(on_event=on_capture)
                await client.start(agent_proc.proc)
                # suppress_replay=False -> the replayed history is captured.
                await client.load_session(
                    cwd=session.target.cwd or _default_cwd(session.target),
                    session_id=session.acp_session_id,
                    suppress_replay=False,
                )

                count = 0
                if session.event_log:
                    count = session.event_log.rebuild(captured)
                    session.event_log.append("session_state_changed", {
                        "status": SessionStatus.IDLE.value,
                        "resynced": True,
                        "acp_session_id": session.acp_session_id,
                    })

                session.client = client
                session.status = SessionStatus.IDLE
                self._db.update_session_status(
                    session_id, SessionStatus.IDLE.value, time.time(),
                    pid=session.pid,
                )
                log.info(
                    "Session %s (%s) resynced: rebuilt %d events",
                    session_id, session.name, count,
                )
            except Exception as exc:
                if client:
                    with contextlib.suppress(Exception):
                        await client.shutdown()
                session.client = None
                session.status = SessionStatus.STOPPED
                self._db.update_session_status(
                    session_id, SessionStatus.STOPPED.value, time.time()
                )
                log.error("Failed to resync session %s: %s", session_id, exc)
                raise

        session.touch()
        return count

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
        if self._draining:
            raise DaemonDrainingError("turn")
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
            # Persist the user's prompt as a durable, replayable event -- not
            # just a row in the turns table -- so every consumer (other tabs,
            # other relay instances, and history replayed on resume/open) sees
            # the prompt bubble, not only the agent's response. This mirrors
            # what the agent's load-time replay emits during a resync, keeping
            # live and replayed histories consistent.
            session.event_log.append("user_message", {"content": prompt})
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

    async def _quiesce_session(self, session: Session) -> None:
        """Best-effort teardown of a session's in-flight prompt + ACP client.

        Must be resilient to a *mid-turn* session: cancelling an in-flight
        prompt or shutting down a busy ACP client must never raise out of
        stop/end. (A raising shutdown here surfaced as HTTP 500 when ending a
        mid-turn session -- see the credential-hang showcase report.) Errors
        are logged and swallowed so teardown always completes.
        """
        task = session._prompt_task
        if task and not task.done():
            if session.client:
                with contextlib.suppress(Exception):
                    await session.client.cancel_prompt()
            task.cancel()
            with contextlib.suppress(BaseException):
                await task
        if session.client:
            try:
                await session.client.shutdown()
            except Exception:
                log.warning(
                    "ACP client shutdown failed while tearing down session %s",
                    session.session_id, exc_info=True,
                )
            session.client = None
        # Clean up unused worktrees (0-turn sessions from crash-loops)
        try:
            await _cleanup_worktree(session.target, session.turn_count)
        except Exception:
            log.warning(
                "worktree cleanup failed while tearing down session %s",
                session.session_id, exc_info=True,
            )

    async def stop_session(self, session_id: str, *, force: bool = False) -> None:
        """Stop a session -- shut down ACP client, preserve state for resume.

        Refuses with SessionBusyError when the session is hosting active
        background sub-agents unless ``force`` is set, so a routine stop does
        not kill in-flight background work (e.g. the PR daemon).

        Teardown is **never gated by the drain flag** (#1755): stopping a
        session is exactly what lets the busy sessions ``drain()`` waits on
        settle, so gating it here would self-deadlock a redeploy.
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        if not force and session.has_active_background_tasks:
            raise SessionBusyError(session_id, session.active_background_tasks)

        await self._quiesce_session(session)

        session.status = SessionStatus.STOPPED
        now = time.time()
        self._db.update_session_status(session_id, SessionStatus.STOPPED.value, now)
        if session.event_log:
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.STOPPED.value,
            })
        session.touch()
        log.info("Session %s (%s) stopped", session_id, session.name)

    async def end_session(self, session_id: str, *, force: bool = False) -> None:
        """End a session -- shut down client and clean up all state.

        Always removes the session (even mid-turn): teardown is best-effort so
        ending never fails with a server error on a busy/hung session (#48).
        Both the persisted-status update and the row delete are suppressed so a
        transient DB error (e.g. a locked SQLite file) can't surface as HTTP
        500. The ENDED status is written *before* the delete so that even if the
        row is not removed, a later restart rehydrate cleans it up rather than
        resurrecting the session as STOPPED/active.

        Refuses with SessionBusyError when the session is hosting active
        background sub-agents unless ``force`` is set -- ending kills the
        process and every in-process sub-agent with it.

        Teardown is **never gated by the drain flag** (#1755): ending a session
        is exactly what lets the busy sessions ``drain()`` waits on settle, so
        gating it would self-deadlock a redeploy (the operator could not clear
        the very sessions blocking the drain).
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        if not force and session.has_active_background_tasks:
            raise SessionBusyError(session_id, session.active_background_tasks)

        await self._quiesce_session(session)

        session.status = SessionStatus.ENDED
        with contextlib.suppress(Exception):
            self._db.update_session_status(
                session_id, SessionStatus.ENDED.value, time.time()
            )
        with contextlib.suppress(Exception):
            self._db.delete_session(session_id)
        self._sessions.pop(session_id, None)
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
