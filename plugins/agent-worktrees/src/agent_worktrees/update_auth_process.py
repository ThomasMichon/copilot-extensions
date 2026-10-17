"""Bounded, read-only authentication subprocesses and credential-helper children."""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_procutil import contained_test_mode, no_window_kwargs, spawn_sync_in_kill_on_close_job

from . import installer_run, push_timeout

_GROUP = "CE_UPDATE_AUTH_PROCESS_GROUP"
_OUTPUT_LIMIT = 1024 * 1024


@dataclass(frozen=True)
class Result:
    status: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    missing: bool = False
    truncated: bool = False


def run(
    argv: Sequence[str],
    *,
    env: Mapping[str, str],
    cwd: Path | None = None,
    timeout: float = 5,
    input: str = "",
) -> Result:
    """Capture at most 1 MiB per stream, draining excess bytes without retaining them.

    Nested helpers retain the outer POSIX session, so its deadline owns them too.
    Windows always requires a no-breakaway Job before the child can execute.
    """
    child_env = dict(env)
    inherited = os.name != "nt" and child_env.get(_GROUP) == str(os.getpgrp())
    own_session = os.name != "nt" and not inherited and not contained_test_mode()
    if os.name != "nt" and not inherited:
        # The child's pid is not known until creation; it publishes its own group
        # to nested runs through the helper entry point instead.
        child_env.pop(_GROUP, None)
    if own_session:
        child_env["CE_UPDATE_AUTH_OWNED_SESSION"] = "1"
    try:
        proc, job = spawn_sync_in_kill_on_close_job(
            list(argv),
            cwd=cwd,
            env=child_env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            require_job=os.name == "nt",
            allow_breakaway=False,
            **(
                no_window_kwargs()
                if os.name == "nt"
                else {
                    "start_new_session": own_session,
                }
            ),
        )
    except FileNotFoundError:
        return Result(None, missing=True)
    except (OSError, RuntimeError) as error:
        return Result(None, stderr=str(error))

    buffers = [bytearray(), bytearray()]
    truncated = [False, False]
    failures: list[OSError] = []

    def drain(stream, index: int) -> None:
        try:
            while chunk := stream.read(65536):
                remaining = max(0, _OUTPUT_LIMIT - len(buffers[index]))
                buffers[index].extend(chunk[:remaining])
                truncated[index] |= len(chunk) > remaining
        except OSError as error:
            failures.append(error)
        finally:
            stream.close()

    def feed() -> None:
        try:
            proc.stdin.write(input.encode("utf-8"))
            proc.stdin.close()
        except BrokenPipeError:
            proc.stdin.close()
        except OSError as error:
            failures.append(error)

    threads = [
        threading.Thread(target=drain, args=(proc.stdout, 0), daemon=True),
        threading.Thread(target=drain, args=(proc.stderr, 1), daemon=True),
        threading.Thread(target=feed, daemon=True),
    ]
    expires = time.monotonic() + timeout
    previous: dict[int, object] = {}

    def kill() -> None:
        if job is not None:
            job.close()
        elif own_session:
            installer_run._sweep_exited_session(proc) if proc.poll() is not None else (
                installer_run._kill_session(proc)
            )
        else:
            push_timeout._kill_tree(proc)

    def interrupted(signum, _frame) -> None:
        kill()
        raise SystemExit(128 + signum)

    try:
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGTERM, signal.SIGINT):
                previous[sig] = signal.signal(sig, interrupted)
        for thread in threads:
            thread.start()
        expired = False
        try:
            proc.wait(timeout=max(0, expires - time.monotonic()))
            for thread in threads:
                thread.join(max(0, expires - time.monotonic()))
            expired = any(thread.is_alive() for thread in threads)
        except subprocess.TimeoutExpired:
            expired = True
        if expired:
            kill()
            proc.wait(timeout=5)
            for thread in threads:
                thread.join(1)
        text = [bytes(value).decode("utf-8", "replace") for value in buffers]
        if failures:
            text[1] = "authentication subprocess pipe failed"
        return Result(
            None if failures else proc.returncode,
            *text,
            timed_out=expired,
            truncated=any(truncated),
        )
    except BaseException:
        kill()
        raise
    finally:
        if job is not None:
            job.close()
        elif own_session and proc.poll() is not None:
            installer_run._sweep_exited_session(proc)
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def publish_group() -> None:
    if os.name != "nt" and os.environ.get("CE_UPDATE_AUTH_OWNED_SESSION") == "1":
        os.environ[_GROUP] = str(os.getpgrp())
