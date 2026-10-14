"""Identity-bound admission for ordinary prompts, never a replacement transport."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .client import BridgeClientError
from .protocol import NATIVE_BOUND_PROMPT_PROTOCOL_VERSION
from .send_outcome import SendRefused

if TYPE_CHECKING:
    from .client import BridgeClient


def submit_native_bound_prompt(
    client: BridgeClient, session_id: str, prompt: str, *,
    expected_native_session_id: str, caller_id: str | None,
) -> dict[str, Any]:
    """Submit only to the exact running native conversation under its turn lock."""
    if not expected_native_session_id or not client.daemon_supports(NATIVE_BOUND_PROMPT_PROTOCOL_VERSION):
        raise SendRefused(
            "refused_unavailable", reason="unsupported_native_fence", retryable=False,
            target=session_id, error="daemon does not support native-bound prompt admission",
        )
    try:
        result = client._request(
            "POST", f"/api/v1/sessions/{quote(session_id, safe='')}/turns/native-bound",
            {"prompt": prompt, "queue": False, "no_resume": True,
             "expected_native_session_id": expected_native_session_id, "caller_id": caller_id},
        ) or {}
    except BridgeClientError as exc:
        if exc.status not in (404, 409):
            raise
        detail = str(exc.detail)
        busy = exc.status == 409 and "not idle" in detail
        raise SendRefused(
            "refused_busy" if busy else "refused_unavailable",
            reason="busy" if busy else "native_admission_refused",
            retryable=busy, target=session_id, error=detail,
        ) from exc
    if (result.get("session_id") != session_id
            or result.get("native_session_id") != expected_native_session_id
            or result.get("queued") is not False
            or type(result.get("turn_index")) is not int):
        raise SendRefused(
            "refused_unavailable", reason="native_admission_unproven", retryable=False,
            target=session_id, error="native-bound prompt admission returned no matching receipt",
        )
    return result
