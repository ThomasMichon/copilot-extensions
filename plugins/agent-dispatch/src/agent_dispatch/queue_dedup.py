"""Logical live-dedup selection shared by serialized creation and exact reads."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable

from .queue_common import Task, _TASK_SELECT
from .queue_records import TaskError

_ACTIVE_DEDUP_PREDICATE = (
    "dedup_key IS NOT NULL AND status IN ('proposed','queued','claimed','started','suspended')"
)


def _live_dedup_rows(
    conn: sqlite3.Connection, selector: str, params: tuple[str, ...]
) -> list[sqlite3.Row]:
    # Keep disjoint branches in one snapshot, using the existing indexes rather
    # than scanning completed lane history through a combined OR predicate.
    return conn.execute(
        f"SELECT {_TASK_SELECT} FROM tasks INDEXED BY idx_tasks_dedup "
        f"WHERE {selector} AND {_ACTIVE_DEDUP_PREDICATE} "
        "UNION ALL "
        f"SELECT {_TASK_SELECT} FROM tasks INDEXED BY idx_tasks_status "
        f"WHERE {selector} AND status = 'submitted' AND require_verification = 1",
        (*params, *params),
    ).fetchall()


class DedupConflictError(TaskError):
    """More than one durable task occupies a logical repo/key identity."""

    def __init__(self, repo: str, dedup_key: str, task_ids: list[str]) -> None:
        self.repo = repo
        self.dedup_key = dedup_key
        self.task_ids = sorted(task_ids)
        super().__init__(
            f"live dedup conflict for repo {repo!r}, key {dedup_key!r}: "
            f"multiple task candidates {self.task_ids!r}"
        )

    def detail(self) -> dict[str, object]:
        return {
            "code": "dedup_conflict",
            "message": str(self),
            "repo": self.repo,
            "dedup_key": self.dedup_key,
            "task_ids": self.task_ids,
        }


def find_live_dedup_task(
    conn: sqlite3.Connection,
    repo: str,
    dedup_key: str,
    *,
    reviewer_target_for_key: Callable[[str | None], str | None] | None = None,
) -> Task | None:
    """Return the sole occupant or report a local conflict without changing rows.

    Creation calls this under its BEGIN IMMEDIATE write transaction, so checking
    pending submissions and inserting a new task remain one serialized operation.
    The five-state physical index is a supplementary, intentionally weaker guard.
    """
    rows = _live_dedup_rows(
        conn,
        "repo = ? AND dedup_key = ?",
        (repo, dedup_key),
    )
    candidates = {row["id"]: row for row in rows}
    target = reviewer_target_for_key(dedup_key) if reviewer_target_for_key else None
    if target is not None and reviewer_target_for_key is not None:
        aliases = _live_dedup_rows(
            conn,
            "repo = ? "
            "AND source = 'recipe' AND origin_ref = 'reviewer' "
            "AND dedup_key LIKE 'recipe:reviewer:%'",
            (repo,),
        )
        candidates.update(
            (row["id"], row)
            for row in aliases
            if reviewer_target_for_key(row["dedup_key"]) == target
        )
    if len(candidates) > 1:
        raise DedupConflictError(repo, dedup_key, list(candidates))
    return Task._from_row(next(iter(candidates.values()))) if candidates else None
