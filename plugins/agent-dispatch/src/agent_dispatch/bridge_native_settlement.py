"""Explicit original-key settlement through the selected Bridge command owner."""

from __future__ import annotations

import hashlib
import json
from typing import Any, TypeGuard

from .bridge_command_delivery import _command_result
from .procutil import run_ssh_command
from .queue_records import TaskError


def original_request_digest(
    bridge_session: str, native_session: str, prompt: str, *, idempotency_key: str,
    no_resume: bool = False,
) -> str:
    """Encode the Bridge version-1 original-request digest without importing it."""
    document = {
        "session_id": bridge_session,
        "request": {
            "invocation_key": idempotency_key,
            "expected_native_session_id": native_session,
            "prompt": prompt,
            "caller_id": f"agent-dispatch:{idempotency_key}",
            "no_resume": no_resume,
        },
    }
    return hashlib.sha256(json.dumps(
        document, sort_keys=True, allow_nan=False, separators=(",", ":"),
    ).encode("utf-8")).hexdigest()


def valid_settlement(
    receipt: Any, bridge_session: str, native_session: str, prompt: str, *,
    idempotency_key: str, no_resume: bool = False,
) -> TypeGuard[dict[str, Any]]:
    """Require the original identity and a provider-issued, request-bound result."""
    if not isinstance(receipt, dict) or (
        type(receipt.get("version")) is not int or receipt["version"] != 1
        or receipt.get("session_id") != bridge_session
        or receipt.get("native_session_id") != native_session
        or receipt.get("invocation_key") != idempotency_key
        or receipt.get("queued") is not False
        or receipt.get("outcome") != receipt.get("admission")
    ):
        return False
    if receipt.get("admission") == "accepted":
        return (
            type(receipt.get("turn_index")) is int and receipt["turn_index"] >= 0
            and receipt.get("fenced") is not True
        )
    return (
        receipt.get("admission") == "not_admitted" and receipt.get("fenced") is True
        and "turn_index" not in receipt
        and receipt.get("request_digest") == original_request_digest(
            bridge_session, native_session, prompt, idempotency_key=idempotency_key,
            no_resume=no_resume,
        )
    )


def native_prompt_settlement(
    bridge_session: str, native_session: str, prompt: str, *,
    idempotency_key: str, machine: str | None, no_resume: bool = False,
) -> dict[str, Any]:
    """Negotiate an explicit repair; never fall back, send, ensure or retry."""
    from . import bridge

    # Keep local discovery and execution on the same selected runtime prefix.
    prefix = bridge._agent_bridge_launch_prefix()

    def call(arguments: list[str], private_input: str = "") -> Any:
        result = _command_result(
            arguments, private_input, machine=machine, launch_prefix=lambda: prefix,
            run_ssh=run_ssh_command, timeout=20.0,
        )
        if result is None or result.returncode:
            raise TaskError("Bridge settlement unavailable; retained send remains unknown")
        try:
            return json.loads(result.stdout)
        except (TypeError, ValueError) as exc:
            raise TaskError(
                "Bridge settlement response malformed; retained send remains unknown"
            ) from exc

    support = call(["version", "--contracts-json"])
    contracts = support.get("contracts") if isinstance(support, dict) else None
    contract = contracts.get("agent-bridge.send") if isinstance(contracts, dict) else None
    if not (
        isinstance(support, dict) and support.get("schema") == "agent-bridge.cli-contracts"
        and type(support.get("version")) is int and support["version"] == 1
        and isinstance(contract, dict)
        and type(contract.get("version")) is int and contract["version"] == 1
        and isinstance(contract.get("capabilities"), list)
        and "native-send-settlement" in contract["capabilities"]
    ):
        raise TaskError(
            "executing Bridge CLI lacks native-send-settlement; no settlement attempted"
        )
    # The capable CLI negotiates the actual running daemon and prohibits HTTP retries.
    receipt = call([
        "--json", "send", bridge_session,
        "--expected-native-session-id", native_session,
        "--idempotency-key", idempotency_key,
        "--caller", f"agent-dispatch:{idempotency_key}",
        "--prompt-file", "-", *(["--no-resume"] if no_resume else []), "--settle-native-send", "--no-wait",
    ], prompt)
    if not valid_settlement(
        receipt, bridge_session, native_session, prompt, idempotency_key=idempotency_key,
        no_resume=no_resume,
    ):
        raise TaskError(
            "Bridge settlement does not prove original wake outcome; send remains unknown"
        )
    return receipt
