"""Legacy and exact represented-message requests over the existing inbox API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

if TYPE_CHECKING:
    from .client import BridgeClient


def send_live_message(
    client: BridgeClient,
    session_id: str,
    *,
    sender: str,
    body: str,
    reply_to: str | None = None,
    kind: str = "prompt",
    delivery: str = "steer",
    wait: bool = False,
    wait_timeout: float | None = None,
    idempotency_key: str | None = None,
    expected_session_id: str | None = None,
    plugin_readiness: dict[str, Any] | None = None,
    exact_session: bool = False,
) -> dict[str, Any]:
    """Submit one inbox request with its selected compatibility and retry policy."""
    from .client import BridgeClientError
    from .client_protocol_floor import protocol_floored_request
    from .protocol import LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION, PLUGIN_READINESS_PROTOCOL_VERSION

    payload: dict[str, Any] = {"sender": sender, "body": body}
    if reply_to:
        payload["reply_to"] = reply_to
    if kind and kind != "prompt":
        payload["kind"] = kind
    if delivery and delivery != "steer":
        payload["delivery"] = delivery
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    if expected_session_id:
        payload["expected_session_id"] = expected_session_id
    if exact_session:
        if not session_id or expected_session_id != session_id or plugin_readiness is not None:
            raise BridgeClientError(422, "exact live send requires its raw expected session")
        payload["exact_session"] = True
    request = client._request
    request_timeout = None
    if plugin_readiness is not None:
        request = protocol_floored_request(client, PLUGIN_READINESS_PROTOCOL_VERSION)
        payload["plugin_readiness"] = plugin_readiness
        request_timeout = float(plugin_readiness.get("timeout_seconds", 120)) + 15
    if wait:
        from .live_message_models import validate_live_reply_timeout

        try:
            seconds = validate_live_reply_timeout(wait_timeout)
        except ValueError as exc:
            raise BridgeClientError(422, f"wait_timeout {exc}") from exc
        client._require_live_message_receipts()
        request = protocol_floored_request(
            client,
            max(
                LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION,
                PLUGIN_READINESS_PROTOCOL_VERSION if plugin_readiness is not None else 0,
            ),
        )
        payload["wait"] = True
        if wait_timeout is not None:
            payload["wait_timeout"] = seconds
        request_timeout = (
            seconds
            + 15.0
            + (
                float(plugin_readiness.get("timeout_seconds", 120))
                if plugin_readiness is not None
                else 0
            )
        )
    target = session_id
    if exact_session:
        request = protocol_floored_request(
            client,
            LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION if wait else 1,
            capabilities={"live_exact_send": 1},
        )
        target = quote(session_id, safe="")
    request_options: dict[str, Any] = {"allow_retry": False} if exact_session else {}
    result = (
        request(
            "POST",
            f"/api/v1/live-sessions/{target}/messages",
            payload,
            request_timeout=request_timeout,
            **request_options,
        )
        or {}
    )
    if exact_session and (
        not isinstance(result, dict)
        or result.get("session_id") != session_id
        or type(result.get("message_id")) is not int
        or result["message_id"] <= 0
    ):
        from .startup_observation import StartupObservationError

        raise StartupObservationError(
            None, "live-message-admission", 0.0, reason="admission_unknown"
        )
    return result
