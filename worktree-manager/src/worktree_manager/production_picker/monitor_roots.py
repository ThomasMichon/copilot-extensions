"""Manager-owned Picker liveness roots for the resident status monitor.

This ports ``agent_worktrees.monitor_roots`` into Worktree Manager so the
production Picker can own its own monitor-root lifecycle without calling back
into underscore-prefixed engine helpers. The on-disk schema intentionally stays
compatible with the engine-side reader: ``status-monitor-roots.d/picker-*.json``
containing the same provable-liveness payload fields.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Callable

from agent_procutil import windowless_daemon_kwargs

from . import context
from .. import agent_plugin_runtime
from .. import engine_client
from ..mux_daemon_process import scrub_session_credentials

_PICKER_HEARTBEAT_INTERVAL = 10.0
_PICKER_STALE_AFTER = 30.0
_LOCK_SCHEMA = 1


def _engine_runtime_home() -> Path:
    slot = agent_plugin_runtime.resolve_installed_plugin_slot("agent-worktrees")
    if slot is not None:
        return slot.parent.parent
    return agent_plugin_runtime.legacy_plugin_root("agent-worktrees")


def _roots_dir() -> Path:
    return _engine_runtime_home() / "status-monitor-roots.d"


def _status_monitor_lock_path() -> Path:
    return _engine_runtime_home() / "status-monitor.lock"


def _current_engine_prefix() -> str | None:
    slot = agent_plugin_runtime.resolve_installed_plugin_slot("agent-worktrees")
    if slot is None:
        return None
    return os.path.realpath(str(slot))


def status_monitor_enabled() -> bool:
    return os.environ.get("AGENT_WORKTREES_STATUS_MONITOR", "").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _caller_has_mux() -> bool:
    return bool(shutil.which("psmux") or shutil.which("tmux"))


def _monitor_lock_satisfies_current_runtime(data: dict | None) -> bool:
    if not (_lock_is_live(data) and isinstance(data, dict)):
        return False
    if data.get("mux") is False and _caller_has_mux():
        return False
    current_prefix = _current_engine_prefix()
    other_prefix = data.get("prefix")
    if current_prefix and isinstance(other_prefix, str) and other_prefix:
        return os.path.realpath(other_prefix) == os.path.realpath(current_prefix)
    return True


def _spawn_status_monitor(base: list[str]) -> bool:
    env = scrub_session_credentials(engine_client._engine_environment())
    for key in ("COPILOT_AGENT_SESSION_ID", "WORKTREE_PROJECT", engine_client.ENGINE_ARGV_ENV):
        env.pop(key, None)
    kwargs: dict = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(_engine_runtime_home()),
        "env": env,
    }
    kwargs.update(windowless_daemon_kwargs(breakaway=True))
    try:
        subprocess.Popen([*base, "status-monitor"], **kwargs)  # noqa: S603
        return True
    except Exception:
        return False


def ensure_status_monitor_running() -> bool:
    if not status_monitor_enabled():
        return False
    if _monitor_lock_satisfies_current_runtime(_read_lock(_status_monitor_lock_path())):
        return True
    base = engine_client.engine_base_command()
    if not base:
        return False
    return _spawn_status_monitor(base)


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if handle:
            kernel32.CloseHandle(handle)
            return True
        error = ctypes.get_last_error()
        return error == 5
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def _process_start_time(pid: int) -> str | None:
    if pid <= 0:
        return None
    if os.name == "nt":
        return _start_time_windows(pid)
    return _start_time_posix(pid)


def _start_time_windows(pid: int) -> str | None:
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.GetProcessTimes.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel32.GetProcessTimes.restype = wintypes.BOOL

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        creation = wintypes.FILETIME()
        exit_ = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        ok = kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_),
            ctypes.byref(kernel),
            ctypes.byref(user),
        )
        if not ok:
            return None
        ticks = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
        return str(ticks)
    except OSError:
        return None
    finally:
        kernel32.CloseHandle(handle)


def _start_time_posix(pid: int) -> str | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(errors="ignore")
    except OSError:
        return None
    rparen = stat.rfind(")")
    if rparen == -1:
        return None
    rest = stat[rparen + 1 :].split()
    if len(rest) < 20:
        return None
    token = rest[19]
    return token if token.isdigit() else None


def _write_lock(path: Path, *, pid: int | None = None, extra: dict | None = None) -> bool:
    owner = os.getpid() if pid is None else pid
    payload: dict = {
        "schema": _LOCK_SCHEMA,
        "pid": owner,
        "start_time": _process_start_time(owner),
        "created_at": time.time(),
    }
    if extra:
        payload.update(extra)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle)
            os.replace(tmp, str(path))
        except OSError:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False
        return True
    except OSError:
        return False


def _read_lock(path: Path) -> dict | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        data = json.loads(text)
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def _lock_is_live(data: dict | None) -> bool:
    if not isinstance(data, dict):
        return False
    pid = data.get("pid")
    if not isinstance(pid, int) or not _pid_alive(pid):
        return False
    recorded = data.get("start_time")
    if not recorded:
        return True
    current = _process_start_time(pid)
    if current is None:
        return True
    return str(recorded) == str(current)


def _remove_lock(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


class PickerHeartbeat:
    """Provable-liveness Picker registration refreshed while its TUI is open."""

    def __init__(
        self,
        project: str,
        *,
        interval: float = _PICKER_HEARTBEAT_INTERVAL,
        ensure_monitor: Callable[[], bool] | None = None,
    ) -> None:
        self.project = project
        self.interval = interval
        token = uuid.uuid4().hex[:12]
        self.path = _roots_dir() / f"picker-{os.getpid()}-{token}.json"
        self._ensure_monitor = ensure_monitor
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _write(self) -> bool:
        return _write_lock(self.path, extra={"kind": "picker", "project": self.project})

    def _ensure(self) -> None:
        if self._ensure_monitor is None:
            return
        try:
            self._ensure_monitor()
        except Exception:
            pass

    def start(self) -> bool:
        if not self._write():
            return False
        self._ensure()
        self._thread = threading.Thread(
            target=self._run,
            name="status-monitor-picker-heartbeat",
            daemon=True,
        )
        self._thread.start()
        return True

    def _run(self) -> None:
        try:
            while not self._stop.wait(self.interval):
                self._write()
                self._ensure()
        finally:
            if self._stop.is_set():
                _remove_lock(self.path)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        _remove_lock(self.path)


def live_picker_projects(*, now: float | None = None, stale_after: float = _PICKER_STALE_AFTER) -> set[str]:
    now = time.time() if now is None else now
    projects: set[str] = set()
    try:
        paths = list(_roots_dir().glob("picker-*.json"))
    except OSError:
        return projects
    for path in paths:
        data = _read_lock(path)
        project = data.get("project") if isinstance(data, dict) else None
        created_at = data.get("created_at") if isinstance(data, dict) else None
        try:
            fresh = now - float(created_at) <= stale_after
        except (TypeError, ValueError):
            fresh = False
        if (
            isinstance(data, dict)
            and data.get("kind") == "picker"
            and isinstance(project, str)
            and project
            and fresh
            and _lock_is_live(data)
        ):
            projects.add(project)
            continue
        _remove_lock(path)
    return projects


def start_picker_monitor_root(project: str | None = None):
    if not status_monitor_enabled():
        return None
    try:
        root = PickerHeartbeat(project or context.project(), ensure_monitor=ensure_status_monitor_running)
        if not root.start():
            return None
        return root
    except Exception:
        return None
