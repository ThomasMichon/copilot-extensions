"""Non-traversing Windows interpreter paths, independent of runtime markers."""
from __future__ import annotations

import errno
import os
import stat
from collections.abc import Callable
from pathlib import Path


def venv_home_python(
    python: str, filename: str, *, resolve_executable: Callable[[str], str | None],
) -> str | None:
    """Read a venv's physical base interpreter without traversing its redirector."""
    venv_root = os.path.dirname(os.path.dirname(python))
    cfg_path = os.path.join(venv_root, "pyvenv.cfg")
    try:
        with open(cfg_path, encoding="utf-8", errors="replace") as fh:
            home = next(
                (value.strip() for line in fh
                 for key, _, value in [line.partition("=")] if key.strip() == "home"),
                "",
            )
    except OSError:
        return None
    # CPython rereads this home through __PYVENV_LAUNCHER__, even if the
    # requested executable is missing. Validate independently of selection.
    if not home:
        return None
    try:
        os.stat(home)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        if getattr(exc, "winerror", None) == 448:
            raise RuntimeError(
                "Cannot preserve the Windows venv: pyvenv.cfg home is blocked "
                "by RedirectionGuard; rebuild the runtime with a physical Python home."
            ) from exc
        raise
    return resolve_executable(os.path.join(home, filename))


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
