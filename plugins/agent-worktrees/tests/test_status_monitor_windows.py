"""Live Windows regression for the production status-monitor launch seam."""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from agent_worktrees import __main__ as m


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


def _process_snapshot() -> dict[int, tuple[str, int]]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
    if snapshot == wintypes.HANDLE(-1).value:
        return {}
    found: dict[int, tuple[str, int]] = {}
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            found[int(entry.th32ProcessID)] = (
                entry.szExeFile,
                int(entry.th32ParentProcessID),
            )
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return found


def _window_snapshot(
    processes: dict[int, tuple[str, int]],
) -> dict[int, tuple[int, str, str, str]]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    windows: dict[int, tuple[int, str, str, str]] = {}
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def visit(hwnd, _lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        title_length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(title_length + 1)
        user32.GetWindowTextW(hwnd, title, title_length + 1)
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, class_name, len(class_name))
        process_name = processes.get(int(pid.value), ("", 0))[0]
        windows[int(hwnd)] = (
            int(pid.value),
            process_name,
            class_name.value,
            title.value,
        )
        return True

    user32.EnumWindows(callback_type(visit), 0)
    return windows


def _foreground_state(
    processes: dict[int, tuple[str, int]],
) -> tuple[int, int, str, str, str]:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    hwnd = user32.GetForegroundWindow()
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    title_length = user32.GetWindowTextLengthW(hwnd)
    title = ctypes.create_unicode_buffer(title_length + 1)
    user32.GetWindowTextW(hwnd, title, title_length + 1)
    class_name = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, class_name, len(class_name))
    process_name = processes.get(int(pid.value), ("", 0))[0]
    return int(hwnd), int(pid.value), process_name, class_name.value, title.value


def _descendants(
    root_pid: int,
    processes: dict[int, tuple[str, int]],
) -> set[int]:
    found = {root_pid}
    changed = True
    while changed:
        changed = False
        for pid, (_, parent_pid) in processes.items():
            if parent_pid in found and pid not in found:
                found.add(pid)
                changed = True
    return found


def _is_terminal_window(state: tuple[int, int, str, str, str]) -> bool:
    _, _, process_name, class_name, _ = state
    return (
        process_name.lower()
        in {"conhost.exe", "openconsole.exe", "windowsterminal.exe", "psmux.exe"}
        or class_name in {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}
    )


def _run_psmux_probe(marker: Path, psmux: str) -> int:
    for _ in range(8):
        subprocess.run(
            [psmux, "list-sessions"],
            capture_output=True,
            timeout=5,
        )
        time.sleep(0.05)
    marker.write_text("ok", encoding="utf-8")
    return 0


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console integration")
def test_status_daemon_console_root_contains_psmux_descendants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    """Production spawn keeps repeated psmux probes invisible and unfocused."""
    psmux = shutil.which("psmux")
    if psmux is None:
        pytest.skip("psmux is unavailable")

    marker = tmp_path / "complete"
    baseline_processes = _process_snapshot()
    baseline_windows = _window_snapshot(baseline_processes)
    baseline_foreground = _foreground_state(baseline_processes)
    captured: dict[str, subprocess.Popen] = {}
    real_popen = m.subprocess.Popen

    def capture_popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        captured["process"] = process
        return process

    monkeypatch.setattr(m.subprocess, "Popen", capture_popen)
    assert m._spawn_detached(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--probe",
            str(marker),
            psmux,
        ]
    )
    process = captured["process"]

    conhost_pids: set[int] = set()
    openconsole_pids: set[int] = set()
    visible_terminal_windows: set[tuple[int, int, str, str, str]] = set()
    foreground_transitions: set[tuple[int, int, str, str, str]] = set()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and not marker.exists():
        processes = _process_snapshot()
        descendants = _descendants(process.pid, processes)
        for pid in descendants:
            name = processes.get(pid, ("", 0))[0].lower()
            if name == "conhost.exe":
                conhost_pids.add(pid)
            elif name == "openconsole.exe":
                openconsole_pids.add(pid)

        for hwnd, window in _window_snapshot(processes).items():
            state = (hwnd, *window)
            if hwnd not in baseline_windows and _is_terminal_window(state):
                visible_terminal_windows.add(state)

        foreground = _foreground_state(processes)
        if foreground != baseline_foreground and _is_terminal_window(foreground):
            foreground_transitions.add(foreground)
        time.sleep(0.002)

    assert marker.exists()
    assert process.wait(timeout=5) == 0
    assert len(conhost_pids) <= 1
    assert openconsole_pids == set()
    assert visible_terminal_windows == set()
    assert foreground_transitions == set()


if __name__ == "__main__" and len(sys.argv) == 4 and sys.argv[1] == "--probe":
    raise SystemExit(_run_psmux_probe(Path(sys.argv[2]), sys.argv[3]))
