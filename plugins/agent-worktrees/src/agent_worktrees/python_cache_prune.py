"""Conservative pruning of per-invocation ``unsealed-*`` Python bytecode caches.

Launchers select ``<runtime-root>/cache/python/installed-<digest>`` when a
runtime has valid install evidence and otherwise mint a one-off
``unsealed-<id>`` prefix. Those one-off prefixes are disposable once their
process has exited, but nothing reclaims them automatically. This module
reports (and with ``apply=True`` removes) only directories that are provably
bytecode-only, old enough to be idle, free of reparse points, and not named by
any live process.

Age alone does not prove the owning interpreter exited, so a candidate is
eligible only after a complete process scan (every process environment and
command line was readable) found no reference to it. Where that evidence is
unavailable or incomplete, candidates are retained unless the caller
explicitly accepts the age grace period as the liveness bound
(``allow_age_only``); a failed scan always retains every candidate.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import stat
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable

DEFAULT_MIN_AGE_SECONDS = 3600.0
_UNSEALED = re.compile(r"^unsealed-[A-Za-z0-9-]+$")
_UNSEALED_REF = re.compile(rb"unsealed-[A-Za-z0-9-]+")
_BYTECODE = re.compile(r"\.pyc(?:\.\d+)?$", re.IGNORECASE)
_REPARSE_POINT = 0x400

# Test seam: invoked after the final inventory, immediately before removal.
_before_remove: Callable[[str], None] | None = None


@dataclass
class Candidate:
    path: str
    status: str
    bytes: int = 0
    files: int = 0
    reason: str = ""


@dataclass
class PruneReport:
    cache_root: str
    apply: bool
    min_age_seconds: float
    liveness: str = "unavailable"
    age_only_allowed: bool = False
    candidates: list[Candidate] = field(default_factory=list)

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.candidates:
            counts[item.status] = counts.get(item.status, 0) + 1
        reclaim = ("removed", "eligible")
        counts["bytes"] = sum(c.bytes for c in self.candidates if c.status in reclaim)
        return counts

    def as_dict(self) -> dict:
        return {
            "cache_root": self.cache_root,
            "apply": self.apply,
            "min_age_seconds": self.min_age_seconds,
            "liveness": self.liveness,
            "age_only_allowed": self.age_only_allowed,
            "summary": self.summary(),
            "candidates": [asdict(c) for c in self.candidates],
        }


class _Unsafe(Exception):
    """A candidate failed a safety check and must be preserved."""


@dataclass
class _Node:
    ident: tuple[int, int]
    files: list[str] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)


def _io_path(path: str) -> str:
    if os.name != "nt":
        return os.path.abspath(path)
    path = os.path.abspath(path)
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def _display(path: str) -> str:
    if path.startswith("\\\\?\\UNC\\"):
        return "\\\\" + path[8:]
    if path.startswith("\\\\?\\"):
        return path[4:]
    return path


def _is_reparse(st: os.stat_result) -> bool:
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & _REPARSE_POINT)


def _ident(st: os.stat_result) -> tuple[int, int]:
    return (st.st_dev, st.st_ino)


def _linked_component(cache_root: str) -> str | None:
    """Return the first linked runtime-owned component of *cache_root*, if any.

    Checks ``<runtime-root>/cache/python``, ``<runtime-root>/cache`` and the
    runtime root itself without following reparse points. Ancestors above the
    runtime root (for example a system-provided temp or home redirection) are
    the account's own layout and are not second-guessed.
    """
    current = _io_path(cache_root)
    for _ in range(3):
        if _is_reparse(os.stat(current, follow_symlinks=False)):
            return _display(current)
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def _inventory(directory: str) -> tuple[dict[str, _Node], int, int, float]:
    """Map each relative dir to its identity and bytecode files.

    Returns the tree, file count, total bytes and newest mtime; raises
    ``_Unsafe`` for any reparse point or non-bytecode entry.
    """
    st = os.stat(directory, follow_symlinks=False)
    if _is_reparse(st) or not stat.S_ISDIR(st.st_mode):
        raise _Unsafe("reparse-point")
    tree = {"": _Node(_ident(st))}
    newest, total, count = st.st_mtime, 0, 0
    pending = [""]
    while pending:
        rel = pending.pop()
        node = tree[rel]
        with os.scandir(os.path.join(directory, rel) if rel else directory) as entries:
            for entry in entries:
                est = entry.stat(follow_symlinks=False)
                if _is_reparse(est):
                    raise _Unsafe("reparse-point")
                newest = max(newest, est.st_mtime)
                child = os.path.join(rel, entry.name) if rel else entry.name
                if stat.S_ISDIR(est.st_mode):
                    node.dirs.append(entry.name)
                    # DirEntry.stat() leaves st_ino/st_dev zero on Windows.
                    tree[child] = _Node(_ident(os.stat(entry.path, follow_symlinks=False)))
                    pending.append(child)
                elif stat.S_ISREG(est.st_mode) and _BYTECODE.search(entry.name):
                    node.files.append(entry.name)
                    total += est.st_size
                    count += 1
                else:
                    raise _Unsafe("non-bytecode-content")
    return tree, count, total, newest


def _remove_posix(directory: str, tree: dict[str, _Node]) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)

    def walk(fd: int, rel: str) -> None:
        if _ident(os.fstat(fd)) != tree[rel].ident:
            raise _Unsafe("changed-during-removal")
        node = tree[rel]
        for name in node.dirs:
            child = os.path.join(rel, name) if rel else name
            child_fd = os.open(name, flags, dir_fd=fd)
            try:
                walk(child_fd, child)
            finally:
                os.close(child_fd)
            os.rmdir(name, dir_fd=fd)
        for name in node.files:
            os.unlink(name, dir_fd=fd)

    fd = os.open(directory, flags)
    try:
        walk(fd, "")
    finally:
        os.close(fd)
    os.rmdir(directory)


def _open_guard(path: str):
    """Open *path* without FILE_SHARE_DELETE so it cannot be renamed or replaced."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(path, 0x80, 0x1 | 0x2, None, 3, 0x02000000 | 0x00200000, None)
    if handle in (None, wintypes.HANDLE(-1).value):
        raise ctypes.WinError(ctypes.get_last_error())
    return handle


def _close_guard(handle) -> None:
    import ctypes
    from ctypes import wintypes

    close = ctypes.WinDLL("kernel32").CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close(handle)


def _remove_windows(directory: str, tree: dict[str, _Node]) -> None:
    guards: list[tuple[str, object]] = []
    try:
        for rel in sorted(tree, key=lambda r: (r.count(os.sep) + bool(r), r)):
            path = os.path.join(directory, rel) if rel else directory
            guards.append((path, _open_guard(path)))
            st = os.stat(path, follow_symlinks=False)
            if _is_reparse(st) or _ident(st) != tree[rel].ident:
                raise _Unsafe("changed-during-removal")
        for rel, node in tree.items():
            base = os.path.join(directory, rel) if rel else directory
            for name in node.files:
                os.unlink(os.path.join(base, name))
        while guards:
            path, handle = guards.pop()
            _close_guard(handle)
            os.rmdir(path)
    finally:
        for _path, handle in guards:
            _close_guard(handle)


def _remove(directory: str, tree: dict[str, _Node]) -> None:
    if os.name == "nt":
        _remove_windows(directory, tree)
    elif os.open in os.supports_dir_fd and os.unlink in os.supports_dir_fd:
        _remove_posix(directory, tree)
    else:
        raise _Unsafe("no-anchored-removal")


def live_unsealed_names(proc_root: str = "/proc") -> tuple[str, set[str] | None]:
    """Return (liveness mode, names referenced by live processes).

    ``("process-scan", names)`` when every process environment and command
    line was read; ``("incomplete", names)`` when some live process could not
    be inspected (for example another account's); ``("unavailable", None)``
    where the platform offers no cheap inspection; ``("unknown", None)`` when
    the scan itself failed. Only a process that exited mid-scan is skipped.
    """
    proc = Path(proc_root)
    if not sys.platform.startswith("linux") or not proc.is_dir():
        return "unavailable", None
    names: set[str] = set()
    complete = True
    try:
        pids = [p for p in os.listdir(proc) if p.isdigit()]
    except OSError:
        return "unknown", None
    for pid in pids:
        for leaf in ("environ", "cmdline"):
            try:
                data = (proc / pid / leaf).read_bytes()
            except (FileNotFoundError, ProcessLookupError):
                break
            except PermissionError:
                complete = False
                continue
            except OSError:
                return "unknown", None
            names.update(m.decode("ascii") for m in _UNSEALED_REF.findall(data))
    return ("process-scan" if complete else "incomplete"), names


def _validate_age(min_age_seconds: float) -> float:
    value = float(min_age_seconds)
    if not math.isfinite(value) or value < 0:
        raise ValueError("min_age_seconds must be a finite, non-negative number")
    return value


def prune_unsealed(
    cache_root: str | os.PathLike[str],
    *,
    apply: bool = False,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    now: float | None = None,
    liveness: tuple[str, Iterable[str] | None] | None = None,
    allow_age_only: bool = False,
) -> PruneReport:
    """Inspect (and optionally remove) idle ``unsealed-*`` dirs under *cache_root*.

    Only direct children named ``unsealed-*`` are considered; ``installed-*``
    and every other name are never touched. A candidate is removed only when
    its whole tree contains nothing but directories and ``.pyc`` files, holds
    no symlink/junction/reparse point (including the root's ancestors), its
    newest entry is older than *min_age_seconds*, and no live process names
    it. *liveness* comes from ``live_unsealed_names``; without a complete
    ``process-scan`` every candidate is retained unless *allow_age_only*
    accepts the grace period as the liveness bound (an ``unknown`` scan is
    never overridden). Any error preserves the candidate (fail closed); root
    level failures are reported as error entries rather than raised.
    """
    min_age = _validate_age(min_age_seconds)
    root_text = os.fspath(cache_root)
    report = PruneReport(_display(os.path.abspath(root_text)), apply, min_age)
    mode, live = liveness if liveness is not None else ("unavailable", None)
    report.liveness = mode
    report.age_only_allowed = bool(allow_age_only)
    proven = mode == "process-scan" or (allow_age_only and mode in ("incomplete", "unavailable"))
    live_names = set(live) if live is not None else set()
    io_root = _io_path(root_text)
    try:
        root_stat = os.stat(io_root, follow_symlinks=False)
    except FileNotFoundError:
        return report
    except OSError as exc:
        report.candidates.append(Candidate(report.cache_root, "error", reason=_error(exc)))
        return report
    try:
        linked = _linked_component(root_text)
    except OSError as exc:
        report.candidates.append(Candidate(report.cache_root, "error", reason=_error(exc)))
        return report
    if linked is not None or _is_reparse(root_stat) or not stat.S_ISDIR(root_stat.st_mode):
        report.candidates.append(Candidate(report.cache_root, "skipped", reason="cache-root-linked-or-not-directory"))
        return report
    current_time = time.time() if now is None else now
    try:
        with os.scandir(io_root) as entries:
            names = sorted(entry.name for entry in entries if _UNSEALED.match(entry.name))
    except OSError as exc:
        report.candidates.append(Candidate(report.cache_root, "error", reason=_error(exc)))
        return report
    for name in names:
        candidate_path = os.path.join(io_root, name)
        item = Candidate(_display(candidate_path), "skipped")
        report.candidates.append(item)
        try:
            tree, count, total, newest = _inventory(candidate_path)
            item.bytes, item.files = total, count
            if not current_time - newest >= min_age:
                item.reason = "younger-than-grace-period"
                continue
            if mode == "unknown":
                item.reason = "liveness-unknown"
                continue
            if not proven:
                item.reason = "liveness-unproven"
                continue
            if name in live_names:
                item.reason = "referenced-by-live-process"
                continue
            if not apply:
                item.status = "eligible"
                continue
            tree, count, total, newest = _inventory(candidate_path)
            if not current_time - newest >= min_age:
                item.reason = "younger-than-grace-period"
                continue
            if _before_remove is not None:
                _before_remove(candidate_path)
            try:
                _remove(candidate_path, tree)
            except _Unsafe as exc:
                item.status = "error"
                item.reason = str(exc)
                continue
            item.status = "removed"
        except _Unsafe as exc:
            item.reason = str(exc)
        except OSError as exc:
            item.status = "error"
            item.reason = _error(exc)
    return report


def _error(exc: OSError) -> str:
    return f"{type(exc).__name__}: {exc.strerror or exc}"


def default_cache_roots(home: str | os.PathLike[str] | None = None) -> list[Path]:
    """Runtime-owned ``cache/python`` roots for this account's plugin runtimes.

    Linked runtime roots are still returned so ``prune_unsealed`` can report
    them as skipped instead of silently following or hiding them.
    """
    base = Path(home) if home is not None else Path.home()
    candidates: list[Path] = []
    for pattern in (".agent-*", ".worktree-manager", ".budget-guidance", ".copilot-extensions/*"):
        for runtime_root in sorted(base.glob(pattern)):
            cache = runtime_root / "cache" / "python"
            try:
                os.stat(cache, follow_symlinks=False)
            except (FileNotFoundError, NotADirectoryError):
                continue
            except OSError:
                # Let the normal root inspection report inaccessible caches.
                pass
            candidates.append(cache)
    return candidates


def add_parsers(sub) -> None:
    parser = sub.add_parser(
        "python-cache",
        help="Inspect or prune per-invocation Python bytecode caches (unsealed-*)",
    )
    actions = parser.add_subparsers(dest="python_cache_action", required=True)
    prune = actions.add_parser(
        "prune",
        help="Report (dry run, default) or remove idle bytecode-only unsealed-* cache dirs",
    )
    prune.add_argument(
        "--root", action="append", default=None,
        help="Runtime root whose cache/python is inspected (repeatable); "
             "default: this account's plugin runtime roots",
    )
    prune.add_argument("--apply", action="store_true", help="Remove eligible directories (default: dry run)")
    prune.add_argument(
        "--min-age-hours", type=float, default=DEFAULT_MIN_AGE_SECONDS / 3600,
        help="Grace period since the newest entry was modified (default 1, minimum 1 with --apply)",
    )
    prune.add_argument(
        "--allow-age-only", action="store_true",
        help="Accept the grace period as the liveness bound when a complete process scan "
             "is unavailable (Windows/macOS) or incomplete; a failed scan still retains everything",
    )
    prune.add_argument("--json", action="store_true", help="Emit the report as JSON")


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if value < 1024 or unit == "GiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def cmd_python_cache(args: argparse.Namespace) -> int:
    min_age_hours = float(args.min_age_hours)
    if not math.isfinite(min_age_hours) or min_age_hours < 0 or (args.apply and min_age_hours < 1):
        print(
            "python-cache prune: --min-age-hours must be finite and >= 1 with --apply (>= 0 otherwise)",
            file=sys.stderr,
        )
        return 2
    roots = [Path(r) / "cache" / "python" for r in args.root] if args.root else default_cache_roots()
    liveness = live_unsealed_names()
    allow = bool(getattr(args, "allow_age_only", False))
    reports = [
        prune_unsealed(
            root, apply=args.apply, min_age_seconds=min_age_hours * 3600,
            liveness=liveness, allow_age_only=allow,
        )
        for root in roots
    ]
    failed = any(c.status == "error" for report in reports for c in report.candidates)
    if args.json:
        print(json.dumps({
            "apply": args.apply, "liveness": liveness[0], "age_only_allowed": allow,
            "roots": [r.as_dict() for r in reports],
        }, indent=2))
        return 1 if failed else 0
    if not reports:
        print("No plugin runtime Python cache roots found.")
    quiet = ("", "younger-than-grace-period", "liveness-unproven")
    for report in reports:
        summary = report.summary()
        verb = "removed" if args.apply else "eligible"
        print(
            f"{report.cache_root}: {summary.get(verb, 0)} {verb} "
            f"({_human(summary['bytes'])}), {summary.get('skipped', 0)} skipped, "
            f"{summary.get('error', 0)} error(s)"
        )
        for item in report.candidates:
            if item.status in ("error",) or item.reason not in quiet:
                print(f"  {item.status}: {item.path} ({item.reason})")
    print(f"Liveness evidence: {liveness[0]}{' + age-only (allowed)' if allow else ''}.")
    if any(c.reason == "liveness-unproven" for r in reports for c in r.candidates):
        print(
            "Idle candidates were retained because no complete process scan proved them unused. "
            "Re-run with --allow-age-only to accept the grace period as the liveness bound."
        )
    if not args.apply and any(r.summary().get("eligible") for r in reports):
        print("Dry run only. Re-run with --apply to remove eligible directories.")
    return 1 if failed else 0
