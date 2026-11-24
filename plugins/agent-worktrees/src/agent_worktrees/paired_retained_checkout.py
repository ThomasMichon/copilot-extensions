"""Local Git admission for a retained, unrecorded companion branch."""

from __future__ import annotations

from pathlib import Path
import re
import os

from . import git_ops


def require_local_checkout_support(anchor: str | Path) -> None:
    """Refuse an unsupported Git before entering local checkout publication."""
    git_ops.git("--no-lazy-fetch", "rev-parse", "--git-common-dir", cwd=anchor)


def require_head(head: str) -> None:
    """Require the exact commit shape emitted by retained-branch inspection."""
    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", head):
        raise ValueError("retained companion adoption requires an exact commit")


def adopt(anchor: str | Path, path: str, branch: str, head: str) -> None:
    """Attach the inspected branch without forcing a registration or moving a ref."""
    observed_head = git_ops.git(
        "--no-lazy-fetch", "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}", cwd=anchor,
    ).stdout.strip()
    if observed_head != head:
        raise ValueError("retained companion branch changed; inspect again")
    # Long-path support is invocation-local; Git still refuses occupied targets.
    git_ops.git(
        "--no-lazy-fetch", "-c", "core.longpaths=true", "worktree", "add", path, branch, cwd=anchor,
    )


def create(anchor: str | Path, path: str, branch: str, start_point: str) -> None:
    """Create from prepared local objects without lazy network I/O under record locks."""
    git_ops.git(
        "--no-lazy-fetch", "-c", "core.longpaths=true", "worktree", "add",
        path, "-b", branch, start_point, "--quiet", cwd=anchor,
    )


def restore(anchor: str | Path, path: str, branch: str) -> None:
    """Reuse only a missing registration; do not fetch or override a locked checkout."""
    retained = require_restore_registration(anchor, path, branch)
    git_ops.git(
        "--no-lazy-fetch", "-c", "core.longpaths=true", "worktree", "add",
        *(["--force"] if retained else []), path, branch, cwd=anchor,
    )


def require_restore_registration(anchor: str | Path, path: str, branch: str) -> bool:
    """Permit only the exact missing original registration, never another branch holder."""
    def normalized(value: str) -> str:
        return os.path.normcase(os.path.normpath(str(Path(value).resolve())))

    raw = git_ops.git(
        "--no-lazy-fetch", "worktree", "list", "--porcelain", "-z",
        cwd=anchor, timeout=10, kill_tree=True,
    ).stdout
    expected, retained = normalized(path), False
    for record in raw.split("\0\0"):
        fields = record.split("\0")
        location = next((value[9:] for value in fields if value.startswith("worktree ")), None)
        if not location:
            continue
        selected = next((value[7:] for value in fields if value.startswith("branch ")), None)
        same_path, same_branch = normalized(location) == expected, selected == f"refs/heads/{branch}"
        if same_branch and not same_path:
            raise ValueError("retained companion branch is registered at another checkout; preserve its owner")
        if same_path:
            if not same_branch or any(value == "locked" or value.startswith("locked ") for value in fields):
                raise ValueError("original companion registration changed or is locked")
            retained = True
    return retained
