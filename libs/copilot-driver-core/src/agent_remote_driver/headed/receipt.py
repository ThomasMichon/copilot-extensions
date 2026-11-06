"""Resolve the native child without mistaking its retained coordinator for Copilot."""

from __future__ import annotations

import hmac
import json
from pathlib import Path

from ..session_host.osutil import pid_alive, process_start_time

PROCESS_RECEIPT = "native-child-v1"
NONCE_ENV = "AGENT_BRIDGE_SESSION_HOST_NONCE"


def _unique(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("Duplicate headed receipt field")
        value[key] = item
    return value


def native_child(
    state_file: Path, *, coordinator_pid: int, coordinator_birth: str,
    session_id: str, nonce: str,
) -> tuple[int, str]:
    """The original caller supplies its retained process identity and private binding."""
    if (
        not state_file.is_absolute() or state_file.is_symlink()
        or type(coordinator_pid) is not int or coordinator_pid < 1
        or type(coordinator_birth) is not str or not coordinator_birth
        or type(session_id) is not str or not session_id
        or type(nonce) is not str or len(nonce) != 64
    ):
        raise ValueError("Headed receipt requires the original caller binding")
    with state_file.open("rb") as stream:
        raw = stream.read(65537)
    if len(raw) > 65536:
        raise ValueError("Headed receipt exceeds its bound")
    state = json.loads(raw, object_pairs_hook=_unique)
    if (
        type(state) is not dict
        or type(state.get("version")) is not int or state["version"] != 2
        or state.get("mode") != "headed"
        or state.get("process_receipt") != PROCESS_RECEIPT
        or state.get("state") not in {"native_starting", "running", "control_unavailable"}
        or state.get("session_id") != session_id
        or type(state.get("nonce")) is not str
        or not hmac.compare_digest(state["nonce"], nonce)
        or type(state.get("pid")) is not int or state["pid"] != coordinator_pid
        or type(state.get("host_pid")) is not int or state["host_pid"] != coordinator_pid
        or state.get("host_process_start_time") != coordinator_birth
        or type(state.get("child_pid")) is not int or state["child_pid"] < 1
        or state["child_pid"] == coordinator_pid
        or type(state.get("child_process_start_time")) is not str
        or not state["child_process_start_time"]
    ):
        raise ValueError("Headed native receipt is unconfirmed")
    child_pid, child_birth = state["child_pid"], state["child_process_start_time"]
    if (
        process_start_time(coordinator_pid) != coordinator_birth or not pid_alive(coordinator_pid)
        or process_start_time(child_pid) != child_birth or not pid_alive(child_pid)
    ):
        raise ValueError("Headed process identity changed or is unavailable")
    return child_pid, child_birth
