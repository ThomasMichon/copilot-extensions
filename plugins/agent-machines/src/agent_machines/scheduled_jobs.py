"""``scheduled-job`` resources: declaration validation, machine-local state,
and the body of ``agent-machines scheduled-job run <id>``.

A scheduled job runs one console-script entrypoint of a centrally installed
application on a fixed interval. The declaring requirement package is the
only authority; the sole local knob is an ``interval_seconds`` override.
Everything machine-local lives under ``~/.agent-machines/scheduled-jobs/``.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .surfaces._common import read_json, write_json_atomic

MIN_INTERVAL_SECONDS = 300
MAX_INTERVAL_SECONDS = 7 * 24 * 3600
DEFAULT_TIMEOUT_SECONDS = 900
LOG_MAX_BYTES = 256 * 1024

_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_FIELDS = {
    "type",
    "id",
    "state",
    "platforms",
    "gate",
    "owner",
    "maintenance_safe",
    "authority",
    "command",
    "interval_seconds",
    "timeout_seconds",
}


def _int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate(decl: dict[str, Any]) -> None:
    """Raise ``ValueError`` for an invalid ``scheduled-job`` declaration.

    A ``state: absent`` declaration needs only its ``id``."""
    job = decl.get("id")
    if set(decl) - _FIELDS:
        raise ValueError(
            f"scheduled-job {job!r} has unsupported fields {sorted(set(decl) - _FIELDS)}"
        )
    if not isinstance(job, str) or not _ID.fullmatch(job):
        raise ValueError(f"scheduled-job id {job!r} must match [a-z0-9][a-z0-9-]* (max 64 chars)")
    if decl.get("state") == "absent" and not set(decl) & {
        "command",
        "interval_seconds",
        "timeout_seconds",
    }:
        return
    command = decl.get("command")
    if not isinstance(command, dict) or set(command) - {"application", "entrypoint", "args"}:
        raise ValueError(
            f"scheduled-job {job!r} command must map application, entrypoint, and args"
        )
    for key in ("application", "entrypoint"):
        value = command.get(key)
        if (
            not isinstance(value, str)
            or not value.strip()
            or value.startswith("-")
            or "/" in value
            or "\\" in value
            or value in {".", ".."}
        ):
            raise ValueError(
                f"scheduled-job {job!r} command.{key} must be a bare name, not a path"
            )
    args = command.get("args", [])
    if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
        raise ValueError(f"scheduled-job {job!r} command.args must be a list of strings")
    interval = decl.get("interval_seconds")
    if not _int(interval) or not MIN_INTERVAL_SECONDS <= interval <= MAX_INTERVAL_SECONDS:
        raise ValueError(
            f"scheduled-job {job!r} interval_seconds must be an integer in "
            f"{MIN_INTERVAL_SECONDS}..{MAX_INTERVAL_SECONDS}"
        )
    timeout = decl.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not _int(timeout) or not 1 <= timeout < interval:
        raise ValueError(
            f"scheduled-job {job!r} timeout_seconds must be a positive integer "
            "below interval_seconds"
        )


# --------------------------------------------------------------------------- #
# Machine-local state
# --------------------------------------------------------------------------- #
def jobs_root(home: Path | None = None) -> Path:
    base = home if home is not None else Path.home()
    return (
        base / ".agent-machines" / "scheduled-jobs"
    )  # marketplace-isolation: allow legacy-compatibility


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def effective_interval(
    job_id: str, declared: int, timeout: int, home: Path | None = None
) -> tuple[int, str]:
    """The interval to schedule and its source (``declared``/``override``).

    An override is clamped into ``max(MIN, timeout + 1)..MAX`` so it can never
    schedule runs faster than the floor or shorter than one run's timeout."""
    entry = read_json(jobs_root(home) / "overrides.json").get(job_id)
    value = entry.get("interval_seconds") if isinstance(entry, dict) else None
    if not _int(value):
        return declared, "declared"
    return min(max(value, MIN_INTERVAL_SECONDS, timeout + 1), MAX_INTERVAL_SECONDS), "override"


def read_owned(home: Path | None = None) -> dict[str, dict[str, Any]]:
    jobs = read_json(jobs_root(home) / "owned.json").get("jobs")
    return {k: v for k, v in jobs.items() if isinstance(v, dict)} if isinstance(jobs, dict) else {}


def _write_owned(home: Path | None, jobs: dict[str, dict[str, Any]]) -> None:
    write_json_atomic(jobs_root(home) / "owned.json", {"version": 1, "jobs": jobs})


def record_owned(home: Path | None, job_id: str, sources: list[str]) -> None:
    jobs = read_owned(home)
    if jobs.get(job_id, {}).get("sources") != sources:
        jobs[job_id] = {"sources": sources, "registered_at": _now()}
        _write_owned(home, jobs)


def forget_owned(home: Path | None, job_id: str) -> None:
    jobs = read_owned(home)
    if jobs.pop(job_id, None) is not None:
        _write_owned(home, jobs)


def _norm(path: str | Path) -> str:
    return os.path.normcase(os.path.abspath(str(path)))


def _under(path: str, root: str) -> bool:
    try:
        return os.path.commonpath([path, root]) == root
    except ValueError:  # different drives
        return False


def withdrawal_confirmed(sources: list[str], repo_roots: list[Path], examined: set[str]) -> bool:
    """Whether an owned job's declaring files prove it is no longer declared.

    Every recorded source must lie in a repo root that is present and
    readable now, and must either be deleted or have been examined in this
    resolution (so it no longer declares the job). An unreachable repo, an
    unexamined file or no recorded source is never proof of withdrawal.
    """
    roots = [_norm(r) for r in repo_roots if Path(r).is_dir() and os.access(r, os.R_OK)]
    seen = {_norm(path) for path in examined}
    if not sources or not roots:
        return False
    for source in sources:
        path = _norm(source)
        if not any(_under(path, root) for root in roots):
            return False
        if Path(source).exists() and path not in seen:
            return False
    return True


def read_state(job_id: str, home: Path | None = None) -> dict[str, Any]:
    return read_json(jobs_root(home) / "state" / f"{job_id}.json")


# --------------------------------------------------------------------------- #
# Running one job
# --------------------------------------------------------------------------- #
def resolve_entrypoint(entrypoint: str, home: Path | None = None) -> str | None:
    """The installed console script, preferring the user tool bin dir."""
    base = home if home is not None else Path.home()
    return shutil.which(entrypoint, path=str(base / ".local" / "bin")) or shutil.which(entrypoint)


def _lock(job_id: str, home: Path | None):
    from .self_update_lock import _PosixFileLock, _WindowsMutex

    if sys.platform == "win32":
        return _WindowsMutex(f"Local\\AgentMachinesScheduledJob_{job_id}")
    return _PosixFileLock(jobs_root(home) / "locks" / f"{job_id}.flock")


def _append_log(path: Path, header: str, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", errors="replace") as handle:
        handle.write(f"=== {header}\n{text.rstrip()}\n" if text.strip() else f"=== {header}\n")
    if path.stat().st_size > LOG_MAX_BYTES:
        tail = path.read_bytes()[-(LOG_MAX_BYTES // 2) :]
        path.write_bytes(tail[tail.find(b"\n") + 1 :])


def run_job(job_id: str, desired: dict[str, Any], *, home: Path | None = None, runner=None) -> int:
    """Run one declared job once; a concurrent run of the same id is a no-op."""
    from .self_update_types import TIMEOUT_RETURNCODE, CommandResult, default_command_runner

    root = jobs_root(home)
    root.mkdir(parents=True, exist_ok=True)
    lock = _lock(job_id, home)
    try:
        if lock.try_acquire() == "timeout":
            return 0
        try:
            command = desired["command"]
            timeout = int(desired.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS)
            started = _now()
            binary = resolve_entrypoint(command["entrypoint"], home)
            if binary is None:
                result = CommandResult(
                    [command["entrypoint"]],
                    127,
                    "",
                    (
                        f"entrypoint {command['entrypoint']!r} of application "
                        f"{command['application']!r} is not installed"
                    ),
                )
            else:
                argv = [binary, *command.get("args", [])]
                result = (runner or default_command_runner)(argv, cwd=root, timeout=timeout)
            timed_out = result.returncode == TIMEOUT_RETURNCODE and "timed out after" in (
                result.stderr or ""
            )
            state = {
                "started_at": started,
                "finished_at": _now(),
                "exit_code": result.returncode,
                "timed_out": timed_out,
            }
            _append_log(
                root / "logs" / f"{job_id}.log",
                f"{started} exit={result.returncode}",
                result.output,
            )
            write_json_atomic(root / "state" / f"{job_id}.json", state)
            return result.returncode
        finally:
            lock.release()
    finally:
        lock.close()
