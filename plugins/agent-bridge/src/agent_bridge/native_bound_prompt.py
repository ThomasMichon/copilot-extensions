"""Identity-bound admission for ordinary prompts, never a replacement transport."""

from __future__ import annotations

import http.client
import json
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .client import BridgeClientError
from .protocol import NATIVE_BOUND_PROMPT_PROTOCOL_VERSION
from .send_outcome import SendRefused

if TYPE_CHECKING:
    from .client import BridgeClient


class NativeSendNotAttempted(SendRefused):
    """Request-bound refusal before this CLI attempts native prompt admission."""

    def __init__(self, target: str, native: str, key: str, error: str) -> None:
        super().__init__(
            "refused_unavailable", reason="native_send_not_attempted", retryable=True,
            target=target, error=error,
        )
        self.native = native
        self.key = key

    def payload(self) -> dict[str, Any]:
        return {
            **super().payload(), "native_send_refusal_version": 1,
            "native_session_id": self.native, "invocation_key": self.key,
            "admission_status": "not_attempted",
        }


def submit_native_bound_prompt(
    client: BridgeClient, session_id: str, prompt: str, *,
    expected_native_session_id: str, caller_id: str | None,
    idempotency_key: str | None = None,
    no_resume: bool = False,
) -> dict[str, Any]:
    """Submit only to the exact running native conversation under its turn lock."""
    from .client import BridgeConnectionError

    try:
        supported = bool(expected_native_session_id) and client.daemon_supports(
            NATIVE_BOUND_PROMPT_PROTOCOL_VERSION,
        )
    except BridgeConnectionError as exc:
        if idempotency_key is not None:
            raise NativeSendNotAttempted(
                session_id, expected_native_session_id, idempotency_key, str(exc),
            ) from exc
        raise
    if not supported:
        raise SendRefused(
            "refused_unavailable", reason="unsupported_native_fence", retryable=False,
            target=session_id, error="daemon does not support native-bound prompt admission",
        )
    if idempotency_key is not None:
        from .client import BridgeConnectionError
        from .client_protocol_floor import protocol_floored_request
        from .protocol import (
            NATIVE_SEND_IDEMPOTENCY_PROTOCOL_VERSION,
            NATIVE_SEND_NO_RESUME_CAPABILITY, NATIVE_SEND_NO_RESUME_CAPABILITY_VERSION,
        )
        from .startup_observation import StartupObservationError

        client._request = protocol_floored_request(  # type: ignore[method-assign]
            client, NATIVE_SEND_IDEMPOTENCY_PROTOCOL_VERSION,
            capabilities={NATIVE_SEND_NO_RESUME_CAPABILITY: NATIVE_SEND_NO_RESUME_CAPABILITY_VERSION}
            if no_resume else None,
        )
        started = time.monotonic()
        attempted = False

        def unknown() -> StartupObservationError:
            return StartupObservationError(
                None, "native-prompt-admission", time.monotonic() - started,
                reason="native_send_acceptance_unknown",
            )

        try:
            target = client.get_session(session_id)
            if (
                not isinstance(target, dict) or not target.get("session_id")
                or target.get("acp_session_id") != expected_native_session_id
            ):
                raise SendRefused(
                    "refused_unavailable", reason="expected_native_mismatch", retryable=False,
                    target=session_id, error="native send target does not match the retained conversation",
                )
            native_target = target["session_id"]
            attempted = True
            result = client._request(
                "POST", f"/api/v1/sessions/{quote(native_target, safe='')}/turns/native-bound/send",
                {"prompt": prompt, "expected_native_session_id": expected_native_session_id,
                 "invocation_key": idempotency_key, "caller_id": caller_id,
                 **({"no_resume": True} if no_resume else {})},
            )
        except BridgeClientError as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            code = detail.get("code", "native_send_unavailable")
            busy = exc.status == 409 and "not idle" in str(code)
            proven_refusal = (
                exc.status in {422, 426}
                or exc.status == 404 and code == "native_session_unavailable"
                or exc.status == 409 and (busy or code in {
                    "native_invocation_conflict", "expected_native_mismatch",
                    "native_session_stopped",
                })
            )
            if attempted and not proven_refusal:
                raise unknown() from exc
            raise SendRefused(
                "refused_busy" if busy else (
                    "refused_conflict" if code == "native_invocation_conflict" else "refused_unavailable"
                ),
                reason="busy" if busy else str(code), retryable=busy,
                target=session_id, error=str(exc.detail),
            ) from exc
        except (BridgeConnectionError, OSError, http.client.HTTPException,
                UnicodeError, json.JSONDecodeError) as exc:
            if attempted:
                raise unknown() from exc
            if isinstance(exc, BridgeConnectionError):
                raise NativeSendNotAttempted(
                    session_id, expected_native_session_id, idempotency_key, str(exc),
                ) from exc
            raise
        if (
            not isinstance(result, dict) or result.get("session_id") != native_target
            or type(result.get("version")) is not int or result["version"] != 1
            or result.get("native_session_id") != expected_native_session_id
            or result.get("invocation_key") != idempotency_key
            or result.get("admission") != "accepted" or result.get("queued") is not False
            or type(result.get("turn_index")) is not int or result["turn_index"] < 0
        ):
            raise unknown()
        return result
    try:
        result = client._request(
            "POST", f"/api/v1/sessions/{quote(session_id, safe='')}/turns/native-bound",
            {"prompt": prompt, "queue": False, "no_resume": True,
             "expected_native_session_id": expected_native_session_id, "caller_id": caller_id},
        ) or {}
    except BridgeClientError as exc:
        if exc.status not in (404, 409):
            raise
        error = str(exc.detail)
        busy = exc.status == 409 and "not idle" in error
        raise SendRefused(
            "refused_busy" if busy else "refused_unavailable",
            reason="busy" if busy else "native_admission_refused",
            retryable=busy, target=session_id, error=error,
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
