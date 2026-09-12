"""Ownership transfer for a Session Host before its ACP client is installed."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Awaitable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from .session_host.host_index import HostRecord
from .session_host.spawner import RemoteSpawnCleanupPendingError
from .session_ownership import finish_owned

if TYPE_CHECKING:
    from .acp_client import AcpClient
    from .session_host.spawner import SpawnedHost
    from .session_manager import SessionManager

log = logging.getLogger("agent-bridge")


@dataclass
class PendingHostLaunch:
    spawner: Any
    spawned: SpawnedHost
    record: HostRecord
    sock: Any = None
    streams: Any = None
    client: AcpClient | None = None


def require_no_pending_host_launch(manager: SessionManager, session_id: str) -> None:
    """Refuse another launch until partial-host cleanup is resolved."""
    record = manager._host_index.get(session_id) if manager._host_index is not None else None
    session = manager._sessions.get(session_id)
    container = session.target.container if session is not None else None
    if (
        isinstance(container, dict)
        and container.get("launch_pending_session_id") == session_id
    ) or session_id in manager._pending_host_launches or (
        record is not None and (record.extra or {}).get("launch_cleanup_pending")
    ):
        raise RemoteSpawnCleanupPendingError(
            f"Session Host launch cleanup is pending for {session_id}; "
            "retained authority must be cleaned up before another launch"
        )


def has_host_ownership(manager: SessionManager, session_id: str) -> bool:
    """Whether a host or durable partial-launch marker still owns the venue."""
    if session_id in manager._pending_host_launches:
        return True
    if manager._host_index is not None and manager._host_index.get(session_id) is not None:
        return True
    session = manager._sessions.get(session_id)
    container = session.target.container if session is not None else None
    return (
        isinstance(container, dict)
        and container.get("launch_pending_session_id") == session_id
    )


def _remember(
    manager: SessionManager, session_id: str, spawner: Any, spawned: SpawnedHost,
) -> PendingHostLaunch:
    from . import __version__

    record = HostRecord(
        session_id=session_id, port=spawned.local_port,
        host_pid=spawned.host_pid, child_pid=spawned.child_pid,
        host_version=__version__, protocol_version=spawned.protocol_version,
        state_file=spawned.state_file, created_at=time.time(), nonce=spawned.nonce,
        boundary=spawned.boundary, endpoint=getattr(spawned, "endpoint", None) or {},
        extra={
            "remote_authority_v2": spawned.boundary != "local",
            "launch_cleanup_pending": True,
        },
    )
    pending = PendingHostLaunch(spawner, spawned, record)
    manager._pending_host_launches[session_id] = pending
    if getattr(spawned, "forward", None) is not None:
        manager._forwards[session_id] = spawned.forward
    relays = getattr(spawned, "relay", None)
    if relays is not None:
        manager._relays[session_id] = (
            list(relays) if isinstance(relays, (list, tuple, set)) else [relays]
        )
    try:
        if manager._host_index is not None:
            manager._host_index.register(record)
        manager._set_container_launch_pending(session_id, True)
    except Exception as exc:
        log.error("Could not persist spawned-host ownership for %s", session_id, exc_info=True)
        raise RemoteSpawnCleanupPendingError(
            f"Spawned-host ownership could not be persisted for {session_id}; "
            "in-memory cleanup handle retained"
        ) from exc
    return pending


async def spawn_host_owned(
    manager: SessionManager, session_id: str, spawner: Any,
    operation: Awaitable[SpawnedHost],
) -> PendingHostLaunch:
    """Join spawn and retain its result even when cancellation wins delivery."""
    task = asyncio.ensure_future(operation)
    try:
        spawned = await finish_owned(task)
    except asyncio.CancelledError:
        if task.done() and not task.cancelled() and task.exception() is None:
            _remember(manager, session_id, spawner, task.result())
        raise
    return _remember(manager, session_id, spawner, spawned)


def complete_host_launch(manager: SessionManager, session_id: str) -> None:
    """Commit a fully initialized host before handing its client to the caller."""
    pending = manager._pending_host_launches[session_id]
    extra = dict(pending.record.extra)
    extra.pop("launch_cleanup_pending", None)
    completed = replace(pending.record, extra=extra)
    if manager._host_index is not None:
        manager._host_index.register(completed)
    pending.record = completed
    manager._set_container_launch_pending(session_id, False)
    manager._pending_host_launches.pop(session_id, None)


async def abort_pending_host_launch(manager: SessionManager, session_id: str) -> None:
    """Abort a partial host or retain its authority for explicit cleanup retry."""
    pending = manager._pending_host_launches.get(session_id)
    if pending is None:
        return
    if pending.client is not None:
        try:
            await pending.client.shutdown()
        except Exception:
            log.warning("Partial host client shutdown failed for %s", session_id, exc_info=True)
    confirmed = await manager._rollback_failed_host_launch(
        pending.spawner, pending.spawned, pending.sock, pending.streams, session_id, None,
    )
    if not confirmed:
        manager._remote_recovery_inconclusive.add(session_id)
        raise RemoteSpawnCleanupPendingError(
            f"Session Host launch cleanup is inconclusive for {session_id}; "
            "authority and cleanup handle retained"
        )


async def rollback_host_launch(
    manager: SessionManager, spawner: Any, spawned: SpawnedHost,
    sock: Any, streams: Any, session_id: str, result: dict | None,
) -> bool:
    """Abort a known launch and retain its record if termination is inconclusive."""
    if sock is not None:
        with contextlib.suppress(Exception):
            await sock.terminate()
    if streams is not None:
        with contextlib.suppress(Exception):
            await streams.aclose()
    if sock is not None:
        with contextlib.suppress(Exception):
            await sock.close()
    remote = getattr(spawned, "boundary", "local") != "local"
    confirmed = False
    if remote:
        abort = getattr(spawner, "abort_spawned", None)
        if callable(abort):
            try:
                confirmed = bool(await abort(spawned, session_id))
            except Exception:
                log.warning("Host abort failed for %s; authority retained", session_id, exc_info=True)
    else:
        if getattr(spawned, "proc", None) is not None:
            await asyncio.to_thread(manager._terminate_local_host_processes, spawned)
        confirmed = not manager._rec_host_alive(spawned) and not manager._rec_child_alive(spawned)
    try:
        await spawned.aclose()
        await manager._drop_forward(session_id, strict=True, preserve_ownership=True)
    except Exception:
        log.warning("Host launch channel cleanup failed for %s", session_id, exc_info=True)
        confirmed = False
    if confirmed:
        pending = manager._pending_host_launches.get(session_id)
        if pending is not None:
            if manager._host_index is not None:
                current = manager._host_index.get(session_id)
                if current == pending.record:
                    manager._host_index.remove(session_id)
            manager._set_container_launch_pending(session_id, False)
            manager._pending_host_launches.pop(session_id, None)
        manager._remote_recovery_inconclusive.discard(session_id)
    if result is not None:
        result.update({
            "host_process_removed": confirmed, "child_process_removed": confirmed,
            "remote_authority_removed": confirmed,
            "forward_removed": session_id not in manager._forwards,
            "relay_removed": session_id not in manager._relays,
        })
    return confirmed
