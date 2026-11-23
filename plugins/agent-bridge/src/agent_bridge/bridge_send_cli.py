"""Opt-in local bridge selection over the existing exact admission owners."""

from __future__ import annotations

import argparse
import http.client
import json
from typing import Any, NoReturn


def selected(args: argparse.Namespace) -> bool:
    """Keep ordinary send selectors unchanged until explicit adoption."""
    enabled = bool(getattr(args, "bridge", False))
    if not enabled and any(
        getattr(args, key, None) for key in ("all_projects", "machine", "namespace")
    ):
        raise SystemExit("--all-projects, --machine and --namespace require --bridge")
    return enabled


def _refuse(args: argparse.Namespace, reason: str, error: str) -> NoReturn:
    from .send_outcome import SendRefused

    raise SendRefused(
        "refused_invalid" if reason == "bridge_send_options" else "refused_unavailable",
        reason=reason,
        error=error,
        retryable=False,
        target=args.target,
    )


def send(args: argparse.Namespace) -> None:
    """Submit one ordinary prompt without legacy resolution, resume or creation."""
    from . import __main__ as core
    from .bridge_listing_cli import _scope
    from .client import BridgeClientError, BridgeConnectionError
    from .client_protocol_floor import hold_protocol_floor
    from .hosting_admission_cli import configure_client
    from .send_outcome import SendRefused
    from .session_targeting_cli import (
        _deliver_to_live_session,
        _resolve_prompt,
        _submit_and_stream,
    )

    if (
        any(
            getattr(args, name, None)
            for name in (
                "new",
                "force",
                "queue",
                "full_history",
                "wait_reply",
                "wait_delivery",
                "notify",
                "status_check",
                "interrupt",
                "plugin_context_stdin",
                "live_exact_notify",
                "live_receipt_only",
                "live_exact",
                "receipt_only",
                "no_resume",
                "settle_native_send",
            )
        )
        or any(
            getattr(args, name, None) is not None
            for name in (
                "native_invocation_file",
                "expected_native_session_id",
                "expected_session_id",
                "startup_communication_timeout",
                "startup_process_timeout",
            )
        )
        or getattr(args, "kind", None) not in (None, "prompt")
        or getattr(args, "delivery", None) not in (None, "steer")
        or getattr(args, "idempotency_key", None) == ""
    ):
        _refuse(
            args,
            "bridge_send_options",
            "--bridge sends ordinary prompts only; creation, resume, delivery controls "
            "and receipt/settlement modes require their existing commands",
        )
    prompt = _resolve_prompt(args, required=True)
    if prompt is None or not prompt.strip():
        _refuse(args, "bridge_send_options", "--bridge requires a nonempty prompt")
    client = core._get_client(ensure=False)
    configure_client(client, args)
    if floor := getattr(args, "min_daemon_protocol", None):
        hold_protocol_floor(client, floor)
    try:
        row: dict[str, Any] = client.resolve_bridge(
            args.target,
            local_only=True,
            **_scope(args),
        )["bridge"]
    except BridgeClientError as exc:
        _refuse(args, "bridge_selection_unavailable", str(exc))
    native = row.get("session_id")
    if row.get("status") != "running" or not isinstance(native, str) or not native:
        _refuse(args, "bridge_send_unavailable", "selected bridge has no running conversation")
    managed = row.get("managed_session_id")
    bound = argparse.Namespace(**vars(args))
    owned = isinstance(managed, str) and bool(managed)
    if owned:
        if any(getattr(args, name, None) for name in ("sender", "reply_to")):
            _refuse(
                args,
                "bridge_send_options",
                "--sender and --reply-to require a registered-session inbox",
            )
        bound.expected_native_session_id = native
        bound.no_resume = True
    else:
        registered = row.get("registered_session_id")
        if not isinstance(registered, str) or registered != native:
            _refuse(
                args, "bridge_send_unavailable", "selected bridge has no exact admission owner"
            )
        bound.expected_session_id = native
        bound.exact_session = True
    args._bridge_send_identity = {
        "session_id": native,
        "managed_session_id": managed if owned else None,
        "registered_session_id": None if owned else native,
    }
    try:
        if owned:
            assert isinstance(managed, str)
            _submit_and_stream(
                client,
                bound,
                managed,
                prompt,
                caller_id=core._caller_id_for(args),
            )
        else:
            _deliver_to_live_session(client, bound, native, prompt)
    except SendRefused as exc:
        if exc.reason != "native_admission_unproven":
            raise
        _unknown(exc)
    except BridgeClientError as exc:
        if exc.status < 500:
            raise
        _unknown(exc)
    except (
        BridgeConnectionError,
        OSError,
        http.client.HTTPException,
        UnicodeError,
        json.JSONDecodeError,
    ) as exc:
        _unknown(exc)


def _unknown(exc: Exception) -> NoReturn:
    from .startup_observation import StartupObservationError

    raise StartupObservationError(
        None,
        "bridge-prompt-admission",
        0.0,
        reason="admission_unknown",
    ) from exc


def register(parser: argparse.ArgumentParser) -> None:
    """Add explicit adoption without changing legacy send syntax or defaults."""
    parser.add_argument(
        "--bridge",
        action="store_true",
        help="Send an ordinary prompt to an exact local bridge; never resume or replace "
        "(requires bridge_inventory: 2 and the selected owner's native admission contract)",
    )
    parser.add_argument("--all-projects", action="store_true")
    parser.add_argument("--machine", help="Filter the conversation's machine (with --bridge)")
    parser.add_argument("--namespace", help="Filter the conversation's namespace (with --bridge)")
