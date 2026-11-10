"""Authenticated host metadata feed, selected subscriptions and explicit ack."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from ..status_store import SafeId, Selection, StatusGap, StatusStore

router = APIRouter(prefix="/api/v1/status", tags=["status"])


class Acknowledge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    epoch: str = Field(min_length=1, max_length=128)
    through: int = Field(ge=0, le=(1 << 63) - 1)


class Reconcile(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accept_lost_history: Literal[True]


def store(request: Request, *, write=False) -> StatusStore:
    if (
        not getattr(request.app.state, "ready", False)
        or getattr(request.app.state, "shutting_down", False)
    ):
        raise HTTPException(503, "host status provider is not serving")
    db = request.app.state.db
    manager = getattr(request.app.state, "session_manager", None)
    if write and manager is not None and manager.is_draining:
        raise HTTPException(503, "host status provider is draining")
    if write and db.read_only:
        raise HTTPException(503, "host status database is read-only")
    return StatusStore(db)


def _failure(exc):
    if isinstance(exc, StatusGap):
        raise HTTPException(409, exc.info) from exc
    if isinstance(exc, LookupError):
        raise HTTPException(404, str(exc)) from exc
    raise HTTPException(409, str(exc)) from exc


@router.get("")
def info(request: Request):
    with store(request).db._runtime_transaction(write=False) as conn:
        return store(request).bounds(conn)


@router.get("/feed")
def feed(
    request: Request,
    after: int = Query(default=0, ge=0, le=(1 << 63) - 1),
    epoch: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=100, ge=1, le=256),
):
    try:
        return store(request).page(after=after, epoch=epoch, limit=limit)
    except (StatusGap, LookupError, ValueError) as exc:
        _failure(exc)


@router.put("/subscriptions/{subscription_id}")
async def subscribe(
    body: Selection, request: Request,
    subscription_id: str = Path(min_length=1, max_length=128, pattern=SafeId),
):
    selected = store(request, write=True)
    runtime = getattr(request.app.state, "status_runtime", None)
    if runtime is None:
        raise HTTPException(503, "host status subscription runtime unavailable")
    try:
        # Remote support is established before the durable subscription effect.
        body = await runtime.prepare(body)
        result = selected.subscribe(subscription_id, body)
        runtime.wake.set()
        return result
    except (LookupError, ValueError) as exc:
        _failure(exc)


@router.get("/subscriptions/{subscription_id}/feed")
def subscription_feed(
    subscription_id: str, request: Request,
    after: int = Query(default=0, ge=0, le=(1 << 63) - 1),
    epoch: str | None = Query(default=None, max_length=128),
    limit: int = Query(default=100, ge=1, le=256),
):
    try:
        return store(request).page(
            after=after, epoch=epoch, limit=limit, subscription_id=subscription_id,
        )
    except (StatusGap, LookupError, ValueError) as exc:
        _failure(exc)


@router.post("/subscriptions/{subscription_id}/ack")
def acknowledge(subscription_id: str, body: Acknowledge, request: Request):
    try:
        result = store(request, write=True).acknowledge(subscription_id, body.through, body.epoch)
        request.app.state.status_runtime.wake.set()
        return result
    except (StatusGap, LookupError, ValueError) as exc:
        _failure(exc)


@router.delete("/subscriptions/{subscription_id}")
def unsubscribe(subscription_id: str, request: Request):
    store(request, write=True).remove(subscription_id)
    request.app.state.status_runtime.wake.set()
    return {"removed": True}


@router.get("/hosts")
def hosts(request: Request):
    rows = [dict(row) for row in store(request).db.execute_read("SELECT * FROM bridge_status_hosts")]
    for row in rows:
        if row["availability"] == "available" and time.time() - row["observed_at"] > 45:
            row["availability"] = "stale"
    return {"hosts": rows}


@router.post("/hosts/{host}/reconcile")
async def reconcile_host(
    body: Reconcile, request: Request,
    host: str = Path(min_length=1, max_length=128, pattern=SafeId),
):
    store(request, write=True)
    runtime = request.app.state.status_runtime
    if runtime.remote is None:
        raise HTTPException(409, "No selected remote status host")
    wanted = {
        a.host for row in request.app.state.db.execute_read("SELECT selection FROM bridge_status_subscriptions")
        for a in Selection.model_validate_json(row["selection"]).agents if a.host != "local"
    }
    if host not in wanted:
        raise HTTPException(409, "Host is not selected by a subscription")
    try:
        await runtime.remote.reset(host)
    except Exception as exc:
        raise HTTPException(503, "Remote status reconciliation is unavailable") from exc
    return {"reconciled": True, "lost_history_accepted": body.accept_lost_history}


@router.get("/events")
async def events(
    request: Request,
    after: int = Query(default=0, ge=0, le=(1 << 63) - 1),
    epoch: str | None = Query(default=None, max_length=128),
):
    selected = store(request)
    try:
        selected.page(after=after, epoch=epoch, limit=1)
    except StatusGap as exc:
        _failure(exc)

    async def stream():
        runtime = request.app.state.status_runtime
        if runtime.streams >= 32:
            yield 'event: bridge_control\ndata: {"code":"status_backpressure","action":"reconnect"}\n\n'
            return
        runtime.streams += 1
        try:
            async for record in records():
                yield record
        finally:
            runtime.streams -= 1

    async def records():
        cursor = after
        last_heartbeat = time.monotonic()
        while not await request.is_disconnected():
            try:
                page = selected.page(after=cursor, epoch=epoch)
            except StatusGap as exc:
                yield f"event: bridge_control\ndata: {json.dumps(exc.info)}\n\n"
                return
            # Host-source composition is one hop, never recursive federation.
            page["events"] = [e for e in page["events"] if e["source"] == "local"]
            if page["through"] > cursor:
                cursor = page["through"]
                yield f"event: status_page\ndata: {json.dumps(page, separators=(',', ':'))}\n\n"
                last_heartbeat = time.monotonic()
                continue
            if time.monotonic() - last_heartbeat >= 10:
                yield "event: _heartbeat\ndata: {}\n\n"
                last_heartbeat = time.monotonic()
            await asyncio.sleep(.5)

    return StreamingResponse(stream(), media_type="text/event-stream")
