"""Opt-in real desktop roundtrip; never registers an elevated task or requests UAC."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
import subprocess
import threading
import time
import warnings

import pytest

from test_windows_elevation import INVOKE, PWSH, SKILL, quote, run_ps

pytestmark = pytest.mark.skipif(
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


@pytest.mark.parametrize("cycle", [1, 2])
def test_interactive_probe_has_real_terminal_cleanup_and_no_console(private_directory, clean_probe_tasks, cycle):
    log = private_directory / f"interactive-{cycle}.log"
    baseline, foreground = snapshot_windows()
    assert baseline, "Visible-window observation is unavailable to this caller"
    stop = threading.Event()
    observations = []

    def observe():
        while not stop.is_set():
            observations.append(snapshot_windows())
            stop.wait(.02)

    watcher = threading.Thread(target=observe)
    watcher.start()
    try:
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
    console_classes = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
    assert not [
        (hwnd, kind) for windows, _ in observations
        for hwnd, kind in windows.items()
        if hwnd not in baseline and kind in console_classes
    ], "An unexpected visible console appeared during the opt-in isolated probe"
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
