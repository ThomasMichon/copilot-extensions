"""Non-secret revocation fences survive an unavailable credential decoder."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
import uuid

from .cache_dpapi import CacheProtectionError
from .ssh_key_files import _ordinary

FILENAME = "service-cache-revocations.json"


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
    if path.stat().st_size > 1024 * 1024:
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
) -> None:
    """Caller holds the existing protected-store lock, including codec failures."""
    epochs = read(base)
    epochs["all" if database is None else _key(database, entry, field)] = uuid.uuid4().hex
    payload = json.dumps({"version": 1, "epochs": epochs}, separators=(",", ":")).encode()
    if len(payload) > 1024 * 1024:
        raise CacheProtectionError("Protected service-cache revocation metadata is too large")
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
