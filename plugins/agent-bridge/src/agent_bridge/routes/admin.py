"""Admin / maintenance endpoints -- /api/v1/*."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from ..drain_contract import DRAIN_CONTRACT_VERSION, DrainRequest

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
    """Negotiate preservation before graceful or explicitly destructive drain.

    Missing contracts are legacy/unnegotiated, never destructive. A valid
    disjoint preservation range selects cold drain by default; an explicit
    graceful-only policy refuses before effects. Authentication precedes control.
    """
    mgr: SessionManager = request.app.state.session_manager
    try:
        body = await request.body()
        control = DrainRequest.model_validate_json(body or b"{}")
    except ValidationError as exc:
        raise HTTPException(422, detail="invalid drain control: " + str(exc)) from exc
    if control.contract_status == "incompatible":
        if control.on_incompatible != "cold":
            raise HTTPException(409, detail="incompatible drain contract; graceful-only control refused")
        from ..session_cold_drain import cold_drain

        try:
            result = await cold_drain(
                mgr, timeout=control.timeout, poll=control.poll, source=control.source,
                reason=control.reason, exclude_session_id=control.exclude_session_id,
            )
        except ValueError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
    else:
        result = await mgr.drain(
            timeout=control.timeout, poll=control.poll, force=control.force,
            source=control.source, reason=control.reason,
            exclude_session_id=control.exclude_session_id,
        )
        result.setdefault("mode", "graceful")
    result.update(contract_status=control.contract_status, drain_contract=DRAIN_CONTRACT_VERSION)
    return result


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
    systemd unit with Restart=on-failure does NOT resurrect it.

    Releases every session-host claim this generation holds (effort
    agent-bridge-unified-zdd-cutover, Phase 3's exit contract) -- but only
    once shutdown can actually be initiated. Releasing first and *then*
    discovering there is no server handle to shut down would leave a still-
    live daemon with no claims -- exactly the split ownership this whole
    mechanism exists to prevent (PR #4543 review). Best-effort within that
    ordering -- a release failure must never block the shutdown it's
    guarding against outliving.
    """
    server = getattr(request.app.state, "uvicorn_server", None)
    if server is None:
        return {"shutting_down": False, "reason": "no server handle",
                "released_claims": []}

    mgr: SessionManager = request.app.state.session_manager
    released: list[str] = []
    try:
        host_index = getattr(mgr, "_host_index", None)
        generation = getattr(mgr, "_generation_id", None)
        if host_index is not None and generation:
            released = host_index.release_all(generation)
    except Exception:  # noqa: BLE001 -- release is best-effort, never blocks shutdown
        pass
    server.should_exit = True
    return {"shutting_down": True, "released_claims": released}


@router.post("/session-hosts/reattach")
async def reattach_hosts(request: Request):
    """Re-run the Session Host reattach + claim scan on demand.

    A passive cutover instance skips the startup reattach task entirely
    (ATTACHing would disconnect the still-active old generation -- see
    ``app.py``'s own ``_reattach_session_hosts_bg``), so it never even
    attempts this scan until now. The cutover CLI calls this endpoint on the
    newly-active daemon once the old generation is *confirmed* exited, so
    this is the *first* reattach/claim pass for it, run once claims are no
    longer contended (Phase 3's "the next generation earns the handoff,
    never assumes it"). Returns the number of sessions (re)attached.
    """
    mgr: SessionManager = request.app.state.session_manager
    reattached = await mgr.reattach_session_hosts()
    return {"reattached": reattached}
