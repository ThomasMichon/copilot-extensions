# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
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
import subprocess
import sys
import time
import warnings
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager, nullcontext
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
DEACTIVATION_SCHEMA = "copilot-extensions.installation-deactivation"
RETIREMENT_SCHEMA = "copilot-extensions.legacy-retirement"
TOMBSTONE_SCHEMA = "copilot-extensions.legacy-installation-ownership"
MAINTENANCE_SCHEMA = "copilot-extensions.installation-maintenance"
RESOLUTION_SCHEMA = "copilot-extensions.installation-resolution"
LOOP_BASELINE_SCHEMA = "copilot-extensions.installation-loop-baseline"
SNAPSHOT_PROVENANCE_SCHEMA = "copilot-extensions.snapshot-provenance"
SNAPSHOT_PROVENANCE_FILE = "snapshot-provenance.json"
RUNTIME_SLOT_OWNERSHIP_SCHEMA = "copilot-extensions.runtime-slot-ownership"
RUNTIME_SLOT_OWNERSHIP_FILE = ".runtime-slot-ownership.json"
RUNTIME_SLOT_RESERVATION_SCHEMA = "copilot-extensions.runtime-slot-reservation"
RUNTIME_SLOT_RESERVATION_FILE = ".runtime-slot-reservation.json"
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
DEACTIVATION_RECORDS_DIR = "deactivations"
RETIREMENT_RECORDS_DIR = "retirements"
RETIREMENT_ID = re.compile(r"[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?", flags=re.ASCII)


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
    return value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns


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
