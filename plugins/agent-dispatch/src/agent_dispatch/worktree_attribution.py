"""Cross-task worktree-attribution guard for a reused dispatch worktree.

A concurrency-1 exclusive lane's worktree is legitimately reused sequentially
across many occurrences/tasks, so a worktree's tracking record
``dispatch_attempt.task_id`` reflects only whichever task most recently
*created* it -- not necessarily the task now attempting to resume it. A
carried reservation whose worktree has since been reassigned to a
*different* task is exactly as unusable as one that is outright missing:
observed live, a task's reservation kept retrying a carried session that had,
in the meantime, become a different task's own worktree, failing identically
every attempt with an unresolvable "unknown" liveness verdict instead of ever
recovering. Extracted from :mod:`agent_dispatch.embody` to keep that module
under its line cap.
"""

from __future__ import annotations


def foreign_task_id(row: dict, task_id: str) -> str | None:
    """The worktree row's current ``dispatch_attempt.task_id`` when it names a
    task other than ``task_id``, else ``None`` (still ours, or untagged)."""
    allocation = row.get("dispatch_attempt")
    owner_task_id = allocation.get("task_id") if isinstance(allocation, dict) else None
    if isinstance(owner_task_id, str) and owner_task_id and owner_task_id != task_id:
        return owner_task_id
    return None
