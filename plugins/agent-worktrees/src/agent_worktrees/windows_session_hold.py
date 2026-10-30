"""Query Windows for the processes using one native session hold file.

Only Restart Manager's resource observation APIs are used. No shutdown,
restart, process launch or persistent registration is requested.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from pathlib import Path


_MAX_OWNERS = 1024
_MORE_DATA = 234


class _UniqueProcess(ctypes.Structure):
    _fields_ = [
        ("pid", wintypes.DWORD),
        ("started", wintypes.FILETIME),
    ]


class _ProcessInfo(ctypes.Structure):
    _fields_ = [
        ("process", _UniqueProcess),
        ("app_name", wintypes.WCHAR * 256),
        ("service_name", wintypes.WCHAR * 64),
        ("application_type", ctypes.c_int),
        ("app_status", wintypes.ULONG),
        ("session_id", wintypes.DWORD),
        ("restartable", wintypes.BOOL),
    ]


def _api():
    api = ctypes.WinDLL("rstrtmgr", use_last_error=True)
    api.RmStartSession.argtypes = [
        ctypes.POINTER(wintypes.DWORD), wintypes.DWORD, wintypes.LPWSTR,
    ]
    api.RmRegisterResources.argtypes = [
        wintypes.DWORD, wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR),
        wintypes.UINT, ctypes.POINTER(_UniqueProcess),
        wintypes.UINT, ctypes.POINTER(wintypes.LPCWSTR),
    ]
    api.RmGetList.argtypes = [
        wintypes.DWORD, ctypes.POINTER(wintypes.UINT),
        ctypes.POINTER(wintypes.UINT), ctypes.POINTER(_ProcessInfo),
        ctypes.POINTER(wintypes.DWORD),
    ]
    api.RmEndSession.argtypes = [wintypes.DWORD]
    for name in ("RmStartSession", "RmRegisterResources", "RmGetList", "RmEndSession"):
        getattr(api, name).restype = wintypes.DWORD
    return api


def _check(status: int) -> None:
    if status:
        raise OSError(status, "Windows native hold observation failed")


def owners(path: Path) -> set[tuple[int, str]]:
    """Return PID/birth pairs for this exact file; errors never mean no owner."""
    api = _api()
    session = wintypes.DWORD()
    key = ctypes.create_unicode_buffer(33)
    _check(api.RmStartSession(ctypes.byref(session), 0, key))
    try:
        files = (wintypes.LPCWSTR * 1)(str(path))
        _check(api.RmRegisterResources(session, 1, files, 0, None, 0, None))
        needed = wintypes.UINT()
        count = wintypes.UINT()
        reasons = wintypes.DWORD()
        status = api.RmGetList(
            session, ctypes.byref(needed), ctypes.byref(count), None,
            ctypes.byref(reasons),
        )
        if status == 0:
            return set()
        if status != _MORE_DATA:
            _check(status)
        if not 1 <= needed.value <= _MAX_OWNERS:
            raise ValueError("Windows native hold owner count exceeds its budget")
        capacity = needed.value
        entries = (_ProcessInfo * capacity)()
        count.value = capacity
        # A changing list fails closed rather than retrying without a bound.
        _check(api.RmGetList(
            session, ctypes.byref(needed), ctypes.byref(count), entries,
            ctypes.byref(reasons),
        ))
        if count.value > capacity:
            raise ValueError("Windows native hold owner count is invalid")
        return {
            (
                entry.process.pid,
                str((entry.process.started.dwHighDateTime << 32)
                    | entry.process.started.dwLowDateTime),
            )
            for entry in entries[:count.value]
        }
    finally:
        _check(api.RmEndSession(session))
