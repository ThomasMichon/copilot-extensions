"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

from ..protocol import HTTP_PROTOCOL_MIN_SUPPORTED, HTTP_PROTOCOL_VERSION

router = APIRouter()


@router.get("/health")
async def health(request: Request) -> dict:
    mgr = getattr(request.app.state, "session_manager", None)
    draining = bool(getattr(mgr, "is_draining", False)) if mgr else False
    # protocol_version / min_protocol_version advertise the HTTP wire-contract
    # version + supported range so a client can gate a version-introduced
    # capability on the daemon's support instead of blind-sending (dotfiles #632).
    # Additive: a client predating these fields simply ignores them.
    body = {
        "status": "ok",
        "service": "agent-bridge",
        "draining": draining,
        "protocol_version": HTTP_PROTOCOL_VERSION,
        "min_protocol_version": HTTP_PROTOCOL_MIN_SUPPORTED,
    }
    # When drained, surface *how long* and *why* so a stuck/aborted drain is
    # visible to monitoring without grepping logs (#1757).
    if draining and mgr is not None:
        drain_status = getattr(mgr, "drain_status", None)
        if callable(drain_status):
            body["drain"] = drain_status()
    return body
