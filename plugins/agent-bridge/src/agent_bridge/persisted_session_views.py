"""Read-only public rendering of persisted elevated session records."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .dispatch_origin import inspect as inspect_dispatch_origin
from .models import SessionInfo, SessionStatus
from .transport import SpawnTarget


def persisted_session_info(row: dict[str, Any], *, daemon_running: bool) -> SessionInfo:
    target_json = row.get("target_json")
    target = (
        SpawnTarget.from_json(target_json)
        if target_json
        else SpawnTarget(
            type=row.get("target_type", "local"),
            cwd=row.get("target_dir", "."),
        )
    )
    session_status = SessionStatus(row["status"])
    if not daemon_running and session_status in {
        SessionStatus.CREATED,
        SessionStatus.STARTING,
        SessionStatus.RUNNING,
        SessionStatus.IDLE,
        SessionStatus.STOPPING,
    }:
        session_status = SessionStatus.STOPPED

    context_size = row.get("context_size")
    context_used = row.get("context_used")
    context_pct = (
        round(context_used / context_size * 100, 1)
        if context_size and context_used is not None
        else None
    )
    last_usage_at = row.get("last_usage_at")
    return SessionInfo(
        session_id=row["id"],
        name=row["name"],
        agent_name=row.get("agent_name"),
        caller_id=row.get("caller_id"), caller_session_id=row.get("caller_session_id"),
        acp_session_id=row.get("acp_session_id"),
        durable_session_id=row.get("acp_session_id") or row["id"],
        target_dir=target.cwd,
        target_type=target.type,
        target_host=target.host,
        project=getattr(target, "project", None),
        worktree_id=target.worktree_id,
        dispatch_origin=inspect_dispatch_origin(row),
        elevated=True,
        read_only=True,
        status=session_status,
        pid=row.get("pid") if daemon_running else None,
        turn_count=row.get("turn_count", 0),
        context_size=context_size,
        context_used=context_used,
        context_pct=context_pct,
        usage_model=row.get("usage_model"),
        last_usage_at=(
            datetime.fromtimestamp(last_usage_at, tz=timezone.utc).isoformat()
            if last_usage_at else None
        ),
        created_at=datetime.fromtimestamp(row["created_at"], tz=timezone.utc),
        updated_at=datetime.fromtimestamp(row["updated_at"], tz=timezone.utc),
    )
