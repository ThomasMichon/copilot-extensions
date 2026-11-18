"""Determine when a frontend started passive may maintain shared sessions."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from fastapi import FastAPI
from zdd import process_exit, routing
from zdd.diagnostics import process_start_time

from .config import config_dir


async def passive_maintenance_ready(app: FastAPI) -> bool:
    if not getattr(app.state, "passive", False):
        return True
    return await asyncio.to_thread(
        owns_session_maintenance, Path(config_dir()), getattr(app.state, "bound_port", None),
    )


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
