"""Ordinary bridge selection over the existing exact admission owners."""

from __future__ import annotations

import argparse
import http.client
import json
from typing import Any, NoReturn


def _specialized(args: argparse.Namespace) -> bool:
    return (
        str(getattr(args, "target", "")).startswith("live:")
        or any(getattr(args, name, None) for name in (
            "new", "force", "queue", "full_history", "wait_reply", "wait_delivery",
            "notify", "status_check", "interrupt", "plugin_context_stdin",
            "live_exact_notify", "live_receipt_only", "live_exact", "receipt_only",
            "no_resume", "settle_native_send",
        ))
        or any(getattr(args, name, None) is not None for name in (
            "native_invocation_file", "expected_native_session_id", "expected_session_id",
            "startup_communication_timeout", "startup_process_timeout",
        ))
        or getattr(args, "kind", None) not in (None, "prompt")
        or getattr(args, "delivery", None) not in (None, "steer")
    )


def selected(args: argparse.Namespace) -> bool:
    """Default ordinary parser calls to bridges; retain explicit legacy modes."""
    choice = getattr(args, "bridge", False)
    enabled = not _specialized(args) if choice is None else bool(choice)
    host = getattr(args, "host", None)
    if not enabled and (host is not None or any(
        getattr(args, key, None) for key in ("all_projects", "machine", "namespace")
    )):
        raise SystemExit(
            "--all-projects, --machine, --namespace and --host require exact bridge "
            "selection; omit legacy modes or select --bridge explicitly"
        )
    if host is not None and not host.strip():
        raise SystemExit("--host requires a nonempty configured Bridge host")
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
    )

    if (
        _specialized(args)
        or getattr(args, "idempotency_key", None) == ""
        or getattr(args, "host", None) is not None and getattr(args, "expand", None)
        or getattr(args, "host", None) is not None and getattr(args, "require_agent_hosting", False)
    ):
        _refuse(
            args,
            "bridge_send_options",
            "--bridge sends ordinary prompts only; creation, resume, delivery controls "
            "and receipt/settlement modes require their existing commands; "
            "--host supports neither an expandable event feed nor end-to-end "
            "--require-agent-hosting enforcement",
        )
    prompt = _resolve_prompt(args, required=True)
    if prompt is None or not prompt.strip():
        _refuse(args, "bridge_send_options", "--bridge requires a nonempty prompt")
    try:
        client = core._get_client(ensure=False, propagate_errors=True)
        configure_client(client, args)
        if floor := getattr(args, "min_daemon_protocol", None):
            hold_protocol_floor(client, floor)
        host = getattr(args, "host", None)
        location: dict[str, Any] = {"hosts": [host]} if host else {"local_only": True}
        row: dict[str, Any] = client.resolve_bridge(
            args.target,
            **location,
            **_scope(args),
        )["bridge"]
    except (BridgeClientError, BridgeConnectionError) as exc:
        _refuse(args, "bridge_selection_unavailable", str(exc))
    native = row.get("session_id")
    if not isinstance(native, str) or not native:
        _refuse(args, "bridge_send_unavailable", "selected bridge has no existing conversation")
    if host:
        from .bridge_remote_send_cli import send as send_remote

        if row.get("status") != "running":
            _refuse(args, "bridge_send_unavailable", "selected bridge has no running conversation")
        if (row.get("route") or {}).get("host") != host:
            _refuse(args, "bridge_selection_unavailable", "selected Bridge host changed")
        send_remote(client, args, row, prompt)
        return
    managed = row.get("managed_session_id")
    registered = row.get("registered_session_id")
    if not managed and registered != native:
        managed = row.get("retained_managed_session_id")
    bound = argparse.Namespace(**vars(args))
    owned = isinstance(managed, str) and bool(managed)
    if owned:
        if any(getattr(args, name, None) for name in ("sender", "reply_to")):
            _refuse(
                args,
                "bridge_send_options",
                "--sender and --reply-to require a registered-session inbox",
            )
    else:
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
            from .managed_message_cli import send as send_managed

            assert isinstance(managed, str)
            send_managed(client, args, managed, native, prompt)
        else:
            _deliver_to_live_session(client, bound, native, prompt)
    except SendRefused as exc:
        if (
            exc.reason == "native_admission_unproven"
            or (
                owned
                and exc.reason in {"native_admission_refused", "busy"}
                and not _proven_managed_refusal(exc, str(managed))
            )
            or (
                not owned
                and exc.reason == "not_found"
                and getattr(exc.__cause__, "detail", None) != "live session not found"
            )
        ):
            _unknown(exc)
        raise
    except BridgeClientError as exc:
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


def _proven_managed_refusal(exc: Exception, session_id: str) -> bool:
    cause = exc.__cause__
    status: object = getattr(cause, "status", None)
    detail: object = getattr(cause, "detail", None)
    if isinstance(detail, dict):
        detail = detail.get("code")
    if not isinstance(detail, str):
        return False
    return (
        status == 404
        and detail == f"Session {session_id} not found"
        or status == 409
        and (
            detail.startswith(
                ("expected_native_mismatch:", "session_stopped:", "session_not_running:")
            )
            or detail.startswith(f"Session {session_id} is ")
            and detail.endswith(", not idle")
        )
    )


def register(parser: argparse.ArgumentParser) -> None:
    """Expose the exact default and an explicit legacy-selector escape path."""
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--bridge",
        action="store_true",
        default=None,
        help="Require exact bridge selection (the ordinary prompt default); never resume or replace "
        "(requires bridge_inventory: 2 and the selected owner's native admission contract)",
    )
    selection.add_argument(
        "--legacy-selector",
        dest="bridge",
        action="store_false",
        help="Use the prior agent/profile selector and its existing resume behavior; "
        "specialized delivery and receipt modes retain that selector automatically",
    )
    parser.add_argument("--all-projects", action="store_true")
    parser.add_argument("--machine", help="Filter the exact conversation's machine")
    parser.add_argument("--namespace", help="Filter the exact conversation's namespace")
    parser.add_argument(
        "--host",
        help="Select one configured Bridge host; requires remote_native_prompt: 1 "
        "for managed sends or remote_exact_live_prompt: 1 for registered inboxes. "
        "Managed text sends wait for a bounded completed result; inbox sends report admission only",
    )
