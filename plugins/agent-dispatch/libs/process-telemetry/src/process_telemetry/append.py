"""Single-operation append for synchronous diagnostic spools."""

from __future__ import annotations

import errno
import os

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
