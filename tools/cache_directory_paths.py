"""Dependency-free ordinary-directory validation for caller-owned test caches."""
from __future__ import annotations

from pathlib import Path
import os
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING or __package__:
    from .native_test_paths import native_test_path
else:
    from native_test_paths import native_test_path


def ordinary_cache_root(path: Path) -> Path:
    """Refuse linked cache roots before resolving a lease or removing contents."""
    absolute = path.absolute()
    for candidate in reversed((absolute, *absolute.parents)):
        try:
            # Extended Windows paths do not interpret "." and ".."; inspect
            # every lexical ancestor so normalization cannot hide a link.
            info = native_test_path(Path(os.path.abspath(candidate))).lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", False):
            raise ValueError(f"Test cache directory is not ordinary: {candidate}")
    return path.resolve()
