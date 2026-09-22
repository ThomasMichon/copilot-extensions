"""Session teardown, interruption, and end-of-life helpers."""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import Any

from .models import SessionStatus
from .session_manager import (
    RemoteHostRecoveryPendingError,
    Session,
    SessionBusyError,
    _cleanup_worktree,
    _codespace_claim_key,
    log,
)


def _core() -> Any:
    from . import session_manager as core

    return core


class _SessionLifecycleMixin:
    """Session teardown, interruption, and end-of-life helpers."""

    async def _quiesce_session(
        self, session: Session, *, cancel_turn: bool = True
    ) -> None:
        """Best-effort teardown of a session's in-flight prompt + ACP client.

        Must be resilient to a *mid-turn* session: cancelling an in-flight
        prompt or shutting down a busy ACP client must never raise out of
        stop/end. (A raising shutdown here surfaced as HTTP 500 when ending a
        mid-turn session -- see the credential-hang showcase report.) Errors
        are logged and swallowed so teardown always completes.

        ``cancel_turn`` (default True): send an ACP ``session/cancel`` to the
        remote agent's in-flight turn. A redeploy/shutdown passes ``False``
        (dotfiles#1661): the frontend detaches (host + child + turn survive for
        reattach) WITHOUT telling the remote agent to cancel -- only the local
        prompt task + ACP client are torn down. Explicit stop/end keep the
        default so an operator stop still cancels.
        """
        # Signal a GRACEFUL detach to the session host BEFORE tearing down the
        # transport, carrying the child's current reapable state, so an idle
        # host self-reaps promptly instead of waiting out the unexpected-grace
        # window (#51). Best-effort and pre-cancel: computed from the *current*
        # status before the in-flight prompt below is cancelled.
        await self._detach_host(session)
        task = session._prompt_task
        if task and not task.done():
            if session.client and cancel_turn:
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

        # Operator explicitly cancelled this turn: retire its durable queue too,
        # BEFORE the runner settles IDLE (whose tail would otherwise drain it).
        # Auto-firing a batch of queued follow-ups right after a manual
        # interrupt would surprise the operator -- mirror NF's "queue cleared if
        # cancelled" (#4114). Cleared before the ACP cancel so the drain that
        # runs on settle finds nothing.
        self._clear_pending_queue(session, reason="interrupted")

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

    async def answer_permission(
        self, session_id: str, request_id: str, option_id: str
    ) -> bool:
        """Resolve a correlated permission request on a live session."""
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        if session.client is None:
            raise ValueError(f"Session {session_id} has no live ACP client")
        return session.client.resolve_permission(request_id, option_id)

    async def stop_session(
        self, session_id: str, *, force: bool = False, reap_host: bool = False,
        cancel_turn: bool = True,
        allow_background_recovery: bool | None = None,
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

        ``cancel_turn`` (default True): tell the remote agent to cancel its
        in-flight turn (ACP ``session/cancel``). A redeploy/shutdown passes
        ``False`` (dotfiles#1661) so the frontend detaches without cancelling --
        the host + child + turn survive for reattach. An explicit operator stop
        keeps the default (a stop IS an explicit host cancel).

        ``allow_background_recovery`` (default: derived from ``cancel_turn``):
        explicit stops go dormant; redeploy detaches stay auto-recoverable.
        """
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")

        if not force and session.has_active_background_tasks:
            raise SessionBusyError(session_id, session.active_background_tasks)

        if allow_background_recovery is None:
            allow_background_recovery = not cancel_turn

        # Serialize with in-flight reattach/resume (same lock resume_session
        # uses): otherwise a reattach begun before this stop could land a
        # live client after stop already persisted dormancy (review of #3058).
        async with session._lifecycle_lock:
            await self._quiesce_session(session, cancel_turn=cancel_turn)

            # Idle-reaper only: free the Session Host child (a plain stop
            # detaches to keep it reattachable, safe since the session is idle).
            if reap_host and self._host_index is not None:
                rec = self._host_index.get(session_id)
                if rec is not None:
                    self._reap_host_record(rec, "idle reap (#1826)")

            session.status = SessionStatus.STOPPED
            now = time.time()
            self._db.update_session_stopped(session_id, now, allow_background_recovery)
            session.background_recovery_enabled = allow_background_recovery
            if not allow_background_recovery:
                self._clear_disconnected_reattach_retry_state(session_id)
        # Release the per-worktree ownership reservation (#2912): a stopped
        # owned session is no longer actively controlling the worktree, so free
        # it for a live CLI (or a later fresh owner) to claim.
        self._db.release_worktree_ownership(session_id=session_id)
        if session.event_log:
            session.event_log.append("session_state_changed", {
                "status": SessionStatus.STOPPED.value,
            })
        session.touch()
        log.info("Session %s (%s) stopped", session_id, session.name)

    async def end_session_if_idle(
        self,
        session_id: str,
        *,
        force: bool = False,
    ) -> None:
        """End only if no turn can start between the idle check and teardown."""
        session_id = self._resolve_ref(session_id) or session_id
        session = self._sessions.get(session_id)
        if not session:
            raise KeyError(f"Session {session_id} not found")
        async with session._turn_start_lock:
            if self._sessions.get(session_id) is not session:
                raise KeyError(f"Session {session_id} not found")
            if (
                session.status != SessionStatus.STOPPED
                and not session.is_at_rest()
            ):
                raise ValueError(f"Session {session_id} is not idle")
            if self._db.count_pending_prompts(session_id) > 0:
                raise ValueError(f"Session {session_id} has queued prompts")
            await self.end_session(session_id, force=force)

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

        container = (
            session.target.container
            if isinstance(session.target.container, dict)
            else {}
        )
        explicit_absence = (
            container.get("authoritative_identity_removed") is True
            or container.get("recreate_failed_without_host") is True
        )
        cleanup_pending = (
            session_id in self._container_lock_sessions
            or container.get("launch_pending_session_id") == session_id
        )
        recovery_inconclusive = session_id in self._remote_recovery_inconclusive
        if (
            (cleanup_pending or recovery_inconclusive)
            and self._host_index is not None
            and self._host_index.get(session_id) is None
            and not explicit_absence
        ):
            self._remote_recovery_inconclusive.discard(session_id)
            try:
                await self._recover_remote_host_records(
                    allow_wake=True,
                    session_ids={session_id},
                )
            except Exception as exc:
                self._remote_recovery_inconclusive.add(session_id)
                raise RemoteHostRecoveryPendingError(
                    "Remote Session Host cleanup could not inspect authority "
                    f"for {session_id}; retained session and remote ownership"
                ) from exc
            if (
                self._host_index.get(session_id) is None
                and session_id in self._remote_recovery_inconclusive
            ):
                raise RemoteHostRecoveryPendingError(
                    "Remote Session Host cleanup is inconclusive; retained "
                    f"session {session_id} and remote ownership"
                )
            if (
                self._host_index.get(session_id) is None
                and session_id not in self._remote_recovery_inconclusive
            ):
                self._set_container_launch_pending(session_id, False)
                container = (
                    session.target.container
                    if isinstance(session.target.container, dict)
                    else {}
                )

        await self._quiesce_session(session)

        # Session-Host mode: an explicit end is a *sanctioned terminate*, so it
        # must REAP the child -- unlike stop, whose host-mode shutdown only
        # detaches to keep the child reattachable. Without this the host + child
        # survive with a dangling index record and are never collected (#1786;
        # goal 1: termination is intentional, not inadvertent).
        rec = None
        if self._host_index is not None:
            rec = self._host_index.get(session_id)
            if rec is not None:
                container = (
                    session.target.container
                    if isinstance(session.target.container, dict)
                    else {}
                )
                if (
                    rec.boundary == "container"
                    and container.get("authoritative_identity_removed") is True
                ):
                    self._kill_forward_sync(session_id)
                    with contextlib.suppress(Exception):
                        self._host_index.remove(session_id)
                elif rec.boundary == "container":
                    self._kill_forward_sync(
                        session_id,
                        release_container_lock=False,
                    )
                    confirmed_dead = await self._remote_reap(
                        rec,
                        getattr(rec, "endpoint", None) or {},
                    )
                    if not confirmed_dead:
                        self._mark_session_failed(
                            session, trigger="remote_reap_inconclusive"
                        )
                        raise RemoteHostRecoveryPendingError(
                            "Container Session Host reap is inconclusive; "
                            f"retained session {session_id} and target ownership"
                        )
                    with contextlib.suppress(Exception):
                        self._host_index.remove(session_id)
                    with contextlib.suppress(Exception):
                        from . import bridge_lock
                        bridge_lock.remove_sync(session_id)
                    self._set_container_launch_pending(session_id, False)
                else:
                    self._reap_host_record(rec, "session ended")

        if (
            container
            and container.get("launch_pending_session_id") != session_id
            and (
                (self._host_index is not None and rec is None)
                or
                container.get("authoritative_identity_removed") is True
                or container.get("recreate_failed_without_host") is True
            )
        ):
            self._release_container_lock(session_id)

        session.status = SessionStatus.ENDED
        with contextlib.suppress(Exception):
            self._clear_pending_queue(session, reason="ended")
        # #897: release the exclusive CodeSpace claim this session held, so the
        # box is immediately re-dispatchable by another worktree instead of
        # waiting for the liveness/TTL sweep. Best-effort and idempotent (the
        # CLI only releases a claim this owner actually holds). Keyed off the
        # persisted target, so it is correct even after a daemon restart.
        with contextlib.suppress(Exception):
            claim_key = _codespace_claim_key(session.target)
            if claim_key is not None:
                core = _core()
                core._release_codespace_claim(*claim_key)
        with contextlib.suppress(Exception):
            self._db.update_session_status(
                session_id, SessionStatus.ENDED.value, time.time()
            )
        with contextlib.suppress(Exception):
            self._db.release_worktree_ownership(session_id=session_id)
        with contextlib.suppress(Exception):
            self._db.delete_session(session_id)
        self._sessions.pop(session_id, None)
        log.info("Session %s (%s) ended and cleaned up", session_id, session.name)

    # -- Context-aware in-place handoff --------------------------------------

