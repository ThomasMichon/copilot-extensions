"""Private, crash-durable owner capabilities; never ordinary discovery."""

from __future__ import annotations

import ctypes
import json
import os
import stat
import tempfile
from pathlib import Path

from zdd.routing_persistence import windows_replace

from .watch_storage import StateCommitUncertain

_DACL = "D:P(A;;FA;;;OW)(A;;FA;;;SY)"


def _windows_private(path: Path, *, protect: bool) -> None:
    from ctypes import wintypes as w

    security = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": (
            [w.LPCWSTR, w.DWORD, ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.ULONG)], w.BOOL,
        ),
        "SetFileSecurityW": ([w.LPCWSTR, w.DWORD, w.LPVOID], w.BOOL),
        "GetFileSecurityW": (
            [w.LPCWSTR, w.DWORD, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL,
        ),
        "ConvertSecurityDescriptorToStringSecurityDescriptorW": (
            [w.LPVOID, w.DWORD, w.DWORD, ctypes.POINTER(w.LPWSTR), ctypes.POINTER(w.ULONG)],
            w.BOOL,
        ),
        "GetSecurityDescriptorOwner": (
            [w.LPVOID, ctypes.POINTER(w.LPVOID), ctypes.POINTER(w.BOOL)], w.BOOL,
        ),
        "OpenProcessToken": ([w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)], w.BOOL),
        "GetTokenInformation": (
            [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD, ctypes.POINTER(w.DWORD)], w.BOOL,
        ),
        "ConvertSidToStringSidW": ([w.LPVOID, ctypes.POINTER(w.LPWSTR)], w.BOOL),
        "EqualSid": ([w.LPVOID, w.LPVOID], w.BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(security, name)
        function.argtypes, function.restype = arguments, result
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [w.LPVOID], w.LPVOID
    kernel.GetCurrentProcess.argtypes, kernel.GetCurrentProcess.restype = [], w.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [w.HANDLE], w.BOOL

    def checked(ok: bool) -> None:
        if not ok:
            raise ctypes.WinError(ctypes.get_last_error())

    token = w.HANDLE()
    checked(security.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)))
    try:
        token_size = w.DWORD()
        security.GetTokenInformation(token, 1, None, 0, ctypes.byref(token_size))
        if ctypes.get_last_error() != 122 or not token_size.value:
            raise ctypes.WinError(ctypes.get_last_error())
        user = ctypes.create_string_buffer(token_size.value)
        checked(security.GetTokenInformation(token, 1, user, token_size, ctypes.byref(token_size)))
        sid = ctypes.cast(user, ctypes.POINTER(w.LPVOID))[0]
    finally:
        kernel.CloseHandle(token)
    if protect:
        descriptor = w.LPVOID()
        owner_text = w.LPWSTR()
        checked(security.ConvertSidToStringSidW(sid, ctypes.byref(owner_text)))
        try:
            checked(security.ConvertStringSecurityDescriptorToSecurityDescriptorW(
                f"O:{owner_text.value}{_DACL}", 1, ctypes.byref(descriptor), None,
            ))
        finally:
            kernel.LocalFree(owner_text)
        try:
            checked(security.SetFileSecurityW(str(path), 0x80000005, descriptor))
        finally:
            kernel.LocalFree(descriptor)
    size = w.DWORD()
    if security.GetFileSecurityW(str(path), 5, None, 0, ctypes.byref(size)):
        raise PermissionError("watch private file security descriptor is unavailable")
    if ctypes.get_last_error() != 122 or not size.value:
        raise ctypes.WinError(ctypes.get_last_error())
    descriptor = ctypes.create_string_buffer(size.value)
    checked(security.GetFileSecurityW(str(path), 5, descriptor, size, ctypes.byref(size)))
    rendered = w.LPWSTR()
    checked(security.ConvertSecurityDescriptorToStringSecurityDescriptorW(
        descriptor, 1, 4, ctypes.byref(rendered), None,
    ))
    try:
        if rendered.value != _DACL:
            raise PermissionError("watch capability file is not owner-private")
    finally:
        kernel.LocalFree(rendered)
    owner, defaulted = w.LPVOID(), w.BOOL()
    checked(security.GetSecurityDescriptorOwner(
        descriptor, ctypes.byref(owner), ctypes.byref(defaulted),
    ))
    if not security.EqualSid(owner, sid):
        raise PermissionError("watch capability file belongs to another OS account")


def require_private(path: Path) -> None:
    parent = path.parent.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(parent.st_mode)
        or getattr(parent, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise PermissionError("watch capability directory must not be a link")
    info = path.stat(follow_symlinks=False)
    if (
        not stat.S_ISREG(info.st_mode)
        or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise PermissionError("watch capability must be a regular non-link file")
    if os.name == "nt":
        _windows_private(path, protect=False)
    elif info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise PermissionError("watch capability file is not owner-private")


def _sync_directory(path: Path) -> None:
    if os.name != "nt":
        try:
            descriptor = os.open(path, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError as error:
            raise StateCommitUncertain("watch capability publication durability is uncertain") from error


def confirm_private(path: Path) -> None:
    """Reconcile an existing publication before treating it as durable custody."""
    require_private(path)
    before = path.stat(follow_symlinks=False)
    descriptor = os.open(path, os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(descriptor)
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise PermissionError("watch capability changed before durability confirmation")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _sync_directory(path.parent)
    require_private(path)
    after = path.stat(follow_symlinks=False)
    if (
        before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns,
    ) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns,
    ):
        raise PermissionError("watch capability changed during durability confirmation")


def write_private(path: Path, document: dict, *, replace: bool = True) -> None:
    """Protect before writing secrets, then publish without a partial visible file."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.stat(follow_symlinks=False)
    if (
        not stat.S_ISDIR(parent.st_mode)
        or getattr(parent, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    ):
        raise PermissionError("watch capability directory must not be a link")
    if os.path.lexists(path):
        require_private(path)
        if not replace:
            raise FileExistsError("watch capability custody already exists")
    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            if os.name == "nt":
                _windows_private(temporary, protect=True)
            require_private(temporary)
            json.dump(document, handle, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        if replace:
            if os.name == "nt":
                windows_replace(temporary, path)
            else:
                os.replace(temporary, path)
        else:
            if os.name == "nt":
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                move = kernel.MoveFileExW
                move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
                move.restype = ctypes.c_int
                # WRITE_THROUGH without REPLACE_EXISTING preserves prior custody.
                if not move(str(temporary), str(path), 0x8):
                    error = ctypes.get_last_error()
                    if error in (80, 183):
                        raise FileExistsError("watch capability custody already exists")
                    raise ctypes.WinError(error)
            else:
                os.link(temporary, path)
        _sync_directory(path.parent)
        require_private(path)
    finally:
        temporary.unlink(missing_ok=True)
