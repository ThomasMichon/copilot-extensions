"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    mgr = getattr(request.app.state, "session_manager", None)
    draining = bool(getattr(mgr, "is_draining", False)) if mgr else False
    return {"status": "ok", "service": "agent-bridge", "draining": draining}
