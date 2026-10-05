"""Live Windows regression for invoke-payload-runtime.ps1's background-prune
launch (see docs/patterns/windows-background-process-launch.md).

Reuses the window/process enumeration helpers from
test_status_monitor_windows.py rather than duplicating them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from test_status_monitor_windows import (
    _descendants,
    _foreground_state,
    _is_terminal_window,
    _process_snapshot,
    _window_snapshot,
)

PLUGIN = Path(__file__).resolve().parents[1]
INVOKE_PS1 = PLUGIN / "scripts" / "invoke-payload-runtime.ps1"


def _prune_functions_source() -> str:
    source = INVOKE_PS1.read_text(encoding="utf-8")
    start = source.index("function Invoke-BootTraceMaybePrune")
    end = source.index("\nfunction Write-BootTrace(", start)
    return source[start:end]


def _csc() -> str | None:
    """The legacy .NET Framework C# compiler, used only to build a genuine,
    dependency-free executable stub -- conhost's CreateProcess-based
    `--headless` launch cannot execute a `.cmd`/`.bat` script directly
    (those need a `cmd.exe` host), so the stand-in for the real interpreter
    must itself be a real PE executable."""
    candidate = shutil.which("csc.exe") or shutil.which("csc")
    if candidate:
        return candidate
    fixed = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
    return str(fixed) if fixed.is_file() else None


def _build_stub_exe(tmp_path: Path, csc: str) -> Path:
    """A trivial real executable: appends one line to the file named by the
    ``STUB_PRUNE_MARKER`` environment variable, ignoring argv entirely (the
    production dispatch's argv shape -- ``-I -m agent_worktrees
    activity-prune-worker <path> 7`` -- is irrelevant to what this test is
    proving)."""
    source = tmp_path / "stub.cs"
    source.write_text(
        "using System;\n"
        "using System.IO;\n"
        "class Stub {\n"
        "    static void Main() {\n"
        "        var marker = Environment.GetEnvironmentVariable(\"STUB_PRUNE_MARKER\");\n"
        "        if (!string.IsNullOrEmpty(marker)) { File.AppendAllText(marker, \"ran\\n\"); }\n"
        "    }\n"
        "}\n",
        encoding="utf-8",
    )
    exe = tmp_path / "stub.exe"
    result = subprocess.run(
        [csc, "/nologo", f"/out:{exe}", str(source)],
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"stub compile failed: {result.stdout}\n{result.stderr}"
    return exe


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console integration")
def test_background_prune_dispatch_launches_no_visible_window(tmp_path: Path):
    """The conhost --headless dispatch in Invoke-BootTraceMaybePrune must
    never surface a visible console window or steal foreground focus,
    across at least two dispatch cycles, launched from a real windowless
    PowerShell parent."""
    csc = _csc()
    if not csc:
        pytest.skip("no C# compiler available to build the stub executable")

    pwsh = (
        subprocess.run(
            ["where", "pwsh"], capture_output=True, text=True,
        ).stdout.splitlines()[:1]
        or [None]
    )[0]
    if not pwsh:
        pwsh = "powershell.exe"

    # A stub "python" standing in for the real interpreter: proves the
    # LAUNCH is windowless, not re-testing the real worker's prune logic
    # (already covered by the Python test suite).
    stub_marker = tmp_path / "worker-ran"
    stub_exe = _build_stub_exe(tmp_path, csc)

    harness = tmp_path / "harness.ps1"
    harness.write_text(
        _prune_functions_source() + "\n"
        f"$script:python = '{stub_exe}'\n"
        "for ($i = 0; $i -lt 2; $i++) {\n"
        f"    $log = Join-Path '{tmp_path}' \"activity-$i.jsonl\"\n"
        "    [IO.File]::WriteAllText($log, ('x' * 600000))\n"
        "    Invoke-BootTraceMaybePrune -LogPath $log\n"
        "    Start-Sleep -Milliseconds 300\n"
        "}\n",
        encoding="utf-8",
    )

    baseline_processes = _process_snapshot()
    baseline_windows = _window_snapshot(baseline_processes)
    baseline_foreground = _foreground_state(baseline_processes)

    env = dict(os.environ)
    env["STUB_PRUNE_MARKER"] = str(stub_marker)
    process = subprocess.Popen(
        [pwsh, "-NoProfile", "-NoLogo", "-File", str(harness)],
        creationflags=subprocess.CREATE_NO_WINDOW,
        env=env,
    )

    visible_terminal_windows: set[tuple[int, int, str, str, str]] = set()
    foreground_transitions: set[tuple[int, int, str, str, str]] = set()
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and process.poll() is None:
            processes = _process_snapshot()
            descendants = _descendants(process.pid, processes)
            for hwnd, window in _window_snapshot(processes).items():
                state = (hwnd, *window)
                if (
                    hwnd not in baseline_windows
                    and window[0] in descendants
                    and _is_terminal_window(state)
                ):
                    visible_terminal_windows.add(state)
            foreground = _foreground_state(processes)
            if (
                foreground != baseline_foreground
                and foreground[1] in descendants
                and _is_terminal_window(foreground)
            ):
                foreground_transitions.add(foreground)
            time.sleep(0.02)
        assert process.wait(timeout=5) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)

    assert visible_terminal_windows == set()
    assert foreground_transitions == set()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not stub_marker.exists():
        time.sleep(0.05)
    assert stub_marker.exists(), "neither dispatch cycle launched the stub worker"
    assert stub_marker.read_text(encoding="utf-8").count("ran") == 2, (
        "both dispatch cycles (two debounce windows) should have launched the worker"
    )
