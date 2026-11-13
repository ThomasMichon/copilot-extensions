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
    body = SendMessageRequest(**message).model_dump(exclude={"exact_session"}, exclude_none=True)
    result = client.send_live_message(session_id, **body)
    if not isinstance(result, dict):
        from .send_outcome import SendUnknown

        raise SendUnknown(session_id, idempotency_key=message.get("idempotency_key"))
    return wait_for_receipt(client, result, timeout=seconds)


def observe(
    client, session_id: str, message_id: int, *, timeout: float = 0.0, machine: str | None = None,
    project: str | None = None,
) -> dict:
    seconds = validate_live_reply_timeout(timeout)
    if machine is not None:
        from urllib.parse import quote

        return client._request(
            "POST", client._remote_path(
                machine, (f"/projects/{quote(project, safe='')}" if project is not None else "")
                + f"/live-sessions/{quote(session_id, safe='')}/messages/{message_id}/delivery",
            ), {"timeout": seconds}, request_timeout=seconds + 30.0,
        ) or {}
    request = protocol_floored_request(client, 1, capabilities={CAPABILITY: 1})
    return request(
        "POST", f"/api/v1/live-sessions/{session_id}/messages/{message_id}/delivery",
        {"timeout": seconds}, request_timeout=seconds + 15.0,
    ) or {}


def wait_for_receipt(
    client, result: dict, *, timeout: float, machine: str | None = None, project: str | None = None,
) -> dict:
    """Observe after admission; an unavailable observer never becomes a failed send."""
    from http.client import HTTPException

    from .client import BridgeClientError, BridgeConnectionError

    session_id, message_id = result.get("session_id"), result.get("message_id")
    try:
        if not session_id or type(message_id) is not int or message_id <= 0:
            raise ValueError("admission returned no observable receipt identity")
        observed = observe(
            client, session_id, message_id, timeout=timeout, machine=machine, project=project,
        )
        if not isinstance(observed, dict):
            raise ValueError("delivery observation is not an object")
        receipt = observed.get("receipt") or {}
        if (not isinstance(receipt, dict) or receipt.get("session_id") != session_id
                or type(receipt.get("message_id")) is not int
                or receipt["message_id"] != message_id):
            raise ValueError("delivery observation does not match the admitted receipt")
        attention = observed.get("attention")
        if (observed.get("outcome") not in ("consumed", "blocked", "timeout", "detached", "unavailable")
                or not isinstance(attention, dict)
                or attention.get("availability") not in ("available", "unavailable")
                or attention.get("state") not in ("blocked", "ready", "unknown")
                or not isinstance(attention.get("reasons"), list)
                or any(not isinstance(reason, str) for reason in attention["reasons"])):
            raise ValueError("delivery observation has an unsupported outcome or attention state")
    except (BridgeClientError, BridgeConnectionError, HTTPException, OSError, ValueError) as exc:
        observed = {
            "receipt": result.get("receipt"), "outcome": "unavailable",
            "attention": {"availability": "unavailable", "state": "unknown", "reasons": [],
                          "reason": "admission retained; delivery observation unavailable"},
        }
        result["receipt_error"] = str(exc)
    if observed["outcome"] == "unavailable":
        result["receipt_current"] = False
    if observed.get("receipt") is not None:
        result["receipt"] = observed["receipt"]
    result["delivery_observation"] = observed
    return result


def command(args) -> None:
    import json
    from . import __main__ as core

    client = core._get_client(ensure=False)
    result = observe(
        client, args.target, args.message_id, timeout=args.timeout, machine=getattr(args, "machine", None),
        project=getattr(args, "project", None),
    )
    if args.json:
        print(json.dumps(result, indent=2))
        return
    print(f"receipt: {result['receipt']['stage']}; delivery: {result['outcome']}")
    attention = result["attention"]
    print(f"attention: {attention['state']} ({attention.get('reason') or 'no observed blockers'})")
    if attention["reasons"]:
        print("pending: " + ", ".join(attention["reasons"]))
