"""Cancellation-safe lifecycle jobs and process-owned session cleanup."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable
from typing import TYPE_CHECKING, TypeVar

from .models import SessionStatus

if TYPE_CHECKING:
    from .acp_client import AcpClient
    from .session_manager import Session, SessionManager
    from .transport import AgentProcess

log = logging.getLogger("agent-bridge")
T = TypeVar("T")


async def finish_owned(operation: Awaitable[T], *, propagate_cancel: bool = True) -> T:
    """Join owned work despite repeated caller cancellation, then propagate it."""
    task = asyncio.ensure_future(operation)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise
            cancelled = True
        except Exception as exc:
            if cancelled and propagate_cancel:
                log.error("Owned lifecycle work failed during cancellation", exc_info=True)
                raise asyncio.CancelledError() from exc
            raise
    if cancelled and propagate_cancel:
        raise asyncio.CancelledError
    return result


async def cleanup_owned_process(session: Session) -> None:
    """Reap and verify the retained process; keep its handle if cleanup fails."""
    process = session._owned_process
    if process is None:
        return
    if process.alive:
        await process.kill()
        if process.alive:
            await asyncio.wait_for(process.proc.wait(), timeout=5.0)
    if process.alive:
        raise RuntimeError(
            f"Process cleanup did not confirm exit for session {session.session_id} "
            f"(pid={process.pid}); ownership retained"
        )
    if session._owned_process is process:
        session._owned_process = None


async def spawn_owned(
    session: Session, operation: Awaitable[AgentProcess],
) -> AgentProcess:
    """Retain a completed spawn even if its caller cancels before receiving it."""
    task = asyncio.ensure_future(operation)
    try:
        process = await finish_owned(task)
    except asyncio.CancelledError:
        if task.done() and not task.cancelled() and task.exception() is None:
            session._owned_process = task.result()
        raise
    session._owned_process = process
    return process


async def cleanup_resume_attempt(
    manager: SessionManager, session: Session, client: AcpClient | None,
) -> None:
    """Contain failed attempt resources without discarding cleanup retry handles."""
    from .session_host_ownership import abort_pending_host_launch, has_host_ownership

    if client is None:
        client = session.client
    try:
        try:
            if client is not None:
                session.client = client
                await client.shutdown(strict=True)
                session.client = None
        finally:
            try:
                await cleanup_owned_process(session)
                await abort_pending_host_launch(manager, session.session_id)
            finally:
                await manager._drop_forward(
                    session.session_id, strict=True, preserve_ownership=True,
                )
        await release_unowned_launch_claim(manager, session)
        if not has_host_ownership(manager, session.session_id):
            manager._release_container_lock(session.session_id)
    except Exception:
        manager._mark_session_failed(session, trigger="resume_cleanup_failed")
        log.error("Resume ownership cleanup failed for %s", session.session_id, exc_info=True)
        raise


async def release_unowned_launch_claim(manager: SessionManager, session: Session) -> None:
    """Release only this failed launch's claim after its host ownership is gone."""
    from .session_host_ownership import has_host_ownership
    from .session_manager import _codespace_claim_key, _release_codespace_claim

    key = session._launch_codespace_claim
    if key is None or has_host_ownership(manager, session.session_id):
        return
    for other in manager.list_sessions():
        if other is not session and (
            other._launch_codespace_claim == key
            or (_codespace_claim_key(other.target) == key and has_host_ownership(manager, other.session_id))
        ):
            session._launch_codespace_claim = None
            return
    if not await asyncio.to_thread(_release_codespace_claim, *key):
        raise RuntimeError(
            f"Launch claim cleanup failed for {session.session_id}; ownership retained"
        )
    session._launch_codespace_claim = None


async def settle_cancelled_resume(
    manager: SessionManager, session: Session, client: AcpClient | None,
    *, trigger: str = "resume_cancelled",
) -> None:
    """Complete cancellation cleanup before publishing a resumable stopped row."""
    try:
        await cleanup_resume_attempt(manager, session, client)
    except Exception:
        manager._mark_session_failed(session, trigger="resume_cleanup_failed")
        raise
    session.status = SessionStatus.STOPPED
    session.restart_status = None
    manager.db.update_session_status(
        session.session_id, SessionStatus.STOPPED.value, time.time(),
    )
    if session.event_log:
        session.event_log.append("session_state_changed", {
            "status": SessionStatus.STOPPED.value, "trigger": trigger,
        })


async def cleanup_failed_resume(
    manager: SessionManager, session: Session, client: AcpClient | None,
) -> None:
    """Reap a failed attempt before retry, including cancellation during cleanup."""
    try:
        await finish_owned(cleanup_resume_attempt(manager, session, client))
    except asyncio.CancelledError:
        await finish_owned(
            settle_cancelled_resume(manager, session, None),
            propagate_cancel=False,
        )
        raise
