"""Confined plain-file paths shared by contract readers and capture owners."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import stat


def safe(root: Path, relative: str, *, directory: bool = False) -> Path:
    """Reject traversal, links/reparse points and file aliases before any I/O."""
    path = PurePosixPath(relative)
    if (
        not relative
        or not path.parts
        or path.is_absolute()
        or path.as_posix() != relative
        or any(part in {".", "..", ".git"} for part in path.parts)
        or "\\" in relative
        or ":" in relative
    ):
        raise ValueError(f"unsafe capture path: {relative}")
    if not root.is_absolute() or root.resolve(strict=True) != root:
        raise ValueError("capture root must be an absolute unlinked checkout")
    target = root / relative
    for node in (target, *target.parents):
        if node == root:
            break
        if node.is_symlink():
            raise ValueError(f"linked capture path: {relative}")
        try:
            info = node.lstat()
        except FileNotFoundError:
            continue
        if getattr(info, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        ):
            raise ValueError(f"reparse capture path: {relative}")
        if node == target:
            if directory:
                if not stat.S_ISDIR(info.st_mode):
                    raise ValueError(f"non-directory capture path: {relative}")
            else:
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise ValueError(f"aliased capture path: {relative}")
                if not stat.S_ISREG(info.st_mode):
                    raise ValueError(f"non-file capture path: {relative}")
        if node != target and not stat.S_ISDIR(info.st_mode):
            raise ValueError(f"non-directory capture parent: {relative}")
    if not target.resolve().is_relative_to(root):
        raise ValueError(f"escaping capture path: {relative}")
    return target
