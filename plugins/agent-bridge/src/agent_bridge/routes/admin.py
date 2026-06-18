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
