"""Deadline-aware hashing preserves the complete immutable-cell integrity contract."""

from __future__ import annotations

import hashlib
import os
import stat
import time
from pathlib import Path


def _integrity_budget(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        from .managed_runtime import ManagedRuntimeLockTimeout

        raise ManagedRuntimeLockTimeout("managed runtime integrity deadline expired")


def _tree_digest(
    root: Path, *, excluded: frozenset[str] = frozenset(), deadline: float | None = None
) -> str:
    from . import managed_runtime as runtime

    _integrity_budget(deadline)
    digest = hashlib.sha256()
    for current, directories, files in os.walk(
        root, topdown=True, followlinks=False, onerror=runtime._walk_error,
    ):
        current_path = Path(current)
        _integrity_budget(deadline)
        runtime._reject_link(current_path, description="managed runtime cell directory")
        directories.sort()
        files.sort()
        for name in directories:
            _integrity_budget(deadline)
            child = current_path / name
            runtime._reject_link(child, description="managed runtime cell directory")
            if not stat.S_ISDIR(child.stat(follow_symlinks=False).st_mode):
                raise runtime.ManagedRuntimeError("managed runtime cell contains a non-directory entry")
            relative = child.relative_to(root).as_posix()
            mode = stat.S_IMODE(child.stat(follow_symlinks=False).st_mode)
            digest.update(f"D\0{relative}\0{mode:o}\0".encode())
        for name in files:
            _integrity_budget(deadline)
            child = current_path / name
            relative = child.relative_to(root).as_posix()
            if relative in excluded:
                continue
            runtime._reject_link(child, description="managed runtime cell file")
            if not stat.S_ISREG(child.stat(follow_symlinks=False).st_mode):
                raise runtime.ManagedRuntimeError("managed runtime cell contains a special file")
            digest.update(relative.encode("utf-8"))
            mode = stat.S_IMODE(child.stat(follow_symlinks=False).st_mode)
            digest.update(f"\0{mode:o}\0".encode("ascii"))
            digest.update(runtime._hash_regular_file(
                child, description="managed runtime cell file", deadline=deadline,
            ).encode("ascii"))
    return digest.hexdigest()
