"""Live Windows regression for :func:`agent_procutil.spawn_daemon`.

Per ``docs/patterns/windows-background-process-launch.md`` § Review and
validation, mocked ``creationflags`` assertions prove wiring, not behavior.
This drives the real ``spawn_daemon`` from a real windowless parent
(``pythonw.exe``) that itself runs inside a kill-on-close Job, the way an
installer, updater or hook launches a resident daemon. The daemon is a real
console-subsystem ``python.exe``; in ``console_children`` mode it also starts
a real console child on every periodic tick. Each scenario runs two full
launch/stop cycles while the pytest side observes actual Win32 top-level
window and foreground state (the same EnumWindows/foreground observer as
``plugins/agent-mcp/tests/test_windows_launch_integration.py``).

Lifetime: the parent exits and its Job is then closed (parent exit alone is
insufficient). Outside contained test mode the daemon must have broken away
and keep ticking; in contained test mode (``COPILOT_EXTENSIONS_TEST_CONTAINED=1``)
breakaway is deliberately suppressed, so the daemon must instead die with the
caller's Job.

Windows-only; skipped everywhere else.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

import agent_procutil as pu

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows console integration")

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

_SURFACED_PROCESS_NAMES = {
    "python.exe", "pythonw.exe", "powershell.exe", "conhost.exe",
    "openconsole.exe", "windowsterminal.exe", "cmd.exe",
}
_SYNCHRONIZE = 0x00100000
_PROCESS_TERMINATE = 0x0001
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_CYCLES = 2

# Runs under a windowless pythonw.exe inside a kill-on-close Job: launches the
# daemon through spawn_daemon, records its pid, and exits without waiting.
_PARENT_SCRIPT = """
import json, sys, traceback
spec = json.loads(sys.argv[1])
try:
    import agent_procutil
    proc = agent_procutil.spawn_daemon(
        spec["argv"],
        cwd=spec["cwd"],
        log_path=spec["log_path"],
        console_children=spec["console_children"],
        display="spawn-daemon-probe",
    )
    result = {"pid": proc.pid}
except agent_procutil.DaemonSpawnError as exc:
    result = {"daemon_spawn_error": str(exc)}
except BaseException:
    result = {"error": traceback.format_exc()}
with open(spec["result"], "w", encoding="utf-8") as fh:
    json.dump(result, fh)
"""

# The daemon: a console-subsystem python.exe that ticks periodically, writing
# its pid/cwd/tick count, optionally running a real console child per tick,
# until the stop file appears (or a safety deadline passes).
_DAEMON_SCRIPT = """
import json, os, subprocess, sys, time
state, console_children = sys.argv[1], sys.argv[2] == "1"
status = os.path.join(state, "status.jsonl")
stop = os.path.join(state, "stop")
ticks = 0
deadline = time.monotonic() + 90
while time.monotonic() < deadline and not os.path.exists(stop):
    if console_children:
        subprocess.run([sys.executable, "-c", "import time; time.sleep(0.1)"], check=True)
    ticks += 1
    # Append-only, so the observing reader never blocks a rename.
    with open(status, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"pid": os.getpid(), "cwd": os.getcwd(), "ticks": ticks}) + "\\n")
    time.sleep(0.1)
print("daemon stopped after", ticks, "ticks", flush=True)
"""


def _configure_win32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    return user32, kernel32, callback_type


def _visible_windows(user32, callback_type):
    windows = set()

    @callback_type
    def visit(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            windows.add(hwnd)
        return True

    user32.EnumWindows(visit, 0)
    return windows


def _process_name(user32, kernel32, hwnd) -> str:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
    if not handle:
        return ""
    try:
        length = wintypes.DWORD(32768)
        name = ctypes.create_unicode_buffer(length.value)
        if kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
            return Path(name.value).name.lower()
        return ""
    finally:
        kernel32.CloseHandle(handle)


class _WindowObserver:
    """Polls visible top-level windows and foreground changes in the background."""

    def __init__(self, user32, kernel32, callback_type):
        self._user32, self._kernel32, self._callback_type = user32, kernel32, callback_type
        self.baseline = _visible_windows(user32, callback_type)
        self.foreground = user32.GetForegroundWindow()
        self.surfaced: dict[int, str] = {}
        self.focus_changes: dict[int, str] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _attributable(self, hwnd) -> str:
        name = _process_name(self._user32, self._kernel32, hwnd)
        return name if name in _SURFACED_PROCESS_NAMES else ""

    def _run(self):
        while not self._stop.is_set():
            for hwnd in _visible_windows(self._user32, self._callback_type) - self.baseline:
                if hwnd not in self.surfaced and (name := self._attributable(hwnd)):
                    self.surfaced[hwnd] = name
            current = self._user32.GetForegroundWindow()
            if current and current != self.foreground and current not in self.baseline:
                if current not in self.focus_changes and (name := self._attributable(current)):
                    self.focus_changes[current] = name
            time.sleep(0.02)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)


def _wait_for(predicate, timeout: float, what: str, log_path: Path):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.05)
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = "<no daemon log>"
    pytest.fail(f"timed out waiting for {what}; daemon log:\n{log_text}")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _latest_status(path: Path):
    try:
        lines = path.read_text(encoding="utf-8").split("\n")
    except OSError:
        return None
    complete = [line for line in lines[:-1] if line.strip()]
    return json.loads(complete[-1]) if complete else None


def _console_python() -> str:
    """A real console-subsystem interpreter that does not re-exec itself."""
    base = getattr(sys, "_base_executable", "") or sys.executable
    return base if os.path.isfile(base) else sys.executable


def _run_cycle(tmp_path: Path, cycle: int, console_children: bool, kernel32) -> None:
    root = tmp_path / f"cycle-{cycle}"
    workdir = root / "home"
    state = root / "state"
    workdir.mkdir(parents=True)
    state.mkdir()
    daemon_script = root / "daemon.py"
    daemon_script.write_text(_DAEMON_SCRIPT, encoding="utf-8")
    parent_script = root / "parent.py"
    parent_script.write_text(_PARENT_SCRIPT, encoding="utf-8")
    result_path = root / "parent-result.json"
    log_path = root / "logs" / "daemon.log"
    spec = {
        "argv": [_console_python(), str(daemon_script), str(state),
                 "1" if console_children else "0"],
        "cwd": str(workdir),
        "log_path": str(log_path),
        "console_children": console_children,
        "result": str(result_path),
    }

    env = os.environ.copy()
    env.pop("__PYVENV_LAUNCHER__", None)
    src = str(Path(pu.__file__).resolve().parents[1])
    env["PYTHONPATH"] = src + os.pathsep + env.get("PYTHONPATH", "")
    pythonw = pu.windowless_python(sys.executable)
    if not pythonw.lower().endswith("pythonw.exe"):
        pytest.skip("pythonw.exe is unavailable on this interpreter")

    parent, job = pu.spawn_sync_in_kill_on_close_job(
        [pythonw, str(parent_script), json.dumps(spec)], env=env,
    )
    daemon_handle = None
    try:
        assert job is not None, "could not place the windowless parent in a Job"
        assert parent.wait(timeout=30) == 0
        result = _read_json(result_path)
        assert result is not None, "parent did not record a result"
        if "daemon_spawn_error" in result:
            pytest.skip(
                "this runner's own Job forbids breakaway: " + result["daemon_spawn_error"]
            )
        assert "pid" in result, result.get("error")
        daemon_pid = result["pid"]
        daemon_handle = kernel32.OpenProcess(
            _SYNCHRONIZE | _PROCESS_QUERY_LIMITED_INFORMATION | _PROCESS_TERMINATE,
            False, daemon_pid,
        )
        assert daemon_handle, f"daemon {daemon_pid} exited before it could be observed"

        status_path = state / "status.jsonl"
        status = _wait_for(
            lambda: (s := _latest_status(status_path)) and s["ticks"] >= _CYCLES and s,
            30, "the daemon's first periodic ticks", log_path,
        )
        assert status["pid"] == daemon_pid
        assert os.path.samefile(status["cwd"], workdir)

        in_job = wintypes.BOOL()
        assert kernel32.IsProcessInJob(daemon_handle, job.handle, ctypes.byref(in_job))
        contained = pu.contained_test_mode()
        assert bool(in_job.value) is contained

        # The parent has already exited; now close the caller's containment.
        job.close()
        job = None
        if contained:
            assert kernel32.WaitForSingleObject(daemon_handle, 5000) == _WAIT_OBJECT_0, (
                "a contained-mode daemon must die with its caller's Job"
            )
            return

        assert kernel32.WaitForSingleObject(daemon_handle, 0) == _WAIT_TIMEOUT, (
            "the daemon must survive its caller's kill-on-close Job"
        )
        ticks_at_close = _latest_status(status_path)["ticks"]
        _wait_for(
            lambda: (s := _latest_status(status_path)) and s["ticks"] >= ticks_at_close + _CYCLES,
            30, "post-Job periodic ticks", log_path,
        )
        assert kernel32.WaitForSingleObject(daemon_handle, 0) == _WAIT_TIMEOUT

        (state / "stop").write_text("", encoding="utf-8")
        assert kernel32.WaitForSingleObject(daemon_handle, 30000) == _WAIT_OBJECT_0
        code = wintypes.DWORD()
        assert kernel32.GetExitCodeProcess(daemon_handle, ctypes.byref(code))
        assert code.value == 0
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        assert "spawn_daemon cwd=" in log_text
        assert "display=spawn-daemon-probe" in log_text
        assert "daemon stopped after" in log_text
    finally:
        if daemon_handle:
            if kernel32.WaitForSingleObject(daemon_handle, 0) != _WAIT_OBJECT_0:
                kernel32.TerminateProcess(daemon_handle, 1)
                kernel32.WaitForSingleObject(daemon_handle, 5000)
            kernel32.CloseHandle(daemon_handle)
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=5)
        if job is not None:
            job.close()


@pytest.mark.parametrize("console_children", [False, True], ids=["detached", "console-children"])
def test_spawn_daemon_is_headless_and_outlives_caller_job(tmp_path, console_children):
    user32, kernel32, callback_type = _configure_win32()
    with _WindowObserver(user32, kernel32, callback_type) as observer:
        for cycle in range(_CYCLES):
            _run_cycle(tmp_path, cycle, console_children, kernel32)
        time.sleep(0.5)

    assert observer.surfaced == {}, f"visible windows surfaced: {observer.surfaced}"
    assert observer.focus_changes == {}, f"foreground transitions: {observer.focus_changes}"
