"""Explicit-host adoption of exact managed and registered-inbox admission."""

from __future__ import annotations

import argparse
import http.client
import json
import sys
from typing import TYPE_CHECKING, Any
import uuid

if TYPE_CHECKING:
    from .bridge_observation_cli import _Target


def send(client: Any, args: argparse.Namespace, row: dict[str, Any], prompt: str) -> None:
    from . import __main__ as core
    from .bridge_observation_cli import _Target, _snapshot
    from .bridge_send_cli import _refuse, _unknown
    from .client import BridgeClientError, BridgeConnectionError
    from .client_read_budget import ReadDeadlineExpired
    from .send_outcome import SendRefused, emit, prompt_outcome

    managed = row.get("managed_session_id")
    if not isinstance(managed, str) or not managed:
        registered = row.get("registered_session_id")
        if not isinstance(registered, str) or not registered or registered != row["session_id"]:
            _refuse(args, "bridge_send_unavailable", "selected bridge has no exact admission owner")
        _send_registered(client, args, registered, prompt)
        return
    if any(getattr(args, name, None) for name in ("sender", "reply_to")):
        _refuse(args, "bridge_send_options", "--sender and --reply-to require a registered-session inbox")
    target = _Target(managed, False, row["session_id"], args.host)
    wait = not args.json and not args.no_wait
    if wait:
        try:
            _snapshot(client, target, max_items=1, max_text_chars=256)
        except (
            BridgeClientError, BridgeConnectionError, OSError, ReadDeadlineExpired,
            http.client.HTTPException, UnicodeError, json.JSONDecodeError,
        ) as exc:
            _refuse(args, "bridge_result_unavailable", str(exc))
    key = getattr(args, "idempotency_key", None)
    if key is None:
        key = uuid.uuid4().hex
        args.idempotency_key = key
    args._bridge_send_identity = {
        "host": args.host, "session_id": target.native_session_id,
        "managed_session_id": managed, "registered_session_id": None,
    }
    try:
        result = client.submit_remote_native_prompt(
            args.host, session_id=managed, native_session_id=target.native_session_id,
            invocation_key=key, prompt=prompt, caller_id=core._caller_id_for(args),
        )
    except BridgeClientError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") == "native_prompt_admission_unknown":
            _unknown(exc)
        if exc.status == 400 and detail.get("code") == "invalid_request":
            _refuse(args, "bridge_send_options", str(exc))
        refusal = detail.get("refusal")
        if detail.get("code") == "native_prompt_refused":
            if (
                isinstance(refusal, dict)
                and refusal.get("outcome") in {
                    "refused_invalid", "refused_busy", "refused_unavailable", "refused_conflict",
                }
                and isinstance(refusal.get("reason"), str)
                and isinstance(refusal.get("error"), str)
                and type(refusal.get("retryable")) is bool
            ):
                raise SendRefused(
                    refusal["outcome"], reason=refusal["reason"], error=refusal["error"],
                    retryable=refusal["retryable"], target=args.target,
                ) from exc
            _unknown(exc)
        _refuse(args, "bridge_remote_admission_unavailable", str(exc))
    except (
        BridgeConnectionError, OSError, http.client.HTTPException, UnicodeError,
        json.JSONDecodeError, ReadDeadlineExpired,
    ) as exc:
        # The client converts every post-attempt failure to bound unknown admission.
        _refuse(args, "bridge_remote_admission_unavailable", str(exc))
    if args.json:
        emit({**result, "bridge": args._bridge_send_identity, "outcome": prompt_outcome(result)})
        return
    print(f"[>] Bridge {args.host}/{managed} -- native {target.native_session_id}, turn {result['turn_index']}")
    print(f"    invocation key: {key}")
    if not wait:
        print("[>] Prompt submitted (--no-wait)")
        return
    _wait_for_result(client, target, result["turn_index"], core._phased_timeouts().command)


def _send_registered(
    client: Any, args: argparse.Namespace, session_id: str, prompt: str,
) -> None:
    from .bridge_send_cli import _refuse, _unknown
    from .client import BridgeClientError, BridgeConnectionError
    from .client_read_budget import ReadDeadlineExpired
    from .send_outcome import emit
    from .session_targeting_cli import _live_sender_label

    if getattr(args, "reply_to", None) is not None:
        _refuse(args, "bridge_send_options", "--reply-to is not supported by remote exact inbox admission")
    sender = _live_sender_label(args)
    key = getattr(args, "idempotency_key", None)
    if key is None:
        key = uuid.uuid4().hex
        args.idempotency_key = key
    args._bridge_send_identity = {
        "host": args.host, "session_id": session_id,
        "managed_session_id": None, "registered_session_id": session_id, "sender": sender,
    }
    try:
        result = client.submit_remote_exact_live_prompt(
            args.host, session_id=session_id, invocation_key=key, sender=sender, prompt=prompt,
        )
    except BridgeClientError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") == "exact_live_prompt_admission_unknown":
            _unknown(exc)
        if exc.status == 400 and detail.get("code") == "invalid_request":
            _refuse(args, "bridge_send_options", str(exc))
        _refuse(args, "bridge_remote_admission_unavailable", str(exc))
    except (
        BridgeConnectionError, OSError, http.client.HTTPException, UnicodeError,
        json.JSONDecodeError, ReadDeadlineExpired,
    ) as exc:
        # The client preserves unknown admission after any attempted POST.
        _refuse(args, "bridge_remote_admission_unavailable", str(exc))
    if args.json:
        emit({
            **result, "bridge": args._bridge_send_identity, "outcome": "accepted",
            "accepted": True, "delivered": None, "duplicate": None,
            "idempotency_key": key,
        })
        return
    print(
        f"[>] Accepted by live session inbox {args.host}/{session_id} "
        f"(message {result['message_id']}, from {sender})"
    )
    print(f"    invocation key: {key}")
    print("    Consumption and completion are unconfirmed; inspect the original message, do not resend.")


def _wait_for_result(client: Any, target: _Target, turn_index: int, timeout: float) -> None:
    from . import __main__ as core
    from .bridge_observation_cli import _snapshot
    from .client import BridgeClientError, BridgeConnectionError
    from .client_read_budget import ReadDeadlineExpired, command_deadline, pause, remaining

    def unobserved(reason: str) -> None:
        print(
            f"[>] Prompt remains accepted; result observation {reason}. "
            "Inspect the original host, owner and invocation key; do not resend.",
            file=sys.stderr,
        )

    try:
        with command_deadline(timeout):
            while True:
                remaining()
                result = _snapshot(client, target, max_items=1, max_text_chars=6000)
                remaining()
                identity = result["identity"]
                if (
                    identity.get("successor_id")
                    or identity.get("current_session_id") != target.session_id
                ):
                    unobserved("stopped on identity change")
                    return
                latest = result["latest_result"]
                value = latest.get("value") or {}
                index = value.get("turn_index")
                if latest["availability"] in {"available", "partial"} and type(index) is int:
                    if index == turn_index:
                        print(value.get("text") or "(turn completed with no assistant text)")
                        if latest["availability"] == "partial":
                            print("[>] The completed result is partial.", file=sys.stderr)
                        if (latest.get("truncation") or {}).get("truncated"):
                            print("[>] The completed result text is truncated.", file=sys.stderr)
                        return
                    if index > turn_index:
                        unobserved("unavailable after a later turn")
                        return
                if result["state"]["session_status"] in {"stopped", "ended", "failed"}:
                    unobserved("stopped before the selected turn result was observed")
                    return
                if (
                    result["state"]["session_status"] == "idle"
                    and result["state"].get("at_rest") is True
                    and latest["availability"] == "unsupported_for_target"
                ):
                    unobserved("unavailable (native result origin is unconfirmed)")
                    return
                pause(core._RECONNECT_BACKOFF)
    except ReadDeadlineExpired:
        unobserved("timed out")
    except KeyboardInterrupt:
        unobserved("interrupted")
    except (
        BridgeClientError, BridgeConnectionError, OSError,
        http.client.HTTPException, UnicodeError, json.JSONDecodeError,
    ) as exc:
        unobserved(f"unavailable ({exc})")
