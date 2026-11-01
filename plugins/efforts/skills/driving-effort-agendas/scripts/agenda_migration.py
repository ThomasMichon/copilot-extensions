"""Fail-closed POSIX legacy-lane fencing, separate from SQLite acceptance."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from agenda import AgendaError, decode

RECOVERY = "agenda.init-recovery.json"
MARKER_SCHEMA = "efforts.agenda-managed-cursor"
LIMIT = 2 * 1024 * 1024


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read_bytes(path: Path, limit: int) -> bytes:
    try:
        with path.open("rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise AgendaError("initialization recovery exceeds its byte bound")
        return raw
    except OSError as exc:
        raise AgendaError(f"cannot read initialization recovery: {exc}") from exc


def _sync_directory(home: Path) -> None:
    fd = os.open(home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_write(home: Path, name: str, raw: bytes) -> None:
    """Only owned destinations; retain any interrupted temporary file as evidence."""
    temporary = home / f".{name}.{uuid4().hex}.tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, home / name)
    _sync_directory(home)


def _marker(request: str, encoder: Callable[[Any], str]) -> bytes:
    return (encoder({"schema": MARKER_SCHEMA, "version": 3,
        "database": "agenda.sqlite3", "recovery": RECOVERY,
        "request_sha256": _digest(request.encode())}) + "\n").encode()


def recover(
    home: Path, request: str, *,
    checked_path: Callable[[Path], Path], encoder: Callable[[Any], str],
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Recover only the same explicit initialization, never interpret marker paths."""
    path = checked_path(home / RECOVERY)
    cursor = checked_path(home / "cursor.json")
    if not path.exists():
        if cursor.exists():
            data = decode(_read_bytes(cursor, 131072), "cursor")
            if data.get("schema") == MARKER_SCHEMA and data.get("version") == 3:
                raise AgendaError("legacy lane is fenced but recovery is missing; preserve files and reconcile")
        return None
    record = decode(_read_bytes(path, LIMIT), "initialization recovery", limit=LIMIT)
    required = {"schema", "version", "request", "documents", "original_cursor", "cursor_sha256"}
    if set(record) != required or record["schema"] != "efforts.agenda-init-recovery" or (
            type(record["version"]) is not int or record["version"] != 1):
        raise AgendaError("unsupported initialization recovery record")
    if record["request"] != request:
        raise AgendaError("initialization recovery belongs to a different request; retry its saved request")
    try:
        original = base64.b64decode(record["original_cursor"], validate=True)
    except (ValueError, TypeError) as exc:
        raise AgendaError("corrupt original cursor recovery bytes") from exc
    if _digest(original) != record["cursor_sha256"]:
        raise AgendaError("original cursor recovery digest mismatch")
    if len(original) > 131072:
        raise AgendaError("original cursor recovery exceeds 128 KiB")
    current = _read_bytes(cursor, 131072) if cursor.exists() else None
    if current not in (original, _marker(request, encoder)):
        raise AgendaError("cursor changed after initialization preparation; preserve it and reconcile")
    return record["documents"], {"recovery_file": RECOVERY, "cursor_sha256": record["cursor_sha256"],
                                "original_cursor": record["original_cursor"], "status": "fenced"}


def prepare(
    home: Path, request: str, documents: dict[str, Any], *,
    checked_path: Callable[[Path], Path], encoder: Callable[[Any], str],
) -> dict[str, Any]:
    cursor = checked_path(home / "cursor.json")
    recovered = recover(home, request, checked_path=checked_path, encoder=encoder)
    if recovered is not None:
        saved, metadata = recovered
        if saved != documents:
            raise AgendaError("recovered initialization documents changed")
    elif not cursor.exists():
        return {"status": "not-needed"}
    else:
        if os.name != "posix":
            raise AgendaError("legacy lane fencing requires POSIX directory fsync; platform unsupported")
        original = _read_bytes(cursor, 131072)
        if decode(original, "original cursor") != documents["cursor"]:
            raise AgendaError("initialization must preserve the original lane cursor; import it before refinement")
        metadata = {"recovery_file": RECOVERY, "cursor_sha256": _digest(original),
                    "original_cursor": base64.b64encode(original).decode("ascii"), "status": "fenced"}
        record = {"schema": "efforts.agenda-init-recovery", "version": 1,
                  "request": request, "documents": documents,
                  "original_cursor": metadata["original_cursor"],
                  "cursor_sha256": metadata["cursor_sha256"]}
        raw = (encoder(record) + "\n").encode()
        decode(raw, "initialization recovery", limit=LIMIT)
        checked_path(home / RECOVERY)
        _atomic_write(home, RECOVERY, raw)
    if os.name != "posix":
        raise AgendaError("legacy lane fencing requires POSIX directory fsync; platform unsupported")
    # Recovery is durable before the old reader's original path becomes unsupported.
    checked_path(cursor)
    _atomic_write(home, "cursor.json", _marker(request, encoder))
    return metadata
