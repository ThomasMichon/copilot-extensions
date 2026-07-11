"""Live interactive-session registry endpoints -- /api/v1/live-sessions/*.

A live *interactive* Copilot CLI session is not owned by the bridge: the
bundled agent-bridge extension registers the session here so the bridge can
represent and (later) message it. Distinct from ``/api/v1/sessions`` (which
holds bridge-spawned ACP sessions). Liveness is heartbeat-based -- the
extension re-POSTs periodically to refresh ``updated_at``; an ungraceful exit
is reaped by staleness rather than relying on a clean deregister.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request

from ..models import (
    LiveSessionInfo,
    LiveSessionListResponse,
    RegisterLiveSessionRequest,
)

if TYPE_CHECKING:
    from ..db import Database

router = APIRouter(prefix="/api/v1/live-sessions", tags=["live-sessions"])


def _db(request: Request) -> "Database":
    db = getattr(request.app.state, "db", None)
    if db is None:
        raise HTTPException(status_code=503, detail="database not ready")
    return db


def _to_info(row: dict[str, Any]) -> LiveSessionInfo:
    return LiveSessionInfo(
        session_id=row["session_id"],
        machine=row.get("machine"),
        cwd=row.get("cwd"),
        worktree_id=row.get("worktree_id"),
        repo=row.get("repo"),
        branch=row.get("branch"),
        pid=row.get("pid"),
        role=row.get("role"),
        status=row.get("status") or "live",
        registered_at=row["registered_at"],
        updated_at=row["updated_at"],
    )


@router.post("", response_model=LiveSessionInfo)
async def register_live_session(
    body: RegisterLiveSessionRequest, request: Request
) -> LiveSessionInfo:
    """Register (or heartbeat-refresh) a live interactive CLI session.

    Idempotent: a re-POST for the same ``session_id`` upserts the row and
    refreshes ``updated_at``, which is how the extension heartbeats liveness.
    """
    db = _db(request)
    now = time.time()
    db.register_live_session(
        body.session_id,
        machine=body.machine,
        cwd=body.cwd,
        worktree_id=body.worktree_id,
        repo=body.repo,
        branch=body.branch,
        pid=body.pid,
        role=body.role,
        now=now,
    )
    row = db.get_live_session(body.session_id)
    if row is None:  # pragma: no cover -- write-then-read on the same connection
        raise HTTPException(status_code=500, detail="registration not persisted")
    return _to_info(row)


@router.get("", response_model=LiveSessionListResponse)
async def list_live_sessions(
    request: Request, worktree_id: str | None = None
) -> LiveSessionListResponse:
    """List registered live interactive CLI sessions (optionally by worktree)."""
    db = _db(request)
    rows = db.list_live_sessions(worktree_id=worktree_id)
    return LiveSessionListResponse(live_sessions=[_to_info(r) for r in rows])


@router.get("/{session_id}", response_model=LiveSessionInfo)
async def get_live_session(session_id: str, request: Request) -> LiveSessionInfo:
    """Fetch a single registered live interactive CLI session."""
    db = _db(request)
    row = db.get_live_session(session_id)
    if row is None:
        raise HTTPException(status_code=404, detail="live session not found")
    return _to_info(row)


@router.delete("/{session_id}")
async def deregister_live_session(
    session_id: str, request: Request
) -> dict[str, Any]:
    """Deregister a live interactive CLI session (best-effort on session exit).

    Deleting an unknown session_id is a no-op (idempotent), so a duplicate or
    late deregister never errors.
    """
    db = _db(request)
    db.deregister_live_session(session_id)
    return {"ok": True, "session_id": session_id}
