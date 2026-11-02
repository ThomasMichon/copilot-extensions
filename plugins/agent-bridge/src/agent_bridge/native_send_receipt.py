"""Read-only CLI observation of an exact ordinary native-send admission."""

from __future__ import annotations

import argparse
import http.client
import json
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .client import BridgeClientError, BridgeConnectionError
from .client_protocol_floor import protocol_floored_request
from .native_send_admission import NativeSendPrompt
from .protocol import (
    NATIVE_SEND_IDEMPOTENCY_PROTOCOL_VERSION,
    NATIVE_SEND_RECEIPT_CAPABILITY,
    NATIVE_SEND_RECEIPT_CAPABILITY_VERSION,
)
from .send_outcome import SendRefused
from .startup_observation import StartupObservationError

if TYPE_CHECKING:
    from .client import BridgeClient


def send(args: argparse.Namespace) -> None:
    """Run the receipt-only CLI branch without growing the general send router."""
    from . import __main__ as core
    from .session_targeting_cli import _resolve_prompt
    from .startup_observation import configure_client
    from .send_outcome import emit

    validate_options(args)
    client = core._get_client(ensure=False)
    configure_client(client, args)
    result = observe(
        client, args.target, _resolve_prompt(args, required=True),
        expected_native_session_id=args.expected_native_session_id,
        idempotency_key=args.idempotency_key, caller_id=core._caller_id_for(args),
        min_protocol=getattr(args, "min_daemon_protocol", None) or 0,
    )
    emit({**result, "outcome": "accepted"})


def validate_options(args: argparse.Namespace) -> None:
    if (
        not isinstance(args.target, str) or not args.target.strip()
        or not all(isinstance(value, str) and value.strip() and len(value) <= 1024 for value in (
            getattr(args, "expected_native_session_id", None),
            getattr(args, "idempotency_key", None),
        ))
        or any(getattr(args, field, None) is not None for field in (
            "expected_session_id", "sender", "reply_to",
        ))
        or any(getattr(args, field, False) for field in (
            "force", "plugin_context_stdin", "steer", "interrupt", "queue",
            "notify", "status_check", "wait_reply", "full_history", "new",
        ))
        or getattr(args, "kind", None) not in (None, "prompt")
        or getattr(args, "delivery", None) not in (None, "steer")
        or getattr(args, "reply_timeout", None) not in (None, 120.0)
    ):
        raise SendRefused(
            "refused_invalid", reason="invalid_native_receipt_options", retryable=False,
            target=args.target,
            error="ordinary --receipt-only requires a native identity and idempotency key "
                  "without alternate delivery options",
        )


def observe(
    client: BridgeClient, session_id: str, prompt: str, *,
    expected_native_session_id: str, idempotency_key: str, caller_id: str | None,
    min_protocol: int = 0,
) -> dict[str, Any]:
    try:
        request = NativeSendPrompt(
            prompt=prompt, expected_native_session_id=expected_native_session_id,
            invocation_key=idempotency_key, caller_id=caller_id,
        )
    except ValueError as exc:
        raise SendRefused(
            "refused_invalid", reason="invalid_native_receipt_request", retryable=False,
            target=session_id, error="invalid ordinary native receipt request",
        ) from exc
    read = protocol_floored_request(
        client, max(NATIVE_SEND_IDEMPOTENCY_PROTOCOL_VERSION, min_protocol),
        capabilities={NATIVE_SEND_RECEIPT_CAPABILITY: NATIVE_SEND_RECEIPT_CAPABILITY_VERSION},
    )
    started = time.monotonic()

    def unproven() -> StartupObservationError:
        return StartupObservationError(
            None, "native-prompt-receipt", time.monotonic() - started,
            reason="native_receipt_unproven",
        )

    try:
        result = read(
            "POST",
            f"/api/v1/sessions/{quote(session_id, safe='')}/turns/native-bound/send/receipt",
            request.model_dump(mode="json"),
        )
    except BridgeClientError as exc:
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
        if exc.status not in {404, 409, 422, 426}:
            raise unproven() from exc
        raise SendRefused(
            "refused_conflict" if code in {
                "native_invocation_conflict", "expected_native_mismatch",
            } else "refused_unavailable",
            reason=code or (
                "unsupported_protocol" if exc.status == 426 else "native_receipt_unproven"
            ),
            retryable=False, target=session_id, error=str(exc.detail),
        ) from exc
    except (BridgeConnectionError, OSError, http.client.HTTPException,
            UnicodeError, json.JSONDecodeError) as exc:
        raise unproven() from exc
    if (
        not isinstance(result, dict) or type(result.get("version")) is not int
        or result["version"] != 1 or result.get("session_id") != session_id
        or result.get("native_session_id") != expected_native_session_id
        or result.get("invocation_key") != idempotency_key
        or result.get("admission") != "accepted" or result.get("queued") is not False
        or type(result.get("turn_index")) is not int or result["turn_index"] < 0
    ):
        raise unproven()
    return result
