"""Conservative pruning of per-invocation ``unsealed-*`` Python bytecode caches.

Launchers select ``<runtime-root>/cache/python/installed-<digest>`` when a
runtime has valid install evidence and otherwise mint a one-off
``unsealed-<id>`` prefix. Those one-off prefixes are disposable once their
process has exited, but nothing reclaims them automatically. This module
reports (and with ``apply=True`` removes) only directories that are provably
bytecode-only, old enough to be idle, and free of reparse points.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

DEFAULT_MIN_AGE_SECONDS = 3600.0
_UNSEALED = re.compile(r"^unsealed-[A-Za-z0-9-]+$")
_BYTECODE = re.compile(r"\.pyc(?:\.\d+)?$", re.IGNORECASE)
_REPARSE_POINT = 0x400


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
            "summary": self.summary(),
            "candidates": [asdict(c) for c in self.candidates],
        }


class _Unsafe(Exception):
    """A candidate failed a safety check and must be preserved."""


def _io_path(path: str) -> str:
    if os.name != "nt":
        return path
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


def _inventory(directory: str) -> tuple[list[str], list[str], int, float]:
    """Return bytecode files, dirs (deepest first), total bytes and newest mtime."""
    files: list[str] = []
    dirs: list[str] = []
    total = 0
    st = os.stat(directory, follow_symlinks=False)
    if _is_reparse(st) or not stat.S_ISDIR(st.st_mode):
        raise _Unsafe("reparse-point")
    newest = st.st_mtime
    pending = [directory]
    while pending:
        current = pending.pop()
        dirs.append(current)
        with os.scandir(current) as entries:
            for entry in entries:
                st = entry.stat(follow_symlinks=False)
                if _is_reparse(st):
                    raise _Unsafe("reparse-point")
                newest = max(newest, st.st_mtime)
                if stat.S_ISDIR(st.st_mode):
                    pending.append(entry.path)
                elif stat.S_ISREG(st.st_mode) and _BYTECODE.search(entry.name):
                    files.append(entry.path)
                    total += st.st_size
                else:
                    raise _Unsafe("non-bytecode-content")
    dirs.sort(key=lambda value: value.count(os.sep), reverse=True)
    return files, dirs, total, newest


def _remove(files: list[str], dirs: list[str]) -> None:
    for path in files:
        os.unlink(path)
    for path in dirs:
        os.rmdir(path)


def prune_unsealed(
    cache_root: str | os.PathLike[str],
    *,
    apply: bool = False,
    min_age_seconds: float = DEFAULT_MIN_AGE_SECONDS,
    now: float | None = None,
) -> PruneReport:
    """Inspect (and optionally remove) idle ``unsealed-*`` dirs under *cache_root*.

    Only direct children named ``unsealed-*`` are considered; ``installed-*``
    and every other name are never touched. A candidate is removed only when
    its whole tree contains nothing but directories and ``.pyc`` files, holds
    no symlink/junction/reparse point, and its newest entry is older than
    *min_age_seconds*. Any error preserves the candidate (fail closed).
    """
    if min_age_seconds < 0:
        raise ValueError("min_age_seconds must be non-negative")
    root_text = os.fspath(cache_root)
    report = PruneReport(_display(os.path.abspath(root_text)), apply, float(min_age_seconds))
    io_root = _io_path(root_text)
    try:
        root_stat = os.stat(io_root, follow_symlinks=False)
    except FileNotFoundError:
        return report
    if _is_reparse(root_stat) or not stat.S_ISDIR(root_stat.st_mode):
        report.candidates.append(Candidate(report.cache_root, "skipped", reason="cache-root-not-plain-directory"))
        return report
    current_time = time.time() if now is None else now
    with os.scandir(io_root) as entries:
        names = sorted(entry.name for entry in entries if _UNSEALED.match(entry.name))
    for name in names:
        candidate_path = os.path.join(io_root, name)
        item = Candidate(_display(candidate_path), "skipped")
        report.candidates.append(item)
        try:
            files, dirs, total, newest = _inventory(candidate_path)
            item.bytes, item.files = total, len(files)
            if current_time - newest < min_age_seconds:
                item.reason = "younger-than-grace-period"
                continue
            if not apply:
                item.status = "eligible"
                continue
            files, dirs, total, newest = _inventory(candidate_path)
            if current_time - newest < min_age_seconds:
                item.reason = "younger-than-grace-period"
                continue
            _remove(files, dirs)
            item.status = "removed"
        except _Unsafe as exc:
            item.reason = str(exc)
        except OSError as exc:
            item.status = "error"
            item.reason = f"{type(exc).__name__}: {exc.strerror or exc}"
    return report


def default_cache_roots(home: str | os.PathLike[str] | None = None) -> list[Path]:
    """Runtime-owned ``cache/python`` roots for this account's plugin runtimes."""
    base = Path(home) if home is not None else Path.home()
    candidates: list[Path] = []
    for pattern in (".agent-*", ".worktree-manager", ".budget-guidance", ".copilot-extensions/*"):
        for runtime_root in sorted(base.glob(pattern)):
            cache = runtime_root / "cache" / "python"
            if cache.is_dir():
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
    if min_age_hours < 0 or (args.apply and min_age_hours < 1):
        print("python-cache prune: --min-age-hours must be >= 1 with --apply (>= 0 otherwise)", file=sys.stderr)
        return 2
    roots = [Path(r) / "cache" / "python" for r in args.root] if args.root else default_cache_roots()
    reports = [
        prune_unsealed(root, apply=args.apply, min_age_seconds=min_age_hours * 3600)
        for root in roots
    ]
    failed = any(c.status == "error" for report in reports for c in report.candidates)
    if args.json:
        print(json.dumps({"apply": args.apply, "roots": [r.as_dict() for r in reports]}, indent=2))
        return 1 if failed else 0
    if not reports:
        print("No plugin runtime Python cache roots found.")
    for report in reports:
        summary = report.summary()
        verb = "removed" if args.apply else "eligible"
        print(
            f"{report.cache_root}: {summary.get(verb, 0)} {verb} "
            f"({_human(summary['bytes'])}), {summary.get('skipped', 0)} skipped, "
            f"{summary.get('error', 0)} error(s)"
        )
        for item in report.candidates:
            if item.status in ("error",) or item.reason not in ("", "younger-than-grace-period"):
                print(f"  {item.status}: {item.path} ({item.reason})")
    if not args.apply and any(r.summary().get("eligible") for r in reports):
        print("Dry run only. Re-run with --apply to remove eligible directories.")
    return 1 if failed else 0
