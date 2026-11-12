"""Capability-selected delivery observation, separate from sending another turn."""

from __future__ import annotations

from .client_protocol_floor import protocol_floored_request
from .live_delivery_state import CAPABILITY
from .live_message_models import SendMessageRequest, validate_live_reply_timeout


def wait_seconds(value: str) -> float:
    import argparse

    try:
        return validate_live_reply_timeout(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def send_and_wait(client, session_id: str, *, timeout: float, **message) -> dict:
    seconds = validate_live_reply_timeout(timeout)
    body = SendMessageRequest(**message).model_dump()
    request = protocol_floored_request(client, 1, capabilities={CAPABILITY: 1})
    return request(
        "POST", f"/api/v1/live-sessions/{session_id}/delivery",
        {"message": body, "timeout": seconds}, request_timeout=seconds + 15.0,
    ) or {}


def observe(client, session_id: str, message_id: int, *, timeout: float = 0.0) -> dict:
    seconds = validate_live_reply_timeout(timeout)
    request = protocol_floored_request(client, 1, capabilities={CAPABILITY: 1})
    return request(
        "POST", f"/api/v1/live-sessions/{session_id}/messages/{message_id}/delivery",
        {"timeout": seconds}, request_timeout=seconds + 15.0,
    ) or {}


def command(args) -> None:
    import json
    from . import __main__ as core

    client = core._get_client(ensure=False)
    result = observe(client, args.target, args.message_id, timeout=args.timeout)
    if args.json:
        print(json.dumps(result, indent=2))
        return
    print(f"receipt: {result['receipt']['stage']}; delivery: {result['outcome']}")
    attention = result["attention"]
    print(f"attention: {attention['state']} ({attention.get('reason') or 'no observed blockers'})")
    if attention["reasons"]:
        print("pending: " + ", ".join(attention["reasons"]))
