"""Admin / maintenance endpoints -- /api/v1/*."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request

if TYPE_CHECKING:
    from ..session_manager import SessionManager

router = APIRouter(prefix="/api/v1", tags=["admin"])


@router.post("/gc")
async def run_gc(request: Request):
    """Run a garbage-collection sweep: prune aged terminal/disconnected
    sessions and compact the DB. Returns the GC summary."""
    mgr: SessionManager = request.app.state.session_manager
    # gc() is synchronous (SQLite + VACUUM) -- run it off the event loop so a
    # large VACUUM can't block request handling.
    return await asyncio.to_thread(mgr.gc, reason="manual")


@router.post("/drain")
async def drain(request: Request):
    """Open the drain gate and wait for in-flight work to settle.

    Refuses new sessions/turns immediately, then blocks until no session is
    actively streaming a turn or hosting background sub-agents (the dev57 busy
    oracle), or until ``timeout`` seconds elapse. This is the app-level,
    OS-agnostic primitive a zero-downtime redeploy calls before swapping the
    daemon, so an active turn is never hard-killed.

    Body (all optional): ``{"timeout": 300, "poll": 1.0, "force": false}``.
    """
    mgr: SessionManager = request.app.state.session_manager
    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        timeout = float(body.get("timeout", 300.0))
        poll = float(body.get("poll", 1.0))
    except (TypeError, ValueError):
        timeout, poll = 300.0, 1.0
    force = bool(body.get("force", False))
    # Optional caller-supplied provenance so a cutover-driven drain is
    # distinguishable from a manual one in logs/health (#1757).
    source = body.get("source") or "drain-endpoint"
    reason = body.get("reason")
    return await mgr.drain(
        timeout=timeout, poll=max(0.05, poll), force=force,
        source=str(source), reason=(str(reason) if reason is not None else None),
    )


@router.post("/undrain")
async def undrain(request: Request):
    """Release the drain gate -- the daemon resumes accepting new work.

    Used to roll back a cutover that was aborted before the old daemon exited,
    so a drained-but-surviving daemon does not stay closed to new sessions."""
    mgr: SessionManager = request.app.state.session_manager
    mgr.set_draining(False, source="undrain-endpoint")
    return {"draining": False}


@router.post("/relay/adopt")
async def relay_adopt(request: Request):
    """Bind the shared credential relay (9857) in this process.

    The post-cutover step: a passive instance started with the relay disabled
    adopts it once the retiring daemon has released the port. Idempotent."""
    adopt = getattr(request.app.state, "adopt_relay", None)
    if adopt is None:
        return {"adopted": False, "reason": "relay adoption unavailable"}
    adopted = await adopt()
    return {"adopted": bool(adopted)}


@router.post("/shutdown")
async def shutdown(request: Request):
    """Request a graceful shutdown of this daemon.

    Used by the cutover orchestrator to retire the *old* daemon once the route
    has flipped and it has drained. Triggers uvicorn's clean shutdown (lifespan
    runs, sessions stop, routing claim is retracted) -- a clean exit, so a
    systemd unit with Restart=on-failure does NOT resurrect it."""
    server = getattr(request.app.state, "uvicorn_server", None)
    if server is None:
        return {"shutting_down": False, "reason": "no server handle"}
    server.should_exit = True
    return {"shutting_down": True}


