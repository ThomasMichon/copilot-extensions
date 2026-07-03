"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    mgr = getattr(request.app.state, "session_manager", None)
    draining = bool(getattr(mgr, "is_draining", False)) if mgr else False
    body = {"status": "ok", "service": "agent-bridge", "draining": draining}
    # When drained, surface *how long* and *why* so a stuck/aborted drain is
    # visible to monitoring without grepping logs (#1757).
    if draining and mgr is not None:
        drain_status = getattr(mgr, "drain_status", None)
        if callable(drain_status):
            body["drain"] = drain_status()
    return body
