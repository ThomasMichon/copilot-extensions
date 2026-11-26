"""One capability-selected acceptance attempt, never a fallback turn submission."""

from __future__ import annotations

import http.client
import json
from typing import Any
from urllib.parse import quote

from .managed_messages import CAPABILITY, ManagedMessage


def submit(client: Any, session_id: str, message: ManagedMessage) -> dict[str, Any]:
    from .client import BridgeClientError, BridgeConnectionError
    from .client_protocol_floor import protocol_floored_request
    from .protocol import HTTP_PROTOCOL_MIN_SUPPORTED

    attempted = False

    def mark_attempt() -> None:
        nonlocal attempted
        attempted = True

    def unknown() -> BridgeClientError:
        return BridgeClientError(502, {
            "code": "managed_message_acceptance_unknown", "session_id": session_id,
            "idempotency_key": message.idempotency_key,
            "message": "Recover only this original target, key and input; do not send a new operation",
        })

    try:
        request = protocol_floored_request(
            client, HTTP_PROTOCOL_MIN_SUPPORTED, capabilities={CAPABILITY: 1},
        )
        result = request(
            "POST", f"/api/v1/sessions/{quote(session_id, safe='')}/messages",
            message.model_dump(), allow_retry=False, _on_request=mark_attempt,
        )
        if (
            not isinstance(result, dict) or result.get("session_id") != session_id
            or result.get("idempotency_key") != message.idempotency_key
            or (message.expected_native_session_id is not None
                and result.get("native_session_id") != message.expected_native_session_id)
            or type(result.get("queue_id")) is not int or result["queue_id"] <= 0
            or result.get("queued") is not True or type(result.get("duplicate")) is not bool
            or not isinstance(result.get("delivery_state"), str)
            or result.get("delivery_state") not in {
                "queued", "handoff_committed", "removed", "target_changed",
            }
            or not (
                result.get("turn_index") is None
                or type(result["turn_index"]) is int and result["turn_index"] >= 0
            )
        ):
            raise unknown()
        return result
    except BridgeClientError as exc:
        if not attempted or (isinstance(exc.detail, dict) and (exc.status, exc.detail.get("code")) in {
            (404, "session_not_found"), (409, "managed_message_key_conflict"),
        }):
            raise
        raise unknown() from exc
    except (BridgeConnectionError, OSError, http.client.HTTPException, UnicodeError, json.JSONDecodeError,
            KeyboardInterrupt) as exc:
        if not attempted:
            raise
        raise unknown() from exc
