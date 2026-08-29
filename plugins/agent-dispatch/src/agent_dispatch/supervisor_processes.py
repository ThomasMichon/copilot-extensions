"""Retire Windows supervisor service process generations.

The Windows interactive-service launcher uses ``conhost --headless`` and
PowerShell, so neither Scheduled Tasks nor the HKCU Run registration owns the
whole live process tree.  A runtime update must therefore reconcile the process
inventory explicitly before starting the current launcher.
"""

from __future__ import annotations

import json
import logging
import ntpath
import os
import shlex
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from .procutil import no_window_kwargs

log = logging.getLogger(__name__)

_ENUM_TIMEOUT_S = 15.0
_AGENT_DISPATCH_NAMES = ("agent_dispatch", "agent-dispatch")


@dataclass(frozen=True)
class WindowsProcess:
    """The process metadata needed to identify a supervisor generation."""

    pid: int
    parent_pid: int
    executable: str = ""
    command_line: str = ""


@dataclass
class SupervisorRetireResult:
    """Outcome of one best-effort supervisor-generation retirement."""

    selected: list[int] = field(default_factory=list)
    retired: list[int] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _tokens(command_line: str) -> list[str]:
    if not command_line:
        return []
    try:
        return shlex.split(command_line, posix=False)
    except ValueError:
        return command_line.split()


def _basename(token: str) -> str:
    return token.strip('"').rsplit("/", 1)[-1].rsplit("\\", 1)[-1]


def _agent_dispatch_argv(command_line: str) -> list[str]:
    tokens = _tokens(command_line)
    for index, token in enumerate(tokens):
        if _basename(token) in _AGENT_DISPATCH_NAMES:
            return tokens[index + 1 :]
    return []


def _under_install_root(path: str, install_dir: str | Path) -> bool:
    if not path:
        return False
    try:
        candidate = ntpath.normcase(ntpath.abspath(path.strip('"')))
        root = ntpath.normcase(ntpath.abspath(str(install_dir)))
        return ntpath.commonpath([candidate, root]) == root
    except (OSError, ValueError):
        return False


def _is_supervisor_runtime(process: WindowsProcess, install_dir: str | Path) -> bool:
    if not _under_install_root(process.executable, install_dir):
        return False
    argv = _agent_dispatch_argv(process.command_line)
    if not argv:
        return False
    if argv[0] != "supervise":
        return False
    return len(argv) == 1 or argv[1] == "serve" or argv[1].startswith("-")


def _is_materialized_supervisor_child(
    process: WindowsProcess, install_dir: str | Path
) -> bool:
    """True for a registrar child identified by its supervisor-owned spec path."""

    if not _under_install_root(process.executable, install_dir):
        return False
    argv = _agent_dispatch_argv(process.command_line)
    if not argv:
        return False
    producer = (
        argv[:2] in (["emitter", "serve"], ["schedule", "serve"])
        or argv[0] == "webhook"
    )
    if not producer:
        return False
    spec_root = ntpath.normcase(
        ntpath.abspath(ntpath.join(str(install_dir), "run", "supervisor"))
    ).rstrip("\\") + "\\"
    command_line = ntpath.normcase(process.command_line.replace('"', ""))
    return spec_root in command_line


def _is_supervisor_wrapper(process: WindowsProcess, install_dir: str | Path) -> bool:
    launcher = ntpath.normcase(
        ntpath.abspath(ntpath.join(str(install_dir), "supervise-service.ps1"))
    )
    command_line = ntpath.normcase(process.command_line.replace('"', ""))
    return launcher in command_line


def select_supervisor_generation_pids(
    processes: Iterable[WindowsProcess],
    install_dir: str | Path,
) -> list[int]:
    """Select every wrapper/supervisor root and descendant, roots first.

    Producer commands are supported standalone surfaces and are selected only
    when ancestry or their supervisor-owned materialized spec proves ownership.
    """

    records = {process.pid: process for process in processes if process.pid > 0}
    children: dict[int, list[int]] = {}
    for process in records.values():
        children.setdefault(process.parent_pid, []).append(process.pid)

    selected = {
        process.pid
        for process in records.values()
        if _is_supervisor_wrapper(process, install_dir)
        or _is_supervisor_runtime(process, install_dir)
        or _is_materialized_supervisor_child(process, install_dir)
    }
    stack = list(selected)
    while stack:
        parent = stack.pop()
        for child in children.get(parent, []):
            if child not in selected:
                selected.add(child)
                stack.append(child)

    def depth(pid: int) -> int:
        seen: set[int] = set()
        current = records.get(pid)
        value = 0
        while current is not None and current.parent_pid in records:
            if current.parent_pid in seen:
                break
            seen.add(current.parent_pid)
            value += 1
            current = records.get(current.parent_pid)
        return value

    return sorted(selected, key=lambda pid: (depth(pid), pid))


def parse_windows_process_json(text: str) -> list[WindowsProcess]:
    """Parse the compact JSON emitted by the Win32_Process inventory query."""

    if not text.strip():
        return []
    payload = json.loads(text)
    rows = payload if isinstance(payload, list) else [payload]
    result: list[WindowsProcess] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            pid = int(row.get("ProcessId") or 0)
            parent_pid = int(row.get("ParentProcessId") or 0)
        except (TypeError, ValueError):
            continue
        if pid <= 0:
            continue
        result.append(
            WindowsProcess(
                pid=pid,
                parent_pid=parent_pid,
                executable=str(row.get("ExecutablePath") or ""),
                command_line=str(row.get("CommandLine") or ""),
            )
        )
    return result


def iter_windows_processes() -> list[WindowsProcess]:
    """Read the Windows process inventory (best-effort)."""

    script = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,ExecutablePath,CommandLine | "
        "ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=_ENUM_TIMEOUT_S,
        check=False,
        **no_window_kwargs(),
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or "Win32_Process query failed").strip())
    return parse_windows_process_json(completed.stdout or "")


def terminate_process(pid: int) -> bool:
    """Force-terminate one Windows process; already-gone is success."""

    try:
        os.kill(pid, signal.SIGTERM)
        return True
    except ProcessLookupError:
        return True
    except (OSError, PermissionError):
        log.debug("could not terminate supervisor process pid=%s", pid, exc_info=True)
        return False


def retire_windows_supervisor_generations(
    install_dir: str | Path,
    *,
    list_processes: Callable[[], list[WindowsProcess]] = iter_windows_processes,
    terminate: Callable[[int], bool] = terminate_process,
    platform_name: str | None = None,
) -> SupervisorRetireResult:
    """Retire every installed supervisor wrapper/master/child generation."""

    result = SupervisorRetireResult()
    if (platform_name or os.name) != "nt":
        return result
    attempted: set[int] = set()
    for _ in range(3):
        try:
            processes = list_processes()
        except Exception as exc:
            result.errors.append(f"process enumeration failed: {exc}")
            break
        selected = [
            pid
            for pid in select_supervisor_generation_pids(processes, install_dir)
            if pid not in attempted
        ]
        if not selected:
            break
        result.selected.extend(selected)
        for pid in selected:
            attempted.add(pid)
            if pid == os.getpid():
                continue
            if terminate(pid):
                result.retired.append(pid)
            else:
                result.errors.append(f"terminate pid={pid} failed")
    return result
