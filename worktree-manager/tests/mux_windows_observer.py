"""Win32 observation independent of optional sibling runtime packages."""

from __future__ import annotations

import ctypes
from ctypes import wintypes


class ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("usage", wintypes.DWORD),
        ("pid", wintypes.DWORD), ("heap", ctypes.c_size_t),
        ("module", wintypes.DWORD), ("threads", wintypes.DWORD),
        ("parent", wintypes.DWORD), ("priority", wintypes.LONG),
        ("flags", wintypes.DWORD), ("name", wintypes.WCHAR * 260),
    ]


def processes() -> dict[int, tuple[str, int]]:
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    api.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    api.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    api.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(ProcessEntry)]
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = api.CreateToolhelp32Snapshot(2, 0)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    result = {}
    try:
        entry = ProcessEntry()
        entry.size = ctypes.sizeof(entry)
        more = api.Process32FirstW(handle, ctypes.byref(entry))
        while more:
            result[int(entry.pid)] = (entry.name, int(entry.parent))
            more = api.Process32NextW(handle, ctypes.byref(entry))
    finally:
        api.CloseHandle(handle)
    return result


def descendants(root: int, table: dict[int, tuple[str, int]]) -> set[int]:
    owned = {root}
    while True:
        expanded = owned | {pid for pid, (_, parent) in table.items() if parent in owned}
        if expanded == owned:
            return owned
        owned = expanded


def windows() -> tuple[dict[int, int], int]:
    api = ctypes.WinDLL("user32", use_last_error=True)
    callback = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    api.EnumWindows.argtypes = [callback, wintypes.LPARAM]
    api.IsWindowVisible.argtypes = [wintypes.HWND]
    api.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    api.GetForegroundWindow.restype = wintypes.HWND
    result: dict[int, int] = {}

    @callback
    def visit(hwnd: int, _parameter: int) -> bool:
        if api.IsWindowVisible(hwnd):
            pid = wintypes.DWORD()
            api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            result[int(hwnd)] = int(pid.value)
        return True

    if not api.EnumWindows(visit, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    foreground = api.GetForegroundWindow()
    pid = wintypes.DWORD()
    if foreground:
        api.GetWindowThreadProcessId(foreground, ctypes.byref(pid))
    return result, int(pid.value)
