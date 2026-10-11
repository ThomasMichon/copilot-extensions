"""Checkout identity attached to a successful explicit finalization."""

from __future__ import annotations

import re

from . import git_ops, tracking


def snapshot(path: str) -> dict[str, str]:
    head = git_ops.git("rev-parse", "--verify", "HEAD", cwd=path).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", head):
        raise ValueError("Cannot record finalized checkout: invalid HEAD")
    branch = git_ops.current_branch(path)
    return {"head": head, "branch": branch or ""}


def matches(record: tracking.WorktreeRecord) -> bool:
    stamp = record.finalized_checkout
    if (
        record.status != "finalized"
        or not isinstance(stamp, dict)
        or set(stamp) != {"head", "branch"}
        or not isinstance(stamp["head"], str)
        or not re.fullmatch(r"[0-9a-f]{40,64}", stamp["head"])
        or not isinstance(stamp["branch"], str)
    ):
        return False
    try:
        return snapshot(record.worktree_path) == stamp
    except (git_ops.GitError, OSError, ValueError):
        return False
