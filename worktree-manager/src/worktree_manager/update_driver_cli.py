"""Durable execution boundary for the Manager's public update command."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from contextlib import contextmanager
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

if TYPE_CHECKING:
    from update_driver import Request

from .engine_acquisition_budget import TIMEOUT_ENV, acquisition_timeout

_CONTEXT_KEYS = (
    "AGENT_HOME",
    "AGENT_WORKTREES_UPDATE_CONTEXT",
    "AGENT_WORKTREES_PAYLOAD_ROOT",
    "COPILOT_EXTENSIONS_CONTEXT",
    "COPILOT_PLUGIN_ROOT",
    "WORKTREE_MANAGER_ROOT",
    "WORKTREE_MANAGER_ENGINE_ARGV",
    "WORKTREE_MANAGER_ENGINE_CMD",
    "CE_UPDATE_AUTH",
)
_UNATTENDED = "CE_UPDATE_UNATTENDED"
_TELEMETRY_KEYS = (
    "COPILOT_EXTENSIONS_UPDATE_RUN_ID", "COPILOT_EXTENSIONS_UPDATE_EVENTS",
    "COPILOT_EXTENSIONS_UPDATE_OWNER",
)
_RUNNER_CONTEXT_KEYS = (*_CONTEXT_KEYS, _UNATTENDED, *_TELEMETRY_KEYS)


def _current_authority(driver, **kwargs):
    inspect = getattr(driver, "current_authority", None)
    if not callable(inspect):
        raise driver.StateError("selected update driver lacks authority support")
    return inspect(**kwargs)


def handoff_environment(args: list[str]) -> dict[str, str]:
    if args and (
        (args[0] == "update" and "--auth-report" not in args)
        or (args[0] == "stage-update" and "--indicator-state" not in args)
        or (args[0] == "reconcile-plugins" and "--apply" in args and "--check-update" not in args)
        or (args[0] == "self-install" and "--apply" in args)
    ):
        from update_driver import participant_environment

        return participant_environment()
    return {}


def _update_context_directory(environment: Mapping[str, str]) -> str | None:
    if "AGENT_WORKTREES_UPDATE_CONTEXT" in environment:
        directory = Path(environment["AGENT_WORKTREES_UPDATE_CONTEXT"])
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError("update invocation context must be an existing absolute directory")
        return str(directory)
    return None


def handoff_launch_options(
    environment: dict[str, str], handoff: dict[str, str], *, update: bool = False,
) -> dict[str, object]:
    """Keep scoped Windows updates headless without changing interactive passthrough."""
    options: dict[str, object] = {"env": {**environment, **handoff}}
    if update:
        directory = _update_context_directory(environment)
        if directory is not None:
            options["cwd"] = directory
    if handoff and os.name == "nt":
        from agent_procutil import no_window_kwargs

        options.update(no_window_kwargs())
        # CREATE_NO_WINDOW otherwise loses redirected inherited handles.
        options.update(stdout=sys.stdout, stderr=sys.stderr)
    return options


def parse_options(rest: list[str]) -> tuple[argparse.Namespace, list[str]]:
    parser = argparse.ArgumentParser(prog="worktree-manager update", allow_abbrev=False)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--detach", action="store_true")
    mode.add_argument("--wait", action="store_true")
    mode.add_argument("--supervised-debug", action="store_true")
    mode.add_argument("--request-status", metavar="ID")
    mode.add_argument("--recovery-status", action="store_true")
    mode.add_argument("--recover-after-restart", action="store_true")
    mode.add_argument("--recover-for-repair", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--unattended", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--engine-acquisition-timeout", help=argparse.SUPPRESS)
    options, forwarded = parser.parse_known_args(rest)
    if "--auth-report" in forwarded and (options.detach or options.supervised_debug or options.unattended):
        parser.error("--auth-report cannot select an installation execution mode")
    recovery = options.recovery_status or options.recover_after_restart or options.recover_for_repair
    if (options.request_status or recovery) and (
        forwarded or options.engine_acquisition_timeout is not None or options.unattended
    ):
        parser.error("request inspection/recovery does not accept installation options")
    if "--auth-report" in forwarded and options.engine_acquisition_timeout is not None:
        parser.error("authentication inspection does not accept an acquisition budget")
    if options.json and not (options.detach or options.request_status or recovery or "--auth-report" in forwarded):
        parser.error("--json requires request inspection, recovery, --detach or --auth-report")
    canonical = argparse.ArgumentParser(prog="worktree-manager update", add_help=False, allow_abbrev=False)
    for name in (
        "auto", "force", "no-anchor-sync", "no-prune-pivots", "recreate-venv",
        "no-manager", "no-project-controller",
    ):
        canonical.add_argument("--" + name, action="store_true")
    canonical.add_argument("--project")
    canonical.add_argument("--target")
    canonical.add_argument("--repair-engine", help=argparse.SUPPRESS)
    canonical.add_argument("--force-runtime", action="append")
    canonical.add_argument("--skip-modules", nargs="*")
    canonical.add_argument("--auth-report", action="store_true")
    canonical.add_argument("--auth-source", action="append")
    canonical.add_argument("--auth-cwd")
    canonical.add_argument("--auth-non-repository", action="store_true")
    canonical.add_argument("--auth-environment", action="store_true")
    canonical.add_argument(
        "--automatic-trigger", choices=("manager", "external", "legacy-agent-machines"),
    )
    canonical.add_argument("--controller")
    parsed = canonical.parse_args(forwarded)
    options.auto = parsed.auto
    options.automatic_trigger = parsed.automatic_trigger
    options.controller = parsed.controller
    options.target = parsed.target
    options.repair = bool(
        parsed.force or parsed.force_runtime or parsed.recreate_venv or parsed.repair_engine
    )
    if parsed.target is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", parsed.target):
        parser.error("--target requires a nonempty component identifier")
    if parsed.target is not None and options.engine_acquisition_timeout is not None:
        parser.error("--target does not bootstrap an engine; --engine-acquisition-timeout is not applicable")
    if parsed.target is not None and any((
        parsed.force, parsed.force_runtime, parsed.skip_modules is not None,
        parsed.recreate_venv, parsed.repair_engine, parsed.auth_report,
    )):
        parser.error("--target cannot be combined with force, repair, auth or module selection")
    if bool(parsed.controller) != (parsed.automatic_trigger == "external"):
        parser.error("--controller is required only with --automatic-trigger external")
    if parsed.automatic_trigger and parsed.auth_report:
        parser.error("automatic installation triggers cannot select authentication inspection")
    if parsed.repair_engine is not None and forwarded[:1] != ["--repair-engine"]:
        parser.error("bootstrap repair must precede ordinary update arguments")
    if not parsed.auth_report and (
        parsed.auth_source or parsed.auth_cwd or parsed.auth_non_repository or parsed.auth_environment
    ):
        parser.error("authentication inspection options require --auth-report")
    return options, forwarded


def source_identity() -> dict[str, object]:
    from .runtime_source_config import configured_source
    from .self_install import default_root, manager_ref, manager_repo
    from .source_config import absolute_local_source

    root = default_root().expanduser().resolve()
    repo = absolute_local_source(manager_repo(root))
    url = urlsplit(repo) if "://" in repo else None
    if url is not None and (
        url.password is not None
        or (url.scheme in {"http", "https"} and (url.username is not None or url.query or url.fragment))
    ):
        raise ValueError("credential-bearing source URLs cannot be journaled; use the source credential helper")
    return {
        "root": str(root),
        "repo": repo,
        "ref": manager_ref(root),
        "runtime_source": configured_source(root).document(),
    }


def request_context(*, capture_engine: bool = True) -> dict[str, str]:
    excluded = set() if capture_engine else {
        "WORKTREE_MANAGER_ENGINE_ARGV", "WORKTREE_MANAGER_ENGINE_CMD",
    }
    context = {
        key: os.environ[key] for key in _CONTEXT_KEYS
        if key in os.environ and key not in excluded
    }
    if context.get("AGENT_HOME", "").strip():
        context["AGENT_HOME"] = os.path.abspath(context["AGENT_HOME"].strip())
    if capture_engine and f"{__package__}.engine_client" in sys.modules:
        from .engine_client import ENGINE_ARGV_ENV, inherited_engine_command

        accepted = inherited_engine_command()
        if accepted is not None:
            context[ENGINE_ARGV_ENV] = json.dumps(accepted)
    from .source_config import default_root

    context["WORKTREE_MANAGER_ROOT"] = str(default_root().expanduser().resolve())
    return context


def request_source_anchor() -> str:
    from .source_config import SOURCE_ANCHOR_ENV

    anchor = os.environ.get(SOURCE_ANCHOR_ENV) or str(Path.cwd().resolve())
    if not Path(anchor).is_absolute():
        raise ValueError("update request source anchor must be absolute")
    return str(Path(anchor).resolve())


def _automatic_arguments(arguments: list[str] | tuple[str, ...]) -> bool:
    return "--auto" in arguments or any(
        arg == "--automatic-trigger" or arg.startswith("--automatic-trigger=") for arg in arguments
    )


def _unattended_request(request: Request) -> bool:
    return (
        request.spec.backend == "manager"
        and ("--unattended" in request.spec.argv or _automatic_arguments(request.spec.argv))
    )


def _retained_automatic_trigger(arguments: list[str], spec) -> list[str]:
    if spec.backend != "manager":
        return arguments
    retained, _ = parse_options(list(spec.argv))
    if not retained.automatic_trigger:
        return arguments
    current, _ = parse_options(arguments)
    if current.automatic_trigger:
        if (current.automatic_trigger, current.controller) != (
            retained.automatic_trigger, retained.controller,
        ):
            raise ValueError("automatic update trigger differs from its retained request")
        return arguments
    return [
        *arguments, "--automatic-trigger", retained.automatic_trigger,
        *(["--controller", retained.controller] if retained.controller else []),
    ]


@contextmanager
def _request_environment(
    context, *, killswitch_path: str | None = None, cwd: str | None = None,
    unattended: bool = False,
):
    from .source_config import SOURCE_ANCHOR_ENV

    values = dict(context)
    if values.keys() - set(_CONTEXT_KEYS):
        raise ValueError("update request contains unsupported execution context")
    values.update((key, os.environ[key]) for key in _TELEMETRY_KEYS if key in os.environ)
    if cwd:
        if not Path(cwd).is_absolute():
            raise ValueError("update request source anchor must be absolute")
        values[SOURCE_ANCHOR_ENV] = cwd
    if cwd is not None and values.get("WORKTREE_MANAGER_ROOT"):
        try:
            root = Path(values["WORKTREE_MANAGER_ROOT"]).expanduser()
        except RuntimeError as error:
            raise ValueError(f"Manager root cannot be expanded: {error}") from error
        if not root.is_absolute():
            if not cwd or not Path(cwd).is_absolute():
                raise ValueError("relative Manager root requires an absolute request cwd")
            root = Path(cwd) / root
            if not root.is_absolute():
                raise ValueError("Manager root cannot resolve against request cwd")
        values["WORKTREE_MANAGER_ROOT"] = str(root)
    keys = (*_RUNNER_CONTEXT_KEYS, SOURCE_ANCHOR_ENV)
    if killswitch_path is not None:
        keys = (*keys, "BOOTSTRAP_KILLSWITCH_STATE_FILE")
        if killswitch_path:
            values["BOOTSTRAP_KILLSWITCH_STATE_FILE"] = killswitch_path
    previous = dict(os.environ)
    try:
        for key in keys:
            os.environ.pop(key, None)
        os.environ.update(values)
        if unattended:
            from .automatic_updates_process import update_environment
            from .self_install import default_root

            safe = update_environment(default_root().absolute())
            # Restore only the request's validated provider/installation selection,
            # never the executor's ambient credentials or transport overrides.
            safe.update(values)
            os.environ.clear()
            os.environ.update(safe)
        yield
    finally:
        os.environ.clear()
        os.environ.update(previous)


@contextmanager
def _acquisition_environment(value: str | None):
    previous = os.environ.get(TIMEOUT_ENV)
    selected = None if value is None else str(acquisition_timeout({TIMEOUT_ENV: value}))
    try:
        if selected is None:
            os.environ.pop(TIMEOUT_ENV, None)
        else:
            os.environ[TIMEOUT_ENV] = selected
        yield
    finally:
        if previous is None:
            os.environ.pop(TIMEOUT_ENV, None)
        else:
            os.environ[TIMEOUT_ENV] = previous


def _source_key() -> str:
    return json.dumps(source_identity(), sort_keys=True, separators=(",", ":"))


def _summary(request) -> dict:
    return {
        "request_id": request.id,
        "state": request.state,
        "exit_code": request.exit_code,
        "error": request.error,
        "log_path": str(request.log_path),
        "settled_at": getattr(request, "settled_at", None),
        "settlement_kind": getattr(request, "settlement_kind", None),
        "recovery_authority": getattr(request, "recovery_authority", None),
        "released_at": getattr(request, "released_at", None),
    }


def _report(request, *, as_json: bool) -> None:
    summary = _summary(request)
    if as_json:
        print(json.dumps(summary))
    else:
        print(f"Update request {request.id}: {request.state}; log: {request.log_path}")
        if request.error:
            print(request.error, file=sys.stderr)
        if summary["recovery_authority"]:
            print(f"Repair release: {summary['recovery_authority']}; cleanup remains unconfirmed.")


def self_install_request(operation: Callable[[], int]) -> int:
    """Explicit installation uses the same native finite driver as bootstrap."""
    import update_driver as driver

    from .self_install import _runtime_installer, default_root, running_payload_dir
    from .runtime_identity import running_application_slot

    try:
        with _runtime_installer()._installation_custody() as custody:
            if custody is not None:
                return operation()
            argv, environment = driver.runtime_command("worktree_manager", ["self-install", "--apply"])
            workspace = running_application_slot() or running_payload_dir()
            root = str(default_root().expanduser().resolve())
            context = {**request_context(), "WORKTREE_MANAGER_ROOT": root}
            selected = _current_authority(
                driver, context=context.get("COPILOT_EXTENSIONS_CONTEXT", ""), cwd=str(workspace),
            )
            request = driver.enqueue(
                driver.RequestSpec(
                    "manager-self-install", ("--apply",), project=root,
                    cwd=str(workspace), source=_source_key(), context=tuple(context.items()),
                    authority="standalone" if selected.configured and selected.mode == "standalone" else "",
                ),
                runner=driver.TrustedRunner(
                    argv, str(workspace), env=tuple(environment.items()), context_keys=_CONTEXT_KEYS,
                ),
            )
            driver.drain()
            result = driver.wait(request.id, output=sys.stdout)
            if result.state != "completed" or result.exit_code is None:
                _report(result, as_json=False)
                return 1
            return result.exit_code
    except (driver.DriverError, driver.ParticipantError, OSError, ValueError) as error:
        print(f"Update driver: {error}", file=sys.stderr)
        return 1


def cmd_update_request(rest: list[str]) -> int:
    import update_driver as driver

    from .engine_client import EngineError
    from .update_orchestration import extract_project

    options, forwarded = parse_options(rest)
    request = None
    try:
        if options.recovery_status:
            state = driver.recovery_status()
            data = {
                "eligible_requests": list(state.eligible_ids),
                "refused": [
                    {"request_id": refusal.request_id, "reason": refusal.reason}
                    for refusal in state.refused
                ],
                "error": state.error,
            }
            print(json.dumps(data) if options.json else f"Update recovery: {data}")
            return 1 if state.error else 0
        if options.recover_after_restart:
            settled = driver.recover_after_restart()
            data = {"settled": [_summary(request) for request in settled], "replayed": False}
            print(json.dumps(data) if options.json else
                  f"Settled {len(settled)} interrupted request(s); no update was run.")
            return 0
        if options.recover_for_repair:
            released = driver.recover_for_repair(authorized=True)
            data = {"released": [_summary(request) for request in released], "replayed": False}
            print(json.dumps(data) if options.json else
                  f"Released {len(released)} queue fence(s) by operator authorization; "
                  "cleanup remains unconfirmed; no update was run.")
            return 0
        if options.request_status:
            request = driver.status(options.request_status)
            _report(request, as_json=options.json)
            return 0
        if "--auth-report" in forwarded:
            from . import engine_client

            project, args = extract_project(forwarded)
            return engine_client.run_engine_passthrough(
                project, ["update", "--no-manager", *args, *(["--json"] if options.json else [])],
            )
        custody = driver.borrowed_custody()
        if custody is not None:
            with custody:
                request = driver.status(custody.request_id, root=custody.root)
                if request.state != "running" or request.spec.backend not in {"manager", "engine"}:
                    raise driver.StateError("Manager does not own the active update request")
                with _request_environment(
                    request.spec.context, killswitch_path=request.spec.killswitch_path,
                    cwd=request.spec.cwd, unattended=_unattended_request(request),
                ), _acquisition_environment(
                    options.engine_acquisition_timeout,
                ):
                    _update_context_directory(os.environ)
                    from .project_update_controller import handoff

                    selected = _current_authority(
                        driver,
                        root=custody.root,
                        context=dict(request.spec.context).get("COPILOT_EXTENSIONS_CONTEXT", ""),
                        cwd=request.spec.cwd,
                    )
                    requested = getattr(request.spec, "authority", "standalone")
                    if selected.configured and (
                        selected.mode != requested
                        or (requested == "project" and selected.project != request.spec.project)
                    ):
                        raise driver.StateError("update authority changed before execution")
                    if requested == "project" and not selected.configured:
                        raise driver.StateError("selected project update authority is unavailable")
                    if selected.configured and selected.mode == "standalone":
                        if "--no-project-controller" not in forwarded:
                            forwarded = [*forwarded, "--no-project-controller"]
                    forwarded = _retained_automatic_trigger(forwarded, request.spec)
                    delegated = requested == "project" and not any(
                        flag in {"--phase", "--target", "--repair-engine", "--no-project-controller"}
                        or flag.startswith(("--phase=", "--target=", "--repair-engine="))
                        for flag in getattr(request.spec, "argv", ())
                    )
                    controlled = handoff(forwarded, authority=selected, delegated=delegated)
                    if controlled is not None:
                        return controlled
                    if request.spec.backend == "manager" and request.spec.source != _source_key():
                        raise driver.StateError("Manager source changed after submission; submit a new request")
                    custody.assert_owned()
                    from . import __main__ as manager

                    return manager._cmd_update(forwarded)
        from .project_update_controller import handoff

        observer = [
            "--" + name.replace("_", "-")
            for name in ("detach", "wait", "supervised_debug", "json")
            if getattr(options, name)
        ]
        context = request_context()
        source_anchor = request_source_anchor()
        selected = _current_authority(
            driver,
            context=context.get("COPILOT_EXTENSIONS_CONTEXT", ""), cwd=source_anchor,
        )
        if selected.configured and selected.mode == "standalone":
            if "--no-project-controller" not in forwarded:
                forwarded = [*forwarded, "--no-project-controller"]
        with _request_environment(context):
            controlled = handoff(
                forwarded, observer=observer, authority=selected, defer_automatic=True,
            )
        if controlled is not None:
            return controlled
        if options.target is not None and options.target != "worktree-manager":
            from .runtime_source_config import require_marketplace

            require_marketplace()
        project, _args = extract_project(forwarded)
        if selected.configured and selected.mode == "project":
            project = selected.project
            forwarded = [*_args, "--project", project]
        _update_context_directory(context)
        cwd = Path.cwd().resolve()
        hold_override = os.environ.get("BOOTSTRAP_KILLSWITCH_STATE_FILE", "")
        hold_path = os.path.abspath(os.path.expanduser(hold_override)) if hold_override else ""
        arguments = list(forwarded)
        if _automatic_arguments(arguments) and "--auto" not in arguments:
            arguments.append("--auto")
        if options.unattended or (
            os.environ.get(_UNATTENDED) == "1" and not _automatic_arguments(arguments)
        ):
            arguments.append("--unattended")
        budget = options.engine_acquisition_timeout
        if budget is None and options.target is None:
            budget = os.environ.get(TIMEOUT_ENV)
        if budget is not None:
            selected_budget = str(acquisition_timeout({TIMEOUT_ENV: budget}))
            arguments.extend(["--engine-acquisition-timeout", selected_budget])
        spec = driver.RequestSpec(
            backend="manager", argv=tuple(arguments), project=project or "",
            cwd=source_anchor, source=_source_key(), context=tuple(context.items()),
            repair="--auto" not in arguments,
            killswitch_path=hold_path,
            authority=selected.mode if selected.configured else "",
        )
        argv, env = driver.runtime_command("worktree_manager", ["update", *arguments])
        request = driver.enqueue(
            spec, runner=driver.TrustedRunner(
                argv, str(cwd), env=tuple(env.items()), context_keys=_RUNNER_CONTEXT_KEYS,
            ),
        )
        print(f"Update request {request.id}; log: {request.log_path}", file=sys.stderr)
        worker_process = None
        if options.supervised_debug:
            driver.drain()
        else:
            worker, environment = driver.runtime_command("update_driver", ["worker"])
            worker_process = driver.ensure_worker(worker, env={
                **os.environ, **environment, "GIT_TERMINAL_PROMPT": "0",
                "GH_PROMPT_DISABLED": "1", "GCM_INTERACTIVE": "never",
            })
        if options.detach:
            _report(request, as_json=options.json)
            return 0
        result = driver.wait(request.id, output=sys.stderr, worker=worker_process)
        if result.state != "completed" or result.exit_code is None:
            _report(result, as_json=False)
            return 1
        from .update_python_path import finish_update

        return finish_update(["update", *rest], result.exit_code)
    except (driver.DriverError, driver.ParticipantError, EngineError, OSError, ValueError) as error:
        print(f"Update driver: {error}", file=sys.stderr)
        if options.json:
            print(json.dumps({
                "request_id": request.id if request is not None else options.request_status,
                "state": request.state if request is not None else "unavailable",
                "exit_code": None, "error": str(error),
            }))
        return 3 if isinstance(error, EngineError) and error.install_hint else 1
