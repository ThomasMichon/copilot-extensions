"""Non-secret revocation fences survive an unavailable credential decoder."""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid
from typing import TYPE_CHECKING

from .cache_dpapi import CacheProtectionError
from .ssh_key_files import _ordinary

if TYPE_CHECKING:
    from .cache import PersistentCache

FILENAME = "service-cache-revocations.json"
MAX_BYTES = 1024 * 1024


@contextlib.contextmanager
def guard(persistent: PersistentCache) -> Iterator[None]:
    """Share revocations across selected compartments without caller-lock recursion."""
    with persistent._selection_guard():
        persistent._refresh_selection()
        with persistent._file_lock(persistent._root_dir / "service-cache-state.lock"):
            with persistent._store_lock():
                yield


@contextlib.contextmanager
def backend_guard(persistent: PersistentCache, database: str) -> Iterator[None]:
    """Serialize backend work for one shared database, independently of client locks."""
    key = hashlib.sha256(database.encode()).hexdigest()[:24]
    with persistent._file_lock(persistent._root_dir / f"service-cache-work-{key}.lock"):
        yield


def _unique(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate service-cache revocation field")
        result[key] = value
    return result


def _key(database: str, entry: str | None = None, field: str | None = None) -> str:
    if entry is None:
        return "database:" + database
    subject = json.dumps([entry, field], separators=(",", ":")).encode()
    return ("entry:" if field is None else "field:") + database + hashlib.sha256(subject).hexdigest()


def read(base: Path) -> dict[str, str]:
    path = base / FILENAME
    try:
        _ordinary(path, directory=False)
    except FileNotFoundError:
        return {}
    if path.stat().st_size > MAX_BYTES:
        raise CacheProtectionError("Protected service-cache revocation metadata is too large")
    try:
        document = json.loads(path.read_bytes(), object_pairs_hook=_unique)
    except (ValueError, UnicodeError) as exc:
        raise CacheProtectionError("Invalid service-cache revocation metadata") from exc
    if (
        not isinstance(document, dict) or set(document) != {"version", "epochs"}
        or type(document["version"]) is not int or document["version"] != 1
        or not isinstance(document["epochs"], dict)
        or any(
            not isinstance(key, str) or not isinstance(value, str)
            or re.fullmatch(r"[a-f0-9]{32}", value) is None
            for key, value in document["epochs"].items()
        )
    ):
        raise CacheProtectionError("Invalid service-cache revocation metadata")
    return document["epochs"]


def snapshot(epochs: dict[str, str], database: str, entry: str, field: str) -> list[str]:
    return [epochs.get(key, "") for key in (
        "all", _key(database), _key(database, entry), _key(database, entry, field),
    )]


def revoke(
    base: Path, database: str | None, entry: str | None = None, field: str | None = None,
    *, reset: bool = False,
) -> None:
    """Caller holds the existing protected-store lock, including codec failures."""
    if reset and database is not None:
        raise ValueError("A service-cache reset requires a global fence")
    epochs = {} if reset else read(base)
    if database is None:
        epochs = {}
    elif entry is None:
        prefixes = tuple(kind + database for kind in ("database:", "entry:", "field:"))
        epochs = {key: value for key, value in epochs.items() if not key.startswith(prefixes)}
    epochs["all" if database is None else _key(database, entry, field)] = uuid.uuid4().hex
    payload = json.dumps({"version": 1, "epochs": epochs}, separators=(",", ":")).encode()
    if len(payload) > MAX_BYTES:
        epochs = {"all": uuid.uuid4().hex}
        payload = json.dumps({"version": 1, "epochs": epochs}, separators=(",", ":")).encode()
    fd, temporary = tempfile.mkstemp(dir=base, prefix=".service-cache-", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        if os.name != "nt":
            os.chmod(temporary, 0o600)
        os.replace(temporary, base / FILENAME)
        if os.name != "nt":
            directory = os.open(base, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
