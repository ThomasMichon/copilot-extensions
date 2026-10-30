"""Durable execution boundary for the Manager's public update command."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from contextlib import contextmanager
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlsplit

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


def handoff_launch_options(environment: dict[str, str], handoff: dict[str, str]) -> dict[str, object]:
    """Keep scoped Windows updates headless without changing interactive passthrough."""
    options: dict[str, object] = {"env": {**environment, **handoff}}
    if handoff and os.name == "nt":
        from agent_procutil import no_window_kwargs

        options.update(no_window_kwargs())
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
    parser.add_argument("--engine-acquisition-timeout", help=argparse.SUPPRESS)
    options, forwarded = parser.parse_known_args(rest)
    if "--auth-report" in forwarded and (options.detach or options.supervised_debug):
        parser.error("--auth-report cannot select an installation execution mode")
    recovery = options.recovery_status or options.recover_after_restart or options.recover_for_repair
    if (options.request_status or recovery) and (
        forwarded or options.engine_acquisition_timeout is not None
    ):
        parser.error("request inspection/recovery does not accept installation options")
    if "--auth-report" in forwarded and options.engine_acquisition_timeout is not None:
        parser.error("authentication inspection does not accept an acquisition budget")
    if options.json and not (options.detach or options.request_status or recovery or "--auth-report" in forwarded):
        parser.error("--json requires request inspection, recovery, --detach or --auth-report")
    canonical = argparse.ArgumentParser(prog="worktree-manager update", add_help=False, allow_abbrev=False)
    for name in ("auto", "force", "no-anchor-sync", "no-prune-pivots", "recreate-venv", "no-manager"):
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
    parsed = canonical.parse_args(forwarded)
    options.auto = parsed.auto
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

    root = default_root().resolve()
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


def request_context() -> dict[str, str]:
    context = {key: os.environ[key] for key in _CONTEXT_KEYS if key in os.environ}
    if f"{__package__}.engine_client" in sys.modules:
        from .engine_client import ENGINE_ARGV_ENV, inherited_engine_command

        accepted = inherited_engine_command()
        if accepted is not None:
            context[ENGINE_ARGV_ENV] = json.dumps(accepted)
    return context


@contextmanager
def _request_environment(context):
    values = dict(context)
    if values.keys() - set(_CONTEXT_KEYS):
        raise ValueError("update request contains unsupported execution context")
    previous = {key: os.environ[key] for key in _CONTEXT_KEYS if key in os.environ}
    try:
        for key in _CONTEXT_KEYS:
            os.environ.pop(key, None)
        os.environ.update(values)
        yield
    finally:
        for key in _CONTEXT_KEYS:
            os.environ.pop(key, None)
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

    with _runtime_installer()._installation_custody() as custody:
        if custody is not None:
            return operation()
        argv, environment = driver.runtime_command("worktree_manager", ["self-install", "--apply"])
        root = str(default_root().resolve())
        context = {**request_context(), "WORKTREE_MANAGER_ROOT": root}
        request = driver.enqueue(
            driver.RequestSpec(
                "manager-self-install", ("--apply",), project=root,
                cwd=str(running_payload_dir()), source=_source_key(), context=tuple(context.items()),
            ),
            runner=driver.TrustedRunner(
                argv, str(running_payload_dir()), env=tuple(environment.items()), context_keys=_CONTEXT_KEYS,
            ),
        )
        driver.drain()
        result = driver.wait(request.id, output=sys.stdout)
        if result.state != "completed" or result.exit_code is None:
            _report(result, as_json=False)
            return 1
        return result.exit_code


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
                with _request_environment(request.spec.context), _acquisition_environment(
                    options.engine_acquisition_timeout,
                ):
                    if request.spec.backend == "manager" and request.spec.source != _source_key():
                        raise driver.StateError("Manager source changed after submission; submit a new request")
                    custody.assert_owned()
                    from . import __main__ as manager

                    return manager._cmd_update(forwarded)
        if options.target is not None and options.target != "worktree-manager":
            from . import engine_client
            from .runtime_source_config import require_marketplace

            require_marketplace()
            project, arguments = extract_project(forwarded)
            observer = [
                "--" + name.replace("_", "-")
                for name in ("detach", "wait", "supervised_debug", "json")
                if getattr(options, name)
            ]
            return engine_client.run_engine_passthrough(
                project, ["update", "--no-manager", *arguments, *observer],
                **({"headless": True} if options.detach else {}),
            )
        project, _args = extract_project(forwarded)
        cwd = Path.cwd().resolve()
        arguments = list(forwarded)
        budget = options.engine_acquisition_timeout
        if budget is None and options.target is None:
            budget = os.environ.get(TIMEOUT_ENV)
        if budget is not None:
            selected_budget = str(acquisition_timeout({TIMEOUT_ENV: budget}))
            arguments.extend(["--engine-acquisition-timeout", selected_budget])
        spec = driver.RequestSpec(
            backend="manager", argv=tuple(arguments), project=project or "",
            cwd=str(cwd), source=_source_key(), context=tuple(request_context().items()),
            repair=not options.auto,
        )
        argv, env = driver.runtime_command("worktree_manager", ["update", *arguments])
        request = driver.enqueue(
            spec, runner=driver.TrustedRunner(
                argv, str(cwd), env=tuple(env.items()), context_keys=_CONTEXT_KEYS,
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
        return result.exit_code
    except (driver.DriverError, driver.ParticipantError, EngineError, OSError, ValueError) as error:
        print(f"Update driver: {error}", file=sys.stderr)
        if options.json:
            print(json.dumps({
                "request_id": request.id if request is not None else options.request_status,
                "state": request.state if request is not None else "unavailable",
                "exit_code": None, "error": str(error),
            }))
        return 3 if isinstance(error, EngineError) and error.install_hint else 1
