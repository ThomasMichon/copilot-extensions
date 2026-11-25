"""Recognize and remove disposable checkout-local test virtual environments.

Test runners have historically built per-plugin virtual environments under a
checkout's ``.test-venvs`` (and sometimes ``.test-runs``) directory. Those
environments are rebuildable caches, so they never justify retaining a
checkout and may be removed when their checkout retires or a session ends.

Recognition is deliberately conservative and mirrors the guided scanner in
``tools/reclaim_legacy_test_dirs.py``. A root is disposable only when it is a
real directory whose entries are complete Python virtual environments,
platform/group directories holding only such environments, or known runner
metadata files. An environment must have the standard layout: known top-level
environment directories and files only, no evidence directory at any depth
and no special files. Anything else -- reports, logs, captures, loose files or
special files -- makes the whole root unrecognized and it is preserved.

Removal fails closed. Each environment is removed only while this process
holds the runners' environment-identity lease, after revalidation under that
lease, when no live process is shown to use it and process inspection is
available. A caller-supplied deadline bounds every stage; when it expires,
nothing more is removed and the remainder is reported as deferred.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import stat
import sys
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

TEST_VENV_ROOT_NAMES = (".test-venvs", ".test-runs")

# Build/test cache directories that are disposable by name (mirrors gc).
CACHE_DIRNAMES = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})

LEASE_SERVICE = "copilot-extensions-test-runner"

_GROUP_FILES = frozenset({".gitignore", "cachedir.tag", ".lock", "scratch-owner.json"})
_ENV_DIRS = frozenset({"bin", "scripts", "lib", "lib64", "include", "share", ".bootstrap-uv-cache"})
_ENV_FILES = frozenset({
    "pyvenv.cfg", "python", "python.exe", ".gitignore", ".typecheck-prepared", ".lock",
    ".dep-fingerprint", "cachedir.tag", ".preparation-generation",
})
_EVIDENCE_NAMES = frozenset({"reports", "evidence", "captures", "artifacts", "logs", "test-results"})
_MAX_GROUP_DEPTH = 3
_RECLAIM_PREFIX = ".aw-reclaim-"
_ACTIVITY_FILES = ("pyvenv.cfg", ".lock", ".dep-fingerprint", ".typecheck-prepared")
_BLOCKING = frozenset({"preserved", "in_use", "unverified", "deferred", "partial", "detached",
                       "recent", "kept"})
_GONE = (FileNotFoundError, ProcessLookupError)

Holder = dict[str, Any]
Problem = tuple[str, str]


class _Expired(Exception):
    """The caller's time budget ran out."""


def _check(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise _Expired


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


def _lstat(path: str | Path) -> os.stat_result | None:
    try:
        return os.lstat(_native(path))
    except OSError:
        return None


def _is_real_dir(path: str | Path) -> bool:
    info = _lstat(path)
    return info is not None and not _is_link(info) and stat.S_ISDIR(info.st_mode)


def _has_marker(path: Path) -> bool:
    marker = _lstat(path / "pyvenv.cfg")
    return marker is not None and not _is_link(marker) and stat.S_ISREG(marker.st_mode)


def _scan(path: str) -> list[os.DirEntry[str]]:
    with os.scandir(_native(path)) as entries:
        return list(entries)


def _valid_layout(path: Path, deadline: float | None = None) -> bool:
    """The whole environment has the standard layout and no evidence."""
    pending: list[str] = []
    try:
        for entry in _scan(str(path)):
            _check(deadline)
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
                _check(deadline)
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


def _is_venv(path: Path, deadline: float | None = None) -> bool:
    return _is_real_dir(path) and _has_marker(path) and _valid_layout(path, deadline)


def _collect(path: Path, depth: int, venvs: list[Path], deadline: float | None, full: bool) -> bool:
    try:
        listed = _scan(str(path))
    except OSError:
        return False
    for entry in sorted(listed, key=lambda item: item.name):
        _check(deadline)
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
            if full and not _valid_layout(child, deadline):
                return False
            venvs.append(child)
        elif depth >= _MAX_GROUP_DEPTH or not _collect(child, depth + 1, venvs, deadline, full):
            return False
    return True


def _venvs(root: str | Path, deadline: float | None, full: bool) -> list[Path] | None:
    path = Path(root)
    if path.name not in TEST_VENV_ROOT_NAMES or not _is_real_dir(path):
        return None
    venvs: list[Path] = []
    return venvs if _collect(path, 1, venvs, deadline, full) else None


def disposable_venvs(root: str | Path, deadline: float | None = None) -> list[Path] | None:
    """Return the environments in a recognized root, or None if unrecognized.

    An empty list means the root holds only group directories and runner
    metadata (for example after an interrupted removal) and is disposable.
    """
    return _venvs(root, deadline, True)


def is_disposable_test_venv_root(root: str | Path) -> bool:
    try:
        return disposable_venvs(root) is not None
    except _Expired:
        return False


def recognized_roots(checkout: str | Path) -> list[Path]:
    """Recognized disposable roots at the top of *checkout*."""
    base = Path(checkout)
    return [base / name for name in TEST_VENV_ROOT_NAMES if is_disposable_test_venv_root(base / name)]


def unrecognized_roots(checkout: str | Path) -> list[Path]:
    """Test roots at the top of *checkout* that hold content other than test venvs."""
    base = Path(checkout)
    return [base / name for name in TEST_VENV_ROOT_NAMES
            if _is_real_dir(base / name) and not is_disposable_test_venv_root(base / name)]


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


def clear_readonly_and_retry(func: Callable[[str], object], path: str, _exc: object) -> None:
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


def _under_either(path: str, base: Path) -> bool:
    return _under(path, base) or _under(path, os.path.realpath(base))


def lease_dir(environment: Path) -> Path:
    """The runners' admission lease for *environment* (``tools/_admission_protocol``)."""
    metadata = os.stat(str(environment))
    if not stat.S_ISDIR(metadata.st_mode) or not metadata.st_ino:
        raise ValueError("test environment requires a directory with filesystem identity")
    key = hashlib.sha256(f"{metadata.st_dev}:{metadata.st_ino}".encode("ascii")).hexdigest()
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "copilot-extensions" / "test-runner" / "environments" / key


def _lease(environment: Path) -> tuple[Any, Problem | None]:
    """Take the runners' environment lease without waiting."""
    try:
        from single_instance_lease import AlreadyRunningError, SingleInstance
    except ImportError as exc:
        return None, ("unverified", f"test runner lease unavailable: {exc}")
    try:
        lease = SingleInstance(lease_dir(environment), service=LEASE_SERVICE)
        lease.acquire()
    except AlreadyRunningError:
        return None, ("in_use", "a test runner holds this environment")
    except Exception as exc:  # fail closed: an unknown lease state keeps the venv
        return None, ("unverified", f"test runner lease could not be checked: {exc}")
    return lease, None


def _release(leases: Iterable[Any]) -> None:
    for lease in leases:
        try:
            lease.release()
        except Exception:
            pass


def _platform() -> str:
    return sys.platform


def _comm(pid: Path) -> str:
    try:
        return (pid / "comm").read_text(encoding="utf-8", errors="replace").strip() or "?"
    except OSError:
        return f"pid {pid.name}"


def _proc_paths(pid: Path, readlink: Callable[[str], str], deadline: float | None) -> list[str]:
    """Paths a process uses: cwd, image, fds, maps, invocation and environment."""
    cwd = readlink(str(pid / "cwd"))
    paths = [cwd, readlink(str(pid / "exe"))]
    for fd in (pid / "fd").iterdir():
        _check(deadline)
        try:
            paths.append(readlink(str(fd)))
        except _GONE:
            continue
    for row in (pid / "maps").read_text(encoding="utf-8", errors="surrogateescape").splitlines():
        fields = row.split(maxsplit=5)
        if len(fields) == 6:
            paths.append(fields[5].replace("\\012", "\n"))
    # /proc/<pid>/exe resolves a symlinked env/bin/python to its base
    # interpreter, so the original invocation is inspected lexically too.
    argv0 = (pid / "cmdline").read_bytes().split(b"\0", 1)[0].decode(errors="surrogateescape")
    if argv0 and os.sep in argv0:
        paths.append(os.path.normpath(os.path.join(cwd, argv0)))
    for item in (pid / "environ").read_bytes().split(b"\0"):
        key, _, value = item.decode(errors="surrogateescape").partition("=")
        if key == "VIRTUAL_ENV" and value:
            paths.append(os.path.normpath(value))
        elif key == "PATH":
            paths.extend(os.path.normpath(part) for part in value.split(os.pathsep) if part)
    return paths


def _linux_scan(
    root: Path,
    deadline: float | None = None,
    *,
    proc: Path = Path("/proc"),
    readlink: Callable[[str], str] = os.readlink,
    uid: int | None = None,
) -> tuple[list[Holder], set[str]] | None:
    """This user's processes using *root*, plus uninspectable process names.

    None means process inspection is unavailable. A process that exits
    mid-scan is absent, not uninspectable.
    """
    if uid is None:
        getuid = getattr(os, "getuid", None)
        if getuid is None:
            return None
        uid = int(getuid())
    if not proc.is_dir():
        return None
    try:
        pids = [entry for entry in proc.iterdir() if entry.name.isdigit()]
    except OSError:
        return None
    forms = {_norm(root), _norm(os.path.realpath(root))}
    resolved: dict[str, str] = {}

    def hit(path: str) -> list[str]:
        if not os.path.isabs(path):
            return []
        if any(_under(path, form) for form in forms):
            return [path]
        real = resolved.setdefault(path, os.path.realpath(path))
        return [path, real] if any(_under(real, form) for form in forms) else []

    holders: list[Holder] = []
    blind: set[str] = set()
    own = str(os.getpid())
    for pid in pids:
        _check(deadline)
        if pid.name == own:
            continue
        try:
            if pid.stat().st_uid != uid:
                continue
            paths = _proc_paths(pid, readlink, deadline)
        except _GONE:
            continue
        except OSError:
            blind.add(_comm(pid))
            continue
        hits = [found for path in paths for found in hit(path)]
        if hits:
            holders.append({"pid": int(pid.name), "name": _comm(pid), "paths": hits})
    return holders, blind


def _windows_scan(root: Path) -> tuple[list[Holder], Problem | None]:
    from . import procs

    try:
        cwd = procs.processes_with_cwd_under(str(root), require_available=True)
    except OSError as exc:
        return [], ("unverified", f"process use cannot be verified: {exc}")
    if cwd:
        names = ", ".join(f"{p.get('name') or '?'}({p.get('pid')})" for p in cwd)
        return [], ("in_use", f"live process(es) work inside {root}: {names}")
    try:
        found = procs.processes_with_executable_under(str(root))
    except OSError as exc:
        return [], ("unverified", f"process use cannot be verified: {exc}")
    # An open handle also makes the final exclusive rename fail.
    return [
        {"pid": p.get("pid"), "name": p.get("name"), "paths": [p.get("executable") or ""]}
        for p in found
    ], None


def _holders(root: Path, deadline: float | None = None) -> tuple[list[Holder], Problem | None]:
    """Processes using *root*, or why use could not be ruled out."""
    current: list[Holder] = [
        {"pid": os.getpid(), "name": "current process", "paths": [value]}
        for value in (sys.executable, sys.prefix) if value and _under(value, root)
    ]
    try:
        if _under(os.getcwd(), root):
            return current, ("in_use", f"current process({os.getpid()}) works inside {root}")
    except OSError as exc:
        return current, ("unverified", f"process use cannot be verified: {exc}")
    platform = _platform()
    if platform == "win32":
        found, problem = _windows_scan(root)
        return current + found, problem
    if platform.startswith("linux"):
        scan = _linux_scan(root, deadline)
        if scan is None:
            return current, ("unverified", "process use cannot be verified")
        holders, blind = scan
        if blind:
            return current + holders, (
                "unverified",
                "process use cannot be verified; cannot inspect: " + ", ".join(sorted(blind)),
            )
        return current + holders, None
    return current, ("unverified", f"process inspection unsupported on {platform}")


def _recent(venv: Path, min_age_secs: float) -> bool:
    if min_age_secs <= 0:
        return False
    newest = 0.0
    for candidate in (venv, *(venv / name for name in _ACTIVITY_FILES)):
        info = _lstat(candidate)
        if info is not None:
            newest = max(newest, info.st_mtime)
    return time.time() - newest < min_age_secs


def _names(holders: Iterable[Holder]) -> str:
    return ", ".join(f"{h.get('name') or '?'}({h.get('pid')})" for h in holders)


def _prune_root(path: str, deadline: float | None) -> bool:
    """Remove emptied group directories and runner metadata only."""
    if _has_marker(Path(path)):
        return False
    for entry in _scan(path):
        _check(deadline)
        info = entry.stat(follow_symlinks=False)
        if _is_link(info):
            return False
        if stat.S_ISDIR(info.st_mode):
            if not _prune_root(entry.path, deadline):
                return False
        elif stat.S_ISREG(info.st_mode) and entry.name.lower() in _GROUP_FILES:
            _remove_file(entry.path)
        else:
            return False
    _remove_dir(_native(path))
    return True


def _remove_entry(entry: dict[str, Any], target: Path, deadline: float | None) -> bool:
    try:
        done = remove_tree(target, deadline=deadline)
    except OSError as exc:
        entry.update(status="partial", detail=f"removal incomplete: {exc}")
        return True
    entry["status"] = "removed" if done else "partial"
    if not done:
        entry["detail"] = "time budget exhausted; removal resumes on a later pass"
    return done


def _clean_root(root: Path, report: dict[str, Any], *, dry_run: bool, min_age_secs: float,
                deadline: float | None, detach_only: bool) -> None:
    def root_status(status: str, detail: str) -> None:
        report["roots"].append({"path": str(root), "status": status, "detail": detail})

    leases: list[Any] = []
    held: dict[str, Problem] = {}
    try:
        candidates = _venvs(root, deadline, False)
        if candidates is None:
            root_status("preserved", "contains content that is not a recognized test venv")
            return
        for venv in candidates:
            _check(deadline)
            lease, problem = _lease(venv)
            if lease is not None:
                leases.append((str(venv), lease))
            elif problem is not None:
                held[str(venv)] = problem
        leased = {path for path, _ in leases}
        # Revalidate the full layout while runners are excluded.
        venvs = disposable_venvs(root, deadline)
        if venvs is None:
            root_status("preserved", "contains content that is not a recognized test venv")
            return
        holders, problem = _holders(root, deadline)
        if problem is not None:
            root_status(*problem)
            return
        loose = [h for h in holders if any(
            not any(_under_either(p, v) for v in venvs) for p in h.get("paths", []))]
        if loose:
            root_status("in_use", f"live process(es) use {root}: {_names(loose)}")
            return
        complete = True
        for venv in venvs:
            entry: dict[str, Any] = {"path": str(venv)}
            report["venvs"].append(entry)
            users = [h for h in holders if any(_under_either(p, venv) for p in h.get("paths", []))]
            if str(venv) in held:
                entry["status"], entry["detail"] = held[str(venv)]
            elif str(venv) not in leased:
                entry.update(status="unverified", detail="environment appeared during inspection")
            elif users:
                entry.update(status="in_use", detail=f"test venv in use by {_names(users)}")
            elif _recent(venv, min_age_secs):
                entry.update(status="recent", detail="recently active; left for a later pass")
            elif dry_run:
                entry["status"] = "would_remove"
                continue
            else:
                _check(deadline)
                target = venv
                if not venv.name.startswith(_RECLAIM_PREFIX):
                    # A failed rename on Windows means an open handle; a successful
                    # one keeps runners from reusing a half-removed environment.
                    target = venv.with_name(f"{_RECLAIM_PREFIX}{venv.name}-{secrets.token_hex(4)}")
                    try:
                        os.rename(_native(venv), _native(target))
                    except OSError as exc:
                        entry.update(status="in_use", detail=f"test venv is locked: {exc}")
                        complete = False
                        continue
                if detach_only:
                    entry.update(status="detached", target=str(target))
                    complete = False
                    continue
                if not _remove_entry(entry, target, deadline):
                    report["expired"] = True
                    return
                if entry["status"] == "removed":
                    continue
            complete = False
        if complete and not dry_run and not detach_only:
            try:
                pruned = _prune_root(str(root), deadline)
            except OSError as exc:
                root_status("partial", str(exc))
            else:
                if pruned:
                    report["roots"].append({"path": str(root), "status": "removed"})
                else:
                    root_status("kept", "new content appeared during removal")
    except _Expired:
        report["expired"] = True
        root_status("deferred", "time budget exhausted; nothing more was removed")
    finally:
        _release(lease for _, lease in leases)


def clean_test_venvs(
    checkout: str | Path,
    *,
    dry_run: bool = False,
    min_age_secs: float = 0.0,
    deadline: float | None = None,
    roots: Iterable[Path] | None = None,
    detach_only: bool = False,
) -> dict[str, Any]:
    """Remove recognized test venvs in *checkout* that are provably unused.

    Returns a JSON-ready report. Unrecognized roots are reported and left in
    place; leased, in-use, unverifiable, recently active or deadline-deferred
    environments are left for a later pass. ``detach_only`` renames removable
    environments out of the runners' way and reports them as ``detached``;
    :func:`remove_detached` deletes them later.
    """
    base = Path(checkout)
    report: dict[str, Any] = {
        "checkout": str(base), "dry_run": dry_run, "venvs": [], "roots": [], "expired": False,
    }
    candidates = list(roots) if roots is not None else [base / name for name in TEST_VENV_ROOT_NAMES]
    for root in candidates:
        if deadline is not None and time.monotonic() >= deadline:
            report["expired"] = True
            break
        if _is_real_dir(root):
            _clean_root(root, report, dry_run=dry_run, min_age_secs=min_age_secs,
                        deadline=deadline, detach_only=detach_only)
        if report["expired"]:
            break
    return _summarize(report)


def remove_detached(report: dict[str, Any], deadline: float | None = None) -> dict[str, Any]:
    """Delete environments a ``detach_only`` pass renamed aside."""
    for entry in report.get("venvs", []):
        if entry.get("status") != "detached":
            continue
        if deadline is not None and time.monotonic() >= deadline:
            report["expired"] = True
            break
        if not _remove_entry(entry, Path(entry["target"]), deadline):
            report["expired"] = True
            break
    return _summarize(report)


def _summarize(report: dict[str, Any]) -> dict[str, Any]:
    venvs = report["venvs"]
    report["removed"] = sum(1 for v in venvs if v.get("status") == "removed")
    report["remaining"] = sum(1 for v in venvs if v.get("status") not in {"removed", "would_remove"})
    return report


def disposal_blocker(report: dict[str, Any], *, allow_unverified: bool = False) -> str | None:
    """Why the checkout holding *report*'s roots must not be deleted, if anything.

    Preserved roots hold producer content; in-use, deferred or partially
    removed environments are still live or incomplete. ``allow_unverified``
    is for whole-checkout disposal that already passed the checkout's own
    process gate: an environment whose use could not be proven is then not a
    further hold, because that gate governs the checkout as a whole.
    """
    statuses = _BLOCKING - ({"unverified"} if allow_unverified else set())
    held = [item for item in (*report.get("roots", []), *report.get("venvs", []))
            if item.get("status") in statuses]
    if held:
        return "; ".join(
            f"{item['status']} test venv content at {item['path']}"
            + (f" ({item['detail']})" if item.get("detail") else "")
            for item in held
        )
    if report.get("expired"):
        return "test venv inspection did not finish within its time budget"
    return None


def blocker(report: dict[str, Any]) -> str | None:
    """Summarize why recognized test venvs are still in use, if they are."""
    held = [v for v in report.get("venvs", []) if v.get("status") == "in_use"]
    held += [r for r in report.get("roots", []) if r.get("status") == "in_use"]
    if held:
        return "; ".join(item.get("detail") or item["path"] for item in held)
    return None


__all__ = [
    "CACHE_DIRNAMES", "LEASE_SERVICE", "TEST_VENV_ROOT_NAMES", "blocker", "clean_test_venvs",
    "clear_readonly_and_retry", "disposable_venvs", "disposal_blocker", "is_disposable_test_venv_root",
    "lease_dir", "native_path", "only_disposable_content", "recognized_roots", "remove_detached",
    "remove_tree", "rmtree_best_effort", "unrecognized_roots",
]
