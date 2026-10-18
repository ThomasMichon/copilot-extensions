"""Resolve service sources without treating a bare anchor as a checkout."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from . import bare_anchor_files, bare_anchor_sync, config as cfg, git_ops, output


def _git(repo: Path, *args: str):
    return git_ops.git(*args, cwd=repo, isolated_repository=True, no_hooks=True, timeout=180)


def ensure_current(repo: Path, config: cfg.Config) -> None:
    remote = config.default_repo.remote or "origin"
    branch = config.default_repo.default_branch or "master"
    output.info(f"Syncing anchor repo ({remote}/{branch})...")
    if bare_anchor_sync.is_bare(repo):
        state, detail = bare_anchor_sync.sync_bare_anchor(
            str(repo), branch, remote=remote, no_hooks=True,
        )
        files = bare_anchor_files.status_of(detail)
        if state != "synced" or (
            files is not None and files not in bare_anchor_files.QUIET_STATUSES
        ):
            raise RuntimeError(f"Cannot sync service source: {state}: {detail}")
        return

    current = _git(repo, "symbolic-ref", "--short", "HEAD").stdout.strip()
    if current != branch:
        raise RuntimeError(f"Service source is on {current!r}, not configured branch {branch!r}")
    git_ops.fetch(remote, cwd=repo, isolated_repository=True, no_hooks=True)
    _git(repo, "merge", "--ff-only", f"refs/remotes/{remote}/{branch}")
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    upstream = _git(repo, "rev-parse", f"refs/remotes/{remote}/{branch}").stdout.strip()
    if head != upstream:
        raise RuntimeError("Service source has local commits not present on its upstream branch")


def resolve(repo: Path, config: cfg.Config, *, deploy: bool = False) -> Path:
    """Retain a commit-pinned source for bare layouts, including redirectors.

    Never select a caller's active worktree. Detached checkouts are retained
    because service installers may record their source path for later use.

    ``repo`` is normalized to its actual anchor first: the installed CLI may
    be invoked from a linked worktree of a pure bare anchor, and
    :func:`git_ops.resolve_to_anchor` is what recognizes that layout (a bare
    anchor's common dir points at the anchor directly, not an
    ``<anchor>/.git`` subdirectory) -- otherwise this served the still-active,
    unmerged worktree instead of the reviewed anchor for both synchronization
    and source classification -- #7088.
    """
    repo = git_ops.resolve_to_anchor(repo)
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
        try:
            _git(repo, "worktree", "add", "--detach", "--lock", str(source), commit)
        except git_ops.GitError as exc:
            if not source.is_dir():
                raise
            logging.getLogger(__name__).warning(
                "Service checkout creation failed; validating an existing checkout: %s", exc,
            )

    if source.is_symlink() or not source.is_dir():
        raise RuntimeError(f"Service source path is not a real checkout directory: {source}")
    owner = _git(source, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    expected_owner = _git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir").stdout.strip()
    if Path(owner).resolve() != Path(expected_owner).resolve():
        raise RuntimeError(f"Service source checkout belongs to another anchor: {source}")
    actual = _git(source, "rev-parse", "--verify", "HEAD^{commit}").stdout.strip()
    changes = _git(source, "status", "--porcelain", "--untracked-files=all").stdout
    if actual != commit or changes.strip():
        raise RuntimeError(f"Service source checkout changed; refusing to overwrite or deploy: {source}")
    return source
