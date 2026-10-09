#!/usr/bin/env python3
"""Best-effort terminal identity probe for the agent-worktrees sessionStart hook.

Loaded by ``hook_client.py`` as a deployed sibling. It observes what only a
process inside the session's terminal can: its ancestry and, on Windows, the
console window and that window's hosting top-level terminal window. Results
are normalized and persisted by ``agent_worktrees.terminal_identity``.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

_MAX_ANCESTORS = 16
_MAX_ATTACH = 6
_BUDGET_S = 1.0


def probe() -> dict:
    """Best-effort facts only this hook process can observe about its terminal.

    Copilot runs hooks without a console window of their own, so on Windows
    the probe briefly attaches to the nearest ancestor's console (preferring
    the Copilot process) to read its console window and that window's root
    owner -- the hosting terminal's top-level window. The probe never raises.
    """
    try:
        if sys.platform == "win32":
            return _probe_windows()
        if sys.platform.startswith("linux"):
            return {"ancestors": _posix_ancestors()}
        # Ancestry and process identity are /proc-based (Linux) or Win32; other
        # platforms (e.g. macOS) record only environment-derived facts.
        return {}
    except Exception:
        return {}


def _posix_ancestors() -> list[int]:
    chain: list[int] = []
    pid = os.getppid()
    for _ in range(_MAX_ANCESTORS):
        if pid <= 1 or pid in chain:
            break
        chain.append(pid)
        try:
            stat_text = Path(f"/proc/{pid}/stat").read_text("utf-8")
        except (OSError, UnicodeError):
            break
        try:
            pid = int(stat_text.rsplit(")", 1)[1].split()[1])
        except (IndexError, ValueError):
            break
    return chain


def _probe_windows() -> dict:
    import ctypes
    from ctypes import wintypes

    started = time.monotonic()
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32.GetConsoleWindow.restype = wintypes.HWND
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
    kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.GetStdHandle.restype = wintypes.HANDLE
    kernel32.GetStdHandle.argtypes = [wintypes.DWORD]
    kernel32.SetStdHandle.argtypes = [wintypes.DWORD, wintypes.HANDLE]
    kernel32.AttachConsole.argtypes = [wintypes.DWORD]
    kernel32.SetConsoleCtrlHandler.argtypes = [ctypes.c_void_p, wintypes.BOOL]
    user32.GetAncestor.restype = wintypes.HWND
    user32.GetAncestor.argtypes = [wintypes.HWND, ctypes.c_uint]
    user32.GetWindowThreadProcessId.argtypes = [
        wintypes.HWND, ctypes.POINTER(wintypes.DWORD)
    ]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]

    class _ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_void_p),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    table: dict[int, tuple[int, str]] = {}
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)
    if snapshot and snapshot != wintypes.HANDLE(-1).value:
        try:
            entry = _ProcessEntry()
            entry.dwSize = ctypes.sizeof(_ProcessEntry)
            more = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while more:
                table[int(entry.th32ProcessID)] = (
                    int(entry.th32ParentProcessID),
                    str(entry.szExeFile),
                )
                more = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)

    ancestors: list[int] = []
    pid = table.get(os.getpid(), (0, ""))[0]
    while pid and pid in table and pid not in ancestors:
        if len(ancestors) >= _MAX_ANCESTORS:
            break
        ancestors.append(pid)
        pid = table[pid][0]

    console_hwnd = kernel32.GetConsoleWindow()
    if not console_hwnd:
        copilot_first = [
            pid for pid in ancestors
            if "copilot" in table[pid][1].lower()
        ]
        candidates = (copilot_first + [
            pid for pid in ancestors if pid not in copilot_first
        ])[:_MAX_ATTACH]
        deadline = started + _BUDGET_S
        if kernel32.GetConsoleCP():
            # Attached to a windowless console (CREATE_NO_WINDOW): AttachConsole
            # would fail with ACCESS_DENIED, and freeing this console would
            # strand our own std handles, so probe from a console-less helper.
            console_hwnd = _detached_console_window(candidates, deadline=deadline)
        else:
            console_hwnd = _attached_console_window(
                kernel32, candidates, deadline=deadline,
            )
    result: dict = {"ancestors": ancestors}
    if not console_hwnd:
        return result

    def _window_facts(hwnd) -> tuple[int | None, str | None]:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        name = ctypes.create_unicode_buffer(256)
        length = user32.GetClassNameW(hwnd, name, 256)
        return (int(owner.value) or None, name.value if length else None)

    root = user32.GetAncestor(console_hwnd, 3)  # GA_ROOTOWNER
    console_class = _window_facts(console_hwnd)[1]
    result["console_hwnd"] = int(console_hwnd)
    result["console_class"] = console_class
    if root and root != console_hwnd:
        host_pid, host_class = _window_facts(root)
        result["host_hwnd"] = int(root)
        result["host_pid"] = host_pid
        result["host_class"] = host_class
        if host_pid in table:
            result["host_exe"] = table[host_pid][1]
    elif console_class == "ConsoleWindowClass":
        # A classic conhost window is itself the top-level terminal window.
        result["host_hwnd"] = int(console_hwnd)
        result["host_class"] = console_class
        result["host_exe"] = "conhost.exe"
    return result


def _attached_console_window(kernel32, candidates: list[int], *, deadline: float):
    std_ids = (-10 & 0xFFFFFFFF, -11 & 0xFFFFFFFF, -12 & 0xFFFFFFFF)
    saved = [kernel32.GetStdHandle(std_id) for std_id in std_ids]
    kernel32.SetConsoleCtrlHandler(None, True)
    try:
        for pid in candidates:
            if time.monotonic() > deadline:
                return None
            if not kernel32.AttachConsole(pid):
                continue
            try:
                hwnd = kernel32.GetConsoleWindow()
            finally:
                kernel32.FreeConsole()
            if hwnd:
                return hwnd
        return None
    finally:
        for std_id, handle in zip(std_ids, saved):
            kernel32.SetStdHandle(std_id, handle)
        kernel32.SetConsoleCtrlHandler(None, False)


_DETACHED_PROBE_SOURCE = """
import ctypes, sys
from ctypes import wintypes
k = ctypes.WinDLL("kernel32")
k.GetConsoleWindow.restype = wintypes.HWND
k.AttachConsole.argtypes = [wintypes.DWORD]
for pid in sys.argv[1:]:
    if k.AttachConsole(int(pid)):
        hwnd = k.GetConsoleWindow()
        k.FreeConsole()
        if hwnd:
            print(int(hwnd))
            break
"""


def _detached_console_window(candidates: list[int], *, deadline: float):
    remaining = deadline - time.monotonic()
    if not candidates or remaining <= 0:
        return None
    # A venv's python.exe is a launcher stub whose real interpreter child would
    # get a fresh console; launch the base interpreter directly instead.
    interpreter = getattr(sys, "_base_executable", None) or sys.executable
    try:
        completed = subprocess.run(
            [interpreter, "-I", "-S", "-c", _DETACHED_PROBE_SOURCE,
             *(str(pid) for pid in candidates)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            creationflags=0x00000008,  # DETACHED_PROCESS: start with no console
            timeout=remaining,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    text = completed.stdout.decode("ascii", "ignore").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None
