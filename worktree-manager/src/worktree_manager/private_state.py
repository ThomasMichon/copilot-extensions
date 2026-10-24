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


def _private_metadata(path: Path, *, directory: bool) -> os.stat_result:
    import stat

    reject_links(path)
    metadata = path.lstat()
    if not (stat.S_ISDIR(metadata.st_mode) if directory else stat.S_ISREG(metadata.st_mode)):
        raise OSError("Private storage requires a real directory or regular file.")
    return metadata


def protect(path: Path, *, directory: bool = False) -> None:
    _private_metadata(path, directory=directory)
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


def verify_private(
    path: Path, *, directory: bool = False, allow_inherited: bool = False,
) -> None:
    """Read-only verification for private evidence readers, never ACL repair."""
    import stat

    metadata = _private_metadata(path, directory=directory)
    if os.name != "nt":
        expected = 0o700 if directory else 0o600
        if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != expected:
            raise OSError("Private storage requires owner-only permissions.")
        return
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_security = advapi.GetFileSecurityW
    get_security.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    get_security.restype = wintypes.BOOL
    needed = wintypes.DWORD()
    get_security(str(path), DACL_SECURITY_INFORMATION, None, 0, ctypes.byref(needed))
    if not needed.value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(needed.value)
    if not get_security(
        str(path), DACL_SECURITY_INFORMATION, descriptor, needed, ctypes.byref(needed),
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    convert = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = [
        wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(wintypes.DWORD),
    ]
    convert.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID
    text = wintypes.LPWSTR()
    if not convert(descriptor, 1, DACL_SECURITY_INFORMATION, ctypes.byref(text), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        inheritance = "OICI" if directory else ""
        owner = f"(A;{inheritance};FA;;;OW)"
        system = f"(A;{inheritance};FA;;;SY)"
        if text.value in {"D:P" + owner + system, "D:P" + system + owner}:
            return
        if allow_inherited and not directory:
            import re

            # SQLite sidecars inherit the already-verified private parent's ACEs.
            match = re.fullmatch(
                r"D:(?:P|AI|AR)*((?:\(A;(?:ID)?;FA;;;(?:OW|SY)\)){2})", text.value or "",
            )
            if match and set(re.findall(r";;;(OW|SY)\)", match[1])) == {"OW", "SY"}:
                return
        raise OSError("Private storage requires a protected owner/SYSTEM DACL.")
    finally:
        kernel.LocalFree(text)
