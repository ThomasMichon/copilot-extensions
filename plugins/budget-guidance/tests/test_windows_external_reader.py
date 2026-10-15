"""Opt-in desktop witness; noninteractive Windows is not a headlessness pass."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job
import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("BUDGET_GUIDANCE_WINDOWS_NATIVE") != "1",
    reason="explicit native interactive Windows validation",
)


@pytest.mark.parametrize("mode", ["reader", "session-cue"])
def test_native_windowless_parent_two_cycles_and_timeout_cleanup(tmp_path, record_property, mode):
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    assert pythonw.is_file(), "native witness requires the owning interpreter's pythonw.exe"
    user = ctypes.WinDLL("user32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.GetForegroundWindow.restype = wintypes.HWND
    user.GetProcessWindowStation.restype = wintypes.HANDLE
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    user.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("size", wintypes.DWORD), ("usage", wintypes.DWORD), ("pid", wintypes.DWORD),
            ("heap", ctypes.c_size_t), ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
            ("parent", wintypes.DWORD), ("priority", wintypes.LONG), ("flags", wintypes.DWORD),
            ("executable", wintypes.WCHAR * 260),
        ]

    kernel.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    kernel.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]

    def terminals():
        snapshot = kernel.CreateToolhelp32Snapshot(2, 0)
        assert snapshot not in (None, ctypes.c_void_p(-1).value)
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        values = set()
        try:
            assert kernel.Process32FirstW(snapshot, ctypes.byref(entry))
            while True:
                if entry.executable.lower() in {"openconsole.exe", "windowsterminal.exe"}:
                    values.add(entry.pid)
                if not kernel.Process32NextW(snapshot, ctypes.byref(entry)):
                    assert ctypes.get_last_error() == 18
                    break
            return values
        finally:
            kernel.CloseHandle(snapshot)

    class Flags(ctypes.Structure):
        _fields_ = [
            ("inherit", wintypes.BOOL), ("reserved", wintypes.BOOL), ("flags", wintypes.DWORD),
        ]

    flags = Flags()
    length = wintypes.DWORD()
    assert user.GetUserObjectInformationW(
        user.GetProcessWindowStation(), 1, ctypes.byref(flags), ctypes.sizeof(flags),
        ctypes.byref(length),
    )
    assert flags.flags & 1, "noninteractive window station cannot validate desktop headlessness"
    record_property("interactive_desktop_observation", True)

    def windows():
        values = {}

        @callback
        def visit(hwnd, _):
            if user.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                values[int(hwnd)] = pid.value
            return True

        assert user.EnumWindows(visit, 0)
        return values

    baseline = windows()
    baseline_terminals = terminals()
    foreground = user.GetForegroundWindow()
    surfaced, focus = set(), set()
    handles, owned = {}, set()
    terminal_acquisitions = set()
    fixture = Path(__file__).with_name("windows_external_reader_fixture.py")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(map(str, sys.path))
    with (tmp_path / "parent.log").open("wb") as log:
        parent, job = spawn_sync_in_kill_on_close_job(
            [str(pythonw), str(fixture), "host" if mode == "reader" else "cue-host",
             str(tmp_path), sys.executable],
            env=env, cwd=tmp_path, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            require_job=True, allow_breakaway=False, **no_window_kwargs(),
        )
        assert job is not None
        owned.add(parent.pid)
        try:
            deadline = time.monotonic() + (20 if mode == "reader" else 30)
            while time.monotonic() < deadline:
                terminal_acquisitions.update(terminals() - baseline_terminals)
                process_records = (
                    tmp_path / str(cycle) / f"{role}.json"
                    for cycle in range(3) for role in ("reader", "descendant")
                )
                for path in process_records:
                    if not path.is_file():
                        continue
                    if path in handles:
                        continue
                    state = json.loads(path.read_text(encoding="utf-8"))
                    assert not state["visible"], "owned reader/descendant exposed its console"
                    handle = kernel.OpenProcess(0x00100000 | 0x1000, False, state["pid"])
                    assert handle, "cannot pin native fixture process identity"
                    handles[path] = handle
                    creation, exit_time, kernel_time, user_time = (
                        wintypes.FILETIME() for _ in range(4)
                    )
                    assert kernel.GetProcessTimes(
                        handle, ctypes.byref(creation), ctypes.byref(exit_time),
                        ctypes.byref(kernel_time), ctypes.byref(user_time),
                    )
                    assert ((creation.dwHighDateTime << 32) | creation.dwLowDateTime) == state["birth"]
                    owned.add(state["pid"])
                current_windows = windows()
                surfaced.update(
                    hwnd for hwnd, pid in current_windows.items()
                    if hwnd not in baseline and (pid in owned or mode == "session-cue")
                )
                current = user.GetForegroundWindow()
                if current and current != foreground:
                    pid = wintypes.DWORD()
                    user.GetWindowThreadProcessId(current, ctypes.byref(pid))
                    if pid.value in owned or mode == "session-cue":
                        focus.add(int(current))
                if parent.poll() is not None:
                    break
                time.sleep(0.01)
            assert parent.poll() == 0, (tmp_path / "parent.log").read_text(errors="replace")
            results = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
            print("NATIVE_LIFECYCLE_RESULTS=" + json.dumps(results, separators=(",", ":")))
            if mode == "reader":
                assert [result["availability"] for result in results] == ["available", "available", "error"], results
                assert [result["consumption"] for result in results] == ["7", "7", None]
                assert results[2]["error"] == "reader deadline exceeded"
            else:
                assert [result["availability"] for result in results] == ["available", "available", None], results
                assert [result["consumption"] for result in results] == ["7", "8", None], results
                assert all(result["stdout"] == "{}" for result in results)
                assert all(result["returncode"] == 0 for result in results)
                assert "session cue could not be written" in results[2]["stderr"]
                assert results[2]["elapsed_seconds"] < 10, results
                for cycle in range(3):
                    root = json.loads((tmp_path / str(cycle) / "reader.json").read_text())
                    child = json.loads((tmp_path / str(cycle) / "descendant.json").read_text())
                    assert child["parent"] == root["pid"], "writer launched behind an unowned redirector"
            assert len(handles) == 6, "must witness root and console descendant in every cycle"
            assert all(kernel.WaitForSingleObject(handle, 0) == 0 for handle in handles.values())
            assert not surfaced
            assert not focus
            assert not terminal_acquisitions, "Default Terminal/OpenConsole acquisition observed"
            record_property("successful_cycles", 2)
            record_property("timeout_tree_cleanup", True)
            record_property("default_terminal_acquisitions", 0)
            record_property("launch_path", mode)
        finally:
            job.terminate_and_wait(5)
            parent.wait(timeout=5)
            for handle in handles.values():
                kernel.CloseHandle(handle)
