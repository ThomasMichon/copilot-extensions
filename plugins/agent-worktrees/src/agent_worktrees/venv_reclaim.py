"""Recognize and remove disposable checkout-local test virtual environments.

Test runners have historically built per-plugin virtual environments under a
checkout's ``.test-venvs`` (and sometimes ``.test-runs``) directory. Those
environments are rebuildable caches, so they never justify retaining a
checkout and may be removed when their checkout retires or a session ends.

Recognition is deliberately conservative. A root is disposable only when it is
a real directory whose entries are Python virtual environments (a directory
with ``pyvenv.cfg``), platform/group directories holding only such
environments, or known runner metadata files. Anything else -- reports, logs,
captures, loose files, links or special files -- makes the whole root
unrecognized, and it is then preserved for its producer.
"""

from __future__ import annotations

import os
import secrets
import stat
import sys
import time
from collections.abc import Iterable
from pathlib import Path

TEST_VENV_ROOT_NAMES = (".test-venvs", ".test-runs")

# Build/test cache directories that are disposable by name (mirrors gc).
CACHE_DIRNAMES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})

_GROUP_FILES = frozenset({".gitignore", "cachedir.tag", ".lock", "scratch-owner.json"})
_EVIDENCE_NAMES = frozenset({"reports", "evidence", "captures", "artifacts", "logs", "test-results"})
_MAX_GROUP_DEPTH = 3
_RECLAIM_PREFIX = ".aw-reclaim-"
_ACTIVITY_FILES = ("pyvenv.cfg", ".lock", ".dep-fingerprint", ".typecheck-prepared")


def native_path(path: str | Path) -> str:
    """Use the Win32 long-path form so deep environments remain removable."""
    text = os.path.abspath(str(path))
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


_native = native_path


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_reparse_tag", 0)) or bool(
        getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    )


def _lstat(path: Path) -> os.stat_result | None:
    try:
        return os.lstat(_native(path))
    except OSError:
        return None


def _is_real_dir(path: Path) -> bool:
    info = _lstat(path)
    return info is not None and not _is_link(info) and stat.S_ISDIR(info.st_mode)


def _is_venv(path: Path) -> bool:
    if not _is_real_dir(path):
        return False
    marker = _lstat(path / "pyvenv.cfg")
    if marker is None or _is_link(marker) or not stat.S_ISREG(marker.st_mode):
        return False
    try:
        names = os.listdir(_native(path))
    except OSError:
        return False
    return not any(name.lower() in _EVIDENCE_NAMES for name in names)


def _collect(path: Path, depth: int, venvs: list[Path]) -> bool:
    try:
        names = sorted(os.listdir(_native(path)))
    except OSError:
        return False
    for name in names:
        child = path / name
        info = _lstat(child)
        if info is None or _is_link(info):
            return False
        if stat.S_ISREG(info.st_mode):
            if name.lower() not in _GROUP_FILES:
                return False
            continue
        if not stat.S_ISDIR(info.st_mode) or name.lower() in _EVIDENCE_NAMES:
            return False
        if _is_venv(child):
            venvs.append(child)
        elif depth >= _MAX_GROUP_DEPTH or not _collect(child, depth + 1, venvs):
            return False
    return True


def disposable_venvs(root: str | Path) -> list[Path] | None:
    """Return the environments in a recognized root, or None if unrecognized.

    An empty list means the root holds only group directories and runner
    metadata (for example after an interrupted removal) and is disposable.
    """
    path = Path(root)
    if path.name not in TEST_VENV_ROOT_NAMES or not _is_real_dir(path):
        return None
    venvs: list[Path] = []
    return venvs if _collect(path, 1, venvs) else None


def is_disposable_test_venv_root(root: str | Path) -> bool:
    return disposable_venvs(root) is not None


def recognized_roots(checkout: str | Path) -> list[Path]:
    """Recognized disposable roots at the top of *checkout*."""
    base = Path(checkout)
    return [base / name for name in TEST_VENV_ROOT_NAMES if is_disposable_test_venv_root(base / name)]


def _disposable_tree(path: Path, depth: int) -> bool:
    try:
        names = os.listdir(_native(path))
    except OSError:
        return False
    for name in names:
        child = path / name
        info = _lstat(child)
        if info is None or _is_link(info) or not stat.S_ISDIR(info.st_mode):
            return False
        if name in CACHE_DIRNAMES:
            continue
        if name in TEST_VENV_ROOT_NAMES and is_disposable_test_venv_root(child):
            continue
        if depth >= 32 or not _disposable_tree(child, depth + 1):
            return False
    return True


def only_disposable_content(checkout: str | Path) -> bool:
    """True when *checkout* holds only empty dirs, caches and recognized test venvs.

    This answers a partial-removal question: once a checkout's Git root is
    gone, a remainder made only of rebuildable content holds no producer data.
    """
    path = Path(checkout)
    return _is_real_dir(path) and _disposable_tree(path, 0)


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


def _purge(path: str, deadline: float | None, keep: str | None) -> bool:
    """Empty *path* bottom-up without following links; False on deadline."""
    with os.scandir(path) as entries:
        listed = list(entries)
    for entry in listed:
        if deadline is not None and time.monotonic() >= deadline:
            return False
        info = entry.stat(follow_symlinks=False)
        if _is_link(info):
            _remove_link(entry.path)
        elif stat.S_ISDIR(info.st_mode):
            if not _purge(entry.path, deadline, None):
                return False
            _remove_dir(entry.path)
        elif keep is None or entry.name != keep:
            _remove_file(entry.path)
    return True


def remove_tree(path: str | Path, *, deadline: float | None = None) -> bool:
    """Remove *path* robustly: read-only bits, long paths, no link traversal.

    Returns False when the deadline interrupted removal. A venv keeps its
    ``pyvenv.cfg`` until last so an interrupted removal stays recognizable.
    """
    native = _native(path)
    info = os.lstat(native)
    if _is_link(info):
        _remove_link(native)
        return True
    if not stat.S_ISDIR(info.st_mode):
        _remove_file(native)
        return True
    if not _purge(native, deadline, "pyvenv.cfg"):
        return False
    marker = os.path.join(native, "pyvenv.cfg")
    if os.path.lexists(marker):
        _remove_file(marker)
    _remove_dir(native)
    return True


def clear_readonly_and_retry(func, path, _exc) -> None:
    """``shutil.rmtree`` error handler: clear the read-only bit and retry once."""
    try:
        os.chmod(path, stat.S_IWRITE)
        func(path)
    except OSError:
        pass


def rmtree_best_effort(path: str | Path) -> None:
    """``shutil.rmtree(ignore_errors=True)`` that also clears read-only bits
    and uses the long-path form on Windows, so venv trees do not linger."""
    import shutil

    try:
        shutil.rmtree(native_path(path), onerror=clear_readonly_and_retry)
    except OSError:
        pass


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _under(path: str | Path, root: str | Path) -> bool:
    child, parent = _norm(path), _norm(root)
    return child == parent or child.startswith(parent.rstrip(os.sep) + os.sep)


def _holders(root: Path) -> tuple[list[dict], str | None]:
    """Processes running a venv interpreter under *root*, or a cwd-wide blocker."""
    from . import procs

    own = [sys.executable, sys.prefix]
    current = [
        {"pid": os.getpid(), "name": "current process", "executable": value}
        for value in own if value and _under(value, root)
    ]
    try:
        if _under(os.getcwd(), root):
            return current, f"current process({os.getpid()}) works inside {root}"
        cwd = procs.processes_with_cwd_under(str(root))
    except OSError as exc:
        return current, f"process inspection unavailable: {exc}"
    if cwd:
        names = ", ".join(f"{p.get('name') or '?'}({p.get('pid')})" for p in cwd)
        return current, f"live process(es) work inside {root}: {names}"
    try:
        found = procs.processes_with_executable_under(str(root))
    except OSError as exc:
        return current, f"process inspection unavailable: {exc}"
    return current + list(found), None


def _recent(venv: Path, min_age_secs: float) -> bool:
    if min_age_secs <= 0:
        return False
    newest = 0.0
    for candidate in (venv, *(venv / name for name in _ACTIVITY_FILES)):
        info = _lstat(candidate)
        if info is not None:
            newest = max(newest, info.st_mtime)
    return time.time() - newest < min_age_secs


def clean_test_venvs(
    checkout: str | Path,
    *,
    dry_run: bool = False,
    min_age_secs: float = 0.0,
    deadline: float | None = None,
    roots: Iterable[Path] | None = None,
) -> dict:
    """Remove recognized test venvs in *checkout* that no live process uses.

    Returns a JSON-ready report. Unrecognized roots are reported and left in
    place; in-use, recently active or deadline-interrupted environments are
    left for a later pass.
    """
    base = Path(checkout)
    report: dict = {"checkout": str(base), "dry_run": dry_run, "venvs": [], "roots": []}
    candidates = list(roots) if roots is not None else [base / name for name in TEST_VENV_ROOT_NAMES]
    for root in candidates:
        if not _is_real_dir(root):
            continue
        venvs = disposable_venvs(root)
        if venvs is None:
            report["roots"].append({
                "path": str(root), "status": "preserved",
                "detail": "contains content that is not a recognized test venv",
            })
            continue
        holders, blocker = _holders(root)
        if blocker:
            report["roots"].append({"path": str(root), "status": "in_use", "detail": blocker})
            continue
        for venv in venvs:
            entry = {"path": str(venv)}
            report["venvs"].append(entry)
            users = [h for h in holders if _under(h.get("executable") or "", venv)]
            if users:
                entry["status"] = "in_use"
                entry["detail"] = "test venv in use by " + ", ".join(
                    f"{h.get('name') or '?'}({h.get('pid')})" for h in users)
                continue
            if _recent(venv, min_age_secs):
                entry.update(status="recent", detail="recently active; left for a later pass")
                continue
            if dry_run:
                entry["status"] = "would_remove"
                continue
            if deadline is not None and time.monotonic() >= deadline:
                entry.update(status="deferred", detail="time budget exhausted")
                continue
            target = venv
            if not venv.name.startswith(_RECLAIM_PREFIX):
                # A failed rename on Windows means an open handle; a successful
                # one keeps runners from reusing a half-removed environment.
                target = venv.with_name(f"{_RECLAIM_PREFIX}{venv.name}-{secrets.token_hex(4)}")
                try:
                    os.rename(_native(venv), _native(target))
                except OSError as exc:
                    entry.update(status="in_use", detail=f"test venv is locked: {exc}")
                    continue
            try:
                done = remove_tree(target, deadline=deadline)
            except OSError as exc:
                entry.update(status="partial", detail=f"removal incomplete: {exc}")
                continue
            entry["status"] = "removed" if done else "partial"
            if not done:
                entry["detail"] = "time budget exhausted; removal resumes on a later pass"
        if dry_run:
            continue
        remaining = disposable_venvs(root)
        if remaining == []:
            try:
                remove_tree(root, deadline=deadline)
                report["roots"].append({"path": str(root), "status": "removed"})
            except OSError as exc:
                report["roots"].append({"path": str(root), "status": "partial", "detail": str(exc)})
    report["removed"] = sum(1 for v in report["venvs"] if v.get("status") == "removed")
    report["remaining"] = sum(1 for v in report["venvs"] if v.get("status") not in {"removed", "would_remove"})
    return report


def blocker(report: dict) -> str | None:
    """Summarize why recognized test venvs still remain, if any."""
    held = [v for v in report.get("venvs", []) if v.get("status") == "in_use"]
    held += [r for r in report.get("roots", []) if r.get("status") == "in_use"]
    if held:
        return "; ".join(item.get("detail") or item["path"] for item in held)
    return None


__all__ = [
    "CACHE_DIRNAMES", "TEST_VENV_ROOT_NAMES", "blocker", "clean_test_venvs", "clear_readonly_and_retry",
    "disposable_venvs",
    "is_disposable_test_venv_root", "native_path", "only_disposable_content", "recognized_roots", "remove_tree",
    "rmtree_best_effort",
]
