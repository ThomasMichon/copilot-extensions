"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter, Request

from .. import __version__
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
        "version": __version__,
        "ready": bool(getattr(request.app.state, "ready", False)),
        "topology_ready": bool(
            getattr(request.app.state, "topology_ready", False)
        ),
        "credential_relay_ready": bool(
            getattr(request.app.state, "credential_relay_ready", False)
        ),
        "draining": draining,
        "protocol_version": HTTP_PROTOCOL_VERSION,
        "min_protocol_version": HTTP_PROTOCOL_MIN_SUPPORTED,
    }
    readiness_error = getattr(request.app.state, "readiness_error", None)
    if readiness_error:
        body["readiness_error"] = readiness_error
    # Live Session Host census (dotfiles#1656): how many independent Session
    # Hosts (each owning a possibly-mid-turn child that survives a frontend
    # restart) this daemon is fronting. Always surfaced so a drain/cutover is
    # never judged "clean" while live hosts it must preserve go unaccounted for.
    if mgr is not None and hasattr(mgr, "live_host_count"):
        body["live_host_count"] = mgr.live_host_count
    resolver = getattr(request.app.state, "resolver", None)
    if resolver is not None:
        errors = getattr(resolver, "topology_errors", [])
        warnings = getattr(resolver, "topology_warnings", [])
        body["topology_error_count"] = len(errors) if isinstance(errors, list) else 0
        body["topology_warning_count"] = (
            len(warnings) if isinstance(warnings, list) else 0
        )
    # When drained, surface *how long* and *why* so a stuck/aborted drain is
    # visible to monitoring without grepping logs (#1757).
    if draining and mgr is not None:
        drain_status = getattr(mgr, "drain_status", None)
        if callable(drain_status):
            body["drain"] = drain_status()
    return body
