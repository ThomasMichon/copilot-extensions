"""Protect local user content before reading or writing it."""

from __future__ import annotations

import os
from pathlib import Path


DACL_SECURITY_INFORMATION = 0x00000004
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000


def reject_links(path: Path) -> None:
    """Inspect the path and its ancestors for symlink/reparse indirection,
    including dangling links.
    """
    import stat

    for candidate in (path, *path.parents):
        try:
            metadata = candidate.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise OSError("Private storage must not use symlinks or reparse points.")


def protect(path: Path, *, directory: bool = False) -> None:
    reject_links(path)
    path.lstat()
    if os.name != "nt":
        path.chmod(0o700 if directory else 0o600)
        return
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD,
                        ctypes.POINTER(wintypes.LPVOID), ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    apply = advapi.SetFileSecurityW
    apply.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID]
    apply.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID
    descriptor = wintypes.LPVOID()
    # Protected DACL: owner and SYSTEM only; child journals inherit protection.
    inheritance = "OICI" if directory else ""
    sddl = f"D:P(A;{inheritance};FA;;;OW)(A;{inheritance};FA;;;SY)"
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        security_information = DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
        if not apply(str(path), security_information, descriptor):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.LocalFree(descriptor)
