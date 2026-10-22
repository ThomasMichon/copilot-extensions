"""UTF-8 file/stdin codec and non-fallback keyed native prompt requests."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
from urllib.parse import quote
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .client import BridgeClient

from .client_protocol_floor import protocol_floored_request
from .native_keyed_admission import KeyedNativePrompt
from .protocol import KEYED_NATIVE_PROMPT_PROTOCOL_VERSION
from .send_outcome import SendRefused, emit
from .client import BridgeClientError


def add_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--native-invocation-file",
                        help="UTF-8 keyed native JSON; '-' reads stdin; returns admission receipt only")
    parser.add_argument("--receipt-only", action="store_true",
                        help="Read the existing native invocation receipt; never admit on a miss")


def validate_options(args: argparse.Namespace) -> None:
    if (
        any(getattr(args, field, None) is not None for field in (
            "prompt", "prompt_file", "expected_session_id", "idempotency_key",
            "sender", "reply_to",
        ))
        or any(getattr(args, field, False) for field in (
            "force", "plugin_context_stdin", "steer", "interrupt", "queue",
            "notify", "status_check", "wait_reply", "full_history", "new",
        ))
        or getattr(args, "kind", None) not in (None, "prompt")
        or getattr(args, "delivery", None) not in (None, "steer")
        or getattr(args, "reply_timeout", None) not in (None, 120.0)
    ):
        raise SendRefused("refused_invalid", reason="mixed_native_invocation",
                          retryable=False, target=args.target,
                          error="keyed native invocation cannot mix alternate prompt/delivery options")
    if getattr(args, "native_invocation_file", None) == "":
        raise SendRefused("refused_invalid", reason="invalid_native_invocation",
                          retryable=False, target=args.target,
                          error="invalid UTF-8 native invocation request")


def send(args: argparse.Namespace, client: BridgeClient) -> None:
    validate_options(args)
    path = args.native_invocation_file
    try:
        if path == "-":
            raw = sys.stdin.buffer.read().decode("utf-8")
        else:
            raw = Path(path).read_text(encoding="utf-8")
        request = KeyedNativePrompt.model_validate_json(raw)
    except (OSError, ValueError, UnicodeError) as exc:
        raise SendRefused("refused_invalid", reason="invalid_native_invocation",
                          retryable=False, target=args.target,
                          error="invalid UTF-8 native invocation request") from exc
    expected = getattr(args, "expected_native_session_id", None)
    if expected != request.expected_native_session_id:
        raise SendRefused("refused_conflict", reason="invocation_mismatch",
                          retryable=False, target=args.target,
                          error="keyed native invocation flags differ from its immutable request")
    floor = max(KEYED_NATIVE_PROMPT_PROTOCOL_VERSION,
                getattr(args, "min_daemon_protocol", None) or 0)
    client._request = protocol_floored_request(client, floor)
    suffix = "/receipt" if args.receipt_only else ""
    try:
        result = client._request(
            "POST", f"/api/v1/sessions/{quote(args.target, safe='')}/turns/native-bound/keyed{suffix}",
            request.model_dump(mode="json"),
        )
    except BridgeClientError as exc:
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
        raise SendRefused(
            "refused_conflict" if code in {
                "native_invocation_conflict", "expected_native_mismatch",
                "native_workspace_binding_changed", "native_admission_conflict",
            } else "refused_unavailable", reason=code or "keyed_native_unavailable",
            retryable=False, target=args.target, error=str(exc.detail),
        ) from exc
    if (
        not isinstance(result, dict) or type(result.get("version")) is not int
        or result["version"] != 1
        or result.get("session_id") != args.target
        or result.get("native_session_id") != expected
        or result.get("invocation_key") != request.invocation_key
        or type(result.get("turn_index")) is not int
        or result["turn_index"] != request.binding.expected_turn_index
        or result.get("queued") is not False or result.get("admission") != "accepted"
    ):
        raise SendRefused("refused_unavailable", reason="keyed_receipt_unproven",
                          retryable=False, target=args.target, error="invalid keyed admission receipt")
    emit({**result, "outcome": "delivered"})
