"""Native private creation and strict directory barriers for typed publication."""

from __future__ import annotations

import ctypes
import os
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any

from agent_procutil import _daemon_instance_owner as private_owner

from agent_logger.sync.provenance import windows_extended_path


def _error(code: int | None = None) -> OSError:
    factory: Callable[[int | None], OSError] = getattr(ctypes, "WinError")
    return factory(code)


def mkdir_private(path: Path) -> None:
    private_owner.mkdir_private(Path(windows_extended_path(path)))


def create_private(path: Path) -> int:
    return private_owner.open_private(
        Path(windows_extended_path(path)), os.O_WRONLY | os.O_CREAT | os.O_EXCL,
    )


def check_private(path: Path) -> None:
    """Validate existing privacy without changing ownership or inheritance."""
    native_apis: Callable[[], tuple[Any, Any]] = getattr(private_owner, "_apis")
    advapi, kernel = native_apis()
    advapi.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.GetAclInformation.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int,
    ]
    advapi.GetAclInformation.restype = wintypes.BOOL
    advapi.GetAce.argtypes = [
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
    ]
    advapi.GetAce.restype = wintypes.BOOL
    owner, acl, descriptor = (ctypes.c_void_p() for _ in range(3))
    error = advapi.GetNamedSecurityInfoW(
        windows_extended_path(path), 1, 5, ctypes.byref(owner), None,
        ctypes.byref(acl), None, ctypes.byref(descriptor),
    )
    if error:
        raise _error(error)
    try:
        token_owners = private_owner._current_sids(os.getpid())
        current = token_owners[0]
        if private_owner._sid_text(owner) not in token_owners or not acl:
            raise PermissionError("typed publication entry has no private owner/DACL")
        allowed = {current, "S-1-5-18", "S-1-5-32-544", "S-1-3-4"}
        info = (wintypes.DWORD * 3)()
        if not advapi.GetAclInformation(acl, info, ctypes.sizeof(info), 2):
            raise _error()
        if info[0] > 1024:
            raise PermissionError("typed publication ACL exceeds its entry budget")
        for index in range(info[0]):
            ace = ctypes.c_void_p()
            if not advapi.GetAce(acl, index, ctypes.byref(ace)):
                raise _error()
            if ace.value is None:
                raise OSError("native access rule is unavailable")
            header = ctypes.string_at(ace, 8)
            if header[0] in (1, 2, 17):
                continue
            if header[0] != 0:
                raise PermissionError("typed publication has an unsupported access rule")
            if (
                int.from_bytes(header[4:8], "little")
                and private_owner._sid_text(ace.value + 8) not in allowed
            ):
                raise PermissionError("typed publication permits another user's access")
    finally:
        kernel.LocalFree(descriptor)


def sync_directory(path: Path) -> None:
    kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel.FlushFileBuffers.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(
        windows_extended_path(path), 0x40000000, 3, None, 3, 0x02200000, None,
    )
    if handle in (None, wintypes.HANDLE(-1).value):
        raise _error()
    try:
        if not kernel.FlushFileBuffers(handle):
            raise _error()
    finally:
        if not kernel.CloseHandle(handle):
            raise _error()
