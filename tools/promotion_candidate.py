"""Deterministic candidate identity and exact-ref retry admission."""
from __future__ import annotations

import os
import time
from collections.abc import Callable
from pathlib import Path


class CandidateIdentityError(RuntimeError):
    pass


def candidate_clock(value: str | None) -> tuple[str, str]:
    try:
        clock = time.gmtime() if value is None else time.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        canonical = time.strftime("%Y-%m-%dT%H:%M:%SZ", clock)
        if value is not None and canonical != value:
            raise ValueError("noncanonical UTC timestamp")
        return canonical, time.strftime("%Y%m%d%H%M%S", clock)
    except ValueError as exc:
        raise CandidateIdentityError("invalid immutable promotion preparation time") from exc


def candidate_environment(value: str | None) -> dict[str, str] | None:
    if value is None:
        return None
    canonical, _ = candidate_clock(value)
    return {
        **os.environ,
        "GIT_AUTHOR_DATE": canonical, "GIT_COMMITTER_DATE": canonical,
        "GIT_AUTHOR_NAME": "github-actions[bot]", "GIT_COMMITTER_NAME": "github-actions[bot]",
        "GIT_AUTHOR_EMAIL": "github-actions[bot]@users.noreply.github.com",
        "GIT_COMMITTER_EMAIL": "github-actions[bot]@users.noreply.github.com",
    }


def push_candidate(
    commit: str, branch: str, *, cwd: Path, git: Callable[..., str],
) -> None:
    ref = f"refs/heads/{branch}"
    remote = git(["ls-remote", "--heads", "origin", ref], cwd=cwd)
    if remote:
        if remote.split() != [commit, ref]:
            raise CandidateIdentityError("existing ref does not match the recomputed candidate identity")
        return
    git(["push", "origin", f"{commit}:{ref}"], cwd=cwd)
