"""Determine when a frontend started passive may maintain shared sessions."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import logging
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from single_instance_lease import AlreadyRunningError
from single_instance_lease.supersession import pid_alive
from zdd import process_exit, routing
from zdd.claims import ClaimConflict
from zdd.diagnostics import process_start_time

from .app_lifecycle import settled_thread_call
from .config import config_dir
from .db import Database
from .passive_refresh import InheritedRefresh
from .session_host.host_index import HostIndex
from .session_status import SessionStatus

if TYPE_CHECKING:
    from .session_manager import SessionManager


def may_refresh_inherited(db: Database, index: HostIndex, generation: str, row: dict[str, Any]) -> bool:
    """Terminal/dormant metadata needs no transport. Otherwise acquire the Host."""
    status = row["status"]
    if status in {SessionStatus.ENDED.value, SessionStatus.FAILED.value} or (
        status == SessionStatus.STOPPED.value and (
            row.get("runtime_custody_json") is not None or not row.get("background_recovery_enabled", 1)
        )
    ):
        return True
    if row.get("runtime_custody_json") is not None or db.retained_recovery_held(row["id"]):
        return False
    try:
        record = index.claim(
            row["id"], generation=generation, owner_pid=os.getpid(),
            pid_alive=pid_alive, require_owner_fields=True,
        )
        if record.boundary == "local":
            # Missing or ambiguous native evidence is neither admission nor exit.
            exited = []
            for name in ("host", "child"):
                birth = record.extra.get(name + "_process_start")
                if not isinstance(birth, str) or not birth:
                    return False
                exited.append(process_exit.observe_original_exit(getattr(record, name + "_pid"), birth))
            if any(exited):
                if all(exited):
                    index.release_exited_local(
                        record, settle=lambda: db.settle_exited_inherited_host(row, record.child_pid),
                    )
                # Even on settlement this read predates STOPPED; refresh it on the next pass.
                return False
    except (ClaimConflict, KeyError):
        return False
    except (OSError, ValueError, NotImplementedError) as exc:
        logging.getLogger("agent-bridge").warning("Deferring inherited session %s: %s", row["id"], exc)
        return False
    return True


def _prepare(
    db: Database, index: HostIndex, generation: str, sid: str, route: Callable[[], bool],
) -> InheritedRefresh | None:
    if not route():
        return None
    index.refresh(strict=True)
    row = db.get_session(sid)
    if row is None:
        return InheritedRefresh(None, index.get(sid)) if route() else None
    if not may_refresh_inherited(db, index, generation, row):
        return None
    host = index.get(sid)
    result = InheritedRefresh.read(db, row, host)
    index.refresh(strict=True)
    return result if host == index.get(sid) and route() else None


async def refresh_inherited_sessions(manager: SessionManager, route: Callable[[], bool]) -> None:
    index: HostIndex = manager._host_index
    for session in manager.list_sessions():
        if not session.awaiting_maintenance_refresh or session.metadata_in_use(include_readers=False):
            continue
        sid = session.session_id
        expected = index.get(sid)
        stop_notices = session._stop_notices
        try:
            # Hold only this session; cancellation waits for the admitted worker before unlocking.
            async with session._turn_start_lock, session._lifecycle_lock:
                prepared = await settled_thread_call(
                    _prepare, manager.db, index.independent_cache(), manager._generation_id, sid, route,
                )
        except (AlreadyRunningError, OSError, ValueError, sqlite3.Error):
            logging.getLogger("agent-bridge").warning("Session %s refresh deferred", sid, exc_info=True)
            continue
        # No await between checking local ownership, publishing the projection and admission.
        if (prepared is not None and manager.get_session(sid) is session and session.awaiting_maintenance_refresh
                and session._stop_notices == stop_notices and not session.metadata_in_use(include_readers=False)
                and index.accept_prepared(sid, expected, prepared.host)):
            manager._rehydrate(merge=True, session_id=sid, prepared=prepared)


async def passive_maintenance_ready(app: FastAPI) -> bool:
    if not getattr(app.state, "passive", False):
        return True
    root, port = Path(config_dir()), getattr(app.state, "bound_port", None)
    def route() -> bool:
        return owns_session_maintenance(root, port)
    if not await asyncio.to_thread(route):
        return False
    await refresh_inherited_sessions(app.state.session_manager, route)
    return await asyncio.to_thread(route)


def owns_session_maintenance(root: Path, port: int | None) -> bool:
    """A route flip precedes retirement; do not recover during that interval."""
    try:
        table = routing.read_table(root, strict=True)
        if not table:
            return False
        raw = table.get("active")
        active = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        pid = os.getpid()
        if (
            active is None or active.pid != pid or active.port != port
            or not active.process_start_time or active.process_start_time != process_start_time(pid)
        ):
            return False
        if "previous" not in table:
            return True
        raw = table["previous"]
        previous = routing.Endpoint.from_dict(raw) if isinstance(raw, dict) else None
        if previous is None or previous.pid is None or not previous.process_start_time:
            return False
        return process_exit.observe_original_exit(previous.pid, previous.process_start_time)
    except (OSError, ValueError, NotImplementedError):
        return False
