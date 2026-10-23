"""Required checkout preparation before an owner's destructive restore."""

from __future__ import annotations

from collections.abc import Callable

from . import permissions, sessions, worktree_trust
from .config import Config
from .execution_spaces import ExecutionSpaceError
from .tracking import WorktreeRecord


def restore_for_launch(
    record: WorktreeRecord, *, perform_remux: Callable[..., dict], config: Config | None = None,
) -> dict:
    """Never reclaim an existing owner before required trust can be established."""
    try:
        worktree_trust.ensure_record_trusted(record, config)
    except (permissions.FolderTrustError, ExecutionSpaceError) as exc:
        return {"ok": False, "reason": str(exc), "action": "failed", "requires_resume": False}
    return perform_remux(
        worktree_id=record.worktree_id,
        session_id=sessions.find_latest_session_id_fast(record.worktree_path, record.sessions),
        worktree_path=record.worktree_path,
        force_sudo=None,
        apply_windows=True,
    )
