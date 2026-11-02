"""Private inventory creation and current-token Windows ownership checks."""

from __future__ import annotations

import contextlib
import errno
from functools import lru_cache
import os
from pathlib import Path
from collections.abc import Iterator
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import ctypes


def _apis():
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    return advapi, kernel


def _sid_text(pointer) -> str:
    import ctypes
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
    import ctypes
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


def _path_owner_sid(path: Path) -> str:
    import ctypes
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
        return _sid_text(owner)
    finally:
        kernel.LocalFree(descriptor)


def assert_path_owner(path: Path) -> None:
    if _path_owner_sid(path) not in _current_sids(os.getpid()):
        raise OSError("daemon instance path has a foreign owner")


@lru_cache(maxsize=1)
def _attributes_type() -> type[ctypes.Structure]:
    import ctypes

    class _SecurityAttributes(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint32),
            ("descriptor", ctypes.c_void_p),
            ("inherit_handle", ctypes.c_int32),
        ]
    return _SecurityAttributes


@contextlib.contextmanager
def _creation_attributes(*, directory: bool) -> Iterator[ctypes.Structure]:
    import ctypes
    from ctypes import wintypes

    advapi, kernel = _apis()
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.restype = wintypes.BOOL
    user = _current_sids(os.getpid())[0]
    inheritance = "OICI" if directory else ""
    # Match private-mode access for the user, SYSTEM and Administrators, but
    # pin the owner to the user rather than the launcher-specific TokenOwner.
    sddl = (
        f"O:{user}D:P(A;{inheritance};FA;;;SY)"
        f"(A;{inheritance};FA;;;BA)(A;{inheritance};FA;;;{user})"
    )
    descriptor = ctypes.c_void_p()
    if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, 1, ctypes.byref(descriptor), None,
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        attributes = _attributes_type()
        yield attributes(ctypes.sizeof(attributes), descriptor, 0)
    finally:
        if kernel.LocalFree(descriptor):
            raise OSError("private inventory security descriptor could not be released")


def _creation_error(error: int, path: Path) -> OSError:
    if error in (80, 183):
        return FileExistsError(errno.EEXIST, "private inventory entry already exists", str(path))
    if error in (2, 3):
        return FileNotFoundError(errno.ENOENT, "private inventory parent is missing", str(path))
    if error == 5:
        return PermissionError(errno.EACCES, "private inventory creation was denied", str(path))
    import ctypes
    return ctypes.WinError(error)


def mkdir_private(path: Path, *, exist_ok: bool = False) -> None:
    if os.name != "nt":
        path.mkdir(mode=0o700, exist_ok=exist_ok)
        return
    import ctypes
    from ctypes import wintypes

    _, kernel = _apis()
    kernel.CreateDirectoryW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(_attributes_type())]
    kernel.CreateDirectoryW.restype = wintypes.BOOL
    with _creation_attributes(directory=True) as attributes:
        created = kernel.CreateDirectoryW(str(path), ctypes.byref(attributes))
        error = 0 if created else ctypes.get_last_error()
    if not created and not (error in (80, 183) and exist_ok and path.is_dir()):
        raise _creation_error(error, path)


def open_private(path: Path, flags: int, mode: int = 0o600) -> int:
    if os.name != "nt":
        return os.open(path, flags, mode)
    if mode != 0o600:
        raise ValueError("private inventory file creation requires mode 0600")
    import ctypes
    from ctypes import wintypes
    import msvcrt

    _, kernel = _apis()
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.POINTER(_attributes_type()), wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [wintypes.HANDLE], wintypes.BOOL
    access_mode = flags & (os.O_WRONLY | os.O_RDWR)
    access = 0x40000000 if access_mode == os.O_WRONLY else 0xC0000000 if access_mode == os.O_RDWR else 0x80000000
    disposition = (
        1 if flags & os.O_CREAT and flags & os.O_EXCL
        else 2 if flags & os.O_CREAT and flags & os.O_TRUNC
        else 4 if flags & os.O_CREAT
        else 5 if flags & os.O_TRUNC
        else 3
    )
    handle = None
    invalid_handle = ctypes.c_void_p(-1).value
    try:
        with _creation_attributes(directory=False) as attributes:
            # OPEN_REPARSE_POINT preserves the caller's no-indirection checks;
            # existing files keep their original security descriptor unchanged.
            handle = kernel.CreateFileW(
                str(path), access, 3, ctypes.byref(attributes), disposition, 0x00200080, None,
            )
            if handle is None or handle == invalid_handle:
                raise _creation_error(ctypes.get_last_error(), path)
        # The HANDLE already carries access and noninheritance. Omitting text
        # flags keeps the CRT descriptor binary; append is its remaining policy.
        descriptor = msvcrt.open_osfhandle(handle, flags & os.O_APPEND)
        handle = None
        try:
            os.set_inheritable(descriptor, False)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor
    finally:
        if handle is not None and handle != invalid_handle and not kernel.CloseHandle(handle):
            raise OSError("private inventory file handle could not be closed")
