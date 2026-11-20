"""``agent-machines scheduled-job run|status`` CLI verbs.

``run <id>`` is what each job's OS timer invokes. It re-resolves the
current declarations first: a job whose withdrawal is confirmed removes its
own timer and exits 0 without running anything; an unconfirmed absence
(e.g. an unreachable declaring repo) keeps the timer and runs nothing.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple

from . import discover as _discover
from . import reconcile as _reconcile
from . import resources as _resources
from . import scheduled_jobs as _jobs
from . import validator as _validator
from .cli_fleet_update import (
    _collect_all_packages,
    _emit_identity_warnings,
    _resolve_machine_identity,
)

if TYPE_CHECKING:
    from .self_update_types import CommandResult

TYPE = "scheduled-job"


class Resolution(NamedTuple):
    declared: dict[str, dict[str, Any]]  # every resolved ``scheduled-job`` desired state by id
    examined: set[str]  # package files read in this resolution
    roots: list[Path]  # repo roots present in this resolution


def _declared(args: argparse.Namespace) -> Resolution | None:
    """Resolve the current declarations, or None on a discovery/validator error."""
    identity = _resolve_machine_identity(args)
    _emit_identity_warnings(identity)
    machine = identity.canonical
    collected = _collect_all_packages(machine, identity.accepted)
    packages = _reconcile.resolve_union(collected, machine, identity.accepted)
    findings = _validator.validate(packages, machine)
    if _validator.has_errors(findings):
        print("scheduled-job refused: validator reported errors:", file=sys.stderr)
        for finding in findings:
            if finding.level == "error":
                print(f"  {finding.code}: {finding.message}", file=sys.stderr)
        return None
    resolved, _ = _resources.resolve_resources(packages, machine, _discover.current_platform())
    roots = [c.path for c in _discover.candidate_repos()] + [_discover.user_package_root()]
    roots += [root for pkg in collected if (root := pkg.repo_root()) is not None]
    return Resolution(
        {res.id: res.desired for res in resolved if res.type == TYPE},
        {str(pkg.source_path) for pkg in collected if pkg.source_path},
        roots,
    )


def _runner(
    argv: list[str], *, cwd: Path | None = None, timeout: int | None = None
) -> CommandResult:
    from .self_update_types import default_command_runner

    return default_command_runner(argv, cwd=cwd, timeout=timeout or 300)


def cmd_run(args: argparse.Namespace) -> int:
    resolution = _declared(args)
    if resolution is None:
        return 1
    desired = resolution.declared.get(args.id)
    if desired is not None and desired.get("state", "present") != "absent":
        return _jobs.run_job(args.id, desired)
    sources = _jobs.read_owned().get(args.id, {}).get("sources") or []
    if desired is None and not _jobs.withdrawal_confirmed(
        sources, resolution.roots, resolution.examined
    ):
        print(
            f"scheduled-job {args.id!r}: declaration unavailable; keeping timer", file=sys.stderr
        )
        return 0
    from . import scheduled_job_tasks as _tasks

    platform = _discover.current_platform()
    if _tasks.unsupported_reason(platform, runner=_runner, resolve_binary=shutil.which) is None:
        # This process is the task's own run: never stop it, just unregister.
        result = _tasks.unregister(
            platform, args.id, runner=_runner, resolve_binary=shutil.which, stop=False
        )
        if result.returncode != 0:
            print(
                f"scheduled-job {args.id!r}: failed to remove withdrawn timer: {result.output}",
                file=sys.stderr,
            )
            return 2
    _jobs.forget_owned(None, args.id)
    print(f"scheduled-job {args.id!r} is no longer declared; removed its timer", file=sys.stderr)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    from . import scheduled_job_tasks as _tasks

    resolution = _declared(args)
    if resolution is None:
        return 1
    declared = resolution.declared
    platform = _discover.current_platform()
    unsupported = _tasks.unsupported_reason(platform, runner=_runner, resolve_binary=shutil.which)
    owned = _jobs.read_owned()
    rows: list[dict[str, Any]] = []
    for job_id in sorted(set(declared) | set(owned)):
        desired = declared.get(job_id) or {}
        present = bool(desired) and desired.get("state", "present") != "absent"
        timeout = int(desired.get("timeout_seconds") or _jobs.DEFAULT_TIMEOUT_SECONDS)
        interval, source = (
            _jobs.effective_interval(job_id, int(desired["interval_seconds"]), timeout)
            if present
            else (None, None)
        )
        row: dict[str, Any] = {
            "id": job_id,
            "declared": present,
            "owned": job_id in owned,
            "interval_seconds": interval,
            "interval_source": source,
            "timeout_seconds": timeout if present else None,
            "registered": None,
            "matching": None,
            "detail": unsupported,
            "last_run": _jobs.read_state(job_id) or None,
        }
        if unsupported is None:
            try:
                state = _tasks.query(
                    platform,
                    job_id,
                    interval or 0,
                    timeout,
                    runner=_runner,
                    resolve_binary=shutil.which,
                    home=Path.home(),
                )
                row.update(registered=state.present, matching=state.matching if present else None)
            except (OSError, RuntimeError) as error:
                row["detail"] = str(error)
        rows.append(row)
    if args.json:
        print(json.dumps({"ok": True, "jobs": rows}, indent=2))
        return 0
    if not rows:
        print("no scheduled jobs declared or registered")
    for row in rows:
        yes = {True: "yes", False: "no", None: "unknown"}
        print(
            f"{row['id']}: declared={yes[row['declared']]}; registered={yes[row['registered']]}; "
            f"matching={yes[row['matching']]}"
        )
        if row["interval_seconds"]:
            print(
                f"  interval: {row['interval_seconds']}s ({row['interval_source']}); "
                f"timeout: {row['timeout_seconds']}s"
            )
        if row["last_run"]:
            run = row["last_run"]
            print(
                f"  last run: {run.get('started_at')} -> {run.get('finished_at')} "
                f"exit={run.get('exit_code')} timed_out={run.get('timed_out')}"
            )
        if row["detail"]:
            print(f"  {row['detail']}")
    return 0


def register_parser(sub: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    """Wire the ``scheduled-job`` subcommand tree onto agent-machines' parser."""
    scheduled = sub.add_parser(
        "scheduled-job", help="Run or inspect repository-declared scheduled jobs."
    )
    scheduled_sub = scheduled.add_subparsers(dest="scheduled_job_command")
    run = scheduled_sub.add_parser(
        "run", help="Run one declared job now (what its OS timer invokes)."
    )
    run.add_argument("id", help="the scheduled-job id")
    run.add_argument("--machine", help="override the target machine name")
    run.set_defaults(func=cmd_run)
    status = scheduled_sub.add_parser(
        "status", help="Show declared/registered jobs and their last run."
    )
    status.add_argument("--machine", help="override the target machine name")
    status.add_argument("--json", action="store_true", help="emit JSON")
    status.set_defaults(func=cmd_status)
