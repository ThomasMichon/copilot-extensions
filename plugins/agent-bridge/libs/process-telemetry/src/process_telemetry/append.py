"""Single-operation append for synchronous diagnostic spools."""

from __future__ import annotations

import errno
import os
import stat
import sys
from pathlib import Path

STRICT_RECORD_BYTES = 64 * 1024


def strict_write_capability() -> str | None:
    """Report this implementation's explicit sync protocol, not storage readiness."""
    if sys.platform == "linux" and all(
        hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK", "fsync")
    ):
        return "linux-file-directory-fsync-v1"
    return None

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _kernel.CreateFileW.argtypes = (
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    )
    _kernel.CreateFileW.restype = wintypes.HANDLE
    _kernel.WriteFile.argtypes = (
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    )
    _kernel.WriteFile.restype = wintypes.BOOL
    _kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    _kernel.CloseHandle.restype = wintypes.BOOL


def append_bytes(path: str | os.PathLike[str], data: bytes) -> None:
    """Append one pre-encoded record, without buffering or a suffix retry.

    Concurrent local-file writers use kernel append positioning. A failed or
    short write can leave a partial record: this is not a transaction, durable
    delivery, network-filesystem guarantee, or repair of an existing bad tail.
    """
    if os.name == "nt":
        _append_windows(path, data)
    else:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o666)
        try:
            written = os.write(fd, data)
            _check_written(written, len(data))
        finally:
            os.close(fd)


def append_bytes_strict(path: str | os.PathLike[str], data: bytes) -> None:
    """One bounded append followed by file and containing-directory sync.

    The owner supplies an existing persistent directory and excludes rotation
    for the whole call. No suffix retry or rollback: failure can leave a complete
    or partial record, but never grants permission to proceed with an effect.
    """
    if strict_write_capability() is None:
        raise NotImplementedError("strict diagnostic sync is unavailable on this platform")
    if type(data) is not bytes or not 0 < len(data) <= STRICT_RECORD_BYTES:
        raise ValueError("strict diagnostic record must be 1..65536 encoded bytes")
    target = Path(path)
    directory = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fd = os.open(
            target.name, os.O_WRONLY | os.O_CREAT | os.O_APPEND | os.O_NOFOLLOW
            | os.O_NONBLOCK, 0o600, dir_fd=directory,
        )
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError("strict diagnostic data path is not an exclusive plain file")
            _check_written(os.write(fd, data), len(data))
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(directory)
    finally:
        os.close(directory)


def _check_written(written: int, expected: int) -> None:
    if written != expected:
        raise OSError(errno.EIO, "short diagnostic spool append; partial record may remain")


def _append_windows(path: str | os.PathLike[str], data: bytes) -> None:
    if len(data) > 0xFFFFFFFF:
        raise OverflowError("diagnostic spool record exceeds WriteFile size")
    name = os.fspath(path)
    if "\0" in name:
        raise ValueError("embedded null character")
    # FILE_APPEND_DATA, not GENERIC_WRITE: the kernel positions each write at
    # EOF, unlike the CRT's O_APPEND seek-then-write across independent handles.
    handle = _kernel.CreateFileW(name, 0x0004, 0x0007, None, 4, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        written = wintypes.DWORD()
        if not _kernel.WriteFile(handle, data, len(data), ctypes.byref(written), None):
            raise ctypes.WinError(ctypes.get_last_error())
        _check_written(written.value, len(data))
    finally:
        if not _kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
