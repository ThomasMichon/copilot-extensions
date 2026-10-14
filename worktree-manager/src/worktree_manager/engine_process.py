"""Own foreground engine preparation and its synchronous descendants."""

from __future__ import annotations

import os
import subprocess

from agent_procutil import (
    contained_test_mode,
    detached_kwargs,
    spawn_sync_in_kill_on_close_job,
)
from . import engine_posix_process

OWNED_GROUP_ENV = "WORKTREE_MANAGER_ENGINE_OWNS_PROCESS_GROUP"
_WINDOWS = os.name == "nt"


def run(argv: list[str], **kwargs) -> subprocess.CompletedProcess:
    timeout = kwargs.pop("timeout")
    kwargs.pop("check", None)
    env = dict(kwargs["env"])
    owns_group = not _WINDOWS and not contained_test_mode()
    if owns_group:
        env[OWNED_GROUP_ENV] = "1"
    else:
        env.pop(OWNED_GROUP_ENV, None)
    kwargs["env"] = env
    if owns_group:
        return engine_posix_process.run(argv, timeout=timeout, **kwargs)
    process, job = spawn_sync_in_kill_on_close_job(
        argv, require_job=_WINDOWS, allow_breakaway=False,
        **kwargs, **(detached_kwargs() if not _WINDOWS else {}),
    )
    completed = False
    try:
        stdout, stderr = process.communicate(timeout=timeout)
        completed = True
        return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    finally:
        if job is not None:
            job.close()
        elif process.poll() is None:
            process.kill()
        if not completed:
            process.communicate(timeout=5)
