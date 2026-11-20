"""Bounded registry snapshots and private atomic record replacement."""

from __future__ import annotations

import base64
from collections.abc import Callable
import contextlib
import os
from pathlib import Path
import re
import sys
from typing import BinaryIO
import uuid


def _open_snapshot(path: Path) -> BinaryIO:
    if sys.platform != "win32":
        return path.open("rb")
    import ctypes
    from ctypes import wintypes
    import msvcrt

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # A read-only snapshot must allow the canonical writer to replace its name.
    # OPEN_REPARSE_POINT leaves indirection visible to the caller's identity check.
    handle = kernel.CreateFileW(str(path), 0x80000000, 7, None, 3, 0x00200080, None)
    invalid = ctypes.c_void_p(-1).value
    if handle is None or handle == invalid:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY | os.O_NOINHERIT)
    except BaseException:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())
        raise
    try:
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def read_record(
    path: Path, *, plain: Callable[[Path], os.stat_result], maximum: int, identity: re.Pattern[str],
) -> dict[str, str]:
    before = plain(path)
    with _open_snapshot(path) as stream:
        if not os.path.samestat(before, os.fstat(stream.fileno())):
            raise OSError("daemon instance record replaced")
        raw = stream.read(maximum + 1)
    if len(raw) > maximum:
        raise ValueError("daemon instance record exceeds its bound")
    record: dict[str, str] = {}
    for line in raw.splitlines():
        key, value = line.split(b"\t")
        name = key.decode("ascii")
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", name) or name in record:
            raise ValueError("invalid daemon instance field")
        record[name] = base64.b64decode(value, validate=True).decode("utf-8", errors="surrogateescape")
    if (
        record.get("schema") not in ("1", "2") or record.get("instance_id") != path.stem
        or not identity.fullmatch(record.get("instance_id", ""))
    ):
        raise ValueError("invalid daemon instance record identity")
    return record


def _replace_snapshot(staging: Path, path: Path) -> None:
    if sys.platform != "win32":
        os.replace(staging, path)
        return
    import ctypes
    from ctypes import wintypes

    encoded = str(path.absolute()).encode("utf-16-le")

    class RenameInformation(ctypes.Structure):
        _fields_ = [
            ("flags", wintypes.DWORD),
            ("root", wintypes.HANDLE),
            ("length", wintypes.DWORD),
            ("name", wintypes.WCHAR * (len(encoded) // 2 + 1)),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.SetFileInformationByHandle.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
    ]
    kernel.SetFileInformationByHandle.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    handle = kernel.CreateFileW(str(staging), 0x10000, 0, None, 3, 0x00200080, None)
    if handle is None or handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        information = RenameInformation()
        # FileRenameInfoEx: atomic replacement while old read snapshots remain
        # valid. MoveFileEx/os.replace refuses an open destination even with
        # delete sharing. Neither unlink-first nor in-place writes are safe.
        information.flags = 3  # REPLACE_IF_EXISTS | POSIX_SEMANTICS
        information.length = len(encoded)
        ctypes.memmove(
            ctypes.addressof(information) + RenameInformation.name.offset, encoded, len(encoded),
        )
        if not kernel.SetFileInformationByHandle(
            handle, 22, ctypes.byref(information), ctypes.sizeof(information),
        ):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        if not kernel.CloseHandle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def write_record(
    path: Path, record: dict[str, str], *, plain: Callable[[Path], os.stat_result],
    maximum: int, open_private: Callable[[Path, int, int], int],
) -> None:
    data = b"".join(
        key.encode("ascii") + b"\t" + base64.b64encode(
            str(value).encode("utf-8", errors="surrogateescape")
        ) + b"\n"
        for key, value in sorted(record.items())
    )
    if len(data) > maximum:
        raise ValueError("daemon instance record exceeds its bound")
    staging = path.with_name(path.name + "." + uuid.uuid4().hex + ".pending")
    try:
        fd = open_private(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(path):
            plain(path)
        _replace_snapshot(staging, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            staging.unlink()


def read_cursor(
    path: Path, *, plain: Callable[[Path], os.stat_result], header: bytes, identity: re.Pattern[str],
) -> str:
    try:
        info = plain(path)
    except FileNotFoundError:
        return ""
    with _open_snapshot(path) as stream:
        if not os.path.samestat(info, os.fstat(stream.fileno())):
            raise OSError("inventory cursor replaced")
        raw = stream.read(97)
    if not raw.startswith(header) or not raw.endswith(b"\n"):
        raise ValueError("invalid inventory cursor")
    key = raw[len(header):-1].decode("ascii")
    if key and not identity.fullmatch(key):
        raise ValueError("invalid inventory cursor")
    return key


def write_cursor(
    path: Path, key: str, *, plain: Callable[[Path], os.stat_result], header: bytes,
    open_private: Callable[[Path, int, int], int],
) -> None:
    if os.path.lexists(path):
        plain(path)
    pending = path.parent / (".reconcile." + uuid.uuid4().hex + ".pending")
    try:
        fd = open_private(pending, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(header + key.encode("ascii") + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(path):
            plain(path)
        _replace_snapshot(pending, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            pending.unlink()
