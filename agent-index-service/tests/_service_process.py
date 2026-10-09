"""Direct worker identity plus containment for temporary hosted-service tests."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from contextlib import contextmanager

from agent_procutil import (
    no_window_kwargs,
    spawn_sync_in_kill_on_close_job,
    windowless_python,
    windowless_python_env,
)


@contextmanager
def owned_python(arguments, *, cwd, stdout, stderr=None, env=None):
    child_env = dict(os.environ) if env is None else dict(env)
    child_env.update(windowless_python_env(sys.executable))
    kwargs = no_window_kwargs()
    if os.name != "nt":
        kwargs["start_new_session"] = True
    process, job = spawn_sync_in_kill_on_close_job(
        [windowless_python(sys.executable), *arguments],
        cwd=cwd, env=child_env, stdin=subprocess.DEVNULL,
        stdout=stdout, stderr=stdout if stderr is None else stderr, **kwargs,
    )
    try:
        if os.name == "nt" and job is None:
            raise RuntimeError("test child could not acquire owned kill-on-close containment")
        yield process
    finally:
        if job is not None:
            job.close()
        elif os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        elif process.poll() is None:
            process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=15)
