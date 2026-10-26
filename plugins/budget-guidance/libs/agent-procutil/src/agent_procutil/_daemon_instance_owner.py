"""Current-token file ownership checks for the private Windows inventory."""

from __future__ import annotations

import ctypes
from functools import lru_cache
import os
from pathlib import Path


def _apis():
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    return advapi, kernel


def _sid_text(pointer) -> str:
    from ctypes import wintypes

    advapi, kernel = _apis()
    text = wintypes.LPWSTR()
    if not advapi.ConvertSidToStringSidW(pointer, ctypes.byref(text)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return text.value
    finally:
        kernel.LocalFree(ctypes.cast(text, ctypes.c_void_p))


@lru_cache(maxsize=4)
def _current_sids(pid: int) -> tuple[str, ...]:
    from ctypes import wintypes

    advapi, kernel = _apis()
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        result = []
        for kind in (1, 4):  # TokenUser and TokenOwner (including elevated owner).
            size = wintypes.DWORD()
            advapi.GetTokenInformation(token, kind, None, 0, ctypes.byref(size))
            if not size.value:
                raise ctypes.WinError(ctypes.get_last_error())
            buffer = ctypes.create_string_buffer(size.value)
            if not advapi.GetTokenInformation(token, kind, buffer, size, ctypes.byref(size)):
                raise ctypes.WinError(ctypes.get_last_error())
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents.value
            result.append(_sid_text(sid))
        return tuple(result)
    finally:
        kernel.CloseHandle(token)


def assert_path_owner(path: Path) -> None:
    from ctypes import wintypes

    advapi, kernel = _apis()
    advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    owner, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
    error = advapi.GetNamedSecurityInfoW(
        str(path), 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(descriptor),
    )
    if error:
        raise ctypes.WinError(error)
    try:
        if _sid_text(owner) not in _current_sids(os.getpid()):
            raise OSError("daemon instance path has a foreign owner")
    finally:
        kernel.LocalFree(descriptor)
