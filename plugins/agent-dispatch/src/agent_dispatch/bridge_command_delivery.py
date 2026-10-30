"""Shared standard-command transport for existing-owner Bridge prompts."""

from __future__ import annotations

import logging
import json
import shlex
import shutil
import subprocess
from collections.abc import Callable

from . import bridge_remote, remote_dispatch
from .procutil import no_window_kwargs

log = logging.getLogger(__name__)


def steering_prompt(task_id: str, message: str | None) -> str:
    return message or (
        f"Task {task_id} received steering. Resume, run `agent-dispatch steer "
        f"take {task_id} --all`, re-read the task for any direction or "
        f"ambient-state changes, and continue."
    )


def _command_result(
    argv: list[str],
    prompt: str,
    *,
    machine: str | None,
    launch_prefix: Callable[[], list[str] | None],
    run_ssh: Callable[..., subprocess.CompletedProcess],
    timeout: float | None,
) -> subprocess.CompletedProcess | None:
    """Run the same Bridge CLI locally or through the existing SSH transport."""
    local = remote_dispatch.local_machine()
    remote = bridge_remote.normalize_host(machine) if machine is not None else None
    if remote is not None and local is None:
        raise RuntimeError("local machine identity is unavailable for Bridge delivery")
    normalized_local = bridge_remote.normalize_host(local) if local is not None else None
    is_remote = remote is not None and remote != normalized_local
    if is_remote:
        ssh = shutil.which("ssh")
        if ssh is None:
            log.warning("Bridge command delivery requires an available SSH executable")
            return None
        # marketplace-isolation: allow remote-management
        command = " ".join(shlex.quote(arg) for arg in ["agent-bridge", *argv])
        cmd = [ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", remote, command]
    else:
        exe = launch_prefix()
        if exe is None:
            log.warning("Bridge command delivery requires an available local runtime")
            return None
        cmd = [*exe, *argv]
    try:
        if is_remote:
            result = run_ssh(cmd, input=prompt, timeout=timeout)
        else:
            result = subprocess.run(
                cmd, input=prompt, check=False, capture_output=True, text=True,
                timeout=timeout, **no_window_kwargs(),
            )
    except (subprocess.SubprocessError, OSError):
        log.warning("Bridge command delivery did not establish acceptance", exc_info=True)
        return None
    if result.returncode:
        log.warning(
            "Bridge command delivery returned exit status %s: %s",
            result.returncode, (result.stderr or "").strip()[:2048],
        )
    return result


def send_command(
    argv: list[str], prompt: str, *, machine: str | None,
    launch_prefix: Callable[[], list[str] | None],
    run_ssh: Callable[..., subprocess.CompletedProcess], timeout: float | None,
) -> bool:
    """Run the standard Bridge send and retain its existing acceptance result."""
    result = _command_result(
        argv, prompt, machine=machine, launch_prefix=launch_prefix,
        run_ssh=run_ssh, timeout=timeout,
    )
    return result is not None and result.returncode == 0


def native_prompt_receipt(
    bridge_session: str, native_session: str, prompt: str, *,
    idempotency_key: str, machine: str | None,
) -> dict:
    """Observe an exact prior send; a miss never admits or resumes a prompt."""
    from . import bridge
    from .queue_records import TaskError

    result = _command_result(
        [
            "--json", "send", bridge_session,
            "--expected-native-session-id", native_session,
            "--idempotency-key", idempotency_key,
            "--caller", f"agent-dispatch:{idempotency_key}",
            "--prompt-file", "-", "--receipt-only", "--no-wait",
        ],
        prompt, machine=machine, launch_prefix=bridge._agent_bridge_launch_prefix,
        run_ssh=bridge.run_ssh_command, timeout=20.0,
    )
    if result is None or result.returncode:
        raise TaskError("Bridge receipt observation unavailable; retained send remains unknown")
    try:
        receipt = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise TaskError("Bridge receipt observation is malformed; retained send remains unknown") from exc
    if not isinstance(receipt, dict) or (
        type(receipt.get("version")) is not int or receipt["version"] != 1
        or receipt.get("session_id") != bridge_session
        or receipt.get("native_session_id") != native_session
        or receipt.get("invocation_key") != idempotency_key
        or receipt.get("admission") != "accepted" or receipt.get("queued") is not False
        or type(receipt.get("turn_index")) is not int or receipt["turn_index"] < 0
    ):
        raise TaskError("Bridge receipt does not prove the retained native send")
    return receipt


def native_prompt(
    bridge_session: str, native_session: str, prompt: str, *,
    idempotency_key: str, machine: str | None,
    launch_prefix: Callable[[], list[str] | None],
    run_ssh: Callable[..., subprocess.CompletedProcess],
    timeout: float | None = 20.0,
) -> bool:
    """Bridge owns protocol negotiation, native admission and receipt recovery."""
    return send_command(
        [
            "--json", "send", bridge_session,
            "--expected-native-session-id", native_session,
            "--idempotency-key", idempotency_key,
            "--caller", f"agent-dispatch:{idempotency_key}",
            "--prompt-file", "-", "--no-wait",
        ],
        prompt, machine=machine, launch_prefix=launch_prefix, run_ssh=run_ssh,
        timeout=timeout,
    )
