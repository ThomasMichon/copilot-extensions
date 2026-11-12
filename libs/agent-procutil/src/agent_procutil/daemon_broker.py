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
from typing import Any, TypeVar

_BOOT_PORT = "_CE_DAEMON_BROKER_PORT"
_BOOT_COOKIE = "_CE_DAEMON_BROKER_COOKIE"
_TIMEOUT = 30.0
_MAX_FRAME = 16 * 1024 * 1024
_HELLO_TIMEOUT = 2.0
_MAX_HELLO = 4096
_MAX_PENDING = 32
_BACKLOG = 32
_SYNCHRONIZE = 0x00100000
_PROCESS_ACCESS = _SYNCHRONIZE | 0x0001 | 0x0800 | 0x1000
_WAIT_TIMEOUT = 0x102
_CREATE_SUSPENDED = 0x00000004
_PROCESS_DUP_HANDLE = 0x0040
_DUPLICATE_SAME_ACCESS = 0x00000002
_DUPLICATE_CLOSE_SOURCE = 0x00000001
_ProcessT = TypeVar("_ProcessT", bound="WindowsDaemonProcess")

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
    if ($r.ReturnValue -ne 0) {
        [Console]::Out.WriteLine('returnvalue=' + [int]$r.ReturnValue)
        exit 1
    }
    [Console]::Out.WriteLine([int]$r.ProcessId)
} catch {
    [Console]::Out.WriteLine('exception=' + $_.Exception.GetType().Name)
    exit 1
}
"""
WMI_POLICY_HINT = (
    "WMI process creation may be blocked by policy, for example the Microsoft "
    "Defender ASR rule 'Block process creations originating from PSExec and "
    "WMI commands'"
)


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

    @classmethod
    def from_handle(cls: type[_ProcessT], handle: int, pid: int, args: list[str]) -> _ProcessT:
        """Take an owned actual-process handle without reopening a numeric PID."""
        process = cls.__new__(cls)
        process.pid, process.args, process.returncode = pid, args, None
        process._handle = handle
        return process

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
            self._handle = None
            try:
                _kernel32().CloseHandle(handle)
            except Exception:  # noqa: BLE001 - interpreter shutdown releases it
                pass


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


def _authenticated(hello: dict, cookie: str) -> bool:
    received = hello.get("cookie")
    return isinstance(received, str) and secrets.compare_digest(
        received.encode("utf-8"), cookie.encode(),
    )


def _hello_frame(buffer: bytearray) -> dict | None:
    """Return a complete hello frame, ``None`` if incomplete; raise if invalid."""
    if len(buffer) < 4:
        return None
    size = int.from_bytes(buffer[:4], "big")
    if not 0 < size <= _MAX_HELLO or len(buffer) > 4 + size:
        raise ValueError("invalid daemon broker hello")
    if len(buffer) < 4 + size:
        return None
    hello = json.loads(bytes(buffer[4:]))
    if not isinstance(hello, dict):
        raise ValueError("invalid daemon broker hello")
    return hello


def _accept_bootstrap(
    listener: socket.socket, cookie: str, pid: int, deadline: float,
) -> socket.socket:
    """Return the bootstrap's authenticated connection.

    Any local process can connect to the loopback listener, so clients are
    read concurrently with bounded resources: at most ``_MAX_PENDING``
    unauthenticated connections (the oldest is evicted), each with a small
    hello size and ``_HELLO_TIMEOUT``. Silent, malformed or wrong-cookie
    clients are dropped without delaying a ready bootstrap. A correct cookie
    with the wrong PID is a real identity failure, not a stray client.
    """
    import selectors

    pending: dict[socket.socket, tuple[bytearray, float]] = {}
    selector = selectors.DefaultSelector()

    def drop(connection: socket.socket) -> None:
        pending.pop(connection, None)
        selector.unregister(connection)
        connection.close()

    listener.setblocking(False)
    selector.register(listener, selectors.EVENT_READ)
    try:
        while True:
            now = time.monotonic()
            for connection, (_, expiry) in list(pending.items()):
                if expiry <= now:
                    drop(connection)
            wake = min([deadline, *(expiry for _, expiry in pending.values())])
            _remaining(deadline)
            for key, _ in selector.select(max(0.0, wake - now)):
                connection = key.fileobj
                if connection is listener:
                    try:
                        accepted = listener.accept()[0]
                    except (BlockingIOError, InterruptedError):
                        continue
                    if len(pending) >= _MAX_PENDING:
                        drop(next(iter(pending)))
                    accepted.setblocking(False)
                    pending[accepted] = (bytearray(), time.monotonic() + _HELLO_TIMEOUT)
                    selector.register(accepted, selectors.EVENT_READ)
                    continue
                if connection not in pending:
                    continue
                buffer = pending[connection][0]
                try:
                    data = connection.recv(_MAX_HELLO + 5 - len(buffer))
                    if not data:
                        raise OSError("daemon broker client closed")
                    buffer.extend(data)
                    hello = _hello_frame(buffer)
                    if hello is None:
                        continue
                    authenticated = _authenticated(hello, cookie)
                except (BlockingIOError, InterruptedError):
                    continue
                except (OSError, ValueError, UnicodeError):
                    authenticated = False
                if not authenticated:
                    drop(connection)
                    continue
                pending.pop(connection)
                selector.unregister(connection)
                if hello.get("pid") != pid:
                    connection.close()
                    raise OSError("daemon broker identity mismatch")
                connection.setblocking(True)
                return connection
    finally:
        for connection in list(pending):
            drop(connection)
        selector.close()

def _outside_caller_job(process: WindowsDaemonProcess) -> bool:
    """Observe immediate membership only; this is not durable lifetime proof."""
    from ctypes import wintypes
    from ._windows_caller_owned import _job_pids

    kernel = _kernel32()
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    in_job = wintypes.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(in_job)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not in_job.value:
        return True
    members = _job_pids(None, kernel=kernel)
    if os.getpid() not in members:
        raise OSError("caller Job membership did not identify the caller")
    # The process's native handle pins this PID throughout adoption.
    return process.pid not in members


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
        # Only the fixed-format status line is reported: never echo output
        # that could carry environment or command details.
        status = str(result.stdout or "").strip().splitlines()[-1:] or ["no-status"]
        detail = status[0] if status[0].replace("=", "").replace("_", "").isalnum() else "unknown"
        raise OSError(f"daemon broker activation failed (Win32_Process.Create {detail[:64]})")
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
    process: WindowsDaemonProcess, *, terminate: bool, retain_unconfirmed: bool = False,
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
    if not exited and retain_unconfirmed:
        # Keep the pinned handle: the caller transfers it with the failure so
        # a possibly live daemon remains controllable.
        return exited, problems
    try:
        process.close()
    except OSError:
        problems.append("close")
    return exited, problems


def _duplicate_into(process: WindowsDaemonProcess, handles: list[int]) -> list[int]:
    """Duplicate caller stdio handles into the WMI bootstrap (no inheritance there)."""
    from ctypes import wintypes

    kernel = _kernel32()
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.DuplicateHandle.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, wintypes.HANDLE,
        ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD, wintypes.BOOL, wintypes.DWORD,
    ]
    kernel.DuplicateHandle.restype = wintypes.BOOL
    target = kernel.OpenProcess(_PROCESS_DUP_HANDLE, False, process.pid)
    if not target:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        duplicated: list[int] = []
        try:
            for handle in handles:
                copy = wintypes.HANDLE()
                if not kernel.DuplicateHandle(
                    kernel.GetCurrentProcess(), handle, target, ctypes.byref(copy),
                    0, False, _DUPLICATE_SAME_ACCESS,
                ):
                    raise ctypes.WinError(ctypes.get_last_error())
                duplicated.append(int(copy.value or 0))
        except OSError:
            # Close the copies already placed in the bootstrap so a partial
            # transfer never leaves a pipe end open there.
            for copy_value in duplicated:
                kernel.DuplicateHandle(
                    target, copy_value, None, None, 0, False, _DUPLICATE_CLOSE_SOURCE,
                )
            raise
        return duplicated
    finally:
        kernel.CloseHandle(target)


def spawn_brokered_daemon(
    command: list[str], *, cwd: str, env: dict[str, str], creationflags: int,
    log_path: str, stderr_log_path: str | None,
    instance_receipt=None, stdio_handles: tuple[int, int, int] | None = None,
) -> WindowsDaemonProcess:
    """Durably launch a resident daemon through WMI ``Win32_Process.Create``.

    This is the Windows durable-launch primitive (#12117): the WMI provider
    creates a bootstrap outside every caller Job and process tree, so neither a
    caller tree-kill nor closing a kill-on-close Job can end the daemon. WMI
    inherits nothing, so the environment, cwd, logs and optional stdio handles
    are passed explicitly. The actual child is pinned by handle, PID and
    creation time and must be outside every Job before it is resumed. Use it
    only for daemons that must outlive their launcher; bounded and
    tool-to-tool children keep normal creation and their caller's Jobs.
    """
    from . import DaemonSpawnError

    deadline = time.monotonic() + _TIMEOUT
    child = bootstrap = None
    staging_started = False
    admission_sent = False
    child_identity = None
    cookie = secrets.token_hex(32)
    stage = "activation"
    activation_detail = ""
    problems = []
    try:
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(_BACKLOG)
            bootstrap_env = {
                **os.environ,
                _BOOT_COOKIE: cookie,
                _BOOT_PORT: str(listener.getsockname()[1]),
            }
            argv = [_bootstrap_python(), "-I", str(Path(__file__).resolve())]
            try:
                pid = _activate(argv, bootstrap_env, cwd, deadline)
            except OSError as error:
                activation_detail = str(error)
                raise
            bootstrap = WindowsDaemonProcess(pid, argv)
            stage = "bootstrap-identity"
            with _accept_bootstrap(listener, cookie, pid, deadline) as connection:
                # WMI may place the bootstrap in its provider host's quota Job
                # (never a caller Job). Only the resident child, created with
                # breakaway, must be outside every Job; that is checked below.
                stage = "child-staging"
                handles = (
                    None if stdio_handles is None
                    else _duplicate_into(bootstrap, list(stdio_handles))
                )
                staging_started = True
                _send(connection, {
                    "argv": command, "cwd": cwd, "env": env,
                    "creationflags": creationflags, "log_path": log_path,
                    "stderr_log_path": stderr_log_path, "deadline": deadline,
                    "stdio_handles": handles,
                }, deadline)
                response = _receive(connection, deadline)
                child_pid = response.get("pid")
                if type(child_pid) is not int or child_pid <= 0:
                    raise OSError("daemon broker did not stage a child")
                child = WindowsDaemonProcess(child_pid, command)
                from ._daemon_instance_identity import created_process_identity
                child_identity = created_process_identity(child)
                stage = "child-adoption"
                if not _outside_job(child):
                    raise OSError("daemon broker child still belongs to a Job")
                if instance_receipt is not None:
                    from ._daemon_instance_store import bind_process
                    if child_identity is None:
                        bind_process(instance_receipt, child_pid)
                    else:
                        bind_process(instance_receipt, child_pid, observed_birth=child_identity[1])
                admission_sent = True
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
    child_retained = False
    if problems and child is not None:
        exited, cleanup = _cleanup_process(child, terminate=True, retain_unconfirmed=True)
        cleanup_confirmed &= exited
        child_retained = not exited
        problems.extend("child-" + problem for problem in cleanup)
    if problems:
        if activation_detail:
            message = (
                "spawn_daemon: Windows durable launch via WMI Win32_Process.Create "
                f"failed: {activation_detail}; {WMI_POLICY_HINT}; daemon not started"
            )
        else:
            message = (
                "spawn_daemon: Windows lifetime broker failed (" + ", ".join(problems) + ")"
            )
        failure = DaemonSpawnError(
            message,
            cleanup_confirmed=cleanup_confirmed,
            process_created=True if child is not None else (None if staging_started else False),
            admission_sent=admission_sent,
            process_identity=child_identity,
        )
        if child_retained:
            failure.process = child
        raise failure from None
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
            stdio = spec.get("stdio_handles")
            with open(spec["log_path"], "ab") as output:
                error_path = spec["stderr_log_path"]
                error = open(error_path, "ab") if error_path else None
                streams: dict[str, Any] = {
                    "stdin": subprocess.DEVNULL, "stdout": output,
                    "stderr": error if error else subprocess.STDOUT,
                }
                descriptors = []
                try:
                    if stdio is not None:
                        import msvcrt

                        for name, handle in zip(("stdin", "stdout", "stderr"), stdio):
                            descriptor = msvcrt.open_osfhandle(int(handle), 0)
                            descriptors.append(descriptor)
                            streams[name] = descriptor
                    child = subprocess.Popen(
                        spec["argv"], cwd=spec["cwd"],
                        env={
                            key: value for key, value in spec["env"].items()
                            if key not in (_BOOT_PORT, _BOOT_COOKIE)
                        },
                        close_fds=True,
                        creationflags=spec["creationflags"] | _CREATE_SUSPENDED,
                        **streams,
                    )
                finally:
                    for descriptor in descriptors:
                        os.close(descriptor)
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
