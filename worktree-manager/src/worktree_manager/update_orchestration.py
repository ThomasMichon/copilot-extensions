"""Manager self-update and call-scoped engine-suite orchestration."""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TYPE_CHECKING

from .update_events import UpdateRun

if TYPE_CHECKING:
    from .runtime_source import ResolvedSource


def preflight_runtime_source(run: UpdateRun | None = None, *, suite: bool = False) -> bool:
    from .runtime_source_config import configured_source, require_marketplace

    try:
        configured_source() if suite else require_marketplace()
    except (OSError, ValueError) as error:
        if run is None:
            print(f"Runtime source: {error}", file=sys.stderr)
        else:
            run.say(str(error), state="failed")
            run.record_engine_error(str(error), missing=False)
        return False
    return True


def extract_project(rest: list[str]) -> tuple[str | None, list[str]]:
    out: list[str] = []
    project: str | None = None
    i = 0
    while i < len(rest):
        if rest[i] == "--project" and i + 1 < len(rest):
            project, i = rest[i + 1], i + 2
            continue
        if rest[i].startswith("--project="):
            project = rest[i].partition("=")[2]
            i += 1
            continue
        out.append(rest[i])
        i += 1
    return project, out


def execute_update(
    rest: list[str], steps: Callable[..., int], *, trigger: str = "manual",
) -> int:
    """Admit a Manager update and preserve its targeted or suite transaction."""
    from .maintenance_policy import admit_update

    if not admit_update(rest):
        return 3
    if any(arg == "--target" or arg.startswith("--target=") for arg in rest):
        from .installation_updates import run_targeted_update

        return run_targeted_update(rest, trigger=trigger)
    from .runtime_source import SourceAcquisitionError
    from .runtime_source_update import repair_selection
    from .update_bootstrap import prepare_update
    from .update_events import UpdateRun

    if not preflight_runtime_source(suite=True):
        return 1
    run = None
    try:
        with repair_selection(rest) as runtime_source:
            source_arguments = (
                {"runtime_source": runtime_source} if runtime_source is not None else {}
            )
            prepared = prepare_update(rest, **source_arguments)
            if prepared.exit_code:
                return prepared.exit_code
            run = UpdateRun(trigger=trigger)
            rc = steps(
                prepared.arguments, run, fresh_manager=prepared.fresh_manager,
                environment=prepared.environment, **source_arguments,
            )
        return run.finish(rc)
    except (OSError, ValueError, SourceAcquisitionError) as error:
        if run is None:
            print(f"Runtime source: {error}", file=sys.stderr)
            return 1
        run.say(f"Runtime source: {error}", state="failed")
        run.record_engine_error(str(error), missing=False)
        return run.finish(1)
    except BaseException as error:
        if run is not None:
            run.abort(error)
        raise


def run_update_steps(
    rest: list[str], run: UpdateRun, *, version: str, fresh_manager: bool = False,
    environment: dict[str, str] | None = None,
    runtime_source: ResolvedSource | None = None,
) -> int:
    from . import engine_client as ec
    from .self_install import SelfUpdateResult, self_update
    from .runtime_source_update import prepare_suite
    from .runtime_source import SourceAcquisitionError

    environment = run.child_environment(environment)
    if not preflight_runtime_source(run, suite=not fresh_manager or runtime_source is not None):
        return 1
    project, forwarded = extract_project(rest)
    try:
        prepared = prepare_suite(
            project, forwarded, environment=environment,
            **({"runtime_source": runtime_source} if runtime_source is not None else {}),
        )
    except (ec.EngineError, OSError, ValueError, SourceAcquisitionError) as error:
        run.say(f"Runtime source: {error}", state="failed")
        run.record_engine_error(str(error), missing=bool(getattr(error, "install_hint", False)))
        return 1

    with run.self_update_phase():
        su = (
            SelfUpdateResult(action="already-current", version=version)
            if fresh_manager else self_update(dry_run=False)
        )
        cutover = su.cutover if isinstance(su.cutover, dict) else None
        cutover_failed = bool(
            cutover and (
                cutover.get("action") == "error"
                or (
                    isinstance(cutover.get("result"), dict)
                    and cutover["result"].get("ok") is False
                )
            )
        )
        if su.action == "updated":
            run.say(f"updated {su.previous or '(none)'} → {su.version} "
                    "(active on next run)")
        elif su.action == "already-current":
            run.say(f"already current ({su.version})")
        else:
            run.say(f"self-update {su.action}: {su.reason} — continuing")
        if cutover_failed:
            run.say(f"mux-daemon cutover failed: {cutover}", state="failed")
        run.record_self_update(
            su.action, cutover_failed, str(cutover) if cutover_failed else su.reason,
        )
    from . import manager_update_check

    manager_update_check.invalidate()
    run.say("Updating harness plugins + runtimes via agent-worktrees")
    try:
        rc = ec.run_engine_passthrough(
            project, ["update", "--no-manager", *prepared.arguments],
            **({"environment": environment} if environment is not None else {}),
            **({"engine_command": prepared.engine_command} if prepared.engine_command is not None else {}),
        )
    except ec.EngineError as error:
        run.say(str(error), state="failed")
        run.record_engine_error(
            str(error), missing=bool(getattr(error, "install_hint", False)),
            cause=error.__cause__,
        )
        if getattr(error, "install_hint", False):
            run.say("Run `worktree-manager setup --apply` to install the engine first.")
        return 1
    if su.action == "auth_required" and rc in (0, 3, 5):
        return 4
    return (1 if cutover_failed else 0) if rc == 0 else rc
