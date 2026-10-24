"""Caller-owned foreground completion, separate from UI startup."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from contextlib import contextmanager

from agent_procutil import spawn_sync_in_kill_on_close_job
from agent_procutil.owned_process import OwnedCleanupError


@contextmanager
def _ignore_interrupt():
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        yield
    finally:
        signal.signal(signal.SIGINT, previous)


def run_interactive(argv: list[str], *, env: dict[str, str] | None = None) -> int:
    """Run after UI exit and settle owned descendants before returning status.

    POSIX completion uses a private PTY/session, relaying the caller terminal
    while retaining redirected non-TTY streams. Managed detachment into another
    session is outside this finite scope. Windows retains a required Job.
    Cleanup failures retain custody in ``OwnedCleanupError``. Call from the
    main thread. POSIX signal exits use 128 + signal; control-C returns 130
    only after cleanup, including Windows' native control-C status.
    """
    if (
        not isinstance(argv, list) or not argv
        or any(not isinstance(arg, str) or "\0" in arg for arg in argv)
    ):
        raise ValueError("interactive argv must be a nonempty list of strings without NUL")
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("interactive completion requires the main thread")
    try:
        if os.name != "nt":
            from ._interactive_terminal import run

            return run(argv, env)
        # headless-guard: allow real user-interactive stdio in the existing console
        process, job = spawn_sync_in_kill_on_close_job(argv, env=env, require_job=True)
        try:
            while True:
                try:
                    code = process.wait(timeout=.1)
                    return 130 if code in (0xC000013A, -1073741510) else code
                except subprocess.TimeoutExpired:
                    pass
        finally:
            with _ignore_interrupt():
                try:
                    job.terminate_and_wait(2)
                    process.wait(timeout=2)
                except BaseException as error:
                    raise OwnedCleanupError(process, job) from error
    except KeyboardInterrupt:
        return 130
