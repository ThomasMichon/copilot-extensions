"""``scheduled-job`` resource: a recurring entrypoint run owned by agent-machines.

Present declarations register (or refresh) one OS timer per job id; absent
declarations remove it. Jobs agent-machines registered but no longer
declared are swept by :meth:`ScheduledJobResourceHandler.sweep_orphans`.
"""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING, Any

from . import scheduled_job_tasks as tasks
from . import scheduled_jobs as jobs
from .resources import ResourceFinding, ResourceHandler, ResourceResult, _select_field

if TYPE_CHECKING:
    from pathlib import Path

    from .manifest import RequirementPackage
    from .resources import ResolvedResource, ResourceContext, ResourceContribution
    from .scheduled_jobs import Runner
    from .self_update_types import CommandResult


def task_runner(ctx: ResourceContext) -> Runner:
    """Adapt ``ctx.runner`` to the backend's ``runner(argv, timeout=)`` shape."""
    from .self_update_types import CommandResult

    def runner(
        argv: list[str], *, cwd: Path | None = None, timeout: int | None = None
    ) -> CommandResult:
        _ = cwd, timeout
        outcome = ctx.runner(argv)
        return CommandResult(list(argv), outcome.returncode, outcome.stdout, outcome.stderr)

    return runner


def binding_findings(resolved: list[ResolvedResource]) -> list[ResourceFinding]:
    """Bind each present ``scheduled-job`` to the ``package`` it names."""
    apps: dict[str, list[str]] = {}
    for res in resolved:
        if (
            res.type == "package"
            and res.desired.get("manager") in jobs.APP_MANAGERS
            and res.desired.get("state", "present") != "absent"
        ):
            apps.setdefault(res.id, []).append(str(res.desired["manager"]))
    findings = []
    for res in resolved:
        if res.type == "scheduled-job" and res.desired.get("state", "present") != "absent":
            problem = jobs.bind_application(res.id, res.desired, apps)
            if problem:
                findings.append(ResourceFinding("error", "scheduled-job-unbound", problem))
    return findings


def _failure(result: CommandResult) -> str | None:
    return None if result.returncode == 0 else (result.output or f"exit code {result.returncode}")


class ScheduledJobResourceHandler(ResourceHandler):
    TYPE = "scheduled-job"
    FIELDS = ("state", "command", "interval_seconds", "timeout_seconds")

    def identity(self, decl: dict[str, Any]) -> tuple[str, str]:
        return (self.TYPE, str(decl["id"]))

    def display_id(self, decl: dict[str, Any]) -> str:
        return str(decl["id"])

    def applies_on(self, decl: dict[str, Any], plat: str) -> bool:
        return super().applies_on(decl, plat) and plat in {"windows", "linux", "wsl"}

    def merge(
        self,
        members: list[ResourceContribution],
    ) -> tuple[dict[str, Any], list[ResourceFinding], list[dict[str, Any]]]:
        identity = self.identity(members[0].declaration)
        defaults = {"state": "present", "timeout_seconds": jobs.DEFAULT_TIMEOUT_SECONDS}
        desired: dict[str, Any] = {}
        findings: list[ResourceFinding] = []
        decisions: list[dict[str, Any]] = []
        for name in self.FIELDS:
            state = desired.get("state")

            def value_of(member: ResourceContribution, name: str = name) -> Any:
                return member.declaration.get(name, defaults.get(name))

            def include(
                member: ResourceContribution, name: str = name, state: Any = state
            ) -> bool:
                return name == "state" or member.declaration.get("state", "present") == state

            value, selected, _, conflict, decision, info = _select_field(
                members, identity, name, value_of, include=include
            )
            desired[name] = value
            if conflict:
                findings.append(
                    ResourceFinding(
                        "error",
                        "resource-conflict",
                        f"scheduled-job {identity[1]!r} declares conflicting {name} "
                        "across packages: "
                        + ", ".join(sorted(member.owner for member in selected)),
                    )
                )
            if info:
                findings.append(info)
            if decision:
                decisions.append(decision)
        desired["sources"] = sorted(
            {str(member.package.source_path) for member in members if member.package.source_path}
        )
        desired["maintenance_safe"] = any(
            bool(m.declaration.get("maintenance_safe")) for m in members
        )
        return desired, findings, decisions

    def apply(
        self, resolved: ResolvedResource, ctx: ResourceContext, dry_run: bool
    ) -> ResourceResult:
        desired = resolved.desired
        present = str(desired.get("state", "present")) != "absent"
        if present and not desired.get("application"):
            return self._result(
                resolved.id,
                dry_run,
                "error",
                detail="command.application is not bound to a declared uv-tool or pipx package",
            )
        timeout = int(desired.get("timeout_seconds") or jobs.DEFAULT_TIMEOUT_SECONDS)
        interval, source = (
            jobs.effective_interval(
                resolved.id,
                int(desired.get("interval_seconds") or 0),
                timeout,
                ctx.home,
            )
            if present
            else (0, "declared")
        )
        return self._converge(
            resolved.id,
            present,
            interval,
            timeout,
            ctx,
            dry_run,
            sources=list(desired.get("sources") or []),
            note=f"every {interval}s ({source}), timeout {timeout}s" if present else "",
        )

    def sweep_orphans(
        self,
        resolved: list[ResolvedResource],
        packages: list[RequirementPackage],
        ctx: ResourceContext,
        dry_run: bool,
    ) -> list[ResourceResult]:
        """Remove jobs this machine registered whose declaration was withdrawn.

        Withdrawal must be confirmed (see :func:`jobs.withdrawal_confirmed`)
        against the repos and package files in this run; otherwise the job is
        left alone and ``scheduled-job run`` re-checks the full declaration set.
        """
        declared = {res.id for res in resolved if res.type == self.TYPE}
        examined = {str(pkg.source_path) for pkg in packages if pkg.source_path}
        roots = [*ctx.repo_paths.values()]
        roots += [root for pkg in packages if (root := pkg.repo_root()) is not None]
        results = []
        for job_id, entry in sorted(jobs.read_owned(ctx.home).items()):
            if job_id in declared:
                continue
            sources = [str(path) for path in entry.get("sources") or []]
            if not jobs.withdrawal_confirmed(sources, roots, examined):
                continue
            results.append(
                self._converge(
                    job_id, False, 0, 0, ctx, dry_run, sources=[], note="declaration withdrawn"
                )
            )
        return results

    # ------------------------------------------------------------------ #
    def _result(
        self, job_id: str, dry_run: bool, action: str, *, changed: bool = False, **kw: Any
    ) -> ResourceResult:
        return ResourceResult(self.TYPE, job_id, changed, dry_run, action, **kw)

    def _converge(
        self,
        job_id: str,
        present: bool,
        interval: int,
        timeout: int,
        ctx: ResourceContext,
        dry_run: bool,
        *,
        sources: list[str],
        note: str,
    ) -> ResourceResult:
        runner = task_runner(ctx)
        label = tasks.backend_label(ctx.platform)
        detail = f"{label} {tasks.task_name(job_id)}" + (f": {note}" if note else "")
        unsupported = tasks.unsupported_reason(
            ctx.platform, runner=runner, resolve_binary=shutil.which
        )
        if unsupported:
            return self._result(
                job_id, dry_run, "skip", detail=unsupported, skipped_reason=unsupported
            )
        try:
            state = tasks.query(
                ctx.platform,
                job_id,
                interval,
                timeout,
                runner=runner,
                resolve_binary=shutil.which,
                home=ctx.home,
            )
            if (present and state.matching) or (not present and not state.present):
                if not dry_run:
                    (
                        jobs.record_owned(ctx.home, job_id, sources)
                        if present
                        else jobs.forget_owned(ctx.home, job_id)
                    )
                return self._result(
                    job_id,
                    dry_run,
                    "none",
                    detail=f"{detail} already {'registered' if present else 'absent'}",
                )
            action = "install" if present else "uninstall"
            if dry_run:
                return self._result(
                    job_id, True, action, changed=True, detail=f"would {action} {detail}"
                )
            if present:
                result = tasks.register(
                    ctx.platform,
                    job_id,
                    interval,
                    timeout,
                    runner=runner,
                    resolve_binary=shutil.which,
                    home=ctx.home,
                )
            else:
                result = tasks.unregister(
                    ctx.platform, job_id, runner=runner, resolve_binary=shutil.which, home=ctx.home
                )
        except (OSError, RuntimeError, ValueError) as error:
            return self._result(job_id, dry_run, "error", detail=f"{detail}: {error}")
        failure = _failure(result)
        if failure:
            from .self_update_tasks import _looks_like_access_denied

            if _looks_like_access_denied(failure):
                return self._result(
                    job_id, False, "defer", detail=f"{detail}: {failure}", deferred_reason=failure
                )
            return self._result(job_id, False, "error", detail=f"{detail}: {failure}")
        if present:
            jobs.record_owned(ctx.home, job_id, sources)
        else:
            jobs.forget_owned(ctx.home, job_id)
        return self._result(job_id, False, action, changed=True, detail=detail)
