"""Caller-owned finite registry probes, including captured-pipe teardown."""

from __future__ import annotations

import os
import signal
import subprocess
from time import monotonic

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job

from .procutil import agent_worktrees_environment, agent_worktrees_launch_prefix
from .registrar import RegistrarError


def run_agent_worktrees_capture(
    *args: str, timeout: float,
) -> subprocess.CompletedProcess[str] | None:
    prefix = agent_worktrees_launch_prefix()
    if prefix is None:
        return None
    deadline = monotonic() + timeout
    argv = [*prefix, *args]
    kwargs = no_window_kwargs()
    if os.name != "nt":
        kwargs["start_new_session"] = True
    try:
        proc, job = spawn_sync_in_kill_on_close_job(
            argv, require_job=True, allow_breakaway=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, env=agent_worktrees_environment(), **kwargs,
        )
    except (OSError, RuntimeError) as exc:
        raise RegistrarError("registered repository probe could not acquire process custody") from exc
    retired = False

    def retire_tree() -> None:
        nonlocal job, retired
        if retired:
            return
        retired = True
        if job is not None:
            held_job, job = job, None
            held_job.close()
        elif os.name != "nt":
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    try:
        stdout, stderr = proc.communicate(timeout=max(0, deadline - monotonic()))
        return subprocess.CompletedProcess(argv, proc.returncode, stdout, stderr)
    except subprocess.TimeoutExpired:
        retire_tree()
        try:
            proc.communicate(timeout=1)
        except subprocess.TimeoutExpired as exc:
            raise RegistrarError("registered repository probe teardown remains unconfirmed") from exc
        return None
    finally:
        retire_tree()
