"""Read exact native kernel boot identity, never uptime or process-age inference."""

from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path


def valid_boot_identity(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        platform, identifier = value.split(":", 1)
        parsed = uuid.UUID(identifier)
        return (
            platform in {"windows", "linux"} and parsed.int != 0
            and str(parsed) == identifier
        )
    except (ValueError, AttributeError):
        return False


def kernel_boot_identity() -> str:
    """Return kernel-provided boot UUID or refuse unsupported/unavailable evidence."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class BootEnvironment(ctypes.Structure):
            _fields_ = [
                ("identifier", ctypes.c_ubyte * 16),
                ("firmware_type", wintypes.ULONG),
                ("boot_flags", ctypes.c_uint64),
            ]

        query = ctypes.WinDLL("ntdll").NtQuerySystemInformation
        query.argtypes = [
            wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.POINTER(wintypes.ULONG),
        ]
        query.restype = wintypes.LONG
        data, returned = BootEnvironment(), wintypes.ULONG()
        status = query(90, ctypes.byref(data), ctypes.sizeof(data), ctypes.byref(returned))
        if status < 0 or returned.value < 16:
            raise RuntimeError("native Windows boot identity query unavailable")
        value = "windows:" + str(uuid.UUID(bytes_le=bytes(data.identifier)))
    elif sys.platform.startswith("linux"):
        value = "linux:" + Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="ascii",
        ).strip().lower()
    else:
        raise RuntimeError("native boot identity is unsupported on this platform")
    if not valid_boot_identity(value):
        raise RuntimeError("native boot identity is malformed")
    return value
