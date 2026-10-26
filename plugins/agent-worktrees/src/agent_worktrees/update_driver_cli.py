"""Queue ordinary suite updates; reserve direct execution for checked custody."""

from __future__ import annotations

import argparse
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

from . import config as cfg
from . import update_cli

_CONTEXT_KEYS = (
    "AGENT_HOME", "AGENT_WORKTREES_UPDATE_CONTEXT", "AGENT_WORKTREES_PAYLOAD_ROOT",
    "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "WORKTREE_MANAGER_ROOT",
    "WORKTREE_MANAGER_ENGINE_ARGV", "WORKTREE_MANAGER_ENGINE_CMD", "CE_UPDATE_AUTH",
)


def _context() -> tuple[tuple[str, str], ...]:
    return tuple((key, os.environ[key]) for key in _CONTEXT_KEYS if key in os.environ)


def _invocation_cwd() -> Path:
    from . import __main__ as core

    return (core._INVOCATION_CWD or Path.cwd()).resolve()


def _request_context() -> tuple[tuple[str, str], ...]:
    context = dict(_context())
    selected = update_cli._invocation_update_context()
    if selected is not None:
        context["AGENT_WORKTREES_UPDATE_CONTEXT"] = str(selected)
    return tuple(context.items())


@contextmanager
def _execution_context(context):
    values = dict(context)
    if values.keys() - set(_CONTEXT_KEYS):
        raise ValueError("update request contains unsupported execution context")
    previous = dict(_context())
    try:
        for key in _CONTEXT_KEYS:
            os.environ.pop(key, None)
        os.environ.update(values)
        yield
    finally:
        for key in _CONTEXT_KEYS:
            os.environ.pop(key, None)
        os.environ.update(previous)


def _report(request, as_json: bool = False) -> None:
    data = {
        "request_id": request.id, "state": request.state, "exit_code": request.exit_code,
        "error": request.error, "log_path": str(request.log_path),
        "settled_at": getattr(request, "settled_at", None),
        "settlement_kind": getattr(request, "settlement_kind", None),
        "recovery_authority": getattr(request, "recovery_authority", None),
        "released_at": getattr(request, "released_at", None),
    }
    flags = getattr(getattr(request, "spec", None), "argv", ())
    phase = next((
        flags[index + 1] for index, flag in enumerate(flags[:-1])
        if flag == "--phase" and flags[index + 1] in update_cli.update_options.UPDATE_PHASES
    ), None)
    if phase:
        data.update(
            phase=phase,
            plugins=[flags[index + 1] for index, flag in enumerate(flags[:-1]) if flag == "--plugin"],
            full_update=False,
        )
    if as_json:
        print(json.dumps(data))
    else:
        print(f"Update request {request.id}: {request.state}; log: {request.log_path}")
        if request.error:
            print(request.error, file=sys.stderr)
        if data["recovery_authority"]:
            print(f"Repair release: {data['recovery_authority']}; cleanup remains unconfirmed.")


def _flags(args: argparse.Namespace) -> list[str]:
    execution = argparse.Namespace(**vars(args))
    for name in ("detach", "wait", "supervised_debug", "json"):
        setattr(execution, name, False)
    return update_cli._update_flags(execution)


def cmd_update_request(args: argparse.Namespace) -> int:
    import update_driver as driver

    update_cli.update_options.validate_auth_flags(args)
    if getattr(args, "inventory", False):
        from .installation_updates import print_inventory

        if _flags(args) or any(getattr(args, key, False) for key in (
            "detach", "wait", "supervised_debug", "request_status", "recovery_status",
            "recover_after_restart", "recover_for_repair", "auth_report",
        )):
            raise ValueError("--inventory is read-only and cannot select an update operation")
        return print_inventory()
    if getattr(args, "auth_report", False):
        return update_cli.cmd_update(args)
    request = None
    try:
        if getattr(args, "recovery_status", False):
            if _flags(args):
                raise ValueError("recovery inspection does not accept installation options")
            state = driver.recovery_status()
            data = {
                "eligible_requests": list(state.eligible_ids),
                "refused": [
                    {"request_id": item.request_id, "reason": item.reason} for item in state.refused
                ],
                "error": state.error,
            }
            print(json.dumps(data) if getattr(args, "json", False) else f"Update recovery: {data}")
            return 1 if state.error else 0
        if getattr(args, "recover_after_restart", False):
            if _flags(args):
                raise ValueError("recovery does not accept installation options")
            settled = driver.recover_after_restart()
            data = {
                "settled": [
                    {"request_id": row.id, "state": row.state, "settled_at": row.settled_at}
                    for row in settled
                ],
                "replayed": False,
            }
            print(json.dumps(data) if getattr(args, "json", False) else
                  f"Settled {len(settled)} interrupted request(s); no update was run.")
            return 0
        if getattr(args, "recover_for_repair", False):
            if _flags(args):
                raise ValueError("repair recovery does not accept installation options")
            released = driver.recover_for_repair(authorized=True)
            data = {
                "released": [
                    {
                        "request_id": row.id, "state": row.state,
                        "recovery_authority": row.recovery_authority, "released_at": row.released_at,
                        "settled_at": row.settled_at, "settlement_kind": row.settlement_kind,
                    } for row in released
                ],
                "replayed": False,
            }
            print(json.dumps(data) if getattr(args, "json", False) else
                  f"Released {len(released)} queue fence(s) by operator authorization; "
                  "cleanup remains unconfirmed; no update was run.")
            return 0
        request_id = getattr(args, "request_status", None)
        if request_id:
            if _flags(args):
                raise ValueError("--request-status does not accept installation options")
            _report(driver.status(request_id), getattr(args, "json", False))
            return 0
        if getattr(args, "json", False) and not getattr(args, "detach", False):
            raise ValueError("--json requires --detach, --request-status or --auth-report")
        custody = driver.borrowed_custody()
        if custody is not None:
            with custody:
                request = driver.status(custody.request_id, root=custody.root)
                if request.state != "running" or request.spec.backend not in {"manager", "engine"}:
                    raise driver.StateError("engine does not participate in this update request")
                with _execution_context(request.spec.context):
                    if (
                        request.spec.backend == "engine"
                        and request.spec.source != str(cfg.install_dir().resolve())
                    ):
                        raise driver.StateError("engine installation changed after submission")
                    custody.assert_owned()
                    return update_cli.cmd_update(args)
        project = cfg.active_project()
        flags = _flags(args)
        bypass = ["--no-manager"] if getattr(args, "no_manager", False) else []
        cwd = _invocation_cwd()
        spec = driver.RequestSpec(
            backend="engine", argv=tuple([*bypass, *flags]), project=project or "", cwd=str(cwd),
            source=str(cfg.install_dir().resolve()), context=_request_context(),
        )
        project_args = ["--project", project] if project else []
        argv, environment = driver.runtime_command(
            "agent_worktrees", [*project_args, "update", *bypass, *flags],
        )
        request = driver.enqueue(
            spec, runner=driver.TrustedRunner(
                argv, str(cwd), env=tuple(environment.items()), context_keys=_CONTEXT_KEYS,
            ),
        )
        print(f"Update request {request.id}; log: {request.log_path}", file=sys.stderr)
        worker_process = None
        if getattr(args, "supervised_debug", False):
            driver.drain()
        else:
            worker, environment = driver.runtime_command("update_driver", ["worker"])
            worker_process = driver.ensure_worker(worker, env={
                **os.environ, **environment, "GIT_TERMINAL_PROMPT": "0",
                "GH_PROMPT_DISABLED": "1", "GCM_INTERACTIVE": "never",
            })
        if getattr(args, "detach", False):
            _report(request, getattr(args, "json", False))
            return 0
        result = driver.wait(request.id, output=sys.stderr, worker=worker_process)
        if result.state != "completed" or result.exit_code is None:
            _report(result)
            return 1
        return result.exit_code
    except (driver.DriverError, driver.ParticipantError, OSError, ValueError) as error:
        print(f"Update driver: {error}", file=sys.stderr)
        if getattr(args, "json", False):
            print(json.dumps({
                "request_id": request.id if request is not None else getattr(args, "request_status", None),
                "state": request.state if request is not None else "unavailable",
                "exit_code": None, "error": str(error),
            }))
        return 1
