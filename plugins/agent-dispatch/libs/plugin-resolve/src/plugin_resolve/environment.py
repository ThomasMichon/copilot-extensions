"""Fresh OS user environments for dependency selection and child launch."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping


def _windows_environment() -> dict[str, str]:
    if sys.platform != "win32":
        raise OSError("Windows user environments are unavailable on this platform")
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    userenv = ctypes.WinDLL("userenv", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
    ]
    userenv.CreateEnvironmentBlock.argtypes = [
        ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.BOOL,
    ]
    userenv.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]
    token = wintypes.HANDLE()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0xA, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    block = ctypes.c_void_p()
    try:
        if not userenv.CreateEnvironmentBlock(ctypes.byref(block), token, False):
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            result: dict[str, str] = {}
            address = block.value
            if address is None:
                raise OSError("OS user environment block is absent")
            offset = 0
            while offset < 1024 * 1024:
                entry = ctypes.wstring_at(address + offset)
                if not entry:
                    return result
                offset += len(entry.encode("utf-16-le")) + 2
                key, separator, value = entry.partition("=")
                if key and separator:
                    result[key] = value
            raise ValueError("OS user environment exceeds supported size")
        finally:
            userenv.DestroyEnvironmentBlock(block)
    finally:
        kernel.CloseHandle(token)


def fresh_user_environment(
    *, overrides: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Read the current user's OS environment; apply only caller-selected overrides.

    POSIX has no independent user environment block, so it retains the process
    environment. Windows deliberately does not inherit the caller's stale PATH.
    """
    result = _windows_environment() if os.name == "nt" else dict(os.environ)
    for key, value in (overrides or {}).items():
        if os.name == "nt":
            for existing in tuple(result):
                if existing.upper() == key.upper():
                    del result[existing]
        result[key] = value
    return result
