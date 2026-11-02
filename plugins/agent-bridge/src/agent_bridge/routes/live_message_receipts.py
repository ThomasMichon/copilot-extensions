"""Cursor-neutral inspection of retained live-message receipt evidence."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..db import Database
from ..live_message_receipts import receipt
from ..models import LiveMessageReceipt
from ..live_keyed_receipt import ReceiptBinding, observe

router = APIRouter()


@router.post("/{session_id}/messages/keyed-receipt/v1")
def get_keyed_receipt(session_id: str, body: ReceiptBinding, request: Request) -> dict:
    return observe(session_id, body, request)


@router.post("/{session_id}/messages/keyed-exact/v1")
async def send_keyed_exact(session_id: str, body: ReceiptBinding, request: Request) -> dict:
    """Expand the existing inbox owner with a raw, non-alias native fence."""
    from .live_sessions import post_live_message
    from ..models import SendMessageRequest
    if session_id != body.expected_session_id:
        raise HTTPException(409, detail="exact live send requires its original session")
    result = await post_live_message(
        session_id, SendMessageRequest(
            sender=body.sender, body=body.body, expected_session_id=body.expected_session_id,
            idempotency_key=body.idempotency_key, exact_session=True, delivery="steer",
        ), request,
    )
    return observe(session_id, body, request) | {"admission": result.model_dump()}


@router.post("/{session_id}/messages/exact-notify/v1")
async def send_exact_notify(session_id: str, body: ReceiptBinding, request: Request) -> dict:
    from ..live_exact_notify import admit
    return await admit(session_id, body, request)


@router.get("/{session_id}/messages/{message_id}/receipt", response_model=LiveMessageReceipt)
def get_live_message_receipt(
    session_id: str, message_id: int, request: Request,
) -> LiveMessageReceipt:
    """Inspect an admitted message without redelivering it or moving a cursor."""
    db: Database = request.app.state.db
    try:
        return receipt(db, session_id, message_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
