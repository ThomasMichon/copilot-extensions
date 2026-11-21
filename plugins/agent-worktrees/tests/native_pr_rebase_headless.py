"""Bounded window observation from a fresh consoleless Windows interpreter."""

import ctypes
import importlib
import json
import os
import sys
import subprocess
import threading
import time
from ctypes import wintypes as w
from pathlib import Path
from collections.abc import Callable

steward_probes = sys.argv[1] == "--steward-probes"
if steward_probes:
    import importlib.util

    root = Path(sys.argv[2])
    spec = importlib.util.spec_from_file_location(
        "mention_guard", root / "plugins" / "copilot-extensions-maintainer"
        / "scripts" / "copilot-mention-guard.py",
    )
    assert spec and spec.loader
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    sys.path.insert(0, str(root / "tools"))
    invoke = importlib.import_module("maintainer_pr_completion").invoke
else:
    sys.path.insert(0, sys.argv[1])
    from agent_worktrees import front_door_cli, git_ops, pr_ops, pr_rebase, state_root


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", w.DWORD), ("cntUsage", w.DWORD), ("th32ProcessID", w.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", w.DWORD),
        ("cntThreads", w.DWORD), ("th32ParentProcessID", w.DWORD),
        ("pcPriClassBase", w.LONG), ("dwFlags", w.DWORD), ("szExeFile", w.WCHAR * 260),
    ]


kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
user = getattr(ctypes, "WinDLL")("user32", use_last_error=True)
kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
kernel.Process32FirstW.argtypes = [w.HANDLE, ctypes.POINTER(ProcessEntry)]
kernel.Process32NextW.argtypes = kernel.Process32FirstW.argtypes
kernel.CloseHandle.argtypes = [w.HANDLE]
kernel.OpenProcess.argtypes = [w.DWORD, w.BOOL, w.DWORD]
kernel.OpenProcess.restype = w.HANDLE
kernel.GetProcessTimes.argtypes = [w.HANDLE, *([ctypes.POINTER(w.FILETIME)] * 4)]
kernel.QueryFullProcessImageNameW.argtypes = [w.HANDLE, w.DWORD, w.LPWSTR, ctypes.POINTER(w.DWORD)]
user.GetForegroundWindow.restype = w.HWND
user.GetWindowThreadProcessId.argtypes = [w.HWND, ctypes.POINTER(w.DWORD)]
user.IsWindowVisible.argtypes = [w.HWND]
user.GetClassNameW.argtypes = [w.HWND, w.LPWSTR, ctypes.c_int]
callback_type = getattr(ctypes, "WINFUNCTYPE")(w.BOOL, w.HWND, w.LPARAM)
user.EnumWindows.argtypes = [callback_type, w.LPARAM]

process_facts: dict[int, dict[str, object]] = {}
window_facts: dict[int, dict[str, object]] = {}


def identity(pid: int) -> dict[str, object]:
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return {"pid": pid, "error": getattr(ctypes, "get_last_error")()}
    try:
        times = [w.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
            return {"pid": pid, "error": getattr(ctypes, "get_last_error")()}
        image, size = ctypes.create_unicode_buffer(32768), w.DWORD(32768)
        kernel.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(size))
        return {"pid": pid, "birth": times[0].dwHighDateTime << 32 | times[0].dwLowDateTime,
                "image": Path(image.value).name}
    finally:
        kernel.CloseHandle(handle)


def descendants() -> set[int]:
    snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
    pairs = []
    try:
        entry = ProcessEntry()
        entry.dwSize = ctypes.sizeof(entry)
        exists = kernel.Process32FirstW(snapshot, ctypes.byref(entry))
        while exists:
            pairs.append((entry.th32ProcessID, entry.th32ParentProcessID, entry.szExeFile))
            exists = kernel.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snapshot)
    owned = {os.getpid()}
    while True:
        found = {pid for pid, parent, _name in pairs if parent in owned}
        if found <= owned:
            for pid, parent, name in pairs:
                if pid in owned:
                    process_facts[pid] = {**identity(pid), "parent": parent, "snapshot_image": name}
            return owned
        owned.update(found)


def window_pid(handle: int | None) -> int:
    pid = w.DWORD()
    user.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    return pid.value


stop = threading.Event()
ready = threading.Event()
seen: set[int] = set()
visible: set[int] = set()
foreground: set[int] = set()
new_visible: set[int] = set()
focus_changes: list[int] = []
errors: list[str] = []


def observe() -> None:
    baseline_windows: set[int] | None = None
    previous_foreground: int | None = None
    try:
        while not stop.is_set():
            seen.update(descendants())
            current_windows: set[int] = set()

            def inspect(handle: int, _param: int) -> bool:
                if user.IsWindowVisible(handle):
                    current_windows.add(int(handle))
                    if window_pid(handle) in seen:
                        visible.add(int(handle))
                        name = ctypes.create_unicode_buffer(256)
                        user.GetClassNameW(handle, name, len(name))
                        pid = window_pid(handle)
                        observed = process_facts.get(pid, {})
                        parent = observed.get("parent")
                        window_facts[int(handle)] = {
                            "process": identity(pid), "observed": observed,
                            "parent": process_facts.get(parent) if isinstance(parent, int) else None,
                            "class": name.value,
                        }
                return True

            callback = callback_type(inspect)
            if not user.EnumWindows(callback, 0):
                raise getattr(ctypes, "WinError")(getattr(ctypes, "get_last_error")())
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
    assert not hasattr(subprocess.Popen.__init__, "_aw_headless")
    for _ in range(2):
        if steward_probes:
            assert guard.current_repo_candidates(sys.argv[3]) == ["example/project"]
            child = str(Path(sys.executable).with_name("python.exe"))
            assert invoke([child, "-c", "import time; time.sleep(0.15)"]).returncode == 0
        else:
            assert pr_rebase._series(sys.argv[3], sys.argv[4], sys.argv[2])
            assert pr_ops._patch_id(sys.argv[3], sys.argv[4], cwd=sys.argv[2])
            assert pr_ops._commit_patch_ids(sys.argv[3], sys.argv[4], cwd=sys.argv[2])
            assert git_ops.resolve_to_anchor(Path(sys.argv[2])).exists()
            get_front_door_toplevel: Callable[[Path], Path] = front_door_cli._git_toplevel
            assert get_front_door_toplevel(Path(sys.argv[2])).exists()
            assert state_root._git_common_dir(Path(sys.argv[2])).is_dir()
            get_toplevel: Callable[[str], str | None] = state_root._git_toplevel
            toplevel = get_toplevel(sys.argv[2])
            assert toplevel is not None
            assert Path(toplevel).resolve() == Path(sys.argv[2]).resolve()
        cycles += 1
        time.sleep(0.1)
finally:
    stop.set()
    thread.join(5)
    Path(sys.argv[4] if steward_probes else sys.argv[5]).write_text(json.dumps({
        "cycles": cycles, "observed_descendants": len(seen) - 1,
        "visible_windows": len(visible), "foreground_owned": len(foreground),
        "new_visible_windows": len(new_visible), "foreground_transitions": len(focus_changes),
        "errors": errors,
        "window_facts": list(window_facts.values())[:10],
    }), encoding="utf-8")
assert not thread.is_alive()
assert not errors
assert not visible and not foreground
assert not new_visible and not focus_changes
