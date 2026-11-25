"""Checkout-local test virtual environments during whole-checkout disposal.

Test runners have historically built per-plugin virtual environments under a
checkout's ``.test-venvs`` (and sometimes ``.test-runs``) directory. Those
environments are rebuildable, and Git-ignored content never holds a
checkout's removal on its own. This module adds two things to the existing
disposal in ``cleanup`` and ``finalize``:

* Test-root content that is not a recognized test venv is preserved: it
  holds the whole checkout (:func:`data_blocker`).
* Recognized, untracked and Git-ignored test-venv roots are removed first,
  while the checkout's Git root is still intact (:func:`predelete_blocker`).
  A locked or deep venv then defers disposal with the checkout inspectable,
  instead of leaving a partial checkout without a Git root.

Recognition is conservative and mirrors the guided scanner in
``tools/reclaim_legacy_test_dirs.py``. A root is recognized only when it is a
real directory whose entries are complete Python virtual environments,
platform/group directories holding only such environments, or known runner
metadata files. An environment must have the standard layout: known
top-level environment directories and files only, no evidence directory at
any depth and no special files. Anything else -- reports, logs, captures,
loose files or special files -- makes the whole root unrecognized.

This module never decides that a venv is unused. Pre-removal runs only inside
an existing disposal, after that disposal's own data and process gates, and
removes nothing that the disposal would not remove next.
"""

from __future__ import annotations

import os
import shutil
import stat
from collections.abc import Callable
from pathlib import Path

from . import git_ops

TEST_VENV_ROOT_NAMES = (".test-venvs", ".test-runs")

_GROUP_FILES = frozenset({".gitignore", "cachedir.tag", ".lock", "scratch-owner.json"})
_ENV_DIRS = frozenset({"bin", "scripts", "lib", "lib64", "include", "share", ".bootstrap-uv-cache"})
_ENV_FILES = frozenset({
    "pyvenv.cfg", "python", "python.exe", ".gitignore", ".typecheck-prepared", ".lock",
    ".dep-fingerprint", "cachedir.tag", ".preparation-generation",
})
_EVIDENCE_NAMES = frozenset({"reports", "evidence", "captures", "artifacts", "logs", "test-results"})
_MAX_GROUP_DEPTH = 3
_GIT_TIMEOUT_SECS = 30.0


def native_path(path: str | Path) -> str:
    """Use the Win32 long-path form so deep environments remain removable."""
    text = os.path.abspath(str(path))
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_reparse_tag", 0)) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _lstat(path: str | Path) -> os.stat_result | None:
    try:
        return os.lstat(native_path(path))
    except OSError:
        return None


def _is_real_dir(path: str | Path) -> bool:
    info = _lstat(path)
    return info is not None and not _is_link(info) and stat.S_ISDIR(info.st_mode)


def _has_marker(path: Path) -> bool:
    marker = _lstat(path / "pyvenv.cfg")
    return marker is not None and not _is_link(marker) and stat.S_ISREG(marker.st_mode)


def _scan(path: str) -> list[os.DirEntry[str]]:
    with os.scandir(native_path(path)) as entries:
        return list(entries)


def _valid_layout(path: Path) -> bool:
    """The whole environment has the standard layout and no evidence."""
    pending: list[str] = []
    try:
        for entry in _scan(str(path)):
            info = entry.stat(follow_symlinks=False)
            name = entry.name.lower()
            if name in _ENV_DIRS:
                if _is_link(info):
                    continue  # never traversed; removal unlinks it
                if not stat.S_ISDIR(info.st_mode):
                    return False
                pending.append(entry.path)
            elif name not in _ENV_FILES or not (_is_link(info) or stat.S_ISREG(info.st_mode)):
                return False
        while pending:
            for entry in _scan(pending.pop()):
                info = entry.stat(follow_symlinks=False)
                if _is_link(info):
                    continue
                if stat.S_ISDIR(info.st_mode):
                    if entry.name.lower() in _EVIDENCE_NAMES:
                        return False
                    pending.append(entry.path)
                elif not stat.S_ISREG(info.st_mode):
                    return False
    except OSError:
        return False
    return True


def _collect(path: Path, depth: int, venvs: list[Path]) -> bool:
    try:
        listed = _scan(str(path))
    except OSError:
        return False
    for entry in sorted(listed, key=lambda item: item.name):
        child = path / entry.name
        info = _lstat(child)
        if info is None or _is_link(info):
            return False
        if stat.S_ISREG(info.st_mode):
            if entry.name.lower() not in _GROUP_FILES:
                return False
            continue
        if not stat.S_ISDIR(info.st_mode) or entry.name.lower() in _EVIDENCE_NAMES:
            return False
        if _has_marker(child):
            if not _valid_layout(child):
                return False
            venvs.append(child)
        elif depth >= _MAX_GROUP_DEPTH or not _collect(child, depth + 1, venvs):
            return False
    return True


def disposable_venvs(root: str | Path) -> list[Path] | None:
    """Return the environments in a recognized root, or None if unrecognized.

    An empty list means the root holds only group directories and runner
    metadata (for example after an interrupted removal).
    """
    path = Path(root)
    if path.name not in TEST_VENV_ROOT_NAMES or not _is_real_dir(path):
        return None
    venvs: list[Path] = []
    return venvs if _collect(path, 1, venvs) else None


def recognized_roots(checkout: str | Path) -> list[Path]:
    """Recognized test-venv roots at the top of *checkout*."""
    base = Path(checkout)
    return [base / name for name in TEST_VENV_ROOT_NAMES if disposable_venvs(base / name) is not None]


def unrecognized_roots(checkout: str | Path) -> list[Path]:
    """Test roots at the top of *checkout* that hold content other than test venvs."""
    base = Path(checkout)
    return [base / name for name in TEST_VENV_ROOT_NAMES
            if _is_real_dir(base / name) and disposable_venvs(base / name) is None]


def data_blocker(checkout: str | Path) -> str | None:
    """Test-root content other than test venvs holds whole-checkout disposal.

    Such content is often Git-ignored run output, so the checkout's own data
    gate does not see it.
    """
    held = unrecognized_roots(checkout)
    if not held:
        return None
    return (
        "preserved test-root content at " + ", ".join(str(root) for root in held)
        + " is not a recognized test venv; review it with tools/reclaim_legacy_test_dirs.py"
    )


def untracked_and_ignored(checkout: str | Path, name: str) -> bool:
    """Git tracks nothing under *name* and ignores it (fails closed)."""
    try:
        listed = git_ops.git("ls-files", "-z", "--", f"{name}/", cwd=checkout, check=False,
                             timeout=_GIT_TIMEOUT_SECS)
        if listed.returncode != 0 or listed.stdout:
            return False
        ignored = git_ops.git("check-ignore", "-q", f"{name}/", cwd=checkout, check=False,
                              timeout=_GIT_TIMEOUT_SECS)
    except Exception:
        return False
    return ignored.returncode == 0


def _remove_file(path: str) -> None:
    try:
        os.unlink(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        os.unlink(path)


def _remove_link(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)


def _remove_dir(path: str) -> None:
    try:
        os.rmdir(path)
    except PermissionError:
        os.chmod(path, stat.S_IWRITE)
        os.rmdir(path)


def _purge(path: str, keep: str | None) -> None:
    """Empty *path* bottom-up without following links."""
    with os.scandir(path) as entries:
        listed = list(entries)
    for entry in listed:
        info = entry.stat(follow_symlinks=False)
        if _is_link(info):
            _remove_link(entry.path)
        elif stat.S_ISDIR(info.st_mode):
            _purge(entry.path, None)
            _remove_dir(entry.path)
        elif keep is None or entry.name != keep:
            _remove_file(entry.path)


def remove_tree(path: str | Path) -> None:
    """Remove *path*: clears read-only bits, uses long paths, never follows links.

    A venv keeps its ``pyvenv.cfg`` until last so an interrupted removal
    stays recognizable on the next attempt. Raises ``OSError`` on failure.
    """
    native = native_path(path)
    info = os.lstat(native)
    if _is_link(info):
        _remove_link(native)
        return
    if not stat.S_ISDIR(info.st_mode):
        _remove_file(native)
        return
    _purge(native, "pyvenv.cfg")
    marker = os.path.join(native, "pyvenv.cfg")
    if os.path.lexists(marker):
        _remove_file(marker)
    _remove_dir(native)


def predelete_blocker(checkout: str | Path) -> str | None:
    """Remove recognized test-venv roots while the checkout's Git root is intact.

    Call only inside a whole-checkout disposal, after its data and process
    gates and immediately before ``git worktree remove``. Roots that are
    unrecognized, tracked or not ignored are left to that disposal's normal
    rules. Returns a blocker when a recognized root could not be removed, so
    the caller defers disposal with the checkout still inspectable.
    """
    base = Path(checkout)
    for name in TEST_VENV_ROOT_NAMES:
        root = base / name
        venvs = disposable_venvs(root)
        if venvs is None or not untracked_and_ignored(base, name):
            continue
        try:
            for venv in venvs:
                remove_tree(venv)
            remove_tree(root)
        except OSError as exc:
            return (
                f"test venv root {root} could not be removed ({exc.__class__.__name__}); "
                "a process may still use it. Disposal deferred with the Git root intact"
            )
    return None


def clear_readonly_and_retry(func: Callable[[str], object], path: str, _exc: object) -> None:
    """``shutil.rmtree`` error handler: clear the read-only bit and retry once."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def rmtree_best_effort(path: str | Path) -> None:
    """``shutil.rmtree(ignore_errors=True)`` that also clears read-only bits
    and uses the long-path form on Windows."""
    try:
        shutil.rmtree(native_path(path), onerror=clear_readonly_and_retry)
    except OSError:
        pass


__all__ = [
    "TEST_VENV_ROOT_NAMES", "clear_readonly_and_retry", "data_blocker", "disposable_venvs",
    "native_path", "predelete_blocker", "recognized_roots", "remove_tree", "rmtree_best_effort",
    "unrecognized_roots", "untracked_and_ignored",
]
