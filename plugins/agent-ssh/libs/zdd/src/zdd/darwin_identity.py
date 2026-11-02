"""Shared native Darwin birth tokens for routing and discovery."""

from __future__ import annotations

import ctypes
import errno


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


def native_identity(pid: int) -> tuple[str, bool]:
    """Read native birth/zombie state; only ESRCH proves process absence.

    Denied, unavailable and malformed evidence raises rather than implying exit.
    No process is signaled and a mismatched PID in the native response is unsafe.
    """
    if type(pid) is not int or not 0 < pid <= 0x7FFFFFFF:
        raise ValueError("Darwin process identity requires a positive native PID")
    try:
        query = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True).proc_pidinfo
    except AttributeError as exc:
        raise NotImplementedError("native Darwin process identity requires proc_pidinfo") from exc
    query.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    query.restype = ctypes.c_int
    info = _BsdInfo()
    size = ctypes.sizeof(info)
    ctypes.set_errno(0)
    returned = query(pid, 3, 0, ctypes.byref(info), size)
    error = ctypes.get_errno()
    if returned <= 0:
        raise OSError(error or errno.EIO, "native Darwin process identity query failed")
    if returned != size:
        raise ValueError("native Darwin process identity response is incomplete")
    if info.pid != pid or not info.start_seconds or info.start_microseconds >= 1_000_000:
        raise ValueError("native Darwin process identity response is malformed")
    return f"{info.start_seconds}:{info.start_microseconds}", info.status == 5  # SZOMB


def birth_time(pid: int) -> str | None:
    """Return a birth token for discovery; unavailable identity remains unknown."""
    try:
        return native_identity(pid)[0]
    except (OSError, ValueError, NotImplementedError):
        return None
