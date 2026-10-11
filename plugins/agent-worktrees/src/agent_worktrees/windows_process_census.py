"""Native, fresh Windows interpreter census for identity-sensitive diagnostics."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from . import locks, procs


def command_line(pid: int) -> str | None:
    """Read a process command line without launching a shell; unreadable is None."""
    k32 = procs._win_kernel32()
    handle = k32.OpenProcess(procs._PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        ntdll = ctypes.WinDLL("ntdll", use_last_error=True)
        query = ntdll.NtQueryInformationProcess
        query.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p,
            wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
        ]
        query.restype = ctypes.c_long
        needed = wintypes.ULONG()
        query(handle, 60, None, 0, ctypes.byref(needed))
        # ProcessCommandLineInformation returns a local UNICODE_STRING plus data.
        if needed.value < ctypes.sizeof(wintypes.USHORT) * 2 or needed.value > 131072:
            return None
        buffer = ctypes.create_string_buffer(needed.value)
        if query(handle, 60, buffer, len(buffer), ctypes.byref(needed)) != 0:
            return None

        class UnicodeString(ctypes.Structure):
            _fields_ = [
                ("length", wintypes.USHORT),
                ("maximum_length", wintypes.USHORT),
                ("buffer", ctypes.c_void_p),
            ]

        if len(buffer) < ctypes.sizeof(UnicodeString):
            return None
        value = UnicodeString.from_buffer(buffer)
        start = ctypes.addressof(buffer)
        pointer = value.buffer or 0
        if (
            value.length % 2
            or value.length > value.maximum_length
            or pointer < start + ctypes.sizeof(UnicodeString)
            or pointer + value.length > start + len(buffer)
        ):
            return None
        return ctypes.string_at(pointer, value.length).decode("utf-16-le")
    finally:
        k32.CloseHandle(handle)


def snapshot() -> list[dict]:
    """Enumerate once; retain only readable, identity-stable Python processes."""
    k32 = procs._win_kernel32()
    records = []
    for pid in procs._win_enum_pids(strict=True):
        before = locks.process_start_time(pid)
        if before is None:
            continue
        cwd, name = procs._win_read_cwd(k32, pid)
        if name not in ("python.exe", "pythonw.exe"):
            continue
        cmdline = command_line(pid)
        after = locks.process_start_time(pid)
        if cmdline is None or before != after:
            continue
        records.append({
            "pid": pid, "cwd": cwd, "command_line": cmdline, "start_time": before,
        })
    return records
