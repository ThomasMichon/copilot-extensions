"""Local submitted-prompt recovery, independent of engine launch success."""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from pathlib import Path

from .self_install import default_root
from .private_state import protect, reject_links


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
    if objects != 1 or version != 0 or application != 0 or seeds != 1 or columns != {
        "id": ("INTEGER", 0, 1),
        "project": ("TEXT", 1, 0),
        "prompt": ("TEXT", 1, 0),
        "submitted_at": ("REAL", 1, 0),
    }:
        raise SeedHistoryError("Local seed history has an incompatible database schema.")
    return True


class SeedHistory:
    def __init__(self, project: str, root: Path | None = None) -> None:
        self.project = project
        self.path = (
            (root if root is not None else default_root())
            / "seed-history" / "history.sqlite3"
        )

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

    def entries(self) -> list[str]:
        try:
            reject_links(self.path)
            if not self.path.exists():
                return []
            self._protect()
            uri = self.path.absolute().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(uri, timeout=5, uri=True)) as conn:
                if not _has_history_schema(conn):
                    return []
                return [
                    row[0] for row in conn.execute(
                        "SELECT prompt FROM seeds WHERE project = ? ORDER BY id DESC",
                        (self.project,),
                    )
                ]
        except (OSError, sqlite3.Error) as exc:
            raise SeedHistoryError("Could not read local seed history.") from exc

    def remember(self, prompt: str) -> None:
        if not prompt.strip():
            return
        try:
            reject_links(self.path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._protect()
            with closing(sqlite3.connect(self.path, timeout=5)) as conn, conn:
                protect(self.path)
                _has_history_schema(conn)
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS seeds "
                    "(id INTEGER PRIMARY KEY, project TEXT NOT NULL, "
                    "prompt TEXT NOT NULL, submitted_at REAL NOT NULL)"
                )
                # Serialize the latest-prompt read and insert; the context commits or rolls back.
                conn.execute("BEGIN IMMEDIATE")
                previous = conn.execute(
                    "SELECT prompt FROM seeds WHERE project = ? ORDER BY id DESC LIMIT 1",
                    (self.project,),
                ).fetchone()
                if previous is None or previous[0] != prompt:
                    conn.execute(
                        "INSERT INTO seeds(project, prompt, submitted_at) VALUES (?, ?, ?)",
                        (self.project, prompt, time.time()),
                    )
        except (OSError, sqlite3.Error) as exc:
            raise SeedHistoryError(
                "Could not save local seed history; prompt has not been submitted."
            ) from exc


class HistoryCursor:
    def __init__(self, entries: list[str]) -> None:
        self.entries = entries
        self.index = -1
        self.edits: dict[int, str] = {}

    def move(self, current: str, direction: int) -> str | None:
        target = self.index + direction
        if target < -1 or target >= len(self.entries):
            return None
        self.edits[self.index] = current
        self.index = target
        return self.edits.get(target, "" if target == -1 else self.entries[target])
