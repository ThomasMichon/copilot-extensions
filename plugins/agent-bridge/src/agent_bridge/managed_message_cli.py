"""Ordinary managed sends return durable input evidence, never turn admission."""

from __future__ import annotations

import argparse
from typing import Any
import uuid


def send(
    client: Any, args: argparse.Namespace, session_id: str, native_session_id: str, prompt: str,
) -> None:
    from . import __main__ as core
    from .client import BridgeClientError
    from .send_outcome import SendRefused, emit

    key = getattr(args, "idempotency_key", None) or uuid.uuid4().hex
    if not 1 <= len(key) <= 512:
        raise SendRefused(
            "refused_invalid", reason="managed_message_key", retryable=False, target=session_id,
            error="managed message idempotency key must contain 1-512 characters",
        )
    args.idempotency_key = key
    try:
        result = client.submit_managed_message(
            session_id, prompt, idempotency_key=key,
            expected_native_session_id=native_session_id, caller_id=core._caller_id_for(args),
        )
    except BridgeClientError as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if (exc.status, detail.get("code")) in {
            (404, "session_not_found"), (409, "managed_message_key_conflict"),
        } or exc.status == 426:
            raise SendRefused(
                "refused_conflict" if exc.status == 409 else "refused_unavailable",
                reason=str(detail.get("code") or "unsupported_durable_acceptance"),
                error=str(exc), retryable=False, target=session_id,
            ) from exc
        raise
    if args.json:
        emit({
            **result, "bridge": args._bridge_send_identity, "accepted": True, "delivered": None,
            "outcome": "duplicate" if result.get("duplicate") else "queued",
        })
        return
    print(f"[>] Accepted by managed session queue {session_id} (input {result['queue_id']})")
    print(f"    native: {result.get('native_session_id')}; invocation key: {key}")
    print(f"    delivery state: {result['delivery_state']}")
    print("    Acceptance does not confirm delivery or resume a stopped recipient.")
