"""Local Git admission for a retained, unrecorded companion branch."""

from __future__ import annotations

from pathlib import Path
import re

from . import git_ops


def require_head(head: str) -> None:
    """Require the exact commit shape emitted by retained-branch inspection."""
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", head):
        raise ValueError("retained companion adoption requires an exact commit")


def adopt(anchor: str | Path, path: str, branch: str, head: str) -> None:
    """Attach the inspected branch without forcing a registration or moving a ref."""
    observed_head = git_ops.git(
        "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}", cwd=anchor,
    ).stdout.strip()
    if observed_head != head:
        raise ValueError("retained companion branch changed; inspect again")
    # Long-path support is invocation-local; Git still refuses occupied targets.
    git_ops.git(
        "-c", "core.longpaths=true", "worktree", "add", path, branch, cwd=anchor,
    )
