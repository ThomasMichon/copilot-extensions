"""Guided triage and reclamation of legacy per-checkout ``.test-venvs``/``.test-runs``.

Test environments and runs now live in the private scratch root (see
``tools/plugin_test_resources.py``). Older runners, checkers and capture tools
wrote them inside each checkout, where they outlive the worktree's usefulness.

This is an on-demand, operator-guided tool, not automatic maintenance. It never
deletes anything a person did not explicitly select from a reviewed inventory:

1. **Inventory** (default, read-only). Reports each candidate's size, content
   fingerprint and status:

   * ``ready``: contains only recognized Python environment layout under
     directories with ``pyvenv.cfg``;
   * ``needs-decision``: also contains unclassified content (run output,
     captured evidence, reports), listed for review;
   * ``refused``: cannot be reclaimed safely (see ``reason``).

   ``--checkout`` limits the inventory to specific checkouts.

2. **Plan**. ``--select PATH`` (repeatable) with ``--plan-out FILE`` records the
   chosen set, bound to each target's inventoried identity and metadata fingerprint.
   A ``needs-decision`` target is accepted only with a matching ``--discard
   PATH`` decision to discard its unclassified content.

3. **Apply**. ``--apply-plan FILE --confirm-writers-stopped`` deletes only the
   planned targets after the operator confirms test/capture writers are stopped. Each
   target is revalidated at the mutation boundary: it must still be an
   ignored, untracked, link-free directory at a registered worktree's root,
   older than ``--min-age-hours``. It is then renamed aside; the renamed tree
   must be the same directory with an unchanged fingerprint and no detected
   live user before removal. Any mismatch or inspection failure refuses the
   target and attempts to restore its name, reporting any restoration failure.

Stop test and capture writers for the selected set before applying a plan.
Liveness: on Windows the rename refuses sharing violations, but is not a lock
against all possible users. On Linux ``/proc`` is scanned for this user's processes whose
cwd, executable, open files or mapped files are inside the tree. A process of this user that
cannot be inspected (for example a non-dumpable ``sshd`` or ``systemd --user``)
refuses the target unless its name is accepted with ``--accept-uninspectable``
when planning; the plan records that acceptance. Other platforms are refused.
These checks detect known users at that moment; they do not prove exclusive
use afterwards or make this safe against concurrent or adversarial mutation.

A removal that fails part-way is reported as ``partial``: entries already
deleted are not recoverable. The remainder is moved back to its original name
when possible, otherwise left at the reported ``.reclaiming-*`` path.
The whole plan's field types are validated before the first deletion; filesystem
eligibility is still rechecked separately for each target.

Run:
  python tools/reclaim_legacy_test_dirs.py [--checkout DIR ...] [--json]
  python tools/reclaim_legacy_test_dirs.py --select PATH [--discard PATH] --plan-out plan.json
  python tools/reclaim_legacy_test_dirs.py --apply-plan plan.json --confirm-writers-stopped
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

LEGACY_NAMES = (".test-venvs", ".test-runs")
PLAN_VERSION = 1
_REPARSE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
_GIT_TIMEOUT = 30
_SAMPLE_LIMIT = 20
_WINDOWS = os.name == "nt"
_ENV_DIRS = {"bin", "scripts", "lib", "lib64", "include", "share"}
_ENV_FILES = {"pyvenv.cfg", "python", "python.exe", ".gitignore", ".typecheck-prepared"}
_EVIDENCE_DIRS = {"reports", "evidence", "captures", "artifacts", "logs", "test-results"}
_GIT_SELECTORS = {
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_CEILING_DIRECTORIES",
    "GIT_NAMESPACE", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
}


@dataclass
class Candidate:
    checkout: str
    path: str
    bytes: int = 0
    newest_mtime: float = 0.0
    identity: list[int] = field(default_factory=list)
    fingerprint: str = ""
    status: str = "pending"
    reason: str = ""
    unclassified_count: int = 0
    unclassified_bytes: int = 0
    unclassified_sample: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


@dataclass
class Survey:
    bytes: int = 0
    newest: float = 0.0
    fingerprint: str = ""
    unclassified: list[tuple[str, int]] = field(default_factory=list)
    problem: str | None = None


def git_environment() -> dict[str, str]:
    """Keep inherited repository selectors from redirecting admission probes."""
    env = {
        key: value for key, value in os.environ.items()
        if key.upper() not in _GIT_SELECTORS and not key.upper().startswith("GIT_CONFIG")
    }
    env.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return env


def _git(checkout: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(checkout), *args], env=git_environment(),
        capture_output=True, text=True, encoding="utf-8", errors="surrogateescape",
        timeout=_GIT_TIMEOUT, check=False,
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )


def registered_worktrees(repo: Path) -> list[Path]:
    result = _git(repo, "worktree", "list", "--porcelain", "-z")
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "git worktree list failed")
    return [
        Path(line[len("worktree "):]) for line in result.stdout.split("\0")
        if line.startswith("worktree ")
    ]


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


def _identity(info: os.stat_result) -> list[int]:
    return [int(info.st_dev), int(info.st_ino)]


def _present(path: Path) -> bool:
    try:
        os.lstat(_native(path))
    except FileNotFoundError:
        return False
    return True


def survey(path: Path) -> Survey:
    """Measure, fingerprint and classify a tree without following links.

    Recognized environment layout is disposable. Other contents and evidence
    directories remain unclassified even when nested inside an environment.
    """
    result = Survey()
    digest = hashlib.sha256()
    rows: list[str] = []
    stack: list[tuple[Path, str, str | None]] = [(path, "", None)]
    while stack:
        current, relative, env_root = stack.pop()
        try:
            entries = list(os.scandir(_native(current)))
        except OSError as exc:
            result.problem = f"unreadable: {exc.strerror or exc}"
            return result
        if env_root is None and any(
            entry.name == "pyvenv.cfg" and entry.is_file(follow_symlinks=False)
            for entry in entries
        ):
            env_root = relative
        for entry in entries:
            entry_path = Path(entry.path)
            child = f"{relative}/{entry.name}" if relative else entry.name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                result.problem = f"unreadable: {exc.strerror or exc}"
                return result
            if _is_link(info, entry_path):
                result.problem = "contains a link or reparse point"
                return result
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                result.problem = "contains a special file (socket, pipe or device)"
                return result
            result.newest = max(result.newest, info.st_mtime)
            if stat.S_ISDIR(info.st_mode):
                rows.append(f"d\0{child}\0{info.st_mtime_ns}")
                stack.append((entry_path, child, env_root))
            else:
                rows.append(f"f\0{child}\0{info.st_size}\0{info.st_mtime_ns}")
                result.bytes += info.st_size
                local = child[len(env_root) + 1:] if env_root else child
                parts = local.lower().split("/")
                disposable = env_root is not None and (
                    parts[0] in _ENV_DIRS or local.lower() in _ENV_FILES
                ) and not _EVIDENCE_DIRS.intersection(child.lower().split("/")[:-1])
                if not disposable:
                    result.unclassified.append((child, info.st_size))
    for row in sorted(rows):
        digest.update(row.encode("utf-8", "surrogateescape") + b"\n")
    result.fingerprint = digest.hexdigest()
    result.unclassified.sort()
    return result


def _references_tree(target: str, prefix: str) -> bool:
    if not os.path.isabs(target):
        return False
    if target == prefix or target.startswith(prefix + os.sep):
        return True
    resolved = os.path.realpath(target)
    return resolved == prefix or resolved.startswith(prefix + os.sep)


def _linux_scan(path: Path) -> tuple[bool, set[str]] | None:
    """Scan this user's processes; None when ``/proc`` is unavailable.

    Returns whether a process's cwd, executable, open or mapped file is inside ``path``
    and the names of this user's processes that could not be inspected (for
    example non-dumpable session managers). A process that exits mid-scan is
    absent, not uninspectable.
    """
    proc = Path("/proc")
    if not proc.is_dir() or not hasattr(os, "getuid"):
        return None
    prefix = os.path.realpath(path)
    uid = os.getuid()
    gone = (FileNotFoundError, ProcessLookupError)
    blind: set[str] = set()
    try:
        pids = [entry for entry in proc.iterdir() if entry.name.isdigit()]
    except OSError:
        return None

    def name(pid: Any) -> str:
        try:
            return (pid / "comm").read_text(encoding="utf-8", errors="replace").strip() or "?"
        except OSError:
            return f"pid {pid.name}"

    for pid in pids:
        try:
            if pid.stat().st_uid != uid:
                continue
            links = [pid / "cwd", pid / "exe", *(pid / "fd").iterdir()]
        except gone:
            continue
        except OSError:
            blind.add(name(pid))
            continue
        for link in links:
            try:
                target = os.readlink(link)
            except gone:
                continue
            except OSError:
                blind.add(name(pid))
                break
            if _references_tree(target, prefix):
                return True, blind
        try:
            maps = (pid / "maps").read_text(encoding="utf-8", errors="surrogateescape")
        except gone:
            continue
        except OSError:
            blind.add(name(pid))
            continue
        for row in maps.splitlines():
            fields = row.split(maxsplit=5)
            if len(fields) == 6:
                target = fields[5].replace("\\012", "\n")
                if _references_tree(target, prefix):
                    return True, blind
    return False, blind


def in_use(path: Path, accepted: frozenset[str] = frozenset()) -> tuple[bool | None, str]:
    """Detected use of ``path``: (True/False/None-if-unverifiable, refusal reason).

    ``accepted`` names uninspectable processes the operator chose to accept.
    """
    if _WINDOWS:
        return False, ""  # verified by the exclusive rename at the mutation boundary
    scan = _linux_scan(path)
    if scan is None:
        return None, "process use cannot be verified"
    found, blind = scan
    if found:
        return True, "in use by a live process"
    unexpected = sorted(blind - accepted)
    if unexpected:
        return None, "process use cannot be verified; cannot inspect: " + ", ".join(unexpected)
    return False, ""


def evaluate(checkout: Path, path: Path, *, min_age: float, now: float,
             accepted: frozenset[str] = frozenset()) -> Candidate:
    candidate = Candidate(checkout=str(checkout), path=str(path))

    def refuse(reason: str) -> Candidate:
        candidate.status, candidate.reason = "refused", reason
        return candidate

    try:
        info = os.lstat(path)
    except OSError as exc:
        return refuse(f"unreadable: {exc.strerror or exc}")
    if _is_link(info, path) or not stat.S_ISDIR(info.st_mode):
        return refuse("not a plain directory")
    candidate.identity = _identity(info)
    relative = path.name + "/"
    tracked = _git(checkout, "ls-files", "-z", "--", relative)
    if tracked.returncode != 0:
        return refuse("tracked-file check failed")
    if tracked.stdout:
        return refuse("contains tracked files")
    ignored = _git(checkout, "check-ignore", "-q", relative)
    if ignored.returncode == 1:
        return refuse("not ignored by Git")
    if ignored.returncode != 0:
        return refuse("ignore check failed")
    found = survey(path)
    candidate.bytes, candidate.fingerprint = found.bytes, found.fingerprint
    candidate.newest_mtime = max(found.newest, info.st_mtime)
    candidate.unclassified_count = len(found.unclassified)
    candidate.unclassified_bytes = sum(size for _name, size in found.unclassified)
    candidate.unclassified_sample = [name for name, _size in found.unclassified[:_SAMPLE_LIMIT]]
    if found.problem:
        return refuse(found.problem)
    if now - candidate.newest_mtime < min_age:
        return refuse("recently modified")
    use, why = in_use(path, accepted)
    if use is not False:
        return refuse(why)
    candidate.status = "needs-decision" if found.unclassified else "ready"
    return candidate


def _remove(path: Path, errors: list[str]) -> None:
    def report(_func: Any, target: str, exc: object) -> None:
        detail = exc[1] if isinstance(exc, tuple) else exc
        errors.append(f"{target}: {detail}")

    if sys.version_info >= (3, 12):
        shutil.rmtree(_native(path), onexc=report)
    else:
        shutil.rmtree(_native(path), onerror=report)


def reclaim(candidate: Candidate, planned: dict[str, Any]) -> None:
    """Delete one revalidated target; ``candidate`` was evaluated just now."""
    path = Path(candidate.path)
    if candidate.identity != planned["identity"] or candidate.fingerprint != planned["fingerprint"]:
        candidate.status, candidate.reason = "refused", "changed since the plan was reviewed"
        return
    if candidate.status == "needs-decision" and not planned.get("discard_unclassified"):
        candidate.status, candidate.reason = "refused", "unclassified content without a discard decision"
        return
    tombstone = path.with_name(f"{path.name}.reclaiming-{os.getpid()}")
    try:
        if _present(tombstone):
            candidate.status, candidate.reason = "refused", "quarantine path already exists"
            return
        os.rename(_native(path), _native(tombstone))
    except OSError as exc:
        candidate.status, candidate.reason = "refused", f"quarantine/rename failed: {exc.strerror or exc}"
        return

    def restore(reason: str, *, partial: bool = False) -> None:
        candidate.status, candidate.reason = "partial" if partial else "refused", reason
        try:
            if _present(path):
                candidate.errors.append(f"original path exists; inspect {tombstone}")
                return
            os.rename(_native(tombstone), _native(path))
        except OSError as exc:
            candidate.errors.append(f"restore failed; inspect {tombstone}: {exc.strerror or exc}")

    try:
        info = os.lstat(tombstone)
    except OSError as exc:
        restore(f"renamed tree unreadable: {exc.strerror or exc}")
        return
    if _identity(info) != planned["identity"]:
        restore("renamed tree is not the reviewed directory")
        return
    try:
        found = survey(tombstone)
    except (OSError, ValueError) as exc:
        restore(f"renamed tree inspection failed: {exc}")
        return
    if found.problem or found.fingerprint != planned["fingerprint"]:
        restore(found.problem or "changed since the plan was reviewed")
        return
    try:
        use, why = in_use(tombstone, frozenset(planned.get("accepted_uninspectable", ())))
    except (OSError, ValueError) as exc:
        restore(f"renamed tree inspection failed: {exc}")
        return
    if use is not False:
        restore(why)
        return
    try:
        _remove(tombstone, candidate.errors)
    except (OSError, ValueError) as exc:
        candidate.errors.append(f"removal interrupted: {exc}")
    try:
        remaining = _present(tombstone)
    except OSError as exc:
        candidate.status, candidate.reason = "partial", "removal outcome could not be verified"
        candidate.errors.append(f"inspect {tombstone}: {exc}")
        return
    if not remaining:
        candidate.status = "reclaimed"
        return
    restore("removal incomplete; some entries may already be gone", partial=True)


def _key(path: Path | str) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def inspect_candidate(checkout: Path, path: Path, *, min_age: float, now: float,
                      accepted: frozenset[str] = frozenset()) -> Candidate:
    try:
        return evaluate(checkout, path, min_age=min_age, now=now, accepted=accepted)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return Candidate(checkout=str(checkout), path=str(path), status="refused",
                         reason=f"inspection failed: {exc}")


def inventory(repo: Path, checkouts: list[Path] | None, *, min_age: float, now: float,
              accepted: frozenset[str] = frozenset()) -> list[Candidate]:
    registered = registered_worktrees(repo)
    if checkouts:
        wanted = {_key(c) for c in checkouts}
        unknown = wanted - {_key(c) for c in registered}
        if unknown:
            raise ValueError(f"not a registered worktree: {', '.join(sorted(unknown))}")
        registered = [c for c in registered if _key(c) in wanted]
    candidates = []
    for checkout in registered:
        for name in LEGACY_NAMES:
            path = checkout / name
            try:
                present = _present(path)
            except OSError:
                present = True
            if present:
                candidates.append(inspect_candidate(checkout, path, min_age=min_age, now=now,
                                                    accepted=accepted))
    return candidates


def write_plan(candidates: list[Candidate], select: list[Path], discard: list[Path],
               repo: Path, out: Path, accepted: frozenset[str] = frozenset()) -> list[str]:
    by_path = {_key(c.path): c for c in candidates}
    discard_keys = {_key(p) for p in discard}
    problems, entries = [], []
    for key in sorted(discard_keys - {_key(p) for p in select}):
        problems.append(f"{key}: --discard without --select")
    for raw in select:
        candidate = by_path.get(_key(raw))
        if candidate is None:
            problems.append(f"{raw}: not a legacy candidate in this inventory")
        elif candidate.status == "refused":
            problems.append(f"{raw}: refused ({candidate.reason})")
        elif candidate.status == "needs-decision" and _key(raw) not in discard_keys:
            problems.append(f"{raw}: has {candidate.unclassified_count} unclassified files; "
                            "review them and add --discard to discard them")
        else:
            entries.append({
                "checkout": candidate.checkout, "path": candidate.path,
                "identity": candidate.identity, "fingerprint": candidate.fingerprint,
                "bytes": candidate.bytes,
                "discard_unclassified": _key(raw) in discard_keys,
                "accepted_uninspectable": sorted(accepted),
            })
    if problems:
        return problems
    out.write_text(json.dumps({
        "version": PLAN_VERSION, "repo": str(repo), "created": time.time(), "targets": entries,
    }, indent=2) + "\n", encoding="utf-8")
    return []


def apply_plan(plan_path: Path, *, min_age: float, now: float) -> list[Candidate]:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or plan.get("version") != PLAN_VERSION:
        raise ValueError("unsupported plan file")
    if not isinstance(plan.get("repo"), str) or not plan["repo"]:
        raise ValueError("plan must name its repository")
    if not isinstance(plan.get("targets"), list):
        raise ValueError("plan must contain a target list")
    for entry in plan["targets"]:
        if not isinstance(entry, dict):
            raise ValueError("invalid plan target")
        if any(not isinstance(entry.get(key), str) or not entry[key]
               for key in ("checkout", "path", "fingerprint")):
            raise ValueError("plan target must name its checkout, path and fingerprint")
        identity = entry.get("identity")
        if not isinstance(identity, list) or len(identity) != 2 or any(
            type(value) is not int for value in identity
        ):
            raise ValueError("invalid plan target identity")
        if type(entry.get("discard_unclassified", False)) is not bool:
            raise ValueError("invalid plan discard decision")
        accepted = entry.get("accepted_uninspectable", [])
        if not isinstance(accepted, list) or any(
            not isinstance(name, str) or not name for name in accepted
        ):
            raise ValueError("invalid plan inspection exceptions")
    registered = {_key(c) for c in registered_worktrees(Path(plan["repo"]))}
    results = []
    for planned in plan["targets"]:
        checkout, path = Path(planned["checkout"]), Path(planned["path"])
        if (_key(checkout) not in registered or path.name not in LEGACY_NAMES
                or _key(path.parent) != _key(checkout)):
            results.append(Candidate(checkout=str(checkout), path=str(path), status="refused",
                                     reason="not a legacy directory of a registered worktree"))
            continue
        candidate = inspect_candidate(checkout, path, min_age=min_age, now=now,
                                      accepted=frozenset(planned.get("accepted_uninspectable", ())))
        if candidate.status != "refused":
            reclaim(candidate, planned)
        results.append(candidate)
    return results


def _report(candidates: list[Candidate], *, as_json: bool, mode: str) -> None:
    totals: dict[str, list[int]] = {}
    for candidate in candidates:
        bucket = totals.setdefault(candidate.status, [0, 0])
        bucket[0] += 1
        bucket[1] += candidate.bytes
    if as_json:
        print(json.dumps({
            "mode": mode,
            "candidates": [asdict(c) for c in candidates],
            "totals": {k: {"count": v[0], "bytes": v[1]} for k, v in totals.items()},
        }, indent=2))
        return
    for candidate in candidates:
        detail = f" ({candidate.reason})" if candidate.reason else ""
        print(f"[{candidate.status}] {candidate.path} {candidate.bytes / 2**20:.1f} MiB{detail}")
        if candidate.unclassified_count:
            print(f"    unclassified: {candidate.unclassified_count} files, "
                  f"{candidate.unclassified_bytes / 2**20:.1f} MiB")
            for name in candidate.unclassified_sample:
                print(f"      {name}")
        for error in candidate.errors:
            print(f"    error: {error}")
    for status, (count, size) in sorted(totals.items()):
        print(f"{status}: {count} directories, {size / 2**30:.2f} GiB")
    if mode == "inventory" and ({"ready", "needs-decision"} & totals.keys()):
        print("Inventory only. Select reviewed targets with --select (and --discard for "
              "needs-decision targets) plus --plan-out, then run --apply-plan.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1],
                    help="any checkout of the repository (default: this one)")
    ap.add_argument("--checkout", type=Path, action="append", default=[],
                    help="limit the inventory to this registered worktree (repeatable)")
    ap.add_argument("--select", type=Path, action="append", default=[],
                    help="legacy directory to include in the plan (repeatable)")
    ap.add_argument("--discard", type=Path, action="append", default=[],
                    help="selected target whose unclassified content may be discarded (repeatable)")
    ap.add_argument("--plan-out", type=Path, help="write the selected targets to this plan file")
    ap.add_argument("--apply-plan", type=Path, help="delete the targets recorded in this plan")
    ap.add_argument("--confirm-writers-stopped", action="store_true",
                    help="confirm test/capture writers for the planned set are stopped; not a lock")
    ap.add_argument("--min-age-hours", type=float, default=24.0,
                    help="refuse trees modified more recently (default: 24)")
    ap.add_argument("--accept-uninspectable", action="append", default=[], metavar="NAME",
                    help="Linux: accept this user's process NAME (as in /proc/<pid>/comm) "
                         "although its open files cannot be inspected (repeatable)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args(argv)
    if args.min_age_hours < 0:
        ap.error("--min-age-hours must be non-negative")
    if args.apply_plan and (args.select or args.discard or args.plan_out or args.checkout
                            or args.accept_uninspectable):
        ap.error("--apply-plan cannot be combined with inventory or selection options")
    if args.apply_plan and not args.confirm_writers_stopped:
        ap.error("stop test/capture writers for the planned set, then add --confirm-writers-stopped")
    if args.confirm_writers_stopped and not args.apply_plan:
        ap.error("--confirm-writers-stopped requires --apply-plan")
    if bool(args.select) != bool(args.plan_out):
        ap.error("--select and --plan-out must be used together")
    if args.discard and not args.select:
        ap.error("--discard requires --select")
    min_age, now = args.min_age_hours * 3600, time.time()
    accepted = frozenset(args.accept_uninspectable)
    try:
        if args.apply_plan:
            results = apply_plan(args.apply_plan, min_age=min_age, now=now)
            _report(results, as_json=args.json, mode="apply")
            return 0 if all(c.status == "reclaimed" for c in results) else 1
        candidates = inventory(args.repo, args.checkout, min_age=min_age, now=now,
                               accepted=accepted)
    except (RuntimeError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.plan_out:
        problems = write_plan(candidates, args.select, args.discard, args.repo, args.plan_out,
                              accepted)
        for problem in problems:
            print(f"error: {problem}", file=sys.stderr)
        if problems:
            return 2
    _report(candidates, as_json=args.json, mode="plan" if args.plan_out else "inventory")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
