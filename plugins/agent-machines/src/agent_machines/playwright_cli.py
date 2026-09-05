"""Machine-local Playwright CLI provisioning.

The provisioner owns only the reusable user-home workspace installed by
``@playwright/cli``. Browser profiles, credentials, navigation, and product
policy remain outside agent-machines.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, TextIO

from agent_procutil import no_window_flags, no_window_kwargs

PACKAGE_NAME = "@playwright/cli"
CLI_COMMAND = "playwright-cli"
PREFIX_QUERY = ["npm", "prefix", "-g"]
OUTPUT_LIMIT = 4000
DEFAULT_TIMEOUT = 600
CLEANUP_TIMEOUT = 15.0
CLEANUP_MARGIN = 60.0
PROVISION_TIMEOUT = 1500.0
MAX_SKILL_FILES = 256
MAX_SKILL_BYTES = 16 * 1024 * 1024
HASH_CHUNK_SIZE = 64 * 1024
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_WINDOWS_JOB_ARGV = "AGENT_MACHINES_JOB_ARGV"
_WINDOWS_JOB_LAUNCHER = (
    "sys=__import__('sys');"
    "gate=sys.stdin.buffer.read(1);"
    "sys.exit(125) if gate!=b'1' else None;"
    "base64=__import__('base64');"
    "json=__import__('json');"
    "os=__import__('os');"
    "subprocess=__import__('subprocess');"
    f"argv=json.loads(base64.urlsafe_b64decode(os.environ.pop('{_WINDOWS_JOB_ARGV}')));"
    "proc=subprocess.Popen(argv,stdin=subprocess.DEVNULL);"
    "sys.exit(proc.wait())"
)

PathResolver = Callable[[str], str | None]


class ProcessHandle(Protocol):
    """The bounded ``Popen`` surface used by the subprocess runner."""

    pid: int
    returncode: int | None
    stdin: TextIO | None

    def communicate(self, timeout: float | None = None) -> tuple[str, str]: ...

    def kill(self) -> None: ...


class WindowsJobHandle(Protocol):
    """The Windows Job Object surface used by the subprocess runner."""

    def assign(self, pid: int) -> None: ...

    def terminate(self, exit_code: int) -> None: ...

    def wait_empty(self, timeout: float) -> bool: ...

    def close(self) -> None: ...


PopenFactory = Callable[..., ProcessHandle]
TreeCleanup = Callable[[ProcessHandle, bool], str]
WindowsJobFactory = Callable[[], WindowsJobHandle]
GroupAlive = Callable[[int], bool]


def package_query(prefix: Path) -> list[str]:
    """Return the prefix-scoped installed-package query."""
    return [
        "npm",
        "list",
        "-g",
        PACKAGE_NAME,
        "--depth=0",
        "--json",
        "--prefix",
        str(prefix),
    ]


def latest_query(prefix: Path) -> list[str]:
    """Return the prefix-scoped registry-version query."""
    return [
        "npm",
        "view",
        PACKAGE_NAME,
        "version",
        "--json",
        "--prefix",
        str(prefix),
    ]


def root_query(prefix: Path) -> list[str]:
    """Return the prefix-scoped global package-root query."""
    return ["npm", "root", "-g", "--prefix", str(prefix)]


def package_install(prefix: Path) -> list[str]:
    """Return the prefix-scoped package installation command."""
    return [
        "npm",
        "install",
        "-g",
        f"{PACKAGE_NAME}@latest",
        "--prefix",
        str(prefix),
    ]


def skill_install(cli_path: Path) -> list[str]:
    """Return the prefix-local skill registration command."""
    return [str(cli_path), "install", "--skills", "agents"]


def npm_command(
    node_path: Path,
    npm_cli_path: Path,
    logical_argv: list[str],
) -> list[str]:
    """Run one logical npm command through Node without a shell shim."""
    if not logical_argv or logical_argv[0] != "npm":
        raise ValueError("logical npm argv must start with 'npm'")
    return [str(node_path), str(npm_cli_path), *logical_argv[1:]]


def playwright_command(node_path: Path, cli_path: Path) -> list[str]:
    """Run Playwright's JavaScript entry point through Node."""
    return [str(node_path), str(cli_path), "install", "--skills", "agents"]


@dataclass(frozen=True)
class RunOutcome:
    """Captured subprocess outcome."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


Runner = Callable[[list[str], Path], RunOutcome]


def _tail(text: str, limit: int = OUTPUT_LIMIT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[-limit:]


def _timeout_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value


def _normalize_path(value: str | Path, *, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    try:
        return path.resolve(strict=False)
    except (OSError, RuntimeError):
        return path.absolute()


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _stat_is_reparse(info: os.stat_result) -> bool:
    return bool(
        stat.S_ISLNK(info.st_mode)
        or getattr(info, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _absolute_path(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _validate_path_chain(
    target: Path,
    *,
    allowed_root: Path,
    label: str,
) -> Path:
    """Validate an existing or future target without traversing reparse points."""
    try:
        resolved_root = allowed_root.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError(f"cannot resolve allowed root for {label}: {exc}") from exc
    if not resolved_root.is_dir():
        raise ValueError(f"allowed root for {label} is not a directory: {resolved_root}")

    candidate = _absolute_path(target)
    if not _is_within(candidate, resolved_root):
        raise ValueError(f"{label} escapes allowed root {resolved_root}: {candidate}")

    current = resolved_root
    relative = candidate.relative_to(resolved_root)
    for index, component in enumerate(relative.parts):
        current = current / component
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        except OSError as exc:
            raise ValueError(f"cannot inspect {label} path {current}: {exc}") from exc
        if _stat_is_reparse(info):
            raise ValueError(f"{label} contains a symlink or reparse point: {current}")
        if index < len(relative.parts) - 1 and not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"{label} has a non-directory ancestor: {current}")
        try:
            resolved = current.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise ValueError(f"cannot resolve {label} path {current}: {exc}") from exc
        if not _is_within(resolved, resolved_root):
            raise ValueError(f"{label} escapes allowed root {resolved_root}: {current}")
    return candidate


class _WindowsJob:
    """A kill-on-close Job Object owned by the command runner."""

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        class _BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class _IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class _ExtendedLimit(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", _BasicLimit),
                ("IoInfo", _IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        class _BasicAccounting(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_int64),
                ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.AssignProcessToJobObject.argtypes = [
            wintypes.HANDLE,
            wintypes.HANDLE,
        ]
        kernel32.TerminateJobObject.restype = wintypes.BOOL
        kernel32.TerminateJobObject.argtypes = [wintypes.HANDLE, wintypes.UINT]
        kernel32.QueryInformationJobObject.restype = wintypes.BOOL
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise OSError(
                ctypes.get_last_error(),
                "CreateJobObjectW failed",
            )
        limits = _ExtendedLimit()
        limits.BasicLimitInformation.LimitFlags = (
            self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        if not kernel32.SetInformationJobObject(
            handle,
            self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(limits),
            ctypes.sizeof(limits),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(handle)
            raise OSError(error, "SetInformationJobObject failed")
        self._ctypes = ctypes
        self._kernel32 = kernel32
        self._handle = handle
        self._BasicAccounting = _BasicAccounting

    def assign(self, pid: int) -> None:
        process = self._kernel32.OpenProcess(
            self._PROCESS_SET_QUOTA | self._PROCESS_TERMINATE,
            False,
            pid,
        )
        if not process:
            raise OSError(
                self._ctypes.get_last_error(),
                f"OpenProcess({pid}) failed",
            )
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, process):
                raise OSError(
                    self._ctypes.get_last_error(),
                    "AssignProcessToJobObject failed",
                )
        finally:
            self._kernel32.CloseHandle(process)

    def terminate(self, exit_code: int) -> None:
        if self._handle and not self._kernel32.TerminateJobObject(
            self._handle,
            exit_code,
        ):
            raise OSError(
                self._ctypes.get_last_error(),
                "TerminateJobObject failed",
            )

    def wait_empty(self, timeout: float) -> bool:
        deadline = time.monotonic() + max(timeout, 0.0)
        while True:
            accounting = self._BasicAccounting()
            if not self._kernel32.QueryInformationJobObject(
                self._handle,
                1,
                self._ctypes.byref(accounting),
                self._ctypes.sizeof(accounting),
                None,
            ):
                raise OSError(
                    self._ctypes.get_last_error(),
                    "QueryInformationJobObject failed",
                )
            if accounting.ActiveProcesses == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


class SubprocessRunner:
    """Run argv commands in a contained process tree with bounded cleanup."""

    def __init__(
        self,
        *,
        resolver: PathResolver = shutil.which,
        windows: bool = os.name == "nt",
        timeout: int = DEFAULT_TIMEOUT,
        cleanup_timeout: float = CLEANUP_TIMEOUT,
        deadline: float | None = None,
        popen_factory: PopenFactory = subprocess.Popen,
        tree_cleanup: TreeCleanup | None = None,
        job_factory: WindowsJobFactory = _WindowsJob,
        clock: Callable[[], float] = time.monotonic,
        group_alive: GroupAlive | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._resolver = resolver
        self._windows = windows
        self._timeout = timeout
        self._cleanup_timeout = cleanup_timeout
        self._deadline = deadline
        self._popen_factory = popen_factory
        self._tree_cleanup = tree_cleanup or self._default_tree_cleanup
        self._job_factory = job_factory
        self._clock = clock
        self._group_alive = group_alive or self._default_group_alive
        self._sleeper = sleeper

    def _execution_argv(self, argv: list[str]) -> list[str]:
        requested = Path(argv[0])
        if requested.is_absolute() and requested.is_file():
            executable = str(requested)
        else:
            executable = self._resolver(argv[0])
        if executable is None:
            raise FileNotFoundError(f"required executable not found: {argv[0]}")
        return [executable, *argv[1:]]

    def _default_tree_cleanup(self, proc: ProcessHandle, force: bool) -> str:
        pid = getattr(proc, "pid", None)
        if not isinstance(pid, int) or pid <= 0:
            try:
                proc.kill()
            except OSError as exc:
                return f"process cleanup failed: {exc}"
            return ""
        if self._windows:
            taskkill = self._resolver("taskkill") or self._resolver("taskkill.exe")
            if taskkill is None:
                return "process-tree cleanup failed: taskkill.exe not found"
            cleanup_timeout = self._remaining_cleanup_time()
            try:
                cleanup = subprocess.run(  # noqa: S603 - exact PID tree cleanup
                    [taskkill, "/PID", str(pid), "/T", "/F"],
                    stdin=subprocess.DEVNULL,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    check=False,
                    timeout=cleanup_timeout,
                    **no_window_kwargs(),
                )
            except subprocess.TimeoutExpired:
                return (
                    "process-tree cleanup failed: taskkill exceeded "
                    f"{cleanup_timeout:g} seconds"
                )
            except OSError as exc:
                return f"process-tree cleanup failed: {exc}"
            if cleanup.returncode != 0:
                detail = (cleanup.stderr or cleanup.stdout or "").strip()
                suffix = f": {detail}" if detail else ""
                return (
                    f"process-tree cleanup exited with {cleanup.returncode}{suffix}"
                )
            return ""
        if not hasattr(os, "killpg"):
            return ""
        try:
            os.killpg(pid, signal.SIGKILL if force else signal.SIGTERM)
        except ProcessLookupError:
            return ""
        except OSError as exc:
            return f"process-group cleanup failed: {exc}"
        return ""

    @staticmethod
    def _default_group_alive(pid: int) -> bool:
        if not hasattr(os, "killpg"):
            return False
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _wait_group_empty(self, pid: int, timeout: float) -> bool:
        deadline = self._clock() + timeout
        while self._group_alive(pid):
            if self._clock() >= deadline:
                return False
            self._sleeper(min(0.05, max(0.001, deadline - self._clock())))
        return True

    def _complete_posix_group(
        self,
        proc: ProcessHandle,
        *,
        already_signaled: bool = False,
    ) -> list[str]:
        pid = getattr(proc, "pid", None)
        if not isinstance(pid, int) or pid <= 0:
            return ["process-group cleanup failed: invalid root PID"]
        errors: list[str] = []
        if not already_signaled:
            cleanup_error = self._tree_cleanup(proc, False)
            if cleanup_error:
                errors.append(cleanup_error)
        if self._wait_group_empty(pid, self._remaining_cleanup_time()):
            return errors
        cleanup_error = self._tree_cleanup(proc, True)
        if cleanup_error:
            errors.append(cleanup_error)
        if not self._wait_group_empty(pid, self._remaining_cleanup_time()):
            errors.append("process group remained active after forced cleanup")
        return errors

    @staticmethod
    def _merge_timeout_output(
        current: str,
        replacement: str | bytes | None,
    ) -> str:
        value = _timeout_output(replacement)
        return value if value else current

    def _timeout_outcome(
        self,
        proc: ProcessHandle,
        exc: subprocess.TimeoutExpired,
        job: WindowsJobHandle | None = None,
    ) -> RunOutcome:
        stdout = _timeout_output(exc.stdout)
        stderr = _timeout_output(exc.stderr)
        cleanup_errors: list[str] = []

        if job is not None:
            try:
                job.terminate(124)
            except OSError as terminate_error:
                cleanup_errors.append(f"Job Object cleanup failed: {terminate_error}")
        elif not self._windows:
            cleanup_error = self._tree_cleanup(proc, False)
            if cleanup_error:
                cleanup_errors.append(cleanup_error)
        else:
            cleanup_error = self._tree_cleanup(proc, False)
            if cleanup_error:
                cleanup_errors.append(cleanup_error)
        try:
            stdout, stderr = proc.communicate(timeout=self._remaining_cleanup_time())
        except (OSError, subprocess.TimeoutExpired) as terminate_exc:
            stdout = self._merge_timeout_output(
                stdout, getattr(terminate_exc, "stdout", None)
            )
            stderr = self._merge_timeout_output(
                stderr, getattr(terminate_exc, "stderr", None)
            )
            if isinstance(terminate_exc, OSError):
                cleanup_errors.append(f"process cleanup wait failed: {terminate_exc}")
            if job is None:
                cleanup_error = self._tree_cleanup(proc, True)
                if cleanup_error:
                    cleanup_errors.append(cleanup_error)
            elif proc.returncode is None:
                cleanup_error = self._default_tree_cleanup(proc, True)
                if cleanup_error:
                    cleanup_errors.append(cleanup_error)
            try:
                stdout, stderr = proc.communicate(
                    timeout=self._remaining_cleanup_time()
                )
            except (OSError, subprocess.TimeoutExpired) as kill_exc:
                stdout = self._merge_timeout_output(
                    stdout, getattr(kill_exc, "stdout", None)
                )
                stderr = self._merge_timeout_output(
                    stderr, getattr(kill_exc, "stderr", None)
                )
                if isinstance(kill_exc, OSError):
                    cleanup_errors.append(
                        f"process cleanup wait failed: {kill_exc}"
                    )
                try:
                    proc.kill()
                except OSError as kill_error:
                    cleanup_errors.append(
                        f"root process cleanup failed: {kill_error}"
                    )
                try:
                    stdout, stderr = proc.communicate(
                        timeout=self._remaining_cleanup_time()
                    )
                except (OSError, subprocess.TimeoutExpired) as await_exc:
                    stdout = self._merge_timeout_output(
                        stdout, getattr(await_exc, "stdout", None)
                    )
                    stderr = self._merge_timeout_output(
                        stderr, getattr(await_exc, "stderr", None)
                    )
                    cleanup_errors.append(
                        f"process tree did not exit after cleanup: {await_exc}"
                    )

        if job is None and not self._windows:
            cleanup_errors.extend(
                self._complete_posix_group(proc, already_signaled=True)
            )
        if job is not None:
            try:
                if not job.wait_empty(self._remaining_cleanup_time()):
                    cleanup_errors.append(
                        "Windows Job Object remained active after termination"
                    )
            except OSError as wait_error:
                cleanup_errors.append(f"Job Object cleanup failed: {wait_error}")
            finally:
                job.close()

        details = [stderr.strip()] if stderr.strip() else []
        details.append(f"command timed out after {exc.timeout} seconds")
        details.extend(cleanup_errors)
        return RunOutcome(124, _tail(stdout or ""), _tail("\n".join(details)))

    def _io_error_outcome(
        self,
        proc: ProcessHandle,
        exc: OSError,
        job: WindowsJobHandle | None = None,
    ) -> RunOutcome:
        cleanup_errors: list[str] = []
        if job is not None:
            try:
                job.terminate(126)
            except OSError as terminate_error:
                cleanup_errors.append(f"Job Object cleanup failed: {terminate_error}")
        elif not self._windows:
            cleanup_error = self._tree_cleanup(proc, False)
            if cleanup_error:
                cleanup_errors.append(cleanup_error)
        else:
            cleanup_error = self._tree_cleanup(proc, False)
            if cleanup_error:
                cleanup_errors.append(cleanup_error)
        try:
            proc.communicate(timeout=self._remaining_cleanup_time())
        except (OSError, subprocess.TimeoutExpired) as cleanup_error:
            if job is None:
                force_error = self._tree_cleanup(proc, True)
                if force_error:
                    cleanup_errors.append(force_error)
            elif job is not None and proc.returncode is None:
                force_error = self._default_tree_cleanup(proc, True)
                if force_error:
                    cleanup_errors.append(force_error)
            cleanup_errors.append(f"process cleanup wait failed: {cleanup_error}")
            try:
                proc.communicate(timeout=self._remaining_cleanup_time())
            except (OSError, subprocess.TimeoutExpired) as reap_error:
                cleanup_errors.append(
                    f"root process did not exit after forced cleanup: {reap_error}"
                )
        if job is None and not self._windows:
            cleanup_errors.extend(
                self._complete_posix_group(proc, already_signaled=True)
            )
        if job is not None:
            try:
                if not job.wait_empty(self._remaining_cleanup_time()):
                    cleanup_errors.append(
                        "Windows Job Object remained active after I/O failure"
                    )
            except OSError as wait_error:
                cleanup_errors.append(f"Job Object cleanup failed: {wait_error}")
            finally:
                job.close()
        details = [f"command I/O failed: {exc}", *cleanup_errors]
        return RunOutcome(126, stderr=_tail("\n".join(details)))

    def _remaining_cleanup_time(self) -> float:
        if self._deadline is None:
            return self._cleanup_timeout
        return max(
            0.001,
            min(self._cleanup_timeout, self._deadline - self._clock()),
        )

    def _command_timeout(self) -> float | None:
        if self._deadline is None:
            return float(self._timeout)
        remaining = self._deadline - self._clock() - CLEANUP_MARGIN
        if remaining <= 0:
            return None
        return min(float(self._timeout), remaining)

    def _windows_launcher(
        self,
        argv: list[str],
    ) -> tuple[list[str], dict[str, str]]:
        encoded = base64.urlsafe_b64encode(
            json.dumps(argv).encode("utf-8")
        ).decode("ascii")
        environment = dict(os.environ)
        environment[_WINDOWS_JOB_ARGV] = encoded
        return [
            sys.executable,
            "-I",
            "-S",
            "-c",
            _WINDOWS_JOB_LAUNCHER,
        ], environment

    @staticmethod
    def _release_windows_launcher(proc: ProcessHandle) -> None:
        if proc.stdin is None:
            raise OSError("Windows Job launcher gate is unavailable")
        proc.stdin.write("1")
        proc.stdin.flush()
        proc.stdin.close()
        proc.stdin = None

    def _complete_windows_job(
        self,
        job: WindowsJobHandle,
    ) -> list[str]:
        cleanup_errors: list[str] = []
        try:
            if not job.wait_empty(self._remaining_cleanup_time()):
                job.terminate(0)
                if not job.wait_empty(self._remaining_cleanup_time()):
                    cleanup_errors.append(
                        "Windows Job Object retained descendants after cleanup"
                    )
        except OSError as exc:
            cleanup_errors.append(f"Job Object cleanup failed: {exc}")
        finally:
            job.close()
        return cleanup_errors

    def __call__(self, argv: list[str], cwd: Path) -> RunOutcome:
        command_timeout = self._command_timeout()
        if command_timeout is None:
            return RunOutcome(
                124,
                stderr=(
                    "provision deadline exhausted before command launch; "
                    "cleanup margin preserved"
                ),
            )
        execution_argv = self._execution_argv(argv)
        popen_kwargs: dict[str, Any]
        job: WindowsJobHandle | None = None
        if self._windows:
            launch_argv, environment = self._windows_launcher(execution_argv)
            popen_kwargs = {
                "creationflags": no_window_flags(),
                "stdin": subprocess.PIPE,
                "env": environment,
            }
        else:
            launch_argv = execution_argv
            popen_kwargs = {"start_new_session": True}
        proc = self._popen_factory(
            launch_argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            **popen_kwargs,
        )
        if self._windows:
            try:
                job = self._job_factory()
                job.assign(proc.pid)
                self._release_windows_launcher(proc)
            except OSError as exc:
                return self._io_error_outcome(
                    proc,
                    OSError(f"cannot establish Windows Job Object containment: {exc}"),
                    job,
                )
        try:
            stdout, stderr = proc.communicate(timeout=command_timeout)
        except subprocess.TimeoutExpired as exc:
            return self._timeout_outcome(proc, exc, job)
        except OSError as exc:
            return self._io_error_outcome(proc, exc, job)
        cleanup_errors = (
            self._complete_windows_job(job)
            if job is not None
            else self._complete_posix_group(proc) if not self._windows else []
        )
        details = [stderr or "", *cleanup_errors]
        returncode = proc.returncode or 0
        if cleanup_errors and returncode == 0:
            returncode = 126
        return RunOutcome(
            returncode,
            stdout or "",
            "\n".join(detail for detail in details if detail).strip(),
        )


@dataclass(frozen=True)
class CommandEvidence:
    """Bounded evidence for one command invocation."""

    argv: list[str]
    cwd: str
    returncode: int
    stdout_tail: str = ""
    stderr_tail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "argv": self.argv,
            "cwd": self.cwd,
            "returncode": self.returncode,
            "stdout_tail": self.stdout_tail,
            "stderr_tail": self.stderr_tail,
        }


@dataclass(frozen=True)
class ProvisionAction:
    """One planned or attempted state transition."""

    action: str
    status: str
    argv: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "status": self.status,
            "argv": self.argv,
        }


@dataclass
class ProvisionResult:
    """Stable result for ``provision-playwright-cli``."""

    mode: str
    home: Path
    node_path: str | None
    npm_path: str | None
    skill_path: Path
    config_path: Path
    npm_configured_prefix: Path | None = None
    npm_prefix: Path | None = None
    npm_prefix_source: str | None = None
    npm_root: Path | None = None
    npm_cli_path: str | None = None
    package_installed: bool | None = None
    package_version: str | None = None
    package_latest_version: str | None = None
    cli_path: str | None = None
    cli_entrypoint: str | None = None
    bundled_skill_path: Path | None = None
    bundled_skill_valid: bool = False
    skill_registered: bool = False
    skill_file_count: int = 0
    config_present: bool = False
    changes_needed: bool = False
    changed: bool = False
    actions: list[ProvisionAction] = field(default_factory=list)
    commands: list[CommandEvidence] = field(default_factory=list)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "operation": "provision-playwright-cli",
            "ok": self.ok,
            "mode": self.mode,
            "home": str(self.home),
            "changes_needed": self.changes_needed,
            "changed": self.changed,
            "prerequisites": {
                "node": {
                    "available": self.node_path is not None,
                    "path": self.node_path,
                },
                "npm": {
                    "available": self.npm_cli_path is not None,
                    "path": self.npm_path,
                    "cli_path": self.npm_cli_path,
                },
            },
            "npm": {
                "configured_prefix": (
                    str(self.npm_configured_prefix)
                    if self.npm_configured_prefix is not None
                    else None
                ),
                "prefix": str(self.npm_prefix) if self.npm_prefix is not None else None,
                "prefix_source": self.npm_prefix_source,
                "root": str(self.npm_root) if self.npm_root is not None else None,
            },
            "package": {
                "name": PACKAGE_NAME,
                "installed": self.package_installed,
                "version": self.package_version,
                "latest_version": self.package_latest_version,
            },
            "cli": {
                "command": CLI_COMMAND,
                "available": self.cli_path is not None,
                "path": self.cli_path,
                "entrypoint": self.cli_entrypoint,
            },
            "skill": {
                "path": str(self.skill_path),
                "bundle_path": (
                    str(self.bundled_skill_path)
                    if self.bundled_skill_path is not None
                    else None
                ),
                "bundle_valid": self.bundled_skill_valid,
                "registered": self.skill_registered,
                "file_count": self.skill_file_count,
            },
            "workspace_config": {
                "path": str(self.config_path),
                "present": self.config_present,
            },
            "actions": [action.to_dict() for action in self.actions],
            "commands": [command.to_dict() for command in self.commands],
            "error": self.error,
        }


@dataclass(frozen=True)
class PackageState:
    installed: bool
    version: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class SkillTree:
    files: dict[str, str]
    error: str | None = None
    unsafe: bool = False


def _command_text(argv: list[str]) -> str:
    if len(argv) >= 2 and Path(argv[1]).name == "npm-cli.js":
        return " ".join(["npm", *argv[2:]])
    if len(argv) >= 2 and Path(argv[1]).name == "playwright-cli.js":
        return " ".join([CLI_COMMAND, *argv[2:]])
    return " ".join(argv)


def _package_state(
    outcome: RunOutcome,
    argv: list[str],
    *,
    prefix_exists: bool,
) -> PackageState:
    command = _command_text(argv)
    if not outcome.stdout.strip():
        return PackageState(
            installed=False,
            error=f"`{command}` returned no JSON (exit {outcome.returncode})",
        )
    try:
        payload = json.loads(outcome.stdout)
    except ValueError as exc:
        return PackageState(
            installed=False,
            error=f"`{command}` returned invalid JSON: {exc}",
        )
    if not isinstance(payload, dict):
        return PackageState(
            installed=False,
            error=f"`{command}` returned a non-object JSON value",
        )
    error = payload.get("error")
    if (
        not prefix_exists
        and isinstance(error, dict)
        and error.get("code") == "ENOENT"
    ):
        return PackageState(installed=False)
    if outcome.returncode not in (0, 1) or isinstance(error, dict):
        detail = ""
        if isinstance(error, dict):
            detail = str(error.get("summary") or error.get("message") or "").strip()
        suffix = f": {detail}" if detail else ""
        return PackageState(
            installed=False,
            error=f"`{command}` failed with exit {outcome.returncode}{suffix}",
        )
    dependencies = payload.get("dependencies", {})
    if not isinstance(dependencies, dict):
        return PackageState(
            installed=False,
            error=f"`{command}` returned invalid dependencies",
        )
    package = dependencies.get(PACKAGE_NAME)
    if package is None:
        return PackageState(installed=False)
    if not isinstance(package, dict):
        return PackageState(
            installed=False,
            error=f"`{command}` returned invalid package metadata",
        )
    if package.get("missing") is True:
        return PackageState(installed=False)
    version = package.get("version")
    return PackageState(
        installed=True,
        version=str(version) if version is not None else None,
    )


def _latest_version(outcome: RunOutcome, argv: list[str]) -> tuple[str | None, str | None]:
    command = _command_text(argv)
    if outcome.returncode != 0:
        return None, f"`{command}` exited with {outcome.returncode}"
    try:
        payload = json.loads(outcome.stdout)
    except ValueError as exc:
        return None, f"`{command}` returned invalid JSON: {exc}"
    if not isinstance(payload, str) or not payload.strip():
        return None, f"`{command}` returned an invalid version"
    return payload.strip(), None


def _path_output(
    outcome: RunOutcome,
    argv: list[str],
    *,
    base: Path,
) -> tuple[Path | None, str | None]:
    command = _command_text(argv)
    if outcome.returncode != 0:
        return None, f"`{command}` exited with {outcome.returncode}"
    value = outcome.stdout.strip()
    if not value:
        return None, f"`{command}` returned an empty path"
    return _normalize_path(value, base=base), None


def _skill_tree(
    path: Path,
    *,
    label: str,
    allowed_root: Path,
    deadline: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> SkillTree:
    files: dict[str, str] = {}
    total_bytes = 0

    def deadline_exhausted() -> bool:
        return deadline is not None and clock() >= deadline

    try:
        root = _validate_path_chain(
            path,
            allowed_root=allowed_root,
            label=label,
        )
        if not root.is_dir():
            return SkillTree({}, f"{label} directory is absent: {root}")

        pending = [root]
        candidates: list[Path] = []
        while pending:
            if deadline_exhausted():
                return SkillTree(
                    {},
                    f"{label} scan exceeded provision deadline",
                    unsafe=True,
                )
            directory = pending.pop()
            with os.scandir(directory) as entries:
                for entry in entries:
                    if deadline_exhausted():
                        return SkillTree(
                            {},
                            f"{label} scan exceeded provision deadline",
                            unsafe=True,
                        )
                    candidate = Path(entry.path)
                    relative = candidate.relative_to(root).as_posix()
                    info = candidate.lstat()
                    if _stat_is_reparse(info):
                        return SkillTree(
                            {},
                            f"{label} contains a symlink or reparse point: {relative}",
                            unsafe=True,
                        )
                    resolved = candidate.resolve(strict=True)
                    if not _is_within(resolved, allowed_root.resolve(strict=True)):
                        return SkillTree(
                            {},
                            f"{label} entry escapes allowed root: {relative}",
                            unsafe=True,
                        )
                    if stat.S_ISDIR(info.st_mode):
                        pending.append(candidate)
                    elif stat.S_ISREG(info.st_mode):
                        candidates.append(candidate)
                        if len(candidates) > MAX_SKILL_FILES:
                            return SkillTree(
                                {},
                                f"{label} exceeds {MAX_SKILL_FILES} files",
                                unsafe=True,
                            )
                    else:
                        return SkillTree(
                            {},
                            f"{label} contains unsupported entry: {relative}",
                            unsafe=True,
                        )

        for candidate in sorted(
            candidates,
            key=lambda item: item.relative_to(root).as_posix(),
        ):
            if deadline_exhausted():
                return SkillTree(
                    {},
                    f"{label} hash exceeded provision deadline",
                    unsafe=True,
                )
            relative = candidate.relative_to(root).as_posix()
            digest = hashlib.sha256()
            file_bytes = 0
            with candidate.open("rb") as stream:
                while True:
                    if deadline_exhausted():
                        return SkillTree(
                            {},
                            f"{label} hash exceeded provision deadline",
                            unsafe=True,
                        )
                    chunk = stream.read(HASH_CHUNK_SIZE)
                    if not chunk:
                        break
                    file_bytes += len(chunk)
                    total_bytes += len(chunk)
                    if total_bytes > MAX_SKILL_BYTES:
                        return SkillTree(
                            {},
                            f"{label} exceeds {MAX_SKILL_BYTES} total bytes",
                            unsafe=True,
                        )
                    digest.update(chunk)
            if file_bytes == 0:
                return SkillTree({}, f"{label} contains empty file: {relative}")
            files[relative] = digest.hexdigest()
    except (MemoryError, OSError, RuntimeError, ValueError) as exc:
        return SkillTree({}, f"{label} is unreadable: {exc}", unsafe=True)
    if "SKILL.md" not in files:
        return SkillTree({}, f"{label} is missing SKILL.md: {root}")
    return SkillTree(files)


def _tree_difference(bundle: SkillTree, target: SkillTree) -> str | None:
    if target.error is not None:
        return target.error
    missing = sorted(bundle.files.keys() - target.files.keys())
    extra = sorted(target.files.keys() - bundle.files.keys())
    different = sorted(
        relative
        for relative in bundle.files.keys() & target.files.keys()
        if bundle.files[relative] != target.files[relative]
    )
    details = []
    if missing:
        details.append(f"missing files: {', '.join(missing)}")
    if extra:
        details.append(f"extra files: {', '.join(extra)}")
    if different:
        details.append(f"different files: {', '.join(different)}")
    return "; ".join(details) or None


def _prefix_fallback(home: Path, *, windows: bool) -> Path:
    if windows:
        return home / "AppData" / "Roaming" / "npm"
    return home / ".local"


def _prefix_cli_path(prefix: Path, *, windows: bool) -> Path:
    if windows:
        return prefix / "playwright-cli.cmd"
    return prefix / "bin" / "playwright-cli"


def _resolve_prefix_cli(
    prefix: Path,
    *,
    home: Path,
    windows: bool,
) -> Path | None:
    expected = _prefix_cli_path(prefix, windows=windows)
    if not expected.is_file():
        return None
    try:
        resolved = expected.resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    if _is_within(resolved, prefix) and _is_within(resolved, home):
        return expected
    return None


def _resolve_executable(
    name: str,
    *,
    resolver: PathResolver,
    base: Path,
) -> Path | None:
    value = resolver(name)
    if value is None:
        return None
    path = _normalize_path(value, base=base)
    return path if path.is_file() else None


def _node_install_root(node_path: Path, *, windows: bool) -> Path:
    if windows:
        return node_path.parent
    return (
        node_path.parent.parent
        if node_path.parent.name.casefold() == "bin"
        else node_path.parent
    )


def _npm_cli_candidates(node_path: Path, *, windows: bool) -> list[Path]:
    install_root = _node_install_root(node_path, windows=windows)
    if windows:
        return [
            install_root / "node_modules" / "npm" / "bin" / "npm-cli.js"
        ]
    return [
        install_root / "lib" / "node_modules" / "npm" / "bin" / "npm-cli.js",
        install_root / "lib64" / "node_modules" / "npm" / "bin" / "npm-cli.js",
        install_root / "node_modules" / "npm" / "bin" / "npm-cli.js",
        install_root / "share" / "nodejs" / "npm" / "bin" / "npm-cli.js",
    ]


def _trusted_npm_cli(
    node_path: Path,
    npm_executable: Path,
    *,
    windows: bool,
) -> Path | None:
    install_root = _node_install_root(node_path, windows=windows).resolve()
    trusted: list[Path] = []
    for candidate in _npm_cli_candidates(node_path, windows=windows):
        package_root = candidate.parents[1]
        try:
            validated_package_root = _validate_path_chain(
                package_root,
                allowed_root=install_root,
                label="npm package root",
            )
            validated_cli = _validate_path_chain(
                candidate,
                allowed_root=install_root,
                label="npm CLI entry point",
            )
            resolved = validated_cli.resolve(strict=True)
            resolved_package_root = validated_package_root.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            continue
        if (
            validated_cli.is_file()
            and resolved.parent == resolved_package_root / "bin"
            and resolved.name == "npm-cli.js"
        ):
            trusted.append(resolved)
    try:
        resolved_npm = npm_executable.resolve(strict=True)
    except (OSError, RuntimeError):
        resolved_npm = None
    if resolved_npm is not None and resolved_npm in trusted:
        return resolved_npm
    if resolved_npm is not None and not windows:
        if resolved_npm in trusted:
            return resolved_npm
    return trusted[0] if trusted else None


def _trusted_playwright_entry(
    npm_root: Path,
    *,
    prefix: Path,
    home: Path,
) -> Path | None:
    package_root = npm_root / "@playwright" / "cli"
    candidate = package_root / "playwright-cli.js"
    try:
        validated_package_root = _validate_path_chain(
            package_root,
            allowed_root=npm_root,
            label="Playwright CLI package root",
        )
        validated_candidate = _validate_path_chain(
            candidate,
            allowed_root=validated_package_root,
            label="Playwright CLI JavaScript entry point",
        )
        resolved = validated_candidate.resolve(strict=True)
        resolved_package_root = validated_package_root.resolve(strict=True)
    except (OSError, RuntimeError, ValueError):
        return None
    if not validated_candidate.is_file():
        return None
    if not _is_within(resolved, resolved_package_root):
        return None
    if not _is_within(resolved, npm_root):
        return None
    if not _is_within(resolved, prefix) or not _is_within(resolved, home):
        return None
    return resolved


def _record_command(
    result: ProvisionResult,
    runner: Runner,
    argv: list[str],
) -> RunOutcome:
    try:
        outcome = runner(argv, result.home)
    except FileNotFoundError as exc:
        outcome = RunOutcome(127, stderr=str(exc))
    except OSError as exc:
        outcome = RunOutcome(126, stderr=str(exc))
    except subprocess.TimeoutExpired as exc:
        stderr = _timeout_output(exc.stderr)
        timeout_detail = f"command timed out after {exc.timeout} seconds"
        outcome = RunOutcome(
            124,
            stdout=_timeout_output(exc.stdout),
            stderr=f"{stderr}\n{timeout_detail}".strip(),
        )
    result.commands.append(
        CommandEvidence(
            argv=list(argv),
            cwd=str(result.home),
            returncode=outcome.returncode,
            stdout_tail=_tail(outcome.stdout),
            stderr_tail=_tail(outcome.stderr),
        )
    )
    return outcome


def provision_playwright_cli(
    *,
    apply: bool = False,
    home: Path | None = None,
    runner: Runner | None = None,
    resolver: PathResolver = shutil.which,
    windows: bool | None = None,
) -> ProvisionResult:
    """Plan or apply the latest prefix-scoped package and registered skill."""
    provision_deadline = time.monotonic() + PROVISION_TIMEOUT
    user_home = (home or Path.home()).expanduser().resolve()
    is_windows = os.name == "nt" if windows is None else windows
    agents_path = user_home / ".agents"
    skills_path = agents_path / "skills"
    skill_path = skills_path / "playwright-cli"
    playwright_path = user_home / ".playwright"
    config_path = playwright_path / "cli.config.json"
    resolved_node = _resolve_executable(
        "node",
        resolver=resolver,
        base=user_home,
    )
    resolved_npm = _resolve_executable(
        "npm",
        resolver=resolver,
        base=user_home,
    )
    result = ProvisionResult(
        mode="apply" if apply else "dry-run",
        home=user_home,
        node_path=str(resolved_node) if resolved_node is not None else None,
        npm_path=str(resolved_npm) if resolved_npm is not None else None,
        skill_path=skill_path,
        config_path=config_path,
    )
    for target, label in (
        (agents_path, "Agent Skills root"),
        (skills_path, "Agent Skills directory"),
        (skill_path, "registered Playwright CLI skill"),
        (playwright_path, "Playwright workspace directory"),
        (config_path, "Playwright workspace config"),
    ):
        try:
            _validate_path_chain(
                target,
                allowed_root=user_home,
                label=label,
            )
        except ValueError as exc:
            result.error = str(exc)
            return result
    result.config_present = config_path.is_file()
    if resolved_node is None:
        result.error = "required executable not found: node"
        return result
    if resolved_npm is None:
        result.error = "required executable not found: npm"
        return result
    npm_cli_path = _trusted_npm_cli(
        resolved_node,
        resolved_npm,
        windows=is_windows,
    )
    if npm_cli_path is None:
        result.error = (
            "trusted npm CLI entry point not found for detected Node installation"
        )
        return result
    result.npm_cli_path = str(npm_cli_path)

    command_runner = runner or SubprocessRunner(
        resolver=resolver,
        windows=is_windows,
        deadline=provision_deadline,
    )
    prefix_argv = npm_command(resolved_node, npm_cli_path, PREFIX_QUERY)
    prefix_outcome = _record_command(result, command_runner, prefix_argv)
    configured_prefix, error = _path_output(
        prefix_outcome,
        prefix_argv,
        base=user_home,
    )
    if error is not None or configured_prefix is None:
        result.error = error
        return result
    result.npm_configured_prefix = configured_prefix
    if _is_within(configured_prefix, user_home):
        prefix = configured_prefix
        result.npm_prefix_source = "configured"
    else:
        prefix = _prefix_fallback(user_home, windows=is_windows)
        result.npm_prefix_source = "user-fallback"
    try:
        prefix = _validate_path_chain(
            prefix,
            allowed_root=user_home,
            label="selected npm prefix",
        )
    except ValueError as exc:
        result.error = str(exc)
        return result
    result.npm_prefix = prefix

    latest_argv = npm_command(
        resolved_node,
        npm_cli_path,
        latest_query(prefix),
    )
    latest_outcome = _record_command(result, command_runner, latest_argv)
    latest_version, error = _latest_version(latest_outcome, latest_argv)
    if error is not None or latest_version is None:
        result.error = error
        return result
    result.package_latest_version = latest_version

    query_argv = npm_command(
        resolved_node,
        npm_cli_path,
        package_query(prefix),
    )
    query_outcome = _record_command(result, command_runner, query_argv)
    package_state = _package_state(
        query_outcome,
        query_argv,
        prefix_exists=prefix.is_dir(),
    )
    if package_state.error is not None:
        result.error = package_state.error
        return result
    result.package_installed = package_state.installed
    result.package_version = package_state.version

    root_argv = npm_command(
        resolved_node,
        npm_cli_path,
        root_query(prefix),
    )
    root_outcome = _record_command(result, command_runner, root_argv)
    npm_root, error = _path_output(root_outcome, root_argv, base=user_home)
    if error is not None or npm_root is None:
        result.error = error
        return result
    try:
        if not _is_within(npm_root, prefix):
            raise ValueError("npm global package root is outside selected prefix")
        npm_root = _validate_path_chain(
            npm_root,
            allowed_root=user_home,
            label="npm global package root",
        )
    except ValueError:
        result.error = (
            f"`{_command_text(root_argv)}` returned a root outside the selected "
            f"prefix or user home, or through an unsafe path: {npm_root}"
        )
        return result
    result.npm_root = npm_root
    result.bundled_skill_path = (
        npm_root / "@playwright" / "cli" / "skills" / "playwright-cli"
    )

    package_needs_update = (
        not package_state.installed or package_state.version != latest_version
    )
    install_action_argv = package_install(prefix)
    install_argv = npm_command(
        resolved_node,
        npm_cli_path,
        install_action_argv,
    )
    register_action_argv = [CLI_COMMAND, "install", "--skills", "agents"]
    package_updated = False
    if package_needs_update:
        result.changes_needed = True
        if not apply:
            result.actions.extend(
                [
                    ProvisionAction(
                        "install-package",
                        "planned",
                        install_action_argv,
                    ),
                    ProvisionAction(
                        "register-skill",
                        "planned",
                        register_action_argv,
                    ),
                ]
            )
            return result

        if not prefix.is_dir():
            try:
                prefix.mkdir(parents=True, exist_ok=True)
            except OSError as exc:
                result.actions.append(
                    ProvisionAction(
                        "install-package",
                        "failed",
                        install_action_argv,
                    )
                )
                result.error = f"cannot create selected npm prefix {prefix}: {exc}"
                return result
        install_outcome = _record_command(result, command_runner, install_argv)
        if install_outcome.returncode != 0:
            result.actions.append(
                ProvisionAction(
                    "install-package",
                    "failed",
                    install_action_argv,
                )
            )
            result.error = (
                f"`{_command_text(install_argv)}` exited with "
                f"{install_outcome.returncode}"
            )
            return result
        verification_outcome = _record_command(result, command_runner, query_argv)
        package_state = _package_state(
            verification_outcome,
            query_argv,
            prefix_exists=prefix.is_dir(),
        )
        if package_state.error is not None:
            result.actions.append(
                ProvisionAction(
                    "install-package",
                    "failed",
                    install_action_argv,
                )
            )
            result.error = package_state.error
            return result
        result.package_installed = package_state.installed
        result.package_version = package_state.version
        if not package_state.installed:
            result.actions.append(
                ProvisionAction(
                    "install-package",
                    "failed",
                    install_action_argv,
                )
            )
            result.error = (
                f"package {PACKAGE_NAME} is absent from {prefix} after install"
            )
            return result
        if package_state.version != latest_version:
            result.actions.append(
                ProvisionAction(
                    "install-package",
                    "failed",
                    install_action_argv,
                )
            )
            result.error = (
                f"package {PACKAGE_NAME} version {package_state.version!r} does "
                f"not match registry latest {latest_version!r} after install"
            )
            return result
        package_updated = True
        result.actions.append(
            ProvisionAction(
                "install-package",
                "succeeded",
                install_action_argv,
            )
        )
        result.changed = True

    cli_path = _resolve_prefix_cli(
        prefix,
        home=user_home,
        windows=is_windows,
    )
    result.cli_path = str(cli_path) if cli_path is not None else None
    playwright_entry = _trusted_playwright_entry(
        npm_root,
        prefix=prefix,
        home=user_home,
    )
    result.cli_entrypoint = (
        str(playwright_entry) if playwright_entry is not None else None
    )
    if playwright_entry is None:
        result.error = (
            "trusted Playwright CLI JavaScript entry point is unavailable "
            f"within selected npm prefix {prefix}"
        )
        return result

    package_root = npm_root / "@playwright" / "cli"
    try:
        package_root = _validate_path_chain(
            package_root,
            allowed_root=npm_root,
            label="Playwright CLI package root",
        )
    except ValueError as exc:
        result.error = str(exc)
        return result
    bundle = _skill_tree(
        result.bundled_skill_path,
        label="bundled Playwright CLI skill",
        allowed_root=package_root,
        deadline=provision_deadline,
    )
    if bundle.error is not None:
        result.error = bundle.error
        return result
    result.bundled_skill_valid = True

    target = _skill_tree(
        skill_path,
        label="registered Playwright CLI skill",
        allowed_root=user_home,
        deadline=provision_deadline,
    )
    if target.unsafe:
        result.error = target.error
        return result
    difference = _tree_difference(bundle, target)
    result.skill_registered = difference is None
    result.skill_file_count = len(target.files) if difference is None else 0
    registration_needed = package_updated or difference is not None
    if not registration_needed:
        result.config_present = config_path.is_file()
        return result

    result.changes_needed = True
    register_argv = playwright_command(resolved_node, playwright_entry)
    if not apply:
        result.actions.append(
            ProvisionAction(
                "register-skill",
                "planned",
                register_action_argv,
            )
        )
        return result

    registration = _record_command(result, command_runner, register_argv)
    if registration.returncode != 0:
        result.actions.append(
            ProvisionAction(
                "register-skill",
                "failed",
                register_action_argv,
            )
        )
        result.error = (
            f"`{_command_text(register_argv)}` exited with "
            f"{registration.returncode}"
        )
        return result

    target = _skill_tree(
        skill_path,
        label="registered Playwright CLI skill",
        allowed_root=user_home,
        deadline=provision_deadline,
    )
    if target.unsafe:
        result.actions.append(
            ProvisionAction(
                "register-skill",
                "failed",
                register_action_argv,
            )
        )
        result.error = target.error
        return result
    for target_path, label in (
        (playwright_path, "Playwright workspace directory"),
        (config_path, "Playwright workspace config"),
    ):
        try:
            _validate_path_chain(
                target_path,
                allowed_root=user_home,
                label=label,
            )
        except ValueError as exc:
            result.actions.append(
                ProvisionAction(
                    "register-skill",
                    "failed",
                    register_action_argv,
                )
            )
            result.error = str(exc)
            return result
    difference = _tree_difference(bundle, target)
    if difference is not None:
        result.actions.append(
            ProvisionAction(
                "register-skill",
                "failed",
                register_action_argv,
            )
        )
        result.error = (
            "registered Playwright CLI skill tree does not match bundled skill: "
            f"{difference}"
        )
        return result

    result.skill_registered = True
    result.skill_file_count = len(target.files)
    result.config_present = config_path.is_file()
    result.actions.append(
        ProvisionAction(
            "register-skill",
            "succeeded",
            register_action_argv,
        )
    )
    result.changed = True
    return result
