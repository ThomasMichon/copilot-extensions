"""Durable routing writes, including Windows write-through rename."""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, TextIO, cast


def _last_error() -> int:
    return int(getattr(ctypes, "get_last_error")())


def _windows_failure(code: int) -> OSError:
    return cast(OSError, getattr(ctypes, "WinError")(code))


def open_reader(path: Path) -> TextIO:
    """Read one snapshot without preventing Windows atomic replacement."""
    if os.name != "nt":
        return path.open("r", encoding="utf-8")
    import msvcrt
    from ctypes import wintypes

    kernel = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    # Read/write/delete sharing leaves replacement independent of reader lifetime.
    handle = create(str(path), 0x80000000, 0x1 | 0x2 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise _windows_failure(_last_error())
    try:
        descriptor = int(getattr(msvcrt, "open_osfhandle")(
            handle, os.O_RDONLY | int(getattr(os, "O_BINARY")),
        ))
    except BaseException:
        close(handle)
        raise
    try:
        return os.fdopen(descriptor, "r", encoding="utf-8")
    except BaseException:
        os.close(descriptor)
        raise


class PublicationCommitUncertain(OSError):
    """Replacement was visible before its durability could be confirmed."""


def windows_replace(source: Path, target: Path, kernel32: Any = None) -> None:
    if kernel32 is None:
        kernel32 = getattr(ctypes, "WinDLL")("kernel32", use_last_error=True)
    move = kernel32.MoveFileExW
    move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    move.restype = ctypes.c_int
    # REPLACE_EXISTING | WRITE_THROUGH: publication must survive a power loss.
    if move(str(source), str(target), 0x1 | 0x8):
        return
    move_error = _last_error()
    if move_error not in (5, 32, 33):
        raise _windows_failure(move_error)
    replace = kernel32.ReplaceFileW
    replace.argtypes = [
        ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong,
        ctypes.c_void_p, ctypes.c_void_p,
    ]
    replace.restype = ctypes.c_int
    if not replace(str(target), str(source), None, 0, None, None):
        error = _last_error()
        if error in (2, 3):
            raise _windows_failure(move_error)
        if error in (1175, 1176, 1177):
            raise PublicationCommitUncertain(
                "routing replacement failed with an uncertain namespace outcome"
            )
        raise _windows_failure(error)
    try:
        # ReplaceFile permits delete-sharing readers; its write-through flag
        # is unsupported, so explicitly flush the replacement before success.
        with target.open("r+b") as handle:
            os.fsync(handle.fileno())
    except OSError:
        raise PublicationCommitUncertain(
            "routing replacement visible but durability unconfirmed"
        ) from None


def write(path: Path, data: dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=False)
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(20):
        try:
            if sys.platform == "win32":
                windows_replace(tmp, path)
            else:
                os.replace(tmp, path)
            break
        except OSError as exc:
            if getattr(exc, "winerror", None) not in (5, 32, 33) or attempt == 19:
                raise
            time.sleep(0.05)
    if sys.platform in ("linux", "darwin"):
        try:
            descriptor = os.open(path.parent, os.O_RDONLY | int(getattr(os, "O_DIRECTORY")))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            raise PublicationCommitUncertain(
                "routing replacement visible but durability unconfirmed"
            ) from None
