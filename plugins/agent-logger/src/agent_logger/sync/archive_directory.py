"""Directory-bound I/O for additive process-log archive publication."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import sys
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from agent_logger.sync.provenance import (
    is_link_or_reparse, open_regular_no_follow, windows_extended_path,
)


class ArchiveRootChangedError(ValueError):
    """The source root was replaced while its canonical ancestry was being pinned."""


def _posix_flags(*names: str) -> int:
    if sys.platform == "win32":
        raise OSError("directory-relative file reads require POSIX")
    flags = os.O_RDONLY
    for name in names:
        value = getattr(os, name, None)
        if value is None:
            raise OSError(errno.ENOTSUP, f"safe archive I/O requires {name}")
        flags |= value
    return flags


def _posix_file_flags() -> int:
    return _posix_flags("O_NOFOLLOW", "O_NONBLOCK")


@contextmanager
def _windows_directory(path: Path) -> Iterator[None]:
    if sys.platform != "win32":
        raise OSError("native directory handles require Windows")
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    info = kernel.GetFileInformationByHandleEx
    info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    info.restype = wintypes.BOOL

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    # READ|WRITE sharing permits child updates, but denies directory renames.
    handle = create(windows_extended_path(path), 0x80000000, 3, None, 3, 0x02200000, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        tag = AttributeTag()
        if not info(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)):
            raise ctypes.WinError(ctypes.get_last_error())
        if tag.attributes & 0x400 and tag.tag & 0x20000000:
            raise OSError(errno.ELOOP, "redirected archive directory rejected", str(path))
        if not tag.attributes & 0x10:
            raise NotADirectoryError(str(path))
        yield
    finally:
        if not close(handle):
            raise ctypes.WinError(ctypes.get_last_error())


@dataclass(frozen=True)
class ArchiveDirectory:
    """All names are relative to one pinned, initially validated directory."""

    path: Path
    fd: int | None

    @staticmethod
    def _name(name: str) -> str:
        if name in ("", ".", "..") or "/" in name or "\\" in name or "\0" in name:
            raise ValueError("archive entry must be a bare filename")
        return name

    def names(self) -> list[str]:
        with os.scandir(self.path if self.fd is None else self.fd) as entries:
            return sorted(entry.name for entry in entries)

    def stat(self, name: str) -> os.stat_result:
        name = self._name(name)
        if self.fd is None:
            return (self.path / name).lstat()
        return os.stat(name, dir_fd=self.fd, follow_symlinks=False)

    def open(self, name: str, *, protected: bool = False) -> BinaryIO:
        name = self._name(name)
        if self.fd is None:
            return open_regular_no_follow(self.path / name, allow_writes=not protected)
        fd = os.open(name, _posix_file_flags(), dir_fd=self.fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            raise ValueError("archive entry is not a regular file")
        return os.fdopen(fd, "rb")

@contextmanager
def archive_directory(path: Path) -> Iterator[ArchiveDirectory]:
    """Pin the resolved real root, allowing intentionally relocated ancestors.

    Resolve once, then pin every canonical ancestor. POSIX operations use only
    the final directory descriptor; Windows pins ancestor names without DELETE
    sharing because its child operations remain pathname-based.
    """
    requested = path.absolute()
    before = requested.lstat()
    if not stat.S_ISDIR(before.st_mode) or is_link_or_reparse(requested, before.st_mode):
        raise ValueError("process-log archive source must be a real directory")
    canonical = requested.resolve(strict=True)
    with ExitStack() as stack:
        fd: int | None = None
        components = [canonical.anchor, *canonical.parts[1:]]
        if sys.platform == "win32":
            for native_component in (*reversed(canonical.parents), canonical):
                stack.enter_context(_windows_directory(native_component))
            opened = canonical.stat()
        else:
            flags = _posix_flags("O_DIRECTORY", "O_NOFOLLOW")
            for component in components:
                fd = os.open(component, flags, dir_fd=fd)
                stack.callback(os.close, fd)
            if fd is None:
                raise OSError("missing pinned archive directory descriptor")
            opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ArchiveRootChangedError("process-log archive root changed while opening")
        yield ArchiveDirectory(canonical, fd)
