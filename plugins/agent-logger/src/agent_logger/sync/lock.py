"""Cross-platform advisory file lock for serialized syncs.

Ported from the multi-machine system session-sync engine: ``msvcrt`` on Windows,
``fcntl`` on POSIX, with a timeout. This is the same locking the
orchestrator's merge queue needs -- kept here so the sync engine never
runs two pushes against the same target concurrently.
"""

from __future__ import annotations

import contextlib
import os
import platform
import stat
import time
from collections.abc import Iterator
from pathlib import Path

from agent_logger.sync.provenance import ensure_real_directory

IS_WINDOWS = platform.system() == "Windows"


def _open_lock_file(lock_file: Path):
    """Open/create a regular lock file without following links/reparse points."""
    if not IS_WINDOWS:
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(lock_file, flags, 0o600)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            os.close(fd)
            raise OSError(f"sync lock is not a regular file: {lock_file}")
        return os.fdopen(fd, "r+")

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    generic_write = 0x40000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_always = 4
    file_attribute_normal = 0x00000080
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
    io_reparse_tag_name_surrogate = 0x20000000
    file_flag_open_reparse_point = 0x00200000
    file_attribute_tag_info = 9

    class FileAttributeTagInfo(ctypes.Structure):
        _fields_ = [
            ("FileAttributes", wintypes.DWORD),
            ("ReparseTag", wintypes.DWORD),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_file = kernel32.CreateFileW
    create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    create_file.restype = wintypes.HANDLE
    get_info = kernel32.GetFileInformationByHandleEx
    get_info.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    get_info.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL

    handle = create_file(
        str(lock_file),
        generic_read | generic_write,
        share_all,
        None,
        open_always,
        file_attribute_normal | file_flag_open_reparse_point,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    info = FileAttributeTagInfo()
    if not get_info(
        handle,
        file_attribute_tag_info,
        ctypes.byref(info),
        ctypes.sizeof(info),
    ):
        error = ctypes.WinError(ctypes.get_last_error())
        close_handle(handle)
        raise error
    if info.FileAttributes & file_attribute_directory:
        close_handle(handle)
        raise OSError(f"sync lock is not a regular file: {lock_file}")
    if (
        info.FileAttributes & file_attribute_reparse_point
        and info.ReparseTag & io_reparse_tag_name_surrogate
    ):
        close_handle(handle)
        raise OSError(f"sync lock is not a regular file: {lock_file}")
    if info.FileAttributes & file_attribute_reparse_point:
        close_handle(handle)
        handle = create_file(
            str(lock_file),
            generic_read | generic_write,
            share_all,
            None,
            open_always,
            file_attribute_normal,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR)
    except OSError:
        close_handle(handle)
        raise
    return os.fdopen(fd, "r+")


@contextlib.contextmanager
def sync_lock(lock_file: Path, timeout: int = 10, wait: bool = True) -> Iterator[bool]:
    """Yield ``True`` if the lock was acquired, ``False`` otherwise.

    The lock is always released on exit. ``wait`` with a ``timeout`` retries
    a non-blocking acquire; ``wait=False`` tries once.
    """
    lock_file = ensure_real_directory(lock_file.parent) / lock_file.name
    fh = _open_lock_file(lock_file)
    try:
        if IS_WINDOWS:
            import msvcrt

            deadline = time.monotonic() + (timeout if wait else 0)
            locked = False
            while True:
                try:
                    msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
                    locked = True
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        break
                    time.sleep(0.5)
            if not locked:
                yield False
                return
        else:
            import fcntl

            deadline = time.monotonic() + (timeout if wait else 0)
            while True:
                try:
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        yield False
                        return
                    time.sleep(0.5)
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        yield True
    finally:
        try:
            if IS_WINDOWS:
                import msvcrt

                with contextlib.suppress(OSError):
                    msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh, fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass
        fh.close()
