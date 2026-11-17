"""Explicit fetch transport shared by ordinary and creation-scoped fetches."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .git_errors import GitError
from .git_state_types import _GitHookOptions


def _fetch_refspecs(
    remote: str, *, cwd: str | Path, refspec: tuple[str, ...],
    timeout: float | None = 30.0,
    isolated_repository: bool = False, no_hooks: bool = False,
    ignore_refmaps: bool = False,
) -> None:
    from . import git_ops

    refmap = ["--refmap="] if refspec and ignore_refmaps else []
    hook_options: _GitHookOptions = {"no_hooks": True} if no_hooks else {}
    try:
        git_ops.git(
            *git_ops._auth_config_args(remote, cwd=cwd), "fetch", remote, "--quiet", *refmap, *refspec,
            cwd=cwd, timeout=timeout, isolated_repository=isolated_repository,
            auth_acquisition=True, **hook_options,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            ["git", "fetch", remote, "--quiet", *refmap, *refspec], 124,
            f"fetch from '{remote}' timed out after {timeout}s "
            "(remote unreachable?)",
        ) from exc
