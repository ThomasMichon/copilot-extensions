"""Durable routing writes, including Windows write-through rename."""

from __future__ import annotations

import ctypes
import json
import os
import sys
import time
from pathlib import Path


class PublicationCommitUncertain(OSError):
    """Replacement was visible before its durability could be confirmed."""


def windows_replace(source: Path, target: Path, kernel32=None) -> None:
    if kernel32 is None:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    move = kernel32.MoveFileExW
    move.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_ulong]
    move.restype = ctypes.c_int
    # REPLACE_EXISTING | WRITE_THROUGH: publication must survive a power loss.
    if not move(str(source), str(target), 0x1 | 0x8):
        raise ctypes.WinError(ctypes.get_last_error())


def write(path: Path, data: dict) -> None:
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
    if sys.platform == "linux":
        try:
            descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
        except OSError:
            raise PublicationCommitUncertain(
                "routing replacement visible but durability unconfirmed"
            ) from None
