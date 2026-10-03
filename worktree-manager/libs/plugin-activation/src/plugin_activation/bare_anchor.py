"""A registered project root that is a bare worktree-class anchor."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path


def git_root(root: Path, git: Callable[..., str], same_file: Callable[[Path, Path], bool]) -> Path:
    """The repository root git reports for ``root``.

    A bare anchor (``core.bare`` with its linked worktrees elsewhere -- the
    worktree-class layout) has no work tree to report, so ``--show-toplevel``
    fails there; it is still ``root`` when git confirms a bare repository whose
    git directory is exactly ``root`` or ``root/.git``. Anything else -- a
    redirected gitfile, a directory inside another repository -- re-raises the
    original failure.
    """
    try:
        return Path(git(root, "rev-parse", "--show-toplevel")).resolve(strict=True)
    except subprocess.CalledProcessError:
        if git(root, "rev-parse", "--is-bare-repository") == "true":
            git_dir = Path(git(root, "rev-parse", "--absolute-git-dir")).resolve(strict=True)
            if same_file(git_dir, root) or same_file(git_dir, root / ".git"):
                return root.resolve(strict=True)
        raise
