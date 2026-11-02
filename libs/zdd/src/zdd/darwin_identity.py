"""Shared native Darwin birth tokens for routing and discovery."""

from __future__ import annotations

import ctypes


def valid_start_time(value: object) -> bool:
    if not isinstance(value, str) or not value or len(value) > 128 or not value.isascii():
        return False
    if value.isdecimal():
        return True
    parts = value.split(":")
    return (
        len(parts) == 2 and all(part.isdecimal() for part in parts)
        and int(parts[0]) > 0 and 0 <= int(parts[1]) < 1_000_000
    )


class _BsdInfo(ctypes.Structure):
    _fields_ = [
        (name, ctypes.c_uint32) for name in (
            "flags", "status", "exit_status", "pid", "parent_pid",
            "uid", "gid", "real_uid", "real_gid", "saved_uid", "saved_gid", "reserved",
        )
    ] + [
        ("command", ctypes.c_char * 16), ("name", ctypes.c_char * 32),
        ("files", ctypes.c_uint32), ("group", ctypes.c_uint32),
        ("job_count", ctypes.c_uint32), ("tty_device", ctypes.c_uint32),
        ("tty_group", ctypes.c_uint32), ("nice", ctypes.c_int32),
        ("start_seconds", ctypes.c_uint64), ("start_microseconds", ctypes.c_uint64),
    ]


def birth_time(pid: int) -> str | None:
    if pid <= 0:
        return None
    try:
        query = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True).proc_pidinfo
        query.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
        query.restype = ctypes.c_int
        info = _BsdInfo()
        size = ctypes.sizeof(info)
        if query(pid, 3, 0, ctypes.byref(info), size) != size:
            return None
    except (OSError, AttributeError):
        return None
    if info.pid != pid or not info.start_seconds or info.start_microseconds >= 1_000_000:
        return None
    return f"{info.start_seconds}:{info.start_microseconds}"
