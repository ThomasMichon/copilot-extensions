"""Checkout identity attached to a successful explicit finalization."""

from __future__ import annotations

import re
import subprocess

from . import git_ops, tracking


def snapshot(path: str) -> dict[str, str]:
    result = git_ops.git(
        "status", "--porcelain=v2", "--branch", "--untracked-files=normal",
        cwd=path, timeout=5,
    )
    lines = result.stdout.splitlines()
    head = next((line.removeprefix("# branch.oid ") for line in lines if line.startswith("# branch.oid ")), "")
    branch = next((line.removeprefix("# branch.head ") for line in lines if line.startswith("# branch.head ")), "")
    if not re.fullmatch(r"[0-9a-f]{40,64}", head) or not branch:
        raise ValueError("Cannot record finalized checkout: invalid HEAD")
    if any(line and not line.startswith("# ") for line in lines):
        raise ValueError("Cannot record finalized checkout: uncommitted content")
    return {"head": head, "branch": "" if branch == "(detached)" else branch}


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
    except (git_ops.GitError, OSError, ValueError, subprocess.TimeoutExpired):
        return False


def optional_snapshot(path: str) -> dict[str, str] | None:
    try:
        return snapshot(path)
    except (git_ops.GitError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        from . import output
        output.warn(f"Finalized checkout display could not be recorded: {exc}")
        return None
