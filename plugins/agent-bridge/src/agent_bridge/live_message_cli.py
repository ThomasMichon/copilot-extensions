"""Attributed live inbox delivery and receipt rendering for ``send``."""

from __future__ import annotations

import argparse
import sys


def deliver_to_live_session(client, args: argparse.Namespace, session_id: str, prompt: str,
                            *, core, remote_address=None) -> None:
    from .client import BridgeClientError
    from .live_message_models import validate_live_reply_timeout
    from .send_outcome import SendRefused, emit, live_outcome, refusal_from_live_error

    wait = getattr(args, "wait_reply", False) and not getattr(args, "no_wait", False)
    wait_timeout = getattr(args, "reply_timeout", 120.0)
    if wait:
        try:
            wait_timeout = validate_live_reply_timeout(wait_timeout)
        except ValueError as exc:
            raise SendRefused(
                "refused_invalid", reason="reply_timeout", error=f"--reply-timeout {exc}",
                retryable=False, target=session_id,
            ) from exc
    sender = core._live_sender_label(args)
    reply_to = (
        getattr(args, "reply_to", None) if getattr(args, "plugin_context_stdin", False)
        else core._live_reply_to(args)
    )
    kind = core._live_message_kind(args)
    delivery = core._live_message_delivery(args)
    idempotency = getattr(args, "idempotency_key", None)
    expected_session_id = getattr(args, "expected_session_id", None)
    delivery_options = {}
    if idempotency:
        delivery_options["idempotency_key"] = idempotency
    if expected_session_id:
        delivery_options["expected_session_id"] = expected_session_id
    if getattr(args, "plugin_readiness", None) is not None:
        delivery_options["plugin_readiness"] = args.plugin_readiness
    try:
        if remote_address is not None:
            from .live_peer_address import send_remote_peer
            result = send_remote_peer(
                client, args, remote_address, sender=sender, reply_to=reply_to,
                kind=kind, delivery=delivery, prompt=prompt,
            )
        else:
            result = client.send_live_message(
                session_id,
                sender=sender,
                body=prompt,
                reply_to=reply_to,
                kind=kind,
                delivery=delivery,
                wait=wait,
                wait_timeout=wait_timeout,
                **delivery_options,
            )
    except BridgeClientError as exc:
        refused = refusal_from_live_error(exc, session_id)
        if refused is None:
            raise
        raise refused from exc
    session_id = result.get("session_id", session_id)
    if args.json:
        # A daemon that reports duplicates always includes the field; an older
        # one omits it, so a keyed retry can't be told from a first delivery.
        duplicate_known = not idempotency or "duplicate" in result
        receipt = result.get("receipt") or {}
        stage = receipt.get("stage")
        payload = {"delivered": stage == "consumed" if stage else None,
                   "accepted": True, "target": session_id, **result,
                   "outcome": live_outcome(result, delivery, duplicate_known=duplicate_known)}
        if not duplicate_known:
            payload["duplicate"] = None
        emit(payload)
        return
    mid = result.get("message_id")
    kind_note = "" if kind == "prompt" else f", kind {kind}"
    delivery_note = "" if delivery == "queue" else f", delivery {delivery}"
    print(
        f"[>] Accepted by live session inbox {result.get('session_id', session_id)} "
        f"(message {mid}, from {sender}{kind_note}{delivery_note})"
    )
    receipt = result.get("receipt") or {}
    print(f"    receipt: {receipt.get('stage') or 'unavailable (delivery evidence unavailable)'}")
    if result.get("receipt_error"):
        print(f"    receipt evidence unavailable: {result['receipt_error']}", file=sys.stderr)
    observe = (
        f"observe on hosting machine {remote_address.machine}"
        if remote_address is not None else "observe"
    )
    if receipt.get("stage") and result.get("receipt_current") is True:
        print(f"    {observe}: agent-bridge read {session_id} --message-id {mid} --json")
    else:
        print(f"    {observe}: agent-bridge read {session_id} --json")
        if receipt.get("stage"):
            print("    (message receipt observation is unavailable; inspect session state instead)")
        else:
            from .protocol import LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION

            print(
                "    (message receipt observation requires daemon protocol "
                f"{LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION} or newer)"
            )
    if reply_to:
        print(f"    reply-to: {reply_to}")
    else:
        print("    (no reply-to: this sender is not a live session; reply won't route)")
    if not wait:
        return
    if result.get("duplicate"):
        print(
            "\n[..] Duplicate request; no additional reply wait was started "
            "(inspect the existing message receipt rather than resend)."
        )
        return
    if result.get("replied"):
        reply = result.get("reply")
        print(f"\n[<] Reply from {session_id}:")
        print(reply if reply else "    (turn completed with no assistant text)")
    else:
        print(
            f"\n[..] No reply within {wait_timeout:g}s "
            "(send remains accepted; inspect its receipt rather than resend)."
        )
