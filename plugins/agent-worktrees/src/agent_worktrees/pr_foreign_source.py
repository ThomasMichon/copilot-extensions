"""Resolve source push remotes separately from the target's fetch policy."""

from __future__ import annotations

from pathlib import Path

from . import pr_publication_checks as checks
from .config import RepoConfig
from .pr_worker_config import matches_remote


def remotes(root: Path, preferred: str) -> dict[str, tuple[str, ...]]:
    return {
        remote: tuple(checks._git(root, "remote", "get-url", "--push", "--all", remote).splitlines())
        for remote in sorted(checks._git(root, "remote").splitlines(), key=lambda value: (value != preferred, value))
    }


def binding(root: Path, repository: str, repo: RepoConfig) -> dict[str, str]:
    for remote, urls in remotes(root, repo.remote).items():
        if len(urls) == 1 and matches_remote(urls[0], repository, repo.pr, str(root)):
            return {"remote": remote, "url": urls[0]}
    raise checks.CheckError("source push destination does not match the selected source repository")
