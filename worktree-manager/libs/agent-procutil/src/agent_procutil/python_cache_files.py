"""No-follow, identity-checked operations on private cache entries."""
from __future__ import annotations

import contextlib
import os
import stat
from pathlib import Path

from . import python_cache_permissions as permissions


def identity(info: os.stat_result) -> tuple[int, int]:
    return info.st_dev, info.st_ino


def present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    return True


def ordinary(info: os.stat_result) -> bool:
    return (
        (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode))
        and not stat.S_ISLNK(info.st_mode)
        and not getattr(info, "st_file_attributes", 0) & 0x400
    )


def _windows_open(path: Path, *, delete: bool, read: bool = False):
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(
        str(path), 0x80 | (0x10000 if delete else 0) | (0x80000000 if read else 0), 3, None, 3,
        0x02000000 | 0x00200000, None,
    )
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    return kernel, handle


class Entry:
    def __init__(
        self, path: Path, info: os.stat_result, *, fd=None, parent=None, windows=None,
        check_budget=None,
    ):
        self.path, self.info = path, info
        self.fd, self.parent, self.windows = fd, parent, windows
        self.check_budget = check_budget or (lambda: None)

    def verify(self) -> None:
        self.check_budget()
        named = self.path.lstat() if self.windows else os.stat(
            self.path.name, dir_fd=self.parent.fd, follow_symlinks=False,
        ) if self.parent else self.path.lstat()
        if not ordinary(named) or identity(named) != identity(self.info):
            raise ValueError("cache entry changed")

    def names(self):
        self.check_budget()
        return os.scandir(self.path if self.windows else self.fd)

    def read(self, limit: int) -> bytes:
        self.check_budget()
        if not stat.S_ISREG(self.info.st_mode) or self.info.st_size > limit:
            raise ValueError("cache metadata exceeds its byte budget")
        if self.windows:
            import ctypes
            from ctypes import wintypes

            kernel, handle = self.windows
            kernel.ReadFile.argtypes = [
                wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
            ]
            kernel.ReadFile.restype = wintypes.BOOL
            buffer, count = ctypes.create_string_buffer(limit + 1), wintypes.DWORD()
            self.check_budget()
            if not kernel.ReadFile(handle, buffer, limit + 1, ctypes.byref(count), None):
                raise ctypes.WinError(ctypes.get_last_error())
            if count.value > limit:
                raise ValueError("cache metadata exceeds its byte budget")
            return buffer.raw[:count.value]
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        descriptor = os.open(
            self.path.name, flags, dir_fd=self.parent.fd,
        )
        with os.fdopen(descriptor, "rb") as stream:
            self.check_budget()
            if identity(os.fstat(stream.fileno())) != identity(self.info):
                raise ValueError("cache metadata changed")
            self.check_budget()
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("cache metadata exceeds its byte budget")
        return data

    def remove(self) -> None:
        self.verify()
        self.check_budget()
        if self.windows:
            import ctypes
            from ctypes import wintypes

            kernel, handle = self.windows
            disposition = wintypes.BOOL(True)
            self.check_budget()
            if not kernel.SetFileInformationByHandle(
                handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition),
            ):
                raise ctypes.WinError(ctypes.get_last_error())
        elif stat.S_ISDIR(self.info.st_mode):
            os.rmdir(self.path.name, dir_fd=self.parent.fd)
        else:
            os.unlink(self.path.name, dir_fd=self.parent.fd)


@contextlib.contextmanager
def child(parent: Entry, name: str, *, expected=None, delete: bool = False, read: bool = False):
    parent.check_budget()
    if not name or name in (".", "..") or Path(name).name != name or ":" in name:
        raise ValueError("cache entry name is not ordinary")
    path = parent.path / name
    descriptor, windows = None, None
    try:
        if os.name == "nt":
            windows = _windows_open(path, delete=delete, read=read)
            parent.check_budget()
            info = path.lstat()
        else:
            # O_NONBLOCK prevents a substituted FIFO from blocking admission.
            descriptor = os.open(
                name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent.fd,
            )
            parent.check_budget()
            info = os.fstat(descriptor)
        if not ordinary(info) or expected is not None and identity(info) != tuple(expected):
            raise ValueError("cache entry is redirected or replaced")
        entry = Entry(
            path, info, fd=descriptor, parent=parent, windows=windows,
            check_budget=parent.check_budget,
        )
        entry.verify()
        yield entry
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if windows is not None:
            windows[0].CloseHandle(windows[1])


@contextlib.contextmanager
def installation(path: Path, *, check_budget=None):
    """Keep ancestors anchored for the entire catalog transaction and pass."""
    check_budget = check_budget or (lambda: None)
    check_budget()
    parts = path.parts
    if not path.is_absolute() or len(parts) > 128:
        raise ValueError("cache root is not an ordinary bounded absolute path")
    with contextlib.ExitStack() as stack:
        root = Path(path.anchor)
        if os.name == "nt":
            windows = _windows_open(root, delete=False)
            stack.callback(windows[0].CloseHandle, windows[1])
            check_budget()
            current = Entry(root, root.lstat(), windows=windows, check_budget=check_budget)
        else:
            descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            stack.callback(os.close, descriptor)
            check_budget()
            current = Entry(root, os.fstat(descriptor), fd=descriptor, check_budget=check_budget)
        check_budget()
        permissions.check(root, ancestor=True)
        for part in parts[1:]:
            check_budget()
            current = stack.enter_context(child(current, part))
            check_budget()
            permissions.check(current.path, ancestor=True)
        check_budget()
        permissions.check(path, private=True)
        check_budget()
        yield current
