"""Session lifecycle operations; transport factories remain manager injection seams."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .session_ownership import cleanup_owned_process, finish_owned

if TYPE_CHECKING:
    from .session_manager import Session, SessionManager


async def detach_for_restart(manager: SessionManager) -> None:
    """Detach active sessions and close owned channels at frontend shutdown."""
    from .session_manager import log

    for session in manager.list_sessions():
        if session.client and session.client.is_running:
            try:
                log.info("Detaching session %s on shutdown", session.session_id)
                await manager.stop_session(
                    session.session_id,
                    cancel_turn=manager.cancel_turns_on_redeploy,
                    for_restart=True,
                )
            except Exception:
                log.warning(
                    "Failed to stop session %s on shutdown",
                    session.session_id, exc_info=True,
                )
    try:
        await manager.close_frontend_transports()
    except Exception:
        log.warning("Failed to close frontend transports on shutdown", exc_info=True)


async def stop_session_admitted(
    self: SessionManager, session: Session, *, force: bool = False, reap_host: bool = False,
    cancel_turn: bool = True, for_restart: bool = False,
) -> None:
    """Stop while the caller already owns this session's turn admission."""

    from .session_manager import (
        SessionBusyError,
        SessionStatus,
        log,
    )

    session_id = session.session_id
    async with session._lifecycle_lock:
        if self._sessions.get(session_id) is not session:
            raise KeyError(f"Session {session_id} not found")
        if not force and session.has_active_background_tasks:
            raise SessionBusyError(session_id, session.active_background_tasks)

        session._lifecycle_generation += 1
        restart_status = None
        if for_restart:
            restart_status = (
                session.restart_status
                if session.status == SessionStatus.STOPPED
                else session.status.value
            )
        async def teardown() -> None:
            try:
                await complete_stop(
                    self, session, restart_status=restart_status,
                    reap_host=reap_host, cancel_turn=cancel_turn,
                    for_restart=for_restart,
                )
            except Exception:
                session.restart_status = None
                self._mark_session_failed(session, trigger="stop_cleanup_failed")
                log.error("Stop cleanup failed for %s; ownership retained", session_id, exc_info=True)
                raise

        await finish_owned(teardown())


async def complete_stop(
    manager: SessionManager, session: Session, *, restart_status: str | None,
    reap_host: bool, cancel_turn: bool, for_restart: bool,
) -> None:
    """Finish every teardown stage and durable transition under retained ownership."""
    from .session_manager import SessionStatus, asyncio, log, time

    session_id = session.session_id
    await manager._quiesce_session(session, cancel_turn=cancel_turn)
    await cleanup_owned_process(session)
    await manager._drop_forward(session_id, strict=True, preserve_ownership=True)
    if not for_restart and manager._host_index is not None:
        manager._host_index.set_resume_flag(session_id, False)
    if reap_host and manager._host_index is not None:
        record = manager._host_index.get(session_id)
        if record is not None:
            manager._reap_host_record(record, "idle reap (#1826)")
    pending = list(manager._remote_reaps_by_session.get(session_id, ()))
    if pending:
        await asyncio.gather(*pending)
    session.status = SessionStatus.STOPPED
    session.restart_status = restart_status
    manager.db.update_session_status(
        session_id, SessionStatus.STOPPED.value, time.time(),
        restart_status=restart_status,
    )
    manager.db.release_worktree_ownership(session_id=session_id)
    if session.event_log:
        session.event_log.append("session_state_changed", {
            "status": SessionStatus.STOPPED.value,
        })
    session.touch()
    log.info("Session %s (%s) stopped", session_id, session.name)


async def end_session_locked(self: SessionManager, session: Session, *, force: bool = False) -> None:
    """End under admission/lifecycle ownership, including authority recovery."""

    from .session_manager import (
        RemoteHostRecoveryPendingError,
        SessionBusyError,
        SessionStatus,
        _codespace_claim_key,
        _release_codespace_claim,
        contextlib,
        log,
        time,
    )

    session_id = session.session_id
    if self._sessions.get(session_id) is not session:
        raise KeyError(f"Session {session_id} not found")
    if not force and session.has_active_background_tasks:
        raise SessionBusyError(session_id, session.active_background_tasks)

    session._lifecycle_generation += 1
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
    await cleanup_owned_process(session)

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
            _release_codespace_claim(*claim_key)
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
