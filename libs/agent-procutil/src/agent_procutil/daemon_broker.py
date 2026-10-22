"""Bounded Windows lifetime broker; also the isolated, stdlib-only bootstrap."""

from __future__ import annotations

import ctypes
import json
import math
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_BOOT_PORT = "_CE_DAEMON_BROKER_PORT"
_BOOT_COOKIE = "_CE_DAEMON_BROKER_COOKIE"
_TIMEOUT = 30.0
_MAX_FRAME = 16 * 1024 * 1024
_SYNCHRONIZE = 0x00100000
_PROCESS_ACCESS = _SYNCHRONIZE | 0x0001 | 0x0800 | 0x1000
_WAIT_TIMEOUT = 0x102
_CREATE_SUSPENDED = 0x00000004

_WMI_CREATE = r"""
$ErrorActionPreference = 'Stop'
try {
    $s = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
        ShowWindow = [uint16]0
        CreateFlags = [uint32]$s.creationflags
        EnvironmentVariables = [string[]]$s.environment
    }
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = [string]$s.command
        CurrentDirectory = [string]$s.cwd
        ProcessStartupInformation = $startup
    }
    if ($r.ReturnValue -ne 0) { exit 1 }
    [Console]::Out.WriteLine([int]$r.ProcessId)
} catch {
    [Console]::Error.WriteLine('daemon broker activation failed')
    exit 1
}
"""


def _kernel32() -> Any:
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.WaitForSingleObject.restype = wintypes.DWORD
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.GetExitCodeProcess.restype = wintypes.BOOL
    kernel.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel.TerminateProcess.restype = wintypes.BOOL
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    return kernel


class WindowsDaemonProcess:
    """Popen's daemon-control surface backed by a pinned actual-child handle."""

    stdin = stdout = stderr = None

    def __init__(self, pid: int, args: list[str]):
        self.pid, self.args = pid, args
        self.returncode: int | None = None
        self._handle = _kernel32().OpenProcess(_PROCESS_ACCESS, False, pid)
        if not self._handle:
            raise ctypes.WinError(ctypes.get_last_error())

    def poll(self) -> int | None:
        from ctypes import wintypes

        if self.returncode is not None:
            return self.returncode
        kernel = _kernel32()
        result = kernel.WaitForSingleObject(self._handle, 0)
        if result == _WAIT_TIMEOUT:
            return None
        if result != 0:
            raise ctypes.WinError(ctypes.get_last_error())
        code = wintypes.DWORD()
        if not kernel.GetExitCodeProcess(self._handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        self.returncode = code.value
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        if timeout is not None and (not math.isfinite(timeout) or timeout < 0):
            raise ValueError("timeout must be finite and nonnegative")
        milliseconds = 0xFFFFFFFF if timeout is None else min(math.ceil(timeout * 1000), 0xFFFFFFFE)
        result = _kernel32().WaitForSingleObject(self._handle, milliseconds)
        if result == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.args, timeout)
        code = self.poll()
        if code is None:
            raise OSError("daemon wait did not observe process exit")
        return code

    def terminate(self) -> None:
        if self.poll() is None and not _kernel32().TerminateProcess(self._handle, 1):
            if self.poll() is None:
                raise ctypes.WinError(ctypes.get_last_error())

    kill = terminate

    def close(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            if not _kernel32().CloseHandle(handle):
                raise ctypes.WinError(ctypes.get_last_error())
            self._handle = None

    def __del__(self) -> None:
        handle = getattr(self, "_handle", None)
        if handle:
            _kernel32().CloseHandle(handle)
            self._handle = None


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("daemon broker deadline expired")
    return remaining


def _send(connection: socket.socket, message: dict, deadline: float) -> None:
    payload = json.dumps(message, ensure_ascii=True).encode("ascii")
    if len(payload) > _MAX_FRAME:
        raise ValueError("daemon broker request exceeds frame limit")
    connection.settimeout(_remaining(deadline))
    connection.sendall(len(payload).to_bytes(4, "big") + payload)


def _read_exact(connection: socket.socket, size: int, deadline: float) -> bytes:
    payload = bytearray()
    while len(payload) < size:
        connection.settimeout(_remaining(deadline))
        part = connection.recv(min(size - len(payload), 65536))
        if not part:
            raise OSError("daemon broker channel closed")
        payload.extend(part)
    return bytes(payload)


def _receive(connection: socket.socket, deadline: float) -> dict:
    size = int.from_bytes(_read_exact(connection, 4, deadline), "big")
    if not 0 < size <= _MAX_FRAME:
        raise ValueError("daemon broker response exceeds frame limit")
    result = json.loads(_read_exact(connection, size, deadline))
    if not isinstance(result, dict):
        raise ValueError("invalid daemon broker response")
    return result


def _outside_job(process: WindowsDaemonProcess) -> bool:
    from ctypes import wintypes

    in_job = wintypes.BOOL()
    if not _kernel32().IsProcessInJob(process._handle, None, ctypes.byref(in_job)):
        raise ctypes.WinError(ctypes.get_last_error())
    return not in_job.value


def caller_in_job() -> bool:
    """A successful breakaway flag alone cannot prove escape from nested Jobs."""
    process = WindowsDaemonProcess(os.getpid(), ["current-daemon-caller"])
    try:
        return not _outside_job(process)
    finally:
        process.close()


def _resume(process: WindowsDaemonProcess) -> None:
    from ctypes import wintypes

    ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
    ntdll.NtResumeProcess.argtypes = [wintypes.HANDLE]
    ntdll.NtResumeProcess.restype = ctypes.c_long
    if ntdll.NtResumeProcess(process._handle) != 0:
        raise OSError("daemon broker could not resume its child")


def _activate(command: list[str], env: dict[str, str], cwd: str, deadline: float) -> int:
    from . import detached_kwargs, no_window_kwargs

    powershell = Path(os.environ["SystemRoot"]) / (
        r"System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    payload = {
        "command": subprocess.list2cmdline(command),
        "cwd": cwd,
        "environment": [f"{key}={value}" for key, value in env.items()],
        "creationflags": detached_kwargs(breakaway=False).get("creationflags", 0),
    }
    result = subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", _WMI_CREATE],
        input=json.dumps(payload, ensure_ascii=True),
        encoding="utf-8", capture_output=True, timeout=_remaining(deadline),
        check=False, **no_window_kwargs(),
    )
    if result.returncode != 0:
        raise OSError("daemon broker activation failed")
    try:
        pid = int(result.stdout.strip())
    except ValueError:
        raise OSError("daemon broker returned an invalid process identity") from None
    if pid <= 0:
        raise OSError("daemon broker returned an invalid process identity")
    return pid


def _bootstrap_python() -> str:
    # Use the actual base interpreter, never a venv redirector. WMI supports
    # DETACHED_PROCESS for this non-captured bootstrap (not CREATE_NO_WINDOW).
    python = Path(sys.base_prefix) / "python.exe"
    if not python.is_file():
        raise OSError("daemon broker requires its base Python interpreter")
    return str(python)


def _cleanup_process(
    process: WindowsDaemonProcess, *, terminate: bool,
) -> tuple[bool, list[str]]:
    problems = []
    if terminate:
        try:
            process.terminate()
        except (OSError, subprocess.SubprocessError):
            problems.append("terminate")
    exited = False
    try:
        process.wait(timeout=5)
        exited = True
    except (OSError, subprocess.SubprocessError):
        problems.append("wait")
    try:
        process.close()
    except OSError:
        problems.append("close")
    return exited, problems


def spawn_brokered_daemon(
    command: list[str], *, cwd: str, env: dict[str, str], creationflags: int,
    log_path: str, stderr_log_path: str | None,
) -> WindowsDaemonProcess:
    """Launch through WMI only after the direct Job-breakaway request failed."""
    from . import DaemonSpawnError

    deadline = time.monotonic() + _TIMEOUT
    child = bootstrap = None
    staging_started = False
    cookie = secrets.token_hex(32)
    stage = "activation"
    problems = []
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            bootstrap_env = {
                **os.environ,
                _BOOT_COOKIE: cookie,
                _BOOT_PORT: str(listener.getsockname()[1]),
            }
            argv = [_bootstrap_python(), "-I", str(Path(__file__).resolve())]
            pid = _activate(argv, bootstrap_env, cwd, deadline)
            bootstrap = WindowsDaemonProcess(pid, argv)
            stage = "bootstrap-identity"
            listener.settimeout(_remaining(deadline))
            with listener.accept()[0] as connection:
                hello = _receive(connection, deadline)
                received_cookie = hello.get("cookie")
                if (
                    not isinstance(received_cookie, str)
                    or not secrets.compare_digest(received_cookie.encode("utf-8"), cookie.encode())
                    or hello.get("pid") != pid
                ):
                    raise OSError("daemon broker identity mismatch")
                if not _outside_job(bootstrap):
                    raise OSError("daemon broker bootstrap still belongs to a Job")
                stage = "child-staging"
                staging_started = True
                _send(connection, {
                    "argv": command, "cwd": cwd, "env": env,
                    "creationflags": creationflags, "log_path": log_path,
                    "stderr_log_path": stderr_log_path, "deadline": deadline,
                }, deadline)
                response = _receive(connection, deadline)
                child_pid = response.get("pid")
                if type(child_pid) is not int or child_pid <= 0:
                    raise OSError("daemon broker did not stage a child")
                child = WindowsDaemonProcess(child_pid, command)
                stage = "child-adoption"
                if not _outside_job(child):
                    raise OSError("daemon broker child still belongs to a Job")
                _send(connection, {"start": child_pid}, deadline)
                if _receive(connection, deadline) != {"started": child_pid}:
                    raise OSError("daemon broker did not confirm child startup")
            stage = "bootstrap-exit"
            if bootstrap.wait(timeout=_remaining(deadline)) != 0:
                raise OSError("daemon broker did not finish successfully")
    except (OSError, ValueError, subprocess.SubprocessError):
        problems.append(stage)
    # Once staging is requested, bootstrap exit alone cannot prove that an
    # unknown child was reaped (its own cleanup may have failed).
    cleanup_confirmed = not (problems and staging_started and child is None)
    if bootstrap is not None:
        # Channel closure lets an uncommitted bootstrap reap its suspended
        # child. Killing the bootstrap first could orphan that child.
        exited, cleanup = _cleanup_process(bootstrap, terminate=False)
        cleanup_confirmed &= exited
        problems.extend("bootstrap-" + problem for problem in cleanup)
    if problems and child is not None:
        exited, cleanup = _cleanup_process(child, terminate=True)
        cleanup_confirmed &= exited
        problems.extend("child-" + problem for problem in cleanup)
    if problems:
        raise DaemonSpawnError(
            "spawn_daemon: Windows lifetime broker failed (" + ", ".join(problems) + ")",
            cleanup_confirmed=cleanup_confirmed,
        ) from None
    if child is None:
        raise DaemonSpawnError("spawn_daemon: Windows lifetime broker did not return a child")
    return child


def _bootstrap() -> int:
    deadline = time.monotonic() + _TIMEOUT
    cookie = os.environ.pop(_BOOT_COOKIE)
    port = int(os.environ.pop(_BOOT_PORT))
    child = None
    committed = False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=_remaining(deadline)) as conn:
            _send(conn, {"cookie": cookie, "pid": os.getpid()}, deadline)
            spec = _receive(conn, deadline)
            deadline = min(deadline, float(spec["deadline"]))
            with open(spec["log_path"], "ab") as output:
                error_path = spec["stderr_log_path"]
                error = open(error_path, "ab") if error_path else None
                try:
                    child = subprocess.Popen(
                        spec["argv"], cwd=spec["cwd"],
                        env={
                            key: value for key, value in spec["env"].items()
                            if key not in (_BOOT_PORT, _BOOT_COOKIE)
                        },
                        stdin=subprocess.DEVNULL, stdout=output,
                        stderr=error if error else subprocess.STDOUT,
                        close_fds=True,
                        creationflags=spec["creationflags"] | _CREATE_SUSPENDED,
                    )
                finally:
                    if error is not None:
                        error.close()
            _send(conn, {"pid": child.pid}, deadline)
            if _receive(conn, deadline) != {"start": child.pid}:
                raise OSError("daemon broker adoption was not acknowledged")
            process = WindowsDaemonProcess(child.pid, spec["argv"])
            try:
                _resume(process)
            finally:
                process.close()
            committed = True
            _send(conn, {"started": child.pid}, deadline)
        return 0
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return 1
    finally:
        if child is not None and not committed:
            child.kill()
            child.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(_bootstrap())
