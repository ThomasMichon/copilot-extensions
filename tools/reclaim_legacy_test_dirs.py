"""Report and reclaim legacy per-checkout ``.test-venvs``/``.test-runs`` trees.

Test environments and runs now live in the private scratch root (see
``tools/plugin_test_resources.py``). Older runners and checkers wrote them
inside each checkout, where they outlive the worktree's useful life.

Dry-run by default; ``--apply`` deletes. A candidate is reclaimed only when:

* it is a real directory directly at a registered worktree's root;
* Git ignores it and tracks nothing beneath it;
* neither it nor anything inside it is a symlink, junction or reparse point;
* nothing inside it changed within ``--min-age-hours``;
* no live process uses it (Windows: an exclusive rename probe; Linux:
  ``/proc`` cwd/exe/fd scan; elsewhere the candidate is refused).

Any uncertainty refuses that candidate and leaves it untouched.

Run:  python tools/reclaim_legacy_test_dirs.py [--apply] [--json]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

LEGACY_NAMES = (".test-venvs", ".test-runs")
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_GIT_TIMEOUT = 30


@dataclass
class Candidate:
    checkout: str
    path: str
    bytes: int = 0
    newest_mtime: float = 0.0
    status: str = "pending"
    reason: str = ""
    errors: list[str] = field(default_factory=list)


def _git(checkout: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(checkout), *args],
        capture_output=True, text=True, timeout=_GIT_TIMEOUT, check=False,
    )


def registered_worktrees(repo: Path) -> list[Path]:
    result = _git(repo, "worktree", "list", "--porcelain")
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git worktree list failed")
    roots = []
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            roots.append(Path(line[len("worktree "):]))
    return roots


def _is_link(info: os.stat_result, path: Path) -> bool:
    if stat.S_ISLNK(info.st_mode):
        return True
    if getattr(info, "st_file_attributes", 0) & _REPARSE:
        return True
    is_junction = getattr(os.path, "isjunction", None)
    return bool(is_junction and is_junction(path))


def _native(path: Path) -> str:
    text = str(path)
    if os.name != "nt" or text.startswith("\\\\?\\"):
        return text
    if text.startswith("\\\\"):
        return "\\\\?\\UNC\\" + text[2:]
    return "\\\\?\\" + text


def measure(path: Path) -> tuple[int, float, str | None]:
    """Total bytes, newest mtime, or a refusal reason; never follows links."""
    total, newest = 0, 0.0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(_native(current)))
        except OSError as exc:
            return total, newest, f"unreadable: {exc.strerror or exc}"
        for entry in entries:
            entry_path = Path(entry.path)
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                return total, newest, f"unreadable: {exc.strerror or exc}"
            if _is_link(info, entry_path):
                return total, newest, "contains a link or reparse point"
            newest = max(newest, info.st_mtime)
            if stat.S_ISDIR(info.st_mode):
                stack.append(entry_path)
            else:
                total += info.st_size
    return total, newest, None


def _linux_in_use(path: Path) -> bool | None:
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    prefix = str(path.resolve())
    for pid in proc.iterdir():
        if not pid.name.isdigit():
            continue
        links = [pid / "cwd", pid / "exe"]
        try:
            links.extend((pid / "fd").iterdir())
        except OSError:
            pass
        for link in links:
            try:
                target = os.readlink(link)
            except OSError:
                continue
            if target == prefix or target.startswith(prefix + os.sep):
                return True
    return False


def _windows_rename_probe(path: Path) -> Path | None:
    """Rename succeeds only when no open handle pins the tree; return tombstone."""
    tombstone = path.with_name(f"{path.name}.reclaiming-{os.getpid()}")
    try:
        os.rename(_native(path), _native(tombstone))
    except OSError:
        return None
    return tombstone


def _remove(path: Path, errors: list[str]) -> None:
    def onerror(func, target, exc_info):
        try:
            os.chmod(target, stat.S_IWRITE)
            func(target)
        except OSError as exc:
            errors.append(f"{target}: {exc.strerror or exc}")

    kwargs = {"onexc": lambda f, p, e: onerror(f, p, None)} if sys.version_info >= (3, 12) \
        else {"onerror": onerror}
    shutil.rmtree(_native(path), **kwargs)


def evaluate(checkout: Path, path: Path, *, min_age: float, now: float) -> Candidate:
    candidate = Candidate(checkout=str(checkout), path=str(path))
    try:
        info = os.lstat(path)
    except OSError as exc:
        candidate.status, candidate.reason = "refused", f"unreadable: {exc.strerror or exc}"
        return candidate
    if _is_link(info, path) or not stat.S_ISDIR(info.st_mode):
        candidate.status, candidate.reason = "refused", "not a plain directory"
        return candidate
    relative = path.name + "/"
    tracked = _git(checkout, "ls-files", "-z", "--", relative)
    if tracked.returncode != 0 or tracked.stdout:
        candidate.status, candidate.reason = "refused", "contains tracked files"
        return candidate
    ignored = _git(checkout, "check-ignore", "-q", relative)
    if ignored.returncode != 0:
        candidate.status, candidate.reason = "refused", "not ignored by Git"
        return candidate
    total, newest, problem = measure(path)
    candidate.bytes, candidate.newest_mtime = total, max(newest, info.st_mtime)
    if problem:
        candidate.status, candidate.reason = "refused", problem
    elif now - candidate.newest_mtime < min_age:
        candidate.status, candidate.reason = "refused", "recently modified"
    elif os.name != "nt":
        in_use = _linux_in_use(path)
        if in_use is None:
            candidate.status, candidate.reason = "refused", "process use cannot be verified"
        elif in_use:
            candidate.status, candidate.reason = "refused", "in use by a live process"
        else:
            candidate.status = "eligible"
    else:
        candidate.status = "eligible"
    return candidate


def reclaim(candidate: Candidate) -> None:
    path = Path(candidate.path)
    if os.name == "nt":
        target = _windows_rename_probe(path)
        if target is None:
            candidate.status, candidate.reason = "refused", "in use by a live process"
            return
    else:
        target = path
    _remove(target, candidate.errors)
    if os.path.lexists(_native(target)):
        if target != path:
            try:
                os.rename(_native(target), _native(path))
            except OSError as exc:
                candidate.errors.append(f"restore failed: {exc.strerror or exc}")
        candidate.status, candidate.reason = "partial", "some entries could not be removed"
    else:
        candidate.status = "reclaimed"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1],
                    help="any checkout of the repository (default: this one)")
    ap.add_argument("--apply", action="store_true", help="delete eligible directories")
    ap.add_argument("--min-age-hours", type=float, default=24.0,
                    help="refuse trees modified more recently (default: 24)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args(argv)
    if args.min_age_hours < 0:
        ap.error("--min-age-hours must be non-negative")
    now = time.time()
    candidates = []
    for checkout in registered_worktrees(args.repo):
        for name in LEGACY_NAMES:
            path = checkout / name
            if os.path.lexists(path):
                candidates.append(evaluate(
                    checkout, path, min_age=args.min_age_hours * 3600, now=now,
                ))
    if args.apply:
        for candidate in candidates:
            if candidate.status == "eligible":
                reclaim(candidate)
    totals: dict[str, list[int]] = {}
    for candidate in candidates:
        bucket = totals.setdefault(candidate.status, [0, 0])
        bucket[0] += 1
        bucket[1] += candidate.bytes
    if args.json:
        print(json.dumps({
            "apply": args.apply,
            "candidates": [asdict(c) for c in candidates],
            "totals": {k: {"count": v[0], "bytes": v[1]} for k, v in totals.items()},
        }, indent=2))
    else:
        for candidate in candidates:
            detail = f" ({candidate.reason})" if candidate.reason else ""
            print(f"[{candidate.status}] {candidate.path} "
                  f"{candidate.bytes / 2**20:.1f} MiB{detail}")
        for status, (count, size) in sorted(totals.items()):
            print(f"{status}: {count} directories, {size / 2**30:.2f} GiB")
        if not args.apply and "eligible" in totals:
            print("Dry run; re-run with --apply to delete eligible directories.")
    return 1 if any(c.status == "partial" for c in candidates) else 0


if __name__ == "__main__":
    raise SystemExit(main())
