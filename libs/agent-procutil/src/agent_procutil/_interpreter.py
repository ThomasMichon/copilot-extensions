"""Non-traversing Windows interpreter paths, independent of runtime markers."""
from __future__ import annotations

import errno
import os
import stat
from pathlib import Path


def physical_executable(path: str) -> str | None:
    """Read link targets before inspecting descendants, including the final file.

    RedirectionGuard can refuse traversal of a junction even when its target is
    usable. Like versioned-runtime's legacy link handling, inspect the entry
    with lstat and readlink instead of resolving it with a traversing stat.
    """
    requested = Path(path)
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    remaining = list(requested.parts)
    current = remaining.pop(0)
    links = 0
    if not remaining:
        return None
    while remaining:
        component = remaining.pop(0)
        if component == "..":
            current = os.path.dirname(current)
            if not remaining:
                return None
            continue
        candidate = os.path.join(current, component)
        try:
            info = os.lstat(candidate)
        except (FileNotFoundError, NotADirectoryError):
            return None
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_reparse_tag", 0) & 0x20000000
        ):
            links += 1
            if links > 40:
                raise OSError(errno.ELOOP, "Interpreter link cycle or excessive indirection", path)
            target = os.readlink(candidate)
            if not os.path.isabs(target):
                target = os.path.join(current, target)
            remaining = list(Path(target).parts) + remaining
            current = remaining.pop(0)
            if not remaining:
                return None
            continue
        if remaining and not stat.S_ISDIR(info.st_mode):
            return None
        if not remaining and not stat.S_ISREG(info.st_mode):
            return None
        current = candidate
    return current
