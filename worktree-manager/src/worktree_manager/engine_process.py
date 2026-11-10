"""Own foreground engine preparation and its synchronous descendants."""

from __future__ import annotations

import os
import subprocess
import time

from agent_procutil import (
    contained_test_mode,
    detached_kwargs,
    spawn_sync_in_kill_on_close_job,
)
from . import engine_posix_process
from .engine_posix_process import EngineCleanupError as EngineCleanupError, cleanup_phase as cleanup_phase

OWNED_GROUP_ENV = "WORKTREE_MANAGER_ENGINE_OWNS_PROCESS_GROUP"
_WINDOWS = os.name == "nt"


def _communicate(process, argv, timeout, cancelled):
    if cancelled is None:
        return process.communicate(timeout=timeout)
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        if cancelled.is_set():
            raise InterruptedError("owned engine read cancelled")
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise subprocess.TimeoutExpired(argv, timeout)
        try:
            return process.communicate(timeout=0.1 if remaining is None else min(remaining, 0.1))
        except subprocess.TimeoutExpired:
            continue


def run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    timeout = kwargs.pop("timeout")
    cancelled = kwargs.pop("cancelled", None)
    confirm_cleanup = kwargs.pop("confirm_cleanup", False)
    kwargs.pop("check", None)
    env = dict(kwargs["env"])
    owns_group = not _WINDOWS and (confirm_cleanup or not contained_test_mode())
    if owns_group:
        env[OWNED_GROUP_ENV] = "1"
    else:
        env.pop(OWNED_GROUP_ENV, None)
    kwargs["env"] = env
    if owns_group:
        return engine_posix_process.run(
            argv, timeout=timeout, **({"cancelled": cancelled} if cancelled is not None else {}),
            **kwargs,
        )
    process, job = spawn_sync_in_kill_on_close_job(
        argv, require_job=_WINDOWS, allow_breakaway=False,
        **kwargs, **(detached_kwargs() if not _WINDOWS else {}),
    )
    completed = False
    try:
        stdout, stderr = _communicate(process, argv, timeout, cancelled)
        completed = True
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    finally:
        try:
            with cleanup_phase():
                if job is not None:
                    if confirm_cleanup:
                        job.terminate_and_wait(5)
                    else:
                        job.close()
                elif process.poll() is None:
                    process.kill()
                if not completed:
                    process.communicate(timeout=5)
        except EngineCleanupError as error:
            error.process = process
            error.job = job
            raise
