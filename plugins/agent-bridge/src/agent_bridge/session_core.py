"""Session manager core state, drain, and persistence helpers."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from dataclasses import replace
from datetime import datetime, timezone
from typing import Any

from .cold_store import ColdStoreSession, fetch_cold_store_session
from .db import Database
from .events import EventLog
from .models import (
    AutoHandoffPolicy,
    ContextThresholds,
    PhasedTimeouts,
    RetentionConfig,
    SessionStatus,
)
from .session_manager import (
    _REQUEST_OVERRIDES_KEY,
    _format_optional_hours,
    _parse_progress_markers,
    ProviderTargetRefreshError,
    Session,
    log,
)
from .transport import SpawnTarget


class _SessionCoreMixin:
    """Session-manager core state, drain, and persistence helpers."""

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
        auto_handoff: AutoHandoffPolicy | None = None,
        timeouts: PhasedTimeouts | None = None,
        retention: RetentionConfig | None = None,
        drain_auto_release_s: float | None = None,
        drain_warn_interval_s: float | None = None,
        session_host_state_dir: str | None = None,
        session_host_stale_reap_seconds: float = 0.0,
        graceful_cancel_settle_seconds: float = 45.0,
        cancel_turns_on_redeploy: bool = False,
        idle_reap_ttl_seconds: float = 0.0,
        live_stall_interrupt_after_s: float = 900.0,
        session_host_unexpected_reap_seconds: float = 60.0,
        session_host_active_reap_seconds: float = 0.0,
    ) -> None:
        self._db = db
        self._sessions: dict[str, Session] = {}
        self._resolver: Any | None = None
        # Session-Host mode is now the ONLY mode (dotfiles#1478): every local and
        # CodeSpace child lives in a survivable Session Host that outlives a
        # frontend restart. The host index is the durable session_id ->
        # host-endpoint map used to reattach. (ssh/command targets still use the
        # process-owned transport in start_session -- an SshSpawner/ElevatedSpawner
        # to host those far-side is the remaining gap, ThomasMichon/copilot-extensions#566.)
        self._session_host_stale_reap_seconds = session_host_stale_reap_seconds
        self._graceful_cancel_settle_seconds = graceful_cancel_settle_seconds
        # Redeploy turn-cancel policy (dotfiles#1661). Default False = the
        # invariant: a frontend redeploy/cutover/shutdown DETACHES in-flight
        # turns (leaves them running on their Session Host for reattach) rather
        # than cancelling them. Cancelling the remote task is an explicit host
        # action only (interrupt_turn / explicit stop). True restores the legacy
        # cancel-then-Resume behavior.
        self._cancel_turns_on_redeploy = cancel_turns_on_redeploy
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
        # Session-host self-reap grace (#51): how long an idle, front-less child
        # lingers after an *unexpected* disconnect before the host reaps itself.
        # Handed to every LocalSpawner-launched host. 0 disables the timer (the
        # graceful-detach fast path still reaps a reapable child promptly).
        self._session_host_unexpected_reap_seconds = session_host_unexpected_reap_seconds
        # Bounded keep-alive for an ACTIVE (mid-turn / active background work)
        # front-less child after an unexpected disconnect (#145): the detached
        # host holds it this long so a reconnecting front can resume the in-flight
        # turn, then lets it go (the session stays resumable via fresh child +
        # load_session replay). 0 disables (legacy: an active child lives until
        # its own stop). Handed to every LocalSpawner/CodeSpaceSpawner.
        self._session_host_active_reap_seconds = session_host_active_reap_seconds
        self._host_index: Any = None
        self._remote_recovery_inconclusive: set[str] = set()
        self._remote_recovery_skipped: set[str] = set()
        # Consecutive failed reattach attempts per session_id (#2998); see
        # _DISCONNECTED_REATTACH_ESCALATE_AFTER.
        self._disconnected_reattach_failures: dict[str, int] = {}
        self._disconnected_reattach_retry_at: dict[str, float] = {}
        # Live remote-boundary forwards (session_id -> LocalForward). Held so a
        # CodeSpace/mesh Session Host's -L forward can be refreshed on reattach
        # and torn down on teardown. Empty for local hosts.
        self._forwards: dict[str, Any] = {}
        # Live dedicated credential-relay supervisors (session_id -> relays).
        # These are intentionally separate from the frontend-refreshed -L above:
        # their lifetime follows the remote Session Host/child.
        self._relays: dict[str, list[Any]] = {}
        # Cross-process ownership for trusted container SSH/Session-Host use.
        # This is bridge-owned (not held by agent-containers' launch wrapper)
        # and follows the remote Host record's lifetime.
        self._container_locks: dict[str, tuple[Any, str]] = {}
        self._container_lock_sessions: dict[str, str] = {}
        # Same-machine, cross-process ownership for a CodeSpace's shared
        # credential-relay reverse-forward, mirroring the container locks
        # above (claim-consistency sweep, agent-bridge-cli-mode-sessions
        # Phase 4 follow-up): Session-Host dispatch to a CodeSpace never runs
        # ``agent-codespaces ssh``/``copilot`` (the CLI verbs that already
        # take this lock), so without this the daemon's own headless dispatch
        # could still collide with a local `ssh`/`copilot` invocation against
        # the same CodeSpace on this machine, even after the cross-machine
        # ``_claim_codespace`` claim above was already closing the
        # cross-worktree half of this gap.
        self._codespace_locks: dict[str, tuple[Any, str]] = {}
        self._codespace_lock_sessions: dict[str, str] = {}
        # Strong refs to in-flight best-effort remote-reap tasks (so they are not
        # GC'd mid-flight); each removes itself on completion.
        self._remote_reap_tasks: set[Any] = set()
        from pathlib import Path as _Path

        from .session_host.host_index import HostIndex
        # Default the host state dir next to the DB (isolated per Database) rather
        # than a hardcoded ~/.agent-bridge/hosts. In production the DB is
        # ~/.agent-bridge/sessions.db, so this still resolves to
        # ~/.agent-bridge/hosts -- identical behavior -- but a test/embedded use
        # with a temp DB gets an isolated index instead of writing into (and
        # sharing, across parallel runs) the real developer/CI home. Now that
        # Session Hosts are always on the index is ALWAYS constructed, so this
        # isolation matters (dotfiles#1478 review).
        if session_host_state_dir:
            sd = _Path(session_host_state_dir).expanduser()
        else:
            sd = _Path(self._db.db_path).expanduser().parent / "hosts"
        sd.mkdir(parents=True, exist_ok=True)
        self._host_index = HostIndex(sd / "index.json")
        self._thresholds = context_thresholds or ContextThresholds()
        # Context-pressure handoff policy (off by default). When enabled, a
        # session crossing the critical threshold rolls the worktree in place
        # instead of dead-ending. Strong refs to in-flight auto-handoff tasks so
        # they are not GC'd mid-cutover (each removes itself on completion).
        self._auto_handoff = auto_handoff or AutoHandoffPolicy()
        self._auto_handoff_tasks: set[asyncio.Task[None]] = set()
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

    def set_resolver(self, resolver: Any) -> None:
        """Attach the live resolver used for safe provider target refresh."""
        self._resolver = resolver

    async def fetch_cold_store_session(
        self, session_id: str
    ) -> ColdStoreSession | None:
        """Ask a registered cold-store provider for a session this ledger has
        nothing live for (see :func:`agent_bridge.cold_store.fetch_cold_store_session`
        and ``visions/plugins/agent-bridge`` §Concepts/*cold-store providers*).
        Only call after :meth:`get_session` returns ``None``."""
        return await fetch_cold_store_session(self._resolver, session_id)

    @staticmethod
    def _provider_backed_target(target: SpawnTarget) -> bool:
        """Whether a persisted target carries namespace-provider metadata."""
        return any(
            isinstance(value, dict) and bool(value)
            for value in (target.codespace, target.container, target.venue)
        )

    async def _refresh_provider_target(self, session: Session) -> None:
        """Re-resolve a stopped provider session before spawning a new child.

        Surviving Session Hosts are reattached before this seam, so only a
        genuinely fresh launch adopts the current provider declaration.
        Session-owned placement, caller identity, and request environment stay
        bound to the existing bridge session.
        """
        if (
            self._resolver is None
            or not session.agent_name
            or not self._provider_backed_target(session.target)
        ):
            return

        persisted = session.target
        persisted_venue = (
            persisted.venue if isinstance(persisted.venue, dict) else {}
        )
        overrides = persisted_venue.get(_REQUEST_OVERRIDES_KEY)
        if not isinstance(overrides, dict):
            raise ProviderTargetRefreshError(
                "Provider target predates override provenance; recreate the "
                "session to adopt the current provider configuration"
            )
        request_env = overrides.get("env")
        request_copilot_args = overrides.get("copilot_args")
        if not isinstance(request_env, dict) or not isinstance(
            request_copilot_args, list
        ):
            raise ProviderTargetRefreshError(
                "Provider target has invalid override provenance; recreate the "
                "session to adopt the current provider configuration"
            )
        try:
            resolved = await self._resolver.resolve_async(session.agent_name)
        except Exception as exc:
            log.warning(
                "Could not refresh provider target for session %s (%s)",
                session.session_id,
                session.agent_name,
                exc_info=True,
            )
            raise ProviderTargetRefreshError(
                "Current provider target could not be resolved; repair provider "
                "configuration and retry"
            ) from exc
        refreshed = replace(
            resolved,
            cwd=persisted.cwd,
            project=persisted.project,
            worktree_id=persisted.worktree_id,
            caller_worktree=persisted.caller_worktree,
            caller_owner_ref=persisted.caller_owner_ref,
            env={**resolved.env, **request_env},
            copilot_args=[
                *resolved.copilot_args,
                *request_copilot_args,
            ],
            venue={
                **(resolved.venue or {}),
                _REQUEST_OVERRIDES_KEY: {
                    "env": dict(request_env),
                    "copilot_args": list(request_copilot_args),
                },
            },
        )
        session.target = refreshed
        self._db.update_session_target(
            session.session_id,
            refreshed.to_json(),
            refreshed.cwd,
        )
        log.info(
            "Refreshed provider target for stopped session %s (%s)",
            session.session_id,
            session.agent_name,
        )

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
            # Live Session Host census (dotfiles#1656): how many independent
            # Session Hosts (each owning a possibly-mid-turn child) this daemon
            # is currently fronting. Surfaced on /health so a drain/cutover never
            # looks "clean" while live hosts it must preserve go unaccounted for.
            "live_host_count": self.live_host_count,
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

    @property
    def cancel_turns_on_redeploy(self) -> bool:
        """Whether a frontend redeploy/cutover/shutdown cancels in-flight turns.

        Default False = the dotfiles#1661 invariant (detach-only; the remote
        turn keeps running on its Session Host for reattach). Read by the app
        lifespan shutdown to pass ``cancel_turn`` to ``stop_session``.
        """
        return self._cancel_turns_on_redeploy

    @property
    def live_host_count(self) -> int:
        """How many live Session Hosts this daemon is currently fronting.

        Each host independently owns a (possibly mid-turn) child that survives a
        frontend restart. Surfaced on /health and in the drain result so a
        drain/cutover never looks "clean" while live hosts it must preserve go
        unaccounted for (dotfiles#1656)."""
        return len(self._live_host_records())

    def busy_sessions(self) -> list[str]:
        """Session IDs that must not be torn down: actively streaming a turn
        (RUNNING) or mid connect/resume (STARTING), hosting active background
        sub-agents (the dev57 busy oracle), or backed by a live **remote**
        Session Host whose far-side child may be mid-work while the local status
        is stale (dotfiles#1633).

        Remote-boundary correctness: ``codespace:``/``ssh`` sessions are the
        majority of hosts, and their turn runs across the boundary -- so the
        local status alone is not a reliable "idle" signal, and keying busy
        purely off it let ``drain`` report a false-clean "0 busy" and tear a live
        remote turn down. A live remote host is therefore counted unless its
        session is *at-rest* ``IDLE`` (which a cutover preserves via host
        reattach, so it need not block drain; the idle reaper is likewise
        remote-aware). This is the signal drain() waits on."""
        busy: set[str] = set()
        for sid, session in self._sessions.items():
            if session.status in (SessionStatus.RUNNING, SessionStatus.STARTING) \
                    or session.has_active_background_tasks:
                busy.add(sid)
        for sid in self._live_remote_host_sessions():
            session = self._sessions.get(sid)
            if session is not None and session.status == SessionStatus.IDLE:
                # At-rest remote host -> preserved across a cutover by host
                # reattach; does not need to block drain.
                continue
            busy.add(sid)
        return sorted(busy)

    async def graceful_cancel_for_redeploy(
        self,
        *,
        settle_timeout: float | None = None,
        exclude_session_id: str | None = None,
    ) -> dict[str, Any]:
        """Prepare in-flight turns for a frontend redeploy / cutover / shutdown.

        **Default (detach-only, the dotfiles#1661 invariant).** A frontend
        restart is a *transport* event, not an explicit host-agent cancel, so
        this does **not** touch the remote agent's turn. The Session Host keeps
        running the child and buffers its frames ("tmux for the agent"), so the
        restarted frontend reattaches and the SAME in-flight turn continues with
        no gap and no re-run. Cancelling the remote task is reserved for explicit
        host actions (``interrupt_turn`` / an explicit stop).

        **Legacy (opt-in, ``cancel_turns_on_redeploy=True``).** Restores the old
        behavior: inject an ACP ``session/cancel`` into every RUNNING turn
        (except ``exclude_session_id`` -- e.g. the agent updating its own
        bridge), flag each host-backed session ``resume_on_reattach`` so the
        restarted frontend sends a single "Resume", and wait up to the settle
        budget for the cancelled turns to stop.

        Returns a summary (``mode`` is ``"detach-only"`` or ``"cancel"``).
        """
        import asyncio as _asyncio

        targets = [
            sid for sid, s in self._sessions.items()
            if s.status == SessionStatus.RUNNING and sid != exclude_session_id
        ]

        if not self._cancel_turns_on_redeploy:
            # Detach-only (dotfiles#1661): leave every in-flight turn running on
            # its Session Host; the successor frontend reattaches and continues
            # it. No ACP cancel, no Resume nudge, no settle wait.
            if targets:
                log.info(
                    "Redeploy detach-only: leaving %d in-flight turn(s) running "
                    "on their Session Host(s) for reattach (no cancel): %s",
                    len(targets), ", ".join(targets),
                )
            return {
                "cancelled": [], "preserved": targets, "settled": True,
                "mode": "detach-only", "enabled": True,
            }

        # --- opt-in legacy: assertively cancel in-flight turns ---------------
        settle = (self._graceful_cancel_settle_seconds
                  if settle_timeout is None else settle_timeout)
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
        return {
            "cancelled": cancelled, "preserved": [], "settled": not still,
            "mode": "cancel", "enabled": True,
        }

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
        await self.graceful_cancel_for_redeploy(
            exclude_session_id=exclude_session_id,
        )
        # Detach-only redeploy (dotfiles#1661): a session backed by a live
        # Session Host is PRESERVED across the restart (its turn keeps running on
        # the host and the successor reattaches), so the drain must not block
        # waiting for it to "settle" -- it never will, and it doesn't need to.
        # Only genuinely non-preservable busy work (process-owned command/ssh
        # turns, background sub-agents) is waited on. When cancelling is opt-in,
        # nothing is preserved and every busy session is waited on as before.
        preserved: set[str] = (
            {r.session_id for r in self._live_host_records()}
            if not self._cancel_turns_on_redeploy else set()
        )
        deadline = time.monotonic() + max(0.0, timeout)
        busy = [s for s in self.busy_sessions()
                if s != exclude_session_id and s not in preserved]
        log.info(
            "Drain started: %d session(s) busy%s, timeout=%.0fs%s",
            len(busy),
            f" ({len(preserved)} host-backed preserved for reattach)"
            if preserved else "",
            timeout, " (force)" if force else "",
        )
        while busy and time.monotonic() < deadline:
            await _asyncio.sleep(poll)
            busy = [s for s in self.busy_sessions()
                    if s != exclude_session_id and s not in preserved]

        drained = not busy
        if drained:
            log.info(
                "Drain complete: no busy sessions remain%s",
                f" ({len(preserved)} host-backed turn(s) preserved for reattach: "
                f"{', '.join(sorted(preserved))})" if preserved else "",
            )
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
            # Live Session Host census (dotfiles#1656): a host-backed turn is
            # PRESERVED across the restart (detached, its turn keeps running on
            # the host, the successor reattaches) rather than drained. Surface it
            # explicitly so a `clean` drain never hides an unaccounted-for live
            # host -- an operator/cutover sees "clean, N turns preserved for
            # reattach", not a bare clean.
            "preserved": sorted(preserved),
            "live_host_count": self.live_host_count,
            "timeout": timeout,
        }


    @property
    def db(self) -> Database:
        """The backing database (used by routes for cursor persistence)."""
        return self._db

    def _mark_session_failed(self, session: Session, *, trigger: str) -> None:
        """Persist and publish one authoritative failed transition."""
        session.status = SessionStatus.FAILED
        self._db.update_session_status(
            session.session_id, SessionStatus.FAILED.value, time.time()
        )
        if session.event_log:
            session.event_log.append(
                "session_state_changed",
                {"status": SessionStatus.FAILED.value, "trigger": trigger},
            )

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
            session.restart_status = status
            session.background_recovery_enabled = bool(row.get("background_recovery_enabled", 1))

            # Restore the session's declared per-session MCP toolset. Without
            # this, a daemon restart silently drops it forever (it otherwise
            # lives only on the in-memory Session object) -- the confirmed
            # root cause of a reviewer task permanently losing its dedicated,
            # credential-bound tools after any restart (aperture-labs #7239).
            config_json = row.get("config_json")
            if config_json:
                try:
                    config_data = json.loads(config_json)
                except (TypeError, ValueError):
                    config_data = None
                if isinstance(config_data, dict):
                    servers = config_data.get("mcp_servers")
                    if isinstance(servers, list):
                        session.mcp_servers = [
                            dict(s) for s in servers if isinstance(s, dict)
                        ]

            # Mark formerly-active sessions as stopped
            interrupted_on_restart = False
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
                        interrupted_on_restart = True
            else:
                session.status = SessionStatus(status)

            # Restore event log from DB
            session.event_log = EventLog.from_db(
                self._db,
                sid,
                acp_session_id=session.acp_session_id,
                worktree_id=target.worktree_id,
            )
            if status in (
                SessionStatus.RUNNING.value,
                SessionStatus.IDLE.value,
                SessionStatus.STARTING.value,
            ):
                # Persist the restart boundary the DB state already records.
                # A formerly-running turn cannot remain open in telemetry/SSE
                # after its process is gone.
                if (
                    interrupted_on_restart
                    and session.event_log.telemetry_conversation_state
                    in {None, "sending", "responding"}
                ):
                    session.event_log.append(
                        "turn_complete", {"stop_reason": "interrupted"}
                    )
                session.event_log.append(
                    "session_state_changed",
                    {"status": SessionStatus.STOPPED.value, "trigger": "daemon_restart"},
                )
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
            "terminal_count": 0,
            "eligible_count": 0,
            "oldest_terminal_age_hours": None,
            "vacuumed": False,
            "reclaimed_bytes": 0,
            "free_bytes_before_vacuum": 0,
            "vacuum_threshold_bytes": int(ret.vacuum_min_free_mb * 1024 * 1024),
        }
        if not ret.enabled:
            return result

        now = now if now is not None else time.time()
        cutoff = now - ret.max_age_hours * 3600.0
        retained = self._db.gc_status_summary(ret.statuses)
        result["terminal_count"] = int(retained["count"])
        oldest_terminal = retained["oldest_updated_at"]
        if oldest_terminal is not None:
            result["oldest_terminal_age_hours"] = (
                max(0.0, now - float(oldest_terminal)) / 3600.0
            )
        eligible = self._db.gc_eligible_session_ids(ret.statuses, cutoff)
        result["eligible_count"] = len(eligible)

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

        info = self._db.db_size_info()
        result["free_bytes_before_vacuum"] = info["free_bytes"]
        if ret.vacuum:
            try:
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

        log.info(
            "GC (%s): terminal=%d eligible=%d pruned=%d oldest_terminal=%s "
            "free=%.1fMB threshold=%.1fMB vacuumed=%s reclaimed=%.1fMB",
            reason,
            result["terminal_count"],
            result["eligible_count"],
            len(pruned),
            _format_optional_hours(
                None
                if result["oldest_terminal_age_hours"] is None
                else float(result["oldest_terminal_age_hours"]) * 3600.0
            ),
            result["free_bytes_before_vacuum"] / (1024.0 * 1024.0),
            result["vacuum_threshold_bytes"] / (1024.0 * 1024.0),
            "yes" if result["vacuumed"] else "no",
            result["reclaimed_bytes"] / 1e6,
        )
        return result

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


