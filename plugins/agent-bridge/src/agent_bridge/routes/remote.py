"""Authenticated local API for narrow remote Bridge carrier operations."""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from ssh_manager import (
    CarrierBackpressure,
    CarrierRemoteError,
    CarrierUnavailable,
    EnvelopeType,
)

from ..remote_operations import (
    RemoteBridgeError,
    RemoteOperationService,
    validate_caller_id,
)

router = APIRouter(prefix="/api/v1/remote", tags=["remote"])


class RemoteCursorAckRequest(BaseModel):
    caller_id: str = Field(min_length=1, max_length=128)
    last_id: int = Field(ge=0)
    continuity_id: str | None = Field(
        default=None, min_length=1, max_length=128
    )


def _service(request: Request) -> RemoteOperationService:
    if not getattr(request.app.state, "ready", True):
        raise HTTPException(
            status_code=503,
            detail="agent-bridge is initializing; retry shortly",
        )
    service = getattr(request.app.state, "remote_operations", None)
    if service is None:
        service = RemoteOperationService(request.app.state.resolver)
    return service


def _raise(error: RemoteBridgeError) -> None:
    raise HTTPException(status_code=error.status, detail=error.public_detail())


def _control(error: RemoteBridgeError) -> str:
    payload = {
        "code": error.code,
        "message": str(error),
        "action": "full_reconcile",
        **error.details,
    }
    return (
        "event: bridge_control\n"
        f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
    )


def _control_response(error: RemoteBridgeError) -> StreamingResponse:
    async def stream():
        yield _control(error)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{host}/sessions/{session_id}/status")
async def remote_session_status(
    host: str,
    session_id: str,
    request: Request,
    caller_id: str = Query(min_length=1, max_length=128),
) -> dict[str, Any]:
    try:
        return await _service(request).session_status(
            host, session_id, validate_caller_id(caller_id)
        )
    except RemoteBridgeError as exc:
        _raise(exc)


@router.get("/{host}/live-sessions/{session_id}")
async def remote_live_session(
    host: str, session_id: str, request: Request
) -> dict[str, Any]:
    try:
        return await _service(request).resolve_live_session(host, session_id)
    except RemoteBridgeError as exc:
        _raise(exc)


@router.get("/{host}/sessions/{session_id}/events")
async def remote_session_events(
    host: str,
    session_id: str,
    request: Request,
    caller_id: str = Query(min_length=1, max_length=128),
    after: int | None = Query(default=None, ge=0),
    continuity_id: str | None = Query(default=None, max_length=128),
) -> StreamingResponse:
    try:
        subscription = await _service(request).subscribe_events(
            host,
            session_id,
            caller_id=validate_caller_id(caller_id),
            after=after,
            continuity_id=continuity_id,
        )
    except CarrierBackpressure as exc:
        return _control_response(
            RemoteBridgeError(
                429,
                "consumer_backpressure",
                str(exc),
                details={"reason": "local event queue overflow"},
            )
        )
    except RemoteBridgeError as exc:
        _raise(exc)

    server = getattr(request.app.state, "uvicorn_server", None)

    async def closing() -> bool:
        if server is not None and getattr(server, "should_exit", False):
            return True
        is_disconnected = getattr(request, "is_disconnected", None)
        if is_disconnected is not None:
            try:
                return bool(await is_disconnected())
            except Exception:
                return False
        return False

    async def stream():
        get_task: asyncio.Task | None = None
        try:
            while True:
                if await closing():
                    return
                get_task = asyncio.create_task(subscription.get())
                while True:
                    done, _pending = await asyncio.wait(
                        {get_task}, timeout=0.5
                    )
                    if done:
                        break
                    if await closing():
                        get_task.cancel()
                        with contextlib.suppress(BaseException):
                            await get_task
                        return
                envelope = get_task.result()
                get_task = None
                if envelope.type is not EnvelopeType.EVENT:
                    continue
                payload = envelope.payload
                kind = payload.get("kind")
                if kind == "heartbeat":
                    yield ": heartbeat\n\n"
                    continue
                if kind == "tool_progress":
                    data = json.dumps(
                        payload.get("data") or {},
                        separators=(",", ":"),
                    )
                    yield f": tool_progress {data}\n\n"
                    continue
                if kind != "event":
                    continue
                event_id = int(payload["id"])
                event_name = str(payload["event"])
                event_continuity = payload.get("continuity_id")
                if isinstance(event_continuity, str):
                    subscription.continuity_id = event_continuity
                event_payload = {
                    "event": event_name,
                    "data": payload.get("data") or {},
                    "timestamp": payload.get("timestamp"),
                    "continuity_id": subscription.continuity_id,
                }
                yield (
                    f"id: {event_id}\n"
                    f"event: {event_name}\n"
                    f"data: {json.dumps(event_payload, separators=(',', ':'))}\n\n"
                )
        except CarrierRemoteError as exc:
            yield _control(RemoteBridgeError.from_carrier(exc))
        except CarrierBackpressure as exc:
            yield _control(
                RemoteBridgeError(
                    429,
                    "consumer_backpressure",
                    str(exc),
                    details={"reason": "local event queue overflow"},
                )
            )
        except CarrierUnavailable as exc:
            yield _control(
                RemoteBridgeError(
                    503,
                    "carrier_unavailable",
                    str(exc),
                    reconnectable=exc.reconnectable,
                )
            )
        finally:
            if get_task is not None and not get_task.done():
                get_task.cancel()
                with contextlib.suppress(BaseException):
                    await get_task
            await subscription.close()

    headers = {
        "Cache-Control": "no-cache",
        "Connection": "keep-alive",
        "X-Accel-Buffering": "no",
        "X-Agent-Bridge-Cursor": str(subscription.last_acked_id),
    }
    if subscription.continuity_id:
        headers["X-Agent-Bridge-Continuity"] = subscription.continuity_id
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers=headers,
    )


@router.post("/{host}/sessions/{session_id}/cursor")
async def remote_session_cursor(
    host: str,
    session_id: str,
    body: RemoteCursorAckRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        caller_id = validate_caller_id(body.caller_id)
        effective = await _service(request).acknowledge(
            host,
            session_id,
            caller_id=caller_id,
            last_id=body.last_id,
            continuity_id=body.continuity_id,
        )
        return {
            "session_id": session_id,
            "caller_id": caller_id,
            "last_acked_id": effective,
            "continuity_id": body.continuity_id,
        }
    except RemoteBridgeError as exc:
        _raise(exc)
