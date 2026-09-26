"""Picker / validate / repair CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from . import config as cfg
from . import installer as inst
from . import output, validate as val
from .update_stage import discover_plugin_dir


def _core():
    from . import __main__ as core

    return core


def _exec_worktree_manager(*args, **kwargs):
    return _core()._exec_worktree_manager(*args, **kwargs)


def _json_output(*args, **kwargs):
    return _core()._json_output(*args, **kwargs)


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
        _json_output(
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


def _resolve_terminal_install_script() -> Path | None:
    """Locate ``install.ps1`` for the Windows Terminal profile refresh."""
    candidates: list[Path] = []

    manifest_path = cfg.install_dir() / "deploy-manifest.json"
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text())
            plugin_source = manifest.get("plugin_source")
            if plugin_source:
                candidates.append(Path(plugin_source) / "scripts" / "install.ps1")
        except Exception:
            pass

    try:
        plugin_dir, _layout = discover_plugin_dir()
        if plugin_dir:
            candidates.append(plugin_dir / "scripts" / "install.ps1")
    except Exception:
        pass

    try:
        candidates.append(Path(__file__).resolve().parents[2] / "scripts" / "install.ps1")
    except Exception:
        pass

    for candidate in candidates:
        try:
            if candidate.exists():
                return candidate
        except Exception:
            continue
    return None


def _refresh_terminal_profiles() -> bool:
    """Regenerate the Windows Terminal fragment from the saved selection."""
    install_script = _resolve_terminal_install_script()
    if install_script is None:
        output.warn(
            "Could not refresh Windows Terminal profiles: install.ps1 not found "
            "(checked deploy-manifest plugin_source and installed-plugin dir)"
        )
        return False

    cmd = ["pwsh", "-NoProfile", "-File", str(install_script), "refresh-profiles"]
    try:
        cmd += ["-ProjectName", cfg.project_name()]
    except Exception:
        pass

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
        )
    except Exception:
        output.warn("Could not refresh Windows Terminal profiles")
        return False

    if result.returncode != 0:
        output.warn(
            f"Could not refresh Windows Terminal profiles (installer exited {result.returncode})"
        )
        return False
    output.ok("Windows Terminal profiles refreshed")
    return True


def cmd_repair(_args: argparse.Namespace) -> int:
    """Repair this machine's agent-worktrees integration in place."""
    output.header("Repairing project binstubs")
    try:
        inst.reconcile_binstubs()
    except Exception as exc:
        output.err(f"Binstub repair failed: {exc}")
        return 1
    return 0
