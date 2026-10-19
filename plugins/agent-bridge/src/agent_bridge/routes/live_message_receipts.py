"""Cursor-neutral inspection of retained live-message receipt evidence."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from ..db import Database
from ..live_message_receipts import receipt
from ..models import LiveMessageReceipt

router = APIRouter()


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
