"""Installed-interpreter fixture; all process and state effects belong to its test."""

from __future__ import annotations

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import threading
import time


def windows_kernel():
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
        ctypes.POINTER(wintypes.FILETIME),
    ] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    return kernel


def handle_birth(kernel, handle) -> int:
    import ctypes
    from ctypes import wintypes

    times = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


def identity(pid: int | None = None) -> dict:
    pid = os.getpid() if pid is None else pid
    if os.name == "nt":
        import ctypes

        kernel = windows_kernel()
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            birth = handle_birth(kernel, handle)
        finally:
            kernel.CloseHandle(handle)
    else:
        fields = Path(f"/proc/{pid}/stat").read_text().rpartition(")")[2].split()
        birth = int(fields[19])
    return {"pid": pid, "birth": birth}


def _write(root: Path, name: str, value: object) -> None:
    (root / f"{name}.json").write_text(json.dumps(value), encoding="utf-8")


def _gate(root: Path) -> None:
    deadline = time.monotonic() + 15
    while not (root / "gate").exists():
        if time.monotonic() >= deadline:
            raise TimeoutError("test gate was not released")
        time.sleep(0.01)


def pidfd_exited(fd: int) -> bool:
    observer = select.poll()
    observer.register(fd, select.POLLIN)
    events = observer.poll(0)
    if any(mask & (select.POLLERR | select.POLLNVAL) for _fd, mask in events):
        raise RuntimeError("test pidfd exit observation failed")
    return any(mask & select.POLLIN for _fd, mask in events)


def open_pidfd(pid: int) -> int:
    from agent_procutil._linux_pidfd import open_pidfd as acquire

    return acquire(pid)


def signal_pidfd(fd: int, signum: int) -> None:
    native = getattr(signal, "pidfd_send_signal", None)
    if native is not None:
        native(fd, signum)
        return
    import ctypes

    send = ctypes.CDLL(None, use_errno=True).pidfd_send_signal
    send.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    send.restype = ctypes.c_int
    if send(fd, signum, None, 0) != 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def _exited(expected: dict) -> bool:
    pid = expected["pid"]
    if os.name == "nt":
        import ctypes

        kernel = windows_kernel()
        handle = kernel.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return True
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            if handle_birth(kernel, handle) != expected["birth"]:
                return True
            state = kernel.WaitForSingleObject(handle, 0)
            if state not in (0, 0x102):
                raise ctypes.WinError(ctypes.get_last_error())
            return state == 0
        finally:
            kernel.CloseHandle(handle)
    try:
        fd = open_pidfd(pid)
    except ProcessLookupError:
        return True
    try:
        try:
            if identity(pid)["birth"] != expected["birth"]:
                return True
        except FileNotFoundError:
            return True
        return pidfd_exited(fd)
    finally:
        os.close(fd)


def main() -> None:
    role, directory, mode = sys.argv[1:]
    root = Path(directory)
    if role == "leaf":
        _write(root, "leaf", {"self": identity(), "parent": identity(os.getppid())})
        time.sleep(60)
        return
    import agent_procutil

    if role == "target":
        subprocess.Popen(
            [sys.executable, "-I", __file__, "leaf", directory, mode],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **agent_procutil.no_window_kwargs(),
        )
        observation = {"self": identity(), "parent": identity(os.getppid())}
        if os.name != "nt":
            observation["group"] = os.getpgrp()
            observation["session"] = os.getsid(0)
        _write(root, "target", observation)
        _gate(root)
        if mode == "completed":
            sys.stdout.buffer.write(b"target-ok")
            return
        if mode in ("stdout", "stderr"):
            stream = getattr(sys, mode).buffer
            stream.write(b"private-fixture-value" * 4)
            stream.flush()
        time.sleep(60)
        return
    if role != "caller":
        raise ValueError("unknown fixture role")
    from agent_procutil.owned_process import OutputLimitExceeded, run_caller_owned

    _write(root, "caller", {
        "self": identity(), "parent": identity(os.getppid()),
        "package": agent_procutil.__file__, "prefix": sys.prefix,
    })
    if mode == "suspended":
        from agent_procutil import _windows_caller_owned

        original = _windows_caller_owned._Process.__init__

        def suspend(process, argv, info, streams):
            original(process, argv, info, streams)
            _write(root, "suspended", identity(info.dwProcessId))
            time.sleep(60)

        _windows_caller_owned._Process.__init__ = suspend
    if mode == "fork-keeper":
        import warnings

        warnings.filterwarnings("ignore", category=DeprecationWarning)

        def fork_keeper():
            while not (root / "leaf.json").exists():
                time.sleep(0.01)
            if os.fork() == 0:
                for fd in (0, 1, 2):
                    os.close(fd)
                _write(root, "unrelated", identity())
                time.sleep(60)
                os._exit(0)

        threading.Thread(target=fork_keeper, daemon=True).start()
    if mode == "interrupt" and os.name == "nt":
        def interrupt() -> None:
            _gate(root)
            signal.raise_signal(signal.SIGINT)

        threading.Thread(target=interrupt, daemon=True).start()
    try:
        result = run_caller_owned(
            [sys.executable, "-I", __file__, "target", directory, mode],
            timeout=5, max_stdout_bytes=32, max_stderr_bytes=32,
            input_text="x" * 524288 if mode == "blocked-input" else None,
        )
        outcome = {"kind": "completed", "code": result.returncode, "stdout": result.stdout}
    except OutputLimitExceeded as error:
        outcome = {"kind": "output-limit", "stream": error.stream, "limit": error.limit}
    except subprocess.TimeoutExpired:
        outcome = {"kind": "timeout"}
    except KeyboardInterrupt:
        outcome = {"kind": "interrupt"}
    observed = []
    for name in ("target", "leaf"):
        record = json.loads((root / f"{name}.json").read_text(encoding="utf-8"))
        observed.extend((record["self"], record["parent"]))
    outcome["unsettled"] = [
        expected["pid"] for expected in observed
        if expected["pid"] != os.getpid() and not _exited(expected)
    ]
    outcome["settled_before_result"] = not outcome["unsettled"]
    _write(root, "result", outcome)
    print(json.dumps(outcome), flush=True)


if __name__ == "__main__":
    main()
