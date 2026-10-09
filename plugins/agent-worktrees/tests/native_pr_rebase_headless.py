"""Bounded window observation from a fresh consoleless Windows interpreter."""

import ctypes
import json
import os
import sys
import threading
import time
from ctypes import wintypes as w
from pathlib import Path

sys.path.insert(0, sys.argv[1])
from agent_worktrees import git_ops, pr_rebase  # noqa: E402


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", w.DWORD),
        ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD),
        ("pcPriClassBase", w.LONG), ("dwFlags", w.DWORD), ("szExeFile", w.WCHAR * 260),
    ]


kernel = ctypes.WinDLL("kernel32", use_last_error=True)
user = ctypes.WinDLL("user32", use_last_error=True)
kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
kernel.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]
kernel.Process32NextW.argtypes = kernel.Process32FirstW.argtypes
kernel.CloseHandle.argtypes = [w.HANDLE]
user.GetForegroundWindow.restype = w.HWND
user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user.IsWindowVisible.argtypes = [w.HWND]
callback_type = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)
user.EnumWindows.argtypes = [callback_type, w.LPARAM]


def descendants():
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    pairs = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        exists = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while exists:
            pairs.append((entry.th32ProcessID, entry.th32ParentProcessID))
            exists = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    owned = {os.getpid()}
    while True:
        found = {pid for pid, parent in pairs if parent in owned}
        if found <= owned:
            return owned
        owned.update(found)


def window_pid(handle):
    pid = w.DWORD()
    user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    return pid.value


stop = threading.Event()
ready = threading.Event()
seen = set()
visible = set()
foreground = set()
new_visible = set()
focus_changes = []
errors = []


def observe():
    baseline_windows = None
    previous_foreground = None
    try:
        while not stop.is_set():
            seen.update(descendants())
            current_windows = set()

            @callback_type
            def inspect(handle, _param):
                if user.IsWindowVisible(handle):
                    current_windows.add(int(handle))
                    if window_pid(handle) in seen:
                        visible.add(int(handle))
                return True

            if not user.EnumWindows(inspect, 0):
                raise ctypes.WinError(ctypes.get_last_error())
            if baseline_windows is None:
                baseline_windows = current_windows
            # Console hosts may be broker-owned rather than process descendants.
            new_visible.update(current_windows - baseline_windows)
            handle = user.GetForegroundWindow()
            if previous_foreground is not None and handle != previous_foreground:
                focus_changes.append(1)
            previous_foreground = handle
            if handle and window_pid(handle) in seen:
                foreground.add(int(handle))
            ready.set()
            stop.wait(0.005)
    except Exception as exc:
        errors.append(f"{type(exc).__name__}: {exc}")
        ready.set()


thread = threading.Thread(target=observe)
thread.start()
assert ready.wait(5)
cycles = 0
try:
    import subprocess

    assert not hasattr(subprocess.Popen.__init__, "_aw_headless")
    for _ in range(2):
        assert pr_rebase._series(sys.argv[3], sys.argv[4], sys.argv[2])
        assert git_ops.resolve_to_anchor(Path(sys.argv[2])).exists()
        cycles += 1
        time.sleep(0.1)
finally:
    stop.set()
    thread.join(5)
    Path(sys.argv[5]).write_text(json.dumps({
        "cycles": cycles, "observed_descendants": len(seen) - 1,
        "visible_windows": len(visible), "foreground_owned": len(foreground),
        "new_visible_windows": len(new_visible), "foreground_transitions": len(focus_changes),
        "errors": errors,
    }), encoding="utf-8")
assert not thread.is_alive()
assert not errors
assert not visible and not foreground
assert not new_visible and not focus_changes
