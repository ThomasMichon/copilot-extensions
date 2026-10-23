"""Per-write records in the existing local form-draft directory."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import uuid

_PENDING_DIRECTORY = ".worktree-manager-publication-pending"
_PENDING_CONTENT = b"worktree-manager.directory-publication.v1\n"


def _windows_move(source, destination, *, replace: bool) -> None:
    import ctypes
    from ctypes import wintypes

    move = ctypes.WinDLL("kernel32", use_last_error=True).MoveFileExW
    move.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    move.restype = wintypes.BOOL
    # WRITE_THROUGH publishes the name on disk before returning, including directories.
    if not move(str(source), str(destination), 0x8 | (0x1 if replace else 0)):
        raise ctypes.WinError(ctypes.get_last_error())


def _windows_flush_directory(path: Path) -> None:
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
        wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.FlushFileBuffers.argtypes = [wintypes.HANDLE]
    kernel.FlushFileBuffers.restype = wintypes.BOOL
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    # A directory flush requires write access plus BACKUP_SEMANTICS, not
    # administrative volume access. Unsupported filesystems fail closed.
    handle = kernel.CreateFileW(str(path), 0x40000000, 0x7, None, 3, 0x02000000, None)
    if handle == wintypes.HANDLE(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        if not kernel.FlushFileBuffers(handle):
            raise ctypes.WinError(ctypes.get_last_error())
    finally:
        kernel.CloseHandle(handle)


def _posix_move_directory(source: Path, destination: Path) -> None:
    import ctypes

    library = ctypes.CDLL(None, use_errno=True)
    paths = (os.fsencode(source), os.fsencode(destination))
    try:
        if sys.platform == "darwin":
            move = library.renamex_np
            move.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            arguments = (*paths, 0x4)  # RENAME_EXCL
        elif sys.platform.startswith("linux"):
            move = library.renameat2
            move.argtypes = [
                ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
            ]
            arguments = (-100, paths[0], -100, paths[1], 0x1)  # RENAME_NOREPLACE
        else:
            raise AttributeError
    except AttributeError:
        raise OSError(errno.ENOTSUP, "Exclusive directory publication is unavailable", str(destination)) from None
    move.restype = ctypes.c_int
    if move(*arguments) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), str(destination))


def _ensure_directory(path: Path) -> None:
    if path.is_dir():
        pending = path / _PENDING_DIRECTORY
        try:
            content = pending.read_bytes()
        except FileNotFoundError:
            return
        if content != _PENDING_CONTENT:
            raise OSError(f"Unrecognized directory publication marker: {pending}")
        _sync_directory(path.parent)
        pending.unlink(missing_ok=True)
        return
    if path.parent == path:
        raise FileNotFoundError(errno.ENOENT, "Draft directory root is unavailable", str(path))
    _ensure_directory(path.parent)
    temporary = Path(tempfile.mkdtemp(dir=path.parent))
    pending = temporary / _PENDING_DIRECTORY
    try:
        # Persist uncertainty before the rename: a failed publication barrier
        # may still leave the destination name visible, even after restart.
        with pending.open("wb") as stream:
            stream.write(_PENDING_CONTENT)
            stream.flush()
            os.fsync(stream.fileno())
        _sync_directory(temporary)
        try:
            if os.name == "nt":
                _windows_move(temporary, path, replace=False)
            else:
                _posix_move_directory(temporary, path)
                _sync_directory(path.parent)
        except FileExistsError:
            if not path.is_dir():
                raise
            _ensure_directory(path)
        else:
            (path / _PENDING_DIRECTORY).unlink(missing_ok=True)
    finally:
        if temporary.exists():
            pending.unlink(missing_ok=True)
            temporary.rmdir()


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        _windows_flush_directory(path)
        return
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def write_draft(base: Path, binding, task_id, values, *, pending: bool) -> Path:
    path = base if binding is None else base.with_stem(f"{base.stem}-{uuid.uuid4().hex}")
    write_record(path, {
        "task_id": task_id, "binding": binding, "values": values,
        "pending": pending, "written_at_ns": time.time_ns(),
    })
    return path


def write_record(path: Path, record: dict) -> None:
    path = path.absolute()
    temporary = None
    try:
        _ensure_directory(path.parent)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = stream.name
            json.dump(record, stream, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name == "nt":
            _windows_move(temporary, path, replace=True)
        else:
            os.replace(temporary, path)
            _sync_directory(path.parent)
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def read_drafts(base: Path, binding) -> list[tuple[Path, dict]]:
    records = []
    paths = [base] if binding is None else base.parent.glob(f"{base.stem}-*.json")
    for path in paths:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if binding is not None and isinstance(record, dict) and not {"pending", "written_at_ns"} <= record.keys():
                continue
            if (isinstance(record, dict) and isinstance(record.get("values"), dict)
                    and record.get("binding") == binding
                    and type(record.get("written_at_ns", 0)) is int
                    and type(record.get("pending", False)) is bool):
                records.append((path, record))
        except (OSError, ValueError):
            continue
    return sorted(records, key=lambda item: (item[1].get("written_at_ns", 0), item[0].name), reverse=True)
