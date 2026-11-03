"""Passive semantic observations of exact already-admitted Bridge sources."""

from __future__ import annotations

import sqlite3
import time
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..compaction_evidence import CompactionEvidenceError
from ..session_observation_contract import (
    HISTORY,
    OBSERVATIONS,
    VERSION,
    ObservationError,
    ReadRequest,
    sse,
)
from ..session_observations import admit, frame

router = APIRouter(prefix="/api/v1", tags=["session-observations"])


def failure(read: ReadRequest, exc: ObservationError) -> dict[str, Any]:
    """Keep failures bounded and distinct from empty/complete history."""
    return {
        "schema": HISTORY if read.history else OBSERVATIONS,
        "version": VERSION,
        "kind": "gap" if exc.code in {"source-changed", "retention-gap"} else "error",
        "selection": read.selection.as_dict(),
        "availability": "unavailable",
        "code": exc.code,
        "message": str(exc),
        "reconcile": "new-snapshot",
        "coverage": "unknown",
    }


async def observations(request: Request, read: ReadRequest) -> AsyncIterator[bytes]:
    """Own only this log waiter and idle-watch registration, not the actor."""
    state = request.app.state
    known = None
    subscribed = False
    cursor = read.position
    prior_source = None
    last_sent = time.monotonic()
    try:
        while True:
            server = getattr(state, "uvicorn_server", None)
            if server is not None and server.should_exit:
                if read.history:
                    yield sse(
                        failure(
                            read,
                            ObservationError(
                                "source-unavailable",
                                "hosting generation is stopping",
                            ),
                        )
                    )
                    return
                yield sse(
                    {
                        "schema": OBSERVATIONS,
                        "version": VERSION,
                        "kind": "going-away",
                        "selection": read.selection.as_dict(),
                        "reason": "hosting-generation-stopping",
                    }
                )
                return
            if await request.is_disconnected():
                return
            try:
                current = admit(state, read)
                if known is not None and (
                    current.log is not known.log or current.source != known.source
                ):
                    raise ObservationError("source-changed", "admitted session source changed")
                known = current
                if read.follow and known.registry == "owned" and not subscribed:
                    known.session.subscriber_count += 1
                    subscribed = True
                body = frame(known, read, after=cursor)
                if prior_source is not None and body["source"] != prior_source:
                    if prior_source["journal_incarnation"] is not None:
                        raise ObservationError(
                            "source-changed", "event history incarnation changed"
                        )
                send = (
                    prior_source is None or prior_source != body["source"] or bool(body["entries"])
                )
                if send:
                    yield sse(body)
                    last_sent = time.monotonic()
                elif time.monotonic() - last_sent >= 5:
                    yield sse(
                        {
                            "schema": OBSERVATIONS,
                            "version": VERSION,
                            "kind": "heartbeat",
                            "source": body["source"],
                            "position": cursor,
                        }
                    )
                    last_sent = time.monotonic()
                prior_source = body["source"]
                cursor = body["position"]
                if not read.follow:
                    return
                if body["kind"] == "delta" and body["has_more"]:
                    continue
                # The existing EventLog wake mechanism owns the subscription.
                # Short waits bound shutdown/retirement checks; no actor/fleet poll.
                await known.log.wait_for_observation(
                    body["retained"]["head"],
                    timeout=0.5,
                )
            except CompactionEvidenceError:
                yield sse(
                    failure(
                        read,
                        ObservationError(
                            "invalid-source",
                            "compaction evidence exceeds the source contract",
                        ),
                    )
                )
                return
            except ObservationError as exc:
                yield sse(failure(read, exc))
                return
            except (OSError, sqlite3.Error):
                yield sse(
                    failure(
                        read,
                        ObservationError(
                            "source-unavailable",
                            "retained source could not be read",
                        ),
                    )
                )
                return
    finally:
        if subscribed and known is not None:
            # Retain exact object custody if the registry replaces this handle.
            # Match the existing remove_subscriber idle-clock policy, without
            # decrementing or touching a replacement session.
            known.session.subscriber_count = max(0, known.session.subscriber_count - 1)
            if known.session.subscriber_count == 0:
                known.session.touch()


def _read(session_id: str, request: Request, *, history: bool) -> StreamingResponse:
    if len(request.scope.get("query_string", b"")) > 8192:
        raise HTTPException(422, detail={"code": "invalid-request", "message": "query too large"})
    try:
        read = ReadRequest.parse(
            session_id, list(request.query_params.multi_items()), history=history
        )
    except ObservationError as exc:
        raise HTTPException(422, detail={"code": exc.code, "message": str(exc)}) from exc
    return StreamingResponse(
        observations(request, read),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/session-observations/{session_id}")
def observe_session(session_id: str, request: Request) -> StreamingResponse:
    """Snapshot and optional held deltas; no delivery cursor is touched."""
    return _read(session_id, request, history=False)


@router.get("/session-observation-history/{session_id}")
def session_observation_history(session_id: str, request: Request) -> StreamingResponse:
    """Exactly one bounded history-page or typed failure, never raw SSE history."""
    return _read(session_id, request, history=True)
