"""Keep a bare anchor's materialized files in step with its branch ref.

A worktree-class anchor is often a former ordinary clone converted to
``core.bare=true``: it keeps its index and checked-out files beside the git
dir while every real edit happens in linked worktrees. Moving only the branch
ref (:func:`bare_anchor_sync.sync_bare_anchor`) leaves those files stale. This
module checks, before the ref moves, that the files can be advanced safely,
then performs the same two-tree ``read-tree -m -u`` a checkout would run
after the ref has moved. Linked worktrees are never touched: every command
targets the anchor's own git dir, index, and work tree explicitly.

Split out of ``bare_anchor_sync.py`` to keep each module within its size cap.
"""

from __future__ import annotations

import os
from pathlib import Path

from . import git_ops

REFRESHED = "refreshed"
NONE = "none"
CURRENT = "current"
STALE_INDEX = "stale-index"
DIRTY = "dirty"
COLLISION = "collision"
INDEX_LOCKED = "index-locked"
REFRESH_FAILED = "refresh-failed"

#: Statuses that need no operator attention; anything else is a warning.
QUIET_STATUSES = frozenset({REFRESHED, NONE, CURRENT})


def _git(path: str, *args: str):
    """Run git in the anchor, ignoring inherited repository selectors.

    An ambient ``GIT_DIR``/``GIT_WORK_TREE``/``GIT_INDEX_FILE`` (for example
    from a hook or a parent git process) would otherwise redirect these
    probes and the ``read-tree -u`` refresh to an unrelated repository.
    """
    return git_ops.git(*args, cwd=path, check=False, isolated_repository=True)


def _git_dir(path: str) -> Path | None:
    out = _git(path, "rev-parse", "--absolute-git-dir")
    text = out.stdout.strip()
    return Path(text) if out.returncode == 0 and text else None


def _work(path: str, git_dir: Path, *args: str):
    """Run git against the anchor's own index and work tree.

    ``core.bare=false`` plus an explicit ``--git-dir``/``--work-tree`` lets
    index/work-tree commands run on a bare anchor, while
    ``--no-optional-locks`` keeps the read-only checks from opportunistically
    rewriting the index.
    """
    return _git(
        path, "--no-optional-locks", "-c", "core.bare=false",
        "--git-dir", str(git_dir), "--work-tree", path, *args,
    )


def _tail(result) -> str:
    lines = [ln.strip() for ln in (result.stderr or result.stdout or "").splitlines() if ln.strip()]
    text = lines[-1] if lines else f"exit {result.returncode}"
    return text.replace(";", ",")[:120]


def current_status(path: str, head: str) -> str:
    """Status for an anchor whose ref did not move: is its index at ``head``?"""
    git_dir = _git_dir(path)
    if git_dir is None or not (git_dir / "index").is_file():
        return NONE
    if _work(path, git_dir, "diff-index", "--cached", "--quiet", head).returncode != 0:
        return STALE_INDEX
    return CURRENT


def precheck(path: str, old: str, new: str) -> str | None:
    """Return a refusal status, or ``None`` when the files can advance safely.

    Run BEFORE the ref moves; any refusal leaves the ref where it is. A pure
    bare repo (no index) has nothing to refresh and always passes.
    """
    git_dir = _git_dir(path)
    if git_dir is None or not (git_dir / "index").is_file():
        return None
    # Another git process owns the index; every check below would misreport.
    if (git_dir / "index.lock").exists():
        return INDEX_LOCKED
    if _work(path, git_dir, "diff-index", "--cached", "--quiet", old).returncode != 0:
        return STALE_INDEX
    # Re-stat the index first: plumbing diff-files and read-tree -u compare
    # cached stat data, so an untouched-but-restatted file would otherwise
    # read as modified. Content changes still show up below.
    _git(
        path, "-c", "core.bare=false", "--git-dir", str(git_dir), "--work-tree", path,
        "update-index", "-q", "--refresh",
    )
    if _work(path, git_dir, "diff-files", "--quiet").returncode != 0:
        return DIRTY
    # No --exclude-per-directory: untracked paths in the way refuse here.
    if _work(path, git_dir, "read-tree", "-m", "-u", "-n", old, new).returncode != 0:
        return COLLISION
    # Current git treats IGNORED files as expendable even without
    # --exclude-per-directory, so a local path that a newly tracked one would
    # overwrite or remove is refused explicitly.
    added = _git(
        path, "diff-tree", "-r", "-z", "--name-only", "--no-renames", "--diff-filter=A", old, new,
    )
    if added.returncode != 0:
        return COLLISION
    tracked = _OldTracked(path, git_dir, old)
    for rel in filter(None, added.stdout.split("\0")):
        if _in_the_way(path, rel, tracked):
            return COLLISION
    return None


class _OldTracked:
    """Membership in ``old``'s tree, loaded on first use and case-folded when
    the anchor's file system is case-insensitive (``core.ignorecase``)."""

    def __init__(self, path: str, git_dir: Path, old: str) -> None:
        self._args = (path, git_dir, old)
        self._paths: set[str] | None = None
        self._fold = False

    def __contains__(self, rel: str) -> bool:
        if self._paths is None:
            path, git_dir, old = self._args
            listing = _work(path, git_dir, "ls-tree", "-r", "-z", "--name-only", old)
            ignorecase = _work(path, git_dir, "config", "--bool", "core.ignorecase")
            self._fold = ignorecase.stdout.strip() == "true"
            names = filter(None, listing.stdout.split("\0")) if listing.returncode == 0 else ()
            self._paths = {self._key(n) for n in names}
        return self._key(rel) in self._paths

    def _key(self, rel: str) -> str:
        return rel.casefold() if self._fold else rel


def _is_dir(local: str) -> bool:
    return os.path.isdir(local) and not os.path.islink(local)


def _in_the_way(path: str, rel: str, tracked: _OldTracked) -> bool:
    """True when a local path not tracked at ``old`` blocks newly added ``rel``.

    That is an untracked file or link at ``rel`` or at one of its parents, or
    an untracked file anywhere under a directory at ``rel``. Paths tracked at
    ``old`` are fine: git replaces those itself (a file/directory type change),
    and the dirty check above already covers their local edits.
    """
    parts = rel.split("/")
    for depth in range(1, len(parts) + 1):
        local = os.path.join(path, *parts[:depth])
        if not os.path.lexists(local):
            return False
        if not _is_dir(local):
            return "/".join(parts[:depth]) not in tracked
    for dirpath, dirnames, filenames in os.walk(local):
        links = [d for d in dirnames if os.path.islink(os.path.join(dirpath, d))]
        for name in (*filenames, *links):
            sub = os.path.relpath(os.path.join(dirpath, name), path).replace(os.sep, "/")
            if sub not in tracked:
                return True
    return False


def refresh(path: str, old: str, new: str) -> str:
    """Advance the anchor's index and files from ``old`` to ``new``."""
    git_dir = _git_dir(path)
    if git_dir is None or not (git_dir / "index").is_file():
        return NONE
    result = _work(path, git_dir, "read-tree", "-m", "-u", old, new)
    if result.returncode != 0:
        return f"{REFRESH_FAILED}:{_tail(result)}"
    return REFRESHED


def status_of(detail: str) -> str | None:
    """Extract the ``files=<status>`` token from a sync result detail.

    The token follows the first ``; files=`` so a failure tail can never be
    mistaken for it.
    """
    marker = "; files="
    idx = detail.find(marker)
    if idx < 0:
        return None
    return detail[idx + len(marker):].split(";", 1)[0].split(":", 1)[0].strip()
