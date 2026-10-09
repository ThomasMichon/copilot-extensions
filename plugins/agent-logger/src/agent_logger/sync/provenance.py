"""Generic per-session provenance sidecars transported by session-sync."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import stat
import uuid
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
MAX_PROVENANCE_BYTES = 1024 * 1024
RESCUE_SNAPSHOT_PROVENANCE = ".rescue-provenance.json"

# Hex chars of a SHA-256 digest kept for a rescue-snapshot directory name.
# 16 hex chars (64 bits) is ample collision resistance for a per-machine
# cache keyed by session/capture id -- a full 64-char digest (two of them,
# nested) was the single largest contributor to a real Windows MAX_PATH
# (260-char) failure under a deep snapshot/containment root (see
# coverage-guided-ci effort notes, 2026-10-05). Every reader of a rescue
# snapshot path must derive it the same way, so route through
# rescue_session_key()/rescue_capture_key() below rather than hashing
# inline.
RESCUE_KEY_HEX_LENGTH = 16

# Hex chars of a uuid4 kept for a transaction/temp-file suffix elsewhere in
# session-sync (filesystem.py) -- same MAX_PATH rationale as above; shared
# here since this module already carries the sibling short-ID budget.
SHORT_ID_HEX_LENGTH = 16


def short_unique_id() -> str:
    return uuid.uuid4().hex[:SHORT_ID_HEX_LENGTH]


_REQUIRED_TEXT = {
    "session_id",
    "provider",
    "venue_kind",
    "venue_id",
    "target_id",
    "capture_id",
    "captured_at",
    "billing_scope",
}
_OPTIONAL_TEXT = {
    "container_instance",
    "container_generation",
    "fleet",
    "repository",
    "source_repo",
    "interface",
    "origin",
    "source",
    "model",
}


def windows_extended_path(path: Path) -> str:
    """Return a Win32 extended path for filesystem operations."""
    raw = os.path.abspath(os.fspath(path))
    if os.name != "nt" or raw.startswith("\\\\?\\"):
        return raw
    if raw.startswith("\\\\"):
        return f"\\\\?\\UNC\\{raw[2:]}"
    return f"\\\\?\\{raw}"


_windows_extended_path = windows_extended_path


def _lstat(path: Path) -> os.stat_result:
    return os.stat(_windows_extended_path(path), follow_symlinks=False)


def _mkdir(path: Path) -> None:
    os.mkdir(_windows_extended_path(path))


def fsync_directory(path: Path) -> None:
    """Persist directory-entry changes where the platform exposes that barrier."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(_windows_extended_path(path), flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def durable_replace(source: Path, destination: Path) -> None:
    """Rename with a durable directory-entry barrier.

    Shared by every sync target that lands an authoritative file via
    rename -- a plain ``os.replace`` is atomic but not durable: on POSIX,
    fsyncing the temp file never persists the *directory-entry* change
    itself, and on Windows a write-through move is needed for the same
    guarantee. A power loss right after an unsynced rename can otherwise
    silently revert to the prior (or no) file on next boot.
    """
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        movefile_replace_existing = 0x00000001
        movefile_write_through = 0x00000008
        move_file = ctypes.WinDLL(
            "kernel32",
            use_last_error=True,
        ).MoveFileExW
        move_file.argtypes = [
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
        ]
        move_file.restype = wintypes.BOOL
        if not move_file(
            _windows_extended_path(source),
            _windows_extended_path(destination),
            movefile_replace_existing | movefile_write_through,
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return
    source_parent = source.parent
    destination_parent = destination.parent
    os.replace(_windows_extended_path(source), _windows_extended_path(destination))
    fsync_directory(source_parent)
    if destination_parent != source_parent:
        fsync_directory(destination_parent)


def rescue_session_key(session_id: str) -> str:
    """Return the (truncated) directory key for one session's rescue
    snapshots. Every writer/reader must derive this the same way -- see
    RESCUE_KEY_HEX_LENGTH."""
    return hashlib.sha256(session_id.encode()).hexdigest()[:RESCUE_KEY_HEX_LENGTH]


def rescue_capture_key(capture_id: str) -> str:
    """Return the (truncated) directory key for one rescued capture. Every
    writer/reader must derive this the same way -- see
    RESCUE_KEY_HEX_LENGTH."""
    return hashlib.sha256(capture_id.encode()).hexdigest()[:RESCUE_KEY_HEX_LENGTH]


def rescue_snapshot_path(
    machine_root: Path,
    session_id: str,
    capture_id: str,
) -> Path:
    """Return the hidden immutable snapshot path for one rescued capture."""
    session_key = rescue_session_key(session_id)
    capture_key = rescue_capture_key(capture_id)
    return (
        machine_root
        / ".session-sync-rescue-captures"
        / session_key
        / capture_key
    )


def is_link_or_reparse(path: Path, mode: int) -> bool:
    """Return whether *path* redirects name resolution outside its location.

    Windows cloud placeholders are reparse points but not name surrogates; they
    are safe to hydrate. Symlinks and mount points carry the name-surrogate bit
    and are rejected.
    """
    if stat.S_ISLNK(mode):
        return True
    if platform.system() != "Windows":
        return False

    import ctypes
    from ctypes import wintypes

    file_share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    file_flag_open_reparse_point = 0x00200000
    file_flag_backup_semantics = 0x02000000
    file_attribute_tag_info = 9
    file_attribute_reparse_point = 0x00000400
    io_reparse_tag_name_surrogate = 0x20000000

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

    handle = create_file(
        _windows_extended_path(path),
        0,
        file_share_all,
        None,
        open_existing,
        file_flag_open_reparse_point | file_flag_backup_semantics,
        None,
    )
    if handle == wintypes.HANDLE(-1).value:
        return True
    try:
        info = FileAttributeTagInfo()
        if not get_info(
            handle,
            file_attribute_tag_info,
            ctypes.byref(info),
            ctypes.sizeof(info),
        ):
            return True
        if not (info.FileAttributes & file_attribute_reparse_point):
            return False
        return bool(info.ReparseTag & io_reparse_tag_name_surrogate)
    finally:
        close_handle(handle)


def anchored_path(path: Path) -> Path:
    """Return an absolute lexical path without resolving links."""
    return path.expanduser().absolute()


def existing_real_directory(path: Path) -> Path | None:
    """Resolve an existing directory only through real directory components."""
    absolute = anchored_path(path)
    current = Path(absolute.anchor)
    try:
        anchor_mode = _lstat(current).st_mode
    except FileNotFoundError:
        return None
    if is_link_or_reparse(current, anchor_mode) or not stat.S_ISDIR(anchor_mode):
        return None
    for part in absolute.parts[1:]:
        current /= part
        try:
            mode = _lstat(current).st_mode
        except FileNotFoundError:
            return None
        if is_link_or_reparse(current, mode) or not stat.S_ISDIR(mode):
            return None
    return absolute


def ensure_real_directory(path: Path) -> Path:
    """Create a directory only through real directory components."""
    absolute = anchored_path(path)
    current = Path(absolute.anchor)
    anchor_mode = _lstat(current).st_mode
    if is_link_or_reparse(current, anchor_mode) or not stat.S_ISDIR(anchor_mode):
        raise OSError(f"directory is unsafe: {current}")
    for part in absolute.parts[1:]:
        current /= part
        try:
            mode = _lstat(current).st_mode
        except FileNotFoundError:
            _mkdir(current)
            mode = _lstat(current).st_mode
        if is_link_or_reparse(current, mode) or not stat.S_ISDIR(mode):
            raise OSError(f"directory is unsafe: {current}")
    return absolute


def open_regular_no_follow(path: Path):
    """Open an existing regular file for binary reading without following links."""
    if platform.system() != "Windows":
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(path, flags)
        opened = os.fstat(fd)
        if not stat.S_ISREG(opened.st_mode):
            os.close(fd)
            raise OSError(f"file is not regular: {path}")
        return os.fdopen(fd, "rb")

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_read = 0x80000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
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
        _windows_extended_path(path),
        generic_read,
        share_all,
        None,
        open_existing,
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
        raise OSError(f"file is not regular: {path}")
    if (
        info.FileAttributes & file_attribute_reparse_point
        and info.ReparseTag & io_reparse_tag_name_surrogate
    ):
        close_handle(handle)
        raise OSError(f"file is not regular: {path}")
    if info.FileAttributes & file_attribute_reparse_point:
        close_handle(handle)
        handle = create_file(
            _windows_extended_path(path),
            generic_read,
            share_all,
            None,
            open_existing,
            file_attribute_normal,
            None,
        )
        if handle == wintypes.HANDLE(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY)
    except OSError:
        close_handle(handle)
        raise
    return os.fdopen(fd, "rb")


def existing_rescue_snapshot_path(
    machine_root: Path,
    session_id: str,
    capture_id: str,
) -> Path | None:
    """Resolve a rescue snapshot only through real directory components."""
    snapshot = rescue_snapshot_path(machine_root, session_id, capture_id)
    current = existing_real_directory(machine_root)
    if current is None:
        return None
    for part in snapshot.relative_to(machine_root).parts:
        current /= part
        try:
            mode = _lstat(current).st_mode
        except OSError:
            return None
        if is_link_or_reparse(current, mode) or not stat.S_ISDIR(mode):
            return None
    return snapshot


def read_provenance_file(path: Path, session_id: str) -> dict[str, Any] | None:
    """Read one valid provenance file through a no-follow handle."""
    try:
        mode = _lstat(path).st_mode
    except OSError:
        return None
    if is_link_or_reparse(path, mode) or not stat.S_ISREG(mode):
        return None
    try:
        with open_regular_no_follow(path) as stream:
            opened = os.fstat(stream.fileno())
            if opened.st_size > MAX_PROVENANCE_BYTES:
                return None
            raw = stream.read(MAX_PROVENANCE_BYTES + 1)
        if len(raw) > MAX_PROVENANCE_BYTES:
            return None
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        return None
    if payload.get("session_id") != session_id:
        return None
    for key in _REQUIRED_TEXT:
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            return None
    for key in _OPTIONAL_TEXT:
        value = payload.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            return None
    members = payload.get("members")
    if members is not None and not isinstance(members, dict):
        return None
    return payload


def read_provenance(machine_root: Path, session_id: str) -> dict[str, Any] | None:
    """Read a valid v1 ``provenance/<session-id>.json`` sidecar.

    Unknown fields are ignored by consumers. A missing sidecar, unsupported
    schema, symlink, malformed JSON, or invalid known field returns ``None``.
    """
    try:
        root_mode = _lstat(machine_root).st_mode
        provenance_dir = machine_root / "provenance"
        provenance_mode = _lstat(provenance_dir).st_mode
    except OSError:
        return None
    if (
        is_link_or_reparse(machine_root, root_mode)
        or not stat.S_ISDIR(root_mode)
        or is_link_or_reparse(provenance_dir, provenance_mode)
        or not stat.S_ISDIR(provenance_mode)
    ):
        return None
    path = provenance_dir / f"{session_id}.json"
    return read_provenance_file(path, session_id)
