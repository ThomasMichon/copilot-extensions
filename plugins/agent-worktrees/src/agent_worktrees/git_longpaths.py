"""Windows long-path support for managed repositories.

Git for Windows limits checkout paths to MAX_PATH (260 characters) unless
``core.longpaths`` is true. The OS ``LongPathsEnabled`` registry value does
not change that. A worktree folder adds its own prefix to every
repository-relative path, so a checkout that works at the anchor can fail in
a worktree with ``Filename too long``.

Two complementary measures:

* :func:`invocation_args` returns ``-c core.longpaths=true`` for Git calls
  that this package makes to create or populate a checkout. It never changes
  stored configuration.
* :func:`ensure_repository` stores ``core.longpaths=true`` in the
  repository-local configuration (shared by every worktree), but only when no
  configuration scope already sets an explicit value. An explicit user value,
  including ``false``, is preserved and reported.
"""

from __future__ import annotations

import sys
from pathlib import Path

KEY = "core.longpaths"


def applies() -> bool:
    """True only for native Windows Git, where MAX_PATH applies."""
    return sys.platform == "win32"


def invocation_args() -> list[str]:
    """Per-invocation Git options that enable long paths on Windows."""
    return ["-c", f"{KEY}=true"] if applies() else []


def ensure_repository(repository: str | Path) -> str:
    """Store a repository-local ``core.longpaths=true`` when no value is set.

    Returns one of ``not-windows``, ``set``, ``present`` (an explicit value
    already exists in some scope), ``missing`` (not a repository root) or
    ``failed``. Never raises.
    """
    if not applies():
        return "not-windows"
    path = Path(repository).expanduser()
    if not path.is_dir():
        return "missing"
    from . import git_ops

    try:
        if not _is_repository_root(path):
            return "missing"
        current = git_ops.git("config", "--get", KEY, cwd=path, check=False, timeout=30, isolated_repository=True)
        if current.returncode == 0 and current.stdout.strip():
            return "present"
        if current.returncode != 1:
            return "failed"
        stored = git_ops.git("config", "--local", KEY, "true", cwd=path, check=False, timeout=30, isolated_repository=True)
    except Exception:
        return "failed"
    return "set" if stored.returncode == 0 else "failed"


def _is_repository_root(path: Path) -> bool:
    """True when ``path`` is a checkout root or bare repository, not a subfolder.

    Guards against writing configuration into an enclosing repository.
    """
    from . import git_ops

    probe = git_ops.git("rev-parse", "--is-bare-repository", "--absolute-git-dir", cwd=path, check=False, timeout=30, isolated_repository=True)
    lines = probe.stdout.splitlines() if probe.returncode == 0 else []
    if len(lines) < 2:
        return False
    if lines[0].strip() == "true":
        root = lines[1].strip()
    else:
        top = git_ops.git("rev-parse", "--show-toplevel", cwd=path, check=False, timeout=30, isolated_repository=True)
        if top.returncode != 0:
            return False
        root = top.stdout.strip()
    try:
        return Path(root).samefile(path)
    except OSError:
        return False
