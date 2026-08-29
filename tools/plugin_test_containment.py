#!/usr/bin/env python3
"""Cross-platform containment for repository plugin test processes."""

from __future__ import annotations

import ctypes
import os
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence, TypeVar

CONTAINED_ENV = "COPILOT_EXTENSIONS_TEST_CONTAINED"
SANDBOX_ENV = "COPILOT_EXTENSIONS_TEST_SANDBOX"
ALLOW_HOST_STATE_ENV = "COPILOT_EXTENSIONS_ALLOW_HOST_STATE"

_ROOT_ENV = {
    "HOME": "home",
    "USERPROFILE": "home",
    "APPDATA": "home/AppData/Roaming",
    "LOCALAPPDATA": "home/AppData/Local",
    "PROGRAMDATA": "program-data",
    "XDG_CONFIG_HOME": "home/.config",
    "XDG_CACHE_HOME": "home/.cache",
    "XDG_DATA_HOME": "home/.local/share",
    "XDG_STATE_HOME": "home/.local/state",
    "XDG_RUNTIME_DIR": "run",
    "COPILOT_HOME": "home/.copilot",
    "AGENT_HOME": "home",
    "AGENT_WORKTREES_HOME": "agent-worktrees",
    "AGENT_WORKTREES_PIVOTS_DIR": "agent-worktrees/pivots",
    "AGENT_WORKTREES_PLUGINS_DIR": "agent-worktrees/plugins",
    "AGENT_BRIDGE_CONFIG_DIR": "agent-bridge/config",
    "AGENT_BRIDGE_PROVIDERS_DIR": "agent-bridge/providers",
    "AGENT_DISPATCH_ROUTING_DIR": "agent-dispatch/routing",
    "AGENT_DISPATCH_RUN_DIR": "agent-dispatch/run",
    "AGENT_INDEX_DATA_DIR": "agent-index/data",
    "AGENT_INDEX_BACKUP_DIR": "agent-index/backups",
    "AGENT_INDEX_ENGINE_HOME": "agent-index/engine",
    "AGENT_LOGGER_HOME": "agent-logger",
    "AGENT_MCP_HOME": "agent-mcp",
    "AGENT_VAULT_CORE_RUN_DIR": "agent-vault/run",
    "AGENT_VAULT_KEK_DIR": "agent-vault/kek",
    "TEMP": "tmp",
    "TMP": "tmp",
    "TMPDIR": "tmp",
}

_FILE_ENV = {
    "AGENT_CONTAINERS_CONFIG": "agent-containers/config.yaml",
    "AGENT_DISPATCH_DB": "agent-dispatch/state/dispatch.db",
    "AGENT_DISPATCH_ENDPOINT": "agent-dispatch/run/endpoint.json",
    "AGENT_WORKTREES_PROJECTS_YAML": "agent-worktrees/projects.yaml",
    "AGENT_WORKTREES_REPOS_YAML": "agent-worktrees/repos.yaml",
}

ROOT_ENV_NAMES = tuple((*_ROOT_ENV, *_FILE_ENV))
ALWAYS_SANDBOX_ENV_NAMES = (
    "TEMP",
    "TMP",
    "TMPDIR",
    "XDG_RUNTIME_DIR",
)

_ALWAYS_SCRUB_NAMES = {
    "AGENT_RT_ROOT",
    "AGENT_WORKTREES_CONFIG_ROOT",
    "AGENT_WORKTREES_OWNER_REF",
    "AGENT_WORKTREES_PAYLOAD_ROOT",
    "COPILOT_AGENT_SESSION_ID",
    "COPILOT_CUSTOM_INSTRUCTIONS_DIRS",
    "COPILOT_PLUGIN_ROOT",
}

_CREDENTIAL_NAMES = {
    "GH_TOKEN",
    "GITHUB_TOKEN",
}

_T = TypeVar("_T")


@dataclass(frozen=True)
class Limits:
    """Resource limits for one plugin test process tree."""

    wall_seconds: float = 900.0
    max_processes: int = 128
    max_memory_mb: int = 4096
    max_temp_mb: int = 2048
    poll_seconds: float = 0.25

    def validate(self) -> None:
        values = {
            "wall_seconds": self.wall_seconds,
            "max_processes": self.max_processes,
            "max_memory_mb": self.max_memory_mb,
            "max_temp_mb": self.max_temp_mb,
            "poll_seconds": self.poll_seconds,
        }
        invalid = [name for name, value in values.items() if value <= 0]
        if invalid:
            raise ValueError(f"containment limits must be positive: {', '.join(invalid)}")


class ContainmentError(RuntimeError):
    """Raised when the process tree cannot be contained safely."""


@dataclass(frozen=True)
class _WindowsEnvironmentSnapshot:
    user: dict[str, tuple[Any, int]]
    machine: dict[str, tuple[Any, int]]


def _read_registry_environment() -> _WindowsEnvironmentSnapshot | None:
    if os.name != "nt":
        return None
    import winreg

    def read(hive: int, subkey: str) -> dict[str, tuple[Any, int]]:
        values: dict[str, tuple[Any, int]] = {}
        try:
            key = winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ)
        except FileNotFoundError:
            return values
        with key:
            index = 0
            while True:
                try:
                    name, value, value_type = winreg.EnumValue(key, index)
                except OSError as exc:
                    if exc.winerror != 259:
                        raise
                    break
                values[name] = (value, value_type)
                index += 1
        return values

    return _WindowsEnvironmentSnapshot(
        user=read(winreg.HKEY_CURRENT_USER, "Environment"),
        machine=read(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
    )


def _registry_environment_drift(
    snapshot: _WindowsEnvironmentSnapshot | None,
) -> list[str]:
    if snapshot is None:
        return []
    current_snapshot = _read_registry_environment()
    if current_snapshot is None:
        raise ContainmentError("Windows environment registry became unavailable")

    def compare(
        label: str,
        expected: dict[str, tuple[Any, int]],
        current: dict[str, tuple[Any, int]],
    ) -> list[str]:
        expected_folded = {
            name.casefold(): (name, data) for name, data in expected.items()
        }
        current_folded = {
            name.casefold(): (name, data) for name, data in current.items()
        }
        names = sorted(set(expected_folded) | set(current_folded))
        drifted = [
            name
            for name in names
            if expected_folded.get(name, (None, None))[1]
            != current_folded.get(name, (None, None))[1]
        ]
        return [
            f"{label}:{current_folded.get(name, expected_folded.get(name))[0]}"
            for name in drifted
        ]

    return compare("User", snapshot.user, current_snapshot.user) + compare(
        "Machine",
        snapshot.machine,
        current_snapshot.machine,
    )


def partition(items: list[_T], size: int) -> list[list[_T]]:
    """Split ``items`` into stable sequential groups of at most ``size``."""
    if size <= 0:
        raise ValueError("partition size must be positive")
    return [items[index : index + size] for index in range(0, len(items), size)]


def isolated_environment(
    base: Mapping[str, str],
    sandbox: Path,
    *,
    allow_explicit_tiers: bool = False,
    allow_host_state: bool = False,
) -> dict[str, str]:
    """Return a contained environment, host-detached unless explicitly allowed."""
    root = sandbox.resolve()
    env = dict(base)
    for name in _ALWAYS_SCRUB_NAMES:
        env.pop(name, None)
    if allow_host_state:
        env[ALLOW_HOST_STATE_ENV] = "1"
        for name in ALWAYS_SANDBOX_ENV_NAMES:
            value = root.joinpath(*_ROOT_ENV[name].split("/"))
            value.mkdir(parents=True, exist_ok=True)
            env[name] = str(value)
    else:
        env.pop(ALLOW_HOST_STATE_ENV, None)
        for name in _CREDENTIAL_NAMES:
            env.pop(name, None)
        for name, relative in _ROOT_ENV.items():
            value = root.joinpath(*relative.split("/"))
            value.mkdir(parents=True, exist_ok=True)
            env[name] = str(value)
        for name, relative in _FILE_ENV.items():
            value = root.joinpath(*relative.split("/"))
            value.parent.mkdir(parents=True, exist_ok=True)
            env[name] = str(value)
    env[CONTAINED_ENV] = "1"
    env[SANDBOX_ENV] = str(root)
    if allow_explicit_tiers:
        env["COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS"] = "1"
    else:
        env.pop("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS", None)
    return env


def _tree_size(path: Path) -> int:
    total = 0
    try:
        files = path.rglob("*")
        for entry in files:
            try:
                if entry.is_file() and not entry.is_symlink():
                    total += entry.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def _linux_group_usage(pgid: int) -> tuple[int, int] | None:
    proc_root = Path("/proc")
    if not proc_root.is_dir():
        return None
    page_size = os.sysconf("SC_PAGE_SIZE")
    count = 0
    rss = 0
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text(encoding="ascii")
            fields = stat[stat.rfind(")") + 2 :].split()
            if int(fields[2]) != pgid:
                continue
            count += 1
            statm = (entry / "statm").read_text(encoding="ascii").split()
            rss += int(statm[1]) * page_size
        except (OSError, ValueError, IndexError):
            continue
    return count, rss


def _ps_group_usage(pgid: int) -> tuple[int, int] | None:
    ps = shutil.which("ps")
    if not ps:
        return None
    result = subprocess.run(
        [ps, "-eo", "pgid=,rss="],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return None
    count = 0
    rss_kib = 0
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            row_pgid, row_rss = map(int, fields)
        except ValueError:
            continue
        if row_pgid == pgid:
            count += 1
            rss_kib += row_rss
    return count, rss_kib * 1024


def _posix_group_usage(pgid: int) -> tuple[int, int] | None:
    return _linux_group_usage(pgid) or _ps_group_usage(pgid)


def _terminate_posix_group(pgid: int) -> None:
    try:
        os.killpg(pgid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError):
        return
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        try:
            os.killpg(pgid, 0)
        except (ProcessLookupError, PermissionError, OSError):
            return
        time.sleep(0.05)
    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


class _WindowsJob:
    """A kill-on-close Job Object for a signalled worker process."""

    _JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
    _JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    _JOB_OBJECT_EXTENDED_LIMIT_INFORMATION = 9
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001

    def __init__(self, limits: Limits) -> None:
        from ctypes import wintypes

        class _BasicLimit(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", wintypes.LARGE_INTEGER),
                ("PerJobUserTimeLimit", wintypes.LARGE_INTEGER),
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
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
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
                ("TotalUserTime", wintypes.LARGE_INTEGER),
                ("TotalKernelTime", wintypes.LARGE_INTEGER),
                ("ThisPeriodTotalUserTime", wintypes.LARGE_INTEGER),
                ("ThisPeriodTotalKernelTime", wintypes.LARGE_INTEGER),
                ("TotalPageFaultCount", wintypes.DWORD),
                ("TotalProcesses", wintypes.DWORD),
                ("ActiveProcesses", wintypes.DWORD),
                ("TotalTerminatedProcesses", wintypes.DWORD),
            ]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel32.QueryInformationJobObject.argtypes = [
            wintypes.HANDLE,
            ctypes.c_int,
            wintypes.LPVOID,
            wintypes.DWORD,
            wintypes.LPVOID,
        ]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            raise ContainmentError(
                f"CreateJobObjectW failed with error {ctypes.get_last_error()}"
            )
        extended = _ExtendedLimit()
        extended.BasicLimitInformation.LimitFlags = (
            self._JOB_OBJECT_LIMIT_ACTIVE_PROCESS
            | self._JOB_OBJECT_LIMIT_JOB_MEMORY
            | self._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        emergency_processes = max(
            limits.max_processes * 2, limits.max_processes + 16
        )
        extended.BasicLimitInformation.ActiveProcessLimit = emergency_processes + 1
        hard_memory_mb = max(
            limits.max_memory_mb * 2, limits.max_memory_mb + 256
        )
        extended.JobMemoryLimit = hard_memory_mb * 1024 * 1024
        if not kernel32.SetInformationJobObject(
            job,
            self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(extended),
            ctypes.sizeof(extended),
        ):
            error = ctypes.get_last_error()
            kernel32.CloseHandle(job)
            raise ContainmentError(
                f"SetInformationJobObject failed with error {error}"
            )
        self._kernel32 = kernel32
        self._handle = job
        self._BasicAccounting = _BasicAccounting
        self._ExtendedLimit = _ExtendedLimit

    def assign(self, pid: int) -> None:
        process = self._kernel32.OpenProcess(
            self._PROCESS_SET_QUOTA | self._PROCESS_TERMINATE, False, pid
        )
        if not process:
            raise ContainmentError(
                f"OpenProcess({pid}) failed with error {ctypes.get_last_error()}"
            )
        try:
            if not self._kernel32.AssignProcessToJobObject(self._handle, process):
                raise ContainmentError(
                    "AssignProcessToJobObject failed with error "
                    f"{ctypes.get_last_error()}"
                )
        finally:
            self._kernel32.CloseHandle(process)

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None

    def usage(self) -> tuple[int, int]:
        accounting = self._BasicAccounting()
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            1,
            ctypes.byref(accounting),
            ctypes.sizeof(accounting),
            None,
        ):
            raise ContainmentError(
                "QueryInformationJobObject(accounting) failed with error "
                f"{ctypes.get_last_error()}"
            )
        extended = self._ExtendedLimit()
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            self._JOB_OBJECT_EXTENDED_LIMIT_INFORMATION,
            ctypes.byref(extended),
            ctypes.sizeof(extended),
            None,
        ):
            raise ContainmentError(
                "QueryInformationJobObject(limits) failed with error "
                f"{ctypes.get_last_error()}"
            )
        return max(0, int(accounting.ActiveProcesses) - 1), int(
            extended.PeakJobMemoryUsed
        )


def _worker_command(command: Sequence[str], ready: Path) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), "_worker", str(ready), *command]


def _worker_main(argv: Sequence[str]) -> int:
    if len(argv) < 2:
        raise SystemExit("worker requires a ready path and command")
    ready = Path(argv[0])
    command = list(argv[1:])
    deadline = time.monotonic() + 30.0
    while not ready.exists():
        if time.monotonic() >= deadline:
            print("containment worker was not assigned before launch", file=sys.stderr)
            return 126
        time.sleep(0.01)
    return subprocess.run(command, check=False).returncode


def _run_contained_process(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    sandbox: Path,
    limits: Limits,
) -> int:
    """Run ``command`` in an owned process tree and return its exit code."""
    limits.validate()
    sandbox.mkdir(parents=True, exist_ok=True)
    ready = sandbox / ".containment-ready"
    ready.unlink(missing_ok=True)
    proc = subprocess.Popen(
        _worker_command(command, ready),
        cwd=str(cwd),
        env=dict(env),
        start_new_session=os.name != "nt",
    )
    job = None
    try:
        if os.name == "nt":
            try:
                job = _WindowsJob(limits)
                job.assign(proc.pid)
            except Exception:
                proc.terminate()
                proc.wait(timeout=5)
                raise
        ready.write_text("assigned\n", encoding="ascii")
        started = time.monotonic()
        next_temp_check = started
        next_usage_check = started
        while True:
            returncode = proc.poll()
            if returncode is not None:
                return returncode
            now = time.monotonic()
            violation = None
            if now - started > limits.wall_seconds:
                violation = f"wall-clock limit exceeded ({limits.wall_seconds:g}s)"
            elif now >= next_temp_check:
                temp_bytes = _tree_size(sandbox)
                if temp_bytes > limits.max_temp_mb * 1024 * 1024:
                    violation = (
                        "temporary-storage limit exceeded "
                        f"({limits.max_temp_mb} MiB)"
                    )
                next_temp_check = now + max(2.0, limits.poll_seconds)
            if violation is None and now >= next_usage_check:
                usage = (
                    job.usage()
                    if os.name == "nt" and job is not None
                    else _posix_group_usage(proc.pid)
                )
                if usage is None:
                    violation = "cannot measure POSIX process-tree resource usage"
                else:
                    process_count, rss_bytes = usage
                    if os.name != "nt":
                        process_count = max(0, process_count - 1)
                    if process_count > limits.max_processes:
                        violation = (
                            f"process-count limit exceeded ({limits.max_processes})"
                        )
                    elif rss_bytes > limits.max_memory_mb * 1024 * 1024:
                        violation = f"memory limit exceeded ({limits.max_memory_mb} MiB)"
                next_usage_check = now + limits.poll_seconds
            if violation is not None:
                print(f"[LIMIT] {violation}", file=sys.stderr)
                return 124
            time.sleep(limits.poll_seconds)
    except KeyboardInterrupt:
        print("[LIMIT] interrupted; reaping contained process tree", file=sys.stderr)
        return 130
    finally:
        if os.name == "nt":
            if job is not None:
                job.close()
            if proc.poll() is None:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        else:
            _terminate_posix_group(proc.pid)
            if proc.poll() is None:
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
        ready.unlink(missing_ok=True)


def run_contained(
    command: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    sandbox: Path,
    limits: Limits,
) -> int:
    """Run a contained tree and fail if it escapes into Windows environment state."""
    environment_snapshot = _read_registry_environment()
    try:
        result = _run_contained_process(
            command,
            cwd=cwd,
            env=env,
            sandbox=sandbox,
            limits=limits,
        )
    finally:
        changed = _registry_environment_drift(environment_snapshot)
        if changed:
            print(
                "[HOST-STATE] contained test changed persistent Windows "
                f"environment state; detected without rollback: {', '.join(changed)}",
                file=sys.stderr,
            )
    if changed:
        return 125
    return result


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] == "_worker":
        return _worker_main(args[1:])
    raise SystemExit("plugin_test_containment is an internal runner module")


if __name__ == "__main__":
    raise SystemExit(main())
