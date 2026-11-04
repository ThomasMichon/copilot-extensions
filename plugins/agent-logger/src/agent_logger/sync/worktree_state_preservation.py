"""Fail-closed separation of typed collections from disposable session trees."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import time

from agent_logger.sync.provenance import is_link_or_reparse
from agent_logger.sync.publication_directory import publication_directory
from agent_logger.sync.worktree_state_namespace import _MARKER, namespace_overlap

_MAX_ENTRIES = 100_000
_MAX_DIRECTORIES = 20_000
_MAX_DEPTH = 64
_SCAN_SECONDS = 30
_PAYLOAD_COMPONENTS = frozenset({"session-state", "provenance", "archived"})


class WorktreeStatePreservationError(OSError):
    """Persistent or uncertain worktree evidence prevents ordinary disposal."""


def validate_collection_root(root: Path) -> None:
    if any(
        part.casefold() in _PAYLOAD_COMPONENTS
        or part.casefold().startswith(".session-sync-")
        for part in root.parts
    ):
        raise ValueError(
            "collection root must be outside disposable session payload and replacement namespaces"
        )


def refuse_collection_at(directory: Path) -> None:
    with publication_directory(directory, create=False) as pinned:
        try:
            pinned.stat(_MARKER)
        except FileNotFoundError:
            return
        raise WorktreeStatePreservationError(
            "ordinary session operation would enter retained worktree-state evidence"
        )


def refuse_collection_ancestors(directory: Path) -> None:
    if namespace_overlap(directory, ""):
        raise WorktreeStatePreservationError(
            "ordinary session publication would overwrite retained worktree-state evidence"
        )


def refuse_collection_destination(path: Path) -> None:
    directory = path.parent
    try:
        info = path.lstat()
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISDIR(info.st_mode) and not is_link_or_reparse(path, info.st_mode):
            directory = path
    refuse_collection_ancestors(directory)


def require_disposable_tree(root: Path) -> None:
    """Prove marker absence without changing permissions or following redirects.

    The disposal owner must exclude concurrent writers. Marker absence is not
    deletion authority or a recursive lease.
    """
    try:
        info = root.lstat()
    except FileNotFoundError:
        return
    if is_link_or_reparse(root, info.st_mode) or not stat.S_ISDIR(info.st_mode):
        raise WorktreeStatePreservationError("recursive disposal requires a real directory")
    pending = [(root, 0)]
    entries = directories = 0
    deadline = time.monotonic() + _SCAN_SECONDS
    while pending:
        directory, depth = pending.pop()
        directories += 1
        if (
            directories > _MAX_DIRECTORIES or depth > _MAX_DEPTH
            or time.monotonic() >= deadline
        ):
            raise WorktreeStatePreservationError("worktree-state preservation scan exceeded its bound")
        refuse_collection_at(directory)
        with publication_directory(directory, create=False) as pinned:
            with os.scandir(pinned.path if pinned.fd is None else pinned.fd) as children:
                for child in children:
                    entries += 1
                    if entries > _MAX_ENTRIES or time.monotonic() >= deadline:
                        raise WorktreeStatePreservationError(
                            "worktree-state preservation could not be established within its budget"
                        )
                    path = directory / child.name
                    info = child.stat(follow_symlinks=False)
                    if child.name == _MARKER:
                        raise WorktreeStatePreservationError(
                            "recursive disposal would erase retained worktree-state evidence"
                        )
                    if stat.S_ISDIR(info.st_mode) and not is_link_or_reparse(path, info.st_mode):
                        pending.append((path, depth + 1))
