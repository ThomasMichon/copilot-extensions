"""Exact live-CLI delivery and cursor-neutral recovery of one retained retry."""

from __future__ import annotations

import json
import logging
import math

from . import bridge
from .bridge_command_delivery import _command_result

log = logging.getLogger("agent-dispatch.supervisor")


def _receipt(value: object, native: str) -> bool:
    return isinstance(value, dict) and (
        value.get("session_id") == native
        and type(value.get("message_id")) is int and value["message_id"] > 0
        and value.get("stage") in {"admitted", "sdk_accepted", "consumed"}
        and isinstance(value.get("admitted_at"), (int, float))
        and not isinstance(value["admitted_at"], bool) and math.isfinite(value["admitted_at"])
    )


def deliver_retry(invocation: dict, *, observe_only: bool) -> tuple[str, str]:
    """Only the caller with a confirmed new admission can send on a receipt miss."""
    native, key, prompt = (
        invocation["native_id"], invocation["idempotency_key"], invocation["prompt"],
    )
    sender = f"agent-dispatch:{key}"
    arguments = [
        "--json", "send", native, "--expected-session-id", native,
        "--idempotency-key", key, "--sender", sender, "--prompt-file", "-", "--no-wait",
    ]

    def run(extra: list[str]) -> tuple[int | None, dict | None]:
        try:
            result = _command_result(
                [*arguments, *extra], prompt, machine=invocation["machine"],
                launch_prefix=bridge._agent_bridge_launch_prefix, run_ssh=bridge.run_ssh_command,
                timeout=20.0,
            )
        except RuntimeError:
            log.warning("pool retry transport identity is unavailable for %s", key, exc_info=True)
            return None, None
        if result is None:
            return None, None
        try:
            value = json.loads(result.stdout)
        except (ValueError, TypeError):
            log.warning("pool retry transport response is malformed for %s", key)
            return result.returncode, None
        return result.returncode, value if isinstance(value, dict) else None

    status, value = run(["--live-receipt-only"])
    binding = {
        "version": 1, "expected_session_id": native, "idempotency_key": key,
        "sender": sender, "body": prompt,
    }
    if status != 0 or value is None or (
        value.get("protocol") != "live-keyed-receipt/v1"
        or type(value.get("version")) is not int or value["version"] != 1
        or value.get("binding") != binding or "receipt" not in value
    ):
        return "startup_unknown", "exact live receipt observation unavailable"
    if _receipt(value["receipt"], native):
        return "startup_succeeded", "exact original live inbox admission observed"
    if value["receipt"] is not None or observe_only:
        return "startup_unknown", "original live inbox admission is unproven; no send replayed"
    status, value = run(["--live-exact"])
    if status == 0 and value is not None and (
        value.get("protocol") == "live-keyed-receipt/v1"
        and type(value.get("version")) is int and value["version"] == 1
        and value.get("binding") == binding
        and _receipt(value.get("receipt"), native)
    ):
        return "startup_succeeded", "exact live inbox admission returned by original caller"
    return "startup_unknown", "original live send did not establish an exact admission outcome"
