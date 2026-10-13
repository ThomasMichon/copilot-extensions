"""Bounded captured execution for finite provider CLI commands."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess

from agent_procutil import no_window_flags, spawn_sync_in_kill_on_close_job


def run_captured(
    argv: list[str], timeout: float, *, errors: str = "strict",
) -> subprocess.CompletedProcess[str]:
    """Retain ownership of the command tree until its captured pipes close."""
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "creationflags": no_window_flags(),
    }
    if os.name != "nt":
        kwargs["start_new_session"] = True
    proc, job = spawn_sync_in_kill_on_close_job(
        argv, require_job=True, allow_breakaway=False, **kwargs,
    )
    try:
        out, err = proc.communicate(timeout=timeout)
    except BaseException:
        if job is not None:
            job.close()
            job = None
        elif os.name != "nt":
            with contextlib.suppress(OSError):
                os.killpg(proc.pid, signal.SIGKILL)
        proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.communicate(timeout=5)
        raise
    finally:
        if job is not None:
            job.close()
    return subprocess.CompletedProcess(
        argv,
        proc.returncode,
        (out or b"").decode("utf-8", errors),
        (err or b"").decode("utf-8", errors),
    )
