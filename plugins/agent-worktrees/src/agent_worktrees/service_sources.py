"""Resolve service sources without treating a bare anchor as a checkout."""

from __future__ import annotations

import hashlib
from pathlib import Path

from . import bare_anchor_files, bare_anchor_sync, config as cfg, git_ops, output


def _git(repo: Path, *args: str):
    return git_ops.git(*args, cwd=repo, isolated_repository=True, timeout=180)


def ensure_current(repo: Path, config: cfg.Config) -> None:
    remote = config.default_repo.remote or "origin"
    branch = config.default_repo.default_branch or "master"
    output.info(f"Syncing anchor repo ({remote}/{branch})...")
    if bare_anchor_sync.is_bare(repo):
        state, detail = bare_anchor_sync.sync_bare_anchor(str(repo), branch, remote=remote)
        files = bare_anchor_files.status_of(detail)
        if state != "synced" or (
            files is not None and files not in bare_anchor_files.QUIET_STATUSES
        ):
            raise RuntimeError(f"Cannot sync service source: {state}: {detail}")
        return

    current = _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
    if current != branch:
        raise RuntimeError(f"Service source is on {current!r}, not configured branch {branch!r}")
    git_ops.fetch(remote, cwd=repo, isolated_repository=True)
    _git(repo, "merge", "--ff-only", f"refs/remotes/{remote}/{branch}")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    upstream = _git(repo, "rev-parse", f"refs/remotes/{remote}/{branch}").stdout.strip()
    if head != upstream:
        raise RuntimeError("Service source has local commits not present on its upstream branch")


def resolve(repo: Path, config: cfg.Config, *, deploy: bool = False) -> Path:
    """Retain a commit-pinned source for bare layouts, including redirectors.

    Never select a caller's active worktree. Detached checkouts are retained
    because service installers may record their source path for later use.
    """
    if deploy:
        ensure_current(repo, config)
    if not bare_anchor_sync.is_bare(repo):
        return repo

    branch = config.default_repo.default_branch or "master"
    commit = _git(repo, "rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}").stdout.strip()
    identity = hashlib.sha256(str(repo.resolve()).encode("utf-8")).hexdigest()[:16]
    source = cfg.project_dir() / "service-sources" / identity / commit
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        _git(
            repo, "-c", "core.hooksPath=", "worktree", "add",
            "--detach", "--lock", str(source), commit,
        )

    if source.is_symlink():
        raise RuntimeError(f"Service source checkout is a symbolic link: {source}")
    actual = _git(source, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
    changes = _git(source, "status", "--porcelain", "--untracked-files=all").stdout
    if actual != commit or changes.strip():
        raise RuntimeError(f"Service source checkout changed; refusing to overwrite or deploy: {source}")
    return source
