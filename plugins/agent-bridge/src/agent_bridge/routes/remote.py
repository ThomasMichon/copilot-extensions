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


class RemoteEventMultiplexSubscription(BaseModel):
    host: str = Field(min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=256)
    caller_id: str = Field(min_length=1, max_length=128)
    after: int | None = Field(default=None, ge=0)
    continuity_id: str | None = Field(
        default=None, min_length=1, max_length=128
    )


class RemoteEventMultiplexRequest(BaseModel):
    subscriptions: list[RemoteEventMultiplexSubscription] = Field(
        min_length=1, max_length=256
    )


class RemoteSessionCreateRequest(BaseModel):
    agent: str = Field(min_length=1, max_length=128)
    prompt: str = Field(min_length=1, max_length=1_048_576)
    caller_id: str = Field(min_length=1, max_length=128)
    timeout: float = Field(default=120.0, ge=1.0, le=600.0)


class RemoteSessionStopRequest(BaseModel):
    force: bool = False
    reap_host: bool = False
    timeout: float = Field(default=20.0, ge=1.0, le=120.0)


class RemoteSessionEndRequest(BaseModel):
    force: bool = False
    if_idle: bool = False
    timeout: float = Field(default=20.0, ge=1.0, le=120.0)


class RemoteLiveMessageRequest(BaseModel):
    sender: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=1_048_576)
    kind: str = Field(default="prompt")
    expected_session_id: str | None = Field(
        default=None, min_length=1, max_length=256
    )
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=256)
    timeout: float = Field(default=20.0, ge=1.0, le=120.0)


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
        "action": "full_reconcile",
        **error.public_detail(),
    }
    return (
        "event: bridge_control\n"
        f"data: {json.dumps(payload, separators=(',', ':'))}\n\n"
    )


def _multiplex_control(
    error: RemoteBridgeError,
    subscription: RemoteEventMultiplexSubscription | None = None,
) -> str:
    payload = {
        "action": "full_reconcile",
        **error.public_detail(),
    }
    if subscription is not None:
        payload.update(
            {
                "host": subscription.host,
                "session_id": subscription.session_id,
                "caller_id": subscription.caller_id,
            }
        )
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


def _multiplex_control_response(
    error: RemoteBridgeError,
    subscription: RemoteEventMultiplexSubscription,
) -> StreamingResponse:
    async def stream():
        yield _multiplex_control(error, subscription)

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


@router.post("/{host}/sessions")
async def remote_create_session(
    host: str,
    body: RemoteSessionCreateRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return await _service(request).create_session(
            host,
            agent=body.agent,
            prompt=body.prompt,
            caller_id=body.caller_id,
            timeout=body.timeout,
        )
    except RemoteBridgeError as exc:
        _raise(exc)


@router.post("/{host}/sessions/{session_id}/stop", status_code=204)
async def remote_stop_session(
    host: str,
    session_id: str,
    body: RemoteSessionStopRequest,
    request: Request,
) -> None:
    try:
        await _service(request).stop_session(
            host,
            session_id,
            force=body.force,
            reap_host=body.reap_host,
            timeout=body.timeout,
        )
    except RemoteBridgeError as exc:
        _raise(exc)


@router.post("/{host}/sessions/{session_id}/end", status_code=204)
async def remote_end_session(
    host: str,
    session_id: str,
    body: RemoteSessionEndRequest,
    request: Request,
) -> None:
    try:
        await _service(request).end_session(
            host,
            session_id,
            force=body.force,
            if_idle=body.if_idle,
            timeout=body.timeout,
        )
    except RemoteBridgeError as exc:
        _raise(exc)


@router.post("/{host}/live-sessions/{target}/messages")
async def remote_send_live_message(
    host: str,
    target: str,
    body: RemoteLiveMessageRequest,
    request: Request,
) -> dict[str, Any]:
    try:
        return await _service(request).send_live_message(
            host,
            target,
            sender=body.sender,
            message=body.message,
            kind=body.kind,
            expected_session_id=body.expected_session_id,
            idempotency_key=body.idempotency_key,
            timeout=body.timeout,
        )
    except RemoteBridgeError as exc:
        _raise(exc)


@router.get("/{host}/sessions/{session_id}/events")
async def remote_session_events(
    host: str,
    session_id: str,
    request: Request,
    caller_id: str = Query(min_length=1, max_length=128),
    after: int | None = Query(default=None, ge=0),
    continuity_id: str | None = Query(
        default=None, min_length=1, max_length=128
    ),
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


@router.post("/events")
async def multiplex_remote_session_events(
    body: RemoteEventMultiplexRequest,
    request: Request,
) -> StreamingResponse:
    """Multiplex exact remote session subscriptions over one local SSE stream."""
    service = _service(request)
    requested = body.subscriptions
    identities = {
        (item.host, item.session_id, item.caller_id) for item in requested
    }
    if len(identities) != len(requested):
        raise HTTPException(
            status_code=422,
            detail="remote event subscriptions must be unique",
        )
    subscriptions: list[tuple[RemoteEventMultiplexSubscription, Any]] = []
    try:
        for item in requested:
            subscription = await service.subscribe_events(
                item.host,
                item.session_id,
                caller_id=validate_caller_id(item.caller_id),
                after=item.after,
                continuity_id=item.continuity_id,
            )
            subscriptions.append((item, subscription))
    except CarrierBackpressure as exc:
        for _item, subscription in subscriptions:
            await subscription.close()
        return _multiplex_control_response(
            RemoteBridgeError(
                429,
                "consumer_backpressure",
                str(exc),
                details={"reason": "local event queue overflow"},
            ),
            item,
        )
    except RemoteBridgeError as exc:
        for _item, subscription in subscriptions:
            await subscription.close()
        return _multiplex_control_response(exc, item)

    server = getattr(request.app.state, "uvicorn_server", None)

    async def closing() -> bool:
        if server is not None and getattr(server, "should_exit", False):
            return True
        is_disconnected = getattr(request, "is_disconnected", None)
        if is_disconnected is None:
            return False
        try:
            return bool(await is_disconnected())
        except Exception:
            return False

    async def stream():
        tasks: dict[asyncio.Task, tuple[RemoteEventMultiplexSubscription, Any]] = {}
        try:
            for item, subscription in subscriptions:
                tasks[asyncio.create_task(subscription.get())] = (
                    item,
                    subscription,
                )
            while tasks:
                if await closing():
                    return
                done, _pending = await asyncio.wait(
                    set(tasks), timeout=0.5, return_when=asyncio.FIRST_COMPLETED
                )
                for task in done:
                    item, subscription = tasks.pop(task)
                    try:
                        envelope = task.result()
                    except CarrierRemoteError as exc:
                        yield _multiplex_control(
                            RemoteBridgeError.from_carrier(exc), item
                        )
                        return
                    except CarrierBackpressure as exc:
                        yield _multiplex_control(
                            RemoteBridgeError(
                                429,
                                "consumer_backpressure",
                                str(exc),
                                details={"reason": "local event queue overflow"},
                            ),
                            item,
                        )
                        return
                    except CarrierUnavailable as exc:
                        yield _multiplex_control(
                            RemoteBridgeError(
                                503,
                                "carrier_unavailable",
                                str(exc),
                                reconnectable=exc.reconnectable,
                            ),
                            item,
                        )
                        return
                    if envelope.type is not EnvelopeType.EVENT:
                        tasks[asyncio.create_task(subscription.get())] = (
                            item,
                            subscription,
                        )
                        continue
                    payload = envelope.payload
                    kind = payload.get("kind")
                    if kind == "heartbeat":
                        yield ": heartbeat\n\n"
                    elif kind == "tool_progress":
                        data = json.dumps(
                            payload.get("data") or {},
                            separators=(",", ":"),
                        )
                        yield f": tool_progress {data}\n\n"
                    elif kind == "event":
                        continuity_id = payload.get("continuity_id")
                        if isinstance(continuity_id, str):
                            subscription.continuity_id = continuity_id
                        event_payload = {
                            "host": item.host,
                            "session_id": item.session_id,
                            "caller_id": item.caller_id,
                            "event_id": int(payload["id"]),
                            "event": str(payload["event"]),
                            "data": payload.get("data") or {},
                            "timestamp": payload.get("timestamp"),
                            "continuity_id": subscription.continuity_id,
                        }
                        yield (
                            "event: bridge_event\n"
                            f"data: {json.dumps(event_payload, separators=(',', ':'))}\n\n"
                        )
                    tasks[asyncio.create_task(subscription.get())] = (
                        item,
                        subscription,
                    )
        finally:
            for task in tasks:
                task.cancel()
            for task in tasks:
                with contextlib.suppress(BaseException):
                    await task
            for _item, subscription in subscriptions:
                await subscription.close()

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
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
