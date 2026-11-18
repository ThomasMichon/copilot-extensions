"""Determine when a frontend started passive may maintain shared sessions."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import logging
from pathlib import Path

from fastapi import FastAPI
from zdd import process_exit, routing
from zdd.diagnostics import process_start_time

from .config import config_dir
from .session_manager import SessionManager


def refresh_inherited_sessions(manager: SessionManager) -> None:
    """Run on the manager's event loop without yielding between refresh and admission."""
    pending = [s for s in manager.list_sessions() if s.awaiting_maintenance_refresh]
    if not pending:
        return
    manager._host_index.refresh(strict=True)
    for session in pending:
        if manager._session_metadata_in_use(session):
            continue
        if manager.db.get_session(session.session_id) is None:
            continue
        manager._rehydrate(merge=True, session_id=session.session_id)
        session.awaiting_maintenance_refresh = False


async def passive_maintenance_ready(app: FastAPI) -> bool:
    if not getattr(app.state, "passive", False):
        return True
    owns_route = await asyncio.to_thread(
        owns_session_maintenance, Path(config_dir()), getattr(app.state, "bound_port", None),
    )
    if owns_route:
        try:
            refresh_inherited_sessions(app.state.session_manager)
        except (OSError, ValueError, sqlite3.Error):
            logging.getLogger("agent-bridge").warning("Passive session refresh deferred", exc_info=True)
            return False
    return owns_route


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
