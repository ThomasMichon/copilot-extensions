"""Opt-in machine-protected Windows cache copies, bound to the primary bytes."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
import hashlib
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
import uuid

from . import kek
from .cache_dpapi import CacheProtectionError
from .ssh_key_files import WindowsSecurity, _ordinary, _require

CAPABILITY = "windows-machine-cache-v1"
DIRECTORY = "machine-fallback"
FILENAME = "credential-cache.enc"
MAGIC = b"AVC1M"
MAX_BYTES = 16 * 1024 * 1024
if TYPE_CHECKING:
    from .cache import PersistentCache


class MachineReadStore(dict[str, Any]):
    """A redundant read must never become primary-cache writeback."""


def _security() -> WindowsSecurity:
    return cast(Callable[[], WindowsSecurity], WindowsSecurity)()


def enabled(cache: PersistentCache) -> bool:
    from .cache import IS_WINDOWS, _path_exists

    return IS_WINDOWS and _path_exists(cache._base_dir / DIRECTORY)


def _read(path: Path, security: WindowsSecurity | None = None) -> bytes:
    before = _ordinary(path, directory=False)
    if security is not None:
        _require(path, security, directory=False)
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
            raise CacheProtectionError("Machine cache location changed while opening")
        value = stream.read(MAX_BYTES + 1)
    after = _ordinary(path, directory=False)
    if security is not None:
        _require(path, security, directory=False)
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
    if len(value) > MAX_BYTES or any(
        getattr(before, field) != getattr(after, field) for field in fields
    ):
        raise CacheProtectionError("Machine cache source changed or exceeds its size limit")
    return value


def _source(cache: PersistentCache, primary: bytes | None = None) -> str:
    from .cache import _path_exists

    primary = _read(cache._cache_file) if primary is None else primary
    if len(primary) > MAX_BYTES:
        raise CacheProtectionError("Machine cache source exceeds its size limit")
    key = _read(cache._key_file) if _path_exists(cache._key_file) else None
    subject = json.dumps([
        os.path.normcase(str(cache._base_dir.resolve())),
        hashlib.sha256(primary).hexdigest(),
        hashlib.sha256(key).hexdigest() if key is not None else None,
    ], separators=(",", ":")).encode()
    return hashlib.sha256(subject).hexdigest()


def _directory(cache: PersistentCache, security: WindowsSecurity) -> Path:
    directory = cache._base_dir / DIRECTORY
    _require(directory, security, directory=True)
    return directory


def write_copy(cache: PersistentCache, store: dict[str, Any], primary: bytes) -> None:
    """Publish a matching candidate before its primary atomic replacement."""
    from .cache import _path_exists

    if not enabled(cache):
        return
    if isinstance(store, MachineReadStore):
        raise CacheProtectionError("Machine cache reads cannot replace the primary")
    security = _security()
    directory = _directory(cache, security)
    target = directory / FILENAME
    if _path_exists(target):
        _require(target, security, directory=False)
    envelope = json.dumps({
        "v": 1, "source": _source(cache, primary), "store": store,
    }, separators=(",", ":")).encode()
    ciphertext = MAGIC + kek._dpapi(True, envelope, machine=True)
    if len(ciphertext) > MAX_BYTES:
        raise CacheProtectionError("Machine cache exceeds its size limit")
    temporary = directory / (".machine-cache-" + uuid.uuid4().hex + ".tmp")
    descriptor = security.create(temporary, directory=False)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            _require(temporary, security, directory=False)
            stream.write(ciphertext)
            stream.flush()
            os.fsync(stream.fileno())
        _directory(cache, security)
        if _path_exists(target):
            _require(target, security, directory=False)
        os.replace(temporary, target)
        _require(target, security, directory=False)
    finally:
        with contextlib.suppress(OSError):
            temporary.unlink(missing_ok=True)


def read_store(cache: PersistentCache) -> MachineReadStore:
    """Read only a secure copy matching the selected primary and key bytes."""
    from .cache_recovery import require_coordinated

    try:
        require_coordinated(cache._root_dir, cache._recovery)
        if not enabled(cache):
            raise CacheProtectionError("Machine cache fallback is not enabled")
        security = _security()
        directory = _directory(cache, security)
        source = _source(cache)
        ciphertext = _read(directory / FILENAME, security)
        if not ciphertext.startswith(MAGIC):
            raise CacheProtectionError("Unsupported machine cache format")
        envelope = json.loads(kek._dpapi(False, ciphertext[len(MAGIC):]))
        if (
            not isinstance(envelope, dict) or set(envelope) != {"v", "source", "store"}
            or type(envelope["v"]) is not int or envelope["v"] != 1
            or envelope["source"] != source
            or not isinstance(envelope["store"], dict)
            or type(envelope["store"].get("v")) is not int or envelope["store"]["v"] != 1
            or not isinstance(envelope["store"].get("entries"), dict)
            or _source(cache) != source
        ):
            raise CacheProtectionError("Machine cache does not match the selected primary")
        _directory(cache, security)
        return MachineReadStore(envelope["store"])
    except CacheProtectionError:
        raise
    except (OSError, ValueError, kek.KekError) as exc:
        raise CacheProtectionError("Machine cache is unavailable; primary retained") from exc


def prepare(cache: PersistentCache) -> dict[str, bool]:
    """Explicitly enable and populate a redundant copy without primary writes."""
    from .cache import IS_WINDOWS, _path_exists

    if not IS_WINDOWS or not cache.enabled:
        raise CacheProtectionError("Machine cache preparation requires an enabled Windows cache")
    with cache.replacement_lock(), cache._store_lock():
        primary = _read(cache._cache_file) if _path_exists(cache._cache_file) else None
        source = _source(cache, primary) if primary is not None else None
        store = cache._read_selected_store(readonly=True, strict=True)
        if source is not None and _source(cache) != source:
            raise CacheProtectionError("Machine cache source changed during preparation")
        security = _security()
        directory = cache._base_dir / DIRECTORY
        security.create(directory, directory=True)
        _require(directory, security, directory=True)
        if primary is not None:
            write_copy(cache, store, primary)
        return {"enabled": True, "populated": primary is not None}


def clear_copy(cache: PersistentCache) -> None:
    """Clear redundant credential data while retaining the explicit opt-in."""
    from .cache import _path_exists

    if enabled(cache):
        security = _security()
        target = _directory(cache, security) / FILENAME
        if _path_exists(target):
            _require(target, security, directory=False)
            target.unlink()
