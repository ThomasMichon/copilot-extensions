"""Sync metadata sidecar.

Each push drops a ``sync-meta.json`` at the machine root so a consumer can
see which machine last wrote, when, and via which transport. Ported from the
multi-machine system engine's ``write_sync_meta`` (local variant).
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

from agent_logger.sync.health import (
    MAX_DEFERRED_FILE_SAMPLES,
    MAX_DEFERRED_PATH_CHARS,
    merge_health,
    migrate_legacy_health,
    validate_recorded_health,
)

log = logging.getLogger("agent-logger.sync-meta")
SYNC_VERSION = "1.0.0"
MAX_EXCLUDED_ROOT_SAMPLES = 10
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
    excluded_roots: Iterable[str] = (),
    excluded_file_count: int = 0,
    excluded_byte_count: int = 0,
    excluded_measurement_complete: bool = True,
) -> None:
    """Atomically write ``sync-meta.json`` into *dest* (best-effort)."""
    now_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    deferred = [str(path) for path in deferred_files]
    excluded = [str(path) for path in excluded_roots]
    try:
        previous = read_sync_meta(dest)
    except OSError as exc:
        log.warning("cannot read prior sync metadata at %s: %s", dest, exc)
        return
    metadata = {
        "machine_id": _bounded_text(machine),
        "last_sync_utc": now_utc,
        "sync_version": SYNC_VERSION,
        "transport": _bounded_text(transport),
        "status": _bounded_text(status),
        "session_count": session_count,
        "deferred_file_count": len(deferred),
        "deferred_files": [
            path[:MAX_DEFERRED_PATH_CHARS] for path in deferred[:MAX_DEFERRED_FILE_SAMPLES]
        ],
        "excluded_detritus_root_count": len(excluded),
        "excluded_detritus_file_count": excluded_file_count,
        "excluded_detritus_byte_count": excluded_byte_count,
        "excluded_detritus_measurement_complete": (excluded_measurement_complete),
        "excluded_detritus_roots": [
            path[:MAX_DEFERRED_PATH_CHARS] for path in excluded[:MAX_EXCLUDED_ROOT_SAMPLES]
        ],
    }
    try:
        merge_health(
            metadata,
            previous,
            leg="session-state",
            status=status,
            deferred=deferred,
            sample_limit=MAX_DEFERRED_FILE_SAMPLES,
            sample_chars=MAX_DEFERRED_PATH_CHARS,
        )
    except OSError as exc:
        log.warning("cannot update sync health at %s: %s", dest, exc)
        return
    _write_metadata(dest, metadata)


def write_process_log_meta(
    dest: Path,
    status: str,
    deferred_files: Iterable[str] = (),
) -> None:
    """Record a log attempt while preserving all session-state diagnostics."""
    try:
        previous = read_sync_meta(dest)
        if previous is None:
            return
        metadata = dict(previous)
        metadata["last_sync_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        merge_health(
            metadata,
            previous,
            leg="process-logs",
            status=status,
            deferred=[str(path) for path in deferred_files],
            sample_limit=MAX_DEFERRED_FILE_SAMPLES,
            sample_chars=MAX_DEFERRED_PATH_CHARS,
        )
    except OSError as exc:
        log.warning("cannot update process-log sync health at %s: %s", dest, exc)
        return
    _write_metadata(dest, metadata)


def _write_metadata(dest: Path, metadata: dict) -> None:
    meta = json.dumps(metadata, indent=2)
    meta_file = dest / "sync-meta.json"
    tmp = meta_file.with_name(f".sync-meta.{uuid.uuid4().hex}.tmp")
    try:
        dest.mkdir(parents=True, exist_ok=True)
        tmp.write_text(meta, encoding="utf-8")
        os.replace(tmp, meta_file)
    except OSError as exc:
        log.warning("cannot publish sync metadata at %s: %s", dest, exc)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("cannot remove sync metadata temporary file at %s: %s", tmp, exc)


def heartbeat_sync_meta(
    dest: Path, machine: str, transport: str, fallback_session_count: int
) -> None:
    """Re-stamp an existing ``sync-meta.json``'s ``last_sync_utc`` IN PLACE,
    and the session-state leg's check time, touching no result fields --
    a no-change fast path still needs the
    destination's own health metadata to reflect a just-verified-current
    pass, or a routine health check would see only the last real *transfer*
    (which may predate the periodic full-reconciliation window) and report a
    healthy, unchanged destination as stale.

    Deliberately does NOT round-trip through :func:`write_sync_meta`: that
    function's own side effects (bumping ``consecutive_partial_count``,
    truncating ``deferred_files``/``excluded_detritus_roots`` back down to
    their sample caps) are meant for an actual new transfer attempt, not a
    skip -- replaying them here would misclassify health (a bumped partial
    streak with no new attempt) and silently shrink an already-bounded
    sample list further each heartbeat. Falls back to a full
    :func:`write_sync_meta` call only when no metadata exists yet at all
    (first-ever heartbeat, nothing to preserve). A malformed/unreadable
    existing file (:func:`read_sync_meta` raising, as opposed to returning
    ``None`` for a genuinely missing one) is left untouched rather than
    silently overwritten with a fresh "ok" status -- that visible error is
    itself a signal worth preserving, not something a no-op heartbeat
    should paper over.
    """
    try:
        previous = read_sync_meta(dest)
    except OSError as exc:
        log.warning("cannot read sync metadata for heartbeat at %s: %s", dest, exc)
        return
    if previous is None:
        write_sync_meta(dest, machine, transport, "ok", fallback_session_count)
        return
    try:
        if "sync_legs" in previous:
            validate_recorded_health(previous)
    except OSError as exc:
        log.warning("cannot validate sync metadata for heartbeat at %s: %s", dest, exc)
        return
    if "sync_legs" not in previous:
        try:
            migrate_legacy_health(previous)
        except OSError as exc:
            log.warning("cannot migrate sync metadata for heartbeat at %s: %s", dest, exc)
            return
    previous["last_sync_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    legs = previous.get("sync_legs")
    if isinstance(legs, dict) and isinstance(legs.get("session-state"), dict):
        legs["session-state"]["last_checked_utc"] = previous["last_sync_utc"]
    meta_file = dest / "sync-meta.json"
    tmp = meta_file.with_name(f".sync-meta.{uuid.uuid4().hex}.tmp")
    try:
        tmp.write_text(json.dumps(previous, indent=2), encoding="utf-8")
        os.replace(tmp, meta_file)
    except OSError as exc:
        log.warning("cannot publish sync metadata heartbeat at %s: %s", dest, exc)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError as exc:
            log.warning("cannot remove sync metadata temporary file at %s: %s", tmp, exc)


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
