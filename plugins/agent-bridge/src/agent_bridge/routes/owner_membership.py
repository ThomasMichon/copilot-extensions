"""Authenticated passive discovery of already-admitted owner membership."""

from __future__ import annotations

import time
import sqlite3
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..owner_membership import CAPABILITY, source_for
from ..owner_membership_contract import OwnerRead
from ..session_observation_contract import ObservationError, sse

router = APIRouter(prefix="/api/v1", tags=["owner-membership"])


def _failure(read: OwnerRead, exc: ObservationError, source: Any = None) -> dict[str, Any]:
    return {
        "schema": CAPABILITY,
        "version": 1,
        "kind": "gap"
        if exc.code
        in {
            "retention-gap",
            "source-changed",
            "publication-failed",
            "membership-overflow",
            "body-too-large",
            "sequence-exhausted",
            "capture-budget-exhausted",
            "capture-raced",
        }
        else "error",
        "source": source.identity(read.owner)
        if source is not None
        else {
            "provider": "bridge",
            "owner_ref": read.owner,
            "hosting_generation": None,
            "journal_incarnation": None,
            "head_session_id": None,
        },
        "availability": "unavailable",
        "completeness": "unknown",
        "code": exc.code,
        "message": str(exc),
        "reconcile": "new-snapshot",
    }


async def stream(request: Request, read: OwnerRead) -> AsyncIterator[bytes]:
    """Own only a membership waiter; discovery does not pin session idle lifetime."""
    source = None
    position = read.after
    first = True
    last_sent = time.monotonic()
    try:
        while True:
            state = request.app.state
            server = getattr(state, "uvicorn_server", None)
            if server is not None and server.should_exit or source is not None and source.closed:
                yield sse(
                    {
                        "schema": CAPABILITY,
                        "version": 1,
                        "kind": "going-away",
                        "source": {"provider": "bridge", "owner_ref": read.owner},
                        "reason": "hosting-generation-stopping",
                    }
                )
                return
            if await request.is_disconnected():
                return
            try:
                manager = getattr(state, "session_manager", None)
                if manager is None:
                    raise ObservationError("source-unavailable", "membership owner is not ready")
                if source is None:
                    source = source_for(manager, read.owner, acquire=True)
                elif source_for(manager, read.owner) is not source:
                    raise ObservationError("source-changed", "membership hosting generation changed")
                body = source.frame(read.owner, position)
                if first or body["changes"]:
                    yield sse(body)
                    position = body["position"]
                    last_sent = time.monotonic()
                elif time.monotonic() - last_sent >= 5:
                    yield sse(
                        {
                            "schema": CAPABILITY,
                            "version": 1,
                            "kind": "heartbeat",
                            "source": body["source"],
                            "position": body["position"],
                        }
                    )
                    position = body["position"]
                    last_sent = time.monotonic()
                first = False
                if not read.follow:
                    return
                if not body["has_more"]:
                    # The scanned cursor avoids spinning on other owners, but
                    # the next delta must splice from the last delivered cursor.
                    await source.wait(read.owner, body["position"])
            except ObservationError as exc:
                yield sse(_failure(read, exc, source))
                return
            except (sqlite3.Error, OSError):
                yield sse(
                    _failure(
                        read,
                        ObservationError("source-unavailable", "membership source cannot be read"),
                        source,
                    )
                )
                return
    finally:
        if source is not None:
            source.release(read.owner)


@router.get("/owner-observations")
def observe_owner(request: Request) -> StreamingResponse:
    """One bounded current snapshot/delta, optionally followed under its own cursor."""
    if len(request.scope.get("query_string", b"")) > 8192:
        raise HTTPException(422, detail={"code": "invalid-request", "message": "query too large"})
    try:
        read = OwnerRead.parse(list(request.query_params.multi_items()))
    except ObservationError as exc:
        raise HTTPException(422, detail={"code": exc.code, "message": str(exc)}) from exc
    return StreamingResponse(
        stream(request, read),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
