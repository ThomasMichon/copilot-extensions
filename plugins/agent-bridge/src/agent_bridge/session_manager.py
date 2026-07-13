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
from dataclasses import replace
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


# Liveness (#145): a RUNNING session whose ACP event stream has produced no
# frame for this long -- while its transport is still alive -- is treated as a
# silent mid-turn *stall* (distinct from a healthy long reasoning step). Chosen
# conservatively so a normal multi-second "thinking" step never trips it.
_STALL_AFTER_S = 180.0


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
        # Liveness tracking (#145). ``last_output_at`` advances on EVERY ACP
        # frame -- unlike ``updated_at``, which only moves at turn boundaries, so
        # a healthy long turn is otherwise indistinguishable from a wedge.
        # ``last_heartbeat_at`` is a periodic transport-liveness beat. Together
        # they separate a *stalled* agent (output stale, channel alive) from a
        # *dead* channel (heartbeat stale). In-memory only; live sessions only.
        self.last_output_at: float | None = None
        self.last_heartbeat_at: float | None = None
        # Count of active event subscribers (SSE streams / attached fronts).
        # Drives the idle reaper (#1826): a session with zero subscribers is
        # "unwatched" and eligible for idle reclamation. In-memory only.
        self.subscriber_count = 0
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

    def note_heartbeat(self, now: float | None = None) -> None:
        """Record that the transport was confirmed alive (periodic beat)."""
        self.last_heartbeat_at = now if now is not None else time.time()

    def liveness_state(
        self, now: float | None = None, stall_after_s: float = _STALL_AFTER_S,
    ) -> str | None:
        """Derive a liveness signal for a RUNNING session, else ``None``.

        Uses output-flow vs transport-liveness -- which the turn-boundary
        ``updated_at`` cannot (#145):

        - ``active``       -- an ACP frame flowed within ``stall_after_s``.
        - ``stalled``      -- transport alive (client running) but no ACP frame
                              for ``stall_after_s`` (silent mid-turn stall).
        - ``disconnected`` -- transport is gone (client not running).

        Returns ``None`` for non-RUNNING sessions (liveness is about an
        in-flight turn; idle/stopped/ended have nothing to stall).
        """
        if self.status != SessionStatus.RUNNING:
            return None
        now = now if now is not None else time.time()
        if not (self.client and self.client.is_running):
            return "disconnected"
        if self.last_output_at is None:
            return "active"
        if now - self.last_output_at > stall_after_s:
            return "stalled"
        return "active"


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
        session_host_stale_reap_seconds: float = 0.0,
        graceful_cancel_settle_seconds: float = 45.0,
        idle_reap_ttl_seconds: float = 0.0,
        live_stall_interrupt_after_s: float = 900.0,
    ) -> None:
        self._db = db
        self._sessions: dict[str, Session] = {}
        # Session-Host mode (experimental, default off): local children live in
        # a survivable Session Host that outlives a frontend restart. The host
        # index is the durable session_id -> host-endpoint map used to reattach.
        self._session_host_enabled = session_host_enabled
        self._session_host_stale_reap_seconds = session_host_stale_reap_seconds
        self._graceful_cancel_settle_seconds = graceful_cancel_settle_seconds
        # Idle-session reaper TTL (#1826): stop an idle, unwatched session past
        # this many seconds to free its Copilot child (resumable via replay).
        # 0 disables. Only acts in Session-Host mode.
        self._idle_reap_ttl_seconds = idle_reap_ttl_seconds
        # Live-stall interrupt threshold (#2427, Phase 5): the watchdog
        # interrupts a RUNNING session that is liveness 'stalled' AND still has a
        # live _prompt_task once its silence exceeds this many seconds. Distinct
        # from (and much larger than) the 180s stall so a legitimately long tool
        # call is not aborted. 0 disables the live-stall interrupt entirely.
        self._live_stall_interrupt_after_s = live_stall_interrupt_after_s
        self._host_index: Any = None
        # Live remote-boundary forwards (session_id -> LocalForward). Held so a
        # CodeSpace/mesh Session Host's -L/-R forward can be refreshed on
        # reattach and torn down on teardown. Empty for local hosts.
        self._forwards: dict[str, Any] = {}
        # Strong refs to in-flight best-effort remote-reap tasks (so they are not
        # GC'd mid-flight); each removes itself on completion.
        self._remote_reap_tasks: set[Any] = set()
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

    @property
    def session_host_enabled(self) -> bool:
        """True when sessions run inside survivable Session Hosts (goal 1/3).

        Gates the liveness-driven reattach driver (``recover_disconnected_hosts``)
        that the app's heartbeat loop calls each beat.
        """
        return self._session_host_enabled

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

    async def graceful_cancel_for_redeploy(
        self,
        *,
        settle_timeout: float | None = None,
        exclude_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Assertively-but-nicely cancel in-flight turns ahead of a redeploy.

        The Session-Host model keeps the child *alive* across a frontend
        restart; this decides what to do with an in-flight **turn**. Rather than
        leave it streaming blind (slow, fragile) or hard-kill it, we:

        1. inject an ACP ``session/cancel`` into every session with an in-flight
           turn (status RUNNING) -- *except* an optional ``exclude_session_id``
           (the session that triggered the redeploy, e.g. an agent updating its
           own bridge -- cancelling it would abort the very command doing the
           update);
        2. flag each such host-backed session ``resume_on_reattach`` so the
           restarted frontend sends it a single ``Resume`` once reattached;
        3. wait up to ``settle_timeout`` seconds for the cancelled turns to
           reach their own stop (capturing the final streamed messages), so the
           subsequent stop is clean and fast.

        No-op unless ``session_host_enabled``. Returns a summary.
        """
        import asyncio as _asyncio

        if not self._session_host_enabled:
            return {"cancelled": [], "settled": True, "enabled": False}
        settle = (self._graceful_cancel_settle_seconds
                  if settle_timeout is None else settle_timeout)
        # Only in-flight turns (goal: "only in-flight turns"); background-busy
        # sessions are left alone.
        targets = [
            sid for sid, s in self._sessions.items()
            if s.status == SessionStatus.RUNNING and sid != exclude_session_id
        ]
        cancelled: list[str] = []
        for sid in targets:
            session = self._sessions.get(sid)
            if session is None or session.client is None:
                continue
            with contextlib.suppress(Exception):
                await session.client.cancel_prompt()
            if self._host_index is not None:
                with contextlib.suppress(Exception):
                    self._host_index.set_resume_flag(sid, True)
            cancelled.append(sid)
        if cancelled:
            log.info(
                "Graceful-cancel: sent ACP cancel to %d in-flight turn(s); "
                "waiting up to %.0fs to settle: %s",
                len(cancelled), settle, ", ".join(cancelled),
            )
        deadline = time.monotonic() + max(0.0, settle)
        still = [s for s in cancelled
                 if (self._sessions.get(s) is not None
                     and self._sessions[s].status == SessionStatus.RUNNING)]
        while still and time.monotonic() < deadline:
            await _asyncio.sleep(0.5)
            still = [s for s in cancelled
                     if (self._sessions.get(s) is not None
                         and self._sessions[s].status == SessionStatus.RUNNING)]
        if still:
            log.warning(
                "Graceful-cancel: %d turn(s) did not settle within %.0fs "
                "(proceeding anyway): %s", len(still), settle, ", ".join(still),
            )
        return {"cancelled": cancelled, "settled": not still, "enabled": True}

    async def drain(
        self,
        *,
        timeout: float = 300.0,
        poll: float = 1.0,
        force: bool = False,
        reason: str | None = None,
        source: str = "drain-endpoint",
        exclude_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Open the drain gate and wait for in-flight work to settle.

        Refuses new sessions/turns immediately, then blocks until no session is
        busy (see busy_sessions) or ``timeout`` seconds elapse. The OS service
        manager (systemd ExecStop / the Windows pre-stop hook) and the cutover
        orchestrator call this *before* the process exits so an active turn is
        never hard-killed. Returns a summary; ``drained`` is False on timeout
        unless ``force`` is set (the caller accepts interrupting the laggards).

        In **Session-Host mode** the drain is *assertive*: it first
        graceful-cancels in-flight turns (ACP ``session/cancel`` + a
        ``resume_on_reattach`` flag), bounded by ``graceful_cancel_settle_seconds``,
        so a redeploy never blocks the full ``timeout`` on a long turn and a
        session updating its own bridge (``exclude_session_id``) is spared.

        ``source``/``reason`` are recorded for observability (#1757). Note the
        gate stays open after this returns (the successor retires this daemon);
        the watchdog armed here auto-releases it if that handoff never lands.
        Teardown (stop/end) stays permitted throughout -- it is what lets the
        busy sessions this loop waits on settle (#1755).
        """
        import asyncio as _asyncio

        self.set_draining(True, reason=reason, source=source)
        if self._session_host_enabled:
            await self.graceful_cancel_for_redeploy(
                exclude_session_id=exclude_session_id,
            )
        deadline = time.monotonic() + max(0.0, timeout)
        busy = [s for s in self.busy_sessions() if s != exclude_session_id]
        log.info(
            "Drain started: %d session(s) busy, timeout=%.0fs%s",
            len(busy), timeout, " (force)" if force else "",
        )
        while busy and time.monotonic() < deadline:
            await _asyncio.sleep(poll)
            busy = [s for s in self.busy_sessions() if s != exclude_session_id]

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
        # Every ACP frame is fresh output -- stamp it so liveness reflects the
        # real event stream, not just turn boundaries (#145).
        session.last_output_at = time.time()
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
        mcp_servers: list[dict[str, Any]] | None = None,
        spawner: Any | None = None,
        remote_child_argv: list[str] | None = None,
        remote_cwd: str | None = None,
    ) -> tuple[AcpClient, str]:
        """Spawn a child inside a survivable Session Host and drive ACP over the
        reattachable loopback endpoint (Session-Host mode).

        ``spawner`` selects the boundary seam (default :class:`LocalSpawner`; a
        :class:`CodeSpaceSpawner` bootstraps the Host inside a CodeSpace and
        stands up the ``-L`` forward). Registers the durable host index -- with
        the remote-boundary ``endpoint`` descriptor -- so a restarted frontend
        can re-forward and reattach. Teardown DETACHES (host-mode
        ``AcpClient.shutdown``), never reaping the child inadvertently -- goal 1.
        """
        from . import __version__
        from .session_host.acp_adapter import open_acp_streams
        from .session_host.client import SessionHostClient
        from .session_host.host_index import HostRecord
        from .session_host.spawner import LocalSpawner
        from .transport import resolve_local_launch

        if spawner is None:
            spawner = LocalSpawner()

        if remote_child_argv is not None:
            # Remote boundary (CodeSpace/mesh): the child runs on the FAR side,
            # so there is no local worktree to resolve -- the Spawner is handed
            # the remote copilot argv + remote cwd directly, and the far-side
            # Session Host owns copilot's stdio as a clean local pipe there.
            args, work_dir, env = remote_child_argv, remote_cwd, {}
        else:
            args, work_dir, env = await resolve_local_launch(
                target, tracker=tracker, session_id=session_id,
            )
            if work_dir and not target.cwd:
                target.cwd = work_dir

        with tracker.stage(ConnectStage.LAUNCH_ACP):
            # Tag the child's environment with its own bridge session id so a
            # command the agent runs (e.g. an in-session `aperture-labs services
            # agent-bridge update`) can tell the drain to spare THIS session --
            # cancelling the turn running the update would abort the update
            # (#1790). Any descendant process inherits it.
            child_env = dict(env or {})
            child_env["AGENT_BRIDGE_SESSION_ID"] = session_id
            # Bootstrap the Session Host through the boundary Spawner seam (P2a).
            # The seam is boundary-agnostic: LocalSpawner binds a loopback port
            # directly; CodeSpaceSpawner ships+launches the Host on the CS and
            # stands up an -L forward so the frontend below still dials
            # 127.0.0.1:<local_port>. spawn() blocks briefly on host readiness,
            # so it is already off-loop.
            spawned = await spawner.spawn(
                args, cwd=work_dir, env=child_env, session_id=session_id,
            )
            # Retain a remote-boundary forward so reattach can refresh it and
            # teardown can cancel it.
            if getattr(spawned, "forward", None) is not None:
                self._forwards[session_id] = spawned.forward
            sock = await SessionHostClient.connect(port=spawned.local_port)
            await sock.attach(0, nonce=spawned.nonce.encode())
            streams = await open_acp_streams(sock)

            async def _closer() -> None:
                await streams.aclose()
                await sock.close()

            client = AcpClient(
                on_event=on_acp_event,
                on_permission=permission_callback,
            )
            # Surface a mid-session transport drop (loopback socket down, host +
            # child alive) as ``disconnected`` so the reattach driver fires (P1).
            streams.on_transport_lost = client.mark_transport_lost
            if permission_callback:
                client.auto_approve = False
            try:
                await asyncio.wait_for(
                    client.start_streams(
                        streams.reader, streams.writer,
                        child_pid=spawned.child_pid, closer=_closer,
                    ),
                    timeout=self._timeouts.session_start,
                )
                session_cwd = target.cwd or _default_cwd(target)
                acp_sid = await asyncio.wait_for(
                    client.new_session(cwd=session_cwd, mcp_servers=mcp_servers),
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
                port=spawned.local_port,
                host_pid=spawned.host_pid,
                child_pid=spawned.child_pid,
                host_version=__version__,
                protocol_version=spawned.protocol_version,
                state_file=spawned.state_file,
                created_at=time.time(),
                nonce=spawned.nonce,
                boundary=spawned.boundary,
                endpoint=getattr(spawned, "endpoint", {}) or {},
            ))
        return client, acp_sid

    # -- boundary-aware Session Host liveness ---------------------------------
    def _rec_host_alive(self, rec: Any) -> bool:
        """Is a Session Host still alive? Boundary-aware.

        A **local** host is a local process, so ``pid_alive`` is authoritative.
        A **remote** host's ``host_pid`` is a *far-side* pid -- checking it
        against local processes is meaningless (and would randomly match an
        unrelated local pid). A remote host is instead treated as *presumed
        alive* here and **verified** by the actual forward + ATTACH probe in
        ``_reattach_one`` (which prunes on failure), so a truly-dead remote host
        is dropped when the reattach fails rather than by a bogus local pid check.
        """
        from .session_host.osutil import pid_alive
        if getattr(rec, "boundary", "local") == "local":
            return pid_alive(rec.host_pid)
        return True

    def _rec_child_alive(self, rec: Any) -> bool:
        """Is the copilot child alive? Local: ``pid_alive``; remote: presumed
        (a dead remote child surfaces as the host closing on ATTACH)."""
        from .session_host.osutil import pid_alive
        if getattr(rec, "boundary", "local") == "local":
            return pid_alive(rec.child_pid)
        return True

    def _live_host_records(self) -> list[Any]:
        """Records whose host is (boundary-appropriately) alive."""
        if self._host_index is None:
            return []
        return [r for r in self._host_index.all() if self._rec_host_alive(r)]

    def _prune_dead_hosts(self) -> None:
        """Drop records whose host is dead (local only -- remote is verified by
        the reattach probe, never by a local pid check)."""
        if self._host_index is None:
            return
        for r in [r for r in self._host_index.all() if not self._rec_host_alive(r)]:
            with contextlib.suppress(Exception):
                self._host_index.remove(r.session_id)

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
        from .session_host.version_mux import HostDisposition, plan_host

        self._prune_dead_hosts()
        reattached = 0
        now = time.time()
        for rec in self._live_host_records():
            plan = plan_host(
                protocol_version=rec.protocol_version,
                child_alive=self._rec_child_alive(rec),
                age_seconds=(now - rec.created_at) if rec.created_at else None,
                stale_reap_seconds=self._session_host_stale_reap_seconds,
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
                self._reap_host_record(rec, plan.reason)
                continue
            session = self._sessions.get(rec.session_id)
            if session is None or not session.acp_session_id:
                # A live host with no adoptable session -- ended out from under
                # it (its row was deleted) or a pre-#1786 orphan. Reap it rather
                # than leak the host + child forever.
                self._reap_host_record(rec, "no adoptable session on reattach")
                continue

            if await self._reattach_one(
                rec, session, new_status=SessionStatus.IDLE,
                send_resume=getattr(rec, "resume_on_reattach", False),
                prune_on_fail=True,
            ):
                reattached += 1
        if reattached:
            log.info("Reattached %d session(s) to surviving Session Hosts", reattached)
        return reattached

    async def _ensure_forward(self, rec: Any) -> None:
        """Ensure a remote-boundary Host's ``-L`` (+ ``-R`` relay) forward is up.

        No-op for a local Host (direct loopback, no forward). For a CodeSpace /
        mesh Host, (re-)establishes the forward so ``rec.port`` resolves before we
        dial it -- the ``refresh_endpoint()`` step of the reattach driver, driven
        from the durable ``rec.endpoint`` descriptor so it works even after a
        frontend restart with no live Spawner. Refreshes an existing forward
        (cancel + re-establish) or rebuilds one from the endpoint.
        """
        boundary = getattr(rec, "boundary", "local")
        endpoint = getattr(rec, "endpoint", None) or {}
        if boundary == "local" or not endpoint:
            return
        from .session_host.endpoints import forward_from_endpoint

        existing = self._forwards.get(rec.session_id)
        try:
            if existing is not None:
                await existing.refresh()
            else:
                fwd = forward_from_endpoint(endpoint)
                await fwd.establish()
                self._forwards[rec.session_id] = fwd
        except Exception:
            log.warning(
                "Failed to (re-)establish forward for session %s (boundary=%s)",
                rec.session_id, boundary, exc_info=True,
            )
            raise

    async def _drop_forward(self, session_id: str) -> None:
        """Cancel and forget a session's remote-boundary forward (if any)."""
        fwd = self._forwards.pop(session_id, None)
        if fwd is not None:
            with contextlib.suppress(Exception):
                await fwd.cancel()

    async def _reattach_one(
        self,
        rec: Any,
        session: Session,
        *,
        new_status: SessionStatus,
        send_resume: bool = False,
        prune_on_fail: bool = False,
    ) -> bool:
        """(Re)connect to a live Session Host and adopt its session -- the shared
        core of both startup reattach and in-session liveness-driven recovery.

        Dials the host's endpoint, resumes by the host-retained seq cursor
        (buffered frames past the durable ack replay with no gap and no
        re-stream), re-initializes ACP over the fresh stream pair, and adopts the
        existing ACP session id -- no child respawn. Wires transport-loss
        detection so a *subsequent* drop re-arms the driver. Sets
        ``session.client`` and ``session.status = new_status``; returns True on
        success.

        ``send_resume`` nudges a graceful-cancelled turn back to work with a
        single "Resume". ``prune_on_fail`` drops the index record on failure
        (startup path); the in-session driver leaves it for a later retry.
        """
        from .session_host.acp_adapter import open_acp_streams
        from .session_host.client import SessionHostClient

        def _on_acp_event(event_type: str, data: dict[str, Any]) -> None:
            if session.event_log:
                session.event_log.append(event_type, data)
            self._capture_progress(session, event_type, data)
            if event_type == "usage_update":
                self._handle_usage_update(session, data)

        # Release any stale (dead-transport) client first so its socketpair +
        # relay tasks are freed; host-mode shutdown DETACHES (child survives).
        old = session.client
        if old is not None:
            with contextlib.suppress(Exception):
                await old.shutdown()

        try:
            await self._ensure_forward(rec)
            sock = await SessionHostClient.connect(port=rec.port)
            await sock.attach(0, nonce=getattr(rec, "nonce", "").encode())
            streams = await open_acp_streams(sock)

            async def _closer(_streams: Any = streams, _sock: Any = sock) -> None:
                await _streams.aclose()
                await _sock.close()

            client = AcpClient(on_event=_on_acp_event)
            streams.on_transport_lost = client.mark_transport_lost
            await asyncio.wait_for(
                client.start_streams(
                    streams.reader, streams.writer,
                    child_pid=rec.child_pid, closer=_closer,
                ),
                timeout=self._timeouts.session_start,
            )
            client.adopt_session(session.acp_session_id)
            session.client = client
            session.status = new_status
            self._db.update_session_status(
                rec.session_id, new_status.value, time.time(), pid=session.pid,
            )
            log.info(
                "Reattached session %s to live Session Host (pid=%s, port=%s)",
                rec.session_id, rec.host_pid, rec.port,
            )
        except Exception:
            log.warning(
                "Failed to reattach session %s to host pid=%s%s",
                rec.session_id, rec.host_pid,
                "; pruning" if prune_on_fail else "",
                exc_info=True,
            )
            if prune_on_fail and self._host_index is not None:
                with contextlib.suppress(Exception):
                    self._host_index.remove(rec.session_id)
            return False

        # If this session's in-flight turn was graceful-cancelled for a redeploy,
        # nudge it back to work with a single "Resume" now that the frontend is
        # reattached (a bare "Resume" re-orients a Copilot session well).
        if send_resume and self._host_index is not None:
            self._host_index.set_resume_flag(rec.session_id, False)
            try:
                await self.submit_prompt(rec.session_id, "Resume")
                log.info(
                    "Sent 'Resume' to reattached session %s "
                    "(turn was graceful-cancelled for redeploy)",
                    rec.session_id,
                )
            except Exception:
                log.warning(
                    "Failed to send 'Resume' to reattached session %s",
                    rec.session_id, exc_info=True,
                )
        return True

    async def recover_disconnected_hosts(self) -> int:
        """In-session liveness-driven reattach for host-backed sessions (P1).

        The P0 heartbeat only *stamps* liveness; this is the *actuator*. For each
        host-backed session whose transport to its (still-alive) Session Host has
        dropped -- ``liveness_state() == 'disconnected'`` on a RUNNING turn, or a
        non-RUNNING session whose host-mode client is no longer running -- while
        the host + child processes survive, redial the host and resume by cursor
        (no restart, no lost turn). A merely ``stalled`` session (channel up,
        agent silent) is surfaced but not reattached -- reconnecting cannot
        un-wedge a silent agent. Returns the count reattached. No-op unless
        ``session_host_enabled``.
        """
        if not self._session_host_enabled or self._host_index is None:
            return 0
        from .session_host.version_mux import HostDisposition, plan_host

        recovered = 0
        now = time.time()
        for rec in list(self._live_host_records()):
            session = self._sessions.get(rec.session_id)
            if session is None or not session.acp_session_id:
                continue
            client = session.client
            # A live, running client needs nothing; surface a stall and move on.
            if client is not None and client.is_running:
                if session.liveness_state(now) == "stalled":
                    log.warning(
                        "Session %s stalled (channel up, no output) -- "
                        "surfaced, not reattached", rec.session_id,
                    )
                continue
            # Transport is down. Only resume if the child is still there; a dead
            # child is a real end, left to normal teardown/GC.
            if not self._rec_child_alive(rec):
                continue
            # Respect version-mux: never drive a host this build can't speak to.
            plan = plan_host(
                protocol_version=rec.protocol_version,
                child_alive=True,
                age_seconds=(now - rec.created_at) if rec.created_at else None,
                stale_reap_seconds=self._session_host_stale_reap_seconds,
            )
            if plan.disposition is not HostDisposition.REATTACH:
                continue
            # Preserve a RUNNING turn's status so its replayed buffered frames
            # keep flowing; otherwise land it IDLE and drivable.
            keep = (SessionStatus.RUNNING
                    if session.status == SessionStatus.RUNNING
                    else SessionStatus.IDLE)
            if await self._reattach_one(
                rec, session, new_status=keep,
                send_resume=getattr(rec, "resume_on_reattach", False),
            ):
                recovered += 1
        if recovered:
            log.info("Recovered %d disconnected host-backed session(s)", recovered)
        return recovered

    async def reconcile_wedged_running(self, now: float | None = None) -> int:
        """Heal sessions wedged in RUNNING (issues #22 / #2384 / #2427).

        Eventual-terminal reconciliation across two shapes of wedge:

        1. **No live turn** (#2384): a session persisted as RUNNING whose turn
           can no longer reach a terminal event -- output has stopped
           (``liveness_state`` ``stalled`` or ``disconnected``) and there is **no
           live prompt task** driving it in this daemon -- would otherwise mirror
           "Responding..." forever. Resync it (rebuild from the agent's
           authoritative replay, respawning the child if the transport is gone)
           so it lands IDLE with a terminal ``session_state_changed``.

        2. **Live-stalled turn** (#2427, Phase 5): a session that is liveness
           ``stalled`` (transport up, no ACP frame for ``_STALL_AFTER_S``) but
           **still has a live ``_prompt_task``** -- the child is alive and a
           ``send_prompt`` is awaiting output that has gone silent. Resync cannot
           touch it (a live turn); instead, once its silence exceeds the separate,
           conservative ``live_stall_interrupt_after_s`` threshold, gracefully
           ``interrupt_turn()`` it (ACP session/cancel, #899). The in-flight
           ``send_prompt`` returns/raises, the runner settles the session to IDLE
           with a terminal event, and consumers converge. Never a task-cancel or
           child kill.

        Guards keep a genuinely progressing turn untouched: a session still
        producing output (liveness ``active``) is always skipped; a live turn is
        interrupted only after real silence past the large, operator-tunable
        threshold (0 disables the live-stall interrupt entirely). Best-effort and
        per-session isolated; a single failure never stalls the sweep. Returns the
        count reconciled (resynced + interrupted).
        """
        now = now if now is not None else time.time()
        healed = 0
        for sid, session in list(self._sessions.items()):
            if session.status != SessionStatus.RUNNING:
                continue
            liveness = session.liveness_state(now)
            if liveness not in ("stalled", "disconnected"):
                continue
            task = session._prompt_task
            if task is not None and not task.done():
                # A live turn is being driven here. The only safe action is a
                # graceful interrupt, and only for a *live-stalled* turn (client
                # up, output silent) that has been silent past the separate,
                # conservative live-stall threshold -- never a 'disconnected'
                # transport (cancel needs the client) and never a merely-long
                # turn still producing output. Diagnose-before-remediating: err
                # toward leaving a live turn alone.
                threshold = self._live_stall_interrupt_after_s
                silent_for = (
                    now - session.last_output_at
                    if session.last_output_at is not None else 0.0
                )
                if (liveness == "stalled" and threshold > 0
                        and silent_for > threshold):
                    try:
                        await self.interrupt_turn(sid)
                        healed += 1
                        log.warning(
                            "Interrupted live-stalled RUNNING session %s "
                            "(live turn silent for %.0fs > %.0fs threshold)",
                            sid, silent_for, threshold,
                        )
                    except Exception:
                        log.warning(
                            "Failed to interrupt live-stalled session %s",
                            sid, exc_info=True,
                        )
                continue
            try:
                await self.resync_session(sid)
                healed += 1
                log.warning(
                    "Reconciled wedged RUNNING session %s to idle "
                    "(no live turn, output stopped)", sid,
                )
            except Exception:
                log.warning(
                    "Failed to reconcile wedged session %s", sid, exc_info=True,
                )
        if healed:
            log.info("Reconciled %d wedged RUNNING session(s)", healed)
        return healed

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
        from .session_host.version_mux import is_compatible

        return [
            rec for rec in self._live_host_records()
            if not is_compatible(rec.protocol_version)
        ]

    def _reap_host_record(self, rec: Any, reason: str) -> None:
        """Reap a Session Host + its child and drop the durable index record.

        Kills the **child first** -- a POSIX SIGTERM to the host does not run its
        cleanup, so the child could otherwise orphan -- then the host, then
        removes the record. Cross-platform via ``osutil.kill_pid`` (on Windows
        ``taskkill /T`` collects the process tree, and the host's kill-on-close
        job also takes the child). Used for both the explicit-terminate reap
        (#1786) and the version-mux stranded/forced reap.
        """
        from .session_host.osutil import kill_pid, reap_zombie

        log.info(
            "Reaping Session Host for session %s (host pid=%s, child pid=%s): %s",
            rec.session_id, rec.host_pid, rec.child_pid, reason,
        )
        boundary = getattr(rec, "boundary", "local")
        if boundary == "local":
            kill_pid(rec.child_pid, force=True)
            kill_pid(rec.host_pid, force=True)
            # Clear the zombie a host we parented leaves behind (no-op for a
            # reattached host that init reaps, or on Windows).
            reap_zombie(rec.child_pid)
            reap_zombie(rec.host_pid)
        else:
            # Remote (CodeSpace / mesh) boundary: host_pid/child_pid live on the
            # FAR side -- killing those pid numbers locally would hit unrelated
            # local processes. Tear down the local forward and best-effort kill
            # the remote host (its PR_SET_PDEATHSIG takes the child with it).
            self._kill_forward_sync(rec.session_id)
            self._schedule_remote_reap(rec, reason)
        with contextlib.suppress(Exception):
            self._host_index.remove(rec.session_id)

    def _kill_forward_sync(self, session_id: str) -> None:
        """Best-effort synchronous teardown of a session's forward process."""
        fwd = self._forwards.pop(session_id, None)
        if fwd is None:
            return
        proc = getattr(fwd, "_proc", None)
        if proc is not None and getattr(proc, "returncode", 0) is None:
            with contextlib.suppress(Exception):
                proc.kill()

    def _schedule_remote_reap(self, rec: Any, reason: str) -> None:
        """Fire-and-forget a remote ``kill`` of a detached far-side Host.

        Uses the durable endpoint's SSH config (no live Spawner needed) to run a
        one-shot ``kill`` over the tunnel. Best-effort: if there is no running
        loop or the exec fails, the detached Host lingers until the CodeSpace
        stops -- never fatal, and never touches a local process.
        """
        endpoint = getattr(rec, "endpoint", None) or {}
        if not endpoint:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        task = loop.create_task(self._remote_reap(rec, endpoint))
        self._remote_reap_tasks.add(task)
        task.add_done_callback(self._remote_reap_tasks.discard)

    async def _remote_reap(self, rec: Any, endpoint: dict) -> None:
        from ssh_manager import ConnectionManager

        from .session_host.endpoints import ssh_config_from_endpoint

        class _StaticSource:
            def __init__(self, cfg):
                self._cfg = cfg

            def get_ssh_config(self):
                return self._cfg

            def refresh(self):
                return self._cfg

        cfg = ssh_config_from_endpoint(endpoint)
        host = cfg.host_alias
        try:
            mgr = ConnectionManager()
            await mgr.ensure_connected(host, _StaticSource(cfg), [])
            # Kill the host's whole PROCESS GROUP, not just the host pid. The
            # host was launched via ``setsid`` (so it leads its own group,
            # pgid == host_pid) and the ``bash -lc`` wrapper + the copilot
            # grandchild inherit that group -- so ``kill -- -<pgid>`` takes the
            # host AND copilot in one shot, with nothing orphaned (killing only
            # host_pid would leave copilot reparented to init). Fall back to the
            # bare pid if the group send is rejected. SIGTERM first (lets copilot
            # flush), then SIGKILL as a backstop.
            pid = int(rec.host_pid)
            await mgr.exec_command(
                host,
                f"kill -TERM -{pid} 2>/dev/null || kill -TERM {pid} 2>/dev/null; "
                f"sleep 1; kill -KILL -{pid} 2>/dev/null || kill -KILL {pid} "
                f"2>/dev/null || true",
                timeout=20.0,
            )
            await mgr.disconnect(host)
            log.info("Remote-reaped Session Host group for session %s (far pid=%s)",
                     rec.session_id, rec.host_pid)
        except Exception:
            log.warning(
                "Best-effort remote reap failed for session %s (far pid=%s); "
                "the detached Host will exit when the CodeSpace stops",
                rec.session_id, rec.host_pid, exc_info=True,
            )

    def sweep_stranded_hosts(self) -> int:
        """Reap stranded incompatible Session Hosts that are now reapable.

        A periodic counterpart to the startup-time gate in
        ``reattach_session_hosts``: during a single long frontend lifetime an
        incompatible host's child may finally reach its own stop, or an immortal
        one may outlive the configured ``session_host_stale_reap_seconds`` sprawl
        bound. This re-evaluates every live host and reaps those whose disposition
        is REAP_STOPPED or FORCE_REAP -- never touching a compatible host or a
        stranded host still within the bound. Returns the count reaped. No-op
        unless ``session_host_enabled``.
        """
        if not self._session_host_enabled or self._host_index is None:
            return 0
        from .session_host.version_mux import HostDisposition, plan_host

        self._prune_dead_hosts()
        now = time.time()
        reaped = 0
        for rec in self._live_host_records():
            plan = plan_host(
                protocol_version=rec.protocol_version,
                child_alive=self._rec_child_alive(rec),
                age_seconds=(now - rec.created_at) if rec.created_at else None,
                stale_reap_seconds=self._session_host_stale_reap_seconds,
            )
            if plan.disposition in (HostDisposition.REAP_STOPPED,
                                    HostDisposition.FORCE_REAP):
                self._reap_host_record(rec, plan.reason)
                reaped += 1
        return reaped

    # -- Subscriber tracking + idle reaper (#1826) ----------------------------

    def add_subscriber(self, session_id: str) -> None:
        """Register an active event subscriber (an SSE stream / attached front).

        Increments the session's live-subscriber count so the idle reaper knows
        the session is being watched. Paired with ``remove_subscriber`` in the
        SSE stream's teardown. No-op for an unknown session.
        """
        sid = self._resolve_ref(session_id) or session_id
        s = self._sessions.get(sid)
        if s is not None:
            s.subscriber_count += 1

    def remove_subscriber(self, session_id: str) -> None:
        """Deregister an event subscriber; clamp at zero.

        When the last subscriber leaves, ``touch()`` the session so the
        idle-reaper TTL clock starts from the moment it became unwatched (not
        from the last turn).
        """
        sid = self._resolve_ref(session_id) or session_id
        s = self._sessions.get(sid)
        if s is not None:
            s.subscriber_count = max(0, s.subscriber_count - 1)
            if s.subscriber_count == 0:
                s.touch()

    async def sweep_idle_sessions(self, *, now: float | None = None) -> int:
        """Stop idle, unwatched sessions past the reap TTL (#1826).

        The bridge owns session process lifetime: a session that is IDLE (agent
        at its own stop -- never mid-turn), has ZERO active subscribers, holds no
        active background sub-agents, **has run at least one turn** (so it has a
        persisted ACP conversation a fresh child can ``load_session``), and has
        been idle+unwatched at least ``idle_reap_ttl_seconds`` is **stopped with
        its host child reaped** -- freeing the Copilot process while leaving the
        session resumable (fresh child + ``load_session`` replay). This is what
        lets a front (Neuron Forge) merely connect/disconnect and never reap for
        resource reasons. Returns the count reaped. No-op unless enabled +
        Session-Host mode.
        """
        ttl = self._idle_reap_ttl_seconds
        if not ttl or ttl <= 0 or not self._session_host_enabled:
            return 0
        now = now if now is not None else time.time()
        reaped = 0
        for sid, s in list(self._sessions.items()):
            if s.status != SessionStatus.IDLE:
                continue
            if s.subscriber_count > 0:
                continue
            if s.has_active_background_tasks:
                continue
            if s.turn_count <= 0:
                # A 0-turn session has no persisted ACP conversation, so a fresh
                # child cannot load_session it -- reaping it to STOPPED would
                # leave it unresumable (validated live: resume -> "session not
                # found"). Only reap sessions with resumable state; leave empties
                # to the existing 0-turn worktree cleanup.
                continue
            idle_for = now - s.updated_at
            if idle_for < ttl:
                continue
            try:
                await self.stop_session(sid, reap_host=True)
            except SessionBusyError:
                continue
            except Exception:
                log.warning(
                    "Idle reap of session %s failed", sid, exc_info=True
                )
                continue
            reaped += 1
            log.info(
                "Idle-reaped session %s (%s): idle+unwatched %.0fs >= %.0fs TTL "
                "-- child freed, session resumable",
                sid, s.name, idle_for, ttl,
            )
        return reaped

    def note_heartbeats(self, now: float | None = None) -> int:
        """Periodic transport-liveness beat for RUNNING sessions (#145).

        Stamps ``last_heartbeat_at`` on every RUNNING session whose ACP client
        subprocess is still alive. A frozen heartbeat then means the transport
        died (tunnel drop / host sleep); a fresh heartbeat with a stale
        ``last_output_at`` means the agent stalled while the channel is up. In
        memory only; cheap (a process poll per session). Returns the count beat.
        """
        now = now if now is not None else time.time()
        beat = 0
        for s in list(self._sessions.values()):
            if s.status != SessionStatus.RUNNING:
                continue
            if s.client and s.client.is_running:
                s.note_heartbeat(now)
                beat += 1
        return beat

    async def start_session(
        self,
        target: SpawnTarget,
        agent_name: str | None = None,
        caller_id: str | None = None,
        permission_callback: Any | None = None,
        mcp_servers: list[dict[str, Any]] | None = None,
        copilot_args: list[str] | None = None,
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
            mcp_servers: Optional per-session MCP toolset (ACP server specs)
                mounted into the ACP session at session/new. None preserves
                the historic empty toolset.
            copilot_args: Optional extra ``copilot`` CLI args appended to
                ``target.copilot_args`` for this session only (e.g. a per-run
                ``--additional-mcp-config``). None preserves the agent's args.
        """
        if self._draining:
            raise DaemonDrainingError("session")
        # Per-session copilot args: append to the resolved target's args for
        # THIS spawn only (a fresh target copy so a shared/cached AgentConfig
        # target is never mutated). Every spawn path appends target.copilot_args,
        # so this reaches local, SSH, and command launches uniformly.
        if copilot_args:
            target = replace(
                target, copilot_args=[*target.copilot_args, *copilot_args]
            )
        # #2178: bind the caller worktree onto the target so the worktree-resolve
        # step records it on the spawned (bridge) worktree, enabling the Picker's
        # "Jump to caller". caller_id is the caller's WORKTREE_ID (agent-bridge
        # convention); a non-worktree caller simply won't resolve in the Picker.
        if caller_id and not target.caller_worktree:
            target = replace(target, caller_worktree=caller_id)
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
            cs_target = None
            if self._session_host_enabled:
                # Prefer the structured provider metadata (#177); fall back to
                # shape-detecting the spawn_command for agents registered before
                # the metadata seam existed (back-compat).
                if isinstance(target.codespace, dict) and target.codespace.get("name"):
                    cs_target = target.codespace
                elif target.spawn_command:
                    from .session_host.codespace_transport import parse_codespace_target
                    cs_target = parse_codespace_target(target.spawn_command)

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
                    mcp_servers=mcp_servers,
                )
            elif cs_target is not None:
                # CodeSpace Session-Host mode (#177): bootstrap the Host inside
                # the CodeSpace, forward its loopback endpoint, and drive ACP over
                # it -- so a host sleep/tunnel flap disconnects the front while
                # copilot keeps running on the CS and the front reattaches by
                # cursor. The relay port rides the persistent forward's -R for
                # ADO/git during a build.
                from .session_host.codespace_transport import build_codespace_spawner

                # Reproduce the relay env prelude the ``agent-codespaces ssh``
                # path injects, so a detached copilot on the CS has working
                # ADO/git auth over the credential relay (the daemon owns the
                # relay; the per-codespace token is minted by agent-codespaces).
                # Guarded: if agent-codespaces isn't importable, the Host runs
                # auth-light (fine for ACP + non-ADO turns).
                relay_prelude = ""
                relay_port = None
                try:
                    from agent_codespaces.relay_launch import build_relay_launch_env
                    relay_prelude, relay_port = build_relay_launch_env(cs_target["name"])
                except Exception:
                    log.info(
                        "CodeSpace relay env unavailable for %s -- launching "
                        "Session Host auth-light", cs_target["name"],
                    )
                cs_spawner = build_codespace_spawner(
                    cs_target["name"], cs_target["repo"], relay_port=relay_port,
                )
                # The acp_command is a far-side SHELL string (e.g.
                # ``cd /workspaces/repo && copilot --acp --stdio``), not an argv,
                # so the Session Host execs it through a login shell (with the
                # relay prelude prepended); copilot inherits the host's stdio pipe
                # as fd 0/1 and its exit ends the shell (child-liveness tracks it).
                remote_argv = ["bash", "-lc", relay_prelude + cs_target["acp_command"]]
                client, acp_sid = await self._connect_via_session_host(
                    target,
                    tracker=tracker,
                    session_id=session_id,
                    on_acp_event=on_acp_event,
                    permission_callback=permission_callback,
                    mcp_servers=mcp_servers,
                    spawner=cs_spawner,
                    remote_child_argv=remote_argv,
                    remote_cwd=None,
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
                            client.new_session(
                                cwd=session_cwd, mcp_servers=mcp_servers,
                            ),
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

        A ``RUNNING`` status only blocks resync while a turn is *actually* live
        in this daemon. A **wedged** session -- status left at ``RUNNING`` with
        no live prompt task (a turn whose runner already exited without a
        terminal event, or a session rehydrated after a daemon restart) -- is
        exactly what needs healing, so it is allowed through; only a genuinely
        live turn is refused (issue #22 / #2385).
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
                turn_live = (
                    session._prompt_task is not None
                    and not session._prompt_task.done()
                )
                if turn_live:
                    raise ValueError(
                        f"Session {session_id} is running a live turn "
                        "-- cannot resync"
                    )
                # Wedged RUNNING: no live turn to protect. Cancel any lingering
                # (already-finished) task handle and heal the stuck state.
                log.warning(
                    "Resyncing wedged RUNNING session %s (no live turn)",
                    session_id,
                )
                if session._prompt_task is not None:
                    with contextlib.suppress(Exception):
                        session._prompt_task.cancel()

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

        # Always drive the event log to a terminal state so no consumer is left
        # mirroring a turn that never ends. On the happy path this trails the
        # client's turn_complete; on failure it is paired with the client's
        # (now non-hanging) error -- either way the stream reaches idle, matching
        # the synthetic idle a resync would emit (issue #22).
        if session.event_log:
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.IDLE.value,
            })

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

    async def interrupt_turn(self, session_id: str) -> "Session":
        """Interrupt the in-flight turn, leaving the session alive and idle.

        Sends an ACP cancel to the active prompt so the current turn stops and
        the session returns to IDLE, ready for the next turn. Unlike
        ``stop_session``/``end_session`` this preserves the ACP client and the
        session itself -- it cancels the *turn*, not the session. A no-op that
        returns the session unchanged if nothing is in flight.

        The in-flight ``_run_prompt`` observes the cancel (``send_prompt``
        returns with a ``cancelled`` stop reason, or raises) and lands the
        session IDLE with a terminal ``session_state_changed`` (the Phase-1
        guarantee), which flows to every consumer over the event stream.
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        task = session._prompt_task
        if (session.status != SessionStatus.RUNNING
                or task is None or task.done()):
            # Nothing live to interrupt -- return the session as-is.
            return session

        # Ask the agent to cancel the active turn (ACP session/cancel).
        if session.client is not None:
            with contextlib.suppress(Exception):
                await session.client.cancel_prompt()

        # Give the runner a bounded moment to settle to a terminal state so the
        # caller sees idle promptly. `shield` so this wait never cancels the
        # runner itself; if it does not settle in time the terminal still flows
        # over the event stream (and the wedged-session watchdog is the backstop).
        # Never force-kill the task here -- that would end the session.
        with contextlib.suppress(Exception):
            await asyncio.wait_for(asyncio.shield(task), timeout=10.0)

        log.info("Interrupted in-flight turn for session %s", session_id)
        return session

    async def answer_ask_user(
        self,
        session_id: str,
        tool_call_id: str,
        content: dict[str, Any] | None,
        *,
        action: str = "accept",
    ) -> bool:
        """Answer a parked ``ask_user`` elicitation on a live session.

        Resolves the ACP client's pending ``elicitation/create`` for the given
        tool call so the agent's ``ask_user`` completes and the turn continues.
        ``action`` is ``accept`` (with ``content``), ``decline``, or ``cancel``.
        Returns ``True`` when a matching request was outstanding, ``False`` when
        none was (already answered/withdrawn). Raises ``KeyError`` if the
        session is unknown and ``ValueError`` if it has no live ACP client.
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        if session.client is None:
            raise ValueError(f"Session {session_id} has no live ACP client")
        return session.client.resolve_elicitation(
            tool_call_id, content, action=action,
        )

    async def stop_session(
        self, session_id: str, *, force: bool = False, reap_host: bool = False
    ) -> None:
        """Stop a session -- shut down ACP client, preserve state for resume.

        Refuses with SessionBusyError when the session is hosting active
        background sub-agents unless ``force`` is set, so a routine stop does
        not kill in-flight background work (e.g. the PR daemon).

        Teardown is **never gated by the drain flag** (#1755): stopping a
        session is exactly what lets the busy sessions ``drain()`` waits on
        settle, so gating it here would self-deadlock a redeploy.

        ``reap_host`` (idle-reaper path, #1826): a plain stop in Session-Host
        mode only *detaches* the client, leaving the child **reattachable**; the
        idle reaper instead wants the child **freed** for resource reclamation,
        so it reaps the host record too. The session still ends STOPPED and is
        resumable via ``load_session`` replay (a *fresh* child) -- allowed
        because the reaper only ever stops an IDLE session, never mid-turn
        (goal 1).
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        if not force and session.has_active_background_tasks:
            raise SessionBusyError(session_id, session.active_background_tasks)

        await self._quiesce_session(session)

        # Idle-reaper only: free the Session Host child (a plain stop detaches
        # to keep it reattachable). Safe here because the session is idle.
        if reap_host and self._session_host_enabled and self._host_index is not None:
            rec = self._host_index.get(session_id)
            if rec is not None:
                self._reap_host_record(rec, "idle reap (#1826)")

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

        # Session-Host mode: an explicit end is a *sanctioned terminate*, so it
        # must REAP the child -- unlike stop, whose host-mode shutdown only
        # detaches to keep the child reattachable. Without this the host + child
        # survive with a dangling index record and are never collected (#1786;
        # goal 1: termination is intentional, not inadvertent).
        if self._session_host_enabled and self._host_index is not None:
            rec = self._host_index.get(session_id)
            if rec is not None:
                self._reap_host_record(rec, "session ended")

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
