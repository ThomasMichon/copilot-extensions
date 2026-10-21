"""Bounded installer children, with ownership surviving abrupt caller death."""

from __future__ import annotations

import logging
import math
import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import ModuleType


class ChildLaunchError(RuntimeError):
    """An owned child could not start; no child cleanup was attempted."""


class ChildTimeoutExpired(subprocess.TimeoutExpired):
    """The child's deadline expired and every owned writer has settled."""


def _settle(operation: Callable[[], object]) -> None:
    failure: BaseException | None = None
    while True:
        try:
            if failure is not None:
                time.sleep(1)
            operation()
            break
        except (OSError, RuntimeError, subprocess.TimeoutExpired, KeyboardInterrupt, SystemExit) as error:
            if failure is None:
                failure = error
            logging.getLogger(__name__).error(
                "Installer teardown is unsettled; retaining construction custody: %s", error,
            )
    if failure is not None:
        raise failure


def run_owned(
    argv: list[str], *, cwd: Path, env: dict[str, str], timeout: float,
    procutil: ModuleType, construction_fd: int,
) -> subprocess.CompletedProcess:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("installer child timeout must be finite and positive")
    options = dict(cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                   encoding="utf-8", errors="replace")
    timeout_error = None
    if os.name == "nt":
        # An explicit application path avoids MAX_PATH command-token parsing.
        try:
            process, job = procutil.spawn_sync_in_kill_on_close_job(
                argv, require_job=True, allow_breakaway=False,
                executable=argv[0],
                **procutil.no_window_kwargs(), **options,
            )
        except procutil.SyncProcessLaunchError as error:
            raise ChildLaunchError(f"Installer child could not start: {error}") from error
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            timeout_error = error
        finally:
            try:
                _settle(lambda: job.terminate_and_wait(10))
            finally:
                _settle(lambda: process.wait(timeout=10))
    else:
        owner_read, owner_write = os.pipe()
        try:
            # The guardian retains the custody descriptor until all children
            # exit, including after SIGKILL closes the caller's liveness pipe.
            try:
                process = subprocess.Popen(
                    [sys.executable, "-I", str(Path(__file__).resolve()),
                     "--owner-fd", str(owner_read), "--", *argv],
                    pass_fds=(owner_read, construction_fd), start_new_session=True, **options,
                )
            except OSError as error:
                raise ChildLaunchError(f"Installer child could not start: {error}") from error
            os.close(owner_read)
            owner_read = -1
            try:
                stdout, stderr = process.communicate(timeout=timeout)
            except subprocess.TimeoutExpired as error:
                timeout_error = error
            finally:
                os.close(owner_write)
                owner_write = -1
                _settle(lambda: process.communicate(timeout=30))
        finally:
            if owner_read >= 0:
                os.close(owner_read)
            if owner_write >= 0:
                os.close(owner_write)
    if timeout_error is not None:
        # Read both captured streams only after every owned writer has settled.
        stdout, stderr = process.communicate(timeout=10)
        raise ChildTimeoutExpired(
            timeout_error.cmd, timeout_error.timeout, output=stdout, stderr=stderr,
        ) from timeout_error
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    result.check_returncode()
    return result


def _terminate_group(process: subprocess.Popen) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 10
    while True:
        observation = subprocess.run(
            ["ps", "-eo", "pid=,pgid=,stat="],
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            check=True, timeout=5,
        )
        rows = [line.split() for line in observation.stdout.splitlines()]
        if any(len(row) != 3 for row in rows):
            raise RuntimeError("Cannot verify installer process-group teardown")
        try:
            live = any(int(row[1]) == process.pid and not row[2].startswith("Z") for row in rows)
        except ValueError as error:
            raise RuntimeError("Cannot parse installer process-group teardown evidence") from error
        if not live:
            process.wait(timeout=10)
            return
        if time.monotonic() >= deadline:
            raise TimeoutError("Installer process group still has live writers after termination")
        time.sleep(0.05)


def _child_exited_unreaped(pid: int) -> bool:
    waitid = getattr(os, "waitid", None)
    if waitid is not None:
        return waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None
    # Python 3.11/3.12 on macOS lack waitid. ps observes zombie state without
    # reaping our direct child, keeping its PID/PGID reserved through cleanup.
    observation = subprocess.run(
        ["ps", "-o", "stat=", "-p", str(pid)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True, timeout=5,
    )
    states = observation.stdout.split()
    if len(states) != 1:
        raise RuntimeError("Cannot observe unreaped installer child state")
    return states[0].startswith("Z")


def _supervise(owner_fd: int, argv: list[str]) -> int:
    process = None
    try:
        if select.select([owner_fd], [], [], 0)[0] and not os.read(owner_fd, 1):
            raise RuntimeError("Installer owner exited before child construction")
        process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, start_new_session=True)
        # Observe exit without reaping the group leader. Its unreaped identity
        # prevents PID/PGID reuse before owned descendants are terminated.
        while True:
            if _child_exited_unreaped(process.pid):
                break
            if select.select([owner_fd], [], [], 0.05)[0] and not os.read(owner_fd, 1):
                raise RuntimeError("Installer owner exited during child construction")
    finally:
        if process is not None:
            _settle(lambda: _terminate_group(process))
        os.close(owner_fd)
    if process is None or process.returncode is None:
        raise RuntimeError("Installer child cleanup returned without an exit status")
    return process.returncode if process.returncode >= 0 else 128 - process.returncode


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-fd", type=int, required=True)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command or args.command[0] != "--" or len(args.command) < 2:
        parser.error("a child argv after -- is required")
    raise SystemExit(_supervise(args.owner_fd, args.command[1:]))
