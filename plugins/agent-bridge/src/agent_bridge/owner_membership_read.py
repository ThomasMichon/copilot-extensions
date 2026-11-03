"""Private read-only SQLite work budgets, never installed on canonical connections."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path

from .owner_membership_records import records
from .session_observation_contract import ObservationError, text

MAX_VM_STEPS = 100_000
PROGRESS_INTERVAL = 100
MAX_READ_SECONDS = 0.05
MAX_VALUE_BYTES = 128 * 1024


@dataclass
class ReadBudget:
    max_steps: int = MAX_VM_STEPS
    seconds: float = MAX_READ_SECONDS
    steps: int = 0
    exhausted: bool = False
    started: float = field(default_factory=time.monotonic)

    def check(self) -> None:
        if self.steps > self.max_steps or time.monotonic() - self.started > self.seconds:
            self.exhausted = True
            raise ObservationError(
                "capture-budget-exhausted",
                "membership read exceeded its private work budget",
            )

    def progress(self) -> int:
        self.steps += PROGRESS_INTERVAL
        try:
            self.check()
        except ObservationError:
            return 1
        return 0


@dataclass(frozen=True)
class ReadSnapshot:
    authority: str
    file_id: tuple[int, int]
    states: dict[str, dict[str, bytes] | ObservationError]
    steps: int


def file_id(path: Path) -> tuple[int, int]:
    try:
        stat = path.stat()
    except OSError as exc:
        raise ObservationError("source-unavailable", "membership database is unavailable") from exc
    return stat.st_dev, stat.st_ino


def snapshot(
    path: Path,
    owners: tuple[str, ...],
    *,
    authority: str | None = None,
    expected_file: tuple[int, int] | None = None,
    budget: ReadBudget | None = None,
) -> ReadSnapshot:
    """Bound all selected owners' physical VM work, including unknown-owner scans."""
    budget = budget if budget is not None else ReadBudget()
    before = file_id(path)
    if expected_file is not None and before != expected_file:
        raise ObservationError("source-changed", "membership database file changed")
    conn = None
    try:
        conn = sqlite3.connect(path.absolute().as_uri() + "?mode=ro", uri=True, timeout=0)
        conn.row_factory = sqlite3.Row
        conn.set_progress_handler(budget.progress, PROGRESS_INTERVAL)
        if hasattr(conn, "setlimit"):
            conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        rows = conn.execute(
            "SELECT id FROM remote_acp_relationship_authority WHERE singleton=1",
        ).fetchall()
        if len(rows) != 1:
            raise ObservationError("source-unavailable", "membership authority is unknown")
        actual = text(rows[0]["id"], "hosting authority")
        if authority is not None and actual != authority:
            raise ObservationError("source-changed", "membership database authority changed")
        states: dict[str, dict[str, bytes] | ObservationError] = {}
        for owner in owners:
            budget.check()
            try:
                states[owner] = records(conn, owner, check=budget.check)
            except ObservationError as exc:
                if exc.code == "capture-budget-exhausted":
                    raise
                states[owner] = exc
        budget.check()
        if file_id(path) != before:
            raise ObservationError("source-changed", "membership database changed during capture")
        return ReadSnapshot(actual, before, states, budget.steps)
    except sqlite3.Error as exc:
        code = "capture-budget-exhausted" if budget.exhausted else "source-unavailable"
        raise ObservationError(code, "membership private read is unavailable") from exc
    finally:
        if conn is not None:
            conn.close()
