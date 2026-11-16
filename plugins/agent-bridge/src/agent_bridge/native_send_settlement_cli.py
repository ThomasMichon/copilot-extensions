"""Explicit original-key settlement, never prompt admission or receipt fallback."""

from __future__ import annotations

import argparse
import http.client
import json
import time
from typing import Any, TypeGuard
from urllib.parse import quote

from .client import BridgeClient, BridgeClientError, BridgeConnectionError
from .client_protocol_floor import protocol_floored_request
from .native_send_admission import NativeSendPrompt
from .native_send_settlement import CAPABILITY, request_digest
from .protocol import NATIVE_SEND_IDEMPOTENCY_PROTOCOL_VERSION
from .send_outcome import SendRefused, emit
from .startup_observation import StartupObservationError


def validate_options(args: argparse.Namespace) -> None:
    """Reject alternate delivery before reading input or discovering a client."""
    if (
        not isinstance(args.target, str) or not args.target.strip()
        or not all(isinstance(value, str) and value.strip() and len(value) <= 1024 for value in (
            getattr(args, "expected_native_session_id", None),
            getattr(args, "idempotency_key", None),
        ))
        or any(getattr(args, field, None) is not None for field in (
            "expected_session_id", "sender", "reply_to", "native_invocation_file",
            "startup_communication_timeout", "startup_process_timeout",
        ))
        or any(getattr(args, field, False) for field in (
            "force", "plugin_context_stdin", "steer", "interrupt", "queue",
            "notify", "status_check", "wait_reply", "full_history", "new",
            "receipt_only", "live_exact_notify", "live_receipt_only", "live_exact",
            "wait_delivery",
        ))
        or getattr(args, "kind", None) not in (None, "prompt")
        or getattr(args, "delivery", None) not in (None, "steer")
        or getattr(args, "reply_timeout", None) not in (None, 120.0)
    ):
        raise SendRefused(
            "refused_invalid", reason="invalid_native_settlement_options", retryable=False,
            target=args.target,
            error="native settlement requires the original native identity, key and prompt "
                  "without alternate delivery modes",
        )


def _valid_result(
    result: Any, session_id: str, request: NativeSendPrompt,
) -> TypeGuard[dict[str, Any]]:
    if not isinstance(result, dict) or (
        type(result.get("version")) is not int or result["version"] != 1
        or result.get("session_id") != session_id
        or result.get("native_session_id") != request.expected_native_session_id
        or result.get("invocation_key") != request.invocation_key
        or result.get("queued") is not False
    ):
        return False
    if result.get("admission") == "accepted":
        return type(result.get("turn_index")) is int and result["turn_index"] >= 0
    return (
        result.get("admission") == "not_admitted" and result.get("fenced") is True
        and "turn_index" not in result
        and result.get("request_digest") == request_digest(session_id, request)
    )


def settle(
    client: BridgeClient, session_id: str, request: NativeSendPrompt, *, min_protocol: int = 0,
) -> dict[str, Any]:
    """Settle through a capable original provider; uncertain writes are not replayed."""
    call = protocol_floored_request(
        client, max(NATIVE_SEND_IDEMPOTENCY_PROTOCOL_VERSION, min_protocol),
        capabilities={CAPABILITY: 1},
    )
    started = time.monotonic()

    def unknown() -> StartupObservationError:
        return StartupObservationError(
            None, "native-send-settlement", time.monotonic() - started,
            reason="native_send_settlement_unknown",
        )

    try:
        result = call(
            "POST",
            f"/api/v1/sessions/{quote(session_id, safe='')}/turns/native-bound/send/settle",
            request.model_dump(mode="json"), allow_retry=False,
        )
    except BridgeClientError as exc:
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
        if not (
            exc.status in {401, 403, 422, 426}
            or exc.status == 404 and code == "native_session_unavailable"
            or exc.status == 409 and code in {
                "native_invocation_conflict", "expected_native_mismatch",
                "native_send_history_unproven",
            }
            or exc.status == 503 and code == "draining"
        ):
            raise unknown() from exc
        raise SendRefused(
            "refused_conflict" if code in {
                "native_invocation_conflict", "expected_native_mismatch",
            } else "refused_unavailable",
            reason=code or ("unsupported_protocol" if exc.status == 426 else "native_settlement_unproven"),
            retryable=False, target=session_id, error=str(exc.detail),
        ) from exc
    except (BridgeConnectionError, OSError, http.client.HTTPException,
            UnicodeError, json.JSONDecodeError) as exc:
        raise unknown() from exc
    if not _valid_result(result, session_id, request):
        raise unknown()
    return result


def send(args: argparse.Namespace) -> None:
    """Use the existing send parser while preserving explicit settlement semantics."""
    from . import __main__ as core
    from .session_targeting_cli import _resolve_prompt

    validate_options(args)
    prompt = _resolve_prompt(args, required=True)
    if prompt is None:
        raise SendRefused(
            "refused_invalid", reason="invalid_native_settlement_request", retryable=False,
            target=args.target, error="native settlement requires the original prompt",
        )
    try:
        request = NativeSendPrompt(
            prompt=prompt, expected_native_session_id=args.expected_native_session_id,
            invocation_key=args.idempotency_key, caller_id=core._caller_id_for(args),
            no_resume=getattr(args, "no_resume", False),
        )
    except ValueError as exc:
        raise SendRefused(
            "refused_invalid", reason="invalid_native_settlement_request", retryable=False,
            target=args.target, error="invalid original native settlement request",
        ) from exc
    try:
        client = core._get_client(ensure=False)
    except (BridgeConnectionError, OSError) as exc:
        raise SendRefused(
            "refused_unavailable", reason="native_settlement_unavailable", retryable=False,
            target=args.target, error=str(exc),
        ) from exc
    result = settle(
        client, args.target, request,
        min_protocol=getattr(args, "min_daemon_protocol", None) or 0,
    )
    emit({**result, "outcome": result["admission"]})
