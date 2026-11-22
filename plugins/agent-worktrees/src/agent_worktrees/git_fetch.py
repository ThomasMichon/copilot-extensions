"""Explicit fetch transport shared by ordinary and creation-scoped fetches."""

from __future__ import annotations

import subprocess
import time
from contextlib import nullcontext
from pathlib import Path
from typing import TypedDict

from .git_errors import GitError
from .git_acquisition_budget import NETWORK_HARD_TIMEOUT, NETWORK_SOFT_TIMEOUT, acquisition_budget
from .git_state_types import _GitHookOptions


class _CompletionOptions(TypedDict, total=False):
    kill_tree: bool


def _fetch_refspecs(
    remote: str, *, cwd: str | Path, refspec: tuple[str, ...],
    timeout: float | None = NETWORK_HARD_TIMEOUT,
    isolated_repository: bool = False, no_hooks: bool = False,
    ignore_refmaps: bool = False,
) -> None:
    from . import git_ops, tracking_write

    refmap = ["--refmap="] if refspec and ignore_refmaps else []
    hook_options: _GitHookOptions = {"no_hooks": True} if no_hooks else {}
    hard_timeout: float | None = NETWORK_HARD_TIMEOUT if timeout is None else min(timeout, NETWORK_HARD_TIMEOUT)
    if tracking_write.foreground_completion_active() and timeout is None:
        hard_timeout = None
    completion_options: _CompletionOptions = {"kill_tree": True} if hard_timeout is None else {}
    started = time.monotonic()
    try:
        with acquisition_budget(started + hard_timeout) if hard_timeout is not None else nullcontext():
            git_ops.git(
                *git_ops._auth_config_args(remote, cwd=cwd), "fetch", remote, "--progress", *refmap, *refspec,
                cwd=cwd, timeout=hard_timeout,
                soft_timeout=NETWORK_SOFT_TIMEOUT if hard_timeout is not None else None,
                **completion_options,
                isolated_repository=isolated_repository,
                auth_acquisition=True, **hook_options,
            )
    except (subprocess.TimeoutExpired, TimeoutError) as exc:
        elapsed = exc.timeout if isinstance(exc, subprocess.TimeoutExpired) else time.monotonic() - started
        raise GitError(
            ["git", "fetch", remote, "--progress", *refmap, *refspec], 124,
            f"fetch from '{remote}' timed out after {elapsed:g}s "
            "(hard timeout; output does not renew the absolute deadline)",
        ) from exc
