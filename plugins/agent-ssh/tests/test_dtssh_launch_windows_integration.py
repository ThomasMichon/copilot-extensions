"""Live Windows regression for the dtssh installer's detached launcher start.

Opt-in (``AGENT_SSH_DTSSH_LAUNCH_INTEGRATION=1``, Windows only): mocked
``Invoke-CimMethod`` tests prove the wiring, not the lifetime. This runs the
real ``Start-DetachedHostLauncher`` (extracted verbatim from
``install-host.ps1``) from a windowless ``pwsh`` caller inside a kill-on-close
Job that forbids breakaway -- the strictest installer context, an SSH session
or updater -- launching a synthetic watchdog instead of the dtssh launcher (no
tunnel or credentials needed). The caller also starts a bounded control child
with a plain ``Start-Process``, which must die with the Job. After the caller
exits and its Job is closed, the watchdog must keep ticking, running a console
child on every tick, with no visible window or foreground change across two
cycles. Fixture processes are tracked by pid handle and cleaned up only by
that identity.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import threading
import time
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform != "win32", reason="Windows process lifetime"),
    pytest.mark.skipif(
        os.environ.get("AGENT_SSH_DTSSH_LAUNCH_INTEGRATION") != "1",
        reason="opt-in live launch regression (AGENT_SSH_DTSSH_LAUNCH_INTEGRATION=1)",
    ),
]

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "transports" / "dtssh" / "scripts" / "install-host.ps1"
)
_SURFACED = {"pwsh.exe", "powershell.exe", "conhost.exe", "openconsole.exe",
             "windowsterminal.exe", "cmd.exe", "ping.exe"}
_SYNCHRONIZE = 0x00100000
_PROCESS_TERMINATE = 0x0001
_QUERY = 0x1000
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_CREATE_NO_WINDOW = 0x08000000
_CYCLES = 2

_WATCHDOG = r"""
param([string]$State)
$status = Join-Path $State 'status.jsonl'
$stop = Join-Path $State 'stop'
$ticks = 0
$deadline = (Get-Date).AddSeconds(90)
while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $stop)) {
    & cmd.exe /c 'exit 0'
    $ticks++
    $line = @{ pid = $PID; ticks = $ticks; cwd = (Get-Location).ProviderPath } |
        ConvertTo-Json -Compress
    Add-Content -LiteralPath $status -Value $line -Encoding utf8
    Start-Sleep -Milliseconds 100
}
"""


def _function(name: str) -> str:
    text = SCRIPT.read_text(encoding="utf-8-sig")
    start = text.index(f"function {name} {{")
    return text[start:text.index("\nfunction ", start + 1)]


def _win32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    return user32, kernel32, callback


class _Observer:
    """Records new visible top-level windows and foreground changes owned by a
    console-related process while the scenario runs."""

    def __init__(self, user32, kernel32, callback):
        self._u, self._k, self._cb = user32, kernel32, callback
        self.baseline = self._visible()
        self.foreground = user32.GetForegroundWindow()
        self.surfaced: dict[int, str] = {}
        self.focus: dict[int, str] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _visible(self):
        found = set()

        @self._cb
        def visit(hwnd, _):
            if self._u.IsWindowVisible(hwnd):
                found.add(hwnd)
            return True

        self._u.EnumWindows(visit, 0)
        return found

    def _name(self, hwnd) -> str:
        pid = wintypes.DWORD()
        self._u.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = self._k.OpenProcess(_QUERY, False, pid.value)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(size.value)
            if self._k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                name = Path(buf.value).name.lower()
                return name if name in _SURFACED else ""
            return ""
        finally:
            self._k.CloseHandle(handle)

    def _run(self):
        while not self._stop.is_set():
            for hwnd in self._visible() - self.baseline:
                if hwnd not in self.surfaced and (name := self._name(hwnd)):
                    self.surfaced[hwnd] = name
            current = self._u.GetForegroundWindow()
            # Any foreground change counts, including to an already-visible
            # terminal window that a console launch could reuse.
            if current and current != self.foreground:
                if current not in self.focus and (name := self._name(current)):
                    self.focus[current] = name
            time.sleep(0.02)

    def __enter__(self):
        self._thread.start()
        return self

    def __exit__(self, *exc):
        self._stop.set()
        self._thread.join(timeout=5)


def _latest(path: Path):
    try:
        lines = [line for line in path.read_text(encoding="utf-8-sig").splitlines() if line]
    except OSError:
        return None
    try:
        return json.loads(lines[-1]) if lines else None
    except ValueError:
        return json.loads(lines[-2]) if len(lines) > 1 else None


def _wait(predicate, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if value := predicate():
            return value
        time.sleep(0.05)
    pytest.fail(f"timed out waiting for {what}")


def _cycle(tmp_path: Path, cycle: int, pwsh: str, kernel32) -> None:
    import agent_procutil as pu

    root = tmp_path / f"cycle-{cycle}"
    install, state = root / "install", root / "state"
    install.mkdir(parents=True)
    state.mkdir()
    watchdog = root / "watchdog.ps1"
    watchdog.write_text(_WATCHDOG, encoding="utf-8")
    result = root / "result.json"
    parent = root / "caller.ps1"
    parent.write_text(
        "$ErrorActionPreference = 'Stop'\n"
        f"$InstallDir = '{install}'\n"
        + _function("Start-DetachedHostLauncher") + "\n"
        + "$launcher = Start-DetachedHostLauncher -LauncherArgs @("
        "'-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', "
        f"'-File', '\"{watchdog}\"', '\"{state}\"')\n"
        "$control = Start-Process -FilePath 'conhost.exe' -PassThru -WindowStyle Hidden "
        "-ArgumentList '--headless', 'ping.exe', '-n', '120', '127.0.0.1'\n"
        "@{ launcher = $launcher; control = $control.Id } | ConvertTo-Json -Compress | "
        f"Set-Content -LiteralPath '{result}' -Encoding utf8\n",
        encoding="utf-8",
    )
    caller, job = pu.spawn_sync_in_kill_on_close_job(
        [pwsh, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
         "-File", str(parent)],
        allow_breakaway=False, creationflags=_CREATE_NO_WINDOW,
    )
    handles: list = []
    try:
        assert job is not None, "could not place the caller in a Job"
        assert caller.wait(timeout=60) == 0
        spec = json.loads(result.read_text(encoding="utf-8-sig"))
        assert spec["launcher"], "Win32_Process.Create did not start the launcher"
        control = kernel32.OpenProcess(_SYNCHRONIZE | _QUERY | _PROCESS_TERMINATE,
                                       False, spec["control"])
        handles.append(control)
        status_path = state / "status.jsonl"
        first = _wait(lambda: (s := _latest(status_path)) and s["ticks"] >= _CYCLES and s,
                      60, "the watchdog's first ticks")
        assert os.path.samefile(first["cwd"], install)
        watchdog_handle = kernel32.OpenProcess(
            _SYNCHRONIZE | _QUERY | _PROCESS_TERMINATE, False, first["pid"])
        assert watchdog_handle, "the watchdog exited before it could be observed"
        handles.append(watchdog_handle)
        in_job = wintypes.BOOL()
        assert kernel32.IsProcessInJob(watchdog_handle, job.handle, ctypes.byref(in_job))
        assert not in_job.value, "the WMI-created watchdog must be outside the caller's Job"

        job.close()
        job = None
        assert kernel32.WaitForSingleObject(control, 10000) == _WAIT_OBJECT_0, (
            "the bounded Start-Process control child must die with the caller's Job"
        )
        assert kernel32.WaitForSingleObject(watchdog_handle, 0) == _WAIT_TIMEOUT
        at_close = _latest(status_path)["ticks"]
        _wait(lambda: (s := _latest(status_path)) and s["ticks"] >= at_close + _CYCLES,
              60, "watchdog ticks after the Job closed")

        (state / "stop").write_text("", encoding="utf-8")
        assert kernel32.WaitForSingleObject(watchdog_handle, 30000) == _WAIT_OBJECT_0
    finally:
        for handle in handles:
            if handle and kernel32.WaitForSingleObject(handle, 0) != _WAIT_OBJECT_0:
                kernel32.TerminateProcess(handle, 1)
                kernel32.WaitForSingleObject(handle, 5000)
            if handle:
                kernel32.CloseHandle(handle)
        if caller.poll() is None:
            caller.kill()
        if job is not None:
            job.close()


def test_wmi_launch_outlives_a_no_breakaway_caller_job_without_windows(tmp_path):
    pwsh = shutil.which("pwsh")
    if pwsh is None:
        pytest.skip("PowerShell 7 (pwsh) is not available")
    user32, kernel32, callback = _win32()
    with _Observer(user32, kernel32, callback) as observer:
        for cycle in range(_CYCLES):
            _cycle(tmp_path, cycle, pwsh, kernel32)
        time.sleep(0.5)
    assert observer.surfaced == {}, f"visible windows surfaced: {observer.surfaced}"
    assert observer.focus == {}, f"foreground transitions: {observer.focus}"
