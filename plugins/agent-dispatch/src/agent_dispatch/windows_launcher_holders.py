"""Observe exact Windows launcher holders; never ask Restart Manager to stop them."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path


class _UniqueProcess(ctypes.Structure):
    _fields_ = [("pid", wintypes.DWORD), ("birth", wintypes.FILETIME)]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", _UniqueProcess),
        ("application", wintypes.WCHAR * 256),
        ("service", wintypes.WCHAR * 64),
        ("kind", wintypes.DWORD),
        ("status", wintypes.ULONG),
        ("session", wintypes.DWORD),
        ("restartable", wintypes.BOOL),
    ]


def launcher_holders(path: Path) -> dict[int, str]:
    """Return PID/native creation pairs for one file, with bounded census retries."""
    api = ctypes.WinDLL("rstrtmgr", use_last_error=True)
    api.RmStartSession.argtypes = [
        ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR,
    ]
    api.RmRegisterResources.argtypes = [
        wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.UINT, ctypes.c_void_p, wintypes.UINT, ctypes.c_void_p,
    ]
    api.RmGetList.argtypes = [
        wintypes.DWORD, ctypes.POINTER(wintypes.UINT), ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(_ProcessInfo), ctypes.POINTER(wintypes.DWORD),
    ]
    api.RmEndSession.argtypes = [wintypes.DWORD]
    for name in ("RmStartSession", "RmRegisterResources", "RmGetList", "RmEndSession"):
        getattr(api, name).restype = wintypes.DWORD

    def checked(code: int) -> None:
        if code:
            raise ctypes.WinError(code)

    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    checked(api.RmStartSession(ctypes.byref(session), 0, key))
    try:
        files = (wintypes.LPCWSTR * 1)(str(path))
        checked(api.RmRegisterResources(session, 1, files, 0, None, 0, None))
        capacity = 0
        for _ in range(3):
            needed, count, reason = wintypes.UINT(), wintypes.UINT(capacity), wintypes.DWORD()
            rows = (_ProcessInfo * capacity)() if capacity else None
            code = api.RmGetList(
                session, ctypes.byref(needed), ctypes.byref(count), rows, ctypes.byref(reason),
            )
            if code == 234:
                if needed.value > 128:
                    raise OSError("launcher holder census exceeds its bound")
                capacity = needed.value
                continue
            checked(code)
            return {
                row.process.pid: "windows:" + str(
                    (row.process.birth.dwHighDateTime << 32) | row.process.birth.dwLowDateTime,
                )
                for row in (rows or ())[:count.value]
            }
        raise OSError("launcher holder census changed repeatedly")
    finally:
        checked(api.RmEndSession(session))
