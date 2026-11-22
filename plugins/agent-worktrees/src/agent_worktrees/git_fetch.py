"""Explicit fetch transport shared by ordinary and creation-scoped fetches."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .git_errors import GitError
from .git_acquisition_budget import NETWORK_HARD_TIMEOUT, NETWORK_SOFT_TIMEOUT
from .git_state_types import _GitHookOptions


def _fetch_refspecs(
    remote: str, *, cwd: str | Path, refspec: tuple[str, ...],
    timeout: float | None = NETWORK_HARD_TIMEOUT,
    isolated_repository: bool = False, no_hooks: bool = False,
    ignore_refmaps: bool = False,
) -> None:
    from . import git_ops

    refmap = ["--refmap="] if refspec and ignore_refmaps else []
    hook_options: _GitHookOptions = {"no_hooks": True} if no_hooks else {}
    hard_timeout = NETWORK_HARD_TIMEOUT if timeout is None else min(timeout, NETWORK_HARD_TIMEOUT)
    try:
        git_ops.git(
            *git_ops._auth_config_args(remote, cwd=cwd), "fetch", remote, "--progress", *refmap, *refspec,
            cwd=cwd, timeout=hard_timeout, soft_timeout=NETWORK_SOFT_TIMEOUT,
            isolated_repository=isolated_repository,
            auth_acquisition=True, **hook_options,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(
            ["git", "fetch", remote, "--progress", *refmap, *refspec], 124,
            f"fetch from '{remote}' timed out after {exc.timeout}s "
            "(hard timeout; output does not renew the absolute deadline)",
        ) from exc
