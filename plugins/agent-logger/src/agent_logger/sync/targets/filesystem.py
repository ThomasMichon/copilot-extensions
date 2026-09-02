"""Filesystem sync targets: ``local`` and ``onedrive``.

Both publish the source tree into ``<root>/<machine>/`` via an incremental
copy (size + mtime delta). They differ only in how the root is resolved:

- ``local`` -- a dotfolder under ``$HOME`` (default
  ``~/.agent-logger/sessions``), or an explicit ``path``.
- ``onedrive`` -- a ``subfolder`` under the OS-resolved OneDrive root, which
  turns a OneDrive folder into a NAS-equivalent fleet aggregation point.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from agent_logger import sessions
from agent_logger.sessions import SessionRef
from agent_logger.sync.lock import sync_lock
from agent_logger.sync.meta import write_sync_meta
from agent_logger.sync.provenance import (
    MAX_PROVENANCE_BYTES,
    RESCUE_SNAPSHOT_PROVENANCE,
    existing_rescue_snapshot_path,
    is_link_or_reparse,
    open_regular_no_follow,
    rescue_snapshot_path,
    windows_extended_path as _windows_extended_path,
)
from agent_logger.sync.targets.base import DoctorResult, PushResult, Target

#: Files never copied to a destination (session lock sidecars, temp files).
_EXCLUDE_NAMES = frozenset({".lock", "lock"})

#: Top-level session-index files kept alongside the ``session-state`` tree when
#: no repo allowlist narrows the scope. Everything else under the source (the
#: rest of ~/.copilot: binaries, installed plugins, OAuth/credential state,
#: encryption keys, settings) is never archived.
_SESSION_INDEX_NAMES = frozenset(
    {"session-store.db", "session-store.db-wal", "session-store.db-shm"}
)
_MAX_TRANSACTION_MANIFEST_BYTES = 16 * 1024 * 1024


def _is_excluded_name(name: str) -> bool:
    return name.casefold() in _EXCLUDE_NAMES


def _is_windows_sharing_violation(exc: OSError) -> bool:
    return (
        os.name == "nt"
        and getattr(exc, "winerror", None) in {32, 33}
    )


class _LockedSourceFile(OSError):
    pass


def _rmdir_replace_target(path: Path) -> None:
    """Remove an empty directory, clearing Windows read-only if needed."""
    io_path = _windows_extended_path(path)
    try:
        os.rmdir(io_path)
    except FileNotFoundError:
        return
    except PermissionError:
        try:
            os.chmod(io_path, stat.S_IWRITE | stat.S_IREAD | stat.S_IXUSR)
        except FileNotFoundError:
            return
        try:
            os.rmdir(io_path)
        except FileNotFoundError:
            return


def _unlink_replace_target(path: Path) -> None:
    """Remove a destination before replacement, clearing read-only if needed."""
    io_path = _windows_extended_path(path)
    try:
        os.unlink(io_path)
    except FileNotFoundError:
        return
    except PermissionError:
        try:
            os.chmod(io_path, stat.S_IWRITE | stat.S_IREAD)
        except FileNotFoundError:
            return
        try:
            os.unlink(io_path)
        except FileNotFoundError:
            return


def _fsync_directory(path: Path) -> None:
    """Persist directory-entry changes where the platform exposes that barrier."""
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    fd = os.open(path, flags)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _durable_replace(source: Path, destination: Path) -> None:
    """Rename with a durable directory-entry barrier."""
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
    os.replace(source, destination)
    _fsync_directory(source_parent)
    if destination_parent != source_parent:
        _fsync_directory(destination_parent)


def _write_bytes_fsync(path: Path, payload: bytes) -> None:
    with open(_windows_extended_path(path), "xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    _fsync_directory(path.parent)


def _fsync_regular_file(path: Path) -> None:
    """Flush one staged regular file through a write-capable safe handle."""
    if os.name != "nt":
        with open_regular_no_follow(path) as stream:
            os.fsync(stream.fileno())
        return

    import ctypes
    import msvcrt
    from ctypes import wintypes

    generic_write = 0x40000000
    share_all = 0x00000001 | 0x00000002 | 0x00000004
    open_existing = 3
    file_attribute_normal = 0x00000080
    file_attribute_directory = 0x00000010
    file_attribute_reparse_point = 0x00000400
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

    handle = create_file(
        _windows_extended_path(path),
        generic_write,
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
    if info.FileAttributes & (
        file_attribute_directory | file_attribute_reparse_point
    ):
        close_handle(handle)
        raise OSError(f"cannot fsync unsafe staged path: {path}")
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_WRONLY)
    except OSError:
        close_handle(handle)
        raise
    with os.fdopen(fd, "wb", closefd=True) as stream:
        os.fsync(stream.fileno())


def _fsync_tree(root: Path) -> None:
    """Persist every staged regular file before any publication rename."""
    directories = [root]
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(_windows_extended_path(directory)) as children:
            for child in children:
                path = directory / child.name
                mode = child.stat(follow_symlinks=False).st_mode
                if is_link_or_reparse(path, mode):
                    raise OSError(f"cannot fsync unsafe staged path: {path}")
                if stat.S_ISDIR(mode):
                    directories.append(path)
                    pending.append(path)
                elif stat.S_ISREG(mode):
                    _fsync_regular_file(path)
                else:
                    raise OSError(f"cannot fsync special staged path: {path}")
    for directory in reversed(directories):
        _fsync_directory(directory)


def _copy_replace(src: Path, dst: Path) -> None:
    """Copy one regular source without following links."""
    temporary = dst.with_name(f".{dst.name}.{uuid.uuid4().hex}.tmp")
    temporary_io = _windows_extended_path(temporary)
    try:
        try:
            source = open_regular_no_follow(src)
        except OSError as exc:
            if _is_windows_sharing_violation(exc):
                raise _LockedSourceFile(f"source file is locked: {src}") from exc
            raise
        with source:
            with open(temporary_io, "xb") as target:
                shutil.copyfileobj(source, target, length=1024 * 1024)
                target.flush()
                os.fsync(target.fileno())
        try:
            shutil.copystat(
                _windows_extended_path(src),
                temporary_io,
                follow_symlinks=False,
            )
        except OSError:
            pass
        _unlink_replace_target(dst)
        _durable_replace(temporary, dst)
    finally:
        _unlink_replace_target(temporary)


def _needs_copy(src: Path, dst: Path) -> bool:
    """Copy if the destination is missing, a different size, or older."""
    try:
        mode = _lstat(dst).st_mode
        if is_link_or_reparse(dst, mode) or not stat.S_ISREG(mode):
            return True
        d = os.stat(_windows_extended_path(dst))
    except OSError:
        return True
    s = os.stat(_windows_extended_path(src))
    return s.st_size != d.st_size or s.st_mtime > d.st_mtime + 1e-6


def _same_file_content(src: Path, dst: Path) -> bool:
    """Compare selected files by content when timestamps cannot be trusted."""
    try:
        source_mode = _lstat(src).st_mode
        destination_mode = _lstat(dst).st_mode
        if (
            is_link_or_reparse(src, source_mode)
            or not stat.S_ISREG(source_mode)
            or is_link_or_reparse(dst, destination_mode)
            or not stat.S_ISREG(destination_mode)
        ):
            return False
        source_stat = os.stat(_windows_extended_path(src))
        destination_stat = os.stat(_windows_extended_path(dst))
    except OSError:
        return False
    if source_stat.st_size != destination_stat.st_size:
        return False

    def digest(path: Path) -> bytes:
        value = hashlib.sha256()
        with open_regular_no_follow(path) as stream:
            while chunk := stream.read(1024 * 1024):
                value.update(chunk)
        return value.digest()

    try:
        return digest(src) == digest(dst)
    except OSError:
        return False


def _read_json_regular(path: Path) -> dict | None:
    """Read one bounded regular JSON object without following links."""
    try:
        mode = _lstat(path).st_mode
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OSError(f"cannot inspect lineage record {path}: {exc}") from exc
    if is_link_or_reparse(path, mode) or not stat.S_ISREG(mode):
        raise OSError(f"lineage record is not a regular file: {path}")
    try:
        with open_regular_no_follow(path) as stream:
            opened = os.fstat(stream.fileno())
            if opened.st_size > MAX_PROVENANCE_BYTES:
                raise OSError(f"lineage record is too large: {path}")
            raw = stream.read(MAX_PROVENANCE_BYTES + 1)
        if len(raw) > MAX_PROVENANCE_BYTES:
            raise OSError(f"lineage record is too large: {path}")
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OSError(f"invalid lineage record {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise OSError(f"lineage record must be an object: {path}")
    return payload


def _lineage_from_provenance(
    path: Path,
    session_id: str,
) -> tuple[tuple[float, str], str, dict] | None:
    """Return rescue order, canonical fingerprint, and receipt fields."""
    payload = _read_json_regular(path)
    if payload is None or payload.get("provider") != "agent-containers":
        return None
    capture_id = payload.get("capture_id")
    captured_at = payload.get("captured_at")
    if (
        payload.get("session_id") != session_id
        or not isinstance(capture_id, str)
        or not capture_id
        or not isinstance(captured_at, str)
    ):
        raise OSError(f"invalid rescue lineage: {path}")
    captured = _parse_iso(captured_at)
    if captured is None:
        raise OSError(f"invalid rescue capture timestamp: {path}")
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    fingerprint = hashlib.sha256(canonical).hexdigest()
    receipt = {
        "schema_version": 1,
        "provider": "agent-containers",
        "session_id": session_id,
        "capture_id": capture_id,
        "captured_at": captured_at,
        "provenance_fingerprint": fingerprint,
    }
    return (captured.timestamp(), capture_id), fingerprint, receipt


def _lineage_from_receipt(
    path: Path,
    session_id: str,
) -> tuple[tuple[float, str], str] | None:
    """Read the destination-side rescue high-water receipt."""
    payload = _read_json_regular(path)
    if payload is None:
        return None
    capture_id = payload.get("capture_id")
    captured_at = payload.get("captured_at")
    fingerprint = payload.get("provenance_fingerprint")
    if (
        payload.get("schema_version") != 1
        or payload.get("provider") != "agent-containers"
        or payload.get("session_id") != session_id
        or not isinstance(capture_id, str)
        or not capture_id
        or not isinstance(captured_at, str)
        or not isinstance(fingerprint, str)
        or len(fingerprint) != 64
    ):
        raise OSError(f"invalid rescue high-water receipt: {path}")
    captured = _parse_iso(captured_at)
    if captured is None:
        raise OSError(f"invalid rescue receipt timestamp: {path}")
    return (captured.timestamp(), capture_id), fingerprint


def _snapshot_matches_source(
    source: Path,
    source_provenance: Path,
    snapshot: Path,
) -> bool:
    """Verify immutable snapshot bytes plus its capture provenance."""
    source_files = _session_files(source, source=True)
    destination_files = _session_files(snapshot, source=False)
    snapshot_provenance = destination_files.pop(
        Path(RESCUE_SNAPSHOT_PROVENANCE),
        None,
    )
    if snapshot_provenance is None or source_files.keys() != destination_files.keys():
        return False
    return (
        all(
            _same_file_content(path, destination_files[relative])
            for relative, path in source_files.items()
        )
        and _same_file_content(source_provenance, snapshot_provenance)
    )


def _path_exists(path: Path) -> bool:
    """Return true for normal paths and dangling symlinks."""
    return os.path.lexists(_windows_extended_path(path))


def _lstat(path: Path) -> os.stat_result:
    return os.stat(_windows_extended_path(path), follow_symlinks=False)


def _mkdir(path: Path) -> None:
    os.mkdir(_windows_extended_path(path))


def _is_real_directory(path: Path) -> bool:
    try:
        mode = _lstat(path).st_mode
    except OSError:
        return False
    return stat.S_ISDIR(mode) and not is_link_or_reparse(path, mode)


def _is_real_file(path: Path) -> bool:
    try:
        mode = _lstat(path).st_mode
    except OSError:
        return False
    return stat.S_ISREG(mode) and not is_link_or_reparse(path, mode)


def _anchored_path(path: Path) -> Path:
    """Return an absolute lexical path without resolving links."""
    return path.expanduser().absolute()


def _ensure_real_directory(path: Path) -> Path:
    """Create a directory only through real directory components."""
    absolute = _anchored_path(path)
    current = Path(absolute.anchor)
    anchor_mode = _lstat(current).st_mode
    if is_link_or_reparse(current, anchor_mode) or not stat.S_ISDIR(anchor_mode):
        raise OSError(f"destination directory is unsafe: {current}")
    for part in absolute.parts[1:]:
        current /= part
        try:
            mode = _lstat(current).st_mode
        except FileNotFoundError:
            _mkdir(current)
            mode = _lstat(current).st_mode
        if is_link_or_reparse(current, mode) or not stat.S_ISDIR(mode):
            raise OSError(f"destination directory is unsafe: {current}")
    return absolute


def _existing_real_directory(path: Path) -> Path | None:
    """Resolve an existing directory only through real directory components."""
    absolute = _anchored_path(path)
    current = Path(absolute.anchor)
    try:
        anchor_mode = _lstat(current).st_mode
    except FileNotFoundError:
        return None
    if is_link_or_reparse(current, anchor_mode) or not stat.S_ISDIR(anchor_mode):
        raise OSError(f"destination directory is unsafe: {current}")
    for part in absolute.parts[1:]:
        current /= part
        try:
            mode = _lstat(current).st_mode
        except FileNotFoundError:
            return None
        if is_link_or_reparse(current, mode) or not stat.S_ISDIR(mode):
            raise OSError(f"destination directory is unsafe: {current}")
    return absolute


def _validate_relative_path(relative: Path) -> None:
    """Reject POSIX and Windows absolute/rooted/traversing relative paths."""
    windows = PureWindowsPath(str(relative))
    posix = PurePosixPath(str(relative))
    if (
        relative.is_absolute()
        or relative.anchor
        or relative.drive
        or windows.anchor
        or windows.drive
        or windows.root
        or posix.is_absolute()
        or ".." in relative.parts
        or ".." in windows.parts
        or ".." in posix.parts
    ):
        raise OSError(f"unsafe destination path: {relative}")


def _ensure_relative_directory(root: Path, relative: Path) -> Path:
    """Create a descendant directory chain without accepting symlinked leaves."""
    _validate_relative_path(relative)
    safe_root = _ensure_real_directory(root)
    current = safe_root
    for part in relative.parts:
        if part in {"", "."}:
            continue
        current /= part
        try:
            mode = _lstat(current).st_mode
        except FileNotFoundError:
            _mkdir(current)
            mode = _lstat(current).st_mode
        if is_link_or_reparse(current, mode) or not stat.S_ISDIR(mode):
            raise OSError(f"destination directory is unsafe: {current}")
    try:
        current.relative_to(safe_root)
    except ValueError as exc:
        raise OSError(f"unsafe destination path: {relative}") from exc
    return current


def _existing_relative_directory(root: Path, relative: Path) -> Path | None:
    """Resolve an existing descendant directory without following symlink leaves."""
    _validate_relative_path(relative)
    safe_root = _existing_real_directory(root)
    if safe_root is None:
        return None
    current = safe_root
    for part in relative.parts:
        if part in {"", "."}:
            continue
        current /= part
        try:
            mode = _lstat(current).st_mode
        except FileNotFoundError:
            return None
        if is_link_or_reparse(current, mode) or not stat.S_ISDIR(mode):
            raise OSError(f"destination directory is unsafe: {current}")
    try:
        current.relative_to(safe_root)
    except ValueError as exc:
        raise OSError(f"unsafe destination path: {relative}") from exc
    return current


def _remove_path_checked(path: Path) -> None:
    """Remove one file/link/tree, preserving cleanup failures."""
    if not _path_exists(path):
        return
    mode = _lstat(path).st_mode
    if is_link_or_reparse(path, mode):
        raise OSError(f"refusing to remove link or reparse point: {path}")
    if not stat.S_ISDIR(mode):
        _unlink_replace_target(path)
        return
    _remove_tree_checked(path)


def _session_files(root: Path, *, source: bool) -> dict[Path, Path]:
    """Return regular session members keyed by relative path."""
    files: dict[Path, Path] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(_windows_extended_path(directory)) as entries:
            for entry in entries:
                path = directory / entry.name
                relative = path.relative_to(root)
                mode = entry.stat(follow_symlinks=False).st_mode
                if is_link_or_reparse(path, mode):
                    if source:
                        continue
                    raise OSError(f"unsafe session destination member: {path}")
                if stat.S_ISDIR(mode):
                    pending.append(path)
                elif stat.S_ISREG(mode):
                    if not source or not _is_excluded_name(path.name):
                        files[relative] = path
                elif not source:
                    raise OSError(f"unsafe session destination member: {path}")
    return files


def _iter_regular_source_files(root: Path):
    """Yield regular source files without descending through links/reparse points."""
    pending = [root]
    while pending:
        directory = pending.pop()
        with os.scandir(_windows_extended_path(directory)) as entries:
            for entry in entries:
                path = directory / entry.name
                mode = entry.stat(follow_symlinks=False).st_mode
                if is_link_or_reparse(path, mode):
                    continue
                if stat.S_ISDIR(mode):
                    pending.append(path)
                elif stat.S_ISREG(mode):
                    yield path


def _copy_session_tree(source: Path, destination: Path) -> tuple[int, int]:
    """Copy one session tree without following links or relying on MAX_PATH."""
    _ensure_real_directory(destination)
    parents: dict[Path, Path] = {Path("."): destination}
    copied = 0
    nbytes = 0
    for src_file in _iter_regular_source_files(source):
        if _is_excluded_name(src_file.name):
            continue
        relative = src_file.relative_to(source)
        parent = parents.get(relative.parent)
        if parent is None:
            parent = _ensure_relative_directory(destination, relative.parent)
            parents[relative.parent] = parent
        _copy_replace(src_file, parent / relative.name)
        copied += 1
        nbytes += os.stat(_windows_extended_path(src_file)).st_size
    return copied, nbytes


def _session_needs_replace(source: Path, destination: Path) -> bool:
    """Return whether the selected destination differs from the safe source tree."""
    if not _is_real_directory(destination) or not _is_real_directory(source):
        return True
    source_files = _session_files(source, source=True)
    destination_files = _session_files(destination, source=False)
    if source_files.keys() != destination_files.keys():
        return True
    return any(
        destination_files[relative].is_symlink()
        or not _same_file_content(path, destination_files[relative])
        for relative, path in source_files.items()
    )


def _finish_transaction(transaction: Path) -> str | None:
    """Mark completed transaction residue as sweepable, then remove it."""
    if not _path_exists(transaction):
        return None
    cleanup = transaction.with_name(f"{transaction.name}.cleanup")
    try:
        _durable_replace(transaction, cleanup)
    except OSError as exc:
        raise OSError(
            f"cannot mark replacement complete; recovery retained at "
            f"{transaction}: {exc}"
        ) from exc
    try:
        _remove_tree_checked(cleanup)
        _remove_tree_checked(cleanup.parent, allow_nonempty=True)
    except OSError as exc:
        return str(exc)
    return None


def _sweep_completed_transactions(replacement_root: Path) -> list[str]:
    """Remove only residue known to follow a completed publish or rollback."""
    if not _path_exists(replacement_root):
        return []
    mode = _lstat(replacement_root).st_mode
    if is_link_or_reparse(replacement_root, mode) or not stat.S_ISDIR(mode):
        raise OSError(f"replacement root must be a directory: {replacement_root}")
    active = [
        child
        for child in replacement_root.iterdir()
        if child.name.endswith(".active")
    ]
    if active:
        joined = ", ".join(str(path) for path in active)
        raise OSError(f"incomplete replacement requires recovery: {joined}")
    failures: list[str] = []
    for child in replacement_root.iterdir():
        if not child.name.endswith(".cleanup"):
            continue
        try:
            _remove_path_checked(child)
        except OSError as exc:
            failures.append(str(exc))
    _remove_tree_checked(replacement_root, allow_nonempty=True)
    return failures


def _write_transaction_manifest(
    transaction: Path,
    dest: Path,
    items: list[tuple[Path, Path, Path]],
) -> None:
    payload = {
        "schema_version": 1,
        "items": [
            {
                "staged": str(staged.relative_to(transaction)),
                "destination": str(destination.relative_to(dest)),
                "backup": str(backup.relative_to(transaction)),
                "had_destination": _path_exists(destination),
                "destination_fingerprint": _path_fingerprint(destination),
            }
            for staged, destination, backup in items
        ],
    }
    encoded = (json.dumps(payload, sort_keys=True) + "\n").encode()
    if len(encoded) > _MAX_TRANSACTION_MANIFEST_BYTES:
        raise OSError("replacement transaction manifest is too large")
    manifest = transaction / "manifest.json"
    temporary = transaction / f".manifest.{uuid.uuid4().hex}.tmp"
    try:
        _write_bytes_fsync(temporary, encoded)
        _durable_replace(temporary, manifest)
    finally:
        _unlink_replace_target(temporary)


def _load_transaction_manifest(transaction: Path) -> list[dict] | None:
    manifest = transaction / "manifest.json"
    if not _path_exists(manifest):
        return None
    with open_regular_no_follow(manifest) as stream:
        raw = stream.read(_MAX_TRANSACTION_MANIFEST_BYTES + 1)
    if len(raw) > _MAX_TRANSACTION_MANIFEST_BYTES:
        raise OSError(f"replacement transaction manifest is too large: {manifest}")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OSError(f"invalid replacement transaction manifest: {manifest}") from exc
    items = payload.get("items") if isinstance(payload, dict) else None
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != 1
        or not isinstance(items, list)
    ):
        raise OSError(f"invalid replacement transaction manifest: {manifest}")
    return items


def _path_fingerprint(path: Path) -> str | None:
    """Hash one regular file or directory tree without following links."""
    if not _path_exists(path):
        return None
    mode = _lstat(path).st_mode
    if is_link_or_reparse(path, mode):
        raise OSError(f"cannot fingerprint unsafe path: {path}")
    digest = hashlib.sha256()
    if stat.S_ISREG(mode):
        digest.update(b"F\0")
        with open_regular_no_follow(path) as stream:
            while chunk := stream.read(1024 * 1024):
                digest.update(chunk)
        return digest.hexdigest()
    if not stat.S_ISDIR(mode):
        raise OSError(f"cannot fingerprint special path: {path}")
    entries: list[tuple[str, str, str | None]] = []
    pending = [path]
    while pending:
        directory = pending.pop()
        with os.scandir(_windows_extended_path(directory)) as children:
            for child in children:
                child_path = directory / child.name
                relative = child_path.relative_to(path).as_posix()
                child_mode = child.stat(follow_symlinks=False).st_mode
                if is_link_or_reparse(child_path, child_mode):
                    raise OSError(f"cannot fingerprint unsafe path: {child_path}")
                if stat.S_ISDIR(child_mode):
                    entries.append(("D", relative, None))
                    pending.append(child_path)
                elif stat.S_ISREG(child_mode):
                    file_digest = hashlib.sha256()
                    with open_regular_no_follow(child_path) as stream:
                        while chunk := stream.read(1024 * 1024):
                            file_digest.update(chunk)
                    entries.append(("F", relative, file_digest.hexdigest()))
                else:
                    raise OSError(f"cannot fingerprint special path: {child_path}")
    for kind, relative, content_digest in sorted(entries):
        digest.update(kind.encode())
        digest.update(b"\0")
        digest.update(relative.encode())
        digest.update(b"\0")
        if content_digest is not None:
            digest.update(content_digest.encode())
        digest.update(b"\0")
    return digest.hexdigest()


def _read_generation(dest: Path) -> str | None:
    path = dest / ".session-sync-generation"
    if not _path_exists(path):
        return None
    with open_regular_no_follow(path) as stream:
        raw = stream.read(129)
    try:
        value = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise OSError(f"invalid replacement generation: {path}") from exc
    if (
        not value
        or len(value) > 128
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for char in value)
    ):
        raise OSError(f"invalid replacement generation: {path}")
    return value


def _write_generation_epoch(dest: Path, value: str) -> None:
    """Atomically advance the scanner epoch after a completed rollback."""
    if (
        not value
        or len(value) > 128
        or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789._-" for char in value)
    ):
        raise OSError("invalid replacement generation value")
    path = dest / ".session-sync-generation"
    temporary = dest / f".session-sync-generation.{uuid.uuid4().hex}.tmp"
    _write_bytes_fsync(temporary, value.encode("ascii"))
    try:
        _unlink_replace_target(path)
        _durable_replace(temporary, path)
    finally:
        _unlink_replace_target(temporary)


def _recover_active_transactions(replacement_root: Path, dest: Path) -> None:
    """Finalize committed transactions or roll incomplete ones back."""
    for raw_transaction in sorted(replacement_root.iterdir()):
        if not raw_transaction.name.endswith(".active"):
            continue
        transaction = _existing_relative_directory(
            replacement_root,
            Path(raw_transaction.name),
        )
        if transaction is None:
            raise OSError(f"unsafe replacement transaction: {raw_transaction}")
        manifest = _load_transaction_manifest(transaction)
        if manifest is None:
            # Publication never starts before the manifest is durable.
            cleanup_error = _finish_transaction(transaction)
            if cleanup_error:
                raise OSError(cleanup_error)
            continue
        if _read_generation(dest) == transaction.name:
            cleanup_error = _finish_transaction(transaction)
            if cleanup_error:
                raise OSError(cleanup_error)
            continue
        rollback_errors: list[str] = []
        for item in reversed(manifest):
            if not isinstance(item, dict):
                rollback_errors.append("invalid transaction item")
                continue
            try:
                destination_rel = Path(item["destination"])
                backup_rel = Path(item["backup"])
                _validate_relative_path(destination_rel)
                _validate_relative_path(backup_rel)
                if (
                    not destination_rel.parts
                    or destination_rel == Path(".")
                    or not destination_rel.name
                    or not backup_rel.parts
                    or backup_rel == Path(".")
                    or not backup_rel.name
                ):
                    raise OSError("transaction paths must be strict descendants")
                destination_parent = _ensure_relative_directory(
                    dest,
                    destination_rel.parent,
                )
                destination = destination_parent / destination_rel.name
                backup_parent = _existing_relative_directory(
                    transaction,
                    backup_rel.parent,
                )
                backup = (
                    backup_parent / backup_rel.name
                    if backup_parent is not None
                    else transaction / backup_rel
                )
                if destination == dest or backup == transaction:
                    raise OSError("transaction paths must be strict descendants")
                had_destination = item.get("had_destination")
                expected_fingerprint = item.get("destination_fingerprint")
                if not isinstance(had_destination, bool):
                    raise OSError("invalid transaction destination state")
                if had_destination:
                    backup_exists = backup_parent is not None and _path_exists(backup)
                    if backup_exists:
                        backup_mode = _lstat(backup).st_mode
                        if is_link_or_reparse(backup, backup_mode):
                            raise OSError(f"unsafe rollback backup: {backup}")
                        _remove_path_checked(destination)
                        _durable_replace(backup, destination)
                    elif (
                        isinstance(expected_fingerprint, str)
                        and _path_fingerprint(destination) == expected_fingerprint
                    ):
                        continue
                    else:
                        raise OSError(f"missing rollback backup: {backup}")
                else:
                    _remove_path_checked(destination)
            except (KeyError, OSError, TypeError) as exc:
                rollback_errors.append(str(exc))
        if rollback_errors:
            raise OSError(
                f"replacement recovery incomplete at {transaction}: "
                f"{'; '.join(rollback_errors)}"
            )
        _write_generation_epoch(dest, f"{transaction.name}.rolled-back")
        cleanup_error = _finish_transaction(transaction)
        if cleanup_error:
            raise OSError(cleanup_error)


def _replace_selected_sessions(
    source: Path,
    dest: Path,
    include_sessions: set[str],
) -> tuple[int, int, str | None]:
    """Publish selected sessions and provenance as one rollback-capable batch."""
    replacement_root = _ensure_relative_directory(
        dest,
        Path(".session-sync-replacement"),
    )
    _recover_active_transactions(replacement_root, dest)
    stale_cleanup_errors = _sweep_completed_transactions(replacement_root)
    transaction = replacement_root / f"{uuid.uuid4().hex}.active"
    staged_root = _ensure_relative_directory(
        replacement_root,
        Path(transaction.name) / "new",
    )
    backup_root = transaction / "old"
    items: list[tuple[Path, Path, Path]] = []
    copied = 0
    nbytes = 0
    try:
        for sid in sorted(include_sessions):
            src_session = source / "session-state" / sid
            session_item: tuple[Path, Path, Path] | None = None
            snapshot_item: tuple[Path, Path, Path] | None = None
            provenance_item: tuple[Path, Path, Path] | None = None
            if (
                _is_real_directory(src_session)
                and _session_needs_replace(
                    src_session,
                    dest / "session-state" / sid,
                )
            ):
                staged_session = staged_root / "session-state" / sid
                session_count, session_bytes = _copy_session_tree(
                    src_session,
                    staged_session,
                )
                copied += session_count
                nbytes += session_bytes
                session_item = (
                    staged_session,
                    dest / "session-state" / sid,
                    backup_root / "session-state" / sid,
                )

            src_provenance = source / "provenance" / f"{sid}.json"
            dst_provenance = dest / "provenance" / f"{sid}.json"
            receipt_item: tuple[Path, Path, Path] | None = None
            candidate_lineage = _lineage_from_provenance(src_provenance, sid)
            if candidate_lineage is not None:
                candidate_order, candidate_fingerprint, receipt_payload = (
                    candidate_lineage
                )
                if not _is_real_directory(src_session):
                    raise OSError(f"rescue session source is unavailable: {src_session}")
                snapshot_dest = rescue_snapshot_path(
                    dest,
                    sid,
                    receipt_payload["capture_id"],
                )
                if _path_exists(snapshot_dest):
                    if (
                        existing_rescue_snapshot_path(
                            dest,
                            sid,
                            receipt_payload["capture_id"],
                        )
                        is None
                    ):
                        raise OSError(
                            f"unsafe immutable rescue snapshot path for {sid}"
                        )
                    if not _snapshot_matches_source(
                        src_session,
                        src_provenance,
                        snapshot_dest,
                    ):
                        raise OSError(
                            f"immutable rescue snapshot changed for {sid}"
                        )
                else:
                    staged_snapshot = rescue_snapshot_path(
                        staged_root,
                        sid,
                        receipt_payload["capture_id"],
                    )
                    snapshot_count, snapshot_bytes = _copy_session_tree(
                        src_session,
                        staged_snapshot,
                    )
                    snapshot_provenance = (
                        staged_snapshot / RESCUE_SNAPSHOT_PROVENANCE
                    )
                    _copy_replace(
                        src_provenance,
                        snapshot_provenance,
                    )
                    copied += snapshot_count + 1
                    nbytes += (
                        snapshot_bytes
                        + os.stat(
                            _windows_extended_path(snapshot_provenance)
                        ).st_size
                    )
                    snapshot_item = (
                        staged_snapshot,
                        snapshot_dest,
                        rescue_snapshot_path(
                            backup_root,
                            sid,
                            receipt_payload["capture_id"],
                        ),
                    )
                receipt_dest = (
                    dest / ".session-sync-rescue-high-water" / f"{sid}.json"
                )
                receipt_root = _existing_relative_directory(
                    dest,
                    Path(".session-sync-rescue-high-water"),
                )
                destination_lineage = (
                    _lineage_from_receipt(receipt_root / f"{sid}.json", sid)
                    if receipt_root is not None
                    else None
                )
                if destination_lineage is None:
                    provenance_root = _existing_relative_directory(
                        dest,
                        Path("provenance"),
                    )
                    published_lineage = (
                        _lineage_from_provenance(
                            provenance_root / f"{sid}.json",
                            sid,
                        )
                        if provenance_root is not None
                        else None
                    )
                    if published_lineage is not None:
                        destination_lineage = published_lineage[:2]
                if destination_lineage is not None:
                    destination_order, destination_fingerprint = destination_lineage
                    if (
                        candidate_order[1] == destination_order[1]
                        and candidate_fingerprint != destination_fingerprint
                    ):
                        raise OSError(
                            f"rescue capture identity changed at destination for {sid}"
                        )
                    if candidate_order < destination_order:
                        raise OSError(
                            f"refusing rescue rewind for {sid}: "
                            f"{candidate_order} < {destination_order}"
                        )
                staged_receipt = (
                    staged_root
                    / ".session-sync-rescue-high-water"
                    / f"{sid}.json"
                )
                _ensure_real_directory(staged_receipt.parent)
                _write_bytes_fsync(
                    staged_receipt,
                    (json.dumps(receipt_payload, sort_keys=True) + "\n").encode(),
                )
                if not _same_file_content(staged_receipt, receipt_dest):
                    receipt_item = (
                        staged_receipt,
                        receipt_dest,
                        backup_root
                        / ".session-sync-rescue-high-water"
                        / f"{sid}.json",
                    )
            if (
                _is_real_file(src_provenance)
                and not _same_file_content(src_provenance, dst_provenance)
            ):
                staged_provenance = staged_root / "provenance" / f"{sid}.json"
                _ensure_real_directory(staged_provenance.parent)
                _copy_replace(src_provenance, staged_provenance)
                copied += 1
                nbytes += os.stat(
                    _windows_extended_path(staged_provenance)
                ).st_size
                provenance_item = (
                    staged_provenance,
                    dst_provenance,
                    backup_root / "provenance" / f"{sid}.json",
                )
            # Publish routing provenance before the matching session. A
            # concurrent scanner can then see old data with new provenance,
            # never new data with missing or stale routing authority.
            if provenance_item is not None:
                items.append(provenance_item)
            if snapshot_item is not None:
                items.append(snapshot_item)
            if session_item is not None:
                items.append(session_item)
            if receipt_item is not None:
                items.append(receipt_item)
        if items:
            staged_generation = staged_root / ".session-sync-generation"
            _write_bytes_fsync(staged_generation, transaction.name.encode("ascii"))
            items.append(
                (
                    staged_generation,
                    dest / ".session-sync-generation",
                    backup_root / ".session-sync-generation",
                )
            )
        _fsync_tree(staged_root)
        _write_transaction_manifest(transaction, dest, items)
    except OSError as staging_error:
        try:
            cleanup_error = _finish_transaction(transaction)
        except OSError as cleanup_mark_error:
            raise OSError(
                f"{staging_error}; {cleanup_mark_error}"
            ) from staging_error
        if cleanup_error:
            raise OSError(
                f"{staging_error}; cleanup deferred: {cleanup_error}"
            ) from staging_error
        raise

    published: list[tuple[Path, Path, bool]] = []
    try:
        for staged, destination, backup in items:
            _ensure_relative_directory(
                dest,
                destination.parent.relative_to(dest),
            )
            had_destination = _path_exists(destination)
            if had_destination:
                _ensure_relative_directory(
                    transaction,
                    backup.parent.relative_to(transaction),
                )
                _durable_replace(destination, backup)
            published.append((destination, backup, had_destination))
            _durable_replace(staged, destination)
    except OSError as publish_error:
        rollback_errors: list[str] = []
        for destination, backup, had_destination in reversed(published):
            try:
                _remove_path_checked(destination)
                if had_destination and _path_exists(backup):
                    _ensure_real_directory(destination.parent)
                    _durable_replace(backup, destination)
                elif had_destination:
                    raise OSError(f"missing rollback backup: {backup}")
            except OSError as rollback_error:
                rollback_errors.append(str(rollback_error))
        if rollback_errors:
            raise OSError(
                f"{publish_error}; rollback incomplete; recovery retained at "
                f"{transaction}: {'; '.join(rollback_errors)}"
            ) from publish_error
        try:
            cleanup_error = _finish_transaction(transaction)
        except OSError as cleanup_mark_error:
            raise OSError(
                f"{publish_error}; rollback completed; {cleanup_mark_error}"
            ) from publish_error
        if cleanup_error:
            raise OSError(
                f"{publish_error}; rollback completed; cleanup deferred: "
                f"{cleanup_error}"
            ) from publish_error
        raise

    cleanup_errors = stale_cleanup_errors
    cleanup_warning = _finish_transaction(transaction)
    if cleanup_warning:
        cleanup_errors.append(cleanup_warning)
    cleanup_warning = "; ".join(cleanup_errors) or None
    return copied, nbytes, cleanup_warning


def _remove_tree_checked(path: Path, *, allow_nonempty: bool = False) -> None:
    """Remove read-only-aware replacement state; failure is a push failure."""
    if not _path_exists(path):
        return
    mode = _lstat(path).st_mode
    if is_link_or_reparse(path, mode) or not stat.S_ISDIR(mode):
        raise OSError(f"refusing recursive removal of unsafe path: {path}")
    if allow_nonempty:
        try:
            _rmdir_replace_target(path)
        except OSError:
            with os.scandir(_windows_extended_path(path)) as entries:
                if next(entries, None) is not None:
                    return
            raise
        return
    directories = [path]
    pending = [path]
    while pending:
        directory = pending.pop()
        with os.scandir(_windows_extended_path(directory)) as scan:
            entries = [
                (entry.name, entry.stat(follow_symlinks=False).st_mode)
                for entry in scan
            ]
        for name, child_mode in entries:
            child = directory / name
            if is_link_or_reparse(child, child_mode):
                raise OSError(f"refusing recursive removal of unsafe path: {child}")
            if stat.S_ISDIR(child_mode):
                directories.append(child)
                pending.append(child)
            elif stat.S_ISREG(child_mode):
                _unlink_replace_target(child)
            else:
                raise OSError(f"refusing recursive removal of special path: {child}")
    for directory in reversed(directories):
        _rmdir_replace_target(directory)


def _parse_iso(ts: str) -> datetime | None:
    """Parse an ISO-8601 timestamp (tolerating a trailing ``Z``)."""
    raw = (ts or "").strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _hub_session_age_days(session_dir: Path, now: datetime) -> float | None:
    """Age in days from a hub session's ``workspace.yaml`` timestamps.

    Uses ``updated_at`` then ``created_at`` -- never filesystem mtime, which is
    unreliable on OneDrive online-only placeholders. ``None`` if no timestamp.
    """
    ws_file = session_dir / "workspace.yaml"
    if not ws_file.is_file():
        return None
    try:
        ws = sessions.parse_workspace_text(
            ws_file.read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        return None
    for key in ("updated_at", "created_at"):
        dt = _parse_iso(ws.get(key, ""))
        if dt is not None:
            return (now - dt).total_seconds() / 86400.0
    return None


def _hub_session_cwd(session_dir: Path) -> str:
    """Normalized cwd/git_root from a hub session's ``workspace.yaml`` ("" if none)."""
    ws_file = session_dir / "workspace.yaml"
    if not ws_file.is_file():
        return ""
    try:
        ws = sessions.parse_workspace_text(
            ws_file.read_text(encoding="utf-8", errors="replace")
        )
    except OSError:
        return ""
    raw = (ws.get("cwd") or ws.get("git_root") or "").strip()
    return os.path.normcase(os.path.normpath(raw)) if raw else ""


def _count_sessions(dest: Path) -> int:
    """Count session directories under ``<dest>/session-state``."""
    base = dest / "session-state"
    if not base.is_dir():
        return 0
    return sum(1 for d in base.iterdir() if d.is_dir())


def _included(rel: Path, include_sessions: set[str] | None) -> bool:
    """Decide whether a relative source path is in scope.

    session-sync archives *session* data only -- the ``session-state`` tree,
    optional per-session ``provenance`` sidecars, plus the global
    ``session-store.db`` index -- never the rest of the source (``~/.copilot``:
    binaries, installed plugins, OAuth/credential state, encryption keys,
    settings).

    With no allowlist, the whole ``session-state`` tree and the session-store.db
    index are included. With an allowlist, only ``session-state/<id>/`` for an
    allowed ``<id>`` is included (the global session-store.db is skipped so
    other repos' session metadata never leaks).
    """
    parts = rel.parts
    if not parts:
        return False
    if parts[0] == "session-state":
        if include_sessions is None:
            return True
        return len(parts) >= 2 and parts[1] in include_sessions
    if parts[0] == "provenance":
        if len(parts) != 2 or rel.suffix != ".json":
            return False
        if include_sessions is None:
            return True
        return rel.stem in include_sessions
    # Top-level session index: kept only when not filtering by repo.
    return (
        include_sessions is None
        and len(parts) == 1
        and rel.name in _SESSION_INDEX_NAMES
    )


class FilesystemTarget(Target):
    """Base for targets that publish to a local-or-mounted directory root."""

    rescue_compare_and_set = True

    def _root(self) -> Path:  # pragma: no cover - abstract-ish
        raise NotImplementedError

    def push(
        self, source: Path, machine: str, include_sessions: set[str] | None = None
    ) -> PushResult:
        try:
            safe_source = _existing_real_directory(source)
        except OSError as exc:
            return PushResult(ok=False, detail=f"unsafe source: {exc}")
        if safe_source is None:
            return PushResult(ok=False, detail=f"source not found: {source}")
        source = safe_source
        try:
            root = self._root()
            dest = _ensure_relative_directory(root, Path(machine))
        except OSError as exc:
            return PushResult(
                ok=False,
                detail=f"cannot create safe destination for {machine}: {exc}",
            )

        copied = 0
        nbytes = 0
        locked_paths: list[Path] = []
        if include_sessions is not None:
            lock_file = dest / ".session-sync-rescue.lock"
            try:
                with sync_lock(lock_file, timeout=30) as acquired:
                    if not acquired:
                        return PushResult(
                            ok=False,
                            detail=f"destination rescue lock is busy: {lock_file}",
                        )
                    copied, nbytes, cleanup_warning = _replace_selected_sessions(
                        source,
                        dest,
                        include_sessions,
                    )
            except OSError as exc:
                return PushResult(ok=False, detail=f"session replace failed: {exc}")
            session_count = _count_sessions(dest)
            write_sync_meta(dest, machine, self.name, "ok", session_count)
            detail = f"-> {dest}"
            if cleanup_warning:
                detail += f" (replacement cleanup deferred: {cleanup_warning})"
            return PushResult(
                ok=True,
                detail=detail,
                file_count=copied,
                byte_count=nbytes,
            )
        try:
            source_files = _iter_regular_source_files(source)
            for src_file in source_files:
                if _is_excluded_name(src_file.name):
                    continue
                rel = src_file.relative_to(source)
                if not _included(rel, include_sessions):
                    continue
                dst_file = dest / rel
                try:
                    _ensure_relative_directory(dest, rel.parent)
                except OSError as exc:
                    return PushResult(ok=False, detail=f"unsafe destination: {exc}")
                if _needs_copy(src_file, dst_file):
                    try:
                        # Replace by unlink-then-copy, never truncate-in-place. A
                        # destination written read-only by another syncer (e.g. the
                        # legacy session-sync's ``.session-origin.json`` provenance
                        # markers, chmod'd 0444 and surfaced as the DOS read-only
                        # attribute over CIFS) cannot be truncate-opened, so a plain
                        # ``copy2`` would raise EPERM and abort the entire push --
                        # and with it the post-push notify. Unlinking needs only
                        # write permission on the parent directory, which we have,
                        # so it succeeds regardless of the file's own mode.
                        _copy_replace(src_file, dst_file)
                    except OSError as exc:
                        if isinstance(exc, _LockedSourceFile):
                            locked_paths.append(rel)
                            continue
                        return PushResult(
                            ok=False,
                            detail=f"copy failed for {rel}: {exc}",
                        )
                    copied += 1
                    nbytes += os.stat(_windows_extended_path(src_file)).st_size
        except OSError as exc:
            return PushResult(ok=False, detail=f"cannot inspect source: {exc}")

        session_count = _count_sessions(dest)
        status = "partial" if locked_paths else "ok"
        write_sync_meta(dest, machine, self.name, status, session_count)
        detail = f"-> {dest}"
        if locked_paths:
            examples = ", ".join(str(path) for path in locked_paths[:3])
            detail += (
                f" (skipped {len(locked_paths)} locked file(s), will retry: "
                f"{examples})"
            )
        return PushResult(
            ok=True,
            detail=detail,
            file_count=copied,
            byte_count=nbytes,
        )

    def prune(self, machine: str, retention_days: int | None) -> int:
        if not isinstance(retention_days, (int, float)) or retention_days <= 0:
            return 0
        machine_root = _existing_relative_directory(self._root(), Path(machine))
        if machine_root is None:
            return 0
        base = _existing_relative_directory(machine_root, Path("session-state"))
        if base is None:
            return 0
        provenance = _existing_relative_directory(
            machine_root,
            Path("provenance"),
        )
        high_water = _existing_relative_directory(
            machine_root,
            Path(".session-sync-rescue-high-water"),
        )
        snapshots = _existing_relative_directory(
            machine_root,
            Path(".session-sync-rescue-captures"),
        )
        cutoff = time.time() - retention_days * 86400
        removed = 0
        for d in base.iterdir():
            if d.is_symlink() or not d.is_dir():
                continue
            newest = max(
                (
                    f.stat().st_mtime
                    for f in d.rglob("*")
                    if f.is_file() and not f.is_symlink()
                ),
                default=d.stat().st_mtime,
            )
            if newest < cutoff and sessions.force_rmtree(d):
                if provenance is not None:
                    (provenance / f"{d.name}.json").unlink(missing_ok=True)
                if high_water is not None:
                    (high_water / f"{d.name}.json").unlink(missing_ok=True)
                if snapshots is not None:
                    snapshot_session = (
                        snapshots / hashlib.sha256(d.name.encode()).hexdigest()
                    )
                    _remove_path_checked(snapshot_session)
                removed += 1
        return removed

    def push_archives(self, archive_root: Path, machine: str) -> PushResult:
        if not archive_root.is_dir():
            return PushResult(ok=True, detail="no local archive store", file_count=0)
        try:
            dest = _ensure_relative_directory(
                self._root(),
                Path(machine) / "archived",
            )
        except OSError as exc:
            return PushResult(
                ok=False,
                detail=f"cannot create safe archive destination for {machine}: {exc}",
            )
        copied = 0
        nbytes = 0
        for src_file in archive_root.iterdir():
            if not src_file.is_file():
                continue
            dst_file = dest / src_file.name
            if _needs_copy(src_file, dst_file):
                try:
                    _copy_replace(src_file, dst_file)
                except OSError as exc:
                    return PushResult(ok=False, detail=f"copy failed: {exc}")
                copied += 1
                nbytes += src_file.stat().st_size
        return PushResult(ok=True, detail=f"-> {dest}", file_count=copied, byte_count=nbytes)

    def reconcile_hub(self, machine: str, *, dry_run: bool = False) -> int:
        """Remove uncompressed hub sessions whose verified archive has landed."""
        base = _existing_relative_directory(self._root(), Path(machine))
        if base is None:
            return 0
        archived = _existing_relative_directory(base, Path("archived"))
        state = _existing_relative_directory(base, Path("session-state"))
        if archived is None or state is None:
            return 0
        removed = 0
        for arc in archived.iterdir():
            if not arc.is_file() or not any(
                arc.name.endswith(s) for s in sessions._ARCHIVE_SUFFIXES
            ):
                continue
            sid = sessions._archive_stem(arc)
            live = state / sid
            if not live.is_dir():
                continue
            ref = SessionRef(id=sid, kind="archive", path=arc, store=archived)
            if not sessions.verify_archive(ref):
                continue
            if dry_run:
                removed += 1
            elif sessions.force_rmtree(live):
                # Count only sessions actually removed -- OneDrive hub dirs are
                # ReadOnly online-only placeholders that defeat a plain rmtree.
                removed += 1
        return removed

    def compact_backlog(
        self,
        machine: str,
        min_age_days: int,
        codec: str,
        *,
        tracked_paths: set[str] | None = None,
        dry_run: bool = False,
    ) -> int:
        """Compact cold hub-only sessions in place under ``{machine}/archived/``.

        ``tracked_paths`` (when provided, for the running machine's own
        namespace) protects any hub session whose worktree is still tracked --
        so a hub copy of a live, picker-visible session is never archived even
        if it is old.
        """
        base = _existing_relative_directory(self._root(), Path(machine))
        if base is None:
            return 0
        state = _existing_relative_directory(base, Path("session-state"))
        if state is None:
            return 0
        archived = _ensure_relative_directory(base, Path("archived"))
        now = datetime.now(timezone.utc)
        compacted = 0
        for d in state.iterdir():
            if not d.is_dir() or not (d / sessions.EVENTS_MEMBER).exists():
                continue
            if sessions.is_archived(d.name, archived, codec):
                continue
            if tracked_paths is not None:
                cwd = _hub_session_cwd(d)
                if cwd and cwd in tracked_paths:
                    continue
            age = _hub_session_age_days(d, now)
            if age is None or age < min_age_days:
                continue
            if dry_run:
                compacted += 1
                continue
            ref = sessions.archive_session(d, archived, codec=codec)
            if sessions.verify_archive(ref):
                compacted += 1
            else:
                sessions.remove_archive(ref)
        return compacted

    def doctor(self) -> DoctorResult:
        result = DoctorResult(ok=True)
        root = self._root()
        # Walk up to the nearest existing ancestor to test writability.
        probe = root
        while not probe.exists() and probe.parent != probe:
            probe = probe.parent
        result.add("root resolved", True, str(root))
        result.add("ancestor exists", probe.exists(), str(probe))
        result.add("ancestor writable", os.access(probe, os.W_OK), str(probe))
        return result

    def describe(self) -> str:
        return f"{self.name}: {self._root()}"


class LocalTarget(FilesystemTarget):
    """Publish to a dotfolder under ``$HOME`` (default) or an explicit path."""

    name = "local"

    def _root(self) -> Path:
        path = self.options.get("path")
        if path:
            return Path(path).expanduser()
        return Path.home() / ".agent-logger" / "sessions"


def resolve_onedrive_root() -> Path | None:
    """Resolve the OneDrive root for the current OS, or ``None``.

    Honors the Windows ``OneDrive*`` environment variables first, then falls
    back to ``~/OneDrive`` if it exists.
    """
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        value = os.environ.get(var)
        if value and Path(value).is_dir():
            return Path(value)
    fallback = Path.home() / "OneDrive"
    if fallback.is_dir():
        return fallback
    return None


class OneDriveTarget(FilesystemTarget):
    """Publish to a subfolder under the resolved OneDrive root."""

    name = "onedrive"
    rescue_compare_and_set = False

    def _root(self) -> Path:
        explicit = self.options.get("root")
        base = Path(explicit).expanduser() if explicit else resolve_onedrive_root()
        if base is None:
            raise FileNotFoundError(
                "OneDrive root not found; set sync.targets.onedrive.root or the "
                "OneDrive environment variable"
            )
        subfolder = self.options.get("subfolder", "Apps/agent-logger/sessions")
        return base / subfolder

    def doctor(self) -> DoctorResult:
        # Surface a clear failure if OneDrive can't be resolved at all.
        if not self.options.get("root") and resolve_onedrive_root() is None:
            result = DoctorResult(ok=True)
            result.add("OneDrive root resolved", False, "no OneDrive env var or ~/OneDrive")
            return result
        return super().doctor()
