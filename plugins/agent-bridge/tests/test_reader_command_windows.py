"""Native launch visibility from a GUI-subsystem parent, across two reads."""

from __future__ import annotations

import ctypes
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest
from agent_procutil import no_window_kwargs, windowless_python


@pytest.mark.skipif(sys.platform != "win32", reason="native Windows launch contract")
def test_owned_reader_is_invisible_from_windowless_parent(tmp_path: Path) -> None:
    from ctypes import wintypes

    import psutil

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user32.EnumWindows.restype = wintypes.BOOL
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowVisible.restype = wintypes.BOOL
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.GetForegroundWindow.restype = wintypes.HWND

    def visible_windows() -> dict[int, int]:
        found: dict[int, int] = {}

        @callback_type
        def visit(hwnd: int, parameter: int) -> bool:
            if user32.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                found[int(hwnd)] = pid.value
            return True

        assert user32.EnumWindows(visit, 0)
        return found

    report = tmp_path / "report.json"
    pythonw = windowless_python(sys.executable)
    console_python = str(Path(pythonw).with_name("python.exe"))
    script = (
        f"import sys;sys.path[:0]={sys.path!r};"
        "import json,pathlib;"
        "from agent_bridge.reader_command import OwnedReaderCommand;"
        f"cmd=[{console_python!r},'-c','import time;time.sleep(0.2);print(\"[]\")'];"
        "results=[];"
        "\nfor cycle in range(2):"
        "\n results.append(OwnedReaderCommand().run(cmd,timeout=3).stdout.strip())"
        f"\npathlib.Path({str(report)!r}).write_text(json.dumps(results))"
    )
    baseline = visible_windows()
    foreground = user32.GetForegroundWindow()
    visible_owned: set[int] = set()
    new_windows: set[int] = set()
    focus_transitions = 0
    with subprocess.Popen(
        [pythonw, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **no_window_kwargs(),
    ) as parent:
        deadline = time.monotonic() + 8
        while parent.poll() is None and time.monotonic() < deadline:
            pids = {parent.pid}
            try:
                pids.update(
                    child.pid for child in psutil.Process(parent.pid).children(recursive=True)
                )
            except psutil.NoSuchProcess:
                pass
            windows = visible_windows()
            owned_windows = {hwnd for hwnd, pid in windows.items() if pid in pids}
            visible_owned.update(owned_windows)
            new_windows.update(owned_windows - baseline.keys())
            current = user32.GetForegroundWindow()
            if current != foreground and windows.get(current) in pids:
                focus_transitions += 1
            foreground = current
            time.sleep(0.01)
        stdout, stderr = parent.communicate(timeout=2)
        assert parent.returncode == 0, (stdout, stderr)
    assert json.loads(report.read_text()) == ["[]", "[]"]
    assert not visible_owned
    assert not new_windows
    assert focus_transitions == 0
