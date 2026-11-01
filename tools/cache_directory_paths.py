"""Dependency-free ordinary-directory validation for caller-owned test caches."""
from __future__ import annotations

from pathlib import Path
import stat

from .native_test_paths import native_test_path


def ordinary_cache_root(path: Path) -> Path:
    """Refuse linked cache roots before resolving a lease or removing contents."""
    native = native_test_path(path.absolute())
    for candidate in (native, *native.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_reparse_tag", False):
            raise ValueError(f"Test cache directory is not ordinary: {candidate}")
    return path.resolve()
