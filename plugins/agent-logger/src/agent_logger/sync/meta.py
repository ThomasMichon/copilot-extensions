"""Sync metadata sidecar.

Each push drops a ``sync-meta.json`` at the machine root so a consumer can
see which machine last wrote, when, and via which transport. Ported from the
multi-machine system engine's ``write_sync_meta`` (local variant).
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

SYNC_VERSION = "1.0.0"
MAX_DEFERRED_FILE_SAMPLES = 10
MAX_DEFERRED_PATH_CHARS = 512
MAX_META_FIELD_CHARS = 256
MAX_SYNC_META_BYTES = 64 * 1024


def _bounded_text(value: str) -> str:
    return str(value)[:MAX_META_FIELD_CHARS]


def write_sync_meta(
    dest: Path,
    machine: str,
    transport: str,
    status: str,
    session_count: int = 0,
    deferred_files: Iterable[str] = (),
) -> None:
    """Atomically write ``sync-meta.json`` into *dest* (best-effort)."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    deferred = [str(path) for path in deferred_files]
    partial_streak = 0
    if status == "partial":
        partial_streak = 1
        try:
            previous = read_sync_meta(dest)
        except OSError:
            previous = None
        if previous and previous.get("status") == "partial":
            prior_streak = previous.get("consecutive_partial_count")
            if isinstance(prior_streak, int) and not isinstance(prior_streak, bool):
                partial_streak = max(1, min(prior_streak, 999_999) + 1)
    meta = json.dumps(
        {
            "machine_id": _bounded_text(machine),
            "last_sync_utc": now_utc,
            "sync_version": SYNC_VERSION,
            "transport": _bounded_text(transport),
            "status": _bounded_text(status),
            "consecutive_partial_count": partial_streak,
            "session_count": session_count,
            "deferred_file_count": len(deferred),
            "deferred_files": [
                path[:MAX_DEFERRED_PATH_CHARS]
                for path in deferred[:MAX_DEFERRED_FILE_SAMPLES]
            ],
        },
        indent=2,
    )
    meta_file = dest / "sync-meta.json"
    tmp = meta_file.with_name(f".sync-meta.{uuid.uuid4().hex}.tmp")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        tmp.write_text(meta, encoding="utf-8")
        os.replace(tmp, meta_file)
    except OSError:
        pass
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def read_sync_meta(dest: Path) -> dict | None:
    """Read one bounded machine sync metadata object."""
    meta_file = dest / "sync-meta.json"
    try:
        with meta_file.open("rb") as stream:
            raw = stream.read(MAX_SYNC_META_BYTES + 1)
    except FileNotFoundError:
        return None
    if len(raw) > MAX_SYNC_META_BYTES:
        raise OSError(f"sync metadata is too large: {meta_file}")
    try:
        payload = json.loads(raw)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        ValueError,
    ) as exc:
        raise OSError(f"invalid sync metadata: {meta_file}") from exc
    if not isinstance(payload, dict):
        raise OSError(f"sync metadata must be an object: {meta_file}")
    return payload
