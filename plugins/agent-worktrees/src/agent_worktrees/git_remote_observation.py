"""Observe pinned refs with the publication's selected transport and credentials."""

from __future__ import annotations

from pathlib import Path
import uuid

from . import git_acquisition_budget, git_ops


def refs(url: str, patterns: tuple[str, ...], *, cwd: str | Path, timeout: float) -> set[str]:
    from .git_push_transport import _auth_attempts, _destination, _url_aliases

    pinned = _destination.get()
    settings = tuple((key, value) for key, value in (pinned[2] if pinned else ())
                     if key in ("uploadpack", "proxy", "proxyauthmethod", "vcs"))
    remote = "agent-worktrees-observe-" + uuid.uuid4().hex
    alias = remote + "-url"
    auth = git_ops._auth_config_args_for_url(url)
    with _url_aliases([(url, alias)]) as aliases:
        for prefix in _auth_attempts(auth):
            result = git_ops.git(
                *prefix, *aliases, "-c", f"remote.{remote}.url={alias}",
                *[argument for key, value in settings for argument in (
                    "-c", f"remote.{remote}.{key}={value}",
                )],
                "ls-remote", remote, *patterns, cwd=cwd, check=False,
                timeout=git_acquisition_budget.remaining_timeout(timeout), kill_tree=True,
                isolated_repository=True,
            )
            if result.returncode == 0:
                return {row[1] for line in result.stdout.splitlines() if len(row := line.split()) == 2}
    raise OSError("Cannot observe the pinned push destination before publication.")
