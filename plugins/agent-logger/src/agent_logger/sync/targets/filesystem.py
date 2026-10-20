"""Filesystem sync targets: ``local`` and ``onedrive``.

Both publish the source tree into ``<root>/<machine>/`` via an incremental
copy (size + mtime delta). They differ only in how the root is resolved:

- ``local`` -- a dotfolder under ``$HOME`` (default
  ``~/.agent-logger/sessions``), or an explicit ``path``.
- ``onedrive`` -- a ``subfolder`` under the OS-resolved OneDrive root, which
  turns a OneDrive folder into a NAS-equivalent fleet aggregation point.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import shutil
import stat
import time
from collections.abc import Callable
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO

from agent_logger import sessions
from agent_logger.process_logs import is_process_log_candidate
from agent_logger.sessions import SessionRef
from agent_logger.sync.archive_directory import (
    ArchiveDirectory, ArchiveRootChangedError, archive_directory,
)
from agent_logger.sync.detritus import (
    DetritusSummary,
    discover_session_detritus,
    discover_session_tree_detritus,
    is_excluded,
)
from agent_logger.sync.lock import sync_lock
from agent_logger.sync.log_archive_readback import ProcessLogReadback, filesystem_log_readback
from agent_logger.sync import protection as _protection
from agent_logger.sync.publication_directory import PublicationDirectory, publication_directory
from agent_logger.sync.meta import (
    heartbeat_sync_meta,
    read_sync_meta,
    write_process_log_meta,
    write_sync_meta,
)
from agent_logger.sync.provenance import (
    MAX_PROVENANCE_BYTES,
    RESCUE_SNAPSHOT_PROVENANCE,
    ensure_real_directory,
    existing_rescue_snapshot_path,
    is_link_or_reparse,
    open_regular_no_follow,
    rescue_session_key,
    rescue_snapshot_path,
    short_unique_id,
)
from agent_logger.sync.provenance import (
    durable_replace as _durable_replace,
)
from agent_logger.sync.provenance import (
    fsync_directory as _fsync_directory,
)
from agent_logger.sync.provenance import (
    windows_extended_path as _windows_extended_path,
)
from agent_logger.sync.targets import publication_admission
from agent_logger.sync.targets.base import (
    SESSION_INDEX_NAMES,
    DoctorResult,
    FleetSyncStatus,
    PushResult,
    SourceIdentityLike,
    SyncStatus,
    Target,
    is_session_path_included,
)

#: Excluded from sync: legacy lock names, ``.lock``/``.tmp`` suffixes, and ``.hold`` (Copilot's restrictive-ACL ``inuse.<pid>.hold`` marker).
_EXCLUDE_NAMES, _EXCLUDE_SUFFIXES = frozenset({".lock", "lock"}), (".lock", ".tmp", ".hold")

_MAX_TRANSACTION_MANIFEST_BYTES = 16 * 1024 * 1024
_MAX_FLEET_MACHINE_DEPTH = 4
_MAX_FLEET_MACHINES = 1000
_MAX_FLEET_DIRECTORIES = 2000
_MAX_FLEET_ENTRIES = 10_000


def _is_excluded_name(name: str) -> bool:
    return name.casefold() in _EXCLUDE_NAMES or name.casefold().endswith(_EXCLUDE_SUFFIXES)


def _is_windows_sharing_violation(exc: OSError) -> bool:
    return (
        os.name == "nt"
        and getattr(exc, "winerror", None) in {32, 33}
    )


class _LockedSourceFile(OSError):
    pass


class _SourceChangedDuringCopy(OSError):
    """The source file's identity or size changed between opening it and
    finishing the byte copy (e.g. rotated/replaced under the same name
    mid-transfer) -- the destination must not land with the old bytes under
    the replacement's metadata, which would make a future incremental pass
    believe the replacement is already synced. Handled the same way as
    :class:`_LockedSourceFile`: this file didn't land this pass, retried
    next pass, never a push failure."""


class _FleetScanLimitError(OSError):
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


def _copy_stream_replace(
    source: BinaryIO, dst: Path, *, revalidate: Callable[[], bool] | None = None,
    destination: PublicationDirectory | None = None,
) -> None:
    """Atomically replace *dst* from an already-open *source* stream.

    Metadata and post-copy identity/size checks use the open source fd.
    Revalidate its directory entry BEFORE touching a previous good copy.
    Process logs additionally supply a pinned destination for every write;
    legacy session-state callers retain their existing pathname behavior.
    """
    before = os.fstat(source.fileno())
    temporary = dst.with_name(f".{dst.name}.{short_unique_id()}.tmp")
    temporary_io = _windows_extended_path(temporary)
    created = False
    try:
        with (destination.create(temporary.name) if destination is not None
              else open(temporary_io, "xb")) as target:
            created = True
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
            if destination is not None:
                destination.metadata(target, temporary.name, before)
                os.fsync(target.fileno())
        after = os.fstat(source.fileno())
        if (before.st_dev, before.st_ino, before.st_size) != (
            after.st_dev, after.st_ino, after.st_size,
        ):
            raise _SourceChangedDuringCopy(
                "source changed identity or size during copy"
            )
        if destination is None:
            try:
                os.utime(temporary_io, ns=(before.st_atime_ns, before.st_mtime_ns))
                os.chmod(temporary_io, stat.S_IMODE(before.st_mode))
            except OSError:
                pass
        if revalidate is not None and not revalidate():
            raise _SourceChangedDuringCopy(
                "source directory entry identity changed during copy"
            )
        if destination is None:
            _unlink_replace_target(dst)
            _durable_replace(temporary, dst)
        else:
            destination.replace(temporary.name, dst.name)
    finally:
        if destination is None:
            _unlink_replace_target(temporary)
        elif created:
            destination.unlink(temporary.name)


def _copy_replace(src: Path, dst: Path) -> None:
    """Copy one regular source without following links."""
    try:
        source = open_regular_no_follow(src)
    except OSError as exc:
        if _is_windows_sharing_violation(exc):
            raise _LockedSourceFile(f"source file is locked: {src}") from exc
        raise
    with source:
        _copy_stream_replace(source, dst)


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


def _needs_copy_from_stat(
    src_stat: os.stat_result, dst: Path, destination: PublicationDirectory,
) -> bool:
    """Same comparison as :func:`_needs_copy`, given an already-fetched
    source ``os.stat_result`` (e.g. from a dir_fd-relative stat) instead of
    re-resolving the source by path -- used by the process-log copy path,
    which must not re-resolve a root-pinned directory entry by path."""
    try:
        d = destination.stat(dst.name)
        mode = d.st_mode
        if is_link_or_reparse(dst, mode) or not stat.S_ISREG(mode):
            return True
    except FileNotFoundError:
        return True
    return src_stat.st_size != d.st_size or src_stat.st_mtime > d.st_mtime + 1e-6


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


def _copy_process_logs(
    source: Path, dest: Path, source_identity: os.stat_result | None = None,
) -> tuple[int, int, list[Path]]:
    """Keep canonical source ancestors pinned for the entire transfer."""
    with ExitStack() as stack:
        try:
            directory = stack.enter_context(archive_directory(source))
        except ArchiveRootChangedError as exc:
            raise _SourceChangedDuringCopy(f"{exc}: {source}") from exc
        except (NotADirectoryError, ValueError) as exc:
            raise ValueError(f"process-log source is not a directory: {source}") from exc
        opened = os.fstat(directory.fd) if directory.fd is not None else directory.path.stat()
        if source_identity is not None and (
            source_identity.st_dev, source_identity.st_ino,
        ) != (opened.st_dev, opened.st_ino):
            raise _SourceChangedDuringCopy("process-log source root changed before opening")
        destination = stack.enter_context(publication_directory(dest))
        return _copy_process_logs_pinned(directory, destination, opened)


def _copy_process_logs_pinned(
    directory: ArchiveDirectory, destination: PublicationDirectory, source_identity: os.stat_result,
) -> tuple[int, int, list[Path]]:
    """Flat, non-recursive incremental copy of process-log evidence.

    Returns ``(copied, bytes, locked_paths)`` for flat process-log candidates.
    Size/mtime comparisons skip unchanged evidence; transient Windows sharing
    violations and identity/size changes defer individual files for retry.

    The caller pins all canonical source ancestors for this entire operation.
    POSIX entry I/O additionally uses the final directory fd; Windows pathname
    reads run while ancestor handles prohibit directory rename.

    A source file that disappears mid-pass (log rotation or deletion racing
    this copy) is treated as an ordinary skipped candidate, never a
    failure -- disappearance is expected for a live log source. Copied byte
    counts are read from the just-written destination, never a re-stat of
    the source, which could itself have rotated away by the time of that
    re-stat.

    On POSIX, a rename-based rotation (the writer renames the file aside
    and creates a fresh one under the same name, rather than truncating
    the existing inode in place) is detected separately from
    :class:`_SourceChangedDuringCopy`: that check only compares the SAME
    open descriptor's own identity before and after the byte copy, which a
    rename can never change (the descriptor still refers to the original
    inode throughout). The directory ENTRY's current identity is instead
    re-stat'd by name (via the pinned directory descriptor, never by path) and
    compared against the identity observed before the copy started, via a
    ``revalidate`` callback :func:`_copy_stream_replace` calls strictly
    *before* committing the replace (see that function's own docstring) --
    a mismatch discards only the just-written temporary file; no landed
    destination copy is ever removed, and the entry is treated as unlanded
    this pass, same as a locked file.
    """
    copied = 0
    nbytes = 0
    locked: list[Path] = []
    source = directory.path
    dest = destination.path

    def _revalidate_root(before: os.stat_result) -> None:
        after = _lstat(source)
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise _SourceChangedDuringCopy("process-log source root changed during copy")

    def _land(
        stream: BinaryIO, dst_path: Path, *, revalidate: Callable[[], bool] | None = None,
    ) -> int | None:
        """Copy *stream* into *dst_path*; returns the landed byte count, or
        ``None`` when this entry didn't land this pass (locked, the source
        changed identity/size mid-copy, or *revalidate* rejected it) --
        callers handle accounting themselves. When given, *revalidate* is
        checked before dst is ever touched (see
        :func:`_copy_stream_replace`), so a rejection can never destroy a
        previously landed, still-good destination copy."""
        try:
            _copy_stream_replace(
                stream, dst_path, revalidate=revalidate, destination=destination,
            )
        except OSError as exc:
            if _is_windows_sharing_violation(exc) or isinstance(exc, _SourceChangedDuringCopy):
                return None
            raise
        return destination.stat(dst_path.name).st_size

    if directory.fd is not None:
        try:
            _revalidate_root(source_identity)
            for name in directory.names():
                if not is_process_log_candidate(name):
                    continue
                try:
                    before = directory.stat(name)
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(before.st_mode):
                    continue
                dst_path = dest / name
                if not _needs_copy_from_stat(before, dst_path, destination):
                    continue

                def _same_entry_identity(
                    name: str = name, before: os.stat_result = before,
                ) -> bool:
                    # A renamed inode keeps its fd identity; revalidate the
                    # directory entry before landing any destination bytes.
                    _revalidate_root(source_identity)
                    try:
                        after = directory.stat(name)
                    except FileNotFoundError:
                        return False
                    return (before.st_dev, before.st_ino) == (after.st_dev, after.st_ino)

                _revalidate_root(source_identity)
                with ExitStack() as source_stack:
                    try:
                        stream = source_stack.enter_context(directory.open(name))
                        opened = os.fstat(stream.fileno())
                        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                            raise ValueError(f"process-log evidence changed while opening: {name}")
                    except (FileNotFoundError, ValueError):
                        continue
                    except OSError as exc:
                        if exc.errno != errno.ELOOP:
                            raise
                        continue
                    size = _land(stream, dst_path, revalidate=_same_entry_identity)
                if size is None:
                    _revalidate_root(source_identity)
                    locked.append(source / name)
                    continue
                copied += 1
                nbytes += size
            _revalidate_root(source_identity)
        except FileNotFoundError as exc:
            raise OSError(f"process-log copy path vanished during transfer: {exc}") from exc
        return copied, nbytes, locked

    try:
        with os.scandir(_windows_extended_path(source)) as scanned:
            names = sorted(entry.name for entry in scanned if is_process_log_candidate(entry.name))
    except FileNotFoundError as exc:
        raise OSError("process-log source vanished during copy") from exc
    for name in names:
        src_path = source / name
        try:
            before = _lstat(src_path)
            mode = before.st_mode
        except FileNotFoundError:
            continue
        if is_link_or_reparse(src_path, mode) or not stat.S_ISREG(mode):
            continue
        dst_path = dest / name
        if not _needs_copy_from_stat(before, dst_path, destination):
            continue
        try:
            stream = open_regular_no_follow(src_path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            if _is_windows_sharing_violation(exc):
                locked.append(src_path)
                continue
            raise
        with stream:
            size = _land(stream, dst_path)
        if size is None:
            locked.append(src_path)
            continue
        copied += 1
        nbytes += size
    _revalidate_root(source_identity)
    return copied, nbytes, locked


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
    excluded_roots: tuple[Path, ...] = (),
) -> bool:
    """Verify immutable snapshot bytes plus its capture provenance."""
    source_files = _session_files(
        source,
        source=True,
        excluded_roots=excluded_roots,
    )
    destination_files = _session_files(
        snapshot,
        source=False,
        excluded_roots=excluded_roots,
    )
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


def _ensure_real_directory(path: Path, *, durable: bool = False) -> Path:
    """Use the shared race-tolerant, no-link directory creation contract."""
    try:
        return ensure_real_directory(path, durable=durable)
    except OSError as exc:
        raise OSError(f"destination {exc}") from exc


def _existing_real_directory(path: Path) -> Path | None:
    """Resolve an existing directory only through real directory components."""
    validated = _existing_real_directory_identity(path)
    return validated[0] if validated is not None else None


def _existing_real_directory_identity(path: Path) -> tuple[Path, os.stat_result] | None:
    """Return the final component's identity from the validating stat itself."""
    absolute = _anchored_path(path)
    current = Path(absolute.anchor)
    try:
        identity = _lstat(current)
        anchor_mode = identity.st_mode
    except FileNotFoundError:
        return None
    if is_link_or_reparse(current, anchor_mode) or not stat.S_ISDIR(anchor_mode):
        raise OSError(f"destination directory is unsafe: {current}")
    for part in absolute.parts[1:]:
        current /= part
        try:
            identity = _lstat(current)
            mode = identity.st_mode
        except FileNotFoundError:
            return None
        if is_link_or_reparse(current, mode) or not stat.S_ISDIR(mode):
            raise OSError(f"destination directory is unsafe: {current}")
    return absolute, identity


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


def _ensure_relative_directory(root: Path, relative: Path, *, durable: bool = False) -> Path:
    """Create a descendant directory chain without accepting symlinked leaves."""
    _validate_relative_path(relative)
    safe_root = _ensure_real_directory(root, durable=durable)
    current = safe_root
    for part in relative.parts:
        if part in {"", "."}:
            continue
        current /= part
        try:
            mode = _lstat(current).st_mode
        except FileNotFoundError:
            try:
                _mkdir(current)
            except FileExistsError:
                pass
            mode = _lstat(current).st_mode
        if is_link_or_reparse(current, mode) or not stat.S_ISDIR(mode):
            raise OSError(f"destination directory is unsafe: {current}")
        if durable:
            _fsync_directory(current.parent)
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


def _session_files(
    root: Path,
    *,
    source: bool,
    excluded_roots: tuple[Path, ...] = (),
) -> dict[Path, Path]:
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
                    if not is_excluded(relative, excluded_roots):
                        pending.append(path)
                elif stat.S_ISREG(mode):
                    if (
                        not is_excluded(relative, excluded_roots)
                        and (not source or not _is_excluded_name(path.name))
                    ):
                        files[relative] = path
                elif not source:
                    raise OSError(f"unsafe session destination member: {path}")
    return files


def _iter_regular_source_files(
    root: Path,
    excluded_roots: tuple[Path, ...] = (),
):
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
                relative = path.relative_to(root)
                if is_excluded(relative, excluded_roots):
                    continue
                if stat.S_ISDIR(mode):
                    pending.append(path)
                elif stat.S_ISREG(mode):
                    yield path


def _copy_session_tree(
    source: Path,
    destination: Path,
    excluded_roots: tuple[Path, ...] = (),
) -> tuple[int, int]:
    """Copy one session tree without following links or relying on MAX_PATH."""
    _ensure_real_directory(destination)
    parents: dict[Path, Path] = {Path("."): destination}
    copied = 0
    nbytes = 0
    for src_file in _iter_regular_source_files(source, excluded_roots):
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


def _session_needs_replace(
    source: Path,
    destination: Path,
    excluded_roots: tuple[Path, ...] = (),
) -> bool:
    """Return whether the selected destination differs from the safe source tree."""
    if not _is_real_directory(destination) or not _is_real_directory(source):
        return True
    source_files = _session_files(
        source,
        source=True,
        excluded_roots=excluded_roots,
    )
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
    temporary = transaction / f".manifest.{short_unique_id()}.tmp"
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
    temporary = dest / f".session-sync-generation.{short_unique_id()}.tmp"
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
    detritus: DetritusSummary,
) -> tuple[int, int, str | None]:
    """Publish selected sessions and provenance as one rollback-capable batch."""
    replacement_root = _ensure_relative_directory(
        dest,
        Path(".session-sync-replacement"),
    )
    _recover_active_transactions(replacement_root, dest)
    stale_cleanup_errors = _sweep_completed_transactions(replacement_root)
    transaction = replacement_root / f"{short_unique_id()}.active"
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
            session_roots = detritus.roots_below(Path("session-state") / sid)
            session_item: tuple[Path, Path, Path] | None = None
            snapshot_item: tuple[Path, Path, Path] | None = None
            provenance_item: tuple[Path, Path, Path] | None = None
            if (
                _is_real_directory(src_session)
                and _session_needs_replace(
                    src_session,
                    dest / "session-state" / sid,
                    session_roots,
                )
            ):
                staged_session = staged_root / "session-state" / sid
                session_count, session_bytes = _copy_session_tree(
                    src_session,
                    staged_session,
                    session_roots,
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
                write_snapshot = False
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
                        session_roots,
                    ):
                        raise OSError(
                            f"immutable rescue snapshot changed for {sid}"
                        )
                    write_snapshot = bool(
                        discover_session_tree_detritus(snapshot_dest).roots
                    )
                else:
                    write_snapshot = True
                if write_snapshot:
                    staged_snapshot = rescue_snapshot_path(
                        staged_root,
                        sid,
                        receipt_payload["capture_id"],
                    )
                    snapshot_count, snapshot_bytes = _copy_session_tree(
                        src_session,
                        staged_snapshot,
                        session_roots,
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


def _deferred_session_ids(locked_paths: list[Path]) -> tuple[str, ...]:
    """Session ids with >=1 deferred (locked) file -- parsed from each path's
    ``session-state/<id>/...`` or ``provenance/<id>.json`` shape."""
    ids: set[str] = set()
    for rel in locked_paths:
        parts = rel.parts
        if len(parts) >= 2 and parts[0] in ("session-state", "provenance"):
            ids.add(Path(parts[1]).stem if parts[0] == "provenance" else parts[1])
    return tuple(sorted(ids))


def _index_deferred(locked_paths: list[Path]) -> bool:
    """Whether a deferred (locked) path is a top-level index file."""
    return any(len(rel.parts) == 1 and rel.name in SESSION_INDEX_NAMES for rel in locked_paths)


_COMPARE_CHUNK = 1024 * 1024


def _regular_file_tuple(path: Path) -> tuple[int, int] | None:
    """``(size, mtime_ns)`` of a regular, non-link file; else ``None``."""
    try:
        info = _lstat(path)
    except OSError:
        return None
    if is_link_or_reparse(path, info.st_mode) or not stat.S_ISREG(info.st_mode):
        return None
    return info.st_size, info.st_mtime_ns


def _same_content(stream: BinaryIO, dst: Path, size: int) -> bool:
    """Whether *dst* already holds exactly the first *size* bytes of *stream*."""
    try:
        existing = open_regular_no_follow(dst)
    except OSError:
        return False
    try:
        with existing:
            if os.fstat(existing.fileno()).st_size != size:
                return False
            stream.seek(0)
            remaining = size
            while remaining > 0:
                expected = stream.read(min(_COMPARE_CHUNK, remaining))
                if not expected or existing.read(len(expected)) != expected:
                    return False
                remaining -= len(expected)
            return not existing.read(1)
    except OSError:
        return False


def _protected_copy(
    src: Path, dst: Path, member: str, *, compare_existing: bool = False,
) -> tuple[_protection.Outcome, tuple[int, int], tuple[int, int] | None, bool]:
    """Publish the protected derivative of *src* at *dst*, streaming.

    Returns ``(outcome, source (size, mtime_ns), destination (size,
    mtime_ns) or None, replaced)``. Unless the outcome is published nothing
    is written, so *dst* keeps whatever version it already had. The source
    is only read. A locked or changing source raises exactly as in
    :func:`_copy_replace`. With *compare_existing*, an identical destination
    is left in place.
    """
    try:
        source = open_regular_no_follow(src)
    except OSError as exc:
        if _is_windows_sharing_violation(exc):
            raise _LockedSourceFile(f"source file is locked: {src}") from exc
        raise
    temporary = dst.with_name(f".{dst.name}.{short_unique_id()}.tmp")
    temporary_io = _windows_extended_path(temporary)
    with source:
        before = os.fstat(source.fileno())
        source_tuple = (before.st_size, before.st_mtime_ns)
        try:
            with open(temporary_io, "xb+") as target:
                try:
                    outcome = _protection.transform_file(
                        member,
                        _protection.BoundedReader(source, before.st_size),
                        before.st_size,
                        target.write,
                    )
                except _protection.SourceChangedError as exc:
                    raise _SourceChangedDuringCopy(f"{exc}: {src}") from exc
                after = os.fstat(source.fileno())
                if (before.st_dev, before.st_ino, before.st_size) != (
                    after.st_dev, after.st_ino, after.st_size,
                ):
                    raise _SourceChangedDuringCopy(
                        "source changed identity or size during copy"
                    )
                if not outcome.published:
                    return outcome, source_tuple, None, False
                target.flush()
                os.fsync(target.fileno())
                if compare_existing and _same_content(target, dst, target.tell()):
                    return outcome, source_tuple, _regular_file_tuple(dst), False
            # A rewritten copy is stamped one second older than its source, so
            # a later unprotected reconcile (size + mtime) restores raw bytes.
            stamp = before.st_mtime_ns - (1_000_000_000 if outcome.changed else 0)
            try:
                os.utime(temporary_io, ns=(before.st_atime_ns, stamp))
                os.chmod(temporary_io, stat.S_IMODE(before.st_mode))
            except OSError:
                pass
            _unlink_replace_target(dst)
            _durable_replace(temporary, dst)
        finally:
            _unlink_replace_target(temporary)
    return outcome, source_tuple, _regular_file_tuple(dst), True


def _read_receipt(dest: Path, session_id: str) -> _protection.Receipt | None:
    """The projection receipt last written for *session_id*, if usable."""
    try:
        directory = _existing_relative_directory(dest, Path(_protection.RECEIPT_DIR))
        if directory is None:
            return None
        with open_regular_no_follow(directory / f"{session_id}.json") as stream:
            raw = stream.read(_protection.MAX_RECEIPT_BYTES + 1)
    except (OSError, ValueError):
        return None
    return _protection.decode_receipt(raw, session_id)


def _remove_receipt(dest: Path, session_id: str) -> None:
    directory = _existing_relative_directory(dest, Path(_protection.RECEIPT_DIR))
    if directory is not None:
        _unlink_replace_target(directory / f"{session_id}.json")


def _write_receipt(dest: Path, session_id: str, payload: bytes | None) -> None:
    """Durably replace one projection receipt (remove it for ``None``)."""
    if payload is None:
        _remove_receipt(dest, session_id)
        return
    directory = _ensure_relative_directory(dest, Path(_protection.RECEIPT_DIR))
    final = directory / f"{session_id}.json"
    temporary = final.with_name(f".{final.name}.{short_unique_id()}.tmp")
    try:
        _write_bytes_fsync(temporary, payload)
        _unlink_replace_target(final)
        _durable_replace(temporary, final)
    finally:
        _unlink_replace_target(temporary)


def _protection_entry(rel: Path, reason: str, prior_copy: str) -> dict[str, str]:
    return {"path": rel.as_posix(), "reason": reason, "prior_copy": prior_copy}


_DOCTOR_SCAN_LIMIT = 20_000


def _unprotected_destination_files(
    settings: _protection.ProtectionSettings, dest: Path,
) -> tuple[int, bool]:
    """Count destination files the current protection settings did not produce.

    Returns ``(count, truncated)``. The walk is bounded so doctor stays quick
    on a large destination; it never reads file content beyond receipts.
    """
    stale = sum(1 for name in SESSION_INDEX_NAMES if _regular_file_tuple(dest / name))
    budget = _DOCTOR_SCAN_LIMIT
    if settings.essential_only:
        provenance = _existing_relative_directory(dest, Path("provenance"))
        if provenance is not None:
            for _path in _iter_regular_source_files(provenance):
                budget -= 1
                if budget < 0:
                    return stale, True
                stale += 1
    state = _existing_relative_directory(dest, Path("session-state"))
    if state is None:
        return stale, False
    with os.scandir(_windows_extended_path(state)) as listing:
        session_ids = sorted(
            entry.name for entry in listing if entry.is_dir(follow_symlinks=False)
        )
    for session_id in session_ids:
        session_dir = state / session_id
        receipt = (
            _read_receipt(dest, session_id)
            if settings.redact_secrets and _protection.safe_member(session_id)
            else None
        )
        for path in _iter_regular_source_files(session_dir):
            if _is_excluded_name(path.name):
                continue
            budget -= 1
            if budget < 0:
                return stale, True
            member = path.relative_to(session_dir).as_posix()
            if not settings.member_included(member):
                stale += 1
            elif settings.redact_secrets:
                entry = receipt.files.get(member) if receipt is not None else None
                if (
                    _protection.classify_prior_copy(entry, _regular_file_tuple(path))
                    != _protection.PRIOR_COPY_PROTECTED
                ):
                    stale += 1
    return stale, False


def _protection_doctor_detail(
    settings: _protection.ProtectionSettings, root: Path, machine: str | None = None,
) -> str:
    """Doctor detail for session protection, warning about earlier copies.

    Protection never removes or rewrites what is already published, so a
    destination can still hold files from before the settings were enabled.
    """
    detail = settings.describe()
    if not machine:
        return (
            f"{detail}; note: existing destination copies are not removed or "
            "rewritten -- files outside session_files and copies published "
            "before redaction stay as previously published"
        )
    try:
        dest = _existing_relative_directory(root, Path(machine))
        if dest is None:
            return f"{detail}; nothing published for this machine yet"
        stale, truncated = _unprotected_destination_files(settings, dest)
    except (OSError, ValueError):
        return f"{detail}; warning: destination not inspected"
    if stale:
        bound = "at least " if truncated else ""
        return (
            f"{detail}; warning: {bound}{stale} destination file(s) were not "
            "produced under these settings and stay as previously published "
            "(remove them manually if they must not be kept)"
        )
    if truncated:
        return (
            f"{detail}; warning: destination check stopped after "
            f"{_DOCTOR_SCAN_LIMIT} files"
        )
    return f"{detail}; no earlier unprotected destination copies found"


class FilesystemTarget(Target):
    """Base for targets that publish to a local-or-mounted directory root."""

    rescue_compare_and_set = True

    def _root(self) -> Path:  # pragma: no cover - abstract-ish
        raise NotImplementedError

    def push(
        self, source: Path, machine: str, include_sessions: set[str] | None = None,
        *, batch_mode: bool = False, source_identity: SourceIdentityLike | None = None,
    ) -> PushResult:
        try:
            safe_source = _existing_real_directory(source)
        except OSError as exc:
            return PushResult(ok=False, detail=f"unsafe source: {exc}")
        if safe_source is None:
            return PushResult(ok=False, detail=f"source not found: {source}")
        source = safe_source
        try:
            detritus = discover_session_detritus(source, include_sessions)
        except OSError as exc:
            return PushResult(ok=False, detail=f"detritus discovery failed: {exc}")
        try:
            root = self._root()
            dest = _ensure_relative_directory(root, Path(machine), durable=source_identity is not None)
        except OSError as exc:
            return PushResult(
                ok=False,
                detail=f"cannot create safe destination for {machine}: {exc}",
            )
        with publication_admission.publication_transaction(
            dest, source_identity, publication_key=machine,
            supports_identity_admission=self.rescue_compare_and_set
        ) as admitted:
            if admitted is not None:
                return admitted
            if self.protection is not None and self.protection.enabled:
                return self._push_protected(
                    source,
                    dest,
                    machine,
                    include_sessions,
                    detritus,
                    batch_mode=batch_mode,
                    source_identity=source_identity,
                )

            copied = 0
            nbytes = 0
            locked_paths: list[Path] = []
            # batch_mode falls through to the plain copy-with-defer loop below
            # like an unfiltered push; real repo-scope filtering takes the
            # atomic rescue path.
            if include_sessions is not None and not batch_mode:
                lock_file = dest / ".session-sync-rescue.lock"
                cleanup_warnings = []
                try:
                    with sync_lock(lock_file, timeout=30) as acquired:
                        if not acquired:
                            return PushResult(
                                ok=False,
                                detail=f"destination rescue lock is busy: {lock_file}",
                            )
                        for _ in range(2):
                            pass_copied, pass_bytes, cleanup_warning = (
                                _replace_selected_sessions(
                                    source,
                                    dest,
                                    include_sessions,
                                    detritus,
                                )
                            )
                            copied += pass_copied
                            nbytes += pass_bytes
                            if cleanup_warning:
                                cleanup_warnings.append(cleanup_warning)
                            latest_detritus = discover_session_detritus(
                                source,
                                include_sessions,
                            )
                            if latest_detritus.roots == detritus.roots:
                                detritus = latest_detritus
                                break
                            detritus = latest_detritus
                        else:
                            return PushResult(
                                ok=False,
                                detail="source detritus changed during publication; retry",
                            )
                except OSError as exc:
                    return PushResult(ok=False, detail=f"session replace failed: {exc}")
                session_count = _count_sessions(dest)
                write_sync_meta(
                    dest,
                    machine,
                    self.name,
                    "ok",
                    session_count,
                    excluded_roots=(str(root) for root in detritus.roots),
                    excluded_file_count=detritus.file_count,
                    excluded_byte_count=detritus.byte_count,
                    excluded_measurement_complete=detritus.measurement_complete,
                )
                detail = f"-> {dest}"
                if cleanup_warnings:
                    detail += (
                        " (replacement cleanup deferred: "
                        f"{'; '.join(cleanup_warnings)})"
                    )
                return PushResult(
                    ok=True,
                    detail=detail,
                    file_count=copied,
                    byte_count=nbytes,
                    excluded_file_count=detritus.file_count,
                    excluded_byte_count=detritus.byte_count,
                    excluded_roots=tuple(str(root) for root in detritus.roots),
                    excluded_measurement_complete=detritus.measurement_complete,
                )
            try:
                destination_detritus = discover_session_detritus(dest, include_sessions)
            except OSError as exc:
                return PushResult(
                    ok=False,
                    detail=f"destination detritus discovery failed: {exc}",
                )
            try:
                cleanup_roots = sorted(
                    set(detritus.roots) | set(destination_detritus.roots)
                )
                for relative in cleanup_roots:
                    stale = _existing_relative_directory(dest, relative)
                    if stale is not None:
                        _remove_path_checked(stale)
            except OSError as exc:
                return PushResult(
                    ok=False,
                    detail=f"detritus cleanup failed for {relative}: {exc}",
                )
            try:
                marker_name_casefold = publication_admission.PUBLICATION_IDENTITY_MARKER.casefold()
                source_files = _iter_regular_source_files(source, detritus.roots)
                for src_file in source_files:
                    rel = src_file.relative_to(source)
                    # Marker reserved only for identified publications (see
                    # publication_admission.check_publication_identity).
                    # Case-insensitive: a case-insensitive destination
                    # filesystem resolves any differently-cased spelling to
                    # the same real marker path.
                    is_root_marker = (
                        rel.parent == Path(".")
                        and src_file.name.casefold() == marker_name_casefold
                    )
                    if _is_excluded_name(src_file.name) or (
                        source_identity is not None and is_root_marker
                    ):
                        continue
                    if not is_session_path_included(rel, include_sessions, batch_mode=batch_mode):
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
                            if isinstance(exc, (_LockedSourceFile, _SourceChangedDuringCopy)):
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

            try:
                latest_detritus = discover_session_detritus(source, include_sessions)
            except OSError as exc:
                return PushResult(
                    ok=False,
                    detail=f"detritus revalidation failed: {exc}",
                )
            if latest_detritus.roots != detritus.roots:
                new_roots = set(latest_detritus.roots) - set(detritus.roots)
                try:
                    for relative in sorted(new_roots):
                        stale = _existing_relative_directory(dest, relative)
                        if stale is not None:
                            _remove_path_checked(stale)
                except OSError as exc:
                    return PushResult(
                        ok=False,
                        detail=f"new detritus cleanup failed for {relative}: {exc}",
                    )
                return PushResult(
                    ok=False,
                    detail="source detritus changed during publication; retry",
                )

            session_count = _count_sessions(dest)
            status = "partial" if locked_paths else "ok"
            write_sync_meta(
                dest,
                machine,
                self.name,
                status,
                session_count,
                deferred_files=(str(path) for path in locked_paths),
                excluded_roots=(str(root) for root in detritus.roots),
                excluded_file_count=detritus.file_count,
                excluded_byte_count=detritus.byte_count,
                excluded_measurement_complete=detritus.measurement_complete,
            )
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
                excluded_file_count=detritus.file_count,
                excluded_byte_count=detritus.byte_count,
                excluded_roots=tuple(str(root) for root in detritus.roots),
                excluded_measurement_complete=detritus.measurement_complete,
                deferred_sessions=_deferred_session_ids(locked_paths),
                index_deferred=_index_deferred(locked_paths),
            )

    def _push_protected(
        self,
        source: Path,
        dest: Path,
        machine: str,
        include_sessions: set[str] | None,
        detritus: Any,
        *,
        batch_mode: bool,
        source_identity: SourceIdentityLike | None,
    ) -> PushResult:
        """Publish under ``sync.redact_secrets`` and/or ``sync.session_files``.

        Mirrors the file-by-file loop of :meth:`push`, but copies only the
        files ``session_files`` selects and, when redacting, a derived copy
        of each one. A file whose protected copy cannot be produced keeps
        whatever version the destination already has: a deferred file is
        retried on the next run, a withheld one is listed. Every other file
        still publishes.
        """
        with ExitStack() as stack:
            if include_sessions is not None and not batch_mode:
                # Same exclusion as the repo-scoped replacement in push().
                lock_file = dest / ".session-sync-rescue.lock"
                try:
                    acquired = stack.enter_context(sync_lock(lock_file, timeout=30))
                except OSError as exc:
                    return PushResult(
                        ok=False, detail=f"destination rescue lock failed: {exc}",
                    )
                if not acquired:
                    return PushResult(
                        ok=False,
                        detail=f"destination rescue lock is busy: {lock_file}",
                    )
            return self._publish_protected(
                source,
                dest,
                machine,
                include_sessions,
                detritus,
                batch_mode=batch_mode,
                source_identity=source_identity,
            )

    def _publish_protected(
        self,
        source: Path,
        dest: Path,
        machine: str,
        include_sessions: set[str] | None,
        detritus: Any,
        *,
        batch_mode: bool,
        source_identity: SourceIdentityLike | None,
    ) -> PushResult:
        settings = self.protection
        if settings is None:  # pragma: no cover - guarded by push()
            raise RuntimeError("session protection is not configured")
        redacting = settings.redact_secrets
        fingerprint = settings.transform_fingerprint()
        copied = 0
        nbytes = 0
        current = 0
        redacted = 0
        unselected = 0
        locked_paths: list[Path] = []
        held_back: list[Path] = []
        deferred: list[dict[str, str]] = []
        withheld: list[dict[str, str]] = []
        receipts: dict[str, _protection.Receipt | None] = {}
        entries: dict[str, dict[str, dict[str, Any]]] = {}

        def hold(rel: Path, entry: dict[str, Any]) -> None:
            item = _protection_entry(rel, entry["reason"], entry["prior_copy"])
            if entry["state"] == _protection.DEFERRED:
                deferred.append(item)
                held_back.append(rel)
            else:
                withheld.append(item)

        try:
            destination_detritus = discover_session_detritus(dest, include_sessions)
        except OSError as exc:
            return PushResult(
                ok=False,
                detail=f"destination detritus discovery failed: {exc}",
            )
        try:
            cleanup_roots = sorted(set(detritus.roots) | set(destination_detritus.roots))
            for relative in cleanup_roots:
                stale = _existing_relative_directory(dest, relative)
                if stale is not None:
                    _remove_path_checked(stale)
        except OSError as exc:
            return PushResult(
                ok=False,
                detail=f"detritus cleanup failed for {relative}: {exc}",
            )
        try:
            marker_name_casefold = publication_admission.PUBLICATION_IDENTITY_MARKER.casefold()
            receipt_dir_casefold = _protection.RECEIPT_DIR.casefold()
            for src_file in _iter_regular_source_files(source, detritus.roots):
                rel = src_file.relative_to(source)
                is_root_marker = (
                    rel.parent == Path(".")
                    and src_file.name.casefold() == marker_name_casefold
                )
                if _is_excluded_name(src_file.name) or (
                    source_identity is not None and is_root_marker
                ):
                    continue
                if rel.parts[0].casefold() == receipt_dir_casefold:
                    continue
                if not is_session_path_included(rel, include_sessions, batch_mode=batch_mode):
                    continue
                if not settings.path_included(rel.as_posix()):
                    # The index rides along with every batch; count it once
                    # per session file instead of once per batch.
                    if not (len(rel.parts) == 1 and rel.name in SESSION_INDEX_NAMES):
                        unselected += 1
                    continue
                dst_file = dest / rel
                if redacting and len(rel.parts) == 1 and rel.name in SESSION_INDEX_NAMES:
                    # The session index is a database: never read, never published.
                    hold(rel, {
                        "state": _protection.WITHHELD,
                        "reason": _protection.REASON_DATABASE,
                        "prior_copy": _protection.classify_prior_copy(
                            None, _regular_file_tuple(dst_file),
                        ),
                    })
                    continue
                try:
                    _ensure_relative_directory(dest, rel.parent)
                except OSError as exc:
                    return PushResult(ok=False, detail=f"unsafe destination: {exc}")
                if not redacting:
                    if _needs_copy(src_file, dst_file):
                        try:
                            _copy_replace(src_file, dst_file)
                        except OSError as exc:
                            if isinstance(exc, (_LockedSourceFile, _SourceChangedDuringCopy)):
                                locked_paths.append(rel)
                                continue
                            return PushResult(
                                ok=False,
                                detail=f"copy failed for {rel}: {exc}",
                            )
                        copied += 1
                        nbytes += os.stat(_windows_extended_path(src_file)).st_size
                    current += 1
                    continue

                parts = rel.parts
                session_member = len(parts) >= 3 and parts[0] == "session-state"
                sid = parts[1] if session_member else ""
                member = "/".join(parts[2:]) if session_member else rel.name
                previous: dict[str, Any] | None = None
                carried: dict[str, Any] | None = None
                if session_member:
                    if sid not in entries:
                        receipts[sid] = _read_receipt(dest, sid)
                        entries[sid] = {}
                    receipt = receipts[sid]
                    if receipt is not None:
                        previous = receipt.files.get(member)
                        if receipt.settings == fingerprint:
                            carried = previous
                    info = _lstat(src_file)
                    reuse = _protection.reusable_entry(
                        receipt, fingerprint, member, info.st_size, info.st_mtime_ns,
                    )
                    if reuse is not None:
                        # Unchanged source under the same transform: the
                        # recorded outcome still holds, so skip re-deriving.
                        dest_tuple = _regular_file_tuple(dst_file)
                        if reuse["state"] != _protection.PUBLISHED:
                            entry = _protection.unpublished_entry(
                                reuse["state"],
                                reuse["reason"],
                                info.st_size,
                                info.st_mtime_ns,
                                _protection.classify_prior_copy(previous, dest_tuple),
                                dest_tuple,
                            )
                            entries[sid][member] = entry
                            hold(rel, entry)
                            continue
                        if dest_tuple == (reuse["dest_size"], reuse["dest_mtime_ns"]):
                            entries[sid][member] = reuse
                            current += 1
                            continue
                try:
                    outcome, source_tuple, dest_tuple, replaced = _protected_copy(
                        src_file, dst_file, member, compare_existing=True,
                    )
                except OSError as exc:
                    if isinstance(exc, (_LockedSourceFile, _SourceChangedDuringCopy)):
                        locked_paths.append(rel)
                        if carried is not None:
                            entries[sid][member] = carried
                        continue
                    return PushResult(
                        ok=False,
                        detail=f"copy failed for {rel}: {exc}",
                    )
                if outcome.published:
                    current += 1
                    redacted += outcome.redacted
                    if replaced:
                        copied += 1
                        nbytes += source_tuple[0]
                    if session_member and dest_tuple is not None:
                        entries[sid][member] = _protection.published_entry(
                            *source_tuple, *dest_tuple, outcome,
                        )
                    continue
                dest_tuple = _regular_file_tuple(dst_file)
                entry = _protection.unpublished_entry(
                    outcome.state,
                    outcome.reason or "",
                    *source_tuple,
                    _protection.classify_prior_copy(previous, dest_tuple),
                    dest_tuple,
                )
                if session_member:
                    entries[sid][member] = entry
                hold(rel, entry)
        except OSError as exc:
            return PushResult(ok=False, detail=f"cannot inspect source: {exc}")

        receipt_failures = 0
        for sid, files in entries.items():
            if not _protection.safe_member(sid):
                continue
            payload = _protection.encode_receipt(sid, fingerprint, files) if files else None
            try:
                _write_receipt(dest, sid, payload)
            except OSError:
                # Only costs a re-derivation next run: identical copies are
                # compared, not rewritten.
                receipt_failures += 1

        try:
            latest_detritus = discover_session_detritus(source, include_sessions)
        except OSError as exc:
            return PushResult(
                ok=False,
                detail=f"detritus revalidation failed: {exc}",
            )
        if latest_detritus.roots != detritus.roots:
            new_roots = set(latest_detritus.roots) - set(detritus.roots)
            try:
                for relative in sorted(new_roots):
                    stale = _existing_relative_directory(dest, relative)
                    if stale is not None:
                        _remove_path_checked(stale)
            except OSError as exc:
                return PushResult(
                    ok=False,
                    detail=f"new detritus cleanup failed for {relative}: {exc}",
                )
            return PushResult(
                ok=False,
                detail="source detritus changed during publication; retry",
            )

        session_count = _count_sessions(dest)
        pending = locked_paths + held_back
        summary = _protection.summarize(
            settings,
            published=current,
            redacted=redacted,
            deferred=deferred,
            withheld=withheld,
            unselected=unselected,
        )
        write_sync_meta(
            dest,
            machine,
            self.name,
            "partial" if pending else "ok",
            session_count,
            deferred_files=(str(path) for path in pending),
            excluded_roots=(str(root) for root in detritus.roots),
            excluded_file_count=detritus.file_count,
            excluded_byte_count=detritus.byte_count,
            excluded_measurement_complete=detritus.measurement_complete,
            reason="protected-deferral" if held_back else None,
            protection=summary,
        )
        notes: list[str] = []
        if locked_paths:
            examples = ", ".join(str(path) for path in locked_paths[:3])
            notes.append(
                f"skipped {len(locked_paths)} locked file(s), will retry: {examples}"
            )
        if unselected:
            notes.append(f"{unselected} file(s) not in session_files left unpublished")
        if receipt_failures:
            notes.append(f"{receipt_failures} projection receipt(s) not recorded")
        detail = f"-> {dest}"
        if notes:
            detail += " (" + "; ".join(notes) + ")"
        return PushResult(
            ok=True,
            detail=detail,
            file_count=copied,
            byte_count=nbytes,
            excluded_file_count=detritus.file_count,
            excluded_byte_count=detritus.byte_count,
            excluded_roots=tuple(str(root) for root in detritus.roots),
            excluded_measurement_complete=detritus.measurement_complete,
            deferred_sessions=_deferred_session_ids(pending),
            index_deferred=_index_deferred(locked_paths),
            deferred_files=tuple(path.as_posix() for path in pending),
            withheld_files=tuple(item["path"] for item in withheld),
            protection_summary=summary,
        )

    def _record_process_log_health(
        self, machine: str, status: str, deferred_files: tuple[str, ...] = (),
        *, reason: str | None = None,
    ) -> None:
        """Update only the process-log leg under existing machine metadata."""
        try:
            machine_root = _existing_relative_directory(self._root(), Path(machine))
        except OSError as exc:
            logging.getLogger("agent-logger.sync-meta").warning(
                "cannot locate process-log sync metadata for %s: %s", machine, exc,
            )
            return
        if machine_root is None:
            return
        write_process_log_meta(machine_root, status, deferred_files, reason=reason)

    def push_process_logs(self, log_root: Path, machine: str) -> PushResult:
        """Publish logs with pinned source/destination ancestry, including creation.

        Health metadata and session-state remain outside this boundary (#5727).
        """
        try:
            log_root = _anchored_path(log_root)
            try:
                source_identity = _lstat(log_root)
            except FileNotFoundError:
                source_identity = None
            redirected = source_identity is not None and is_link_or_reparse(
                log_root, source_identity.st_mode,
            )
            if source_identity is not None and (
                not stat.S_ISDIR(source_identity.st_mode)
                or redirected
            ):
                reason = "symlink/reparse" if redirected else "not a directory"
                raise OSError(f"process-log source must be a real directory: {log_root} ({reason})")
        except OSError as exc:
            self._record_process_log_health(machine, "partial", reason="unsafe process-log source")
            return PushResult(ok=False, detail=f"unsafe process-log source: {exc}")
        if source_identity is None:
            self._record_process_log_health(machine, "partial", reason="no process-log source")
            return PushResult(ok=True, detail="no process-log source")
        try:
            root = self._root()
            relative = Path(machine) / "logs"
            _validate_relative_path(relative)
            dest = _anchored_path(root) / relative
        except OSError as exc:
            self._record_process_log_health(machine, "partial", reason="unsafe process-log destination")
            return PushResult(
                ok=False,
                detail=f"cannot create safe destination for {machine}/logs: {exc}",
            )
        try:
            copied, nbytes, locked_paths = _copy_process_logs(log_root, dest, source_identity)
        except (OSError, ValueError) as exc:
            # ValueError surfaces here only from the root-pinning open
            # itself (e.g. the configured root was replaced with a symlink
            # between this method's own validation above and the copy) --
            # a genuinely unsafe root, not the benign per-file skip
            # `_copy_process_logs` already handles internally.
            self._record_process_log_health(machine, "partial", reason="process-log copy failed")
            return PushResult(ok=False, detail=f"process-log copy failed: {exc}")
        detail = f"-> {dest}"
        if locked_paths:
            examples = ", ".join(path.name for path in locked_paths[:3])
            detail += f" (skipped {len(locked_paths)} locked file(s), will retry: {examples})"
            # ok=True here means "the pass ran," not "every file landed" --
            # at least one log was deferred (locked/rotated), so persisted
            # health must reflect that, exactly like an outright failure.
        self._record_process_log_health(
            machine, "partial" if locked_paths else "ok",
            tuple(path.name for path in locked_paths),
        )
        return PushResult(ok=True, detail=detail, file_count=copied, byte_count=nbytes,
                          process_logs_complete=not locked_paths)

    def process_log_readback(self, machine: str) -> ProcessLogReadback:
        return filesystem_log_readback(self.name, self._root(), machine)

    def sync_status(self, machine: str) -> SyncStatus:
        try:
            machine_root = _existing_relative_directory(self._root(), Path(machine))
            metadata = (
                read_sync_meta(machine_root)
                if machine_root is not None
                else None
            )
            return SyncStatus(supported=True, metadata=metadata)
        except (OSError, ValueError) as exc:
            return SyncStatus(supported=True, error=str(exc))

    def heartbeat(self, machine: str) -> None:
        """Re-stamp destination health metadata, no transfer -- see
        ``agent_logger.sync.meta.heartbeat_sync_meta``.

        A no-op when the machine root doesn't already exist: a deleted
        destination must never get a fresh "ok" heartbeat recreating an
        empty directory and masking that its sessions are actually gone.
        """
        try:
            dest = _existing_relative_directory(self._root(), Path(machine))
        except OSError:
            return
        if dest is None:
            return
        heartbeat_sync_meta(dest, machine, self.name, _count_sessions(dest))

    def fleet_sync_status(self) -> FleetSyncStatus:
        """Read bounded per-machine metadata without entering session payloads."""
        try:
            root = _existing_real_directory(self._root())
            if root is None:
                return FleetSyncStatus(supported=True)
            machines: dict[str, SyncStatus] = {}
            pending = [(root, Path())]
            visited_directories = 0
            visited_entries = 0
            while pending:
                directory, relative = pending.pop()
                visited_directories += 1
                if visited_directories > _MAX_FLEET_DIRECTORIES:
                    raise _FleetScanLimitError(
                        f"fleet scan exceeds {_MAX_FLEET_DIRECTORIES} directories"
                    )
                metadata_path = directory / "sync-meta.json"
                try:
                    metadata_mode = os.lstat(
                        _windows_extended_path(metadata_path)
                    ).st_mode
                except FileNotFoundError:
                    metadata_mode = None
                except OSError as exc:
                    if not relative.parts:
                        raise
                    machines[relative.as_posix()] = SyncStatus(
                        supported=True,
                        error=str(exc),
                    )
                    if len(machines) > _MAX_FLEET_MACHINES:
                        raise OSError(
                            f"fleet metadata exceeds {_MAX_FLEET_MACHINES} machines"
                        )
                    continue
                if relative.parts and metadata_mode is not None and (
                    stat.S_ISREG(metadata_mode)
                    and not is_link_or_reparse(metadata_path, metadata_mode)
                ):
                    machine = relative.as_posix()
                    try:
                        metadata = read_sync_meta(directory)
                        machines[machine] = SyncStatus(
                            supported=True,
                            metadata=metadata,
                        )
                    except OSError as exc:
                        machines[machine] = SyncStatus(
                            supported=True,
                            error=str(exc),
                        )
                    if len(machines) > _MAX_FLEET_MACHINES:
                        raise OSError(
                            f"fleet metadata exceeds {_MAX_FLEET_MACHINES} machines"
                        )
                    continue
                if len(relative.parts) >= _MAX_FLEET_MACHINE_DEPTH:
                    continue
                children: list[tuple[str, int]] = []
                try:
                    with os.scandir(_windows_extended_path(directory)) as entries:
                        for entry in entries:
                            visited_entries += 1
                            if visited_entries > _MAX_FLEET_ENTRIES:
                                raise _FleetScanLimitError(
                                    f"fleet scan exceeds {_MAX_FLEET_ENTRIES} entries"
                                )
                            try:
                                mode = entry.stat(follow_symlinks=False).st_mode
                            except OSError:
                                continue
                            children.append((entry.name, mode))
                except _FleetScanLimitError:
                    raise
                except OSError as exc:
                    if not relative.parts:
                        raise
                    machines[relative.as_posix()] = SyncStatus(
                        supported=True,
                        error=str(exc),
                    )
                    if len(machines) > _MAX_FLEET_MACHINES:
                        raise OSError(
                            f"fleet metadata exceeds {_MAX_FLEET_MACHINES} machines"
                        )
                    continue
                if relative and any(
                    name in {"session-state", "provenance", "archived"}
                    for name, _mode in children
                ):
                    machines[relative.as_posix()] = SyncStatus(
                        supported=True,
                        metadata=None,
                    )
                    if len(machines) > _MAX_FLEET_MACHINES:
                        raise OSError(
                            f"fleet metadata exceeds {_MAX_FLEET_MACHINES} machines"
                        )
                    continue
                for name, mode in children:
                    path = directory / name
                    if stat.S_ISDIR(mode) and not is_link_or_reparse(path, mode):
                        pending.append((path, relative / name))
            return FleetSyncStatus(supported=True, machines=machines)
        except OSError as exc:
            return FleetSyncStatus(supported=True, error=str(exc))

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
        receipts = _existing_relative_directory(
            machine_root,
            Path(_protection.RECEIPT_DIR),
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
                    snapshot_session = snapshots / rescue_session_key(d.name)
                    _remove_path_checked(snapshot_session)
                if receipts is not None:
                    (receipts / f"{d.name}.json").unlink(missing_ok=True)
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
        removed_ids: set[str] = set()
        for arc in archived.iterdir():
            if not arc.is_file() or not any(
                arc.name.endswith(s) for s in sessions._ARCHIVE_SUFFIXES
            ):
                continue
            sid = sessions._archive_stem(arc)
            live = state / sid
            if sid in removed_ids or not live.is_dir():
                continue
            ref = SessionRef(id=sid, kind="archive", path=arc, store=archived)
            if not sessions.verify_archive(ref):
                continue
            if dry_run or sessions.force_rmtree(live):
                # Count successful removes only; OneDrive placeholders can defeat rmtree.
                removed_ids.add(sid)
        return len(removed_ids)

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
        if self.protection is not None and self.protection.enabled:
            result.add(
                "session protection",
                True,
                _protection_doctor_detail(self.protection, root, self.doctor_machine),
            )
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
        return Path.home() / ".agent-logger" / "sessions"  # marketplace-isolation: allow legacy-compatibility


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
