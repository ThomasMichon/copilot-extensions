"""Opt-in real desktop roundtrip; never registers an elevated task or requests UAC."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import sys
import threading
import time
import warnings

import pytest

from test_windows_elevation import INVOKE, PWSH, SKILL, desktop_binding_verified, quote, run_ps

interactive_desktop = pytest.mark.skipif(
    os.name != "nt" or not PWSH
    or os.environ.get("AGENT_CONDUCT_TEST_INTERACTIVE_ELEVATION") != "1",
    reason="Explicit same-user interactive Windows desktop opt-in required",
)


def snapshot_windows():
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.IsWindowVisible.restype = wintypes.BOOL
    user.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user.GetForegroundWindow.restype = wintypes.HWND
    rows = {}
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    @callback_type
    def visit(hwnd, _):
        if user.IsWindowVisible(hwnd):
            name = ctypes.create_unicode_buffer(256)
            user.GetClassNameW(hwnd, name, len(name))
            rows[int(hwnd)] = name.value
        return True

    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    assert user.EnumWindows(visit, 0)
    return rows, int(user.GetForegroundWindow() or 0)


def desktop_identity():
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentThreadId.restype = wintypes.DWORD
    kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    user.GetProcessWindowStation.restype = wintypes.HANDLE
    user.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user.GetThreadDesktop.restype = wintypes.HANDLE
    user.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, wintypes.LPWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
    ]
    names = []
    for handle in (user.GetProcessWindowStation(), user.GetThreadDesktop(kernel.GetCurrentThreadId())):
        text = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        if not handle or not user.GetUserObjectInformationW(handle, 2, text, 512, ctypes.byref(needed)):
            return None
        names.append(text.value)
    session = wintypes.DWORD()
    if not kernel.ProcessIdToSessionId(os.getpid(), ctypes.byref(session)):
        return None
    return f"{session.value}|{names[0]}|{names[1]}"


def observe_desktop(stop, ready, observations, identities, errors):
    try:
        identities.append(desktop_identity())
        observations.append(snapshot_windows())
        ready.set()
        while not stop.wait(.02):
            observations.append(snapshot_windows())
    except Exception as error:
        errors.append(error)


def require_working_observer(errors):
    if errors:
        raise AssertionError("Desktop observer failed; window and focus qualification is unavailable") from errors[0]


@pytest.mark.parametrize("successful_snapshots", [0, 1])
def test_observer_failure_cannot_qualify_empty_or_partial_observations(monkeypatch, successful_snapshots):
    stop, ready = threading.Event(), threading.Event()
    observations, identities, errors = [], [], []
    failure = OSError("synthetic desktop observation failure")

    def snapshot():
        if len(observations) == successful_snapshots:
            raise failure
        return {1: "synthetic-window"}, 1

    monkeypatch.setattr(sys.modules[__name__], "desktop_identity", lambda: "1|station|desktop")
    monkeypatch.setattr(sys.modules[__name__], "snapshot_windows", snapshot)
    watcher = threading.Thread(target=observe_desktop, args=(stop, ready, observations, identities, errors))
    watcher.start()
    watcher.join(timeout=2)
    assert not watcher.is_alive()
    assert ready.is_set() is bool(successful_snapshots)
    assert len(observations) == successful_snapshots
    with pytest.raises(AssertionError, match="Desktop observer failed") as raised:
        require_working_observer(errors)
    assert raised.value.__cause__ is failure


def test_observer_takes_first_snapshot_before_signaling_ready(monkeypatch):
    stop, ready = threading.Event(), threading.Event()
    observations, identities, errors = [], [], []

    def snapshot():
        assert not ready.is_set()
        stop.set()
        return {1: "synthetic-window"}, 1

    monkeypatch.setattr(sys.modules[__name__], "desktop_identity", lambda: "1|station|desktop")
    monkeypatch.setattr(sys.modules[__name__], "snapshot_windows", snapshot)
    observe_desktop(stop, ready, observations, identities, errors)
    require_working_observer(errors)
    assert ready.is_set()
    assert observations == [({1: "synthetic-window"}, 1)]
    assert identities == ["1|station|desktop"]


@pytest.fixture
def clean_probe_tasks(tmp_path):
    yield
    for request in tmp_path.glob("private/elevation-*/request.json"):
        data = json.loads(request.read_text(encoding="utf-8"))
        assert data["interactiveProbe"] is True
        assert data["taskName"] == "AgentConduct-Elevation-" + data["runId"]
        result = run_ps(
            "-Command",
            "$s = New-Object -ComObject Schedule.Service; $s.Connect(); $f = $s.GetFolder('\\'); "
            f"$task = @($f.GetTasks(1) | Where-Object Name -eq {quote(data['taskName'])}); "
            "if ($task.Count) { "
            "if ($task[0].Definition.Principal.RunLevel -ne 0) { throw 'Probe task privilege changed' }; "
            "$name = $task[0].Definition.Principal.UserId; "
            "$owner = if ($name.StartsWith('S-1-')) { $name } else { "
            "[Security.Principal.NTAccount]::new($name).Translate([Security.Principal.SecurityIdentifier]).Value }; "
            f"if ($owner -ne {quote(data['ownerSid'])}) {{ throw 'Probe task owner changed' }}; "
            "if ($task[0].GetInstances(0).Count) { $task[0].Stop(0) }; "
            "$until = [DateTime]::UtcNow.AddSeconds(3); "
            "while ($task[0].GetInstances(0).Count -and [DateTime]::UtcNow -lt $until) { Start-Sleep -Milliseconds 50 }; "
            "if ($task[0].GetInstances(0).Count) { throw 'Probe task cleanup unconfirmed' }; "
            f"$f.DeleteTask({quote(data['taskName'])}, 0) }}",
        )
        assert result.returncode == 0, result.stdout + result.stderr


@pytest.fixture
def private_directory(tmp_path):
    path = tmp_path / "private"
    result = run_ps(
        "-Command",
        "$acl = [Security.AccessControl.DirectorySecurity]::new(); "
        "$acl.SetAccessRuleProtection($true, $false); "
        "$sid = [Security.Principal.WindowsIdentity]::GetCurrent().User; "
        "foreach ($identity in @($sid, [Security.Principal.SecurityIdentifier]::new('S-1-5-18'), "
        "[Security.Principal.SecurityIdentifier]::new('S-1-5-32-544'))) { "
        "$acl.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new("
        "$identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')) }; "
        f"[IO.FileSystemAclExtensions]::Create([IO.DirectoryInfo]::new({quote(path)}), $acl)",
    )
    assert result.returncode == 0, result.stderr
    return path


@interactive_desktop
@pytest.mark.parametrize("cycle", [1, 2])
def test_interactive_probe_has_real_terminal_cleanup_and_no_console(private_directory, clean_probe_tasks, cycle):
    log = private_directory / f"interactive-{cycle}.log"
    baseline, foreground = snapshot_windows()
    baseline_desktop = desktop_identity()
    stop = threading.Event()
    ready = threading.Event()
    observations = []
    observer_desktop = []
    observer_errors = []
    watcher = threading.Thread(
        target=observe_desktop, args=(stop, ready, observations, observer_desktop, observer_errors),
    )
    watcher.start()
    try:
        initialized = ready.wait(2)
        require_working_observer(observer_errors)
        assert initialized, "Desktop observer did not start"
        result = subprocess.run(
            [
                PWSH, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(INVOKE),
                "-ScriptPath", str(SKILL / "assets" / "probe.ps1"),
                "-LogPath", str(log), "-InteractiveProbe",
                "-ConsentTimeoutSeconds", "30", "-TimeoutSeconds", "10",
            ],
            capture_output=True, text=True, encoding="utf-8", timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        time.sleep(.1)
    except subprocess.TimeoutExpired as error:
        tail = log.read_text(encoding="utf-8")[-4000:] if log.exists() else "log not created"
        raise AssertionError(f"Interactive probe command exceeded 25 seconds; retained log tail:\n{tail}") from error
    finally:
        stop.set()
        watcher.join(timeout=2)
    assert not watcher.is_alive()
    require_working_observer(observer_errors)
    assert observations, "Desktop observer collected no snapshots"
    assert result.returncode == 0, result.stdout + result.stderr
    rows = log.read_text(encoding="utf-8").splitlines()
    terminal = json.loads(rows[-1].removeprefix("AGENT_ELEVATION_END "))
    assert terminal["state"] == "probe" and terminal["exitCode"] == 0
    assert "agent-conduct-elevation stderr probe" in rows
    run_id = terminal["runId"]
    request, = private_directory.glob("elevation-*/request.json")
    receipt = json.loads(request.read_text(encoding="utf-8"))
    assert receipt["runId"] == run_id
    check = run_ps(
        "-Command",
        "$s = New-Object -ComObject Schedule.Service; $s.Connect(); "
        f"$found = @($s.GetFolder('\\').GetTasks(1) | Where-Object Name -eq {quote(receipt['taskName'])}); "
        "if ($found.Count) { throw 'Owned probe task remained registered' }",
    )
    assert check.returncode == 0, check.stderr
    resumed = run_ps("-File", str(INVOKE), "-WaitFor", str(request))
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    assert json.loads(resumed.stdout)["runId"] == run_id
    started = json.loads((request.parent / "worker-started.json").read_text(encoding="utf-8"))
    assert started["runId"] == run_id
    bound = desktop_binding_verified(baseline_desktop, observer_desktop[0], started.get("desktopIdentity"))
    if not bound:
        warnings.warn(
            "Observer is not bound to the selected session/desktop; window and focus qualification "
            "are unavailable. Use a bound interactive observer or an operator witness.",
            RuntimeWarning,
        )
        return
    console_classes = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
    assert not [
        (hwnd, kind) for windows, _ in observations
        for hwnd, kind in windows.items()
        if hwnd not in baseline and kind in console_classes
    ], "An unexpected visible console appeared on the selected desktop"
    if foreground:
        assert all(active == foreground for _, active in observations), (
            "Foreground changed during the opt-in probe; run on a quiescent desktop"
        )
    else:
        warnings.warn(
            "Foreground is unobservable to this caller; this run does not qualify focus behavior. "
            "Use an interactive observer or an operator witness.",
            RuntimeWarning,
        )
