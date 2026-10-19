"""Inbox receipt evidence derived from the existing durable message rows."""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from .models import LiveMessageReceipt, ResultField

if TYPE_CHECKING:
    from .db import Database
    from .result_snapshot import _TextBudget

_MESSAGE_ID = re.compile(r'<agent-message\b[^>]*\bmsg-id="(\d+)"')


def consumed_message_id(event: dict[str, Any]) -> int | None:
    data = event.get("data") or {}
    if (
        event.get("type") != "user.message"
        or event.get("agentId")
        or data.get("agentId")
        or data.get("parentToolCallId")
        or data.get("source") != "agent-bridge"
    ):
        return None
    match = _MESSAGE_ID.search(str(data.get("transformedContent") or data.get("content") or ""))
    return int(match[1]) if match else None


def record_consumption(db: Database, session_id: str, events: list[dict[str, Any]]) -> None:
    now = time.time()
    for event in events:
        message_id = consumed_message_id(event)
        if message_id is not None:
            db.consume_live_message(session_id, message_id, now)


def receipt(db: Database, session_id: str, message_id: int) -> LiveMessageReceipt:
    row = db.live_message_receipt(session_id, message_id)
    if row is None:
        raise LookupError("live-message receipt is no longer available")
    return LiveMessageReceipt(**row)


def admission_evidence(
    db: Database, session_id: str, message_id: int, now: float, duplicate: bool,
) -> tuple[LiveMessageReceipt | None, bool]:
    """Retirement cannot revoke an already-committed admission result."""
    row = db.live_message_receipt(session_id, message_id)
    if row is not None:
        return LiveMessageReceipt(**row), True
    if duplicate:
        return None, False
    return LiveMessageReceipt(
        session_id=session_id, message_id=message_id, stage="admitted", admitted_at=now
    ), False


def project_inbox(inbox: list[dict[str, Any]] | None, max_items: int, budget: _TextBudget) -> ResultField:
    """Bound metadata using the snapshot's existing shared text budget."""
    if inbox is None:
        return ResultField(availability="unsupported_for_target", reason="inbox evidence was not supplied")
    messages = []
    for row in inbox[:max_items]:
        sender, sender_cut, _ = budget.clip(row.get("sender"), field_limit=128)
        kind, kind_cut, _ = budget.clip(row.get("kind"), field_limit=64)
        messages.append({
            "message_id": row["message_id"], "sender": sender, "kind": kind,
            "delivery": row["delivery"], "admitted_at": row["admitted_at"],
            "sdk_accepted_at": row["sdk_accepted_at"],
            "stage": "sdk_accepted" if row["sdk_accepted_at"] is not None else "admitted",
            "truncated": sender_cut or kind_cut,
        })
    return ResultField(
        availability="available", value=messages,
        reason="more pending messages omitted" if len(inbox) > max_items else None,
    )
