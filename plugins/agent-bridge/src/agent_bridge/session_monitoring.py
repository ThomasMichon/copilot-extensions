"""Session monitoring, heartbeat, and reap sweeps."""

from __future__ import annotations

import contextlib
import time
from typing import Any

from .models import SessionStatus
from .session_manager import SessionBusyError, log


class _SessionMonitoringMixin:
    """Session monitoring, heartbeat, and reap sweeps."""

    async def recover_disconnected_hosts(self) -> int:
        """In-session liveness-driven reattach for host-backed sessions (P1).

        The P0 heartbeat only *stamps* liveness; this is the *actuator*. For each
        host-backed session whose transport to its (still-alive) Session Host has
        dropped -- ``liveness_state() == 'disconnected'`` on a RUNNING turn, or a
        non-RUNNING session whose host-mode client is no longer running -- while
        the host + child processes survive, redial the host and resume by cursor
        (no restart, no lost turn). A merely ``stalled`` session (channel up,
        agent silent) is surfaced but not reattached -- reconnecting cannot
        un-wedge a silent agent. Disconnected CodeSpaces require a fresh no-wake
        availability check on each pass, including venues skipped at startup.
        Returns the count reattached.
        """
        if self._host_index is None:
            return 0
        from .session_host.version_mux import HostDisposition, plan_host

        recovered = 0
        codespace_availability: dict[str, bool] = {}
        now = time.time()
        for rec in list(self._live_host_records()):
            session = self._sessions.get(rec.session_id)
            if session is None or not session.acp_session_id:
                continue
            if session.status in {SessionStatus.FAILED, SessionStatus.ENDED}:
                continue  # terminal sessions wait for explicit recovery (#2379)
            if not session.background_recovery_enabled:
                continue
            client = session.client
            if (
                client is not None
                and client.host_child_exit_code is not None
            ):
                with contextlib.suppress(Exception):
                    await client.shutdown()
                self._reap_host_record(
                    rec,
                    "Session Host child exit observed by attached client",
                )
                session.client = None
                session.status = SessionStatus.STOPPED
                self._db.update_session_status(
                    rec.session_id, SessionStatus.STOPPED.value, time.time()
                )
                if session.event_log:
                    session.event_log.append("session_state_changed", {
                        "status": SessionStatus.STOPPED.value,
                        "host_child_exited": True,
                        "exit_code": client.host_child_exit_code,
                    })
                self._clear_disconnected_reattach_retry_state(rec.session_id)
                continue
            # A live, running client needs nothing; surface a stall and move on.
            if client is not None and client.is_running:
                if session.liveness_state(now) == "stalled":
                    log.warning(
                        "Session %s stalled (channel up, no output) -- "
                        "surfaced, not reattached", rec.session_id,
                    )
                self._clear_disconnected_reattach_retry_state(rec.session_id)
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
            # Same in-flight-resume race as the startup reattach path (see
            # reattach_session_hosts): skip this session for this pass rather
            # than race a concurrent resume_session()/lifecycle operation that
            # already holds the lock.
            if session._lifecycle_lock.locked():
                continue
            if await self._reattach_or_note_failure(
                rec, session, keep=keep,
                codespace_availability=codespace_availability, now=now,
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
           so it lands IDLE with a terminal ``session_state_changed``. A
           ``disconnected`` (transport gone) session is rebuilt at once; a
           ``stalled`` (transport UP, silent) one is held until its silence
           passes ``live_stall_interrupt_after_s`` first, because a **reattached,
           still-thinking** turn (adopted by cursor after a restart, no
           ``send_prompt`` here) is indistinguishable from a wedge by output
           alone -- resyncing it early would land a live think IDLE (#1276).

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
            # No live turn in THIS daemon. Two shapes reach here:
            #  * ``disconnected`` -- the transport is gone; nothing live to
            #    preserve and the log may be truncated, so rebuild it now.
            #  * ``stalled`` -- the client is UP but no local prompt task drives
            #    it. That is ALSO exactly what a **reattached, still-thinking**
            #    turn looks like: after a daemon restart / tunnel flap the
            #    Session Host's child survives and is adopted by cursor with NO
            #    ``send_prompt`` in this daemon, and a deep-reasoning step emits
            #    no ACP frame for minutes. Output alone can't tell that from a
            #    genuine wedge, and ``resync_session`` tears the client down,
            #    respawns, and lands the session IDLE -- which would KILL a
            #    resumed think mid-turn (dotfiles#1276). So hold off on a
            #    client-up stall until its silence exceeds the same conservative
            #    threshold the live-stall interrupt uses; a real wedge still
            #    heals, just later. ``disconnected`` (no live transport) is not
            #    gated -- it must rebuild to recover.
            if liveness == "stalled":
                threshold = self._live_stall_interrupt_after_s
                silent_for = (
                    now - session.last_output_at
                    if session.last_output_at is not None else 0.0
                )
                if not (threshold > 0 and silent_for > threshold):
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
        if self._host_index is None:
            return []
        from .session_host.version_mux import is_compatible

        return [
            rec for rec in self._live_host_records()
            if not is_compatible(rec.protocol_version)
        ]

    def sweep_stranded_hosts(self) -> int:
        """Reap stranded incompatible Session Hosts that are now reapable.

        A periodic counterpart to the startup-time gate in
        ``reattach_session_hosts``: during a single long frontend lifetime an
        incompatible host's child may finally reach its own stop, or an immortal
        one may outlive the configured ``session_host_stale_reap_seconds`` sprawl
        bound. This re-evaluates every live host and reaps those whose disposition
        is REAP_STOPPED or FORCE_REAP -- never touching a compatible host or a
        stranded host still within the bound. Returns the count reaped.
        """
        if self._host_index is None:
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
        persisted ACP conversation a fresh child can ``load_session``), is **not
        backed by a live remote Session Host** (whose far-side child's activity
        the frontend cannot see -- dotfiles#1633), and has been idle+unwatched at
        least ``idle_reap_ttl_seconds`` is **stopped with its host child reaped**
        -- freeing the Copilot process while leaving the session resumable (fresh
        child + ``load_session`` replay). This is what lets a front (Neuron
        Forge) merely connect/disconnect and never reap for resource reasons.
        Returns the count reaped. No-op unless enabled + Session-Host mode.
        """
        ttl = self._idle_reap_ttl_seconds
        if not ttl or ttl <= 0:
            return 0
        now = now if now is not None else time.time()
        # Sessions fronted by a live REMOTE host must not be idle-reaped on LOCAL
        # idle/unwatched signals: a codespace/ssh child can be mid remote
        # tool-call while the session looks idle here, so "freeing" it decapitates
        # live remote work (observed: a 15-30min remote build poll whose child was
        # freed while the session read idle+unwatched -- dotfiles#1633). Local
        # hosts keep the reaper (their pid + status are locally authoritative).
        remote_host_sids = self._live_remote_host_sessions()
        reaped = 0
        for sid, s in list(self._sessions.items()):
            if s.status != SessionStatus.IDLE:
                continue
            if s.subscriber_count > 0:
                continue
            if s.has_active_background_tasks:
                continue
            if sid in remote_host_sids:
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

