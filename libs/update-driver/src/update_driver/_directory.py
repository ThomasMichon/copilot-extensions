"""Atomic private queue-directory creation without changing existing permissions."""

from __future__ import annotations

import os
from pathlib import Path
import stat


def create_queue_directory(path: Path, *, exist_ok: bool = True) -> None:
    if os.name != "nt":
        path.mkdir(parents=True, exist_ok=exist_ok, mode=0o700)
        return
    _windows_directory(path, exist_ok=exist_ok)


def _windows_directory(path: Path, *, exist_ok: bool) -> None:
    import ctypes as c
    from ctypes import wintypes as w

    class Attributes(c.Structure):
        _fields_ = [("length", w.DWORD), ("descriptor", w.LPVOID), ("inherit", w.BOOL)]

    class SidAttributes(c.Structure):
        _fields_ = [("sid", w.LPVOID), ("attributes", w.DWORD)]

    kernel = c.WinDLL("kernel32", use_last_error=True)
    security = c.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [w.LPVOID]
    kernel.LocalFree.restype = w.LPVOID
    kernel.CreateDirectoryW.argtypes = [w.LPCWSTR, c.POINTER(Attributes)]
    kernel.CreateDirectoryW.restype = w.BOOL
    security.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, c.POINTER(w.HANDLE)]
    security.OpenProcessToken.restype = w.BOOL
    security.GetTokenInformation.argtypes = [
        w.HANDLE, c.c_int, w.LPVOID, w.DWORD, c.POINTER(w.DWORD),
    ]
    security.GetTokenInformation.restype = w.BOOL
    security.ConvertSidToStringSidW.argtypes = [w.LPVOID, c.POINTER(w.LPWSTR)]
    security.ConvertSidToStringSidW.restype = w.BOOL
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        w.LPCWSTR, w.DWORD, c.POINTER(w.LPVOID), w.LPVOID,
    ]
    security.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = w.BOOL
    token, text, descriptor = w.HANDLE(), w.LPWSTR(), w.LPVOID()
    if not security.OpenProcessToken(kernel.GetCurrentProcess(), 8, c.byref(token)):
        raise c.WinError(c.get_last_error())
    try:
        needed = w.DWORD()
        if (
            security.GetTokenInformation(token, 1, None, 0, c.byref(needed))
            or c.get_last_error() != 122 or needed.value < c.sizeof(SidAttributes)
        ):
            raise OSError("Cannot determine the queue account identity")
        data = c.create_string_buffer(needed.value)
        if not security.GetTokenInformation(token, 1, data, needed, c.byref(needed)):
            raise c.WinError(c.get_last_error())
        user = c.cast(data, c.POINTER(SidAttributes)).contents.sid
        if not security.ConvertSidToStringSidW(user, c.byref(text)):
            raise c.WinError(c.get_last_error())
        sid = text.value
        # An elevated token's default owner can be Administrators. Bind both
        # ownership and inheritable access to TokenUser instead of Owner Rights.
        sddl = f"O:{sid}D:P(A;OICI;FA;;;{sid})(A;OICI;FA;;;SY)(A;OICI;FA;;;BA)"
        if not security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, c.byref(descriptor), None,
        ):
            raise c.WinError(c.get_last_error())
        attributes = Attributes(c.sizeof(Attributes), descriptor, False)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not kernel.CreateDirectoryW(str(path), c.byref(attributes)):
            error = c.get_last_error()
            if error != 183:
                raise c.WinError(error)
            metadata = path.lstat()
            if (
                not exist_ok or not stat.S_ISDIR(metadata.st_mode)
                or getattr(metadata, "st_file_attributes", 0) & 0x400
            ):
                raise FileExistsError(f"Queue directory is not an existing ordinary directory: {path}")
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if text:
            kernel.LocalFree(c.cast(text, w.LPVOID))
        kernel.CloseHandle(token)
