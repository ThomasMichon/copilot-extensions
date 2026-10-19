"""Verified, additive ZIP snapshots of settled local process logs."""

from __future__ import annotations

import hashlib
import logging
import os
import re
import stat
import sys
import time
import zipfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, BinaryIO

from agent_logger.sync.archive_directory import ArchiveDirectory, archive_directory
from agent_logger.sync.log_archive_readback import LogArchiveRevision
from agent_logger.sync.targets.claim_file import create_claim_file

log = logging.getLogger("agent-logger.log-archives")
_LOG_NAME = re.compile(r"process-[0-9]{1,20}-([0-9]{1,10})\.log\Z")
_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class LogArchiveSettings:
    enabled: bool = False
    local_source: bool = False
    min_quiet_seconds: int = 600


def resolve_log_archive_settings(raw: object) -> LogArchiveSettings:
    """Validate explicit snapshot opt-in and local-producer ownership."""
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("process_logs.zip_snapshots must be an object")
    enabled = raw.get("enabled", False)
    local_source = raw.get("local_source", False)
    quiet = raw.get("min_quiet_seconds", 600)
    if not isinstance(enabled, bool) or not isinstance(local_source, bool):
        raise ValueError("process-log ZIP enabled/local_source must be booleans")
    if isinstance(quiet, bool) or not isinstance(quiet, int) or quiet <= 0:
        raise ValueError("process-log min_quiet_seconds must be a positive integer")
    if enabled and not local_source:
        raise ValueError("process-log ZIP snapshots require explicit local_source=true")
    return LogArchiveSettings(enabled, local_source, quiet)


@dataclass
class LogArchiveResult:
    """One pass's evidence, without implying source retirement."""

    scanned: int = 0
    archived: int = 0
    unchanged: int = 0
    skipped_recent: int = 0
    skipped_active: int = 0
    skipped_unclassified: int = 0
    failed: list[str] = field(default_factory=list)


def producer_exited(pid: int) -> bool:
    """Prove a local PID is absent; permission/probe failures are not exit proof.

    PID reuse conservatively keeps a log. This does not identify a producer on
    another machine: callers must use the local producer's original log root.
    """
    if not 0 < pid <= 2**31 - 1:
        raise ValueError("process-log PID is outside the supported positive range")
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    close_handle = kernel.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = open_process(0x1000, False, pid)
    if not handle:
        error = ctypes.get_last_error()
        if error == 87:  # ERROR_INVALID_PARAMETER: the positive PID is absent.
            return True
        raise ctypes.WinError(error)
    try:
        get_exit_code = kernel.GetExitCodeProcess
        get_exit_code.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        get_exit_code.restype = wintypes.BOOL
        code = wintypes.DWORD()
        if not get_exit_code(handle, ctypes.byref(code)):
            raise ctypes.WinError(ctypes.get_last_error())
        return code.value != 259
    finally:
        if not close_handle(handle):
            raise ctypes.WinError(ctypes.get_last_error())


def _fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int]:
    # Windows lstat/fstat can disagree on ctime (change time vs creation time).
    # Use birth time consistently there; protected handles prevent writes/renames.
    identity_time = (
        getattr(info, "st_birthtime_ns", info.st_ctime_ns)
        if sys.platform == "win32" else info.st_ctime_ns
    )
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, identity_time


def _hash_stream(stream: IO[bytes], size: int) -> str:
    digest = hashlib.sha256()
    remaining = size
    while remaining:
        chunk = stream.read(min(_CHUNK_BYTES, remaining))
        if not chunk:
            raise ValueError("process-log evidence truncated while reading")
        digest.update(chunk)
        remaining -= len(chunk)
    if stream.read(1):
        raise ValueError("process-log evidence grew while reading")
    return digest.hexdigest()


def _verify_archive(
    root: ArchiveDirectory, archive_name: str, name: str, expected_hash: str, size: int,
) -> None:
    before = root.stat(archive_name)
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"process-log ZIP is not a regular file: {archive_name}")
    with root.open(archive_name, protected=True) as raw:
        if _fingerprint(os.fstat(raw.fileno())) != _fingerprint(before):
            raise ValueError(f"process-log ZIP changed while opening: {archive_name}")
        _verify_zip_stream(raw, name, expected_hash, size)
        if _fingerprint(root.stat(archive_name)) != _fingerprint(before):
            raise ValueError(f"process-log ZIP changed during verification: {archive_name}")


def _verify_zip_stream(raw: BinaryIO, name: str, expected_hash: str, size: int) -> None:
    raw.seek(0)
    with zipfile.ZipFile(raw) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].filename != name:
            raise ValueError("process-log ZIP member mismatch")
        info = members[0]
        if info.file_size != size or stat.S_ISLNK(info.external_attr >> 16):
            raise ValueError("process-log ZIP metadata mismatch")
        if info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED):
            raise ValueError("process-log snapshot ZIP compression is unsupported")
        with archive.open(info) as member:
            if _hash_stream(member, size) != expected_hash:
                raise ValueError("process-log ZIP content mismatch")


def _archive_opened(
    root: ArchiveDirectory, name: str, raw: BinaryIO, pid: int,
    selected: os.stat_result, *, dry_run: bool,
) -> bool:
    before = os.fstat(raw.fileno())
    if _fingerprint(before) != _fingerprint(selected):
        raise ValueError("process-log archive candidate changed while opening")
    digest = _hash_stream(raw, before.st_size)
    destination = LogArchiveRevision(name, digest, before.st_size).archive_name

    def validate_source() -> None:
        if (
            _fingerprint(os.fstat(raw.fileno())) != _fingerprint(before)
            or _fingerprint(root.stat(name)) != _fingerprint(before)
        ):
            raise ValueError(f"process-log changed during ZIP snapshot: {name}")
        if not producer_exited(pid):
            raise ValueError(f"process-log producer became active during ZIP snapshot: {name}")

    validate_source()
    try:
        root.stat(destination)
    except FileNotFoundError:
        exists = False
    else:
        exists = True
    if exists:
        _verify_archive(root, destination, name, digest, before.st_size)
        validate_source()
        return False
    if dry_run:
        return True

    with create_claim_file(root.path, directory_fd=root.fd, mode=0o600) as snapshot:
        output = snapshot.stream
        raw.seek(0)
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            info = zipfile.ZipInfo(name)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | 0o600) << 16
            with archive.open(info, "w", force_zip64=True) as member:
                remaining = before.st_size
                while remaining:
                    chunk = raw.read(min(_CHUNK_BYTES, remaining))
                    if not chunk:
                        raise ValueError("process-log truncated during ZIP snapshot")
                    member.write(chunk)
                    remaining -= len(chunk)
        output.flush()
        os.fsync(output.fileno())
        _verify_zip_stream(output, name, digest, before.st_size)
        validate_source()
        snapshot.publish(root.path / destination)
        published = root.stat(destination)
        if (published.st_dev, published.st_ino) != snapshot.file_id:
            raise ValueError("process-log ZIP identity changed during publication")
        _verify_zip_stream(output, name, digest, before.st_size)
        validate_source()
        return True


def archive_settled_process_logs(
    log_root: Path, *, min_quiet_seconds: int, dry_run: bool = False,
) -> LogArchiveResult:
    """Snapshot settled local raw logs; preserve every source and existing ZIP.

    The caller holds the configured sync lock. The quiet threshold must be
    explicitly positive; a quiet file whose producer is alive is never eligible.
    Unknown filenames and PIDs are retained, not guessed. Gzip/ZIP inputs remain
    unchanged. Streams are bounded; full member hashes are verified before
    publication. This is additive preservation, not a reclamation policy.
    """
    if (
        isinstance(min_quiet_seconds, bool)
        or not isinstance(min_quiet_seconds, int)
        or min_quiet_seconds <= 0
    ):
        raise ValueError("process-log min_quiet_seconds must be a positive integer")
    root = log_root.absolute()
    try:
        root.lstat()
    except FileNotFoundError:
        log.info("process-log ZIP snapshot skipped: no process-log source")
        return LogArchiveResult()
    with archive_directory(root) as directory:
        return _archive_directory(directory, min_quiet_seconds, dry_run=dry_run)


def _archive_directory(
    root: ArchiveDirectory, min_quiet_seconds: int, *, dry_run: bool,
) -> LogArchiveResult:
    result = LogArchiveResult()
    now = time.time()
    for name in root.names():
        if not name.startswith("process-") or not name.endswith(".log"):
            continue
        result.scanned += 1
        match = _LOG_NAME.fullmatch(name)
        if match is None or not 0 < int(match[1]) <= 2**31 - 1:
            result.skipped_unclassified += 1
            continue
        pid = int(match[1])
        try:
            info = root.stat(name)
            if not stat.S_ISREG(info.st_mode):
                raise ValueError("process-log archive candidate is not a regular file")
            if now - info.st_mtime < min_quiet_seconds:
                result.skipped_recent += 1
                continue
            if not producer_exited(pid):
                result.skipped_active += 1
                continue
            with root.open(name, protected=True) as raw:
                archived = _archive_opened(root, name, raw, pid, info, dry_run=dry_run)
            if archived:
                result.archived += 1
            else:
                result.unchanged += 1
        except (OSError, ValueError, EOFError, zipfile.BadZipFile, RuntimeError, zlib.error) as error:
            message = f"{name}: {error}"
            result.failed.append(message)
            log.warning("process-log ZIP snapshot failed: %s", message)
    return result
