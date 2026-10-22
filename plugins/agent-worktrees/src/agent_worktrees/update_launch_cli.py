"""Native launch maintenance submits fixed phases, never caller-supplied argv."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from . import reconcile, update_cli, update_stage
from .reconcile_deployment import execution_diagnostic


def add_parsers(sub) -> None:
    parser = sub.add_parser("launch-update", help="Submit durable native launch maintenance")
    parser.add_argument("phase", choices=("bootstrap", "reconcile", "staged"))
    parser.add_argument("--status", help="Staged acquisition status path")
    parser.add_argument("--detach", action="store_true", help="Return after durable submission")
    parser.add_argument(
        "--defer-worktrees", action="store_true",
        help="Leave the Worktrees runtime to the launcher's staged request",
    )
    parser.add_argument("--snapshot", help=argparse.SUPPRESS)


def _staged_snapshot(path: Path) -> tuple[dict, str]:
    content = path.read_bytes()
    if len(content) > 1024 * 1024:
        raise ValueError("Staged status exceeds the protocol limit")
    status = json.loads(content)
    if not isinstance(status, dict) or status.get("stage_done") is not True:
        raise ValueError("Staged acquisition has no complete result")
    for field in ("plugin_dir", "runtime_root"):
        if not isinstance(status.get(field), str) or not Path(status[field]).is_absolute():
            raise ValueError(f"Staged acquisition has an invalid {field}")
    if not isinstance(status.get("plugin_changed"), bool):
        raise ValueError("Staged acquisition has no boolean change decision")
    payload, _layout = update_stage.discover_plugin_dir()
    if payload is None or not reconcile._same_path(Path(status.get("plugin_dir", "")), payload):
        raise ValueError("Staged acquisition no longer names the installed payload")
    identity = content + b"\0" + str(payload.resolve()).encode("utf-8")
    identity += b"\0" + update_stage.fingerprint(payload).encode("utf-8")
    return status, hashlib.sha256(identity).hexdigest()


def cmd_launch_update(args: argparse.Namespace) -> int:
    from .update_driver_json_cli import _run_request

    flags = [args.phase]
    if args.phase == "staged":
        path = Path(args.status).expanduser().resolve() if args.status else update_stage.status_path()
        try:
            # A replay keeps its original selection; validation happens inside
            # custody so refusal is published as this request's durable result.
            digest = args.snapshot or _staged_snapshot(path)[1]
        except (OSError, ValueError, TypeError) as error:
            print(f"Launch update: {error}", file=sys.stderr)
            print(json.dumps({"ok": False, "reason": "staged-source-invalid", "message": str(error)}))
            return 1
        args.status, args.snapshot = str(path), digest
        flags += ["--status", str(path), "--snapshot", digest]
    elif args.status or args.snapshot:
        print("Launch update: only staged apply accepts acquisition status", file=sys.stderr)
        print(json.dumps({"ok": False, "reason": "invalid-phase-options"}))
        return 2
    if args.defer_worktrees:
        if args.phase != "bootstrap":
            print("Launch update: deferral is only valid for bootstrap", file=sys.stderr)
            print(json.dumps({"ok": False, "reason": "invalid-phase-options"}))
            return 2
        flags.append("--defer-worktrees")
    return _run_request("launch-update", flags, args, _execute)


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _apply(update: dict, *, staged: bool = False) -> dict:
    service = update.get("service", "?")
    diagnostic = execution_diagnostic(update, staged=staged)
    if diagnostic:
        _log(f"launch: {service} [{diagnostic['reason']}] {diagnostic['message']}")
        return {"service": service, "ok": False, "returncode": 3, "diagnostic": diagnostic}
    _log(f"launch: {service} starting")
    code = reconcile._run_plan_step(update["argv"], update, _log, output_fd=2)
    _log(f"launch: {service} {'done' if code == 0 else 'failed'} (exit {code})")
    return {"service": service, "ok": code == 0, "returncode": code}


def _execute(args: argparse.Namespace) -> int:
    try:
        return _execute_phase(args)
    except (OSError, ValueError) as error:
        _log(f"launch: {error}")
        print(json.dumps({"ok": False, "reason": "launch-phase-invalid", "message": str(error)}))
        return 1


def _execute_phase(args: argparse.Namespace) -> int:
    if args.phase == "reconcile":
        args.apply = True
        args.installer_output_fd = 2
        return update_cli.cmd_reconcile_plugins(args)
    executed = []
    if args.phase == "bootstrap":
        plan = update_cli._core_helper("plan_pre_launch", update_cli.plan_pre_launch)()
        for diagnostic in plan.get("diagnostics", []):
            _log(f"launch: [{diagnostic.get('reason')}] {diagnostic.get('message')}")
        if plan.get("action") == "self-update":
            for update in plan.get("updates", []):
                if args.defer_worktrees and update.get("service") == "agent-worktrees":
                    _log("launch: agent-worktrees deferred to the staged request")
                    continue
                # Repository bootstrap services do not inherit runtime-only unsets.
                update.setdefault("unset_environment", [])
                executed.append(_apply(update))
            remaining = update_cli._core_helper("plan_pre_launch", update_cli.plan_pre_launch)()
            if remaining.get("action") == "self-update":
                _log("launch: still stale after update; continuing with existing runtime")
    else:
        status, digest = _staged_snapshot(Path(args.status))
        if digest != args.snapshot:
            raise ValueError("Staged source changed after submission; acquire a new result")
        if status.get("runtime_apply_blocked"):
            raise ValueError(f"Staged runtime apply blocked: {status['runtime_apply_blocked']}")
        if status.get("plugin_changed") is True:
            payload, _layout = update_stage.discover_plugin_dir()
            if payload is None:
                raise ValueError("Staged payload disappeared")
            environment, root = reconcile.runtime_installer_environment(
                "agent-worktrees", payload, base={},
            )
            if not reconcile._same_path(Path(status.get("runtime_root", "")), root):
                raise ValueError("Staged runtime installation changed")
            installer = next((
                payload / "scripts" / name for name in update_cli.svc._preferred_installer_order()
                if (payload / "scripts" / name).is_file()
            ), None)
            command = update_cli._build_installer_argv(installer, install_dir=root) if installer else None
            if command is None:
                raise ValueError("Staged payload has no supported installer")
            argv = command[1]
            if installer.suffix == ".ps1":
                argv.insert(1, "-NoProfile")
            project = update_cli.cfg.active_project()
            if project:
                argv += ["-ProjectName" if installer.suffix == ".ps1" else "--project-name", project]
            executed.append(_apply({
                "service": "agent-worktrees", "phase": "runtime",
                "payload_root": str(payload), "runtime_root": str(root),
                "environment": environment,
                "unset_environment": list(reconcile._RUNTIME_ENV_UNSET), "argv": argv,
            }, staged=True))
    print(json.dumps({"action": "launch-update", "phase": args.phase, "executed": executed}))
    return 1 if any(not item["ok"] for item in executed) else 0
