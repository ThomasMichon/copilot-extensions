"""Local submitted-prompt recovery, independent of engine launch success."""

from __future__ import annotations

import math
import sqlite3
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from .launch_attempts import RETENTION_SECONDS
from .self_install import default_root
from .private_state import protect, reject_links

_APPLICATION_ID = 0x57534831
_INDEX = "CREATE INDEX seeds_expiry ON seeds(submitted_at)"


class SeedHistoryError(RuntimeError):
    """Prompt recovery storage is unavailable; never include prompt contents."""


def _has_history_schema(conn: sqlite3.Connection) -> bool:
    # One snapshot avoids mixed observations when another first writer creates the schema.
    objects, seeds, pages, free, version, application = conn.execute(
        "SELECT (SELECT count(*) FROM sqlite_master), "
        "(SELECT count(*) FROM sqlite_master WHERE type = 'table' AND name = 'seeds'), "
        "(SELECT page_count FROM pragma_page_count), "
        "(SELECT freelist_count FROM pragma_freelist_count), "
        "(SELECT user_version FROM pragma_user_version), "
        "(SELECT application_id FROM pragma_application_id)"
    ).fetchone()
    if objects == 0:
        if pages in (0, 1) and free == version == application == 0:
            return False
        raise SeedHistoryError("Local seed history is not an uninitialized database.")
    columns = {
        row[1]: (row[2].upper(), row[3], row[5])
        for row in conn.execute("PRAGMA table_info(seeds)")
    }
    legacy = version == application == 0
    indexes = conn.execute(
        "SELECT type, name, sql FROM sqlite_master WHERE name != 'seeds'"
    ).fetchall()
    if (
        seeds != 1
        or (objects != 1 if legacy else (
            version not in (1, 2) or application != _APPLICATION_ID
            or objects != 2 or indexes != [("index", "seeds_expiry", _INDEX)]
        ))
        or columns != {
            "id": ("INTEGER", 0, 1),
            "project": ("TEXT", 1, 0),
            "prompt": ("TEXT", 1, 0),
            "submitted_at": ("REAL", 1, 0),
        }
    ):
        raise SeedHistoryError("Local seed history has an incompatible database schema.")
    return True


def _timestamp() -> float:
    now = time.time()
    if not math.isfinite(now) or now < 0:
        raise SeedHistoryError("Local seed history clock is invalid.")
    return now


def _cutoff() -> float:
    return _timestamp() - RETENTION_SECONDS


def _validate_legacy_timestamps(conn: sqlite3.Connection) -> None:
    if conn.execute(
        "SELECT 1 FROM seeds WHERE typeof(submitted_at) NOT IN ('real', 'integer') "
        "OR submitted_at < 0 OR submitted_at > ? LIMIT 1", (sys.float_info.max,),
    ).fetchone():
        raise SeedHistoryError("Local seed history has an invalid submission timestamp.")


class SeedHistory:
    def __init__(self, project: str, root: Path | None = None) -> None:
        self.project = project
        self.path = (
            (root if root is not None else default_root())
            / "seed-history" / "history.sqlite3"
        )

    def _exists(self) -> bool:
        reject_links(self.path)
        exists = self.path.exists()
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            reject_links(sidecar)
            if sidecar.exists() and not exists:
                exists = self.path.exists()
                if not exists:
                    raise SeedHistoryError("Local seed history is missing but a SQLite sidecar remains.")
        if exists:
            if not stat.S_ISREG(self.path.stat().st_mode):
                raise SeedHistoryError("Local seed history requires a regular database file.")
            with self.path.open("rb") as stream:
                header = stream.read(20)
            if (header[:16] != b"SQLite format 3\0" or header[18:20] != b"\x02\x02") and any(
                self.path.with_name(self.path.name + suffix).exists()
                for suffix in ("-wal", "-shm")
            ):
                raise SeedHistoryError("Local seed history has an unexpected WAL sidecar.")
        return exists

    def _protect(self) -> None:
        protect(self.path.parent, directory=True)
        if self.path.exists():
            protect(self.path)
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = self.path.with_name(self.path.name + suffix)
            reject_links(sidecar)
            try:
                protect(sidecar)
            except FileNotFoundError:
                pass  # SQLite can remove an optional sidecar when another connection commits.

    def _entries(self) -> list[tuple[str, float]]:
        try:
            if not self._exists():
                return []
            self._protect()
            with self.path.open("rb") as stream:
                header = stream.read(20)
            if header[18:20] == b"\x02\x02":
                raise SeedHistoryError("Local WAL seed history requires retention maintenance.")
            uri = self.path.absolute().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, timeout=5, uri=True)) as conn:
                conn.execute("BEGIN")
                if not _has_history_schema(conn):
                    return []
                if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
                    _validate_legacy_timestamps(conn)
                rows = conn.execute(
                    "SELECT prompt, submitted_at FROM seeds WHERE project = ? AND submitted_at > ? "
                    "ORDER BY id DESC",
                    (self.project, _cutoff()),
                ).fetchall()
                if any(
                    not isinstance(timestamp, (int, float)) or not math.isfinite(timestamp)
                    or timestamp < 0 for _, timestamp in rows
                ):
                    raise SeedHistoryError("Local seed history has an invalid submission timestamp.")
                return rows
        except (OSError, sqlite3.Error) as exc:
            raise SeedHistoryError("Could not read local seed history.") from exc

    def entries(self) -> list[str]:
        return [prompt for prompt, _timestamp in self._entries()]

    def cursor(self) -> HistoryCursor:
        rows = self._entries()
        return HistoryCursor(
            [prompt for prompt, _timestamp in rows],
            expires_at=[timestamp + RETENTION_SECONDS for _prompt, timestamp in rows],
        )

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._protect()
        with closing(sqlite3.connect(self.path, timeout=5)) as conn:
            protect(self.path)
            self._protect()
            _has_history_schema(conn)
            # Switching a recognized legacy WAL store checkpoints its live pages.
            # Refuse busy writers rather than deleting any SQLite-owned sidecar.
            if conn.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                raise SeedHistoryError("Local seed history requires DELETE journaling.")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA secure_delete=ON")
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                initialized = _has_history_schema(conn)
                if not initialized:
                    conn.execute(
                        "CREATE TABLE seeds "
                        "(id INTEGER PRIMARY KEY, project TEXT NOT NULL, "
                        "prompt TEXT NOT NULL, submitted_at REAL NOT NULL)"
                    )
                if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
                    _validate_legacy_timestamps(conn)
                    conn.execute(_INDEX)
                    conn.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                    # V1 means a legacy freelist scrub must still finish.
                    conn.execute(f"PRAGMA user_version={1 if initialized else 2}")
                yield conn
            if conn.execute("PRAGMA user_version").fetchone()[0] == 1:
                conn.execute("VACUUM")
                with conn:
                    conn.execute("PRAGMA user_version=2")

    def expire(self) -> int:
        """Migrate recognized legacy history and physically erase expired copies."""
        try:
            if not self._exists():
                return 0
            with self._write() as conn:
                return conn.execute(
                    "DELETE FROM seeds WHERE submitted_at <= ?", (_cutoff(),),
                ).rowcount
        except (OSError, sqlite3.Error) as exc:
            raise SeedHistoryError("Could not erase expired local seed history.") from exc

    def remember(self, prompt: str) -> None:
        if not prompt.strip():
            return
        try:
            self._exists()
            now = _timestamp()
            with self._write() as conn:
                conn.execute("DELETE FROM seeds WHERE submitted_at <= ?", (now - RETENTION_SECONDS,))
                previous = conn.execute(
                    "SELECT prompt FROM seeds WHERE project = ? ORDER BY id DESC LIMIT 1",
                    (self.project,),
                ).fetchone()
                if previous is None or previous[0] != prompt:
                    conn.execute(
                        "INSERT INTO seeds(project, prompt, submitted_at) VALUES (?, ?, ?)",
                        (self.project, prompt, now),
                    )
        except (OSError, sqlite3.Error) as exc:
            raise SeedHistoryError(
                "Could not save local seed history; prompt has not been submitted."
            ) from exc


class HistoryCursor:
    def __init__(self, entries: list[str], *, expires_at: list[float] | None = None) -> None:
        if expires_at is not None and len(expires_at) != len(entries):
            raise ValueError("History cursor deadlines must match its entries.")
        self.entries = entries
        self._expires_at = expires_at
        self._next_expiry = min(expires_at, default=math.inf) if expires_at is not None else math.inf
        self._expired: set[int] = set()
        self.index = -1
        self.edits: dict[int, str] = {}

    def move(self, current: str, direction: int) -> str | None:
        if self._expires_at is not None:
            now = _timestamp()
            if now >= self._next_expiry:
                self._next_expiry = math.inf
                for index, deadline in enumerate(self._expires_at):
                    if deadline <= now:
                        self._expired.add(index)
                        self.entries[index] = ""
                        self.edits.pop(index, None)
                    else:
                        self._next_expiry = min(self._next_expiry, deadline)
        target = self.index + direction
        while 0 <= target < len(self.entries) and target in self._expired:
            target += direction
        if target < -1 or target >= len(self.entries):
            return None
        if self.index not in self._expired:
            self.edits[self.index] = current
        self.index = target
        return self.edits.get(target, "" if target == -1 else self.entries[target])
