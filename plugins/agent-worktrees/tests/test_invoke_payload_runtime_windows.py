"""Live Windows regression for invoke-payload-runtime.ps1's background-prune
launch (see docs/patterns/windows-background-process-launch.md).

Reuses the window/process enumeration helpers from
test_status_monitor_windows.py rather than duplicating them.
"""

from __future__ import annotations

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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console integration")
def test_background_prune_dispatch_launches_no_visible_window(tmp_path: Path):
    """The conhost --headless dispatch in Invoke-BootTraceMaybePrune must
    never surface a visible console window or steal foreground focus,
    across at least two dispatch cycles, launched from a real windowless
    PowerShell parent."""
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
    stub_python = tmp_path / "stub-python.cmd"
    stub_python.write_text(
        f'@echo off\r\necho ran >> "{stub_marker}"\r\n', encoding="utf-8",
    )

    harness = tmp_path / "harness.ps1"
    harness.write_text(
        _prune_functions_source() + "\n"
        f"$script:python = '{stub_python}'\n"
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

    process = subprocess.Popen(
        [pwsh, "-NoProfile", "-NoLogo", "-File", str(harness)],
        creationflags=subprocess.CREATE_NO_WINDOW,
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
