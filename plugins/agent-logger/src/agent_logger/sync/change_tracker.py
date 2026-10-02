"""Durable local session-signature tracking for incremental sync.

Re-walking and re-diffing the entire session-state tree on every scheduled
push is expensive once the corpus grows large -- especially when the
transport crosses a slow filesystem bridge (e.g. WSL's DrvFS view of a
Windows path), where even a no-op rsync invocation that only *compares*
mtimes/sizes can take long enough to exceed the engine's own subprocess
timeout. This module keeps a small local SQLite record of each session's
last-synced content signature, computed from local, native file stats only
(never rsync, never the network), so a routine run can cheaply ask "what
actually changed since last time?" and hand the target only that narrow set
via ``Target.push(..., include_sessions=...)``.

This is a *local* optimization, never a second source of truth: the
destination is always authoritative. A periodic full reconciliation pass
(see :func:`ChangeTracker.should_full_sync`) and the explicit ``run --full``
escape hatch exist precisely so local drift (a corrupted/stale db, content
this signature scheme can't detect) is never permanent -- see the
``session-sync-setup`` skill's "Change tracking" section for the operator
workflow.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any


def resolve_settings(raw: dict[str, Any]) -> dict[str, Any]:
    """Fill in defaults for a repo/machine-config ``sync.change_tracking`` block.

    On by default: a routine push only considers sessions whose local
    signature changed since the last sync. ``full_sync_interval_hours``
    periodically forces a full, segmented reconciliation (``<= 0`` disables
    the cadence; only ``run --full`` forces one after the first).
    ``batch_size`` bounds sessions per push call during a full pass so one
    rsync invocation can't exceed the engine's own subprocess timeout.
    ``db_path`` overrides the default ``<home>/sync-state.db`` (see
    :func:`resolve_db_path`).
    """
    return {
        "enabled": bool(raw.get("enabled", True)),
        "full_sync_interval_hours": float(raw.get("full_sync_interval_hours", 24)),
        "batch_size": int(raw.get("batch_size") or 100),
        "db_path": raw.get("db_path"),
    }

_SCHEMA = """
CREATE TABLE IF NOT EXISTS session_signatures (
    session_id TEXT PRIMARY KEY,
    signature TEXT NOT NULL,
    synced_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_LAST_FULL_SYNC_KEY = "last_full_sync_at"


def compute_signature(session_dir: Path) -> str:
    """Cheap content signature: sha256 over every file's sorted (relpath,
    size, mtime_ns).

    Stat-only -- never reads file content -- so this stays fast even over a
    slow filesystem bridge. Changes whenever a file is added, removed, or
    its size/mtime changes (covers appends, truncations, and touches).
    """
    hasher = hashlib.sha256()
    entries: list[tuple[str, int, int]] = []
    for path in session_dir.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat_result = path.stat()
        except OSError:
            continue
        rel = path.relative_to(session_dir).as_posix()
        entries.append((rel, stat_result.st_size, stat_result.st_mtime_ns))
    for rel, size, mtime_ns in sorted(entries):
        hasher.update(f"{rel}\0{size}\0{mtime_ns}\n".encode())
    return hasher.hexdigest()


def chunked(items: Iterable[str], size: int) -> Iterator[list[str]]:
    """Split *items* into lists of at most *size* -- bounds one rsync call's
    directory-walk cost so a large corpus can't exceed the engine's own
    subprocess timeout (see :mod:`agent_logger.sync.engine`'s segmented
    full-sync path).
    """
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def resolve_db_path(db_path_setting: str | None, home: Path) -> Path:
    """Resolve a configured (or default) change-tracker db path.

    Shared by the engine (single-config runs) and tenancy (per-tenant db
    naming, same pattern as ``chronicle.db_path``) so both agree on the
    fallback without either owning the other's config resolution.
    """
    if db_path_setting:
        return Path(db_path_setting).expanduser()
    return home / "sync-state.db"


class ChangeTracker:
    """Per-target durable record of each session's last-synced signature."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path

    def changed_sessions(
        self, source: Path, session_ids: Iterable[str] | None = None
    ) -> set[str]:
        """Session ids whose on-disk signature differs from (or has no)
        stored record.

        ``session_ids``, when given, scopes the scan to just those ids (e.g.
        a repo-allowlist's already-narrowed set) rather than every session
        under *source*.
        """
        session_state = source / "session-state"
        if not session_state.is_dir():
            return set()
        candidates = (
            [session_state / sid for sid in session_ids]
            if session_ids is not None
            else list(session_state.iterdir())
        )
        changed: set[str] = set()
        with _connect(self.db_path) as conn:
            for candidate in candidates:
                if not candidate.is_dir():
                    continue
                signature = compute_signature(candidate)
                row = conn.execute(
                    "SELECT signature FROM session_signatures WHERE session_id = ?",
                    (candidate.name,),
                ).fetchone()
                if row is None or row[0] != signature:
                    changed.add(candidate.name)
        return changed

    def record(self, source: Path, session_ids: Iterable[str]) -> None:
        """Persist the current signature for each of *session_ids* as synced."""
        session_state = source / "session-state"
        now = time.time()
        with _connect(self.db_path) as conn:
            for session_id in session_ids:
                session_dir = session_state / session_id
                if not session_dir.is_dir():
                    continue
                signature = compute_signature(session_dir)
                conn.execute(
                    "INSERT INTO session_signatures (session_id, signature, synced_at) "
                    "VALUES (?, ?, ?) ON CONFLICT(session_id) DO UPDATE SET "
                    "signature=excluded.signature, synced_at=excluded.synced_at",
                    (session_id, signature, now),
                )

    def known_session_ids(self) -> set[str]:
        """Every session id this tracker currently holds a signature for."""
        with _connect(self.db_path) as conn:
            rows = conn.execute("SELECT session_id FROM session_signatures").fetchall()
        return {row[0] for row in rows}

    def vanished_sessions(self, source: Path) -> set[str]:
        """Known session ids with no corresponding directory under *source*
        anymore -- e.g. locally compacted/pruned since the last sync. The
        destination copy is reclaimed by the existing age-based
        :meth:`~agent_logger.sync.targets.base.Target.prune`, not by this
        tracker; this is only for keeping the local db from accumulating
        stale rows forever.
        """
        known = self.known_session_ids()
        if not known:
            return set()
        session_state = source / "session-state"
        present = (
            {d.name for d in session_state.iterdir() if d.is_dir()}
            if session_state.is_dir()
            else set()
        )
        return known - present

    def forget(self, session_ids: Iterable[str]) -> None:
        """Drop stored signatures for sessions that no longer exist locally."""
        with _connect(self.db_path) as conn:
            conn.executemany(
                "DELETE FROM session_signatures WHERE session_id = ?",
                [(session_id,) for session_id in session_ids],
            )

    def should_full_sync(self, interval_hours: float) -> bool:
        """Whether a periodic full reconciliation pass is due.

        Always ``True`` for a from-scratch db (no full sync ever recorded).
        ``interval_hours <= 0`` disables the periodic cadence thereafter --
        every later run stays incremental until an explicit ``run --full``.
        """
        with _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT value FROM sync_meta WHERE key = ?", (_LAST_FULL_SYNC_KEY,)
            ).fetchone()
        if row is None:
            return True
        if interval_hours <= 0:
            return False
        return (time.time() - float(row[0])) >= interval_hours * 3600

    def mark_full_sync(self) -> None:
        """Record that a full reconciliation pass just completed."""
        with _connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO sync_meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (_LAST_FULL_SYNC_KEY, str(time.time())),
            )

    def reset(self) -> None:
        """Discard every stored signature and full-sync marker.

        The operator-invoked "doctor" escape hatch for local/upstream drift:
        pairing this with ``run --full`` forces a complete, from-scratch
        reconciliation against the real destination (always authoritative)
        and rebuilds every signature from what that reconciliation actually
        pushed, rather than trusting a potentially stale local db.
        """
        with _connect(self.db_path) as conn:
            conn.execute("DELETE FROM session_signatures")
            conn.execute("DELETE FROM sync_meta")
