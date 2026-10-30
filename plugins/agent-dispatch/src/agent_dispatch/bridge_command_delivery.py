"""Shared standard-command transport for existing-owner Bridge prompts."""

from __future__ import annotations

import logging
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


def send_command(
    argv: list[str],
    prompt: str,
    *,
    machine: str | None,
    launch_prefix: Callable[[], list[str] | None],
    run_ssh: Callable[..., subprocess.CompletedProcess],
    timeout: float | None,
) -> bool:
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
            return False
        # marketplace-isolation: allow remote-management
        command = " ".join(shlex.quote(arg) for arg in ["agent-bridge", *argv])
        cmd = [ssh, "-o", "BatchMode=yes", "-o", "ConnectTimeout=3", remote, command]
    else:
        exe = launch_prefix()
        if exe is None:
            log.warning("Bridge command delivery requires an available local runtime")
            return False
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
        return False
    if result.returncode:
        log.warning(
            "Bridge command delivery returned exit status %s: %s",
            result.returncode, (result.stderr or "").strip()[:2048],
        )
    return result.returncode == 0


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
