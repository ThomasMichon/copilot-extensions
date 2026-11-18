"""Determine when a frontend started passive may maintain shared sessions."""

from __future__ import annotations

import asyncio
import os
import sqlite3
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from single_instance_lease.supersession import pid_alive
from zdd import process_exit, routing
from zdd.claims import ClaimConflict
from zdd.diagnostics import process_start_time

from .config import config_dir
from .session_status import SessionStatus

if TYPE_CHECKING:
    from .session_manager import SessionManager


def may_refresh_inherited(manager: SessionManager, row: dict[str, Any]) -> bool:
    """Terminal/dormant metadata needs no transport. Otherwise acquire the Host."""
    status = row["status"]
    if status in {SessionStatus.ENDED.value, SessionStatus.FAILED.value} or (
        status == SessionStatus.STOPPED.value and (
            row.get("runtime_custody_json") is not None or not row.get("background_recovery_enabled", 1)
        )
    ):
        return True
    if row.get("runtime_custody_json") is not None or manager.db.retained_recovery_held(row["id"]):
        return False
    try:
        record = manager._host_index.claim(
            row["id"], generation=manager._generation_id, owner_pid=os.getpid(),
            pid_alive=pid_alive, require_owner_fields=True,
        )
        if record.boundary == "local":
            # Missing or ambiguous native evidence is neither admission nor exit.
            exited = [
                process_exit.observe_original_exit(
                    getattr(record, name + "_pid"), record.extra.get(name + "_process_start"),
                ) for name in ("host", "child")
            ]
            if any(exited):
                if all(exited):
                    manager._host_index.release_exited_local(
                        record, settle=lambda: manager.db.settle_exited_inherited_host(row, record.child_pid),
                    )
                # Even on settlement this read predates STOPPED; refresh it on the next pass.
                return False
    except (ClaimConflict, KeyError):
        return False
    except (OSError, ValueError, NotImplementedError) as exc:
        logging.getLogger("agent-bridge").warning("Deferring inherited session %s: %s", row["id"], exc)
        return False
    return True


def refresh_inherited_sessions(manager: SessionManager) -> None:
    """Run on the manager's event loop without yielding between refresh and admission."""
    pending = [s for s in manager.list_sessions() if s.awaiting_maintenance_refresh]
    if not pending:
        return
    manager._host_index.refresh(strict=True)
    for session in pending:
        manager._rehydrate(merge=True, session_id=session.session_id, promote_inherited=True)


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
