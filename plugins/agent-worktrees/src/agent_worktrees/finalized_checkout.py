"""Checkout identity attached to a successful explicit finalization."""

from __future__ import annotations

import re
import subprocess

from . import git_ops, tracking


def snapshot(path: str) -> dict[str, str]:
    head = git_ops.git("rev-parse", "--verify", "HEAD", cwd=path, timeout=5).stdout.strip()
    if not re.fullmatch(r"[0-9a-f]{40,64}", head):
        raise ValueError("Cannot record finalized checkout: invalid HEAD")
    branch = git_ops.git("rev-parse", "--abbrev-ref", "HEAD", cwd=path, timeout=5).stdout.strip()
    return {"head": head, "branch": "" if branch == "HEAD" else branch}


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
        if snapshot(record.worktree_path) != stamp:
            return False
        return not git_ops.git(
            "status", "--porcelain", "--untracked-files=normal",
            cwd=record.worktree_path, timeout=5,
        ).stdout.strip()
    except (git_ops.GitError, OSError, ValueError, subprocess.TimeoutExpired):
        return False
