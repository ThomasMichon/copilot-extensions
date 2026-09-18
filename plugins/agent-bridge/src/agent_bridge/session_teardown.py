"""Session lifecycle operations; transport factories remain manager injection seams."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .session_ownership import cleanup_owned_process, finish_owned

_SHUTDOWN_CLEANUP_RETRY_SECONDS = 5.0

if TYPE_CHECKING:
    from .session_manager import Session, SessionManager


async def detach_for_restart(manager: SessionManager) -> None:
    """Keep shutdown ownership until process cleanup is confirmed."""
    manager._shutting_down = True
    await finish_owned(_detach_for_restart_owned(manager))


async def _detach_for_restart_owned(manager: SessionManager) -> None:
    """Detach active sessions and close owned channels at frontend shutdown."""
    from .session_manager import asyncio, log

    for session in manager.list_sessions():
        if (
            manager._background_recovery_allowed(session)
            or session.client is not None
            or session._owned_process is not None
            or session._turn_start_lock.locked()
            or session._lifecycle_lock.locked()
            or manager._remote_reaps_by_session.get(session.session_id)
            or session.session_id in manager._pending_host_launches
        ):
            try:
                log.info("Detaching session %s on shutdown", session.session_id)
                await manager.stop_session(
                    session.session_id,
                    force=True,
                    cancel_turn=manager.cancel_turns_on_redeploy,
                    for_restart=True,
                    reap_host=session.session_id in manager._pending_host_launches,
                )
            except Exception:
                log.warning(
                    "Failed to stop session %s on shutdown",
                    session.session_id, exc_info=True,
                )
    while pending := [
        session for session in manager.list_sessions()
        if session._owned_process is not None
        or session.client is not None
        or session.session_id in manager._forwards
        or session.session_id in manager._relays
        or manager._remote_reap_pending(session.session_id)
        or (
            session.session_id in manager._pending_host_launches
            and not manager._pending_host_launches[session.session_id].durable
        )
    ]:
        log.error(
            "Shutdown blocked by unconfirmed cleanup for %s; retaining "
            "cleanup handles and database, retrying in %.1fs",
            ", ".join(session.session_id for session in pending),
            _SHUTDOWN_CLEANUP_RETRY_SECONDS,
        )
        await asyncio.sleep(_SHUTDOWN_CLEANUP_RETRY_SECONDS)
        for session in pending:
            try:
                await manager.stop_session(
                    session.session_id, force=True, for_restart=True,
                    cancel_turn=manager.cancel_turns_on_redeploy,
                    reap_host=(
                        session.session_id in manager._pending_host_launches
                        or manager._remote_reap_pending(session.session_id)
                    ),
                )
            except Exception:
                log.error(
                    "Shutdown process cleanup retry failed for %s",
                    session.session_id, exc_info=True,
                )
    while True:
        try:
            await manager.close_frontend_transports()
            break
        except Exception:
            log.error(
                "Shutdown blocked by frontend transport cleanup; retaining "
                "handles and database for retry", exc_info=True,
            )
            await asyncio.sleep(_SHUTDOWN_CLEANUP_RETRY_SECONDS)
    if manager._remote_reap_tasks:
        await finish_owned(asyncio.gather(
            *list(manager._remote_reap_tasks), return_exceptions=True,
        ))
    await retry_orphan_reaps(manager)


async def retry_orphan_reaps(manager: SessionManager) -> None:
    """Keep orphan cleanup ownership until termination and metadata removal succeed."""
    import copy

    from .session_manager import RemoteHostRecoveryPendingError, asyncio, log

    while orphan_ids := [
        sid for sid in manager._remote_reaps_by_session
        if sid not in manager._sessions and manager._remote_reap_pending(sid)
    ]:
        for session_id in orphan_ids:
            await join_remote_reaps(manager, session_id, retry_failed=True)
            if not manager._remote_reap_pending(session_id):
                continue
            record = manager._host_index.get(session_id) if manager._host_index is not None else None
            if record is None:
                raise RemoteHostRecoveryPendingError(
                    f"Orphan reap for {session_id} has no authority record; cleanup remains unconfirmed"
                )
            try:
                if await reap_remote_record(manager, copy.deepcopy(record)):
                    continue
                log.error("Orphan remote reap remains unconfirmed for %s; shutdown pending", session_id)
            except Exception:
                log.error("Orphan remote reap retry failed for %s; shutdown pending", session_id, exc_info=True)
        if any(manager._remote_reap_pending(sid) for sid in orphan_ids):
            await asyncio.sleep(_SHUTDOWN_CLEANUP_RETRY_SECONDS)


async def reap_remote_record(
    manager: SessionManager, record: Any, *,
    session: Session | None = None, generation: int | None = None,
    record_revision: int | None = None,
) -> bool:
    """Keep authority until termination is confirmed; never forget a replacement."""
    from .session_manager import log

    if record_revision is None and manager._host_index is not None:
        record_revision = manager._host_index.revision(record.session_id)
    endpoint = getattr(record, "endpoint", None) or {}
    if endpoint:
        confirmed = await manager._remote_reap(record, endpoint)
    else:
        log.warning("Cannot confirm remote reap without endpoint for %s", record.session_id)
        confirmed = False
    current = manager._host_index.get(record.session_id) if manager._host_index else None
    current_revision = manager._host_index.revision(record.session_id) if manager._host_index else None
    if confirmed:
        if current == record and current_revision == record_revision:
            from .session_host_ownership import finish_host_metadata_cleanup

            await manager._drop_forward(record.session_id, strict=True, preserve_ownership=True)
            if (
                manager._host_index is None
                or manager._host_index.get(record.session_id) != record
                or manager._host_index.revision(record.session_id) != record_revision
            ):
                return confirmed
            finish_host_metadata_cleanup(manager, record.session_id, record)
            owned = manager._remote_reaps_by_session.get(record.session_id)
            if owned is not None:
                owned.difference_update(task for task in list(owned) if task.done())
                if not owned and manager._remote_reaps_by_session.get(record.session_id) is owned:
                    manager._remote_reaps_by_session.pop(record.session_id, None)
    elif (
        session is not None
        and manager._sessions.get(record.session_id) is session
        and session._lifecycle_generation == generation
        and current == record
        and current_revision == record_revision
    ):
        manager._mark_session_failed(
            session, trigger="remote_reap_inconclusive",
            restart_status=session.restart_status,
        )
    return confirmed


async def reap_remote_checked(manager: SessionManager, session: Session, record: Any) -> None:
    """Surface unconfirmed remote termination with its authority record retained."""
    from .session_manager import RemoteHostRecoveryPendingError

    if not await reap_remote_record(
        manager, record, session=session, generation=session._lifecycle_generation,
    ):
        raise RemoteHostRecoveryPendingError(
            f"Remote Session Host reap is inconclusive for {session.session_id}; "
            "authority and ownership retained"
        )


async def join_remote_reaps(
    manager: SessionManager, session_id: str, *, retry_failed: bool = False,
) -> None:
    """Join existing reaps before any competing teardown or explicit retry."""
    from .session_manager import RemoteHostRecoveryPendingError, asyncio

    pending = list(manager._remote_reaps_by_session.get(session_id, ()))
    if pending:
        results = await asyncio.gather(*pending, return_exceptions=True)
        if not retry_failed and any(result is not True for result in results):
            raise RemoteHostRecoveryPendingError(
                f"Pending remote reap is inconclusive for {session_id}; ownership retained"
            )


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
            prior = (
                session.restart_status
                if session.status in {SessionStatus.STOPPED, SessionStatus.FAILED}
                else session.status.value
            )
            if prior in (
                SessionStatus.RUNNING.value, SessionStatus.IDLE.value,
                SessionStatus.STARTING.value,
            ):
                restart_status = prior
        async def teardown() -> None:
            try:
                await complete_stop(
                    self, session, restart_status=restart_status,
                    reap_host=reap_host, cancel_turn=cancel_turn,
                    for_restart=for_restart,
                )
            except Exception:
                self._mark_session_failed(
                    session, trigger="stop_cleanup_failed", restart_status=restart_status,
                )
                log.error("Stop cleanup failed for %s; ownership retained", session_id, exc_info=True)
                raise

        await finish_owned(teardown())


async def complete_stop(
    manager: SessionManager, session: Session, *, restart_status: str | None,
    reap_host: bool, cancel_turn: bool, for_restart: bool,
) -> None:
    """Finish every teardown stage and durable transition under retained ownership."""
    from .session_manager import RemoteHostRecoveryPendingError, SessionStatus, asyncio, log, time

    session_id = session.session_id
    try:
        await manager._quiesce_session(session, cancel_turn=cancel_turn)
        await cleanup_owned_process(session)
    finally:
        await manager._drop_forward(session_id, strict=True, preserve_ownership=True)
    await join_remote_reaps(manager, session_id, retry_failed=reap_host)
    if not for_restart and manager._host_index is not None:
        manager._host_index.set_resume_flag(session_id, False)
    if reap_host and session_id in manager._pending_host_launches:
        from .session_host_ownership import abort_pending_host_launch

        await abort_pending_host_launch(manager, session_id)
    if reap_host and manager._host_index is not None:
        record = manager._host_index.get(session_id)
        if record is not None:
            if getattr(record, "boundary", "local") == "local":
                manager._reap_host_record(record, "idle reap (#1826)")
            else:
                await reap_remote_checked(manager, session, record)
    pending = list(manager._remote_reaps_by_session.get(session_id, ()))
    if pending:
        if not all(await asyncio.gather(*pending)):
            raise RemoteHostRecoveryPendingError(
                f"Pending remote reap is inconclusive for {session_id}; ownership retained"
            )
    if reap_host:
        from .session_host_ownership import has_host_ownership, require_no_pending_host_launch

        require_no_pending_host_launch(manager, session_id)
        if not has_host_ownership(manager, session_id):
            manager._release_container_lock(session_id)
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

    await join_remote_reaps(self, session_id)
    session._lifecycle_generation += 1
    if session_id in self._pending_host_launches:
        from .session_host_ownership import abort_pending_host_launch

        await abort_pending_host_launch(self, session_id)
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
                from .session_host_ownership import finish_host_metadata_cleanup

                await self._drop_forward(session_id, strict=True, preserve_ownership=True)
                if not finish_host_metadata_cleanup(self, session_id, rec):
                    raise RemoteHostRecoveryPendingError(
                        f"Host authority changed during end for {session_id}; ownership retained"
                    )
            elif rec.boundary != "local":
                await self._drop_forward(session_id, strict=True, preserve_ownership=True)
                await reap_remote_checked(self, session, rec)
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
