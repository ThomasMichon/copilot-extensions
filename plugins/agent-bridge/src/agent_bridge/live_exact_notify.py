"""Negotiated informational notifications bound to one raw live native."""

from __future__ import annotations

import argparse
from urllib.parse import quote

from fastapi import HTTPException, Request

from .client import BridgeClientError
from .client_protocol_floor import protocol_floored_request
from .live_keyed_receipt import ReceiptBinding
from .send_outcome import SendRefused, emit

PROTOCOL = "live-exact-notify/v1"
CAPABILITY = "live_exact_notify"


async def admit(session_id: str, body: ReceiptBinding, request: Request) -> dict:
    """Reuse inbox admission while preserving notify intent and native identity."""
    from .models import SendMessageRequest
    from .routes.live_sessions import post_live_message

    if session_id != body.expected_session_id:
        raise HTTPException(409, detail="exact notify requires its original session")
    result = await post_live_message(
        session_id, SendMessageRequest(
            sender=body.sender, body=body.body, kind="notify", delivery="steer",
            expected_session_id=session_id, idempotency_key=body.idempotency_key,
            exact_session=True,
        ), request,
    )
    return {
        "protocol": PROTOCOL, "version": 1, "binding": body.model_dump(),
        "admission": result.model_dump(),
    }


def send(args: argparse.Namespace) -> None:
    """Send only through the selected peer's exact informational operation."""
    from . import __main__ as core
    from .session_targeting_cli import _resolve_prompt

    if (
        not args.json or not args.expected_session_id or not args.idempotency_key or not args.sender
        or args.target != args.expected_session_id
        or any(getattr(args, name, False) for name in (
            "new", "force", "queue", "steer", "interrupt", "notify", "status_check",
            "wait_reply", "receipt_only", "plugin_context_stdin", "full_history",
            "live_exact", "live_receipt_only",
        ))
        or getattr(args, "expected_native_session_id", None) is not None
        or getattr(args, "native_invocation_file", None) is not None
        or getattr(args, "reply_to", None) is not None
        or getattr(args, "delivery", None) not in (None, "steer")
        or getattr(args, "kind", None) not in (None, "notify")
    ):
        raise SendRefused(
            "refused_invalid", reason="invalid_live_notify_options", retryable=False,
            target=args.target, error="exact informational notify options required",
        )
    body = ReceiptBinding(
        version=1, expected_session_id=args.expected_session_id,
        idempotency_key=args.idempotency_key, sender=args.sender,
        body=_resolve_prompt(args, required=True),
    )
    client = core._get_client(ensure=False)
    request = protocol_floored_request(
        client, max(1, getattr(args, "min_daemon_protocol", None) or 1),
        capabilities={CAPABILITY: 1},
    )
    try:
        result = request(
            "POST", f"/api/v1/live-sessions/{quote(args.target, safe='')}/messages/exact-notify/v1",
            body.model_dump(), allow_retry=False,
        )
    except BridgeClientError as exc:
        raise SendRefused(
            "refused_unavailable", reason="live_notify_unproven", retryable=False,
            target=args.target, error=str(exc.detail),
        ) from exc
    if not isinstance(result, dict) or (
        result.get("protocol") != PROTOCOL or type(result.get("version")) is not int
        or result["version"] != 1 or result.get("binding") != body.model_dump()
        or not isinstance(result.get("admission"), dict)
        or result["admission"].get("session_id") != args.target
        or type(result["admission"].get("message_id")) is not int
        or result["admission"]["message_id"] <= 0
    ):
        raise SendRefused(
            "refused_unavailable", reason="live_notify_unproven", retryable=False,
            target=args.target, error="invalid exact notify response",
        )
    emit(result)
