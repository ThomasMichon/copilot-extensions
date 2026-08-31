#!/usr/bin/env python3
"""Resolve, validate, and explicitly mutate marketplace installation context."""
from __future__ import annotations

import argparse
import errno
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import stat
import sys
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import wraps
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

LOCK_SCHEMA = "copilot-extensions.installation-lock"
LOCK_VERSION = 1
LOCK_INITIALIZATION_GRACE_SECONDS = 5.0
LOCK_POLL_SECONDS = 0.01
RUNTIME_SLOT_LOCK_TIMEOUT_SECONDS = 30.0
RUNTIME_SLOT_COMPLETION_LOCK_TIMEOUT_SECONDS = 300.0
MAX_NAMESPACE_LOCATORS = 16
MAX_RECEIPT_GENERATION = (1 << 63) - 1
ROOT_NAMES = ("versions", "snapshots", "state", "run", "logs", "cache", "launchers")
POLICY_SCHEMA = "copilot-extensions.installation-mode"
ACTIVATION_SCHEMA = "copilot-extensions.installation-activation"
TOMBSTONE_SCHEMA = "copilot-extensions.legacy-installation-ownership"
RESOLUTION_SCHEMA = "copilot-extensions.installation-resolution"
SNAPSHOT_PROVENANCE_SCHEMA = "copilot-extensions.snapshot-provenance"
SNAPSHOT_PROVENANCE_FILE = "snapshot-provenance.json"
RUNTIME_SLOT_OWNERSHIP_SCHEMA = "copilot-extensions.runtime-slot-ownership"
RUNTIME_SLOT_OWNERSHIP_FILE = ".runtime-slot-ownership.json"
WINDOWS_ERROR_ACCESS_DENIED = 5
RUNTIME_SLOT_COMPLETION_SCHEMA = "copilot.extensions/runtime-slot-completion/v1"
RUNTIME_SLOT_COMPLETION_FILE = ".runtime-slot-completion.json"
BUILD_COMPLETION_FILE = ".install-complete.json"
CURRENT_VERSION_FILE = "current-version"
LAST_KNOWN_GOOD_FILE = "last-known-good"
RFC3339_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
LOWER_SHA256 = re.compile(r"[0-9a-f]{64}", flags=re.ASCII)
MAX_RECEIPT_PID = (1 << 63) - 1
MAX_SNAPSHOT_ENTRIES = 100_000
MAX_SNAPSHOT_PATH_BYTES = 4_096
MAX_SNAPSHOT_CONTENT_BYTES = 4_294_967_296


@dataclass(frozen=True)
class _ValidatedFileDigest:
    digest: str
    identity: tuple[int, int]
    metadata: tuple[int, int, int, int]


@dataclass
class _SnapshotWalkFrame:
    relative_parts: tuple[str, ...]
    initial_stat: os.stat_result
    manifest: list[tuple[bytes, str]]
    entries: list[tuple[str, str, bytes, os.stat_result, str]]
    next_entry: int = 0


_VALIDATED_FILE_SHA256: ContextVar[dict[str, _ValidatedFileDigest] | None] = (
    ContextVar("_VALIDATED_FILE_SHA256", default=None)
)
_VALIDATION_SCOPE_DEPTH: ContextVar[int] = ContextVar(
    "_VALIDATION_SCOPE_DEPTH",
    default=0,
)


class InstallationContextError(ValueError):
    """Raised when installation identity or ownership cannot be proven."""


@dataclass(frozen=True)
class NormalizedSource:
    """Portable marketplace source identity."""

    kind: str
    canonical: str
    ref: str = ""


def _fail(message: str) -> None:
    raise InstallationContextError(message)


def _validation_scope(function: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(function)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        depth = _VALIDATION_SCOPE_DEPTH.get()
        outermost = depth == 0
        cache_token = None
        if outermost:
            cache_token = _VALIDATED_FILE_SHA256.set({})
        depth_token = _VALIDATION_SCOPE_DEPTH.set(depth + 1)
        try:
            return function(*args, **kwargs)
        finally:
            _VALIDATION_SCOPE_DEPTH.reset(depth_token)
            if cache_token is not None:
                _VALIDATED_FILE_SHA256.reset(cache_token)

    return wrapped


def _property(value: Mapping[str, Any] | None, name: str, default: Any = None) -> Any:
    if value is None:
        return default
    if not isinstance(value, Mapping):
        _fail(f"Expected a JSON object while reading field '{name}'.")
    for candidate in value:
        if candidate != name and candidate.casefold() == name.casefold():
            _fail(f"JSON property '{candidate}' conflicts with exact case '{name}'.")
    if name not in value:
        return default
    result = value[name]
    return default if result is None else result


def _string_property(
    value: Mapping[str, Any],
    name: str,
    default: str = "",
) -> str:
    result = _property(value, name, default)
    if result is None:
        return default
    if not isinstance(result, str):
        _fail(f"Source field '{name}' must be a string.")
    if "\0" in result:
        _fail(f"Source field '{name}' may not contain NUL.")
    return result


def canonical_path(value: str | os.PathLike[str], *, must_exist: bool = False) -> Path:
    """Return an absolute physical path while permitting a missing suffix."""

    text = os.fspath(value)
    if not text.strip():
        _fail("A required path is empty.")
    path = Path(text)
    try:
        resolved = path.resolve(strict=must_exist)
    except (OSError, RuntimeError) as error:
        _fail(f"Cannot resolve path '{value}': {error}")
    if must_exist and not resolved.exists():
        _fail(f"Path does not exist: {value}")
    return resolved


def _path_is_fully_qualified(
    value: str | os.PathLike[str],
    *,
    platform: str | None = None,
) -> bool:
    selected_platform = platform or os.name
    if selected_platform == "nt":
        path = PureWindowsPath(os.fspath(value))
        return bool(path.drive and path.root)
    return PurePosixPath(os.fspath(value)).is_absolute()


def _path_key(path: Path) -> str:
    return os.path.normcase(os.fspath(canonical_path(path)))


def paths_equal(left: str | os.PathLike[str], right: str | os.PathLike[str]) -> bool:
    return _path_key(Path(left)) == _path_key(Path(right))


def _is_link_or_junction(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if is_junction and is_junction():
        return True
    try:
        attributes = os.lstat(path).st_file_attributes
    except (AttributeError, FileNotFoundError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def path_is_within(child: str | os.PathLike[str], parent: str | os.PathLike[str]) -> bool:
    child_path = canonical_path(child)
    parent_path = canonical_path(parent)
    try:
        return os.path.commonpath((_path_key(child_path), _path_key(parent_path))) == _path_key(
            parent_path
        )
    except ValueError:
        return False


def _rename_directory_no_replace(source: Path, destination: Path) -> None:
    if os.name == "nt":
        try:
            os.rename(source, destination)
        except OSError as error:
            if destination.exists() or _is_link_or_junction(destination):
                _fail("Runtime slot appeared during publication; refusing replacement.")
            _fail(f"Cannot publish runtime slot '{destination}': {error}")
        return

    import ctypes

    source_bytes = os.fsencode(source)
    destination_bytes = os.fsencode(destination)
    library = ctypes.CDLL(None, use_errno=True)
    if sys.platform.startswith("linux"):
        rename_no_replace = getattr(library, "renameat2", None)
        if rename_no_replace is None:
            _fail("Atomic no-replace directory publication is unavailable.")
        rename_no_replace.argtypes = (
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename_no_replace.restype = ctypes.c_int
        result = rename_no_replace(
            -100,
            source_bytes,
            -100,
            destination_bytes,
            1,
        )
    elif sys.platform == "darwin":
        rename_no_replace = getattr(library, "renamex_np", None)
        if rename_no_replace is None:
            _fail("Atomic no-replace directory publication is unavailable.")
        rename_no_replace.argtypes = (
            ctypes.c_char_p,
            ctypes.c_char_p,
            ctypes.c_uint,
        )
        rename_no_replace.restype = ctypes.c_int
        result = rename_no_replace(source_bytes, destination_bytes, 0x00000004)
    else:
        _fail("Atomic no-replace directory publication is unavailable.")
    if result == 0:
        return
    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        _fail("Runtime slot appeared during publication; refusing replacement.")
    if error_number in {
        errno.EINVAL,
        errno.ENOSYS,
        errno.ENOTSUP,
        getattr(errno, "EOPNOTSUPP", errno.ENOTSUP),
    }:
        _fail("Atomic no-replace directory publication is unavailable.")
    _fail(
        f"Cannot publish runtime slot '{destination}': "
        f"{os.strerror(error_number)}"
    )


def read_json(path: str | os.PathLike[str]) -> Any:
    canonical = canonical_path(path, must_exist=True)
    try:
        content, validated_stat = _read_regular_file(
            canonical,
            label="JSON document",
            require_stable_identity=True,
        )
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
        _cache_validated_file_digest(canonical, content, validated_stat)
        return value
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        _fail(f"Invalid JSON in '{canonical}': {error}")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _json_bytes(value: Mapping[str, Any]) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _stat_identity(value: os.stat_result) -> tuple[int, int]:
    return value.st_dev, value.st_ino


def _stat_metadata(value: os.stat_result) -> tuple[int, int, int, int]:
    change_time_ns = value.st_ctime_ns
    if os.name == "nt":
        change_time_ns = getattr(value, "st_birthtime_ns", change_time_ns)
    return value.st_mode, value.st_size, value.st_mtime_ns, change_time_ns


def _file_cache_key(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _cache_validated_file_digest(
    path: Path,
    content: bytes,
    validated_stat: os.stat_result,
) -> str:
    cached = _ValidatedFileDigest(
        digest=hashlib.sha256(content).hexdigest(),
        identity=_stat_identity(validated_stat),
        metadata=_stat_metadata(validated_stat),
    )
    key = _file_cache_key(path)
    cache = _VALIDATED_FILE_SHA256.get()
    if cache is None:
        return cached.digest
    previous = cache.get(key)
    if previous is not None and previous != cached:
        _fail(f"File '{path}' changed after it was validated.")
    updated = dict(cache)
    updated[key] = cached
    _VALIDATED_FILE_SHA256.set(updated)
    return cached.digest


def _invalidate_validated_file_digest(path: Path) -> None:
    cache = _VALIDATED_FILE_SHA256.get()
    if cache is None:
        return
    key = _file_cache_key(path)
    if key not in cache:
        return
    updated = dict(cache)
    updated.pop(key)
    _VALIDATED_FILE_SHA256.set(updated)


def _read_regular_file(
    path: Path,
    *,
    label: str,
    require_stable_identity: bool = False,
) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        for _attempt in range(64):
            descriptor = os.open(path, flags)
            opened_stat = os.fstat(descriptor)
            if not stat.S_ISREG(opened_stat.st_mode):
                _fail(f"{label} must be an ordinary file.")
            try:
                named_stat = os.lstat(path)
            except OSError:
                os.close(descriptor)
                descriptor = -1
                continue
            if stat.S_ISLNK(named_stat.st_mode) or _is_link_or_junction(path):
                _fail(f"{label} may not be a symbolic link or reparse point.")
            if not stat.S_ISREG(named_stat.st_mode):
                _fail(f"{label} must be an ordinary file.")
            if _stat_identity(named_stat) == _stat_identity(opened_stat):
                break
            if not require_stable_identity and _attempt == 63:
                break
            os.close(descriptor)
            descriptor = -1
        else:
            _fail(f"{label} changed while it was being opened.")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        final_stat = os.fstat(descriptor)
        if require_stable_identity and (
            _stat_identity(opened_stat) != _stat_identity(final_stat)
            or _stat_metadata(opened_stat) != _stat_metadata(final_stat)
        ):
            _fail(f"{label} changed while it was being read.")
    except OSError as error:
        _fail(f"Cannot read {label.lower()} '{path}': {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        current_stat = os.lstat(path)
    except OSError as error:
        _fail(f"Cannot inspect {label.lower()} '{path}': {error}")
    if _is_link_or_junction(path):
        _fail(f"{label} may not be a symbolic link or reparse point.")
    if not stat.S_ISREG(current_stat.st_mode):
        _fail(f"{label} must be an ordinary file.")
    if require_stable_identity and (
        _stat_identity(current_stat) != _stat_identity(final_stat)
        or _stat_metadata(current_stat) != _stat_metadata(final_stat)
    ):
        _fail(f"{label} changed while it was being read.")
    return b"".join(chunks), final_stat


def _read_regular_file_bytes(
    path: Path,
    *,
    label: str,
    require_stable_identity: bool = False,
) -> bytes:
    content, _validated_stat = _read_regular_file(
        path,
        label=label,
        require_stable_identity=require_stable_identity,
    )
    return content


def _sha256_file(path: Path) -> str:
    cache = _VALIDATED_FILE_SHA256.get()
    cached = cache.get(_file_cache_key(path)) if cache is not None else None
    if cached is not None:
        content, current_stat = _read_regular_file(
            path,
            label=f"File '{path}'",
            require_stable_identity=True,
        )
        current = _ValidatedFileDigest(
            digest=hashlib.sha256(content).hexdigest(),
            identity=_stat_identity(current_stat),
            metadata=_stat_metadata(current_stat),
        )
        if current != cached:
            _fail(f"File '{path}' changed after it was validated.")
        return current.digest
    content, validated_stat = _read_regular_file(
        path,
        label=f"File '{path}'",
        require_stable_identity=True,
    )
    return _cache_validated_file_digest(path, content, validated_stat)


def _snapshot_content_sha256(
    snapshot_root: Path,
    *,
    max_entries: int = MAX_SNAPSHOT_ENTRIES,
    max_path_bytes: int = MAX_SNAPSHOT_PATH_BYTES,
    max_content_bytes: int = MAX_SNAPSHOT_CONTENT_BYTES,
) -> str:
    records: list[tuple[bytes, str]] = []
    entry_count = 0
    total_content_bytes = 0

    if max_entries < 0 or max_path_bytes < 0 or max_content_bytes < 0:
        _fail("Snapshot content limits must be non-negative integers.")

    def relative_bytes(relative_text: str) -> bytes:
        try:
            encoded = relative_text.encode("utf-8")
        except UnicodeEncodeError as error:
            _fail(
                f"Snapshot content path is not valid UTF-8: '{relative_text}': "
                f"{error}"
            )
        if len(encoded) > max_path_bytes:
            _fail(
                "Snapshot content relative path exceeds the "
                f"{max_path_bytes}-byte UTF-8 limit: '{relative_text}'."
            )
        return encoded

    def account_entry(
        relative_text: str,
        encoded: bytes,
        entry_stat: os.stat_result,
    ) -> None:
        nonlocal entry_count, total_content_bytes
        entry_count += 1
        if entry_count > max_entries:
            _fail(
                f"Snapshot content exceeds the {max_entries}-entry limit."
            )
        if stat.S_ISREG(entry_stat.st_mode):
            total_content_bytes += entry_stat.st_size
            if total_content_bytes > max_content_bytes:
                _fail(
                    "Snapshot content exceeds the "
                    f"{max_content_bytes}-byte regular-file limit."
                )
        if len(encoded) > max_path_bytes:
            _fail(
                "Snapshot content relative path exceeds the "
                f"{max_path_bytes}-byte UTF-8 limit: '{relative_text}'."
            )

    def entry_kind(entry_stat: os.stat_result, relative_text: str) -> str:
        if stat.S_ISLNK(entry_stat.st_mode):
            _fail(
                "Snapshot content may not contain symbolic links or reparse "
                f"points: '{relative_text}'."
            )
        if stat.S_ISDIR(entry_stat.st_mode):
            return "D"
        if stat.S_ISREG(entry_stat.st_mode):
            return "F"
        _fail(
            "Snapshot content entries must be ordinary files or "
            f"directories: '{relative_text}'."
        )

    def hash_opened_file(
        descriptor: int,
        opened_stat: os.stat_result,
        relative_text: str,
    ) -> str:
        file_hash = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            file_hash.update(chunk)
        final_stat = os.fstat(descriptor)
        if (
            _stat_identity(opened_stat) != _stat_identity(final_stat)
            or _stat_metadata(opened_stat) != _stat_metadata(final_stat)
        ):
            _fail(f"Snapshot content changed during hashing: '{relative_text}'.")
        return file_hash.hexdigest()

    if (
        os.name != "nt"
        and os.open in os.supports_dir_fd
        and os.scandir in os.supports_fd
    ):
        directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)

        def validate_directory(
            descriptor: int,
            relative_parts: tuple[str, ...],
            expected_stat: os.stat_result | None,
        ) -> os.stat_result:
            opened_stat = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(opened_stat.st_mode)
                or (
                    expected_stat is not None
                    and (
                        _stat_identity(opened_stat)
                        != _stat_identity(expected_stat)
                        or _stat_metadata(opened_stat)
                        != _stat_metadata(expected_stat)
                    )
                )
            ):
                relative_text = "/".join(relative_parts)
                if relative_text:
                    _fail(
                        "Snapshot content changed during hashing: "
                        f"'{relative_text}'."
                    )
                _fail("Snapshot content changed during hashing.")
            return opened_stat

        def enter_directory(
            directory_descriptor: int,
            relative_parts: tuple[str, ...],
            expected_stat: os.stat_result | None,
        ) -> _SnapshotWalkFrame:
            initial_stat = validate_directory(
                directory_descriptor,
                relative_parts,
                expected_stat,
            )
            manifest: list[tuple[bytes, str]] = []
            inspected: list[tuple[str, str, bytes, os.stat_result, str]] = []
            try:
                with os.scandir(directory_descriptor) as iterator:
                    for entry in iterator:
                        relative_text = "/".join((*relative_parts, entry.name))
                        try:
                            entry_stat = os.stat(
                                entry.name,
                                dir_fd=directory_descriptor,
                                follow_symlinks=False,
                            )
                        except OSError as error:
                            _fail(
                                "Cannot inspect snapshot content "
                                f"'{relative_text}': {error}"
                            )
                        encoded = relative_bytes(relative_text)
                        kind = entry_kind(entry_stat, relative_text)
                        account_entry(relative_text, encoded, entry_stat)
                        manifest.append((encoded, kind))
                        inspected.append(
                            (
                                entry.name,
                                relative_text,
                                encoded,
                                entry_stat,
                                kind,
                            )
                        )
            except OSError as error:
                _fail(f"Cannot enumerate snapshot content: {error}")
            manifest.sort()
            return _SnapshotWalkFrame(
                relative_parts,
                initial_stat,
                manifest,
                inspected,
            )

        def hash_descriptor_file(
            directory_descriptor: int,
            frame: _SnapshotWalkFrame,
            entry: tuple[str, str, bytes, os.stat_result, str],
        ) -> None:
            name, relative_text, encoded, entry_stat, _kind = entry
            validate_directory(
                directory_descriptor,
                frame.relative_parts,
                frame.initial_stat,
            )
            descriptor = -1
            try:
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
                flags |= getattr(os, "O_CLOEXEC", 0)
                flags |= getattr(os, "O_NONBLOCK", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(name, flags, dir_fd=directory_descriptor)
                opened_stat = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened_stat.st_mode)
                    or _stat_identity(opened_stat) != _stat_identity(entry_stat)
                ):
                    _fail(
                        "Snapshot content changed during hashing: "
                        f"'{relative_text}'."
                    )
                file_digest = hash_opened_file(
                    descriptor,
                    opened_stat,
                    relative_text,
                )
                current_stat = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(current_stat.st_mode)
                    or _stat_identity(current_stat) != _stat_identity(entry_stat)
                    or _stat_metadata(current_stat) != _stat_metadata(entry_stat)
                ):
                    _fail(
                        f"Snapshot content changed during hashing: '{relative_text}'."
                    )
            except OSError as error:
                _fail(f"Cannot hash snapshot content '{relative_text}': {error}")
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            if frame.relative_parts or name != SNAPSHOT_PROVENANCE_FILE:
                records.append((encoded, file_digest))

        def leave_directory(
            directory_descriptor: int,
            frame: _SnapshotWalkFrame,
        ) -> None:
            final_directory_stat = validate_directory(
                directory_descriptor,
                frame.relative_parts,
                frame.initial_stat,
            )
            final_manifest: list[tuple[bytes, str]] = []
            try:
                with os.scandir(directory_descriptor) as iterator:
                    for entry in iterator:
                        if len(final_manifest) >= len(frame.manifest):
                            _fail("Snapshot content tree changed during hashing.")
                        relative_text = "/".join(
                            (*frame.relative_parts, entry.name)
                        )
                        entry_stat = os.stat(
                            entry.name,
                            dir_fd=directory_descriptor,
                            follow_symlinks=False,
                        )
                        final_manifest.append(
                            (
                                relative_bytes(relative_text),
                                entry_kind(entry_stat, relative_text),
                            )
                        )
                final_directory_stat = os.fstat(directory_descriptor)
            except OSError as error:
                _fail(f"Cannot enumerate snapshot content: {error}")
            final_manifest.sort()
            if (
                frame.manifest != final_manifest
                or _stat_identity(final_directory_stat)
                != _stat_identity(frame.initial_stat)
                or _stat_metadata(final_directory_stat)
                != _stat_metadata(frame.initial_stat)
            ):
                _fail("Snapshot content tree changed during hashing.")

        current_descriptor = -1
        try:
            current_descriptor = os.open(snapshot_root, directory_flags)
            try:
                opened_root_stat = os.fstat(current_descriptor)
                if not stat.S_ISDIR(opened_root_stat.st_mode):
                    _fail("Snapshot root must be an ordinary directory.")
                named_root_stat = os.lstat(snapshot_root)
                if (
                    not stat.S_ISDIR(named_root_stat.st_mode)
                    or _stat_identity(named_root_stat)
                    != _stat_identity(opened_root_stat)
                ):
                    _fail(
                        "Snapshot root may not traverse a symbolic link or "
                        "reparse point."
                    )
                stack = [
                    enter_directory(
                        current_descriptor,
                        (),
                        opened_root_stat,
                    )
                ]
                while stack:
                    frame = stack[-1]
                    if frame.next_entry < len(frame.entries):
                        entry = frame.entries[frame.next_entry]
                        frame.next_entry += 1
                        name, relative_text, _encoded, entry_stat, kind = entry
                        if kind == "D":
                            child_descriptor = -1
                            try:
                                child_descriptor = os.open(
                                    name,
                                    directory_flags,
                                    dir_fd=current_descriptor,
                                )
                                child_frame = enter_directory(
                                    child_descriptor,
                                    (*frame.relative_parts, name),
                                    entry_stat,
                                )
                                os.close(current_descriptor)
                                current_descriptor = child_descriptor
                                stack.append(
                                    child_frame
                                )
                            except OSError as error:
                                if child_descriptor >= 0:
                                    os.close(child_descriptor)
                                _fail(
                                    "Cannot enumerate snapshot content "
                                    f"'{relative_text}': {error}"
                                )
                            except BaseException:
                                if child_descriptor >= 0:
                                    os.close(child_descriptor)
                                raise
                        else:
                            hash_descriptor_file(
                                current_descriptor,
                                frame,
                                entry,
                            )
                        continue
                    leave_directory(current_descriptor, frame)
                    stack.pop()
                    if stack:
                        parent_descriptor = -1
                        try:
                            parent_descriptor = os.open(
                                "..",
                                directory_flags,
                                dir_fd=current_descriptor,
                            )
                            validate_directory(
                                parent_descriptor,
                                stack[-1].relative_parts,
                                stack[-1].initial_stat,
                            )
                        except BaseException:
                            if parent_descriptor >= 0:
                                os.close(parent_descriptor)
                            raise
                        os.close(current_descriptor)
                        current_descriptor = parent_descriptor
                current_root_stat = os.lstat(snapshot_root)
                if (
                    not stat.S_ISDIR(current_root_stat.st_mode)
                    or _stat_identity(current_root_stat)
                    != _stat_identity(opened_root_stat)
                ):
                    _fail("Snapshot root changed during hashing.")
            finally:
                os.close(current_descriptor)
        except OSError as error:
            _fail(f"Cannot enumerate snapshot content '{snapshot_root}': {error}")
    else:
        def directory_path(relative_parts: tuple[str, ...]) -> Path:
            return snapshot_root.joinpath(*relative_parts)

        def enter_directory(
            relative_parts: tuple[str, ...],
            expected_stat: os.stat_result | None,
        ) -> _SnapshotWalkFrame:
            directory = directory_path(relative_parts)
            try:
                initial_stat = os.lstat(directory)
            except OSError as error:
                _fail(f"Cannot inspect snapshot content '{directory}': {error}")
            if (
                not stat.S_ISDIR(initial_stat.st_mode)
                or _is_link_or_junction(directory)
            ):
                _fail(
                    "Snapshot content may not traverse symbolic links or reparse "
                    f"points: '{'/'.join(relative_parts)}'."
                )
            if expected_stat is not None and (
                _stat_identity(initial_stat) != _stat_identity(expected_stat)
                or _stat_metadata(initial_stat) != _stat_metadata(expected_stat)
            ):
                _fail("Snapshot content changed during hashing.")
            manifest: list[tuple[bytes, str]] = []
            inspected: list[tuple[str, str, bytes, os.stat_result, str]] = []
            try:
                with os.scandir(directory) as iterator:
                    for entry in iterator:
                        path = Path(entry.path)
                        relative_text = "/".join((*relative_parts, entry.name))
                        if _is_link_or_junction(path):
                            _fail(
                                "Snapshot content may not contain symbolic links "
                                f"or reparse points: '{relative_text}'."
                            )
                        try:
                            entry_stat = os.lstat(path)
                        except OSError as error:
                            _fail(
                                f"Cannot inspect snapshot content '{path}': {error}"
                            )
                        encoded = relative_bytes(relative_text)
                        kind = entry_kind(entry_stat, relative_text)
                        account_entry(relative_text, encoded, entry_stat)
                        manifest.append((encoded, kind))
                        inspected.append(
                            (
                                entry.name,
                                relative_text,
                                encoded,
                                entry_stat,
                                kind,
                            )
                        )
            except OSError as error:
                _fail(f"Cannot enumerate snapshot content '{directory}': {error}")
            manifest.sort()
            return _SnapshotWalkFrame(
                relative_parts,
                initial_stat,
                manifest,
                inspected,
            )

        def hash_path_file(
            frame: _SnapshotWalkFrame,
            entry: tuple[str, str, bytes, os.stat_result, str],
        ) -> None:
            name, relative_text, encoded, entry_stat, _kind = entry
            path = directory_path((*frame.relative_parts, name))
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NONBLOCK", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = -1
            try:
                descriptor = os.open(path, flags)
                opened_stat = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened_stat.st_mode)
                    or _stat_identity(opened_stat) != _stat_identity(entry_stat)
                ):
                    _fail(
                        "Snapshot content changed during hashing: "
                        f"'{relative_text}'."
                    )
                file_digest = hash_opened_file(
                    descriptor,
                    opened_stat,
                    relative_text,
                )
            except OSError as error:
                _fail(f"Cannot hash snapshot content '{relative_text}': {error}")
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            try:
                current_stat = os.lstat(path)
            except OSError as error:
                _fail(f"Cannot inspect snapshot content '{relative_text}': {error}")
            if (
                _is_link_or_junction(path)
                or not stat.S_ISREG(current_stat.st_mode)
                or _stat_identity(current_stat) != _stat_identity(entry_stat)
                or _stat_metadata(current_stat) != _stat_metadata(entry_stat)
            ):
                _fail(f"Snapshot content changed during hashing: '{relative_text}'.")
            if frame.relative_parts or name != SNAPSHOT_PROVENANCE_FILE:
                records.append((encoded, file_digest))

        def leave_directory(frame: _SnapshotWalkFrame) -> None:
            directory = directory_path(frame.relative_parts)
            try:
                current_directory_stat = os.lstat(directory)
            except OSError as error:
                _fail(f"Cannot inspect snapshot content '{directory}': {error}")
            if (
                _is_link_or_junction(directory)
                or not stat.S_ISDIR(current_directory_stat.st_mode)
                or _stat_identity(current_directory_stat)
                != _stat_identity(frame.initial_stat)
                or _stat_metadata(current_directory_stat)
                != _stat_metadata(frame.initial_stat)
            ):
                _fail("Snapshot content tree changed during hashing.")
            final_manifest: list[tuple[bytes, str]] = []
            try:
                with os.scandir(directory) as iterator:
                    for entry in iterator:
                        if len(final_manifest) >= len(frame.manifest):
                            _fail("Snapshot content tree changed during hashing.")
                        path = Path(entry.path)
                        relative_text = "/".join(
                            (*frame.relative_parts, entry.name)
                        )
                        if _is_link_or_junction(path):
                            _fail(
                                "Snapshot content may not contain symbolic links "
                                f"or reparse points: '{relative_text}'."
                            )
                        entry_stat = os.lstat(path)
                        final_manifest.append(
                            (
                                relative_bytes(relative_text),
                                entry_kind(entry_stat, relative_text),
                            )
                        )
                final_directory_stat = os.lstat(directory)
            except OSError as error:
                _fail(f"Cannot enumerate snapshot content '{directory}': {error}")
            final_manifest.sort()
            if (
                _is_link_or_junction(directory)
                or not stat.S_ISDIR(final_directory_stat.st_mode)
                or frame.manifest != final_manifest
                or _stat_identity(final_directory_stat)
                != _stat_identity(frame.initial_stat)
                or _stat_metadata(final_directory_stat)
                != _stat_metadata(frame.initial_stat)
            ):
                _fail("Snapshot content tree changed during hashing.")

        stack = [enter_directory((), None)]
        while stack:
            frame = stack[-1]
            if frame.next_entry < len(frame.entries):
                entry = frame.entries[frame.next_entry]
                frame.next_entry += 1
                name, _relative_text, _encoded, entry_stat, kind = entry
                if kind == "D":
                    stack.append(
                        enter_directory(
                            (*frame.relative_parts, name),
                            entry_stat,
                        )
                    )
                else:
                    hash_path_file(frame, entry)
                continue
            leave_directory(frame)
            stack.pop()
    digest = hashlib.sha256()
    for relative_bytes, file_sha256 in sorted(records, key=lambda item: item[0]):
        digest.update(b"F\0")
        digest.update(relative_bytes)
        digest.update(b"\0")
        digest.update(file_sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _atomic_write_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    lock: _DirectoryLock | Sequence[_DirectoryLock] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(16)}")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        if lock is None:
            locks: Sequence[_DirectoryLock] = ()
        elif isinstance(lock, Sequence):
            locks = lock
        else:
            locks = (lock,)
        for held_lock in locks:
            held_lock.assert_owned()
        os.replace(temporary, path)
        _invalidate_validated_file_digest(path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_text(
    path: Path,
    value: str,
    *,
    lock: _DirectoryLock | Sequence[_DirectoryLock] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(16)}")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((value + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if lock is None:
            locks: Sequence[_DirectoryLock] = ()
        elif isinstance(lock, Sequence):
            locks = lock
        else:
            locks = (lock,)
        for held_lock in locks:
            held_lock.assert_owned()
        os.replace(temporary, path)
        _invalidate_validated_file_digest(path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(_json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != "nt":
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _openprocess_denied_means_live(last_error: int) -> bool:
    return last_error == WINDOWS_ERROR_ACCESS_DENIED


def _publish_json_no_replace(
    path: Path,
    value: Mapping[str, Any],
    *,
    locks: Sequence[_DirectoryLock],
) -> bool:
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(16)}")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        for held_lock in locks:
            held_lock.assert_owned()
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        except OSError as error:
            if os.path.lexists(path):
                return False
            _fail(f"Cannot publish runtime slot completion '{path}': {error}")
        _invalidate_validated_file_digest(path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _pid_is_live(pid: int) -> bool:
    if pid < 1:
        return False
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        synchronize = 0x00100000
        process_query_limited_information = 0x1000
        wait_object_0 = 0
        wait_timeout = 258
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            synchronize | process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return _openprocess_denied_means_live(ctypes.get_last_error())
        try:
            wait_result = kernel32.WaitForSingleObject(handle, 0)
            if wait_result == wait_object_0:
                return False
            if wait_result == wait_timeout:
                return True
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


class _DirectoryLock(AbstractContextManager["_DirectoryLock"]):
    def __init__(
        self,
        path: Path,
        *,
        kind: str,
        marketplace_id: str,
        plugin_id: str | None = None,
        timeout_seconds: float = LOCK_INITIALIZATION_GRACE_SECONDS,
    ) -> None:
        self.path = path
        self.kind = kind
        self.marketplace_id = marketplace_id
        self.plugin_id = plugin_id
        self.timeout_seconds = timeout_seconds
        self.token = secrets.token_hex(16)
        self.owner_path = path / "owner.json"
        self.host = socket.gethostname().split(".", 1)[0].casefold()
        self.acquired = False

    def _owner(self) -> Mapping[str, Any]:
        owner = read_json(self.owner_path)
        if not isinstance(owner, Mapping):
            _fail(f"Installation lock owner receipt '{self.owner_path}' must be an object.")
        if (
            _string_property(owner, "schema") != LOCK_SCHEMA
            or _property(owner, "version") != LOCK_VERSION
            or _string_property(owner, "kind") != self.kind
            or _string_property(owner, "marketplaceId") != self.marketplace_id
            or _string_property(owner, "pluginId") != (self.plugin_id or "")
        ):
            _fail(f"Installation lock owner receipt '{self.owner_path}' is invalid.")
        token = _string_property(owner, "token")
        host = _string_property(owner, "host")
        pid = _property(owner, "pid")
        if (
            not token
            or not host
            or isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid < 1
        ):
            _fail(f"Installation lock owner receipt '{self.owner_path}' is incomplete.")
        return owner

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            if time.monotonic() >= deadline:
                if self.path.exists() and not self.owner_path.exists():
                    try:
                        age = time.time() - self.path.stat().st_mtime
                    except FileNotFoundError:
                        continue
                    if age >= LOCK_INITIALIZATION_GRACE_SECONDS:
                        _fail(
                            f"Installation lock '{self.path}' has no owner receipt; "
                            "explicit repair is required."
                        )
                _fail(f"Installation lock '{self.path}' remained busy.")
            try:
                self.path.mkdir()
            except FileExistsError:
                if not self.owner_path.exists():
                    try:
                        age = time.time() - self.path.stat().st_mtime
                    except FileNotFoundError:
                        continue
                    if age >= LOCK_INITIALIZATION_GRACE_SECONDS:
                        _fail(
                            f"Installation lock '{self.path}' has no owner receipt; "
                            "explicit repair is required."
                        )
                    if time.monotonic() >= deadline:
                        _fail(f"Installation lock '{self.path}' remained busy.")
                    time.sleep(LOCK_POLL_SECONDS)
                    continue
                try:
                    owner = self._owner()
                except InstallationContextError:
                    if not self.path.exists() or not self.owner_path.exists():
                        time.sleep(LOCK_POLL_SECONDS)
                        continue
                    raise
                owner_host = _string_property(owner, "host")
                owner_pid = _property(owner, "pid")
                if owner_host == self.host:
                    if not _pid_is_live(owner_pid):
                        time.sleep(LOCK_POLL_SECONDS)
                        try:
                            current_owner = self._owner()
                        except InstallationContextError:
                            if not self.path.exists() or not self.owner_path.exists():
                                continue
                            raise
                        if _string_property(current_owner, "token") != _string_property(
                            owner, "token"
                        ):
                            continue
                        _fail(
                            f"Installation lock '{self.path}' has a stale owner "
                            f"(host={owner_host}, pid={owner_pid}); explicit repair is required."
                        )
                    if time.monotonic() >= deadline:
                        _fail(f"Installation lock '{self.path}' remained busy.")
                    time.sleep(LOCK_POLL_SECONDS)
                    continue
                _fail(
                    f"Installation lock '{self.path}' is busy "
                    f"(host={owner_host}, pid={owner_pid})."
                )
            else:
                owner = {
                    "schema": LOCK_SCHEMA,
                    "version": LOCK_VERSION,
                    "kind": self.kind,
                    "marketplaceId": self.marketplace_id,
                    "pluginId": self.plugin_id or "",
                    "token": self.token,
                    "host": self.host,
                    "pid": os.getpid(),
                    "acquiredAt": _utc_now(),
                }
                try:
                    _atomic_write_json(self.owner_path, owner)
                except BaseException:
                    if self.owner_path.exists():
                        self.owner_path.unlink()
                    self.path.rmdir()
                    raise
                self.acquired = True
                return

    def assert_owned(self) -> None:
        if not self.acquired:
            _fail(f"Installation lock '{self.path}' is not held.")
        owner = self._owner()
        if _string_property(owner, "token") != self.token:
            _fail(f"Installation lock '{self.path}' ownership changed during mutation.")

    def release(self) -> None:
        if not self.acquired:
            return
        self.assert_owned()
        deadline = time.monotonic() + 1.0
        while True:
            try:
                self.owner_path.unlink()
                break
            except PermissionError as error:
                if time.monotonic() >= deadline:
                    _fail(f"Cannot release installation lock '{self.path}': {error}")
                time.sleep(LOCK_POLL_SECONDS)
        deadline = time.monotonic() + 1.0
        while True:
            try:
                self.path.rmdir()
                break
            except PermissionError as error:
                if time.monotonic() >= deadline:
                    _fail(f"Cannot release installation lock '{self.path}': {error}")
                time.sleep(LOCK_POLL_SECONDS)
            except OSError as error:
                _fail(f"Cannot release installation lock '{self.path}': {error}")
        self.acquired = False

    def __enter__(self) -> _DirectoryLock:
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if exc_type is None:
            self.release()
            return
        try:
            self.release()
        except InstallationContextError as release_error:
            warnings.warn(
                f"{release_error} while preserving the original mutation failure.",
                RuntimeWarning,
                stacklevel=2,
            )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    casefolded: dict[str, str] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"Duplicate JSON property '{key}'.")
        folded = key.casefold()
        if folded in casefolded:
            _fail(
                f"JSON properties '{casefolded[folded]}' and '{key}' differ only by case."
            )
        result[key] = value
        casefolded[folded] = key
    return result


def _normalize_git_url(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail("A git source URL may not contain control characters.")
    if not value.strip():
        _fail("A git source requires url.")
    candidate = value.strip()
    if re.search(r"%(?![0-9A-Fa-f]{2})", candidate):
        _fail("Git URL has a malformed percent-escape.")
    scp_match = re.fullmatch(r"[^/@:]+@([^/:]+):(.+)", candidate)
    if scp_match:
        candidate = f"ssh://{scp_match.group(1)}/{scp_match.group(2)}"
    parsed = urlsplit(candidate)
    if not parsed.scheme or not parsed.hostname:
        _fail(f"Git URL must be absolute and include a host: {value}")
    host = parsed.hostname.lower()
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            _fail(f"Git URL has an invalid host: {value}")
    elif not re.fullmatch(r"[a-z0-9._-]+", host):
        _fail(f"Git URL has an invalid host: {value}")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = ""
    try:
        if parsed.port is not None:
            defaults = {"http": 80, "https": 443}
            if defaults.get(parsed.scheme.lower()) != parsed.port:
                port = f":{parsed.port}"
    except ValueError as error:
        _fail(f"Invalid git URL '{value}': {error}")
    path = quote(
        (parsed.path or "/").replace("\\", "/"),
        safe="/-._~!$&'()*+,;=:@%[]",
    )
    path = re.sub(
        r"%([0-9a-fA-F]{2})",
        lambda match: (
            chr(int(match.group(1), 16))
            if 48 <= int(match.group(1), 16) <= 57
            or 65 <= int(match.group(1), 16) <= 90
            or 97 <= int(match.group(1), 16) <= 122
            or chr(int(match.group(1), 16)) in "-._~"
            else f"%{match.group(1).upper()}"
        ),
        path,
    )
    parts = path.split("/")
    normalized_parts: list[str] = []
    for part in parts:
        if not part and not normalized_parts:
            continue
        if part == ".":
            continue
        if part == "..":
            if normalized_parts:
                normalized_parts.pop()
            continue
        normalized_parts.append(part)
    while normalized_parts and not normalized_parts[-1]:
        normalized_parts.pop()
    path = f"/{'/'.join(normalized_parts)}"
    if path.lower().endswith(".git"):
        path = path[:-4]
    if not path.startswith("/"):
        path = f"/{path}"
    return urlunsplit((parsed.scheme.lower(), f"{host}{port}", path, "", ""))


def normalize_source(
    descriptor: Mapping[str, Any],
    base_directory: str | os.PathLike[str] | None = None,
    *,
    from_receipt: bool = False,
) -> NormalizedSource:
    """Normalize a source descriptor into the portable identity record."""

    raw_kind = _string_property(descriptor, "kind") or _string_property(
        descriptor,
        "source",
    )
    kind = raw_kind.strip().lower()
    aliases = {"local": "directory", "url": "git"}
    kind = aliases.get(kind, kind)
    ref = _string_property(descriptor, "ref")
    canonical_input = _string_property(descriptor, "canonical")
    if from_receipt and not canonical_input:
        _fail("A receipt source requires canonical identity.")
    canonical = ""

    if kind == "github":
        if canonical_input:
            if not canonical_input.startswith("github:"):
                _fail(f"Invalid canonical GitHub source '{canonical_input}'.")
            repository = canonical_input[7:]
        else:
            repository = (
                _string_property(descriptor, "repo")
                or _string_property(descriptor, "url")
            )
        repository = repository.strip()
        repository = re.sub(r"(?i)^https?://github\.com/", "", repository)
        repository = re.sub(r"(?i)^ssh://git@github\.com/", "", repository)
        repository = re.sub(r"(?i)^git@github\.com:", "", repository)
        repository = repository.strip("/")
        if repository.lower().endswith(".git"):
            repository = repository[:-4]
        if not re.fullmatch(r"[^/]+/[^/]+", repository):
            _fail(f"GitHub source requires owner/repository, got '{repository}'.")
        canonical = f"github:{repository.lower()}"
    elif kind == "git":
        if canonical_input:
            if not canonical_input.startswith("git:"):
                _fail(f"Invalid canonical git source '{canonical_input}'.")
            git_url = canonical_input[4:]
        else:
            git_url = _string_property(descriptor, "url")
        canonical = f"git:{_normalize_git_url(git_url)}"
    elif kind == "opaque":
        if canonical_input:
            canonical = canonical_input
        else:
            opaque_id = (
                _string_property(descriptor, "id")
                or _string_property(descriptor, "value")
            )
            if not opaque_id.strip():
                _fail("An opaque source requires a non-empty id.")
            canonical = f"opaque:{opaque_id}"
        if not canonical.startswith("opaque:"):
            _fail(f"Invalid canonical opaque source '{canonical}'.")
    elif kind == "directory":
        stable_id = _string_property(descriptor, "stableId").strip()
        if canonical_input:
            if canonical_input.startswith("directory-id:"):
                receipt_id = canonical_input[13:].strip()
                if not receipt_id:
                    _fail("A canonical directory-id source requires a non-empty id.")
                canonical = f"directory-id:{receipt_id}"
            elif canonical_input.startswith("directory:"):
                directory = canonical_input[10:]
                canonical = f"directory:{canonical_path(directory, must_exist=not from_receipt)}"
            else:
                _fail(f"Invalid canonical directory source '{canonical_input}'.")
        elif stable_id:
            canonical = f"directory-id:{stable_id}"
        else:
            directory_text = _string_property(descriptor, "path")
            if not directory_text.strip():
                _fail("A directory source requires a non-empty path or stableId.")
            directory = Path(directory_text)
            if not directory.is_absolute():
                if base_directory is None:
                    _fail("A relative directory source requires a declaration base directory.")
                directory = Path(base_directory) / directory
            canonical = f"directory:{canonical_path(directory, must_exist=True)}"
        if not (
            canonical.startswith("directory:") or canonical.startswith("directory-id:")
        ):
            _fail(f"Invalid canonical directory source '{canonical}'.")
    else:
        _fail(f"Unsupported source kind '{kind}'.")

    return NormalizedSource(kind=kind, canonical=canonical, ref=ref)


def source_record(source: NormalizedSource) -> str:
    fields = (
        ("version", "1"),
        ("kind", source.kind),
        ("source", source.canonical),
        ("ref", source.ref),
    )
    return "".join(
        f"{name}:{len(value.encode('utf-8'))}:{value}\n" for name, value in fields
    )


def _slug(value: str) -> str:
    result: list[str] = []
    previous_dash = False
    for character in value:
        if "A" <= character <= "Z":
            character = chr(ord(character) + 32)
        if "a" <= character <= "z" or "0" <= character <= "9":
            result.append(character)
            previous_dash = False
        elif result and not previous_dash:
            result.append("-")
            previous_dash = True
    return "".join(result).strip("-") or "marketplace"


def source_identity(source: NormalizedSource, readable_name: str) -> dict[str, Any]:
    record = source_record(source)
    digest = hashlib.sha256(record.encode("utf-8")).hexdigest()
    return {
        "kind": source.kind,
        "canonical": source.canonical,
        "ref": source.ref,
        "record": record,
        "sha256": digest,
        "fingerprint": f"sha256:{digest}",
        "marketplaceId": f"{_slug(readable_name)}--{digest[:16]}",
    }


def _get_declarations(
    key: str,
    copilot_home: Path,
    project_root: Path | None,
) -> list[dict[str, Any]]:
    settings_paths: list[tuple[Path, str, Path]] = [
        (copilot_home / "settings.json", "user", copilot_home),
        (copilot_home / "settings.local.json", "user-local", copilot_home),
    ]
    if project_root is not None:
        for relative in (
            ".claude/settings.json",
            ".claude/settings.local.json",
            ".github/copilot/settings.json",
            ".github/copilot/settings.local.json",
        ):
            settings_paths.append(
                (project_root / relative, f"project:{project_root}", project_root)
            )
    declarations: list[dict[str, Any]] = []
    for path, label, base in settings_paths:
        if not path.is_file():
            continue
        settings = read_json(path)
        marketplaces = _property(settings, "extraKnownMarketplaces")
        if not isinstance(marketplaces, Mapping):
            continue
        for candidate in marketplaces:
            if candidate != key and candidate.casefold() == key.casefold():
                _fail(f"JSON property '{candidate}' conflicts with exact case '{key}'.")
        if key not in marketplaces:
            continue
        declaration = marketplaces[key]
        descriptor = _property(declaration, "source")
        if not isinstance(descriptor, Mapping):
            _fail(f"Marketplace '{key}' has no source in '{path}'.")
        declarations.append(
            {
                "source": normalize_source(descriptor, base),
                "declaredIn": label,
                "settingsPath": canonical_path(path),
            }
        )
    if not declarations:
        _fail(
            "No user or explicit project extraKnownMarketplaces declaration found "
            f"for installed key '{key}'."
        )
    identities = {
        (
            item["source"].kind,
            item["source"].canonical,
            item["source"].ref,
        )
        for item in declarations
    }
    if len(identities) != 1:
        locations = ", ".join(str(item["settingsPath"]) for item in declarations)
        _fail(
            f"Conflicting declarations for marketplace key '{key}' in: {locations}. "
            "Supply explicit management provenance."
        )
    return declarations


def _resolve_installed_evidence(
    payload: Path,
    copilot_home: Path,
    project_root: Path | None,
) -> dict[str, Any] | None:
    installed = canonical_path(copilot_home / "installed-plugins")
    try:
        relative = payload.relative_to(installed)
    except ValueError:
        return None
    if len(relative.parts) != 2 or not all(relative.parts):
        _fail(
            "Installed payload must be exactly "
            f"<copilot-home>/installed-plugins/<key>/<plugin>: {payload}"
        )
    key, plugin_id = relative.parts
    declarations = _get_declarations(key, copilot_home, project_root)
    return {
        "source": declarations[0]["source"],
        "pluginId": plugin_id,
        "readableName": key,
        "locator": {
            "kind": "installed",
            "copilotHome": str(copilot_home),
            "marketplaceKey": key,
            "declaredIn": [item["declaredIn"] for item in declarations],
        },
    }


def _resolve_directory_evidence(
    payload: Path,
    requested_plugin_id: str | None,
) -> dict[str, Any] | None:
    cursor = payload
    manifest_paths = (
        ".github/plugin/marketplace.json",
        "marketplace.json",
        ".plugin/marketplace.json",
        ".claude-plugin/marketplace.json",
    )
    while True:
        for relative_manifest in manifest_paths:
            manifest_path = cursor / relative_manifest
            if not manifest_path.is_file():
                continue
            manifest = read_json(manifest_path)
            metadata = _property(manifest, "metadata", {})
            if not isinstance(metadata, Mapping):
                _fail(f"Marketplace metadata must be an object in '{manifest_path}'.")
            plugin_root_text = _string_property(metadata, "pluginRoot")
            source_base = cursor
            if plugin_root_text:
                plugin_root = Path(plugin_root_text)
                if plugin_root.is_absolute():
                    _fail(
                        f"Marketplace metadata.pluginRoot must be relative in '{manifest_path}'."
                    )
                if ".." in plugin_root.parts:
                    _fail(f"Marketplace metadata.pluginRoot may not escape '{cursor}'.")
                source_base = canonical_path(cursor / plugin_root)
                if not path_is_within(source_base, cursor):
                    _fail(f"Marketplace metadata.pluginRoot escapes '{cursor}'.")
            matches: list[Mapping[str, Any]] = []
            plugins = _property(manifest, "plugins", [])
            if not isinstance(plugins, Sequence) or isinstance(plugins, (str, bytes)):
                plugins = []
            for plugin in plugins:
                if not isinstance(plugin, Mapping):
                    continue
                name = _string_property(plugin, "name")
                if requested_plugin_id and name != requested_plugin_id:
                    continue
                source_path_text = _string_property(plugin, "source")
                if not source_path_text:
                    continue
                source_path = Path(source_path_text)
                if source_path.is_absolute() or ".." in source_path.parts:
                    _fail(
                        "Marketplace plugin source must be relative and remain "
                        f"beneath '{cursor}'."
                    )
                candidate = canonical_path(source_base / source_path)
                if not path_is_within(candidate, cursor):
                    _fail(f"Marketplace plugin source escapes '{cursor}'.")
                if candidate.exists() and paths_equal(candidate, payload):
                    matches.append(plugin)
            if len(matches) != 1:
                _fail(
                    f"Marketplace manifest '{manifest_path}' does not contain exactly "
                    f"one plugin entry resolving to '{payload}'."
                )
            plugin_id = _string_property(matches[0], "name")
            return {
                "source": normalize_source(
                    {"source": "directory", "path": str(cursor)},
                    cursor,
                ),
                "pluginId": plugin_id,
                "readableName": _string_property(manifest, "name", "marketplace"),
                "locator": {
                    "kind": "directory",
                    "marketplaceRoot": str(canonical_path(cursor, must_exist=True)),
                },
            }
        if cursor.parent == cursor:
            break
        cursor = canonical_path(cursor.parent)
    return None


def _locator_matches(locator: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
    if _property(locator, "kind", "") != _property(receipt, "kind", ""):
        return False
    if locator["kind"] == "installed":
        return (
            _string_property(receipt, "marketplaceKey") == locator["marketplaceKey"]
            and paths_equal(
                _string_property(receipt, "copilotHome"),
                str(locator["copilotHome"]),
            )
        )
    if locator["kind"] == "directory":
        return paths_equal(
            _string_property(receipt, "marketplaceRoot"),
            str(locator["marketplaceRoot"]),
        )
    return False


def _assert_positive_integer(value: Any, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail(f"{name} must be an integer.")
    if value < 1:
        _fail(f"{name} must be at least 1.")


def _assert_receipt_state(value: Any, name: str) -> None:
    if value not in {"active", "inactive", "orphaned", "removing"}:
        _fail(f"{name} must be active, inactive, orphaned, or removing.")


def _assert_receipt_generation(value: Any, name: str) -> None:
    _assert_positive_integer(value, name)
    if value > MAX_RECEIPT_GENERATION:
        _fail(f"{name} exceeds the portable signed 64-bit maximum.")


@_validation_scope
def validate_namespace_receipt(
    receipt_path: str | os.PathLike[str],
    durable_home: str | os.PathLike[str],
) -> dict[str, Any]:
    receipt_pointer = Path(receipt_path)
    if not _path_is_fully_qualified(receipt_pointer):
        _fail("The namespace receipt pointer must be absolute.")
    if not _path_is_fully_qualified(durable_home):
        _fail("--durable-home must be absolute.")
    durable = canonical_path(durable_home)
    lexical_marketplaces_root = durable / "marketplaces"
    if _is_link_or_junction(lexical_marketplaces_root):
        _fail("The marketplaces root may not be a symbolic link or reparse point.")
    marketplaces_root = canonical_path(lexical_marketplaces_root)
    if not paths_equal(marketplaces_root.parent, durable):
        _fail("The marketplaces root escapes the durable installation home.")
    lexical_cell_root = receipt_pointer.parent
    if _is_link_or_junction(lexical_cell_root):
        _fail("The marketplace cell root may not be a symbolic link or reparse point.")
    cell_root = canonical_path(lexical_cell_root)
    if not paths_equal(cell_root.parent, marketplaces_root):
        _fail(
            f"Namespace receipt '{receipt_pointer}' is outside the durable "
            "marketplaces root."
        )
    if _is_link_or_junction(receipt_pointer):
        _fail("namespace.json may not be a symbolic link or reparse point.")
    actual_receipt = canonical_path(receipt_pointer, must_exist=True)
    if not paths_equal(actual_receipt.parent, cell_root):
        _fail("namespace.json escapes its canonical marketplace cell.")
    marketplace_id = cell_root.name
    lexical_canonical_receipt = cell_root / "namespace.json"
    if _is_link_or_junction(lexical_canonical_receipt):
        _fail("namespace.json may not be a symbolic link or reparse point.")
    canonical_receipt = canonical_path(lexical_canonical_receipt)
    if not paths_equal(actual_receipt, canonical_receipt):
        _fail(
            f"namespace.json is not at its exact canonical receipt location "
            f"'{canonical_receipt}'."
        )
    namespace = read_json(actual_receipt)
    namespace_version = _property(namespace, "version")
    if (
        _string_property(namespace, "schema")
        != "copilot-extensions.marketplace-namespace"
        or isinstance(namespace_version, bool)
        or not isinstance(namespace_version, int)
        or namespace_version != 1
    ):
        _fail(f"Namespace receipt '{actual_receipt}' has an unsupported schema or version.")
    if _string_property(namespace, "marketplaceId") != marketplace_id:
        _fail(f"Namespace receipt '{actual_receipt}' does not match its cell directory.")
    match = re.fullmatch(r"(.+)--([0-9a-f]{16})", marketplace_id)
    if match is None:
        _fail(f"Invalid source-derived marketplace id '{marketplace_id}'.")
    _assert_receipt_generation(
        _property(namespace, "generation"),
        "namespace.json generation",
    )
    _assert_receipt_state(_property(namespace, "state"), "namespace.json state")
    source_receipt = _property(namespace, "source")
    if not isinstance(source_receipt, Mapping):
        _fail(f"Namespace receipt '{actual_receipt}' has no source identity.")
    normalized = normalize_source(
        {
            "kind": _property(source_receipt, "kind"),
            "canonical": _property(source_receipt, "canonical"),
            "ref": _property(source_receipt, "ref", ""),
        },
        from_receipt=True,
    )
    identity = source_identity(normalized, match.group(1))
    if identity["marketplaceId"] != marketplace_id:
        _fail(f"Namespace receipt '{actual_receipt}' id does not match its normalized source.")
    if _string_property(source_receipt, "fingerprint") != identity["fingerprint"]:
        _fail(
            f"Namespace receipt '{actual_receipt}' fingerprint does not match "
            "its normalized source."
        )
    return {
        "receipt": namespace,
        "receiptPath": actual_receipt,
        "cellRoot": cell_root,
        "marketplaceId": marketplace_id,
        "identity": identity,
    }


def _assert_plugin_id(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?", value) or value in {
        ".",
        "..",
    }:
        _fail(f"Invalid filesystem-safe plugin id '{value}'.")
    basename = value.split(".", 1)[0].upper()
    if (
        basename in {"CON", "PRN", "AUX", "NUL"}
        or re.fullmatch(r"COM[1-9]", basename)
        or re.fullmatch(r"LPT[1-9]", basename)
    ):
        _fail(f"Invalid filesystem-safe plugin id '{value}'.")


def _assert_snapshot_id(value: str) -> None:
    if not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9._+-]*[A-Za-z0-9])?",
        value,
    ) or value in {".", ".."}:
        _fail(f"Invalid filesystem-safe snapshot id '{value}'.")
    basename = value.split(".", 1)[0].upper()
    if (
        basename in {"CON", "PRN", "AUX", "NUL"}
        or re.fullmatch(r"COM[1-9]", basename)
        or re.fullmatch(r"LPT[1-9]", basename)
    ):
        _fail(f"Invalid filesystem-safe snapshot id '{value}'.")


def _assert_runtime_version(value: str) -> None:
    if len(value) > 128:
        _fail("Runtime version exceeds the portable 128-character limit.")
    if not re.fullmatch(
        r"[A-Za-z0-9](?:[A-Za-z0-9._+-]*[A-Za-z0-9])?",
        value,
    ) or value in {".", ".."}:
        _fail(f"Invalid filesystem-safe runtime version '{value}'.")
    basename = value.split(".", 1)[0].upper()
    if (
        basename in {"CON", "PRN", "AUX", "NUL"}
        or re.fullmatch(r"COM[1-9]", basename)
        or re.fullmatch(r"LPT[1-9]", basename)
    ):
        _fail(f"Invalid filesystem-safe runtime version '{value}'.")


def _resolve_relative_root(plugin_root: Path, relative: str, name: str) -> Path:
    if not relative.strip() or Path(relative).is_absolute():
        _fail(f"roots.{name} must be a non-empty relative path.")
    raw_parts = re.split(r"[\\/]", relative)
    if "." in raw_parts or ".." in raw_parts:
        _fail(f"roots.{name} may not escape or use dot segments.")
    resolved = canonical_path(plugin_root / relative)
    if resolved.parent != plugin_root and not path_is_within(resolved, plugin_root):
        _fail(f"roots.{name} escapes pluginRoot.")
    return resolved


@_validation_scope
def validate_context_receipt(
    receipt_path: str | os.PathLike[str],
    durable_home: str | os.PathLike[str],
    *,
    expected_marketplace_id: str | None = None,
    expected_plugin_id: str | None = None,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_cell_root: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    environment = environment if environment is not None else os.environ
    if not _path_is_fully_qualified(durable_home):
        _fail("--durable-home must be absolute.")
    receipt_pointer = Path(receipt_path)
    if not _path_is_fully_qualified(receipt_pointer):
        _fail("The installation-context receipt pointer must be absolute.")
    if _is_link_or_junction(receipt_pointer):
        _fail("install.json may not be a symbolic link or reparse point.")
    for name, expectation in (
        ("expected payload root", expected_payload_root),
        ("expected cell root", expected_cell_root),
    ):
        if expectation is not None and not _path_is_fully_qualified(expectation):
            _fail(f"{name} must be absolute.")
    actual_receipt = canonical_path(receipt_pointer, must_exist=True)
    install = read_json(actual_receipt)
    install_version = _property(install, "version")
    if (
        _string_property(install, "schema")
        != "copilot-extensions.plugin-installation"
        or isinstance(install_version, bool)
        or not isinstance(install_version, int)
        or install_version != 1
    ):
        _fail("install.json has an unsupported schema or version.")
    marketplace_id = _string_property(install, "marketplaceId")
    plugin_id = _string_property(install, "pluginId")
    if not marketplace_id or not plugin_id:
        _fail("install.json identity is incomplete.")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*--[0-9a-f]{16}", marketplace_id):
        _fail(f"Invalid source-derived marketplace id '{marketplace_id}'.")
    _assert_plugin_id(plugin_id)
    durable = canonical_path(durable_home)
    lexical_marketplaces_root = durable / "marketplaces"
    if _is_link_or_junction(lexical_marketplaces_root):
        _fail("The marketplaces root may not be a symbolic link or reparse point.")
    marketplaces_root = canonical_path(lexical_marketplaces_root)
    if not paths_equal(marketplaces_root.parent, durable):
        _fail("The marketplaces root escapes the durable installation home.")
    lexical_cell_root = marketplaces_root / marketplace_id
    if _is_link_or_junction(lexical_cell_root):
        _fail("The marketplace cell root may not be a symbolic link or reparse point.")
    cell_root = canonical_path(lexical_cell_root)
    if not paths_equal(cell_root.parent, marketplaces_root):
        _fail("The marketplace cell root escapes the marketplaces root.")
    lexical_plugins_root = cell_root / "plugins"
    if _is_link_or_junction(lexical_plugins_root):
        _fail("The cell plugins root may not be a symbolic link or reparse point.")
    plugins_root = canonical_path(lexical_plugins_root)
    if not paths_equal(plugins_root.parent, cell_root):
        _fail("The cell plugins root escapes the marketplace cell.")
    lexical_plugin_root = plugins_root / plugin_id
    if _is_link_or_junction(lexical_plugin_root):
        _fail("The plugin root may not be a symbolic link or reparse point.")
    plugin_root = canonical_path(lexical_plugin_root)
    if not paths_equal(plugin_root.parent, plugins_root):
        _fail("The plugin root escapes the cell plugins root.")
    lexical_canonical_receipt = plugin_root / "install.json"
    if _is_link_or_junction(lexical_canonical_receipt):
        _fail("install.json may not be a symbolic link or reparse point.")
    canonical_receipt = canonical_path(lexical_canonical_receipt)
    if not paths_equal(actual_receipt, canonical_receipt):
        _fail(
            f"install.json is not at its exact canonical receipt location "
            f"'{canonical_receipt}'."
        )
    if not paths_equal(_string_property(install, "pluginRoot"), plugin_root):
        _fail("install.json pluginRoot does not match its canonical cell/plugin location.")
    if expected_marketplace_id and marketplace_id != expected_marketplace_id:
        _fail(
            f"Expected marketplace '{expected_marketplace_id}', receipt names "
            f"'{marketplace_id}'."
        )
    if expected_plugin_id and plugin_id != expected_plugin_id:
        _fail(f"Expected plugin '{expected_plugin_id}', receipt names '{plugin_id}'.")
    if expected_cell_root and not paths_equal(cell_root, expected_cell_root):
        _fail(f"Expected cell '{expected_cell_root}', receipt belongs to '{cell_root}'.")
    _assert_receipt_generation(_property(install, "generation"), "install.json generation")
    _assert_receipt_state(_property(install, "state"), "install.json state")

    lexical_namespace_path = cell_root / "namespace.json"
    if _is_link_or_junction(lexical_namespace_path):
        _fail("namespace.json may not be a symbolic link or reparse point.")
    namespace_path = canonical_path(lexical_namespace_path)
    if not paths_equal(_string_property(install, "namespaceReceipt"), namespace_path):
        _fail("install.json namespaceReceipt is not the exact namespace receipt in the same cell.")
    validated_namespace = validate_namespace_receipt(lexical_namespace_path, durable)
    if validated_namespace["marketplaceId"] != marketplace_id:
        _fail("namespace.json marketplaceId does not match install.json.")
    identity = validated_namespace["identity"]

    payload_receipt = _property(install, "payload")
    if not isinstance(payload_receipt, Mapping):
        _fail("install.json payload is missing.")
    payload_text = _string_property(payload_receipt, "root")
    if not _path_is_fully_qualified(payload_text):
        _fail("payload.root must be absolute.")
    if not _string_property(payload_receipt, "version").strip():
        _fail("payload.version must be a non-empty string.")
    if _string_property(payload_receipt, "origin") not in {
        "installed",
        "directory",
        "staged",
        "explicit",
    }:
        _fail("payload.origin must be installed, directory, staged, or explicit.")
    origin_receipt = _property(payload_receipt, "originReceipt")
    if origin_receipt is not None:
        if not isinstance(origin_receipt, str):
            _fail("payload.originReceipt must be a string.")
        if not _path_is_fully_qualified(origin_receipt):
            _fail("payload.originReceipt must be absolute.")
    payload_root = canonical_path(payload_text)
    if expected_payload_root and not paths_equal(payload_root, expected_payload_root):
        _fail(f"Expected payload '{expected_payload_root}', receipt names '{payload_root}'.")
    inherited_payload = environment.get("COPILOT_PLUGIN_ROOT")
    if inherited_payload:
        if not _path_is_fully_qualified(inherited_payload):
            _fail("COPILOT_PLUGIN_ROOT must be absolute.")
        if not paths_equal(payload_root, inherited_payload):
            _fail("COPILOT_PLUGIN_ROOT conflicts with the validated payload root.")

    roots_receipt = _property(install, "roots")
    if not isinstance(roots_receipt, Mapping):
        _fail("install.json roots are missing.")
    roots: dict[str, Path] = {}
    for name in ("versions", "snapshots", "state", "run", "logs", "cache", "launchers"):
        roots[f"{name}Root"] = _resolve_relative_root(
            plugin_root,
            _string_property(roots_receipt, name),
            name,
        )
    return {
        "action": "validate",
        "marketplaceId": marketplace_id,
        "marketplaceSlot": marketplace_id,
        "sourceFingerprint": identity["fingerprint"],
        "source": {
            "kind": identity["kind"],
            "canonical": identity["canonical"],
            "ref": identity["ref"],
        },
        "pluginId": plugin_id,
        "payloadRoot": str(payload_root),
        "cellRoot": str(cell_root),
        "pluginRoot": str(plugin_root),
        **{name: str(path) for name, path in roots.items()},
        "reposRoot": str(canonical_path(cell_root / "repos")),
        "namespaceReceipt": str(namespace_path),
        "installReceipt": str(actual_receipt),
        "namespaceGeneration": _property(
            validated_namespace["receipt"],
            "generation",
        ),
        "generation": _property(install, "generation"),
        "state": _property(install, "state"),
    }


def _assert_expected_generation(
    actual: int,
    expected: int,
    receipt_name: str,
) -> None:
    if isinstance(expected, bool) or not isinstance(expected, int) or expected < 0:
        _fail(f"Expected {receipt_name} generation must be a non-negative integer.")
    if expected > MAX_RECEIPT_GENERATION:
        _fail(
            f"Expected {receipt_name} generation exceeds the portable "
            "signed 64-bit maximum."
        )
    if actual != expected:
        _fail(
            f"{receipt_name} generation changed: expected {expected}, found {actual}; "
            "restart installation-context resolution."
        )


def _normalized_locator(locator: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if locator is None:
        return None
    kind = _string_property(locator, "kind")
    if kind == "installed":
        declared_in = _property(locator, "declaredIn", [])
        if not isinstance(declared_in, Sequence) or isinstance(declared_in, (str, bytes)):
            _fail("Installed locator declaredIn must be an array.")
        declarations: list[str] = []
        for value in declared_in:
            if not isinstance(value, str) or not value:
                _fail("Installed locator declaredIn values must be non-empty strings.")
            if value not in declarations:
                declarations.append(value)
        return {
            "kind": "installed",
            "copilotHome": str(canonical_path(_string_property(locator, "copilotHome"))),
            "marketplaceKey": _string_property(locator, "marketplaceKey"),
            "declaredIn": declarations,
        }
    if kind == "directory":
        return {
            "kind": "directory",
            "marketplaceRoot": str(
                canonical_path(
                    _string_property(locator, "marketplaceRoot"),
                    must_exist=True,
                )
            ),
        }
    _fail(f"Unsupported marketplace locator kind '{kind}'.")


def _locator_equal(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    if _string_property(left, "kind") != _string_property(right, "kind"):
        return False
    if left["kind"] == "installed":
        return (
            _string_property(left, "marketplaceKey")
            == _string_property(right, "marketplaceKey")
            and paths_equal(
                _string_property(left, "copilotHome"),
                _string_property(right, "copilotHome"),
            )
        )
    return paths_equal(
        _string_property(left, "marketplaceRoot"),
        _string_property(right, "marketplaceRoot"),
    )


def _namespace_receipt_value(
    resolved: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
    *,
    state: str,
    now: str,
) -> dict[str, Any]:
    _assert_receipt_state(state, "namespace.json state")
    locator = _normalized_locator(_property(resolved, "locator"))
    locators: list[dict[str, Any]] = []
    if existing is not None:
        prior = _property(existing, "locators", [])
        if not isinstance(prior, Sequence) or isinstance(prior, (str, bytes)):
            _fail("namespace.json locators must be an array.")
        for item in prior:
            if not isinstance(item, Mapping):
                _fail("namespace.json locators must contain objects.")
            locators.append(dict(item))
    if locator is not None and not any(_locator_equal(locator, item) for item in locators):
        locators.append(locator)
    locators = locators[-MAX_NAMESPACE_LOCATORS:]
    source = _property(resolved, "source")
    if not isinstance(source, Mapping):
        _fail("Resolved installation source is missing.")
    created_at = _string_property(existing, "createdAt") if existing is not None else now
    return {
        "schema": "copilot-extensions.marketplace-namespace",
        "version": 1,
        "marketplaceId": _string_property(resolved, "marketplaceId"),
        "source": {
            "kind": _string_property(source, "kind"),
            "canonical": _string_property(source, "canonical"),
            "ref": _string_property(source, "ref"),
            "fingerprint": _string_property(resolved, "sourceFingerprint"),
        },
        "locators": locators,
        "generation": _property(existing, "generation") if existing is not None else 1,
        "state": state,
        "createdAt": created_at or now,
        "updatedAt": now,
    }


def _install_receipt_value(
    resolved: Mapping[str, Any],
    existing: Mapping[str, Any] | None,
    *,
    payload_version: str,
    payload_origin: str,
    payload_origin_receipt: str | os.PathLike[str] | None,
    state: str,
    now: str,
) -> dict[str, Any]:
    if not payload_version.strip():
        _fail("payload version must be a non-empty string.")
    if payload_origin not in {"installed", "directory", "staged", "explicit"}:
        _fail("payload origin must be installed, directory, staged, or explicit.")
    _assert_receipt_state(state, "install.json state")
    payload: dict[str, Any] = {
        "root": _string_property(resolved, "payloadRoot"),
        "version": payload_version,
        "origin": payload_origin,
    }
    if payload_origin_receipt is not None:
        origin_receipt = Path(payload_origin_receipt)
        if not _path_is_fully_qualified(origin_receipt):
            _fail("payload origin receipt must be absolute.")
        payload["originReceipt"] = str(canonical_path(origin_receipt, must_exist=True))
    if existing is not None:
        existing_roots = _property(existing, "roots")
        if not isinstance(existing_roots, Mapping):
            _fail("install.json roots are missing.")
        roots = {name: _string_property(existing_roots, name) for name in ROOT_NAMES}
    else:
        roots = {
            name: os.path.relpath(
                _string_property(resolved, f"{name}Root"),
                _string_property(resolved, "pluginRoot"),
            )
            for name in ROOT_NAMES
        }
    created_at = _string_property(existing, "createdAt") if existing is not None else now
    return {
        "schema": "copilot-extensions.plugin-installation",
        "version": 1,
        "marketplaceId": _string_property(resolved, "marketplaceId"),
        "pluginId": _string_property(resolved, "pluginId"),
        "pluginRoot": _string_property(resolved, "pluginRoot"),
        "namespaceReceipt": _string_property(resolved, "namespaceReceipt"),
        "payload": payload,
        "roots": roots,
        "generation": _property(existing, "generation") if existing is not None else 1,
        "state": state,
        "createdAt": created_at or now,
        "updatedAt": now,
    }


def _without_mutation_fields(receipt: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in receipt.items()
        if key not in {"generation", "updatedAt"}
    }


@_validation_scope
def stamp_context(
    *,
    payload_version: str,
    payload_origin: str,
    expected_namespace_generation: int,
    expected_install_generation: int,
    payload_origin_receipt: str | os.PathLike[str] | None = None,
    namespace_state: str = "active",
    install_state: str = "active",
    payload_root: str | os.PathLike[str] | None = None,
    plugin_id: str | None = None,
    copilot_home: str | os.PathLike[str] | None = None,
    project_root: str | os.PathLike[str] | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    source_descriptor: Mapping[str, Any] | None = None,
    marketplace_key: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Create or update installation receipts under attributable directory locks."""

    caller_environment = environment if environment is not None else os.environ
    resolution_environment = dict(caller_environment)
    resolution_environment.pop("COPILOT_EXTENSIONS_CONTEXT", None)
    resolved = resolve_context(
        payload_root=payload_root,
        plugin_id=plugin_id,
        copilot_home=copilot_home,
        project_root=project_root,
        durable_home=durable_home,
        source_descriptor=source_descriptor,
        marketplace_key=marketplace_key,
        environment=resolution_environment,
    )
    durable = canonical_path(
        durable_home
        or Path(caller_environment.get("HOME") or Path.home())
        / ".copilot-extensions"
    )
    marketplace_id = _string_property(resolved, "marketplaceId")
    resolved_plugin_id = _string_property(resolved, "pluginId")
    namespace_path = Path(_string_property(resolved, "namespaceReceipt"))
    install_path = Path(_string_property(resolved, "installReceipt"))
    namespace_changed = False
    install_changed = False

    genesis_lock = _DirectoryLock(
        durable / "marketplaces" / ".locks" / f"{marketplace_id}.genesis",
        kind="genesis",
        marketplace_id=marketplace_id,
    )
    with genesis_lock:
        existing_namespace: Mapping[str, Any] | None = None
        if namespace_path.exists():
            validated = validate_namespace_receipt(namespace_path, durable)
            existing_namespace = validated["receipt"]
            actual_generation = _property(existing_namespace, "generation")
        else:
            actual_generation = 0
        _assert_expected_generation(
            actual_generation,
            expected_namespace_generation,
            "namespace.json",
        )
        desired_namespace = _namespace_receipt_value(
            resolved,
            existing_namespace,
            state=namespace_state,
            now=_utc_now(),
        )
        if (
            existing_namespace is None
            or _without_mutation_fields(existing_namespace)
            != _without_mutation_fields(desired_namespace)
        ):
            if existing_namespace is not None:
                if actual_generation >= MAX_RECEIPT_GENERATION:
                    _fail(
                        "namespace.json generation cannot be incremented; "
                        "explicit repair is required."
                    )
                desired_namespace["generation"] = actual_generation + 1
            _atomic_write_json(namespace_path, desired_namespace, lock=genesis_lock)
            namespace_changed = True

    install_lock = _DirectoryLock(
        Path(_string_property(resolved, "cellRoot"))
        / ".locks"
        / f"{resolved_plugin_id}.install.lock",
        kind="install",
        marketplace_id=marketplace_id,
        plugin_id=resolved_plugin_id,
    )
    with install_lock:
        existing_install: Mapping[str, Any] | None = None
        if install_path.exists():
            validated = validate_context_receipt(
                install_path,
                durable,
                expected_marketplace_id=marketplace_id,
                expected_plugin_id=resolved_plugin_id,
                environment={},
            )
            existing_install = read_json(validated["installReceipt"])
            actual_generation = _property(existing_install, "generation")
        else:
            actual_generation = 0
        _assert_expected_generation(
            actual_generation,
            expected_install_generation,
            "install.json",
        )
        desired_install = _install_receipt_value(
            resolved,
            existing_install,
            payload_version=payload_version,
            payload_origin=payload_origin,
            payload_origin_receipt=payload_origin_receipt,
            state=install_state,
            now=_utc_now(),
        )
        if (
            existing_install is None
            or _without_mutation_fields(existing_install)
            != _without_mutation_fields(desired_install)
        ):
            if existing_install is not None:
                if actual_generation >= MAX_RECEIPT_GENERATION:
                    _fail(
                        "install.json generation cannot be incremented; "
                        "explicit repair is required."
                    )
                desired_install["generation"] = actual_generation + 1
            _atomic_write_json(install_path, desired_install, lock=install_lock)
            install_changed = True

    result = validate_context_receipt(
        install_path,
        durable,
        expected_marketplace_id=marketplace_id,
        expected_plugin_id=resolved_plugin_id,
        expected_payload_root=_string_property(resolved, "payloadRoot"),
        environment=caller_environment,
    )
    result.update(
        {
            "action": "stamp",
            "namespaceChanged": namespace_changed,
            "installChanged": install_changed,
            "operative": False,
        }
    )
    return result


def _snapshot_provenance_paths(
    validated: Mapping[str, Any],
    snapshot_id: str,
) -> tuple[Path, Path]:
    _assert_snapshot_id(snapshot_id)
    snapshots_root = canonical_path(_string_property(validated, "snapshotsRoot"))
    lexical_snapshot_root = snapshots_root / snapshot_id
    if _is_link_or_junction(lexical_snapshot_root):
        _fail("Snapshot root may not be a symbolic link or reparse point.")
    if not lexical_snapshot_root.is_dir():
        _fail("Snapshot root must be an existing materialized directory.")
    snapshot_root = canonical_path(lexical_snapshot_root, must_exist=True)
    if not paths_equal(snapshot_root.parent, snapshots_root):
        _fail("Snapshot root must be one direct child of snapshotsRoot.")
    if os.path.normcase(snapshot_root.name) != os.path.normcase(snapshot_id):
        _fail("Snapshot root does not retain the requested snapshot id.")
    if not any(path.name != SNAPSHOT_PROVENANCE_FILE for path in snapshot_root.iterdir()):
        _fail("Snapshot root must contain materialized payload content.")
    lexical_provenance_path = snapshot_root / SNAPSHOT_PROVENANCE_FILE
    if _is_link_or_junction(lexical_provenance_path):
        _fail("Snapshot provenance may not be a symbolic link or reparse point.")
    provenance_path = canonical_path(lexical_provenance_path)
    if not path_is_within(provenance_path, snapshots_root):
        _fail("Snapshot provenance path escapes snapshotsRoot.")
    return snapshot_root, provenance_path


def _payload_identity_from_install(
    install_path: str | os.PathLike[str],
) -> dict[str, Any]:
    install = read_json(install_path)
    if not isinstance(install, Mapping):
        _fail("install.json must be a JSON object.")
    payload = _property(install, "payload")
    if not isinstance(payload, Mapping):
        _fail("install.json payload is missing.")
    root = _string_property(payload, "root")
    if not _path_is_fully_qualified(root):
        _fail("payload.root must be absolute.")
    version = _string_property(payload, "version")
    if not version.strip():
        _fail("payload.version must be a non-empty string.")
    origin = _string_property(payload, "origin")
    if origin not in {"installed", "directory", "staged", "explicit"}:
        _fail("payload.origin must be installed, directory, staged, or explicit.")
    origin_receipt = _property(payload, "originReceipt")
    if origin_receipt is not None:
        if not isinstance(origin_receipt, str):
            _fail("payload.originReceipt must be a string.")
        if not _path_is_fully_qualified(origin_receipt):
            _fail("payload.originReceipt must be absolute.")
        origin_receipt = str(canonical_path(origin_receipt))
    return {
        "root": str(canonical_path(root)),
        "version": version,
        "origin": origin,
        "originReceipt": origin_receipt,
    }


def _validate_snapshot_provenance(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    snapshot_id: str,
    require_current_receipts: bool,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_payload_version: str | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate cell-local snapshot provenance against current canonical receipts."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    if expected_payload_root is not None and not _path_is_fully_qualified(
        expected_payload_root
    ):
        _fail("Expected snapshot payload root must be absolute.")
    if expected_payload_version is not None and (
        not isinstance(expected_payload_version, str)
        or not expected_payload_version.strip()
    ):
        _fail("Expected snapshot payload version must be a non-empty string.")
    caller_environment = environment if environment is not None else os.environ
    if durable_home is not None and not _path_is_fully_qualified(durable_home):
        _fail("--durable-home must be absolute.")
    durable = canonical_path(
        durable_home
        or Path(caller_environment.get("HOME") or Path.home())
        / ".copilot-extensions"
    )
    context_path = Path(context)
    if not _path_is_fully_qualified(context_path):
        _fail("Snapshot context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    snapshot_root, provenance_path = _snapshot_provenance_paths(
        validated,
        snapshot_id,
    )
    actual_provenance = canonical_path(provenance_path, must_exist=True)
    if not paths_equal(actual_provenance, provenance_path):
        _fail(
            "Snapshot provenance is not at its exact canonical location "
            f"'{provenance_path}'."
        )
    provenance = read_json(actual_provenance)
    if not isinstance(provenance, Mapping):
        _fail("Snapshot provenance must be a JSON object.")
    provenance_version = _property(provenance, "version")
    if (
        _string_property(provenance, "schema") != SNAPSHOT_PROVENANCE_SCHEMA
        or isinstance(provenance_version, bool)
        or not isinstance(provenance_version, int)
        or provenance_version != 1
    ):
        _fail("Snapshot provenance has an unsupported schema or version.")
    marketplace_id = _string_property(provenance, "marketplaceId")
    plugin_id = _string_property(provenance, "pluginId")
    if marketplace_id != expected_marketplace_id:
        _fail(
            f"Expected marketplace '{expected_marketplace_id}', snapshot provenance "
            f"names '{marketplace_id}'."
        )
    if plugin_id != expected_plugin_id:
        _fail(
            f"Expected plugin '{expected_plugin_id}', snapshot provenance names "
            f"'{plugin_id}'."
        )

    source = _property(provenance, "source")
    if not isinstance(source, Mapping):
        _fail("Snapshot provenance source is missing.")
    normalized = normalize_source(
        {
            "kind": _string_property(source, "kind"),
            "canonical": _string_property(source, "canonical"),
            "ref": _string_property(source, "ref"),
        },
        from_receipt=True,
    )
    identity = source_identity(normalized, marketplace_id.rsplit("--", 1)[0])
    fingerprint = _string_property(source, "fingerprint")
    if identity["marketplaceId"] != marketplace_id:
        _fail("Snapshot provenance marketplaceId does not match its normalized source.")
    if identity["fingerprint"] != fingerprint:
        _fail("Snapshot provenance fingerprint does not match its normalized source.")
    if (
        fingerprint != _string_property(validated, "sourceFingerprint")
        or normalized.kind != _string_property(validated["source"], "kind")
        or normalized.canonical != _string_property(validated["source"], "canonical")
        or normalized.ref != _string_property(validated["source"], "ref")
    ):
        _fail("Snapshot provenance source does not match the canonical namespace receipt.")

    snapshot = _property(provenance, "snapshot")
    if not isinstance(snapshot, Mapping):
        _fail("Snapshot provenance snapshot identity is missing.")
    if _string_property(snapshot, "id") != snapshot_id:
        _fail("Snapshot provenance id does not match its canonical snapshot directory.")
    recorded_snapshot_root = _string_property(snapshot, "root")
    if not _path_is_fully_qualified(recorded_snapshot_root):
        _fail("Snapshot provenance snapshot.root must be absolute.")
    if not paths_equal(recorded_snapshot_root, snapshot_root):
        _fail("Snapshot provenance snapshot.root is not its exact canonical location.")

    namespace_reference = _property(provenance, "namespaceReceipt")
    install_reference = _property(provenance, "installReceipt")
    if not isinstance(namespace_reference, Mapping) or not isinstance(
        install_reference,
        Mapping,
    ):
        _fail("Snapshot provenance receipt references are missing.")
    namespace_path = _string_property(namespace_reference, "path")
    install_path = _string_property(install_reference, "path")
    if not _path_is_fully_qualified(namespace_path) or not _path_is_fully_qualified(
        install_path
    ):
        _fail("Snapshot provenance receipt paths must be absolute.")
    if not paths_equal(namespace_path, _string_property(validated, "namespaceReceipt")):
        _fail("Snapshot provenance namespace receipt does not match the current context.")
    if not paths_equal(install_path, _string_property(validated, "installReceipt")):
        _fail("Snapshot provenance install receipt does not match the current context.")
    namespace_generation = _property(namespace_reference, "generation")
    install_generation = _property(install_reference, "generation")
    _assert_receipt_generation(
        namespace_generation,
        "snapshot provenance namespace generation",
    )
    _assert_receipt_generation(
        install_generation,
        "snapshot provenance install generation",
    )
    current_namespace_generation = _property(validated, "namespaceGeneration")
    current_install_generation = _property(validated, "generation")
    if require_current_receipts:
        if namespace_generation != current_namespace_generation:
            _fail(
                "Snapshot provenance namespace generation is stale; "
                "restart snapshot production."
            )
        if install_generation != current_install_generation:
            _fail(
                "Snapshot provenance install generation is stale; "
                "restart snapshot production."
            )
    elif (
        current_namespace_generation < namespace_generation
        or current_install_generation < install_generation
    ):
        _fail("Current receipt generation predates the owned runtime slot.")

    namespace = read_json(namespace_path)
    if not isinstance(namespace, Mapping):
        _fail("namespace.json must be a JSON object.")
    if require_current_receipts and (
        _string_property(namespace, "state") != "active"
        or _string_property(validated, "state") != "active"
    ):
        _fail("Snapshot provenance requires active namespace and install receipts.")

    payload = _property(provenance, "payload")
    if not isinstance(payload, Mapping):
        _fail("Snapshot provenance payload identity is missing.")
    if "originReceipt" not in payload:
        _fail("Snapshot provenance payload.originReceipt must be present.")
    recorded_payload = {
        "root": _string_property(payload, "root"),
        "version": _string_property(payload, "version"),
        "origin": _string_property(payload, "origin"),
        "originReceipt": _property(payload, "originReceipt"),
    }
    if not _path_is_fully_qualified(recorded_payload["root"]):
        _fail("Snapshot provenance payload.root must be absolute.")
    recorded_payload["root"] = str(canonical_path(recorded_payload["root"]))
    origin_receipt = recorded_payload["originReceipt"]
    if origin_receipt is not None:
        if not isinstance(origin_receipt, str):
            _fail("Snapshot provenance payload.originReceipt must be a string or null.")
        if not _path_is_fully_qualified(origin_receipt):
            _fail("Snapshot provenance payload.originReceipt must be absolute.")
        recorded_payload["originReceipt"] = str(canonical_path(origin_receipt))
    if expected_payload_root is not None and not paths_equal(
        recorded_payload["root"],
        expected_payload_root,
    ):
        _fail(
            f"Expected snapshot payload root '{expected_payload_root}', provenance "
            f"names '{recorded_payload['root']}'."
        )
    if (
        expected_payload_version is not None
        and recorded_payload["version"] != expected_payload_version
    ):
        _fail(
            f"Expected snapshot payload version '{expected_payload_version}', "
            f"provenance names '{recorded_payload['version']}'."
        )
    current_payload = _payload_identity_from_install(validated["installReceipt"])
    if require_current_receipts and recorded_payload != current_payload:
        _fail("Snapshot provenance payload does not match the pinned install receipt.")
    _parse_rfc3339_utc(
        _string_property(provenance, "createdAt"),
        "snapshot provenance createdAt",
    )
    return {
        "action": "snapshot-validate",
        "status": "ready",
        "reason": "snapshot-provenance-valid",
        "provenance": str(actual_provenance),
        "snapshotRoot": str(snapshot_root),
        "snapshotId": snapshot_id,
        "marketplaceId": marketplace_id,
        "pluginId": plugin_id,
        "sourceFingerprint": fingerprint,
        "namespaceReceipt": str(canonical_path(namespace_path)),
        "installReceipt": str(canonical_path(install_path)),
        "namespaceGeneration": namespace_generation,
        "installGeneration": install_generation,
        "payload": current_payload if require_current_receipts else recorded_payload,
        "operative": False,
    }


@_validation_scope
def validate_snapshot_provenance(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    snapshot_id: str,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_payload_version: str | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate snapshot provenance against the current canonical receipts."""

    return _validate_snapshot_provenance(
        context=context,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        snapshot_id=snapshot_id,
        expected_payload_root=expected_payload_root,
        expected_payload_version=expected_payload_version,
        durable_home=durable_home,
        environment=environment,
        require_current_receipts=True,
    )


@_validation_scope
def stamp_snapshot_provenance(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    expected_namespace_generation: int,
    expected_install_generation: int,
    snapshot_id: str,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Atomically publish immutable snapshot provenance under both receipt locks."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    _assert_expected_generation(
        expected_namespace_generation,
        expected_namespace_generation,
        "namespace.json",
    )
    _assert_expected_generation(
        expected_install_generation,
        expected_install_generation,
        "install.json",
    )
    caller_environment = environment if environment is not None else os.environ
    if durable_home is not None and not _path_is_fully_qualified(durable_home):
        _fail("--durable-home must be absolute.")
    durable = canonical_path(
        durable_home
        or Path(caller_environment.get("HOME") or Path.home())
        / ".copilot-extensions"
    )
    context_path = Path(context)
    if not _path_is_fully_qualified(context_path):
        _fail("Snapshot context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    cell_root = canonical_path(_string_property(validated, "cellRoot"))
    plugin_root = canonical_path(_string_property(validated, "pluginRoot"))
    install_path = canonical_path(_string_property(validated, "installReceipt"))
    genesis_lock = _DirectoryLock(
        durable
        / "marketplaces"
        / ".locks"
        / f"{expected_marketplace_id}.genesis",
        kind="genesis",
        marketplace_id=expected_marketplace_id,
    )
    install_lock = _DirectoryLock(
        cell_root / ".locks" / f"{expected_plugin_id}.install.lock",
        kind="install",
        marketplace_id=expected_marketplace_id,
        plugin_id=expected_plugin_id,
    )
    with genesis_lock, install_lock:
        validated = validate_context_receipt(
            install_path,
            durable,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            expected_cell_root=cell_root,
            environment={},
        )
        _assert_expected_generation(
            int(validated["namespaceGeneration"]),
            expected_namespace_generation,
            "namespace.json",
        )
        _assert_expected_generation(
            int(validated["generation"]),
            expected_install_generation,
            "install.json",
        )
        namespace = read_json(validated["namespaceReceipt"])
        if not isinstance(namespace, Mapping):
            _fail("namespace.json must be a JSON object.")
        if (
            _string_property(namespace, "state") != "active"
            or _string_property(validated, "state") != "active"
        ):
            _fail("Snapshot provenance requires active namespace and install receipts.")
        snapshot_root, provenance_path = _snapshot_provenance_paths(
            validated,
            snapshot_id,
        )
        snapshot_changed = False
        if os.path.lexists(provenance_path):
            published = validate_snapshot_provenance(
                context=install_path,
                expected_marketplace_id=expected_marketplace_id,
                expected_plugin_id=expected_plugin_id,
                snapshot_id=snapshot_id,
                durable_home=durable,
                environment={},
            )
        else:
            source = validated["source"]
            payload = _payload_identity_from_install(install_path)
            desired = {
                "schema": SNAPSHOT_PROVENANCE_SCHEMA,
                "version": 1,
                "marketplaceId": expected_marketplace_id,
                "pluginId": expected_plugin_id,
                "source": {
                    "kind": _string_property(source, "kind"),
                    "canonical": _string_property(source, "canonical"),
                    "ref": _string_property(source, "ref"),
                    "fingerprint": _string_property(validated, "sourceFingerprint"),
                },
                "snapshot": {
                    "id": snapshot_id,
                    "root": str(snapshot_root),
                },
                "payload": payload,
                "namespaceReceipt": {
                    "path": _string_property(validated, "namespaceReceipt"),
                    "generation": int(validated["namespaceGeneration"]),
                },
                "installReceipt": {
                    "path": _string_property(validated, "installReceipt"),
                    "generation": int(validated["generation"]),
                },
                "createdAt": _utc_now(),
            }
            _atomic_write_json(
                provenance_path,
                desired,
                lock=(genesis_lock, install_lock),
            )
            snapshot_changed = True
            published = validate_snapshot_provenance(
                context=install_path,
                expected_marketplace_id=expected_marketplace_id,
                expected_plugin_id=expected_plugin_id,
                snapshot_id=snapshot_id,
                durable_home=durable,
                environment={},
            )
        published.update(
            {
                "action": "snapshot-stamp",
                "reason": (
                    "snapshot-provenance-published"
                    if snapshot_changed
                    else "snapshot-provenance-current"
                ),
                "snapshotChanged": snapshot_changed,
                "pluginRoot": str(plugin_root),
            }
        )
        return published


def _runtime_slot_paths(
    validated: Mapping[str, Any],
    runtime_version: str,
    *,
    require_existing: bool,
) -> tuple[Path, Path, Path]:
    _assert_runtime_version(runtime_version)
    plugin_root = canonical_path(_string_property(validated, "pluginRoot"))
    install = read_json(_string_property(validated, "installReceipt"))
    roots = _property(install, "roots") if isinstance(install, Mapping) else None
    if not isinstance(roots, Mapping):
        _fail("install.json roots is missing.")
    versions_relative = _string_property(roots, "versions")
    lexical_versions_root = plugin_root / versions_relative
    cursor = plugin_root
    for part in Path(versions_relative).parts:
        cursor /= part
        if _is_link_or_junction(cursor):
            _fail("Versions root may not traverse a symbolic link or reparse point.")
        if cursor.exists() and not cursor.is_dir():
            _fail("Versions root path components must be ordinary directories.")
    if require_existing and not lexical_versions_root.is_dir():
        _fail("Versions root must be an existing directory.")
    resolved_versions_root = canonical_path(lexical_versions_root)
    if not paths_equal(
        resolved_versions_root,
        _string_property(validated, "versionsRoot"),
    ):
        _fail("Versions root does not match the validated install receipt.")
    if paths_equal(resolved_versions_root, plugin_root) or not path_is_within(
        resolved_versions_root,
        plugin_root,
    ):
        _fail("Versions root must remain beneath the canonical plugin root.")
    lexical_slot_root = resolved_versions_root / runtime_version
    if _is_link_or_junction(lexical_slot_root):
        _fail("Runtime slot may not be a symbolic link or reparse point.")
    if require_existing and not lexical_slot_root.is_dir():
        _fail("Runtime slot must be an existing directory.")
    slot_root = canonical_path(lexical_slot_root)
    if not paths_equal(slot_root.parent, resolved_versions_root):
        _fail("Runtime slot must be one direct child of versionsRoot.")
    if os.path.normcase(slot_root.name) != os.path.normcase(runtime_version):
        _fail("Runtime slot does not retain the requested runtime version.")
    ownership_path = slot_root / RUNTIME_SLOT_OWNERSHIP_FILE
    if _is_link_or_junction(ownership_path):
        _fail("Runtime slot ownership may not be a symbolic link or reparse point.")
    return resolved_versions_root, slot_root, ownership_path


def _runtime_slot_ownership_value(
    validated: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    runtime_version: str,
    slot_root: Path,
    *,
    created_at: str,
) -> dict[str, Any]:
    return {
        "schema": RUNTIME_SLOT_OWNERSHIP_SCHEMA,
        "version": 1,
        "marketplaceId": _string_property(validated, "marketplaceId"),
        "pluginId": _string_property(validated, "pluginId"),
        "sourceFingerprint": _string_property(validated, "sourceFingerprint"),
        "runtime": {
            "version": runtime_version,
            "root": str(slot_root),
        },
        "snapshot": {
            "id": _string_property(snapshot, "snapshotId"),
            "root": _string_property(snapshot, "snapshotRoot"),
            "provenance": _string_property(snapshot, "provenance"),
            "provenanceSha256": _sha256_file(
                Path(_string_property(snapshot, "provenance"))
            ),
        },
        "namespaceReceipt": {
            "path": _string_property(snapshot, "namespaceReceipt"),
            "generation": _property(snapshot, "namespaceGeneration"),
        },
        "installReceipt": {
            "path": _string_property(snapshot, "installReceipt"),
            "generation": _property(snapshot, "installGeneration"),
        },
        "createdAt": created_at,
    }


def _validated_runtime_slot_ownership(
    validated: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    runtime_version: str,
) -> dict[str, Any]:
    _, slot_root, ownership_path = _runtime_slot_paths(
        validated,
        runtime_version,
        require_existing=True,
    )
    if not ownership_path.exists():
        _fail("Runtime slot ownership must exist.")
    actual_ownership = canonical_path(ownership_path, must_exist=True)
    if not paths_equal(actual_ownership, ownership_path):
        _fail(
            "Runtime slot ownership is not at its exact canonical location "
            f"'{ownership_path}'."
        )
    if not actual_ownership.is_file():
        _fail("Runtime slot ownership must be an ordinary file.")
    ownership = read_json(actual_ownership)
    if not isinstance(ownership, Mapping):
        _fail("Runtime slot ownership must be a JSON object.")
    ownership_version = _property(ownership, "version")
    if (
        _string_property(ownership, "schema") != RUNTIME_SLOT_OWNERSHIP_SCHEMA
        or isinstance(ownership_version, bool)
        or not isinstance(ownership_version, int)
        or ownership_version != 1
    ):
        _fail("Runtime slot ownership has an unsupported schema or version.")
    created_at = _string_property(ownership, "createdAt")
    _parse_rfc3339_utc(created_at, "runtime slot ownership createdAt")
    expected = _runtime_slot_ownership_value(
        validated,
        snapshot,
        runtime_version,
        slot_root,
        created_at=created_at,
    )
    runtime = _property(ownership, "runtime")
    recorded_snapshot = _property(ownership, "snapshot")
    namespace_reference = _property(ownership, "namespaceReceipt")
    install_reference = _property(ownership, "installReceipt")
    if not all(
        isinstance(value, Mapping)
        for value in (
            runtime,
            recorded_snapshot,
            namespace_reference,
            install_reference,
        )
    ):
        _fail("Runtime slot ownership identity objects are missing.")
    path_fields = (
        (runtime, expected["runtime"], "root", "runtime.root"),
        (
            recorded_snapshot,
            expected["snapshot"],
            "root",
            "snapshot.root",
        ),
        (
            recorded_snapshot,
            expected["snapshot"],
            "provenance",
            "snapshot.provenance",
        ),
        (
            namespace_reference,
            expected["namespaceReceipt"],
            "path",
            "namespaceReceipt.path",
        ),
        (
            install_reference,
            expected["installReceipt"],
            "path",
            "installReceipt.path",
        ),
    )
    for recorded, expected_container, key, label in path_fields:
        recorded_path = _string_property(recorded, key)
        if not _path_is_fully_qualified(recorded_path):
            _fail(f"Runtime slot ownership {label} must be absolute.")
        if not paths_equal(recorded_path, _string_property(expected_container, key)):
            _fail(
                "Runtime slot ownership does not match the validated snapshot "
                "and installation receipts."
            )
        expected_container[key] = recorded_path
    _assert_receipt_generation(
        _property(namespace_reference, "generation"),
        "runtime slot ownership namespace generation",
    )
    _assert_receipt_generation(
        _property(install_reference, "generation"),
        "runtime slot ownership install generation",
    )
    if dict(ownership) != expected:
        _fail(
            "Runtime slot ownership does not match the validated snapshot "
            "and installation receipts."
        )
    namespace = read_json(_string_property(validated, "namespaceReceipt"))
    if not isinstance(namespace, Mapping):
        _fail("namespace.json must be a JSON object.")
    return {
        "action": "slot-validate",
        "status": "ready",
        "reason": "runtime-slot-ownership-valid",
        "slotRoot": str(slot_root),
        "runtimeVersion": runtime_version,
        "ownership": str(actual_ownership),
        "snapshotId": _string_property(snapshot, "snapshotId"),
        "snapshotProvenance": _string_property(snapshot, "provenance"),
        "marketplaceId": _string_property(validated, "marketplaceId"),
        "pluginId": _string_property(validated, "pluginId"),
        "sourceFingerprint": _string_property(validated, "sourceFingerprint"),
        "namespaceReceipt": _string_property(snapshot, "namespaceReceipt"),
        "installReceipt": _string_property(snapshot, "installReceipt"),
        "namespaceGeneration": _property(snapshot, "namespaceGeneration"),
        "installGeneration": _property(snapshot, "installGeneration"),
        "namespaceState": _string_property(namespace, "state"),
        "installState": _string_property(validated, "state"),
        "slotEmpty": not any(
            child.name != RUNTIME_SLOT_OWNERSHIP_FILE
            for child in slot_root.iterdir()
        ),
        "activated": False,
        "operative": False,
    }


@_validation_scope
def validate_runtime_slot_ownership(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    snapshot_id: str,
    runtime_version: str,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_payload_version: str | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate one cell-local runtime slot against snapshot and receipt identity."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    _assert_runtime_version(runtime_version)
    caller_environment = environment if environment is not None else os.environ
    if durable_home is not None and not _path_is_fully_qualified(durable_home):
        _fail("--durable-home must be absolute.")
    durable = canonical_path(
        durable_home
        or Path(caller_environment.get("HOME") or Path.home())
        / ".copilot-extensions"
    )
    context_path = Path(context)
    if not _path_is_fully_qualified(context_path):
        _fail("Runtime slot context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    snapshot = _validate_snapshot_provenance(
        context=context_path,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        snapshot_id=snapshot_id,
        expected_payload_root=expected_payload_root,
        expected_payload_version=expected_payload_version,
        durable_home=durable,
        environment={},
        require_current_receipts=False,
    )
    return _validated_runtime_slot_ownership(
        validated,
        snapshot,
        runtime_version,
    )


@_validation_scope
def provision_runtime_slot(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    snapshot_id: str,
    runtime_version: str,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_payload_version: str | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Create or validate an owned runtime slot without activating it."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    _assert_runtime_version(runtime_version)
    caller_environment = environment if environment is not None else os.environ
    if durable_home is not None and not _path_is_fully_qualified(durable_home):
        _fail("--durable-home must be absolute.")
    durable = canonical_path(
        durable_home
        or Path(caller_environment.get("HOME") or Path.home())
        / ".copilot-extensions"
    )
    context_path = Path(context)
    if not _path_is_fully_qualified(context_path):
        _fail("Runtime slot context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    cell_root = canonical_path(_string_property(validated, "cellRoot"))
    genesis_lock = _DirectoryLock(
        durable
        / "marketplaces"
        / ".locks"
        / f"{expected_marketplace_id}.genesis",
        kind="genesis",
        marketplace_id=expected_marketplace_id,
        timeout_seconds=RUNTIME_SLOT_LOCK_TIMEOUT_SECONDS,
    )
    install_lock = _DirectoryLock(
        cell_root / ".locks" / f"{expected_plugin_id}.install.lock",
        kind="install",
        marketplace_id=expected_marketplace_id,
        plugin_id=expected_plugin_id,
        timeout_seconds=RUNTIME_SLOT_LOCK_TIMEOUT_SECONDS,
    )
    with genesis_lock, install_lock:
        validated = validate_context_receipt(
            context_path,
            durable,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            environment={},
        )
        versions_root, slot_root, _ = _runtime_slot_paths(
            validated,
            runtime_version,
            require_existing=False,
        )
        if slot_root.exists():
            snapshot = _validate_snapshot_provenance(
                context=context_path,
                expected_marketplace_id=expected_marketplace_id,
                expected_plugin_id=expected_plugin_id,
                snapshot_id=snapshot_id,
                expected_payload_root=expected_payload_root,
                expected_payload_version=expected_payload_version,
                durable_home=durable,
                environment={},
                require_current_receipts=False,
            )
            result = _validated_runtime_slot_ownership(
                validated,
                snapshot,
                runtime_version,
            )
            result["action"] = "slot-provision"
            result["reason"] = "runtime-slot-ownership-current"
            result["slotChanged"] = False
            return result
        snapshot = _validate_snapshot_provenance(
            context=context_path,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            snapshot_id=snapshot_id,
            expected_payload_root=expected_payload_root,
            expected_payload_version=expected_payload_version,
            durable_home=durable,
            environment={},
            require_current_receipts=True,
        )
        try:
            versions_root.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            _fail(f"Cannot create versions root '{versions_root}': {error}")
        verified_versions_root, slot_root, _ = _runtime_slot_paths(
            validated,
            runtime_version,
            require_existing=False,
        )
        if not paths_equal(verified_versions_root, versions_root):
            _fail("Versions root changed during runtime slot provisioning.")
        if not versions_root.is_dir():
            _fail("Versions root must be an ordinary directory.")
        slot_digest = hashlib.sha256(os.fsencode(slot_root)).hexdigest()[:16]
        temporary_slot = versions_root.parent / (
            f".runtime-slot-{slot_digest}-{secrets.token_hex(8)}"
        )
        temporary_ownership = temporary_slot / RUNTIME_SLOT_OWNERSHIP_FILE
        try:
            temporary_slot.mkdir(mode=0o700)
            ownership = _runtime_slot_ownership_value(
                validated,
                snapshot,
                runtime_version,
                slot_root,
                created_at=_utc_now(),
            )
            genesis_lock.assert_owned()
            install_lock.assert_owned()
            _write_private_json(temporary_ownership, ownership)
            genesis_lock.assert_owned()
            install_lock.assert_owned()
            _rename_directory_no_replace(temporary_slot, slot_root)
            if os.name != "nt":
                for parent in (versions_root.parent, versions_root):
                    directory = os.open(parent, os.O_RDONLY)
                    try:
                        os.fsync(directory)
                    finally:
                        os.close(directory)
        except BaseException:
            try:
                temporary_ownership.unlink()
            except OSError:
                pass
            try:
                temporary_slot.rmdir()
            except OSError:
                pass
            raise
        result = _validated_runtime_slot_ownership(
            validated,
            snapshot,
            runtime_version,
        )
        result["action"] = "slot-provision"
        result["reason"] = "runtime-slot-ownership-published"
        result["slotChanged"] = True
        return result


def _runtime_slot_completion_paths(
    validated: Mapping[str, Any],
    runtime_version: str,
) -> tuple[Path, Path, Path, Path]:
    _, slot_root, ownership_path = _runtime_slot_paths(
        validated,
        runtime_version,
        require_existing=True,
    )
    build_path = slot_root / BUILD_COMPLETION_FILE
    completion_path = slot_root / RUNTIME_SLOT_COMPLETION_FILE
    if _is_link_or_junction(completion_path):
        _fail("Runtime slot completion may not be a symbolic link or reparse point.")
    return slot_root, ownership_path, build_path, completion_path


def _read_regular_json_object(
    path: Path,
    *,
    label: str,
    required_keys: set[str],
    require_stable_identity: bool = False,
) -> tuple[dict[str, Any], str]:
    if not os.path.lexists(path):
        _fail(f"{label} must exist.")
    if _is_link_or_junction(path):
        _fail(f"{label} may not be a symbolic link or reparse point.")
    actual = canonical_path(path, must_exist=True)
    if not paths_equal(actual, path):
        _fail(f"{label} is not at its exact canonical location '{path}'.")
    try:
        content, _validated_stat = _read_regular_file(
            actual,
            label=label,
            require_stable_identity=require_stable_identity,
        )
    except OSError as error:
        _fail(f"Cannot read {label.lower()} '{actual}': {error}")
    try:
        value = json.loads(
            content.decode("utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (UnicodeError, json.JSONDecodeError) as error:
        _fail(f"Invalid JSON in '{actual}': {error}")
    if not isinstance(value, Mapping):
        _fail(f"{label} must be a JSON object.")
    if set(value) != required_keys:
        _fail(f"{label} contains unknown or missing fields.")
    digest = hashlib.sha256(content).hexdigest()
    return dict(value), digest


def _validated_build_completion(
    build_path: Path,
    runtime_version: str,
    snapshot_content_sha256: str,
) -> dict[str, Any]:
    build, receipt_sha256 = _read_regular_json_object(
        build_path,
        label="Build completion evidence",
        required_keys={"version", "completed_at", "pid", "payload_hash"},
    )
    version = build["version"]
    if not isinstance(version, str) or version != runtime_version:
        _fail("Build completion evidence version must match the runtime version.")
    completed_at = build["completed_at"]
    _parse_rfc3339_utc(completed_at, "build completion completed_at")
    pid = build["pid"]
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid < 0
        or pid > MAX_RECEIPT_PID
    ):
        _fail(
            "Build completion evidence pid must be an integer from 0 through "
            f"{MAX_RECEIPT_PID}."
        )
    payload_hash = build["payload_hash"]
    if not isinstance(payload_hash, str) or LOWER_SHA256.fullmatch(payload_hash) is None:
        _fail("Build completion evidence payload_hash must be lowercase 64-hex.")
    if payload_hash != snapshot_content_sha256:
        _fail(
            "Build completion evidence payload_hash does not match the snapshot "
            "content digest."
        )
    return {
        "path": str(build_path),
        "receiptSha256": receipt_sha256,
        "payloadSha256": payload_hash,
        "pid": pid,
        "completedAt": completed_at,
        "value": build,
    }


def _runtime_slot_completion_value(
    validated: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    ownership: Mapping[str, Any],
    build: Mapping[str, Any],
    runtime_version: str,
    slot_root: Path,
    snapshot_content_sha256: str,
) -> dict[str, Any]:
    ownership_path = Path(_string_property(ownership, "ownership"))
    return {
        "schema": RUNTIME_SLOT_COMPLETION_SCHEMA,
        "marketplaceId": _string_property(validated, "marketplaceId"),
        "pluginId": _string_property(validated, "pluginId"),
        "sourceFingerprint": _string_property(validated, "sourceFingerprint"),
        "runtime": {
            "version": runtime_version,
            "root": str(slot_root),
        },
        "snapshot": {
            "id": _string_property(snapshot, "snapshotId"),
            "provenance": _string_property(snapshot, "provenance"),
            "provenanceSha256": _sha256_file(
                Path(_string_property(snapshot, "provenance"))
            ),
            "contentSha256": snapshot_content_sha256,
        },
        "ownership": {
            "path": str(ownership_path),
            "sha256": _sha256_file(ownership_path),
        },
        "build": {
            "receipt": _string_property(build, "path"),
            "receiptSha256": _string_property(build, "receiptSha256"),
            "payloadSha256": _string_property(build, "payloadSha256"),
            "pid": _property(build, "pid"),
        },
        "namespaceReceipt": {
            "path": _string_property(ownership, "namespaceReceipt"),
            "generation": _property(ownership, "namespaceGeneration"),
        },
        "installReceipt": {
            "path": _string_property(ownership, "installReceipt"),
            "generation": _property(ownership, "installGeneration"),
        },
        "completedAt": _string_property(build, "completedAt"),
    }


def _validated_runtime_slot_completion(
    validated: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    ownership: Mapping[str, Any],
    runtime_version: str,
) -> dict[str, Any]:
    slot_root, ownership_path, build_path, completion_path = (
        _runtime_slot_completion_paths(validated, runtime_version)
    )
    snapshot_content_sha256 = _snapshot_content_sha256(
        Path(_string_property(snapshot, "snapshotRoot"))
    )
    completion, _ = _read_regular_json_object(
        completion_path,
        label="Runtime slot completion",
        require_stable_identity=True,
        required_keys={
            "schema",
            "marketplaceId",
            "pluginId",
            "sourceFingerprint",
            "runtime",
            "snapshot",
            "ownership",
            "build",
            "namespaceReceipt",
            "installReceipt",
            "completedAt",
        },
    )
    nested_shapes = (
        ("runtime", {"version", "root"}),
        ("snapshot", {"id", "provenance", "provenanceSha256", "contentSha256"}),
        ("ownership", {"path", "sha256"}),
        ("build", {"receipt", "receiptSha256", "payloadSha256", "pid"}),
        ("namespaceReceipt", {"path", "generation"}),
        ("installReceipt", {"path", "generation"}),
    )
    for name, keys in nested_shapes:
        nested = completion[name]
        if not isinstance(nested, Mapping) or set(nested) != keys:
            _fail(f"Runtime slot completion {name} contains unknown or missing fields.")
    if completion["schema"] != RUNTIME_SLOT_COMPLETION_SCHEMA:
        _fail("Runtime slot completion has an unsupported schema.")
    _parse_rfc3339_utc(
        completion["completedAt"],
        "runtime slot completion completedAt",
    )
    for container_name, key in (
        ("namespaceReceipt", "generation"),
        ("installReceipt", "generation"),
    ):
        _assert_receipt_generation(
            completion[container_name][key],
            f"runtime slot completion {container_name} generation",
        )
    for container_name, fields in (
        ("snapshot", ("provenanceSha256", "contentSha256")),
        ("ownership", ("sha256",)),
        ("build", ("receiptSha256", "payloadSha256")),
    ):
        for field in fields:
            value = completion[container_name][field]
            if not isinstance(value, str) or LOWER_SHA256.fullmatch(value) is None:
                _fail(
                    f"Runtime slot completion {container_name}.{field} must be "
                    "lowercase 64-hex."
                )
    pid = completion["build"]["pid"]
    if (
        isinstance(pid, bool)
        or not isinstance(pid, int)
        or pid < 0
        or pid > MAX_RECEIPT_PID
    ):
        _fail(
            "Runtime slot completion build.pid must be an integer from 0 through "
            f"{MAX_RECEIPT_PID}."
        )
    expected_paths = {
        ("runtime", "root"): str(slot_root),
        ("snapshot", "provenance"): _string_property(snapshot, "provenance"),
        ("ownership", "path"): str(ownership_path),
        ("build", "receipt"): str(build_path),
        ("namespaceReceipt", "path"): _string_property(
            ownership, "namespaceReceipt"
        ),
        ("installReceipt", "path"): _string_property(ownership, "installReceipt"),
    }
    path_fields = (
        ("runtime", "root"),
        ("snapshot", "provenance"),
        ("ownership", "path"),
        ("build", "receipt"),
        ("namespaceReceipt", "path"),
        ("installReceipt", "path"),
    )
    for container_name, key in path_fields:
        recorded = completion[container_name][key]
        if not isinstance(recorded, str) or not _path_is_fully_qualified(recorded):
            _fail(
                f"Runtime slot completion {container_name}.{key} must be absolute."
            )
        if recorded != expected_paths[(container_name, key)]:
            _fail(
                "Runtime slot completion does not match the validated snapshot, "
                "ownership, and installation receipts."
            )
    if (
        completion["marketplaceId"] != _string_property(validated, "marketplaceId")
        or completion["pluginId"] != _string_property(validated, "pluginId")
        or completion["sourceFingerprint"]
        != _string_property(validated, "sourceFingerprint")
        or completion["runtime"]["version"] != runtime_version
        or completion["snapshot"]["id"] != _string_property(snapshot, "snapshotId")
        or completion["snapshot"]["provenanceSha256"]
        != _sha256_file(Path(_string_property(snapshot, "provenance")))
        or completion["snapshot"]["contentSha256"] != snapshot_content_sha256
        or completion["ownership"]["sha256"] != _sha256_file(ownership_path)
        or completion["build"]["payloadSha256"] != snapshot_content_sha256
        or completion["namespaceReceipt"]["generation"]
        != _property(ownership, "namespaceGeneration")
        or completion["installReceipt"]["generation"]
        != _property(ownership, "installGeneration")
    ):
        _fail(
            "Runtime slot completion does not match the validated snapshot, "
            "ownership, and installation receipts."
        )
    return {
        "action": "slot-completion-validate",
        "status": "ready",
        "reason": "runtime-slot-completion-valid",
        "slotRoot": str(slot_root),
        "runtimeVersion": runtime_version,
        "ownership": str(ownership_path),
        "completion": str(completion_path),
        "buildReceipt": str(build_path),
        "receipt": completion,
        "snapshotId": _string_property(snapshot, "snapshotId"),
        "snapshotProvenance": _string_property(snapshot, "provenance"),
        "marketplaceId": _string_property(validated, "marketplaceId"),
        "pluginId": _string_property(validated, "pluginId"),
        "sourceFingerprint": _string_property(validated, "sourceFingerprint"),
        "namespaceReceipt": _string_property(ownership, "namespaceReceipt"),
        "installReceipt": _string_property(ownership, "installReceipt"),
        "namespaceGeneration": _property(ownership, "namespaceGeneration"),
        "installGeneration": _property(ownership, "installGeneration"),
        "completedAt": completion["completedAt"],
        "payloadSha256": completion["build"]["payloadSha256"],
        "activated": False,
        "operative": False,
    }


@_validation_scope
def validate_runtime_slot_completion(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    expected_payload_root: str | os.PathLike[str],
    expected_payload_version: str,
    snapshot_id: str,
    runtime_version: str,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Validate immutable runtime build completion without activating the slot."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    _assert_runtime_version(runtime_version)
    if not _path_is_fully_qualified(expected_payload_root):
        _fail("Expected snapshot payload root must be absolute.")
    if not isinstance(expected_payload_version, str) or not expected_payload_version.strip():
        _fail("Expected snapshot payload version must be a non-empty string.")
    caller_environment = environment if environment is not None else os.environ
    if durable_home is not None and not _path_is_fully_qualified(durable_home):
        _fail("--durable-home must be absolute.")
    durable = canonical_path(
        durable_home
        or Path(caller_environment.get("HOME") or Path.home())
        / ".copilot-extensions"
    )
    context_path = Path(context)
    if not _path_is_fully_qualified(context_path):
        _fail("Runtime slot context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    snapshot = _validate_snapshot_provenance(
        context=context_path,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        snapshot_id=snapshot_id,
        expected_payload_root=expected_payload_root,
        expected_payload_version=expected_payload_version,
        durable_home=durable,
        environment={},
        require_current_receipts=False,
    )
    ownership = _validated_runtime_slot_ownership(
        validated,
        snapshot,
        runtime_version,
    )
    return _validated_runtime_slot_completion(
        validated,
        snapshot,
        ownership,
        runtime_version,
    )


@_validation_scope
def complete_runtime_slot(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    expected_payload_root: str | os.PathLike[str],
    expected_payload_version: str,
    snapshot_id: str,
    runtime_version: str,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Publish immutable runtime build completion without activating the slot."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    _assert_runtime_version(runtime_version)
    if not _path_is_fully_qualified(expected_payload_root):
        _fail("Expected snapshot payload root must be absolute.")
    if not isinstance(expected_payload_version, str) or not expected_payload_version.strip():
        _fail("Expected snapshot payload version must be a non-empty string.")
    caller_environment = environment if environment is not None else os.environ
    if durable_home is not None and not _path_is_fully_qualified(durable_home):
        _fail("--durable-home must be absolute.")
    durable = canonical_path(
        durable_home
        or Path(caller_environment.get("HOME") or Path.home())
        / ".copilot-extensions"
    )
    context_path = Path(context)
    if not _path_is_fully_qualified(context_path):
        _fail("Runtime slot context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    cell_root = canonical_path(_string_property(validated, "cellRoot"))
    genesis_lock = _DirectoryLock(
        durable
        / "marketplaces"
        / ".locks"
        / f"{expected_marketplace_id}.genesis",
        kind="genesis",
        marketplace_id=expected_marketplace_id,
        timeout_seconds=RUNTIME_SLOT_COMPLETION_LOCK_TIMEOUT_SECONDS,
    )
    install_lock = _DirectoryLock(
        cell_root / ".locks" / f"{expected_plugin_id}.install.lock",
        kind="install",
        marketplace_id=expected_marketplace_id,
        plugin_id=expected_plugin_id,
        timeout_seconds=RUNTIME_SLOT_COMPLETION_LOCK_TIMEOUT_SECONDS,
    )
    with genesis_lock, install_lock:
        validated = validate_context_receipt(
            context_path,
            durable,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            expected_cell_root=cell_root,
            environment={},
        )
        slot_root, _, build_path, completion_path = _runtime_slot_completion_paths(
            validated,
            runtime_version,
        )
        if os.path.lexists(completion_path):
            snapshot = _validate_snapshot_provenance(
                context=context_path,
                expected_marketplace_id=expected_marketplace_id,
                expected_plugin_id=expected_plugin_id,
                snapshot_id=snapshot_id,
                expected_payload_root=expected_payload_root,
                expected_payload_version=expected_payload_version,
                durable_home=durable,
                environment={},
                require_current_receipts=False,
            )
            ownership = _validated_runtime_slot_ownership(
                validated,
                snapshot,
                runtime_version,
            )
            result = _validated_runtime_slot_completion(
                validated,
                snapshot,
                ownership,
                runtime_version,
            )
            created = False
        else:
            snapshot = _validate_snapshot_provenance(
                context=context_path,
                expected_marketplace_id=expected_marketplace_id,
                expected_plugin_id=expected_plugin_id,
                snapshot_id=snapshot_id,
                expected_payload_root=expected_payload_root,
                expected_payload_version=expected_payload_version,
                durable_home=durable,
                environment={},
                require_current_receipts=True,
            )
            ownership = _validated_runtime_slot_ownership(
                validated,
                snapshot,
                runtime_version,
            )
            snapshot_content_sha256 = _snapshot_content_sha256(
                Path(_string_property(snapshot, "snapshotRoot"))
            )
            build = _validated_build_completion(
                build_path,
                runtime_version,
                snapshot_content_sha256,
            )
            desired = _runtime_slot_completion_value(
                validated,
                snapshot,
                ownership,
                build,
                runtime_version,
                slot_root,
                snapshot_content_sha256,
            )
            confirmed_snapshot_content_sha256 = _snapshot_content_sha256(
                Path(_string_property(snapshot, "snapshotRoot"))
            )
            if confirmed_snapshot_content_sha256 != snapshot_content_sha256:
                _fail("Snapshot content changed before completion publication.")
            created = _publish_json_no_replace(
                completion_path,
                desired,
                locks=(genesis_lock, install_lock),
            )
            result = _validated_runtime_slot_completion(
                validated,
                snapshot,
                ownership,
                runtime_version,
            )
        result["action"] = "slot-complete"
        result["reason"] = (
            "runtime-slot-completion-published"
            if created
            else "runtime-slot-completion-current"
        )
        result["created"] = created
        return result


def _read_runtime_marker(path: Path, label: str) -> str | None:
    if not os.path.lexists(path):
        return None
    if _is_link_or_junction(path):
        _fail(f"{label} may not be a symbolic link or reparse point.")
    actual = canonical_path(path, must_exist=True)
    if not paths_equal(actual, path):
        _fail(f"{label} is not at its exact canonical location '{path}'.")
    try:
        content, _ = _read_regular_file(
            actual,
            label=label,
            require_stable_identity=True,
        )
        value = content.decode("utf-8")
    except (OSError, UnicodeError) as error:
        _fail(f"Cannot read {label.lower()} '{actual}': {error}")
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    if not value or "\n" in value or "\r" in value or value != value.strip():
        _fail(f"{label} must contain exactly one runtime version.")
    _assert_runtime_version(value)
    return value


@_validation_scope
def cutover_runtime_slot(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    expected_payload_root: str | os.PathLike[str],
    expected_payload_version: str,
    snapshot_id: str,
    runtime_version: str,
    expected_namespace_generation: int,
    expected_install_generation: int,
    expected_current_version: str | None = None,
    expect_current_absent: bool = False,
    durable_home: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """CAS-cut over one completed owned runtime slot without activating it."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    _assert_snapshot_id(snapshot_id)
    _assert_runtime_version(runtime_version)
    if not _path_is_fully_qualified(expected_payload_root):
        _fail("Expected snapshot payload root must be absolute.")
    if not isinstance(expected_payload_version, str) or not expected_payload_version.strip():
        _fail("Expected snapshot payload version must be a non-empty string.")
    if (expected_current_version is None) == (not expect_current_absent):
        _fail(
            "Specify exactly one of expected_current_version and "
            "expect_current_absent."
        )
    if expected_current_version is not None:
        _assert_runtime_version(expected_current_version)
    expected_generations = {
        "namespace": expected_namespace_generation,
        "install": expected_install_generation,
    }
    for name, value in expected_generations.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail(f"Expected {name} generation must be a non-negative integer.")
        if value > MAX_RECEIPT_GENERATION:
            _fail(
                f"Expected {name} generation exceeds the portable signed "
                "64-bit maximum."
            )

    caller_environment = environment if environment is not None else os.environ
    if durable_home is not None and not _path_is_fully_qualified(durable_home):
        _fail("--durable-home must be absolute.")
    durable = canonical_path(
        durable_home
        or Path(caller_environment.get("HOME") or Path.home())
        / ".copilot-extensions"
    )
    context_path = Path(context)
    if not _path_is_fully_qualified(context_path):
        _fail("Runtime slot context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    cell_root = canonical_path(_string_property(validated, "cellRoot"))
    install_path = canonical_path(_string_property(validated, "installReceipt"))
    genesis_lock = _DirectoryLock(
        durable
        / "marketplaces"
        / ".locks"
        / f"{expected_marketplace_id}.genesis",
        kind="genesis",
        marketplace_id=expected_marketplace_id,
        timeout_seconds=RUNTIME_SLOT_COMPLETION_LOCK_TIMEOUT_SECONDS,
    )
    install_lock = _DirectoryLock(
        cell_root / ".locks" / f"{expected_plugin_id}.install.lock",
        kind="install",
        marketplace_id=expected_marketplace_id,
        plugin_id=expected_plugin_id,
        timeout_seconds=RUNTIME_SLOT_COMPLETION_LOCK_TIMEOUT_SECONDS,
    )
    with genesis_lock, install_lock:
        validated = validate_context_receipt(
            install_path,
            durable,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            expected_cell_root=cell_root,
            environment={},
        )
        runtime_root = canonical_path(
            _string_property(validated, "versionsRoot")
        ).parent
        current_path = runtime_root / CURRENT_VERSION_FILE
        last_known_good_path = runtime_root / LAST_KNOWN_GOOD_FILE
        actual_namespace_generation = int(validated["namespaceGeneration"])
        actual_install_generation = int(validated["generation"])
        actual_current_version = _read_runtime_marker(
            current_path,
            "Current version marker",
        )
        actual_last_known_good = _read_runtime_marker(
            last_known_good_path,
            "Last-known-good marker",
        )
        actual_generations = {
            "namespace": actual_namespace_generation,
            "install": actual_install_generation,
        }
        if actual_generations != expected_generations:
            return {
                "action": "slot-cutover",
                "status": "revalidation-required",
                "reason": "generation-changed",
                "cutoverChanged": False,
                "runtimeVersion": runtime_version,
                "currentVersion": actual_current_version,
                "lastKnownGoodVersion": actual_last_known_good,
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "expectedNamespaceGeneration": expected_namespace_generation,
                "expectedInstallGeneration": expected_install_generation,
                "activated": False,
                "operative": False,
            }
        namespace_receipt = read_json(validated["namespaceReceipt"])
        if not isinstance(namespace_receipt, Mapping):
            _fail("namespace.json must be a JSON object.")
        if (
            _string_property(namespace_receipt, "state") != "active"
            or _string_property(validated, "state") != "active"
        ):
            _fail("Runtime slot cutover requires active namespace and install receipts.")
        current_matches = (
            actual_current_version is None
            if expect_current_absent
            else actual_current_version == expected_current_version
        )
        if not current_matches:
            return {
                "action": "slot-cutover",
                "status": "revalidation-required",
                "reason": "current-version-changed",
                "cutoverChanged": False,
                "runtimeVersion": runtime_version,
                "currentVersion": actual_current_version,
                "lastKnownGoodVersion": actual_last_known_good,
                "expectedCurrentVersion": expected_current_version,
                "expectedCurrentAbsent": expect_current_absent,
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "activated": False,
                "operative": False,
            }

        completion = validate_runtime_slot_completion(
            context=install_path,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            expected_payload_root=expected_payload_root,
            expected_payload_version=expected_payload_version,
            snapshot_id=snapshot_id,
            runtime_version=runtime_version,
            durable_home=durable,
            environment={},
        )
        confirmed_current_version = _read_runtime_marker(
            current_path,
            "Current version marker",
        )
        confirmed_last_known_good = _read_runtime_marker(
            last_known_good_path,
            "Last-known-good marker",
        )
        if (
            confirmed_current_version != actual_current_version
            or confirmed_last_known_good != actual_last_known_good
        ):
            return {
                "action": "slot-cutover",
                "status": "revalidation-required",
                "reason": "runtime-marker-changed",
                "cutoverChanged": False,
                "runtimeVersion": runtime_version,
                "currentVersion": confirmed_current_version,
                "lastKnownGoodVersion": confirmed_last_known_good,
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "activated": False,
                "operative": False,
            }

        desired_last_known_good = runtime_version
        locks = (genesis_lock, install_lock)
        changed = (
            actual_current_version != runtime_version
            or actual_last_known_good != desired_last_known_good
        )
        if actual_current_version != runtime_version:
            _atomic_write_text(current_path, runtime_version, lock=locks)
        if actual_last_known_good != desired_last_known_good:
            _atomic_write_text(
                last_known_good_path,
                desired_last_known_good,
                lock=locks,
            )
        published_current = _read_runtime_marker(
            current_path,
            "Current version marker",
        )
        published_last_known_good = _read_runtime_marker(
            last_known_good_path,
            "Last-known-good marker",
        )
        if (
            published_current != runtime_version
            or published_last_known_good != desired_last_known_good
        ):
            _fail(
                "Published runtime cutover markers did not validate as current: "
                f"current={published_current!r}, "
                f"last-known-good={published_last_known_good!r}, "
                f"expected={runtime_version!r}."
            )
        return {
            "action": "slot-cutover",
            "status": "ready",
            "reason": (
                "runtime-slot-cutover-published"
                if changed
                else "runtime-slot-cutover-current"
            ),
            "cutoverChanged": changed,
            "runtimeVersion": runtime_version,
            "previousVersion": actual_current_version,
            "currentVersion": published_current,
            "lastKnownGoodVersion": published_last_known_good,
            "currentMarker": str(current_path),
            "lastKnownGoodMarker": str(last_known_good_path),
            "completion": completion["completion"],
            "namespaceGeneration": actual_namespace_generation,
            "installGeneration": actual_install_generation,
            "activated": False,
            "operative": False,
        }


def _find_existing_source(
    durable_home: Path,
    fingerprint: str,
    desired_id: str,
    locator: Mapping[str, Any] | None,
) -> list[dict[str, Any]]:
    marketplaces = durable_home / "marketplaces"
    if not marketplaces.is_dir():
        return []
    results: list[dict[str, Any]] = []
    for cell_directory in sorted(path for path in marketplaces.iterdir() if path.is_dir()):
        receipt_path = cell_directory / "namespace.json"
        if not receipt_path.is_file():
            continue
        validated = validate_namespace_receipt(receipt_path, durable_home)
        receipt_fingerprint = validated["identity"]["fingerprint"]
        receipt_id = validated["marketplaceId"]
        if cell_directory.name == desired_id and receipt_fingerprint != fingerprint:
            _fail(
                f"Marketplace id '{desired_id}' is already occupied by a different "
                "full source fingerprint."
            )
        if receipt_fingerprint != fingerprint:
            continue
        locator_match = locator is None
        if locator is not None:
            receipt_locators = _property(validated["receipt"], "locators", [])
            locator_match = any(
                isinstance(known, Mapping) and _locator_matches(locator, known)
                for known in receipt_locators
            )
        results.append(
            {
                "marketplaceId": receipt_id,
                "namespaceReceipt": str(canonical_path(receipt_path)),
                "sameId": receipt_id == desired_id,
                "locatorMatch": locator_match,
            }
        )
    return results


@_validation_scope
def resolve_context(
    *,
    payload_root: str | os.PathLike[str] | None = None,
    plugin_id: str | None = None,
    copilot_home: str | os.PathLike[str] | None = None,
    project_root: str | os.PathLike[str] | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    context: str | os.PathLike[str] | None = None,
    expected_marketplace_id: str | None = None,
    expected_plugin_id: str | None = None,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_cell_root: str | os.PathLike[str] | None = None,
    source_descriptor: Mapping[str, Any] | None = None,
    marketplace_key: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve existing evidence into a non-operative installation context."""

    environment = environment if environment is not None else os.environ
    home = Path(environment.get("HOME") or Path.home())
    copilot_value = Path(copilot_home or home / ".copilot")
    durable_value = Path(durable_home or home / ".copilot-extensions")
    if not _path_is_fully_qualified(copilot_value) or not _path_is_fully_qualified(
        durable_value
    ):
        _fail("--copilot-home and --durable-home must be absolute.")
    copilot = canonical_path(copilot_value)
    durable = canonical_path(durable_value)
    project = (
        canonical_path(project_root, must_exist=True) if project_root is not None else None
    )
    pointer = context or environment.get("COPILOT_EXTENSIONS_CONTEXT")
    if pointer:
        payload_expectation = (
            expected_payload_root
            or payload_root
            or environment.get("COPILOT_PLUGIN_ROOT")
        )
        plugin_expectation = plugin_id or expected_plugin_id
        if not plugin_expectation:
            _fail("resolve with an explicit context requires an expected plugin id.")
        if (
            payload_expectation is None
            and expected_marketplace_id is None
            and expected_cell_root is None
        ):
            _fail(
                "resolve with an explicit context requires an expected payload, "
                "marketplace, or cell identity."
            )
        result = validate_context_receipt(
            pointer,
            durable,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=plugin_expectation,
            expected_payload_root=payload_expectation,
            expected_cell_root=expected_cell_root,
            environment=environment,
        )
        result["action"] = "resolve"
        return result

    payload_value = payload_root or environment.get("COPILOT_PLUGIN_ROOT")
    if payload_value is None:
        _fail("resolve requires --payload-root or COPILOT_PLUGIN_ROOT.")
    payload_input = Path(payload_value)
    if not _path_is_fully_qualified(payload_input):
        _fail("The payload root must be absolute.")
    payload = canonical_path(payload_input, must_exist=True)
    if not payload.is_dir():
        _fail(f"The payload root must be an existing directory: {payload}")
    inherited_payload = environment.get("COPILOT_PLUGIN_ROOT")
    if inherited_payload:
        if not _path_is_fully_qualified(inherited_payload):
            _fail("COPILOT_PLUGIN_ROOT must be absolute.")
        if not paths_equal(payload, inherited_payload):
            _fail("COPILOT_PLUGIN_ROOT conflicts with --payload-root.")

    if source_descriptor is not None:
        if not plugin_id:
            _fail("Explicit source resolution requires --plugin-id.")
        evidence = {
            "source": normalize_source(source_descriptor),
            "pluginId": plugin_id,
            "readableName": marketplace_key or "marketplace",
            "locator": None,
        }
    else:
        evidence = _resolve_installed_evidence(payload, copilot, project)
        if evidence is None:
            evidence = _resolve_directory_evidence(payload, plugin_id)
        if evidence is None:
            _fail(
                f"Cannot establish marketplace provenance for payload '{payload}'. "
                "Supply an explicit source descriptor for management/development mode."
            )
        if plugin_id and plugin_id != evidence["pluginId"]:
            _fail(
                f"Expected plugin '{plugin_id}', payload evidence identifies "
                f"'{evidence['pluginId']}'."
            )

    source = evidence["source"]
    identity = source_identity(source, evidence["readableName"])
    resolved_plugin_id = str(evidence["pluginId"])
    _assert_plugin_id(resolved_plugin_id)
    existing = _find_existing_source(
        durable,
        identity["fingerprint"],
        identity["marketplaceId"],
        evidence["locator"],
    )
    rebind = [
        entry
        for entry in existing
        if not entry["sameId"] or not entry["locatorMatch"]
    ]
    if rebind:
        owners = ", ".join(str(entry["marketplaceId"]) for entry in rebind)
        _fail(
            f"Source '{identity['fingerprint']}' already owns cell/locator '{owners}'; "
            "explicit rebind or new-cell intent is required."
        )
    cell_root = canonical_path(durable / "marketplaces" / identity["marketplaceId"])
    plugin_root_path = canonical_path(cell_root / "plugins" / resolved_plugin_id)
    return {
        "action": "resolve",
        "source": {
            "kind": identity["kind"],
            "canonical": identity["canonical"],
            "ref": identity["ref"],
            "record": identity["record"],
        },
        "sourceFingerprint": identity["fingerprint"],
        "marketplaceId": identity["marketplaceId"],
        "marketplaceSlot": identity["marketplaceId"],
        "pluginId": resolved_plugin_id,
        "payloadRoot": str(payload),
        "cellRoot": str(cell_root),
        "pluginRoot": str(plugin_root_path),
        "versionsRoot": str(canonical_path(plugin_root_path / "versions")),
        "snapshotsRoot": str(canonical_path(plugin_root_path / "snapshots")),
        "stateRoot": str(canonical_path(plugin_root_path / "state")),
        "runRoot": str(canonical_path(plugin_root_path / "run")),
        "logsRoot": str(canonical_path(plugin_root_path / "logs")),
        "cacheRoot": str(canonical_path(plugin_root_path / "cache")),
        "launchersRoot": str(canonical_path(plugin_root_path / "launchers")),
        "reposRoot": str(canonical_path(cell_root / "repos")),
        "namespaceReceipt": str(canonical_path(cell_root / "namespace.json")),
        "installReceipt": str(canonical_path(plugin_root_path / "install.json")),
        "locator": evidence["locator"],
        "existingCells": existing,
        "rebindRequired": False,
        "operative": False,
    }


_MISSING = object()


def _exact_property(value: Mapping[str, Any], name: str) -> Any:
    if not isinstance(value, Mapping):
        _fail(f"{name} belongs to a JSON object.")
    for candidate in value:
        if not isinstance(candidate, str):
            _fail("JSON object property names must be strings.")
        if candidate != name and candidate.casefold() == name.casefold():
            _fail(f"JSON property '{candidate}' conflicts with exact case '{name}'.")
    return value[name] if name in value else _MISSING


def _required_string(value: Mapping[str, Any], name: str, label: str) -> str:
    result = _exact_property(value, name)
    if result is _MISSING or not isinstance(result, str) or not result:
        _fail(f"{label}.{name} must be a non-empty string.")
    if "\0" in result:
        _fail(f"{label}.{name} may not contain NUL.")
    return result


def _required_integer(value: Mapping[str, Any], name: str, label: str) -> int:
    result = _exact_property(value, name)
    if isinstance(result, bool) or not isinstance(result, int):
        _fail(f"{label}.{name} must be an integer.")
    if result < 1 or result > MAX_RECEIPT_GENERATION:
        _fail(
            f"{label}.{name} must be a positive signed 64-bit integer."
        )
    return result


def _parse_rfc3339_utc(value: Any, label: str) -> datetime:
    if not isinstance(value, str) or RFC3339_UTC.fullmatch(value) is None:
        _fail(f"{label} must be an RFC3339 UTC timestamp with second precision.")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        _fail(f"{label} is not a valid RFC3339 UTC timestamp: {error}")


def _coerce_current_time(value: datetime | str | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        return _parse_rfc3339_utc(value, "current time")
    if not isinstance(value, datetime):
        _fail("current_time must be a datetime or RFC3339 UTC string.")
    if value.tzinfo is None:
        _fail("current_time must include a UTC offset.")
    return value.astimezone(timezone.utc)


def _normalize_short_host(value: str) -> str:
    return value.split(".", 1)[0].casefold()


def _current_environment(
    *,
    environment: Mapping[str, str],
    os_profile: str | os.PathLike[str] | None,
    platform: str | None,
    wsl_distro: str | None,
) -> tuple[dict[str, Any], Path]:
    selected_platform = platform or ("windows" if os.name == "nt" else "posix")
    if selected_platform not in {"windows", "posix"}:
        _fail("platform must be exactly 'windows' or 'posix'.")
    if os_profile is not None:
        profile_value = Path(os_profile)
    elif selected_platform == "windows":
        user_profile = environment.get("USERPROFILE")
        if not user_profile:
            _fail("Cannot determine the canonical Windows user profile.")
        profile_value = Path(user_profile)
    else:
        try:
            import pwd

            profile_value = Path(pwd.getpwuid(os.getuid()).pw_dir)
        except (ImportError, KeyError, OSError) as error:
            _fail(f"Cannot determine the passwd-database account home: {error}")
    if not _path_is_fully_qualified(profile_value):
        _fail("The canonical operating-system user profile must be absolute.")
    profile = canonical_path(profile_value, must_exist=True)
    selected_wsl = (
        None
        if selected_platform == "windows"
        else (
            wsl_distro
            if wsl_distro is not None
            else environment.get("WSL_DISTRO_NAME") or None
        )
    )
    if selected_wsl is not None and not isinstance(selected_wsl, str):
        _fail("wslDistro must be a string or null.")
    return (
        {
            "platform": selected_platform,
            "homeRealPath": str(profile),
            "wslDistro": selected_wsl,
        },
        profile,
    )


def _validate_environment_record(
    value: Any,
    current: Mapping[str, Any],
    label: str,
) -> tuple[dict[str, Any], bool]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be a JSON object.")
    platform = _required_string(value, "platform", label)
    if platform not in {"windows", "posix"}:
        _fail(f"{label}.platform must be exactly 'windows' or 'posix'.")
    home = _required_string(value, "homeRealPath", label)
    if platform == "windows":
        absolute = bool(
            re.match(r"^[A-Za-z]:[\\/]", home)
            or re.match(r"^\\\\[^\\/]+[\\/][^\\/]+(?:[\\/]|$)", home)
        )
    else:
        absolute = PurePosixPath(home).is_absolute()
    if not absolute:
        _fail(f"{label}.homeRealPath must be absolute.")
    distro_value = _exact_property(value, "wslDistro")
    if distro_value is _MISSING:
        _fail(f"{label}.wslDistro is required.")
    if distro_value is not None and not isinstance(distro_value, str):
        _fail(f"{label}.wslDistro must be a string or null.")
    if platform == "windows" and distro_value is not None:
        _fail(f"{label}.wslDistro must be null on Windows.")
    normalized = {
        "platform": platform,
        "homeRealPath": home,
        "wslDistro": distro_value,
    }
    return normalized, normalized != dict(current)


def _validate_legacy_probe(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be a JSON object.")
    declared = _exact_property(value, "declared")
    if not isinstance(declared, bool):
        _fail(f"{label}.declared must be a JSON boolean.")
    result = _exact_property(value, "result")
    if result not in {"absent", "present", "unknown"}:
        _fail(f"{label}.result must be absent, present, or unknown.")
    if not declared and result != "unknown":
        _fail(f"{label}.result must be unknown when declared is false.")
    checked_at = _exact_property(value, "checkedAt")
    if checked_at is _MISSING:
        _fail(f"{label}.checkedAt is required.")
    if checked_at is not None:
        _parse_rfc3339_utc(checked_at, f"{label}.checkedAt")
    return {
        "declared": declared,
        "result": result,
        "checkedAt": checked_at,
    }


def _validate_marketplace_id(value: str) -> None:
    if re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*--[0-9a-f]{16}", value) is None:
        _fail(f"Invalid source-derived marketplace id '{value}'.")


def _policy_result(
    path: Path,
    *,
    authoritative: bool,
    marketplace_id: str | None,
    plugin_id: str | None,
    entry_present: bool | None = None,
    entry_is_file: bool | None = None,
) -> tuple[dict[str, Any], bool | None]:
    evidence: dict[str, Any] = {
        "path": str(path),
        "authoritative": authoritative,
        "state": "missing",
        "scope": "default",
        "enabled": False,
        "reason": "policy-default-false",
    }
    present = os.path.lexists(path) if entry_present is None else entry_present
    is_file = path.is_file() if entry_is_file is None else entry_is_file
    if not present:
        if not authoritative:
            evidence["reason"] = "policy-injected-non-authoritative"
        return evidence, False
    if not is_file:
        evidence.update(
            state="invalid",
            enabled=None,
            reason="policy-invalid",
        )
        return evidence, None
    try:
        policy = read_json(path)
        if not isinstance(policy, Mapping):
            _fail("Installation-mode policy must be a JSON object.")
        schema = _exact_property(policy, "schema")
        version = _exact_property(policy, "version")
        if schema != POLICY_SCHEMA:
            _fail(f"Installation-mode policy schema must be '{POLICY_SCHEMA}'.")
        if isinstance(version, bool) or not isinstance(version, int):
            _fail("Installation-mode policy version must be an integer.")
        if version > 1:
            evidence.update(
                state="unsupported",
                enabled=None,
                reason="policy-version-unsupported",
            )
            return evidence, None
        if version != 1:
            _fail("Installation-mode policy version must be 1.")
        installation_mode = _exact_property(policy, "installationMode")
        if installation_mode is _MISSING:
            installation_mode = {}
        if not isinstance(installation_mode, Mapping):
            _fail("installationMode must be a JSON object.")

        global_enabled = _exact_property(installation_mode, "enabled")
        if global_enabled is not _MISSING and not isinstance(global_enabled, bool):
            _fail("installationMode.enabled must be a JSON boolean.")
        marketplaces = _exact_property(installation_mode, "marketplaces")
        if marketplaces is _MISSING:
            marketplaces = {}
        if not isinstance(marketplaces, Mapping):
            _fail("installationMode.marketplaces must be a JSON object.")

        for candidate_marketplace, marketplace_value in marketplaces.items():
            _validate_marketplace_id(candidate_marketplace)
            if not isinstance(marketplace_value, Mapping):
                _fail(
                    f"Marketplace policy '{candidate_marketplace}' must be a JSON object."
                )
            marketplace_enabled = _exact_property(marketplace_value, "enabled")
            if marketplace_enabled is not _MISSING and not isinstance(
                marketplace_enabled, bool
            ):
                _fail(
                    f"Marketplace policy '{candidate_marketplace}'.enabled must be "
                    "a JSON boolean."
                )
            plugins = _exact_property(marketplace_value, "plugins")
            if plugins is _MISSING:
                plugins = {}
            if not isinstance(plugins, Mapping):
                _fail(
                    f"Marketplace policy '{candidate_marketplace}'.plugins must be "
                    "a JSON object."
                )
            for candidate_plugin, plugin_value in plugins.items():
                _assert_plugin_id(candidate_plugin)
                if not isinstance(plugin_value, Mapping):
                    _fail(
                        f"Plugin policy '{candidate_plugin}' must be a JSON object."
                    )
                plugin_enabled = _exact_property(plugin_value, "enabled")
                if plugin_enabled is not _MISSING and not isinstance(
                    plugin_enabled, bool
                ):
                    _fail(
                        f"Plugin policy '{candidate_plugin}'.enabled must be a "
                        "JSON boolean."
                    )

        scope = "default"
        enabled = False
        reason = "policy-default-false"
        if global_enabled is not _MISSING:
            scope = "global"
            enabled = global_enabled
            reason = f"policy-global-{'true' if enabled else 'false'}"
        if marketplace_id is not None and marketplace_id in marketplaces:
            selected_marketplace = marketplaces[marketplace_id]
            selected_marketplace_enabled = _exact_property(
                selected_marketplace, "enabled"
            )
            if selected_marketplace_enabled is not _MISSING:
                scope = "marketplace"
                enabled = selected_marketplace_enabled
                reason = f"policy-marketplace-{'true' if enabled else 'false'}"
            selected_plugins = _exact_property(selected_marketplace, "plugins")
            if selected_plugins is _MISSING:
                selected_plugins = {}
            if plugin_id is not None and plugin_id in selected_plugins:
                selected_plugin_enabled = _exact_property(
                    selected_plugins[plugin_id], "enabled"
                )
                if selected_plugin_enabled is not _MISSING:
                    scope = "plugin"
                    enabled = selected_plugin_enabled
                    reason = f"policy-plugin-{'true' if enabled else 'false'}"

        evidence.update(
            state="valid",
            scope=scope,
            enabled=enabled,
            reason=(
                reason if authoritative else "policy-injected-non-authoritative"
            ),
        )
        return evidence, enabled if authoritative else False
    except InstallationContextError:
        evidence.update(
            state="invalid",
            enabled=None,
            reason="policy-invalid",
        )
        return evidence, None


def _activation_result(
    *,
    plugin_root: Path,
    durable_home: Path,
    marketplace_id: str,
    plugin_id: str,
    current_environment: Mapping[str, Any],
    legacy_root: Path,
) -> dict[str, Any]:
    activation_entry = plugin_root / "installation-activation.json"
    activation_present = os.path.lexists(activation_entry)
    activation_is_file = activation_entry.is_file() and not activation_entry.is_symlink()
    activation_path = canonical_path(activation_entry)
    missing = {
        "state": "missing",
        "path": None,
        "actualMode": "legacy",
        "runtimeRoot": str(legacy_root),
        "context": None,
        "activationGeneration": None,
        "installGeneration": None,
        "reason": None,
    }
    if not activation_present:
        return missing
    result = dict(missing)
    result["path"] = str(activation_path)
    if not activation_is_file:
        result.update(
            state="invalid",
            actualMode=None,
            runtimeRoot=None,
            reason="activation-invalid",
        )
        return result
    try:
        activation = read_json(activation_path)
        if not isinstance(activation, Mapping):
            _fail("Installation activation must be a JSON object.")
        if _exact_property(activation, "schema") != ACTIVATION_SCHEMA:
            _fail(f"Installation activation schema must be '{ACTIVATION_SCHEMA}'.")
        version = _exact_property(activation, "version")
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            _fail("Installation activation version must be 1.")
        if _required_string(
            activation, "marketplaceId", "installation activation"
        ) != marketplace_id:
            _fail("Installation activation marketplaceId does not match its cell.")
        if _required_string(
            activation, "pluginId", "installation activation"
        ) != plugin_id:
            _fail("Installation activation pluginId does not match its plugin root.")
        mode = _required_string(activation, "mode", "installation activation")
        state = _required_string(activation, "state", "installation activation")
        if (mode, state) not in {
            ("namespaced", "active"),
            ("legacy", "deactivated"),
        }:
            _fail("Installation activation mode/state pair is invalid.")
        _, foreign = _validate_environment_record(
            _exact_property(activation, "environment"),
            current_environment,
            "installation activation.environment",
        )
        if foreign:
            result.update(
                state="foreign",
                actualMode=None,
                runtimeRoot=None,
                reason="foreign-environment",
            )
            return result
        context_text = _required_string(
            activation, "context", "installation activation"
        )
        context_path = Path(context_text)
        if not _path_is_fully_qualified(context_path):
            _fail("Installation activation context must be absolute.")
        canonical_context = canonical_path(plugin_root / "install.json")
        if not paths_equal(context_path, canonical_context):
            _fail("Installation activation context is not the canonical install.json.")
        namespace_generation = _required_integer(
            activation, "namespaceGeneration", "installation activation"
        )
        pinned_install_generation = _required_integer(
            activation, "installGeneration", "installation activation"
        )
        activation_generation = _required_integer(
            activation, "generation", "installation activation"
        )
        legacy = _exact_property(activation, "legacy")
        if not isinstance(legacy, Mapping):
            _fail("Installation activation legacy evidence must be a JSON object.")
        disposition = _required_string(
            legacy, "disposition", "installation activation.legacy"
        )
        if disposition not in {"absent", "quiesced", "retained-inert", "restored"}:
            _fail("Installation activation legacy disposition is invalid.")
        _validate_legacy_probe(
            _exact_property(legacy, "probe"),
            "installation activation.legacy.probe",
        )
        created_at = _parse_rfc3339_utc(
            _exact_property(activation, "createdAt"),
            "installation activation.createdAt",
        )
        updated_at = _parse_rfc3339_utc(
            _exact_property(activation, "updatedAt"),
            "installation activation.updatedAt",
        )
        if updated_at < created_at:
            _fail("Installation activation updatedAt precedes createdAt.")
        validated = validate_context_receipt(
            canonical_context,
            durable_home,
            expected_marketplace_id=marketplace_id,
            expected_plugin_id=plugin_id,
            expected_cell_root=plugin_root.parent.parent,
            environment={},
        )
        current_namespace_generation = int(validated["namespaceGeneration"])
        current_install_generation = int(validated["generation"])
        actual_mode = "namespaced" if mode == "namespaced" else "legacy"
        runtime_root = plugin_root if actual_mode == "namespaced" else legacy_root
        result.update(
            actualMode=actual_mode,
            runtimeRoot=str(runtime_root),
            context=str(canonical_context),
            activationGeneration=activation_generation,
            installGeneration=current_install_generation,
        )
        if (
            namespace_generation != current_namespace_generation
            or pinned_install_generation != current_install_generation
        ):
            result.update(state="revalidation", reason="revalidation-required")
            return result
        result.update(state="valid", reason=None)
        return result
    except InstallationContextError:
        result.update(
            state="invalid",
            actualMode=None,
            runtimeRoot=None,
            context=None,
            activationGeneration=None,
            installGeneration=None,
            reason="activation-invalid",
        )
        return result


@_validation_scope
def compare_and_swap_activation(
    *,
    context: str | os.PathLike[str],
    expected_marketplace_id: str,
    expected_plugin_id: str,
    expected_namespace_generation: int,
    expected_install_generation: int,
    expected_activation_generation: int,
    activation_mode: str,
    activation_state: str,
    legacy_disposition: str,
    legacy_probe: Mapping[str, Any],
    durable_home: str | os.PathLike[str] | None = None,
    legacy_root: str | os.PathLike[str] | None = None,
    environment: Mapping[str, str] | None = None,
    os_profile: str | os.PathLike[str] | None = None,
    platform: str | None = None,
    wsl_distro: str | None = None,
) -> dict[str, Any]:
    """Atomically publish a generation-pinned activation receipt."""

    _validate_marketplace_id(expected_marketplace_id)
    _assert_plugin_id(expected_plugin_id)
    if (activation_mode, activation_state) not in {
        ("namespaced", "active"),
        ("legacy", "deactivated"),
    }:
        _fail("Activation mode/state pair is invalid.")
    if legacy_disposition not in {
        "absent",
        "quiesced",
        "retained-inert",
        "restored",
    }:
        _fail("Activation legacy disposition is invalid.")
    recorded_probe = _validate_legacy_probe(
        legacy_probe,
        "activation legacy probe",
    )
    expected_generations = {
        "namespace": expected_namespace_generation,
        "install": expected_install_generation,
        "activation": expected_activation_generation,
    }
    for name, value in expected_generations.items():
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            _fail(f"Expected {name} generation must be a non-negative integer.")

    caller_environment = environment if environment is not None else os.environ
    current_environment, _ = _current_environment(
        environment=caller_environment,
        os_profile=os_profile,
        platform=platform,
        wsl_distro=wsl_distro,
    )
    durable = canonical_path(
        durable_home
        or Path(caller_environment.get("HOME") or Path.home())
        / ".copilot-extensions"
    )
    context_path = Path(context)
    if not _path_is_fully_qualified(context_path):
        _fail("Activation context must be absolute.")
    validated = validate_context_receipt(
        context_path,
        durable,
        expected_marketplace_id=expected_marketplace_id,
        expected_plugin_id=expected_plugin_id,
        environment={},
    )
    marketplace_id = _string_property(validated, "marketplaceId")
    plugin_id = _string_property(validated, "pluginId")
    cell_root = canonical_path(_string_property(validated, "cellRoot"))
    plugin_root = canonical_path(_string_property(validated, "pluginRoot"))
    install_path = canonical_path(_string_property(validated, "installReceipt"))
    activation_path = plugin_root / "installation-activation.json"
    legacy_root_value = legacy_root or (
        Path(current_environment["homeRealPath"]) / f".{plugin_id}"
    )
    if not _path_is_fully_qualified(legacy_root_value):
        _fail("Activation legacy root must be absolute.")
    resolved_legacy_root = canonical_path(legacy_root_value)

    genesis_lock = _DirectoryLock(
        durable / "marketplaces" / ".locks" / f"{marketplace_id}.genesis",
        kind="genesis",
        marketplace_id=marketplace_id,
    )
    install_lock = _DirectoryLock(
        cell_root / ".locks" / f"{plugin_id}.install.lock",
        kind="install",
        marketplace_id=marketplace_id,
        plugin_id=plugin_id,
    )
    with genesis_lock, install_lock:
        validated = validate_context_receipt(
            install_path,
            durable,
            expected_marketplace_id=marketplace_id,
            expected_plugin_id=plugin_id,
            expected_cell_root=cell_root,
            environment={},
        )
        actual_namespace_generation = int(validated["namespaceGeneration"])
        actual_install_generation = int(validated["generation"])
        activation = _activation_result(
            plugin_root=plugin_root,
            durable_home=durable,
            marketplace_id=marketplace_id,
            plugin_id=plugin_id,
            current_environment=current_environment,
            legacy_root=resolved_legacy_root,
        )
        existing: Mapping[str, Any] | None = None
        if activation["state"] == "missing":
            actual_activation_generation = 0
        elif activation["state"] == "foreign":
            _fail("Existing activation receipt belongs to a foreign environment.")
        elif activation["state"] == "invalid":
            _fail("Existing activation receipt is invalid.")
        else:
            actual_activation_generation = int(activation["activationGeneration"])
            loaded = read_json(activation_path)
            if not isinstance(loaded, Mapping):
                _fail("Existing activation receipt must be a JSON object.")
            existing = loaded

        actual_generations = {
            "namespace": actual_namespace_generation,
            "install": actual_install_generation,
            "activation": actual_activation_generation,
        }
        if actual_generations != expected_generations:
            return {
                "action": "activation-cas",
                "status": "revalidation-required",
                "reason": "generation-changed",
                "activation": (
                    str(canonical_path(activation_path))
                    if os.path.lexists(activation_path)
                    else None
                ),
                "activationChanged": False,
                "activationGeneration": actual_activation_generation,
                "namespaceGeneration": actual_namespace_generation,
                "installGeneration": actual_install_generation,
                "expectedActivationGeneration": expected_activation_generation,
                "expectedNamespaceGeneration": expected_namespace_generation,
                "expectedInstallGeneration": expected_install_generation,
                "operative": False,
            }

        namespace_receipt = read_json(validated["namespaceReceipt"])
        if not isinstance(namespace_receipt, Mapping):
            _fail("namespace.json must be a JSON object.")
        if (
            _string_property(namespace_receipt, "state") != "active"
            or _string_property(validated, "state") != "active"
        ):
            _fail("Activation requires active namespace and install receipts.")

        if actual_activation_generation >= MAX_RECEIPT_GENERATION:
            _fail(
                "installation-activation.json generation cannot be incremented; "
                "explicit repair is required."
            )
        next_generation = actual_activation_generation + 1
        now = _utc_now()
        desired: dict[str, Any] = {
            "schema": ACTIVATION_SCHEMA,
            "version": 1,
            "marketplaceId": marketplace_id,
            "pluginId": plugin_id,
            "mode": activation_mode,
            "state": activation_state,
            "environment": current_environment,
            "context": str(install_path),
            "namespaceGeneration": actual_namespace_generation,
            "installGeneration": actual_install_generation,
            "generation": next_generation,
            "legacy": {
                "disposition": legacy_disposition,
                "probe": recorded_probe,
            },
            "createdAt": (
                _exact_property(existing, "createdAt")
                if existing is not None
                else now
            ),
            "updatedAt": now,
        }
        _atomic_write_json(
            activation_path,
            desired,
            lock=(genesis_lock, install_lock),
        )

        published = _activation_result(
            plugin_root=plugin_root,
            durable_home=durable,
            marketplace_id=marketplace_id,
            plugin_id=plugin_id,
            current_environment=current_environment,
            legacy_root=resolved_legacy_root,
        )
        if published["state"] != "valid":
            _fail("Published activation receipt did not validate as current.")
        return {
            "action": "activation-cas",
            "status": "ready",
            "reason": "activation-published",
            "activation": published["path"],
            "activationChanged": True,
            "activationGeneration": published["activationGeneration"],
            "namespaceGeneration": actual_namespace_generation,
            "installGeneration": actual_install_generation,
            "environment": current_environment,
            "mode": activation_mode,
            "state": activation_state,
            "context": str(install_path),
            "operative": False,
        }


def _tombstone_result(
    *,
    legacy_root: Path,
    durable_home: Path,
    plugin_id: str | None,
    current_marketplace_id: str | None,
    current_environment: Mapping[str, Any],
) -> dict[str, Any]:
    tombstone_entry = legacy_root / ".installation-ownership.json"
    tombstone_present = os.path.lexists(tombstone_entry)
    tombstone_is_file = tombstone_entry.is_file() and not tombstone_entry.is_symlink()
    tombstone_path = canonical_path(tombstone_entry)
    result: dict[str, Any] = {
        "root": str(legacy_root),
        "tombstone": None,
        "disposition": "active",
        "ownerMarketplaceId": None,
        "status": None,
        "reason": None,
    }
    if not tombstone_present:
        return result
    result["tombstone"] = str(tombstone_path)
    if not tombstone_is_file:
        result.update(
            disposition="orphaned-transfer",
            status="orphaned-transfer",
            reason="orphaned-transfer",
        )
        return result
    try:
        tombstone = read_json(tombstone_path)
        if not isinstance(tombstone, Mapping):
            _fail("Legacy ownership tombstone must be a JSON object.")
        if _exact_property(tombstone, "schema") != TOMBSTONE_SCHEMA:
            _fail(f"Legacy ownership schema must be '{TOMBSTONE_SCHEMA}'.")
        version = _exact_property(tombstone, "version")
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            _fail("Legacy ownership version must be 1.")
        owner_marketplace_id = _required_string(
            tombstone, "marketplaceId", "legacy ownership"
        )
        _validate_marketplace_id(owner_marketplace_id)
        owner_plugin_id = _required_string(
            tombstone, "pluginId", "legacy ownership"
        )
        _assert_plugin_id(owner_plugin_id)
        if plugin_id is None or owner_plugin_id != plugin_id:
            _fail("Legacy ownership pluginId does not match the requested plugin.")
        _, foreign = _validate_environment_record(
            _exact_property(tombstone, "environment"),
            current_environment,
            "legacy ownership.environment",
        )
        if foreign:
            result.update(
                disposition="orphaned-transfer",
                ownerMarketplaceId=owner_marketplace_id,
                status="foreign-environment",
                reason="foreign-environment",
            )
            return result
        activation_reference = _exact_property(tombstone, "activation")
        if not isinstance(activation_reference, Mapping):
            _fail("Legacy ownership activation must be a JSON object.")
        activation_text = _required_string(
            activation_reference, "path", "legacy ownership.activation"
        )
        activation_pointer = Path(activation_text)
        if not _path_is_fully_qualified(activation_pointer):
            _fail("Legacy ownership activation.path must be absolute.")
        pinned_generation = _required_integer(
            activation_reference, "generation", "legacy ownership.activation"
        )
        _parse_rfc3339_utc(
            _exact_property(tombstone, "transferredAt"),
            "legacy ownership.transferredAt",
        )
        destination_plugin_root = canonical_path(
            durable_home
            / "marketplaces"
            / owner_marketplace_id
            / "plugins"
            / owner_plugin_id
        )
        canonical_activation = canonical_path(
            destination_plugin_root / "installation-activation.json"
        )
        if not paths_equal(activation_pointer, canonical_activation):
            _fail("Legacy ownership activation.path is not canonical.")
        destination = _activation_result(
            plugin_root=destination_plugin_root,
            durable_home=durable_home,
            marketplace_id=owner_marketplace_id,
            plugin_id=owner_plugin_id,
            current_environment=current_environment,
            legacy_root=legacy_root,
        )
        if (
            destination["state"] != "valid"
            or destination["actualMode"] != "namespaced"
            or destination["activationGeneration"] != pinned_generation
        ):
            _fail("Legacy ownership destination activation is not current and active.")
        disposition = (
            "owned-by-current-cell"
            if owner_marketplace_id == current_marketplace_id
            else "owned-by-other-cell"
        )
        result.update(
            disposition=disposition,
            ownerMarketplaceId=owner_marketplace_id,
        )
        return result
    except InstallationContextError:
        result.update(
            disposition="orphaned-transfer",
            status="orphaned-transfer",
            reason="orphaned-transfer",
        )
        return result


def _maintenance_result(
    *,
    profile: Path,
    plugin_root: Path | None,
    current_time: datetime,
    host: str,
    pid_is_live: Callable[[int], bool],
) -> dict[str, Any]:
    candidates = [
        ("user", profile / ".copilot-extensions" / "maintenance")
    ]
    if plugin_root is not None:
        candidates.append(("plugin", plugin_root / "maintenance"))
    for scope, marker_entry in candidates:
        if not os.path.lexists(marker_entry):
            continue
        marker = marker_entry.absolute()
        sidecar_entry = marker_entry.with_name(f"{marker_entry.name}.json")
        sidecar = sidecar_entry.absolute()
        state = "stale"
        if (
            not marker_entry.is_symlink()
            and sidecar_entry.is_file()
            and not sidecar_entry.is_symlink()
        ):
            try:
                value = read_json(sidecar_entry)
                if not isinstance(value, Mapping):
                    _fail("Maintenance sidecar must be a JSON object.")
                owner = _required_string(value, "owner", "maintenance")
                sidecar_host = _required_string(value, "host", "maintenance")
                pid = _required_integer(value, "pid", "maintenance")
                reason = _required_string(value, "reason", "maintenance")
                entered_at = _parse_rfc3339_utc(
                    _exact_property(value, "enteredAt"), "maintenance.enteredAt"
                )
                expected_until = _parse_rfc3339_utc(
                    _exact_property(value, "expectedUntil"),
                    "maintenance.expectedUntil",
                )
                if (
                    owner
                    and reason
                    and _normalize_short_host(sidecar_host) == _normalize_short_host(host)
                    and entered_at <= current_time <= expected_until
                    and pid_is_live(pid)
                ):
                    state = "active"
            except (InstallationContextError, OSError):
                state = "stale"
        return {
            "state": state,
            "scope": scope,
            "marker": str(marker),
            "sidecar": str(sidecar),
        }
    return {
        "state": "inactive",
        "scope": "none",
        "marker": None,
        "sidecar": None,
    }


def _trusted_plugin_id(
    *,
    plugin_id: str | None,
    expected_plugin_id: str | None,
    payload_root: str | os.PathLike[str] | None,
    context: str | os.PathLike[str] | None,
) -> str | None:
    candidates: list[str] = []
    if plugin_id:
        candidates.append(plugin_id)
    if expected_plugin_id:
        candidates.append(expected_plugin_id)
    if payload_root:
        candidates.append(Path(payload_root).name)
    if context:
        pointer = Path(context)
        if pointer.name == "install.json" and pointer.parent.parent.name == "plugins":
            candidates.append(pointer.parent.name)
    for candidate in candidates:
        try:
            _assert_plugin_id(candidate)
            return candidate
        except InstallationContextError:
            continue
    return None


@_validation_scope
def resolve_installation_mode(
    *,
    legacy_root: str | os.PathLike[str],
    legacy_probe: Mapping[str, Any] | None = None,
    policy_path: str | os.PathLike[str] | None = None,
    payload_root: str | os.PathLike[str] | None = None,
    plugin_id: str | None = None,
    copilot_home: str | os.PathLike[str] | None = None,
    project_root: str | os.PathLike[str] | None = None,
    durable_home: str | os.PathLike[str] | None = None,
    context: str | os.PathLike[str] | None = None,
    expected_marketplace_id: str | None = None,
    expected_plugin_id: str | None = None,
    expected_payload_root: str | os.PathLike[str] | None = None,
    expected_cell_root: str | os.PathLike[str] | None = None,
    source_descriptor: Mapping[str, Any] | None = None,
    marketplace_key: str | None = None,
    environment: Mapping[str, str] | None = None,
    os_profile: str | os.PathLike[str] | None = None,
    platform: str | None = None,
    wsl_distro: str | None = None,
    current_time: datetime | str | None = None,
    host: str | None = None,
    pid_is_live: Callable[[int], bool] | None = None,
) -> dict[str, Any]:
    """Resolve desired and actual installation mode without mutating state."""

    environment = environment if environment is not None else os.environ
    current_environment, profile = _current_environment(
        environment=environment,
        os_profile=os_profile,
        platform=platform,
        wsl_distro=wsl_distro,
    )
    now = _coerce_current_time(current_time)
    current_host = host or socket.gethostname()
    liveness = pid_is_live or _pid_is_live
    legacy_value = Path(legacy_root)
    if not _path_is_fully_qualified(legacy_value):
        _fail("legacy_root must be absolute.")
    resolved_legacy_root = canonical_path(legacy_value)
    probe = _validate_legacy_probe(
        legacy_probe
        if legacy_probe is not None
        else {"declared": False, "result": "unknown", "checkedAt": None},
        "legacy probe",
    )
    resolved_durable_home = canonical_path(
        durable_home or profile / ".copilot-extensions"
    )
    trusted_plugin_id = _trusted_plugin_id(
        plugin_id=plugin_id,
        expected_plugin_id=expected_plugin_id,
        payload_root=payload_root,
        context=context,
    )
    resolved_context: dict[str, Any] | None = None
    identity_reason: str | None = None
    try:
        resolved_context = resolve_context(
            payload_root=payload_root,
            plugin_id=plugin_id,
            copilot_home=copilot_home,
            project_root=project_root,
            durable_home=resolved_durable_home,
            context=context,
            expected_marketplace_id=expected_marketplace_id,
            expected_plugin_id=expected_plugin_id,
            expected_payload_root=expected_payload_root,
            expected_cell_root=expected_cell_root,
            source_descriptor=source_descriptor,
            marketplace_key=marketplace_key,
            environment=environment,
        )
        trusted_plugin_id = str(resolved_context["pluginId"])
    except InstallationContextError:
        identity_reason = (
            "context-invalid"
            if context or environment.get("COPILOT_EXTENSIONS_CONTEXT")
            else "provenance-blocked"
        )

    marketplace_id = (
        str(resolved_context["marketplaceId"]) if resolved_context is not None else None
    )
    plugin_root = (
        canonical_path(resolved_context["pluginRoot"])
        if resolved_context is not None
        else None
    )
    canonical_policy_entry = (
        profile / ".copilot-extensions" / "installation-mode.json"
    )
    canonical_policy = canonical_path(canonical_policy_entry)
    if policy_path is not None:
        policy_value = Path(policy_path)
        if not _path_is_fully_qualified(policy_value):
            _fail("policy_path must be absolute.")
        policy_entry_present = os.path.lexists(policy_value)
        policy_entry_is_file = policy_value.is_file() and not policy_value.is_symlink()
        selected_policy = canonical_path(policy_value)
        policy_authoritative = False
    else:
        policy_entry_present = os.path.lexists(canonical_policy_entry)
        policy_entry_is_file = (
            canonical_policy_entry.is_file()
            and not canonical_policy_entry.is_symlink()
        )
        selected_policy = canonical_policy
        policy_authoritative = True
    policy, policy_enabled = _policy_result(
        selected_policy,
        authoritative=policy_authoritative,
        marketplace_id=marketplace_id,
        plugin_id=trusted_plugin_id,
        entry_present=policy_entry_present,
        entry_is_file=policy_entry_is_file,
    )

    activation = (
        _activation_result(
            plugin_root=plugin_root,
            durable_home=resolved_durable_home,
            marketplace_id=marketplace_id,
            plugin_id=trusted_plugin_id,
            current_environment=current_environment,
            legacy_root=resolved_legacy_root,
        )
        if plugin_root is not None
        and marketplace_id is not None
        and trusted_plugin_id is not None
        else {
            "state": "missing",
            "path": None,
            "actualMode": None,
            "runtimeRoot": None,
            "context": None,
            "activationGeneration": None,
            "installGeneration": None,
            "reason": None,
        }
    )
    legacy = _tombstone_result(
        legacy_root=resolved_legacy_root,
        durable_home=resolved_durable_home,
        plugin_id=trusted_plugin_id,
        current_marketplace_id=marketplace_id,
        current_environment=current_environment,
    )
    legacy["probe"] = probe
    maintenance = _maintenance_result(
        profile=profile,
        plugin_root=plugin_root,
        current_time=now,
        host=current_host,
        pid_is_live=liveness,
    )

    desired_mode: str | None
    if marketplace_id is None or policy_enabled is None:
        desired_mode = None
    else:
        desired_mode = "namespaced" if policy_enabled else "legacy"
    actual_mode = activation["actualMode"]
    runtime_root = activation["runtimeRoot"]
    if (
        not policy_authoritative
        and activation["state"] in {"valid", "revalidation"}
        and activation["actualMode"] == "namespaced"
    ):
        desired_mode = "namespaced"

    invalid_reason: str | None = None
    if policy["state"] in {"invalid", "unsupported"}:
        invalid_reason = str(policy["reason"])
    elif identity_reason == "context-invalid":
        invalid_reason = identity_reason
    elif activation["state"] == "invalid":
        invalid_reason = str(activation["reason"])

    status = "ready"
    reason = str(policy["reason"])
    if invalid_reason is not None:
        status = "invalid"
        reason = invalid_reason
    elif maintenance["state"] in {"active", "stale"}:
        status = "maintenance-blocked"
        reason = f"maintenance-{maintenance['state']}"
    elif activation["state"] == "foreign" or legacy["status"] == "foreign-environment":
        status = "foreign-environment"
        reason = "foreign-environment"
    elif legacy["status"] == "orphaned-transfer":
        status = "orphaned-transfer"
        reason = "orphaned-transfer"
    elif activation["state"] == "revalidation":
        status = "revalidation-required"
        reason = "revalidation-required"
    elif identity_reason == "provenance-blocked":
        status = "provenance-blocked"
        reason = "provenance-blocked"
        desired_mode = None
        actual_mode = None
        runtime_root = None
    elif desired_mode == "legacy" and actual_mode == "namespaced":
        status = "deactivation-required"
        reason = "deactivation-required"
    elif desired_mode == "namespaced" and actual_mode == "legacy":
        if (
            activation["state"] == "missing"
            and probe["declared"]
            and probe["result"] == "absent"
        ):
            reason = "activation-required"
        else:
            status = "migration-required"
            reason = "migration-required"
    elif actual_mode == "namespaced":
        reason = "namespaced-active"

    return {
        "schema": RESOLUTION_SCHEMA,
        "version": 1,
        "marketplaceId": marketplace_id,
        "pluginId": trusted_plugin_id,
        "environment": current_environment,
        "desiredMode": desired_mode,
        "actualMode": actual_mode,
        "status": status,
        "maintenance": maintenance,
        "runtimeRoot": runtime_root,
        "context": activation["context"],
        "activation": activation["path"],
        "activationGeneration": activation["activationGeneration"],
        "installGeneration": activation["installGeneration"],
        "reason": reason,
        "policy": policy,
        "legacy": {
            "root": legacy["root"],
            "probe": probe,
            "tombstone": legacy["tombstone"],
            "disposition": legacy["disposition"],
            "ownerMarketplaceId": legacy["ownerMarketplaceId"],
        },
    }


@_validation_scope
def probe_legacy_entrypoint(**arguments: Any) -> dict[str, Any]:
    """Resolve installation mode and decide whether legacy mutation is allowed."""

    result = resolve_installation_mode(**arguments)
    allow_mutation = False
    probe_reason = str(result["reason"])
    legacy = result["legacy"]
    if result["status"] == "migration-required":
        allow_mutation = legacy["tombstone"] is None
        probe_reason = (
            "migration-required"
            if allow_mutation
            else "legacy-owned-by-other-cell"
        )
    elif result["status"] == "ready" and result["actualMode"] == "legacy":
        if legacy["tombstone"] is not None:
            probe_reason = "legacy-owned-by-other-cell"
        elif result["desiredMode"] == "namespaced":
            probe_reason = "namespaced-requested"
        else:
            allow_mutation = True
            probe_reason = "legacy-active"
    elif result["actualMode"] == "namespaced":
        probe_reason = "namespaced-active"
    decision = dict(result)
    decision["allowMutation"] = allow_mutation
    decision["probeReason"] = probe_reason
    return decision


def _source_descriptor(arguments: argparse.Namespace) -> Mapping[str, Any] | None:
    if arguments.source_json and arguments.source_file:
        _fail("Specify only one of --source-json and --source-file.")
    if arguments.source_file:
        value = read_json(arguments.source_file)
    elif arguments.source_json:
        try:
            value = json.loads(
                arguments.source_json,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except json.JSONDecodeError as error:
            _fail(f"Invalid --source-json: {error}")
    else:
        return None
    if not isinstance(value, Mapping):
        _fail("A source descriptor must be a JSON object.")
    return value


def _legacy_probe_argument(arguments: argparse.Namespace) -> Mapping[str, Any] | None:
    if arguments.legacy_probe_json and arguments.legacy_probe_file:
        _fail("Specify only one of --legacy-probe-json and --legacy-probe-file.")
    if arguments.legacy_probe_file:
        value = read_json(arguments.legacy_probe_file)
    elif arguments.legacy_probe_json:
        try:
            value = json.loads(
                arguments.legacy_probe_json,
                object_pairs_hook=_reject_duplicate_keys,
            )
        except json.JSONDecodeError as error:
            _fail(f"Invalid --legacy-probe-json: {error}")
    else:
        return None
    if not isinstance(value, Mapping):
        _fail("Legacy probe evidence must be a JSON object.")
    return value


def _add_common_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--copilot-home")
    parser.add_argument("--durable-home")
    parser.add_argument("--project-root")


def _add_resolution_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--source-json")
    parser.add_argument("--source-file")
    parser.add_argument("--marketplace-key")
    parser.add_argument("--plugin-id")
    parser.add_argument("--payload-root")
    parser.add_argument("--context")
    parser.add_argument("--expected-marketplace-id")
    parser.add_argument("--expected-plugin-id")
    parser.add_argument("--expected-payload-root")
    parser.add_argument("--expected-cell-root")
    _add_common_paths(parser)


def _add_mode_arguments(parser: argparse.ArgumentParser) -> None:
    _add_resolution_arguments(parser)
    parser.add_argument("--legacy-root", required=True)
    parser.add_argument("--legacy-probe-json")
    parser.add_argument("--legacy-probe-file")
    parser.add_argument("--policy-path")


def _parse_cli_generation(value: str) -> int:
    if re.fullmatch(r"[0-9]+", value, flags=re.ASCII) is None:
        raise argparse.ArgumentTypeError(
            "generation must be an unsigned ASCII decimal integer"
        )
    parsed = int(value, 10)
    if parsed > MAX_RECEIPT_GENERATION:
        raise argparse.ArgumentTypeError(
            "generation exceeds the portable signed 64-bit maximum"
        )
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="action", required=True)

    source_parser = subparsers.add_parser("source-id")
    source_parser.add_argument("--source-json")
    source_parser.add_argument("--source-file")
    source_parser.add_argument("--marketplace-key")

    resolve_parser = subparsers.add_parser("resolve")
    _add_resolution_arguments(resolve_parser)

    validate_parser = subparsers.add_parser("validate")
    validate_parser.add_argument("--context")
    validate_parser.add_argument("--payload-root")
    validate_parser.add_argument("--expected-marketplace-id")
    validate_parser.add_argument("--expected-plugin-id")
    validate_parser.add_argument("--expected-payload-root")
    validate_parser.add_argument("--expected-cell-root")
    _add_common_paths(validate_parser)

    stamp_parser = subparsers.add_parser("stamp")
    stamp_parser.add_argument("--source-json")
    stamp_parser.add_argument("--source-file")
    stamp_parser.add_argument("--marketplace-key")
    stamp_parser.add_argument("--plugin-id")
    stamp_parser.add_argument("--payload-root")
    stamp_parser.add_argument("--payload-version", required=True)
    stamp_parser.add_argument(
        "--payload-origin",
        required=True,
        choices=("installed", "directory", "staged", "explicit"),
    )
    stamp_parser.add_argument("--payload-origin-receipt")
    stamp_parser.add_argument(
        "--expected-namespace-generation",
        required=True,
        type=_parse_cli_generation,
    )
    stamp_parser.add_argument(
        "--expected-install-generation",
        required=True,
        type=_parse_cli_generation,
    )
    stamp_parser.add_argument(
        "--namespace-state",
        default="active",
        choices=("active", "inactive", "orphaned", "removing"),
    )
    stamp_parser.add_argument(
        "--install-state",
        default="active",
        choices=("active", "inactive", "orphaned", "removing"),
    )
    _add_common_paths(stamp_parser)

    activation_parser = subparsers.add_parser("activation-cas")
    activation_parser.add_argument("--context", required=True)
    activation_parser.add_argument("--expected-marketplace-id", required=True)
    activation_parser.add_argument("--expected-plugin-id", required=True)
    activation_parser.add_argument(
        "--expected-namespace-generation",
        required=True,
        type=_parse_cli_generation,
    )
    activation_parser.add_argument(
        "--expected-install-generation",
        required=True,
        type=_parse_cli_generation,
    )
    activation_parser.add_argument(
        "--expected-activation-generation",
        required=True,
        type=_parse_cli_generation,
    )
    activation_parser.add_argument(
        "--activation-mode",
        required=True,
        choices=("namespaced", "legacy"),
    )
    activation_parser.add_argument(
        "--activation-state",
        required=True,
        choices=("active", "deactivated"),
    )
    activation_parser.add_argument(
        "--legacy-disposition",
        required=True,
        choices=("absent", "quiesced", "retained-inert", "restored"),
    )
    activation_parser.add_argument("--legacy-probe-json")
    activation_parser.add_argument("--legacy-probe-file")
    activation_parser.add_argument("--legacy-root")
    activation_parser.add_argument("--durable-home")

    snapshot_stamp_parser = subparsers.add_parser("snapshot-stamp")
    snapshot_stamp_parser.add_argument("--context", required=True)
    snapshot_stamp_parser.add_argument("--expected-marketplace-id", required=True)
    snapshot_stamp_parser.add_argument("--expected-plugin-id", required=True)
    snapshot_stamp_parser.add_argument(
        "--expected-namespace-generation",
        required=True,
        type=_parse_cli_generation,
    )
    snapshot_stamp_parser.add_argument(
        "--expected-install-generation",
        required=True,
        type=_parse_cli_generation,
    )
    snapshot_stamp_parser.add_argument("--snapshot-id", required=True)
    snapshot_stamp_parser.add_argument("--durable-home")

    snapshot_validate_parser = subparsers.add_parser("snapshot-validate")
    snapshot_validate_parser.add_argument("--context", required=True)
    snapshot_validate_parser.add_argument("--expected-marketplace-id", required=True)
    snapshot_validate_parser.add_argument("--expected-plugin-id", required=True)
    snapshot_validate_parser.add_argument("--snapshot-id", required=True)
    snapshot_validate_parser.add_argument("--durable-home")

    slot_provision_parser = subparsers.add_parser("slot-provision")
    slot_provision_parser.add_argument("--context", required=True)
    slot_provision_parser.add_argument("--expected-marketplace-id", required=True)
    slot_provision_parser.add_argument("--expected-plugin-id", required=True)
    slot_provision_parser.add_argument("--snapshot-id", required=True)
    slot_provision_parser.add_argument("--runtime-version", required=True)
    slot_provision_parser.add_argument("--expected-payload-root")
    slot_provision_parser.add_argument("--expected-payload-version")
    slot_provision_parser.add_argument("--durable-home")

    slot_validate_parser = subparsers.add_parser("slot-validate")
    slot_validate_parser.add_argument("--context", required=True)
    slot_validate_parser.add_argument("--expected-marketplace-id", required=True)
    slot_validate_parser.add_argument("--expected-plugin-id", required=True)
    slot_validate_parser.add_argument("--snapshot-id", required=True)
    slot_validate_parser.add_argument("--runtime-version", required=True)
    slot_validate_parser.add_argument("--expected-payload-root")
    slot_validate_parser.add_argument("--expected-payload-version")
    slot_validate_parser.add_argument("--durable-home")

    slot_complete_parser = subparsers.add_parser("slot-complete")
    slot_complete_parser.add_argument("--context", required=True)
    slot_complete_parser.add_argument("--expected-marketplace-id", required=True)
    slot_complete_parser.add_argument("--expected-plugin-id", required=True)
    slot_complete_parser.add_argument("--expected-payload-root", required=True)
    slot_complete_parser.add_argument("--expected-payload-version", required=True)
    slot_complete_parser.add_argument("--snapshot-id", required=True)
    slot_complete_parser.add_argument("--runtime-version", required=True)
    slot_complete_parser.add_argument("--durable-home")

    slot_completion_validate_parser = subparsers.add_parser(
        "slot-completion-validate"
    )
    slot_completion_validate_parser.add_argument("--context", required=True)
    slot_completion_validate_parser.add_argument(
        "--expected-marketplace-id",
        required=True,
    )
    slot_completion_validate_parser.add_argument(
        "--expected-plugin-id",
        required=True,
    )
    slot_completion_validate_parser.add_argument(
        "--expected-payload-root",
        required=True,
    )
    slot_completion_validate_parser.add_argument(
        "--expected-payload-version",
        required=True,
    )
    slot_completion_validate_parser.add_argument("--snapshot-id", required=True)
    slot_completion_validate_parser.add_argument("--runtime-version", required=True)
    slot_completion_validate_parser.add_argument("--durable-home")

    slot_cutover_parser = subparsers.add_parser("slot-cutover")
    slot_cutover_parser.add_argument("--context", required=True)
    slot_cutover_parser.add_argument("--expected-marketplace-id", required=True)
    slot_cutover_parser.add_argument("--expected-plugin-id", required=True)
    slot_cutover_parser.add_argument("--expected-payload-root", required=True)
    slot_cutover_parser.add_argument("--expected-payload-version", required=True)
    slot_cutover_parser.add_argument("--snapshot-id", required=True)
    slot_cutover_parser.add_argument("--runtime-version", required=True)
    slot_cutover_parser.add_argument(
        "--expected-namespace-generation",
        required=True,
        type=_parse_cli_generation,
    )
    slot_cutover_parser.add_argument(
        "--expected-install-generation",
        required=True,
        type=_parse_cli_generation,
    )
    current_expectation = slot_cutover_parser.add_mutually_exclusive_group(
        required=True
    )
    current_expectation.add_argument("--expected-current-version")
    current_expectation.add_argument(
        "--expect-current-absent",
        action="store_true",
    )
    slot_cutover_parser.add_argument("--durable-home")

    status_parser = subparsers.add_parser("status")
    _add_mode_arguments(status_parser)

    probe_parser = subparsers.add_parser("probe-legacy")
    _add_mode_arguments(probe_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    arguments = parser.parse_args(argv)
    try:
        descriptor = _source_descriptor(arguments) if hasattr(arguments, "source_json") else None
        legacy_probe = (
            _legacy_probe_argument(arguments)
            if hasattr(arguments, "legacy_probe_json")
            else None
        )
        if arguments.action == "source-id":
            if descriptor is None:
                _fail("source-id requires --source-json or --source-file.")
            result = source_identity(
                normalize_source(descriptor),
                arguments.marketplace_key or "marketplace",
            )
        elif arguments.action == "validate":
            pointer = arguments.context or os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
            if not pointer:
                _fail("validate requires --context or COPILOT_EXTENSIONS_CONTEXT.")
            result = validate_context_receipt(
                pointer,
                arguments.durable_home or Path.home() / ".copilot-extensions",
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_payload_root=(
                    arguments.expected_payload_root or arguments.payload_root
                ),
                expected_cell_root=arguments.expected_cell_root,
            )
        elif arguments.action == "resolve":
            result = resolve_context(
                payload_root=arguments.payload_root,
                plugin_id=arguments.plugin_id,
                copilot_home=arguments.copilot_home,
                project_root=arguments.project_root,
                durable_home=arguments.durable_home,
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_payload_root=arguments.expected_payload_root,
                expected_cell_root=arguments.expected_cell_root,
                source_descriptor=descriptor,
                marketplace_key=arguments.marketplace_key,
            )
        elif arguments.action == "stamp":
            result = stamp_context(
                payload_version=arguments.payload_version,
                payload_origin=arguments.payload_origin,
                expected_namespace_generation=arguments.expected_namespace_generation,
                expected_install_generation=arguments.expected_install_generation,
                payload_origin_receipt=arguments.payload_origin_receipt,
                payload_root=arguments.payload_root,
                plugin_id=arguments.plugin_id,
                copilot_home=arguments.copilot_home,
                project_root=arguments.project_root,
                durable_home=arguments.durable_home,
                source_descriptor=descriptor,
                marketplace_key=arguments.marketplace_key,
                namespace_state=arguments.namespace_state,
                install_state=arguments.install_state,
            )
        elif arguments.action == "activation-cas":
            if legacy_probe is None:
                _fail(
                    "activation-cas requires --legacy-probe-json or "
                    "--legacy-probe-file."
                )
            result = compare_and_swap_activation(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_namespace_generation=arguments.expected_namespace_generation,
                expected_install_generation=arguments.expected_install_generation,
                expected_activation_generation=arguments.expected_activation_generation,
                activation_mode=arguments.activation_mode,
                activation_state=arguments.activation_state,
                legacy_disposition=arguments.legacy_disposition,
                legacy_probe=legacy_probe,
                durable_home=arguments.durable_home,
                legacy_root=arguments.legacy_root,
            )
        elif arguments.action == "snapshot-stamp":
            result = stamp_snapshot_provenance(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_namespace_generation=arguments.expected_namespace_generation,
                expected_install_generation=arguments.expected_install_generation,
                snapshot_id=arguments.snapshot_id,
                durable_home=arguments.durable_home,
            )
        elif arguments.action == "snapshot-validate":
            result = validate_snapshot_provenance(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                snapshot_id=arguments.snapshot_id,
                durable_home=arguments.durable_home,
            )
        elif arguments.action == "slot-provision":
            result = provision_runtime_slot(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                snapshot_id=arguments.snapshot_id,
                runtime_version=arguments.runtime_version,
                expected_payload_root=arguments.expected_payload_root,
                expected_payload_version=arguments.expected_payload_version,
                durable_home=arguments.durable_home,
            )
        elif arguments.action == "slot-validate":
            result = validate_runtime_slot_ownership(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                snapshot_id=arguments.snapshot_id,
                runtime_version=arguments.runtime_version,
                expected_payload_root=arguments.expected_payload_root,
                expected_payload_version=arguments.expected_payload_version,
                durable_home=arguments.durable_home,
            )
        elif arguments.action == "slot-complete":
            result = complete_runtime_slot(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_payload_root=arguments.expected_payload_root,
                expected_payload_version=arguments.expected_payload_version,
                snapshot_id=arguments.snapshot_id,
                runtime_version=arguments.runtime_version,
                durable_home=arguments.durable_home,
            )
        elif arguments.action == "slot-completion-validate":
            result = validate_runtime_slot_completion(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_payload_root=arguments.expected_payload_root,
                expected_payload_version=arguments.expected_payload_version,
                snapshot_id=arguments.snapshot_id,
                runtime_version=arguments.runtime_version,
                durable_home=arguments.durable_home,
            )
        elif arguments.action == "slot-cutover":
            result = cutover_runtime_slot(
                context=arguments.context,
                expected_marketplace_id=arguments.expected_marketplace_id,
                expected_plugin_id=arguments.expected_plugin_id,
                expected_payload_root=arguments.expected_payload_root,
                expected_payload_version=arguments.expected_payload_version,
                snapshot_id=arguments.snapshot_id,
                runtime_version=arguments.runtime_version,
                expected_namespace_generation=arguments.expected_namespace_generation,
                expected_install_generation=arguments.expected_install_generation,
                expected_current_version=arguments.expected_current_version,
                expect_current_absent=arguments.expect_current_absent,
                durable_home=arguments.durable_home,
            )
        else:
            mode_arguments = {
                "legacy_root": arguments.legacy_root,
                "legacy_probe": legacy_probe,
                "policy_path": arguments.policy_path,
                "payload_root": arguments.payload_root,
                "plugin_id": arguments.plugin_id,
                "copilot_home": arguments.copilot_home,
                "project_root": arguments.project_root,
                "durable_home": arguments.durable_home,
                "context": arguments.context,
                "expected_marketplace_id": arguments.expected_marketplace_id,
                "expected_plugin_id": arguments.expected_plugin_id,
                "expected_payload_root": arguments.expected_payload_root,
                "expected_cell_root": arguments.expected_cell_root,
                "source_descriptor": descriptor,
                "marketplace_key": arguments.marketplace_key,
            }
            if arguments.action == "status":
                result = resolve_installation_mode(**mode_arguments)
            else:
                result = probe_legacy_entrypoint(**mode_arguments)
        json.dump(result, sys.stdout, ensure_ascii=False, separators=(",", ":"))
        sys.stdout.write("\n")
        if arguments.action == "probe-legacy" and not result["allowMutation"]:
            return 3
        return 0
    except InstallationContextError as error:
        print(f"installation-context: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
