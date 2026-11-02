"""Exact, read-only CLI inbox admission recovery; no alias or send fallback."""

from __future__ import annotations

import argparse
from urllib.parse import quote

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .client import BridgeClientError
from .client_protocol_floor import protocol_floored_request
from .send_outcome import SendRefused, emit

PROTOCOL = "live-keyed-receipt/v1"
CAPABILITY = "live_keyed_receipt"


class ReceiptBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(strict=True, ge=1, le=1)
    expected_session_id: str = Field(min_length=1, max_length=1024)
    idempotency_key: str = Field(min_length=1, max_length=1024)
    sender: str = Field(min_length=1, max_length=1024)
    body: str = Field(min_length=1)


def observe(session_id: str, body: ReceiptBinding, request: Request) -> dict:
    """Inspect the exact original row without liveness, aliases or cursor effects."""
    if session_id != body.expected_session_id:
        raise HTTPException(409, detail="live receipt requires its exact original session")
    db = request.app.state.db
    rows = db.execute_read(
        "SELECT session_id,id AS message_id,created_at AS admitted_at,"
        "delivered_at AS sdk_accepted_at,consumed_at,sender,body,kind,delivery,exact_session"
        " FROM live_messages WHERE session_id=? AND idempotency_key=?",
        (session_id, body.idempotency_key),
    )
    receipt = None
    if rows:
        row = dict(rows[0])
        if len(rows) != 1 or (
            row["sender"] != body.sender or row["body"] != body.body
            or row["kind"] != "prompt" or row["delivery"] != "steer"
            or row["exact_session"] != 1
        ):
            raise HTTPException(409, detail="live receipt invocation binding conflicts")
        receipt = {name: row[name] for name in (
            "session_id", "message_id", "admitted_at", "sdk_accepted_at", "consumed_at",
        )}
        receipt["stage"] = (
            "consumed" if row["consumed_at"] is not None
            else "sdk_accepted" if row["sdk_accepted_at"] is not None else "admitted"
        )
    return {"protocol": PROTOCOL, "version": 1, "binding": body.model_dump(), "receipt": receipt}


def send(args: argparse.Namespace) -> None:
    """Negotiate a receipt-only read; a miss cannot admit work or ensure a daemon."""
    from . import __main__ as core
    from .session_targeting_cli import _resolve_prompt

    receipt_only = bool(getattr(args, "live_receipt_only", False))
    if (
        receipt_only == bool(getattr(args, "live_exact", False))
        or not args.json or not args.expected_session_id or not args.idempotency_key or not args.sender
        or args.target != args.expected_session_id
        or any(getattr(args, name, False) for name in (
            "new", "force", "queue", "steer", "interrupt", "notify", "status_check",
            "wait_reply", "receipt_only", "plugin_context_stdin", "full_history",
            "live_exact_notify",
        ))
        or getattr(args, "expected_native_session_id", None) is not None
        or getattr(args, "native_invocation_file", None) is not None
        or getattr(args, "reply_to", None) is not None
        or getattr(args, "delivery", None) not in (None, "steer")
        or getattr(args, "kind", None) not in (None, "prompt")
    ):
        raise SendRefused("refused_invalid", reason="invalid_live_receipt_options",
                          retryable=False, target=args.target, error="exact live receipt options required")
    body = ReceiptBinding(
        version=1, expected_session_id=args.expected_session_id,
        idempotency_key=args.idempotency_key, sender=args.sender,
        body=_resolve_prompt(args, required=True),
    )
    client = core._get_client(ensure=False)
    read = protocol_floored_request(
        client, max(1, getattr(args, "min_daemon_protocol", None) or 1), capabilities={CAPABILITY: 1},
    )
    try:
        result = read(
            "POST", f"/api/v1/live-sessions/{quote(args.target, safe='')}/messages/"
            f"{'keyed-receipt' if receipt_only else 'keyed-exact'}/v1",
            body.model_dump(), allow_retry=False,
        )
    except BridgeClientError as exc:
        raise SendRefused("refused_unavailable", reason="live_receipt_unproven",
                          retryable=False, target=args.target, error=str(exc.detail)) from exc
    if not isinstance(result, dict) or (
        result.get("protocol") != PROTOCOL or type(result.get("version")) is not int
        or result["version"] != 1 or result.get("binding") != body.model_dump()
        or "receipt" not in result
    ):
        raise SendRefused("refused_unavailable", reason="live_receipt_unproven",
                          retryable=False, target=args.target, error="invalid live receipt response")
    emit(result)
