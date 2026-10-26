"""Bounded installer children, with optional retained construction custody."""

from __future__ import annotations

import logging
import importlib.util
import json
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
    procutil: ModuleType | None, construction_fd: int | None,
    runtime_root: Path | None = None, runtime_version: str | None = None,
    runtime_path: Path | None = None,
    deadline: float | None = None,
) -> subprocess.CompletedProcess:
    """Settle owned writers before returning or propagating a timeout.

    POSIX callers may omit procutil. With no construction descriptor, storage
    custody remains caller-held only; abrupt caller-death retention is not
    established by this mode.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("installer child timeout must be finite and positive")
    if os.name == "nt" and procutil is None:
        raise ValueError("Windows installer children require native process utilities")
    if deadline is not None:
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
            raise ValueError("installer child deadline must be finite")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ChildTimeoutExpired(argv, 0, output="", stderr="")
        timeout = min(timeout, remaining)
    if any(value is not None for value in (runtime_root, runtime_version, runtime_path)):
        if not all(value is not None for value in (runtime_root, runtime_version, runtime_path)):
            raise ValueError("installer retained child requires complete runtime custody")
        if os.name == "nt":
            return _retained_windows(
                argv, cwd=cwd, env=env, timeout=timeout, procutil=procutil,
                root=runtime_root, version=runtime_version, runtime_path=runtime_path,
                **({"deadline": deadline} if deadline is not None else {}),
            )
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
            stdout, stderr = process.communicate(
                timeout=timeout if deadline is None else max(0, min(timeout, deadline - time.monotonic())),
            )
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
                    pass_fds=(owner_read,) if construction_fd is None else (owner_read, construction_fd),
                    start_new_session=True, **options,
                )
            except OSError as error:
                raise ChildLaunchError(f"Installer child could not start: {error}") from error
            os.close(owner_read)
            owner_read = -1
            try:
                stdout, stderr = process.communicate(
                    timeout=timeout if deadline is None else max(0, min(timeout, deadline - time.monotonic())),
                )
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
    if timeout_error is None and deadline is not None and time.monotonic() >= deadline:
        timeout_error = subprocess.TimeoutExpired(argv, timeout, output=stdout, stderr=stderr)
    if timeout_error is not None:
        # Read both captured streams only after every owned writer has settled.
        stdout, stderr = process.communicate(timeout=10)
        raise ChildTimeoutExpired(
            timeout_error.cmd, timeout_error.timeout, output=stdout, stderr=stderr,
        ) from timeout_error
    result = subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
    result.check_returncode()
    return result


def _retained_windows(argv, *, cwd, env, timeout, procutil, root, version, runtime_path, deadline=None):
    import msvcrt

    reader, writer = os.pipe()
    process = None
    try:
        handle = msvcrt.get_osfhandle(reader)
        startup = subprocess.STARTUPINFO()
        startup.lpAttributeList = {"handle_list": [handle]}
        os.set_handle_inheritable(handle, True)
        try:
            try:
                process = subprocess.Popen(
                    [sys.executable, "-I", "-B", str(Path(__file__).resolve()),
                     "--owner-handle", str(handle), "--runtime-root", str(root),
                     "--runtime-version", version, "--runtime-path", str(runtime_path),
                     "--procutil-path", str(Path(procutil.__file__).resolve()),
                     "--timeout", str(timeout),
                     *(["--deadline", str(deadline)] if deadline is not None else []), "--", *argv],
                    cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                    startupinfo=startup, close_fds=True, executable=sys.executable,
                    **procutil.no_window_kwargs(),
                )
            except OSError as error:
                raise ChildLaunchError(f"Installer guardian could not start: {error}") from error
        finally:
            os.set_handle_inheritable(handle, False)
        os.close(reader)
        reader = -1
        try:
            stdout, stderr = process.communicate(timeout=timeout + 30)
        finally:
            os.close(writer)
            writer = -1
            # The guardian releases retention only after its Job has no writers.
            _settle(lambda: process.communicate(timeout=30))
        if process.returncode != 0:
            raise RuntimeError("installer retention guardian did not confirm settlement")
        try:
            outcome = json.loads(stdout)
        except ValueError:
            raise RuntimeError("installer retention guardian returned invalid completion") from None
        if (
            not isinstance(outcome, dict)
            or set(outcome) != {"returncode", "stdout", "stderr", "timed_out", "launch_error"}
            or type(outcome["returncode"]) is not int
            or type(outcome["timed_out"]) is not bool
            or not isinstance(outcome["stdout"], str) or not isinstance(outcome["stderr"], str)
            or (outcome["launch_error"] is not None
                and not isinstance(outcome["launch_error"], str))
        ):
            raise RuntimeError("installer retention guardian returned invalid completion")
        if outcome["launch_error"] is not None:
            raise ChildLaunchError(f"Installer child could not start: {outcome['launch_error']}")
        if outcome["timed_out"]:
            raise ChildTimeoutExpired(
                argv, timeout, output=outcome["stdout"], stderr=outcome["stderr"],
            )
        result = subprocess.CompletedProcess(
            argv, outcome["returncode"], outcome["stdout"], outcome["stderr"],
        )
        result.check_returncode()
        return result
    finally:
        if reader >= 0:
            os.close(reader)
        if writer >= 0:
            os.close(writer)


def _load_resource(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError("installer guardian resource could not be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _supervise_windows(args, argv):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.PeekNamedPipe.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p,
    )
    kernel.PeekNamedPipe.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)

    def disconnected():
        if kernel.PeekNamedPipe(args.owner_handle, None, 0, None, None, None):
            return False
        error = ctypes.get_last_error()
        if error in (109, 233):
            return True
        raise ctypes.WinError(error)

    absolute_deadline = getattr(args, "deadline", None)
    expired = {"returncode": 1, "stdout": "", "stderr": "", "timed_out": True, "launch_error": None}
    try:
        runtime = _load_resource("installer_guardian_runtime", args.runtime_path)
        procutil = _load_resource("installer_guardian_procutil", args.procutil_path)
        if absolute_deadline is not None and time.monotonic() >= absolute_deadline:
            return expired
        with runtime.retention_lease(Path(args.runtime_root), args.runtime_version):
            if disconnected():
                raise RuntimeError("installer caller exited before child execution")
            if absolute_deadline is not None and time.monotonic() >= absolute_deadline:
                return expired
            try:
                process, job = procutil.spawn_sync_in_kill_on_close_job(
                    argv, require_job=True, allow_breakaway=False, executable=argv[0],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, encoding="utf-8", errors="replace", **procutil.no_window_kwargs(),
                )
            except procutil.SyncProcessLaunchError as error:
                return {
                    "returncode": 1, "stdout": "", "stderr": "",
                    "timed_out": False, "launch_error": str(error),
                }
            deadline = time.monotonic() + args.timeout
            if absolute_deadline is not None:
                deadline = min(deadline, absolute_deadline)
            timed_out = False
            try:
                while True:
                    if disconnected():
                        raise RuntimeError("installer caller exited during child execution")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        timed_out = True
                        break
                    try:
                        stdout, stderr = process.communicate(timeout=min(remaining, 0.1))
                        break
                    except subprocess.TimeoutExpired:
                        continue
            finally:
                try:
                    _settle(lambda: job.terminate_and_wait(10))
                finally:
                    _settle(lambda: process.wait(timeout=10))
            stdout, stderr = process.communicate(timeout=10)
            return {
                "returncode": process.returncode, "stdout": stdout,
                "stderr": stderr, "timed_out": timed_out, "launch_error": None,
            }
    finally:
        kernel.CloseHandle(args.owner_handle)


def _terminate_group(process: subprocess.Popen) -> None:
    if process.returncode is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        # Darwin reports EPERM for a group whose only members are zombies; the
        # ps check below still refuses to settle while a live member remains.
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
    ownership = parser.add_mutually_exclusive_group(required=True)
    ownership.add_argument("--owner-fd", type=int)
    ownership.add_argument("--owner-handle", type=int)
    parser.add_argument("--runtime-root")
    parser.add_argument("--runtime-version")
    parser.add_argument("--runtime-path")
    parser.add_argument("--procutil-path")
    parser.add_argument("--timeout", type=float)
    parser.add_argument("--deadline", type=float)
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.command or args.command[0] != "--" or len(args.command) < 2:
        parser.error("a child argv after -- is required")
    if args.deadline is not None and (not math.isfinite(args.deadline) or args.deadline <= 0):
        parser.error("a finite positive monotonic deadline is required")
    if args.owner_handle is not None:
        if os.name != "nt" or not all((
            args.runtime_root, args.runtime_version, args.runtime_path, args.procutil_path,
        )) or args.timeout is None or not math.isfinite(args.timeout) or args.timeout <= 0:
            parser.error("Windows guardian requires complete runtime custody and a finite timeout")
        print(json.dumps(_supervise_windows(args, args.command[1:])), flush=True)
        raise SystemExit(0)
    raise SystemExit(_supervise(args.owner_fd, args.command[1:]))
