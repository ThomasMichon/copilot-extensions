"""Delivery-boundary waits never require a continuously running turn to end."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..live_delivery_state import DeliveryStateObservation
from ..live_message_models import SendMessageRequest
from ..live_message_receipts import receipt

router = APIRouter(prefix="/api/v1/live-sessions", tags=["live-delivery"])


class DeliveryWait(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout: float = Field(default=120.0, ge=0.0, le=600.0, allow_inf_nan=False)


class DeliverySend(DeliveryWait):
    message: SendMessageRequest

    @model_validator(mode="after")
    def no_reply_wait(self):
        if self.message.wait:
            raise ValueError("delivery waiting and reply waiting are separate operations")
        if self.message.plugin_readiness is not None:
            raise ValueError("delivery waiting does not combine plugin startup readiness")
        return self


def _exact(request: Request, session_id: str) -> dict:
    row = request.app.state.db.get_live_session(session_id)
    if row is None:
        raise HTTPException(404, "live session not found")
    if row["session_id"] != session_id:
        raise HTTPException(409, "delivery observations require the exact native session")
    return row


@router.post("/{session_id}/delivery-state")
async def publish_state(
    session_id: str, body: DeliveryStateObservation, request: Request,
) -> dict:
    row = _exact(request, session_id)
    if row.get("pid") != body.pid or row.get("process_started_at") != body.process_started_at:
        raise HTTPException(409, "native extension incarnation changed")
    try:
        request.app.state.live_delivery_state.publish(session_id, body)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"ok": True}


async def _observe(
    session_id: str, message_id: int, timeout: float, request: Request,
    *, admitted: dict | None = None, admission_known: bool = False,
) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fact = receipt(request.app.state.db, session_id, message_id).model_dump()
        except LookupError:
            if not admission_known:
                raise HTTPException(404, "live-message receipt is no longer available") from None
            return {
                "receipt": admitted, "outcome": "unavailable",
                "attention": {"availability": "unavailable", "state": "unknown", "reasons": [],
                              "reason": "admission retained; current receipt unavailable"},
            }
        row = request.app.state.db.get_live_session(session_id)
        attention = request.app.state.live_delivery_state.inspect(row)
        if row is not None and row.get("role") == "acp":
            manager = getattr(request.app.state, "session_manager", None)
            if callable(getattr(manager, "get_session", None)):
                from ..acp_inbox import attention as acp_attention

                attention = acp_attention(manager.get_session(session_id))
        if fact["stage"] == "consumed":
            acp = request.app.state.db.execute_read(
                "SELECT 1 FROM turns WHERE session_id=? AND live_message_id=? AND completed_at IS NOT NULL",
                (session_id, message_id),
            )
            return {"receipt": fact, "outcome": "consumed", "attention": attention,
                    "evidence": "completed_acp_prompt" if acp else "attributed_root_user_message"}
        if attention["state"] == "blocked":
            return {"receipt": fact, "outcome": "blocked", "attention": attention}
        if time.monotonic() >= deadline:
            return {"receipt": fact, "outcome": "timeout", "attention": attention}
        if await request.is_disconnected():
            return {"receipt": fact, "outcome": "detached", "attention": attention}
        await asyncio.sleep(min(0.25, max(0.0, deadline - time.monotonic())))


@router.post("/{session_id}/messages/{message_id}/delivery")
async def observe_delivery(
    session_id: str, message_id: int, body: DeliveryWait, request: Request,
) -> dict:
    return await _observe(session_id, message_id, body.timeout, request)


@router.post("/{session_id}/delivery")
async def send_and_observe(
    session_id: str, body: DeliverySend, request: Request,
) -> dict:
    from .live_sessions import post_live_message

    admitted = await post_live_message(session_id, body.message, request)
    observed = await _observe(
        admitted.session_id, admitted.message_id, body.timeout, request,
        admitted=admitted.receipt.model_dump() if admitted.receipt else None,
        admission_known=True,
    )
    result = {**admitted.model_dump(), "receipt": observed["receipt"], "delivery_observation": observed}
    if observed["outcome"] == "unavailable":
        result.update(receipt_current=False, receipt_error=observed["attention"]["reason"])
    return result
