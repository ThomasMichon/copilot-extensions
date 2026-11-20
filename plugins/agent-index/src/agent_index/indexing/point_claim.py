"""Whole pinned queue snapshots retained with acquired component work."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from agent_index.indexing.change_hints import _identity, _keys
from agent_index.indexing.point_hints import PointChangeHints

if TYPE_CHECKING:
    import sqlite3
    from collections.abc import Iterator

    from agent_index.indexing.task_store import TaskRecord


@dataclass(frozen=True)
class PendingPointClaim:
    """A retained snapshot, not proof of coverage or current queue eligibility."""

    task_id: str
    point_claim_id: str
    hints: PointChangeHints

    def __post_init__(self) -> None:
        _identity(self.task_id, "task_id")
        if (
            not isinstance(self.point_claim_id, str)
            or len(self.point_claim_id) != 32
            or any(char not in "0123456789abcdef" for char in self.point_claim_id)
        ):
            raise ValueError("Point claim identity must be a retained queue pin")
        if type(self.hints) is not PointChangeHints:
            raise ValueError("Pending point claims require complete typed v2 hints")

    @classmethod
    def from_task(cls, task: TaskRecord) -> PendingPointClaim:
        """Capture a pinned parked task without acknowledging or executing it."""
        from agent_index.indexing.task_store import TaskStatus

        if (
            task.status != TaskStatus.QUEUED_HINTS_V2
            or task.point_claim_id is None
            or type(task.change_hints) is not PointChangeHints
        ):
            raise ValueError("Pending point claims require an immutable parked queue pin")
        return cls(task.id, task.point_claim_id, task.change_hints)

    def to_dict(self) -> dict[str, Any]:
        """Retain every target version, parent and correlation with its queue pin."""
        return {
            "version": 1, "task_id": self.task_id, "point_claim_id": self.point_claim_id,
            "hints": self.hints.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: object) -> PendingPointClaim:
        """Refuse incomplete or unknown retained claim representations."""
        data = _keys(value, {"version", "task_id", "point_claim_id", "hints"})
        if type(data["version"]) is not int or data["version"] != 1:
            raise ValueError("Unsupported pending point claim version")
        return cls(
            data["task_id"], data["point_claim_id"], PointChangeHints.from_dict(data["hints"]),
        )


def protect_point_parents(conn: sqlite3.Connection) -> None:
    """Keep older journal writers from replacing or clearing a bound parent."""
    for action, condition in (
        ("DELETE", "json_extract(old.payload, '$.version') = 9"),
        ("UPDATE", "json_extract(old.payload, '$.version') = 9"),
        ("INSERT", "EXISTS (SELECT 1 FROM pending_crawls WHERE source = new.source "
         "AND json_extract(payload, '$.version') = 9)"),
    ):
        conn.execute(
            f"CREATE TRIGGER IF NOT EXISTS pending_point_claim_{action.lower()} "
            f"BEFORE {action} ON pending_crawls WHEN {condition} BEGIN "
            "SELECT RAISE(ABORT, 'Point-claim pending completion is not supported'); END",
        )


@contextmanager
def _point_completion_transition(conn: sqlite3.Connection, fence: str) -> Iterator[None]:
    """Keep the legacy fence installed except inside one owning writer transaction."""
    if not conn.in_transaction or fence not in {
        "tasks_point_execution_unsupported_update", "pending_point_claim_delete",
    }:
        raise ValueError("Point completion requires its owning transaction and exact fence")
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'trigger' AND name = ?", (fence,),
    ).fetchone()
    if row is None or not isinstance(row[0], str):
        raise ValueError("Point completion fence is missing")
    # SQLite rolls back both the schema and data on interruption before commit.
    conn.execute(f"DROP TRIGGER {fence}")
    try:
        yield
    finally:
        conn.execute(row[0])
