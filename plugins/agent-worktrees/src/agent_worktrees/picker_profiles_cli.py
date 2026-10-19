"""Picker / validate / repair CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path

from agent_procutil import no_window_kwargs

from . import config as cfg
from . import installer as inst
from . import output, validate as val


def _core():
    from . import __main__ as core

    return core


def _exec_worktree_manager(*args, **kwargs):
    return _core()._exec_worktree_manager(*args, **kwargs)




def _usable_worktree_manager(*args, **kwargs):
    return _core()._usable_worktree_manager(*args, **kwargs)


def cmd_manager_install_trigger(*args, **kwargs):
    return _core().cmd_manager_install_trigger(*args, **kwargs)


def add_parsers(sub) -> None:
    p = sub.add_parser(
        "repair",
        help=(
            "Repair local integration in place by reconciling project binstubs. "
            "Version-independent (unlike 'update')."
        ),
    )
    p = sub.add_parser(
        "picker",
        help="Inspect or hand off to the standalone Worktree Manager picker",
    )
    p.add_argument(
        "picker_action",
        choices=["status", "mock", "screenshot"],
        nargs="?",
        default="status",
        help=(
            "status (default) reports whether the standalone Worktree Manager "
            "owns the picker seam here; mock and screenshot hand off to that "
            "manager when it is installed, otherwise the install trigger is shown"
        ),
    )
    p.add_argument("--json", action="store_true", help="Emit a JSON result")
    p.add_argument(
        "--out",
        default=None,
        help="screenshot: write the capture to this file (default: stdout)",
    )
    p.add_argument(
        "--format",
        dest="picker_format",
        choices=["svg", "text", "ansi"],
        default="svg",
        help="screenshot format: svg (audit screenshot), text (plain character grid), ansi (colour-aware grid)",
    )
    p.add_argument(
        "--live",
        action="store_true",
        help="screenshot: render the multi-machine SSH source instead of the local-only source",
    )
    p.add_argument(
        "--pivot",
        dest="picker_pivot",
        default=None,
        help="screenshot: switch to this pivot (top tab) before capturing, e.g. 'CodeSpaces' (case-insensitive; unknown labels capture the default Worktrees tab)",
    )
    p.add_argument(
        "--wait",
        dest="picker_wait",
        type=float,
        default=0.0,
        help="screenshot: with --pivot, seconds to wait for a registered pivot's background list to finish loading so the capture shows real rows (default: 0 = no wait)",
    )
    p.add_argument(
        "--local",
        dest="picker_local",
        action="store_true",
        help="mock: force the local-only source (data_local) instead of the multi-machine SSH source -- for an isolated sandbox preview with no resolvable mesh repo/roster",
    )
    p = sub.add_parser("validate", help="Validate core infrastructure files")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--files", nargs="*", default=None)
    p.add_argument("--worktree-path", default=None)
    p.add_argument("--default-branch", default="origin/master")


def cmd_picker(args: argparse.Namespace) -> int:
    """Inspect or hand off to the standalone Worktree Manager picker."""
    action = getattr(args, "picker_action", "status")
    as_json = getattr(args, "json", False)
    mgr = _usable_worktree_manager()

    if action == "mock":
        if not mgr:
            return cmd_manager_install_trigger(cfg.active_project())
        subcommand = ["picker", "mock"]
        project = cfg.active_project()
        if project:
            subcommand.append(project)
        if getattr(args, "picker_local", False):
            subcommand.append("--local")
        return _exec_worktree_manager(mgr, None, subcommand=subcommand)

    if action == "screenshot":
        if not mgr:
            return cmd_manager_install_trigger(cfg.active_project())
        subcommand = [
            "picker",
            "screenshot",
        ]
        project = cfg.active_project()
        if project:
            subcommand.append(project)
        subcommand += ["--format", getattr(args, "picker_format", "svg")]
        out = getattr(args, "out", None)
        if out:
            subcommand += ["--out", out]
        if getattr(args, "live", False):
            subcommand.append("--live")
        pivot = getattr(args, "picker_pivot", None)
        if pivot:
            subcommand += ["--pivot", pivot]
        wait_pivot = float(getattr(args, "picker_wait", 0.0) or 0.0)
        if wait_pivot > 0:
            subcommand += ["--wait", str(wait_pivot)]
        return _exec_worktree_manager(mgr, None, subcommand=subcommand)

    effective = mgr is not None
    if as_json:
        output._json_output(
            {
                "effective": effective,
                "manager_available": effective,
                "bundled": False,
                "install_trigger": not effective,
            }
        )
    else:
        print(f"effective:    {str(effective).lower()}")
        print("owner:        Worktree Manager" if effective else "owner:        install trigger")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    worktree_path = args.worktree_path or str(Path.cwd())
    files = args.files if args.files else None

    validate_paths: list[str] | None = None
    try:
        config = cfg.load_config()
        repo = config.default_repo
        if repo.validate_paths:
            validate_paths = repo.validate_paths
    except Exception:
        pass

    failures = val.validate_files(
        worktree_path,
        files,
        default_branch=args.default_branch,
        dry_run=args.dry_run,
        validate_paths=validate_paths,
    )
    return 1 if failures else 0


def _refresh_terminal_profiles(project: str | None = None, *, machine: str | None = None) -> bool:
    """Invoke the Manager's narrow owner surface, without staging an installer."""
    from .manager_launch_cli import _discover_control_plane_provider_manifests
    from .control_plane_providers import _machine_control_plane_providers_dir
    from .update_events import bounded

    try:
        manager = _discover_control_plane_provider_manifests(
            directory=_machine_control_plane_providers_dir(),
        ).get("worktree-manager")
        if manager is None:
            output.warn("Could not refresh Windows Terminal profiles: Worktree Manager is unavailable")
            return False
        target_project = cfg.project_name() if project is None else project
        if machine is None:
            machine = cfg.load_config(
                cfg.project_dir(target_project) / "config.yaml",
                project=target_project, include_control_plane_related_pr=False,
            ).machine
        if not isinstance(machine, str) or not machine.strip():
            raise ValueError("Cannot resolve machine key for terminal profile refresh")
        arguments = [*manager.command, "refresh-profiles", target_project, "--machine", machine]
        from . import projects_registry, registry_paths

        context = registry_paths.installation_context()
        input_kwargs = {}
        if context is not None:
            registry = registry_paths.registry_root()
            projects = projects_registry.parse_projects_registry(registry / "projects.yaml")
            names = set(projects["projects"]) | {target_project}
            input_kwargs["input"] = json.dumps({
                "schema": "copilot-extensions.terminal-profile-input-roots",
                "version": 1,
                "context": os.environ["COPILOT_EXTENSIONS_CONTEXT"],
                "payloadRoot": context["payloadRoot"],
                "registryRoot": str(registry),
                "projectRoots": {name: str(cfg.project_dir(name)) for name in sorted(names)},
            })
            arguments.append("--input-roots-stdin")
        result = subprocess.run(
            arguments,
            **input_kwargs,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **no_window_kwargs(),
        )
        response = json.loads(result.stdout)
        if (result.returncode != 0 or not isinstance(response, dict)
                or response.get("schema") != "copilot-extensions.terminal-profile-refresh"
                or type(response.get("version")) is not int
                or response.get("version") != 1
                or response.get("status") != "verified_current"):
            detail = (response.get("error") if isinstance(response, dict) else result.stderr)
            detail = detail or "Manager did not verify current profiles; update or repair Manager"
            output.warn(f"Could not refresh Windows Terminal profiles: {bounded(str(detail), 400)}")
            return False
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        output.warn(f"Could not refresh Windows Terminal profiles: {bounded(str(error), 400)}")
        return False
    output.ok("Windows Terminal profiles refreshed")
    for note in response.get("notes", []):
        output.info(bounded(str(note), 400))
    return True


def cmd_repair(_args: argparse.Namespace) -> int:
    """Repair this machine's agent-worktrees integration in place."""
    output.header("Repairing project binstubs")
    try:
        result = inst.reconcile_binstubs()
    except Exception as exc:
        output.err(f"Binstub repair failed: {exc}")
        return 1
    return 1 if result.get("blocked") else 0
