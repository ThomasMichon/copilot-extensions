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
            if sidecar.exists():
                protect(sidecar)

    def entries(self) -> list[str]:
        try:
            reject_links(self.path)
            if not self.path.exists():
                return []
            self._protect()
            with closing(sqlite3.connect(self.path, timeout=5)) as conn:
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
                conn.execute(
                    "CREATE TABLE IF NOT EXISTS seeds "
                    "(id INTEGER PRIMARY KEY, project TEXT NOT NULL, "
                    "prompt TEXT NOT NULL, submitted_at REAL NOT NULL)"
                )
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
