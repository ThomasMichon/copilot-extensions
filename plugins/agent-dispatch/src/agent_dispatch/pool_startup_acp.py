"""Ordinary ACP startup uses Bridge's native admission, never the live inbox."""

from __future__ import annotations

import json

from . import bridge
from .bridge_command_delivery import _command_result
from .ordinary_agent_facts import identity
from .pool_startup_retry_contract import driver_binding


def _run(arguments: list[str], prompt: str, machine: str) -> tuple[int | None, dict | None]:
    try:
        result = _command_result(
            arguments, prompt, machine=machine,
            launch_prefix=bridge._agent_bridge_launch_prefix, run_ssh=bridge.run_ssh_command,
            timeout=20.0,
        )
    except RuntimeError:
        return None, None
    if result is None:
        return None, None
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        return result.returncode, None
    return result.returncode, value if isinstance(value, dict) else None


def reachable(reservation: dict, native: str) -> bool:
    """Read the exact Bridge record on its retained host, without resume or alias."""
    bound = driver_binding(reservation, native)
    actual = identity(reservation["agent_info"])
    status, value = _run(
        ["--json", "status", bound["bridge_session_id"], "--full"], "",
        reservation["execution_machine"],
    )
    return status == 0 and isinstance(value, dict) and (
        value.get("session_id") == actual.bridge
        and value.get("acp_session_id") == value.get("durable_session_id") == actual.native
        and value.get("worktree_id") == actual.worktree
        and value.get("project") == actual.project and value.get("target_dir") == actual.path
        and value.get("mode") == "acp" and value.get("read_only") is False
        and value.get("at_rest") is False and value.get("status") in {"idle", "running"}
        and value.get("liveness") in {None, "active"}
        and (value["status"] == "idle" or value["liveness"] == "active")
    )


def deliver(invocation: dict, *, observe_only: bool) -> tuple[str, str]:
    """Only an acknowledged new retry may send after a qualified receipt miss."""
    session, native, key = (
        invocation["bridge_session_id"], invocation["native_id"], invocation["idempotency_key"],
    )
    arguments = [
        "--json", "send", session, "--expected-native-session-id", native,
        "--idempotency-key", key, "--caller", f"agent-dispatch:{key}",
        "--prompt-file", "-", "--no-wait",
    ]

    def receipt(value: dict | None) -> bool:
        return isinstance(value, dict) and (
            type(value.get("version")) is int and value["version"] == 1
            and value.get("session_id") == session and value.get("native_session_id") == native
            and value.get("invocation_key") == key and value.get("admission") == "accepted"
            and value.get("queued") is False and type(value.get("turn_index")) is int
            and value["turn_index"] >= 0
        )

    status, value = _run(
        [*arguments, "--receipt-only"], invocation["prompt"], invocation["machine"],
    )
    if status == 0 and receipt(value):
        return "startup_succeeded", "exact original ACP native admission observed"
    if observe_only or status != 69 or not isinstance(value, dict) or (
        value.get("outcome") != "refused_unavailable"
        or value.get("reason") != "native_receipt_missing" or value.get("target") != session
    ):
        return "startup_unknown", "original ACP native admission is unproven; no send replayed"
    status, value = _run(arguments, invocation["prompt"], invocation["machine"])
    if status == 0 and receipt(value):
        return "startup_succeeded", "exact ACP native admission returned by original caller"
    if status == 75 and isinstance(value, dict) and (
        value.get("target") == session and value.get("outcome") == "refused_busy"
        and value.get("reason") == "busy"
    ):
        return "startup_rejected", "original ACP native admission refused a busy turn"
    return "startup_unknown", "original ACP send did not establish an exact admission outcome"
