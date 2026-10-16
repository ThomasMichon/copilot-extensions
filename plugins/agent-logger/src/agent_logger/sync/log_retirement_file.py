"""Exact-file retirement boundary; no collection or retention policy is enabled."""

from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager, nullcontext
from dataclasses import dataclass
from typing import BinaryIO

from agent_logger.sync.archive_directory import ArchiveDirectory
from agent_logger.sync.log_archives import _fingerprint, _hash_stream, _LOG_NAME
from agent_logger.sync.provenance import windows_extended_path


@dataclass(frozen=True)
class LogRetirementRevision:
    """The source revision whose replacement the caller must independently verify."""

    fingerprint: tuple[int, int, int, int, int]
    sha256: str


@dataclass
class RetirementFile:
    """Keep the original file exclusively open until explicit disposition commit.

    This is only a file boundary. The caller must first verify the local archive,
    every required target, retention policy and exited producer. No caller is
    wired into sync until those independent policy gates are implemented.
    """

    stream: BinaryIO
    _delete_by_handle: Callable[[], None]
    _committed: bool = False

    @property
    def committed(self) -> bool:
        """Disposition was accepted, even if a later acknowledgement fails."""
        return self._committed

    def retire(
        self, revision: LogRetirementRevision, *,
        commit_guard: AbstractContextManager[None] | None = None,
    ) -> None:
        """Mark only the held, unchanged original for deletion on context exit."""
        if self._committed:
            raise RuntimeError("process-log retirement already committed")
        opened = os.fstat(self.stream.fileno())
        if _fingerprint(opened) != revision.fingerprint:
            raise ValueError("process-log retirement revision changed")
        self.stream.seek(0)
        if _hash_stream(self.stream, opened.st_size) != revision.sha256:
            raise ValueError("process-log retirement content changed")
        if _fingerprint(os.fstat(self.stream.fileno())) != revision.fingerprint:
            raise ValueError("process-log changed during retirement verification")
        # Policy must still hold after the potentially long final content hash.
        with commit_guard if commit_guard is not None else nullcontext():
            if _fingerprint(os.fstat(self.stream.fileno())) != revision.fingerprint:
                raise ValueError("process-log changed at retirement commit")
            self._delete_by_handle()
            self._committed = True


@contextmanager
def retirement_file(directory: ArchiveDirectory, name: str) -> Iterator[RetirementFile]:
    """Open an exclusive original under a caller-held pinned directory boundary.

    Windows sharing exclusion covers both pre-existing and new writable handles
    and prevents rename/replacement through commit. Deletion uses the held
    original handle, never stat followed by pathname unlink. Read-only files,
    links, unsupported filesystems and sharing conflicts fail without changing
    attributes or source contents. POSIX has no proven boundary here.
    """
    if not _LOG_NAME.fullmatch(name):
        raise ValueError("process-log retirement requires a raw producer-log filename")
    if os.name != "nt" or directory.fd is not None:
        raise OSError(errno.ENOTSUP, "exact-file process-log retirement requires native Windows")

    import ctypes
    import msvcrt
    from ctypes import wintypes

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    class Disposition(ctypes.Structure):
        _fields_ = [("delete", ctypes.c_ubyte)]

    if ctypes.sizeof(Disposition) != 1:
        raise RuntimeError("unsupported Windows file-disposition ABI")

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    inspect = kernel.GetFileInformationByHandleEx
    inspect.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    inspect.restype = wintypes.BOOL
    set_info = kernel.SetFileInformationByHandle
    set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    set_info.restype = wintypes.BOOL
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL

    # No DELETE_ON_CLOSE: abandoning verification must retain the original.
    handle = create(
        windows_extended_path(directory.path / name),
        0x80000000 | 0x00010000, 0, None, 3, 0x00200080, None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    fd: int | None = None
    try:
        attributes = AttributeTag()
        if not inspect(handle, 9, ctypes.byref(attributes), ctypes.sizeof(attributes)):
            raise ctypes.WinError(ctypes.get_last_error())
        if attributes.attributes & (0x10 | 0x400):
            raise ValueError("process-log retirement source must be a non-reparse regular file")
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
            raise ValueError("process-log retirement source must have exactly one regular-file link")

        def commit() -> None:
            disposition = Disposition(1)
            if not set_info(handle, 4, ctypes.byref(disposition), ctypes.sizeof(disposition)):
                raise ctypes.WinError(ctypes.get_last_error())

        with os.fdopen(fd, "rb", closefd=False) as stream:
            yield RetirementFile(stream, commit)
    finally:
        if fd is not None:
            os.close(fd)
        elif not close(handle):
            raise ctypes.WinError(ctypes.get_last_error())
