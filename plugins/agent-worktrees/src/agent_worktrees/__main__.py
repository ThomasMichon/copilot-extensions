"""CLI entry point — subcommand dispatcher for agent-worktrees.

Usage (via binstub):
    <project>                             # launch interactive picker
    <project> --no-update                 # skip pre-flight auto-update
    <project> --no-mux                    # bypass tmux/psmux multiplexer
    <project> resolve [--dry-run]         # emit JSON launch plan
    <project> get <key>                   # query project paths

Usage (direct):
    agent-worktrees resolve [--dry-run] [--recovery] [--no-mux] [-- args...]
    agent-worktrees resolve --json --worktree-id <id>
    agent-worktrees list [--json] [--tracking-status active|complete|...]
    agent-worktrees create [--json]
    agent-worktrees finalize [worktree-id] [--dry-run] [--json]
    agent-worktrees mark-complete [worktree-id] [--title T] [--title-only]
    agent-worktrees status [--json]
    agent-worktrees cleanup [--clean] [--include-unused] [--max-age-days N]
    agent-worktrees validate [--dry-run] [--files F...]
    agent-worktrees install [--force] [--machine NAME]
    agent-worktrees uninstall [--remove-config]
    agent-worktrees update
    agent-worktrees install-status
    agent-worktrees get <key>
    agent-worktrees services list [--json]
    agent-worktrees services status [--json]
    agent-worktrees services check-stale <install_dir> <repo_dir>
    agent-worktrees pre-launch

JSON mode (--json):
    stdout is machine-parseable JSON only, stderr is log output only.
    No TTY prompts, no picker, no color.  Stable schema with version field.
    Non-zero exit codes for errors with JSON error envelope on stdout.
    --json implies --no-mux.

When invoked with no subcommand (or unrecognized flags), the default
behaviour is "launch": exec into launch-session.sh with passthrough args.
The ``agent-worktrees`` prefix is stripped for SSH compatibility
(``<project> agent-worktrees cleanup`` still works).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import platform
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from . import config as cfg
from . import finalize as fin
from . import git_ops
from . import installer as inst
from . import output
from . import permissions
from . import services as svc
from . import sessions
from . import tracking
from . import validate as val
from .picker import ItemKind, MenuItem, PickResult, pick


# ── Env var migration helpers ───────────────────────────────────────────
# Phase 2 of copilot-worktrees extraction: APERTURE_* → WORKTREE_*
# Read new name first, fall back to old for backward compat.

_ENV_MIGRATION = {
    "WORKTREE_NO_UPDATE": "APERTURE_NO_UPDATE",
    "WORKTREE_NO_MUX": "APERTURE_NO_MUX",
    "WORKTREE_VERBOSE": "APERTURE_PRE_FLIGHT_VERBOSE",
    "WORKTREE_ID": "APERTURE_WORKTREE_ID",
    "WORKTREE_REPO": "APERTURE_REPO",
}


def _env_get(new_name: str) -> str | None:
    """Read an env var by its new name, falling back to the legacy name."""
    val = os.environ.get(new_name)
    if val:
        return val
    legacy = _ENV_MIGRATION.get(new_name)
    if legacy:
        return os.environ.get(legacy)
    return None


def _env_set(new_name: str, value: str) -> None:
    """Set both new and legacy env var names (transition period)."""
    os.environ[new_name] = value
    legacy = _ENV_MIGRATION.get(new_name)
    if legacy:
        os.environ[legacy] = value


# ═══════════════════════════════════════════════════════════════════════════
# Default launch — exec into launch-session.sh when no subcommand given
# ═══════════════════════════════════════════════════════════════════════════


def cmd_launch(argv: list[str]) -> int:
    """Default action: exec into launch-session.sh with passthrough args.

    Consumes ``--no-update``, ``--no-mux``, and ``--verbose`` and propagates
    them as environment variables so launch-session.sh can read them.
    """
    passthrough: list[str] = []
    for arg in argv:
        if arg == "--no-update":
            _env_set("WORKTREE_NO_UPDATE", "1")
        elif arg == "--no-mux":
            _env_set("WORKTREE_NO_MUX", "1")
        elif arg == "--verbose":
            _env_set("WORKTREE_VERBOSE", "1")
        else:
            passthrough.append(arg)

    # Resolve launch script path from installed location
    inst_dir = cfg.install_dir()
    plat = cfg.detect_platform()

    if plat == "windows":
        launch_script = inst_dir / "bin" / "launch-session.cmd"
    else:
        launch_script = inst_dir / "bin" / "launch-session.sh"

    # Fall back to legacy location
    if not launch_script.exists():
        legacy_name = "launch-session.sh"
        legacy = Path.home() / f".{cfg.project_name()}" / "bin" / legacy_name
        if legacy.exists():
            launch_script = legacy

    if not launch_script.exists():
        output.err(f"{launch_script.name} not found at {launch_script}")
        output.err("Run 'agent-worktrees install' first.")
        return 1

    if plat == "windows":
        # On Windows, use cmd.exe to run the .cmd launcher
        sys.exit(subprocess.call(
            ["cmd.exe", "/c", str(launch_script), *passthrough],
        ))
    else:
        os.execvp("bash", ["bash", str(launch_script), *passthrough])
    return 1  # unreachable — os.execvp replaces process


def _age_str(started_at: str) -> str:
    """Format a human-readable age string from an ISO timestamp."""
    try:
        start = datetime.fromisoformat(started_at)
        delta = datetime.now() - start
        minutes = int(delta.total_seconds() / 60)
        if minutes >= 1440:
            return f"{minutes // 1440}d ago"
        if minutes >= 60:
            return f"{minutes // 60}h ago"
        return f"{minutes}m ago"
    except Exception:
        return "?"


def _normalize_path(p: str) -> str:
    """Normalize for comparison."""
    p = p.rstrip("/\\")
    if platform.system() == "Windows":
        return p.lower()
    return p


def _build_active_paths(records: list[tracking.WorktreeRecord]) -> set[str]:
    """Build set of normalized paths with live sessions (lock files OR mux sessions)."""
    all_paths = [r.worktree_path for r in records if r.worktree_path]
    session_ctx = sessions.scan_sessions(all_paths)
    active = {
        _normalize_path(p) for p, sids in session_ctx.active_sessions.items() if sids
    }
    # Also check for live multiplexer sessions (independent of lock files)
    for rec in records:
        if rec.worktree_path and sessions.has_mux_session(rec.worktree_id):
            active.add(_normalize_path(rec.worktree_path))
    return active


def _apply_tracking_override(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
) -> git_ops.WorktreeStateInfo:
    """Let tracking metadata override ambiguous git-state classification.

    When a worktree was finalized with zero commits (e.g., manual
    intervention, no code changes), the reflog has no ``commit`` entries
    and ``classify_worktree`` returns UNUSED. The tracking YAML correctly
    records ``status: finalized`` — trust it.
    """
    if info.state == git_ops.WorktreeState.UNUSED and rec.status == "finalized":
        return dataclasses.replace(info, state=git_ops.WorktreeState.COMPLETED)
    return info


# ═══════════════════════════════════════════════════════════════════════════
# resolve — JSON launch plan (Python exits before Copilot starts)
# ═══════════════════════════════════════════════════════════════════════════

def _emit_plan(plan: dict) -> None:
    """Write the JSON launch plan to the real stdout (not the swapped one).

    For exec/wsl actions, injects COPILOT_CUSTOM_INSTRUCTIONS_DIRS pointing
    to the install dir so machine+repo-specific instructions are loaded
    without polluting other repos on the same machine.
    """
    if plan.get("action") in ("exec", "wsl"):
        env = plan.setdefault("env", {})
        env.setdefault(
            "COPILOT_CUSTOM_INSTRUCTIONS_DIRS", str(cfg.install_dir())
        )
    sys.__stdout__.write(json.dumps(plan) + "\n")
    sys.__stdout__.flush()


# ═══════════════════════════════════════════════════════════════════════════
# JSON output helpers — shared by all --json modes
# ═══════════════════════════════════════════════════════════════════════════

_JSON_SCHEMA_VERSION = 1


def _json_output(data: dict) -> None:
    """Write a versioned JSON envelope to the real stdout.

    Always writes to ``sys.__stdout__`` so it works inside
    ``output.stdout_to_stderr()`` blocks.
    """
    envelope = {"version": _JSON_SCHEMA_VERSION, **data}
    sys.__stdout__.write(json.dumps(envelope, indent=2) + "\n")
    sys.__stdout__.flush()


def _json_error(message: str, exit_code: int = 1) -> int:
    """Emit a JSON error envelope and return the exit code."""
    _json_output({"error": message})
    return exit_code


def _worktree_to_dict(
    rec: tracking.WorktreeRecord,
    *,
    state_info: git_ops.WorktreeStateInfo | None = None,
) -> dict:
    """Serialize a WorktreeRecord to a JSON-friendly dict.

    If ``state_info`` is provided, includes git-derived classification
    (state, ahead, behind, dirty) alongside the tracking status.
    """
    d: dict = {
        "id": rec.worktree_id,
        "branch": rec.branch,
        "path": rec.worktree_path,
        "machine": rec.machine,
        "platform": rec.platform,
        "status": rec.status,
        "started_at": rec.started_at,
        "title": rec.title,
        "resume_count": rec.resume_count,
    }
    if rec.completed_at:
        d["completed_at"] = rec.completed_at
    if state_info is not None:
        d["state"] = state_info.state.value
        d["ahead"] = state_info.ahead
        d["behind"] = state_info.behind
        d["dirty"] = state_info.dirty
        if state_info.branch_drift and state_info.current_branch:
            d["current_branch"] = state_info.current_branch
            d["branch_drift"] = True
    return d


def _create_worktree_core(
    config: cfg.Config,
    *,
    profile: cfg.CopilotProfile | None = None,
    no_mux: bool = False,
) -> dict:
    """Create a new worktree and return a dict with worktree info + launch plan.

    Performs the side-effects (fetch, git worktree add, tracking YAML,
    permissions) but does NOT launch copilot.  Returns a dict suitable
    for JSON serialization.

    Raises ``RuntimeError`` on failure.
    """
    repo = config.default_repo
    plat = cfg.detect_platform()
    plat_short = "win" if plat == "windows" else plat

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)
    worktree_id = f"{config.machine}-{plat_short}-{timestamp}-{suffix}"
    branch = f"worktree/{worktree_id}"
    worktree_path = str(Path(repo.worktree_root) / worktree_id)
    upstream = f"{repo.remote}/{repo.default_branch}"

    # Ensure root exists
    Path(repo.worktree_root).mkdir(parents=True, exist_ok=True)

    # Fetch and create
    print(f"Fetching latest from {repo.remote}...", file=sys.stderr)
    git_ops.git("fetch", repo.remote, "--quiet", cwd=repo.anchor, check=False)

    print(f"Creating worktree on branch {branch}...", file=sys.stderr)
    git_ops.create_worktree(repo.anchor, worktree_path, branch, upstream)

    # Write tracking YAML
    tracking_path = cfg.tracking_dir()
    tracking_path.mkdir(parents=True, exist_ok=True)
    record = tracking.create_new_record(
        worktree_id=worktree_id,
        branch=branch,
        worktree_path=worktree_path,
        repo=config.repo_name,
        machine=config.machine,
        platform_name=plat,
        tracking_path=tracking_path,
    )

    # Clone permissions
    if permissions.clone_permissions(repo.anchor, worktree_path):
        print("Copied Copilot permissions to worktree path.", file=sys.stderr)

    # Trust the new worktree path
    if permissions.add_trusted_folder(worktree_path):
        print("Added worktree path to trusted_folders.", file=sys.stderr)

    # Build launch command (for caller to use)
    fake_args = argparse.Namespace(
        copilot_args=[], recovery=False, no_mux=no_mux,
        no_resume=False, profile=None,
    )
    launch_cmd = _build_launch_cmd(config, fake_args, worktree_path, profile=profile)
    env = _build_env(profile)

    return {
        "worktree": _worktree_to_dict(record),
        "launch": {
            "work_dir": worktree_path,
            "cmd": launch_cmd,
            "env": env,
            "worktree_id": worktree_id,
            "post_exit": True,
            "no_mux": no_mux,
        },
    }


def _build_env(profile: cfg.CopilotProfile | None) -> dict[str, str]:
    """Build env dict with auto-injected vars, then profile overrides.

    Convention-based vars (like COPILOT_CUSTOM_INSTRUCTIONS_DIRS) are set
    first, then profile env merges on top.  For path-list vars like
    COPILOT_CUSTOM_INSTRUCTIONS_DIRS, profile values are appended rather
    than replacing the auto-injected value.
    """
    env: dict[str, str] = {}

    # Auto-inject: dynamic instructions live in ~/.{project}
    project_dir = str(cfg.project_dir())
    env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] = project_dir

    # Merge profile env, appending for path-list keys
    if profile and profile.env:
        _PATH_LIST_KEYS = {"COPILOT_CUSTOM_INSTRUCTIONS_DIRS"}
        for k, v in profile.env.items():
            if k in _PATH_LIST_KEYS and k in env:
                env[k] = env[k] + os.pathsep + v
            else:
                env[k] = v

    return env


def _build_launch_cmd(
    config: cfg.Config,
    args: argparse.Namespace,
    work_dir: str,
    profile: cfg.CopilotProfile | None = None,
) -> list[str]:
    """Build the launch command from config or fallback convention.

    If the repo config has ``launch`` / ``launch_recovery`` entries for
    the current platform, those are used with variable substitution.
    Otherwise falls back to the legacy ``tools/setup/setup.{ps1,sh}``
    convention for backward compatibility.
    """
    recovery = getattr(args, "recovery", False)
    repo = config.default_repo
    plat = config.platform  # "windows", "wsl", or "linux"
    plat_key = plat if plat != "wsl" else "linux"

    # Try config-driven launch commands first
    launch_map = repo.launch_recovery if recovery else repo.launch
    if plat_key in launch_map:
        template = launch_map[plat_key]
        anchor = repo.anchor
        variables = {
            "work_dir": work_dir,
            "anchor": anchor,
            "machine": config.machine,
            "repo_name": config.repo_name,
        }
        cmd = [arg.format(**variables) for arg in template]
    else:
        # Legacy fallback — hardcoded setup script convention
        if platform.system() == "Windows":
            if recovery:
                setup_path = str(Path(repo.anchor) / "tools" / "setup" / "setup.ps1")
            else:
                setup_path = str(Path(work_dir) / "tools" / "setup" / "setup.ps1")
            cmd = ["pwsh.exe", "-NoProfile", "-NoLogo", "-File", setup_path, "-Machine", config.machine]
            if recovery:
                cmd.append("-Recovery")
        else:
            if recovery:
                setup_path = str(Path(repo.anchor) / "tools" / "setup" / "setup.sh")
            else:
                setup_path = str(Path(work_dir) / "tools" / "setup" / "setup.sh")
            cmd = ["bash", setup_path, "--machine", config.machine]
            if recovery:
                cmd.append("--recovery")

    extra = getattr(args, "copilot_args", []) or []
    cmd.extend(extra)

    # Append profile-specific Copilot args
    if profile and profile.copilot_args:
        cmd.extend(profile.copilot_args)

    return cmd


def cmd_resolve(args: argparse.Namespace) -> int:
    """Resolve a launch plan and emit it as JSON.

    All user-facing output (picker, status messages) goes to stderr.
    The JSON launch plan goes to the real stdout for the calling shell.

    With ``--json``, skips the interactive picker and resolves a specific
    worktree by ID (``--worktree-id``).  ``--json`` implies ``--no-mux``.
    """
    use_json = getattr(args, "json", False)
    if use_json:
        args.no_mux = True
        # Validate required args before any I/O
        wt_id = getattr(args, "worktree_id", None)
        if not wt_id:
            return _json_error("--worktree-id is required with --json")

    with output.stdout_to_stderr():
        if use_json:
            try:
                config = cfg.load_config()
            except Exception as e:
                return _json_error(str(e))

            wt_id = _resolve_worktree_id(wt_id)  # type: ignore[possibly-undefined]
            yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
            if not yaml_path.exists():
                return _json_error(f"Worktree not found: {wt_id}")
            record = tracking.load_record(yaml_path)
            tracking.mark_resumed(record)

            launch_cmd = _build_launch_cmd(config, args, record.worktree_path)
            env = _build_env(None)

            # Auto-resume session
            no_resume = getattr(args, "no_resume", False)
            if not no_resume:
                last_session = sessions.find_latest_session_id(record.worktree_path)
                if last_session:
                    launch_cmd.extend(["--resume", last_session])

            _json_output({
                "worktree": _worktree_to_dict(record),
                "launch": {
                    "action": "exec",
                    "work_dir": record.worktree_path,
                    "cmd": launch_cmd,
                    "env": env,
                    "worktree_id": record.worktree_id,
                    "post_exit": True,
                    "no_mux": True,
                },
            })
            return 0

        config = cfg.load_config()
        repo = config.default_repo

        # WSL delegation
        if args.copilot_args and args.copilot_args[0] == "wsl":
            remaining = args.copilot_args[1:]
            project = cfg.project_name()
            no_mux_export = "export WORKTREE_NO_MUX=1; export APERTURE_NO_MUX=1; " if args.no_mux else ""
            if args.dry_run:
                output.dry_run(f"Would delegate to WSL: WORKTREE_PROJECT={project} ~/.agent-worktrees/bin/launch-session.sh {' '.join(remaining)}")
                _emit_plan({"action": "none", "exit_code": 0})
                return 0
            wsl_cmd = ["wsl", "bash", "-lc", f"{no_mux_export}export WORKTREE_PROJECT={project}; ~/.agent-worktrees/bin/launch-session.sh {' '.join(remaining)}"]
            _emit_plan({"action": "wsl", "cmd": wsl_cmd})
            return 0

        tracking_path = cfg.tracking_dir()
        tracking_path.mkdir(parents=True, exist_ok=True)
        current_platform = cfg.detect_platform()

        # Picker loop — re-enters after system menu actions
        while True:

            # Load active worktrees (include "complete" — these are worktrees
            # where finalization failed or was skipped, e.g. terminal closed
            # before post-exit could run).  They still have local commits and
            # should be resumable in the picker.
            records = tracking.list_records(
                tracking_path, status_filter="active", platform_filter=current_platform,
            )
            complete_records = tracking.list_records(
                tracking_path, status_filter="complete", platform_filter=current_platform,
            )
            # Revert stale "complete" records to "active" so they behave
            # normally in the picker and downstream classification.
            for rec in complete_records:
                tracking.update_status(rec, "active")
            records = records + complete_records

            # Include finalized worktrees whose directories still exist.
            # This happens when finalization skips removal because we're
            # running inside the worktree or a live session is detected.
            finalized_records = tracking.list_records(
                tracking_path, status_filter="finalized", platform_filter=current_platform,
            )
            finalized_still_present = [
                r for r in finalized_records if Path(r.worktree_path).exists()
            ]
            records = records + finalized_still_present

            records = [
                r for r in records
                if Path(r.worktree_path).exists()
                and (Path(r.worktree_path) / ".git").exists()
            ]

            # Scan for live Copilot sessions and mux sessions
            all_paths = [r.worktree_path for r in records if r.worktree_path]
            session_ctx = sessions.scan_sessions(all_paths)
            active_paths = _build_active_paths(records)

            # Classify each by git state (session-aware)
            classified: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
            for rec in records:
                info = git_ops.classify_worktree(
                    rec.worktree_path, rec.branch,
                    remote=repo.remote, default_branch=repo.default_branch,
                    active_paths=active_paths,
                )
                info = _apply_tracking_override(rec, info)
                classified.append((rec, info))

            # Bucket into categories
            active_wts: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
            recent_wts: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
            unused_wts: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
            completed_wts: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []

            for rec, info in classified:
                if info.state == git_ops.WorktreeState.ACTIVE:
                    active_wts.append((rec, info))
                elif info.state == git_ops.WorktreeState.UNUSED:
                    unused_wts.append((rec, info))
                elif info.state == git_ops.WorktreeState.COMPLETED:
                    completed_wts.append((rec, info))
                else:
                    recent_wts.append((rec, info))

            # Build picker menu
            menu_items: list[MenuItem] = []

            def _wt_label(rec: tracking.WorktreeRecord, info: git_ops.WorktreeStateInfo, icon: str) -> str:
                age = _age_str(rec.started_at)
                resume = f", {rec.resume_count} resumes" if rec.resume_count > 0 else ""
                norm = _normalize_path(rec.worktree_path)
                sessions_list = session_ctx.active_sessions.get(norm, [])
                tag = ""
                if len(sessions_list) > 1:
                    tag = f" 🟢 {len(sessions_list)} sessions"
                elif len(sessions_list) == 1:
                    tag = " 🟢 in session"

                # Show branch drift indicator when HEAD differs from tracked branch
                drift_tag = ""
                if info.branch_drift and info.current_branch:
                    drift_tag = f" ⚠ {info.current_branch}"

                state_tag = f" [{info.state.value}]" if info.state in (git_ops.WorktreeState.UNUSED, git_ops.WorktreeState.COMPLETED) else ""
                short_id = rec.worktree_id[-4:] if len(rec.worktree_id) > 4 else rec.worktree_id
                return f"{icon} …{short_id}  ({age}{resume}){tag}{drift_tag}{state_tag}"

            def _wt_subtitle(rec: tracking.WorktreeRecord, info: git_ops.WorktreeStateInfo) -> str | None:
                """Resolve the best available title for a worktree."""
                norm = _normalize_path(rec.worktree_path)
                title = ""
                if rec.title and rec.title != "null":
                    title = rec.title
                elif norm in session_ctx.latest_summary:
                    title = session_ctx.latest_summary[norm]
                elif info.title:
                    title = info.title
                if not title:
                    return None
                # Sanitize to single line
                return " ".join(title.split())

            for rec, info in active_wts:
                menu_items.append(MenuItem(
                    label=_wt_label(rec, info, "🟢"),
                    subtitle=_wt_subtitle(rec, info),
                    kind=ItemKind.NORMAL, value=("worktree", rec),
                ))

            if active_wts:
                menu_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))

            new_idx = len(menu_items)
            menu_items.append(MenuItem(label="✨ New worktree", kind=ItemKind.ACTION, value=("new", None)))
            menu_items.append(MenuItem(label="📂 Base repo (no worktree)", kind=ItemKind.ACTION, value=("base", None)))

            if recent_wts:
                menu_items.append(MenuItem(label="─── recent ─────────────────────", kind=ItemKind.SEPARATOR))
            for rec, info in recent_wts:
                menu_items.append(MenuItem(
                    label=_wt_label(rec, info, "🌳"),
                    subtitle=_wt_subtitle(rec, info),
                    kind=ItemKind.NORMAL, value=("worktree", rec),
                ))

            if unused_wts:
                menu_items.append(MenuItem(label="─── unused ─────────────────────", kind=ItemKind.SEPARATOR))
                for rec, info in unused_wts:
                    menu_items.append(MenuItem(
                        label=_wt_label(rec, info, "⬜"),
                        subtitle=_wt_subtitle(rec, info),
                        kind=ItemKind.DIMMED, value=("worktree", rec),
                    ))

            if completed_wts:
                menu_items.append(MenuItem(label="─── completed ──────────────────", kind=ItemKind.SEPARATOR))
                for rec, info in completed_wts:
                    menu_items.append(MenuItem(
                        label=_wt_label(rec, info, "✅"),
                        subtitle=_wt_subtitle(rec, info),
                        kind=ItemKind.DIMMED, value=("worktree", rec),
                    ))

            # System menu item
            menu_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
            menu_items.append(MenuItem(label="⚙ System menu", kind=ItemKind.ACTION, value=("system", None)))

            # Build profile labels for the picker toggle
            profiles = config.copilot_profiles or [cfg.DEFAULT_PROFILE]
            profile_labels = [p.label for p in profiles]

            # Resolve --profile flag to a default index
            profile_default = 0
            requested_profile = getattr(args, "profile", None)
            if requested_profile:
                for i, p in enumerate(profiles):
                    if p.name == requested_profile:
                        profile_default = i
                        break

            result = pick(
                menu_items,
                title=f"🌳 {config.repo_name.replace('-', ' ').title()} — Worktree Picker",
                subtitle="Use ↑↓, Enter select, : system menu, Esc cancel",
                default=new_idx,
                profile_labels=profile_labels if len(profiles) > 1 else None,
                profile_default=profile_default,
            )

            # Handle system menu via : key or ⚙ item
            if result.command == "system":
                rc = _run_system_menu(config, args)
                if rc is not None:
                    return rc
                continue

            if result.selected < 0:
                print("Cancelled.")
                _emit_plan({"action": "none", "exit_code": 0})
                return 0

            sel = result.selected
            selected_profile = profiles[result.profile_idx]
            action, value = menu_items[sel].value  # type: ignore[misc]

            # System menu via selectable ⚙ item
            if action == "system":
                rc = _run_system_menu(config, args)
                if rc is not None:
                    return rc
                continue

            if selected_profile.name != "cloud":
                print(f"   Backend: {selected_profile.label}")

            # --- Base repo mode ---
            if action == "base":
                return _resolve_base_repo(config, args, profile=selected_profile)

            # --- Resume ---
            if action == "worktree":
                rec = value  # type: ignore[assignment]
                return _resolve_resume(rec, config, args, profile=selected_profile)

            # --- New worktree ---
            return _resolve_new(config, args, profile=selected_profile)


def _run_system_menu(config: cfg.Config, args: argparse.Namespace) -> int | None:
    """Show system menu and run the selected action.

    Returns an exit code if the caller should exit, or None to re-show
    the main picker.
    """
    system_items = [
        MenuItem(label="🧹 Cleanup worktrees", kind=ItemKind.ACTION, value="cleanup"),
        MenuItem(label="📊 Worktree status", kind=ItemKind.ACTION, value="status"),
        MenuItem(label="", kind=ItemKind.SEPARATOR),
        MenuItem(label="↩ Back to picker", kind=ItemKind.ACTION, value="back"),
    ]

    result = pick(
        system_items,
        title=f"⚙ {config.repo_name.replace('-', ' ').title()} — System Menu",
        subtitle="Use ↑↓, Enter select, Esc back",
        default=0,
    )

    if result.selected < 0:
        return None  # Back to picker

    action = system_items[result.selected].value
    if action == "back":
        return None

    if action == "cleanup":
        return _system_cleanup(config)

    if action == "status":
        return _system_status(config)

    return None


def _system_cleanup(config: cfg.Config) -> int | None:
    """Compact cleanup flow for the system menu — picker-style UX."""
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    records = tracking.list_records(tracking_path)

    if not records:
        _system_pause("No tracked worktrees.")
        return None

    # Classify all worktrees
    git_ops.fetch(repo.remote, cwd=repo.anchor)
    upstream = f"{repo.remote}/{repo.default_branch}"

    active_paths = _build_active_paths(records)

    cleanable: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
    unused: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []

    for rec in records:
        if rec.status == "finalized":
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
        elif rec.worktree_path and Path(rec.worktree_path).exists():
            info = git_ops.classify_worktree(
                rec.worktree_path, rec.branch,
                fetch=False, remote=repo.remote, default_branch=repo.default_branch,
                active_paths=active_paths,
            )
        else:
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.GONE)

        if info.state == git_ops.WorktreeState.COMPLETED:
            cleanable.append((rec, info))
        elif info.state == git_ops.WorktreeState.GONE:
            if not rec.branch or git_ops.is_branch_merged(
                rec.branch, upstream, cwd=repo.anchor,
            ):
                cleanable.append((rec, info))
        elif info.state == git_ops.WorktreeState.UNUSED:
            unused.append((rec, info))

    if not cleanable and not unused:
        _system_pause("Nothing to clean — all worktrees are active or have unmerged work.")
        return None

    # Build confirmation picker
    confirm_items: list[MenuItem] = []

    if cleanable:
        confirm_items.append(MenuItem(
            label=f"🧹 Clean {len(cleanable)} completed worktree(s)",
            subtitle=", ".join(r.worktree_id[-4:] for r, _ in cleanable),
            kind=ItemKind.ACTION, value="clean",
        ))

    if unused:
        confirm_items.append(MenuItem(
            label=f"🧹 Also clean {len(unused)} unused worktree(s) (empty)",
            subtitle=", ".join(r.worktree_id[-4:] for r, _ in unused),
            kind=ItemKind.ACTION, value="clean-all",
        ))

    confirm_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
    confirm_items.append(MenuItem(label="↩ Cancel", kind=ItemKind.ACTION, value="cancel"))

    result = pick(
        confirm_items,
        title="🧹 Cleanup — select action",
        subtitle="Use ↑↓, Enter select, Esc cancel",
        default=0,
    )

    if result.selected < 0:
        return None

    choice = confirm_items[result.selected].value
    if choice == "cancel":
        return None

    # Execute cleanup
    include_unused = (choice == "clean-all")
    cleanup_args = argparse.Namespace(
        clean=True, include_unused=include_unused, max_age_days=None,
    )
    cmd_cleanup(cleanup_args)

    # Show result briefly in a picker-style pause
    _system_pause("Cleanup complete.")
    return None


def _system_status(config: cfg.Config) -> int | None:
    """Compact status view for the system menu."""
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    records = tracking.list_records(tracking_path)

    if not records:
        _system_pause("No tracked worktrees.")
        return None

    all_paths = [r.worktree_path for r in records]
    session_ctx = sessions.scan_sessions(all_paths)
    active_paths = _build_active_paths(records)

    # Build status as picker items (view-only)
    status_items: list[MenuItem] = []
    STATE_ICONS = {
        "active": "🟢", "unused": "⬜", "completed": "✅",
        "wip": "🌳", "dirty": "🔴", "gone": "💀", "orphan": "❓",
    }

    for rec in records:
        info = git_ops.classify_worktree(
            rec.worktree_path, rec.branch,
            fetch=True, remote=repo.remote, default_branch=repo.default_branch,
            active_paths=active_paths,
        )
        info = _apply_tracking_override(rec, info)
        short_id = rec.worktree_id[-4:]
        icon = STATE_ICONS.get(info.state.value, "·")
        age = _age_str(rec.started_at)
        state_str = info.state.value

        label = f"{icon} …{short_id}  {state_str:<10} {age}"
        title = rec.title if (rec.title and rec.title != "null") else (info.title or None)
        subtitle = " ".join(title.split()) if title else None

        status_items.append(MenuItem(
            label=label, subtitle=subtitle,
            kind=ItemKind.DIMMED, value=None,
        ))

    status_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
    status_items.append(MenuItem(label="↩ Back", kind=ItemKind.ACTION, value="back"))

    pick(
        status_items,
        title=f"📊 {config.repo_name.replace('-', ' ').title()} — Status",
        subtitle="Esc or Enter to return",
        default=len(status_items) - 1,
    )
    return None


def _system_pause(msg: str) -> None:
    """Show a brief message via a single-item picker (press Enter to dismiss)."""
    items = [MenuItem(label=f"↩ {msg}", kind=ItemKind.ACTION, value="ok")]
    pick(items, title="", subtitle="Enter to return", default=0)


def _resolve_base_repo(
    config: cfg.Config,
    args: argparse.Namespace,
    profile: cfg.CopilotProfile | None = None,
) -> int:
    """Resolve launch plan for base repo mode."""
    repo = config.default_repo
    print()
    print("📂 Base Repo Mode — No Worktree")
    print(f"   Path: {repo.anchor}")
    print()
    output.warn("Commits will go directly to the current branch.")
    print()

    dirty = git_ops.get_dirty_files(repo.anchor)
    if dirty:
        output.warn(f"Anchor repo has {len(dirty)} uncommitted change(s):")
        for f in dirty[:5]:
            print(f"     {f}")
        if len(dirty) > 5:
            print(f"     ... and {len(dirty) - 5} more")
        print()

    launch_cmd = _build_launch_cmd(config, args, repo.anchor, profile=profile)
    merged_env = _build_env(profile)
    if args.dry_run:
        output.dry_run(f"Would launch: {' '.join(launch_cmd)}")
        if merged_env:
            output.dry_run(f"Would set env: {', '.join(f'{k}={v}' for k, v in merged_env.items())}")
        _emit_plan({"action": "none", "exit_code": 0})
        return 0

    _emit_plan({
        "action": "exec",
        "work_dir": repo.anchor,
        "cmd": launch_cmd,
        "env": merged_env,
        "worktree_id": None,
        "post_exit": False,
        "no_mux": getattr(args, "no_mux", False),
    })
    return 0


def _resolve_resume(
    record: tracking.WorktreeRecord,
    config: cfg.Config,
    args: argparse.Namespace,
    profile: cfg.CopilotProfile | None = None,
) -> int:
    """Resolve launch plan for resuming an existing worktree."""
    print()
    print(f"🌳 Resuming worktree: {record.worktree_id}")
    print(f"   Path: {record.worktree_path}")

    tracking.mark_resumed(record)

    launch_cmd = _build_launch_cmd(config, args, record.worktree_path, profile=profile)
    merged_env = _build_env(profile)

    # Auto-resume: find the most recent Copilot session for this worktree
    # and pass --resume <session-id> so the user picks up where they left off.
    no_resume = getattr(args, "no_resume", False)
    if not no_resume:
        last_session = sessions.find_latest_session_id(record.worktree_path)
        if last_session:
            launch_cmd.extend(["--resume", last_session])
            print(f"   Resuming session: {last_session[:12]}…")

    print()

    if args.dry_run:
        output.dry_run(f"Would launch: {' '.join(launch_cmd)}")
        if merged_env:
            output.dry_run(f"Would set env: {', '.join(f'{k}={v}' for k, v in merged_env.items())}")
        _emit_plan({"action": "none", "exit_code": 0})
        return 0

    _emit_plan({
        "action": "exec",
        "work_dir": record.worktree_path,
        "cmd": launch_cmd,
        "env": merged_env,
        "worktree_id": record.worktree_id,
        "post_exit": True,
        "no_mux": getattr(args, "no_mux", False),
    })
    return 0


def _resolve_new(
    config: cfg.Config,
    args: argparse.Namespace,
    profile: cfg.CopilotProfile | None = None,
) -> int:
    """Resolve launch plan for creating a new worktree."""
    repo = config.default_repo
    plat = cfg.detect_platform()
    plat_short = "win" if plat == "windows" else plat

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)
    worktree_id = f"{config.machine}-{plat_short}-{timestamp}-{suffix}"
    branch = f"worktree/{worktree_id}"
    worktree_path = str(Path(repo.worktree_root) / worktree_id)

    print()
    print(f"🌳 {config.repo_name.replace('-', ' ').title()} — New Worktree")
    print(f"   Worktree: {worktree_id}")
    print(f"   Path:     {worktree_path}")
    print()

    if args.dry_run:
        output.dry_run(f"Would fetch from {repo.remote}")
        output.dry_run(f"Would create worktree at {worktree_path} on branch {branch}")
        output.dry_run(f"Would write tracking YAML")
        output.dry_run(f"Would clone permissions")
        output.dry_run(f"Would add worktree path to trusted_folders")
        launch_cmd = _build_launch_cmd(config, args, worktree_path, profile=profile)
        merged_env = _build_env(profile)
        output.dry_run(f"Would launch: {' '.join(launch_cmd)}")
        if merged_env:
            output.dry_run(f"Would set env: {', '.join(f'{k}={v}' for k, v in merged_env.items())}")
        print()
        output.ok("Dry run complete — no changes made")
        _emit_plan({"action": "none", "exit_code": 0})
        return 0

    result = _create_worktree_core(
        config, profile=profile, no_mux=getattr(args, "no_mux", False),
    )
    _emit_plan({
        "action": "exec",
        **result["launch"],
    })
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# Worktree-ID inference — shared by finalize, post-exit, mark-complete
# ═══════════════════════════════════════════════════════════════════════════

def _infer_worktree_id(
    explicit: str | None,
    config: cfg.Config | None = None,
) -> str | None:
    """Return the worktree ID from an explicit arg, CWD, or env var.

    Resolution order:
      1. Explicit value passed on the CLI
      2. CWD under the configured ``worktree_root`` directory
      3. ``WORKTREE_ID`` environment variable (falls back to
         ``APERTURE_WORKTREE_ID`` for backward compat; last resort — may
         be stale in long-lived tmux servers or sub-agent contexts)

    Git branch is intentionally *not* used for identification — worktrees
    are permitted to switch to feature branches, so the branch name is
    not a reliable indicator of which worktree directory we're in.

    When CWD yields a worktree ID that disagrees with the env var, a
    warning is emitted.

    Returns None if no source yields a worktree ID.
    """
    if explicit:
        return explicit

    from_cwd = _infer_worktree_id_from_cwd(config)
    from_env = _env_get("WORKTREE_ID")

    # Pick the best source
    resolved = from_cwd or from_env

    # Cross-check: warn when CWD disagrees with the env var
    if from_env and resolved and resolved != from_env:
        output.warn(
            f"Ignoring WORKTREE_ID={from_env}; "
            f"working directory resolves to {resolved}."
        )
    elif resolved and resolved == from_env and not from_cwd:
        output.warn(
            f"Using WORKTREE_ID={from_env}; "
            f"could not verify from working directory."
        )

    return resolved


def _infer_worktree_id_from_cwd(
    config: cfg.Config | None = None,
) -> str | None:
    """Derive worktree ID from the current working directory.

    If CWD (or a parent) sits directly inside ``worktree_root``, the
    first path component under that root is the worktree ID.  Validated
    against the tracking directory to avoid false positives.
    """
    try:
        if config is None:
            config = cfg.load_config()
        wt_root = Path(config.default_repo.worktree_root).resolve()
    except Exception:
        return None

    cwd = Path.cwd().resolve()
    try:
        rel = cwd.relative_to(wt_root)
    except ValueError:
        return None

    if not rel.parts:
        return None  # CWD is exactly worktree_root

    candidate = rel.parts[0]

    # Validate: a tracking YAML should exist for this candidate
    yaml_path = cfg.tracking_dir() / f"{candidate}.yaml"
    if yaml_path.exists():
        return candidate

    # Even without a tracking file, if the directory exists under
    # worktree_root and has a .git entry it's a valid worktree
    wt_dir = wt_root / candidate
    if wt_dir.is_dir() and (wt_dir / ".git").exists():
        return candidate

    return None


def _resolve_worktree_id(raw_id: str) -> str:
    """Canonicalize a worktree ID, resolving short suffixes.

    If ``raw_id`` matches a tracking file directly, return as-is.
    Otherwise, search for tracking files whose stem ends with the
    given suffix.  Raises ``SystemExit`` on ambiguous or invalid IDs.
    """
    import re
    # Reject IDs with path-traversal or glob metacharacters
    if re.search(r'[/\\]|\.\.', raw_id):
        output.err(f"Invalid worktree ID: {raw_id}")
        raise SystemExit(1)

    tdir = cfg.tracking_dir()

    # Exact match — fast path
    if (tdir / f"{raw_id}.yaml").exists():
        return raw_id

    # Suffix match: iterate tracking files whose stems end with raw_id
    matches = [
        p.stem for p in tdir.glob("*.yaml")
        if p.stem.endswith(raw_id)
    ]

    if len(matches) == 1:
        return matches[0]

    if len(matches) > 1:
        short_list = ", ".join(sorted(m[-12:] for m in matches))
        output.err(
            f"Ambiguous short ID '{raw_id}' matches {len(matches)} "
            f"worktrees: {short_list}"
        )
        raise SystemExit(1)

    # No tracking match — return as-is (caller will fail on missing YAML)
    return raw_id


# ═══════════════════════════════════════════════════════════════════════════
# post-exit — finalization after Copilot exits
# ═══════════════════════════════════════════════════════════════════════════

def cmd_post_exit(args: argparse.Namespace) -> int:
    """Run post-exit checks on a worktree after Copilot exits. Idempotent."""
    config = cfg.load_config()
    worktree_id = _infer_worktree_id(args.worktree_id, config)
    if not worktree_id:
        output.err("Could not determine worktree ID. Pass it explicitly or run from inside a worktree.")
        return 1
    worktree_id = _resolve_worktree_id(worktree_id)

    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        output.warn(f"No tracking record for {worktree_id} — skipping post-exit.")
        return 0

    try:
        record = tracking.load_record(yaml_path)
    except Exception as e:
        output.err(f"Failed to load record {worktree_id}: {e}")
        return 1

    # Already finalized — nothing to do
    if record.status == "finalized":
        output.ok(f"Worktree {worktree_id} already finalized.")
        return 0

    return _post_exit_gate(record, config)


def _post_exit_gate(record: tracking.WorktreeRecord, config: cfg.Config) -> int:
    """Check post-exit state and trigger finalization if the session is complete.

    Returns 0 on success or skip, 1 on finalization failure.
    """
    worktree_id = record.worktree_id

    if record.status == "complete":
        print(f"Session {worktree_id} marked complete — starting finalization...")
        success = fin.finalize(worktree_id, config)
        if success:
            return 0
        output.err(
            f"Finalization failed for {worktree_id}. "
            f"Run 'agent-worktrees finalize' to retry."
        )
        return 1

    if record.status == "orphaned":
        output.warn(
            f"Session {worktree_id} is orphaned (previous finalization failed). "
            f"Run 'agent-worktrees finalize' to retry."
        )
        return 0

    # status == "active" — session wasn't marked complete
    print(
        f"Session {worktree_id} is still active (not marked complete). "
        f"Skipping finalization."
    )
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# finalize
# ═══════════════════════════════════════════════════════════════════════════

def cmd_finalize(args: argparse.Namespace) -> int:
    use_json = getattr(args, "json", False)
    if use_json:
        ctx = output.stdout_to_stderr()
        ctx.__enter__()
    else:
        ctx = None  # type: ignore[assignment]

    try:
        try:
            config = cfg.load_config(Path(args.config) if args.config else None)
        except Exception as e:
            if use_json:
                return _json_error(str(e))
            raise
        worktree_id = _infer_worktree_id(args.worktree_id, config)
        if not worktree_id:
            msg = "Could not determine worktree ID. Pass it explicitly or run from inside a worktree."
            if use_json:
                return _json_error(msg)
            output.err(msg)
            return 1
        worktree_id = _resolve_worktree_id(worktree_id)
        success = fin.finalize(worktree_id, config, dry_run=args.dry_run)

        if use_json:
            yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
            final_status = "finalized"
            if yaml_path.exists():
                try:
                    rec = tracking.load_record(yaml_path)
                    final_status = rec.status
                except Exception:
                    pass
            _json_output({
                "worktree_id": worktree_id,
                "success": success,
                "status": final_status,
            })

        return 0 if success else 1
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


# ═══════════════════════════════════════════════════════════════════════════
# mark-complete
# ═══════════════════════════════════════════════════════════════════════════

def cmd_mark_complete(args: argparse.Namespace) -> int:
    config = cfg.load_config()
    worktree_id = _infer_worktree_id(args.worktree_id, config)

    if not worktree_id:
        output.err("Could not determine worktree ID. Pass it explicitly or run from inside a worktree.")
        return 1
    worktree_id = _resolve_worktree_id(worktree_id)

    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"

    if not yaml_path.exists():
        output.warn(f"Tracking file not found at {yaml_path}")
        print("Creating minimal tracking file...")
        record = tracking.WorktreeRecord(
            worktree_id=worktree_id,
            branch=git_ops.get_current_branch("."),
            worktree_path=str(Path.cwd()),
            repo=cfg.project_name(),
            machine="",
            platform="",
            started_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            last_resumed_at=datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
            resume_count=0,
            title=args.title,
            status="active" if args.title_only else "complete",
            completed_at=None if args.title_only else datetime.now().strftime("%Y-%m-%dT%H:%M:%S"),
        )
        tracking.save_record(record, yaml_path)
    else:
        record = tracking.load_record(yaml_path)
        if args.title:
            record.title = args.title.replace("\n", " ").strip()
        if not args.title_only:
            tracking.update_status(record, "complete")
        else:
            tracking.save_record(record)

    if args.title_only:
        print(f"🏷️  Worktree {worktree_id} title updated: {args.title}")
        return 0

    msg = f"✅ Worktree {worktree_id} marked complete."
    if args.title:
        msg += f" Title: {args.title}"
    print(msg)

    # Attempt finalization immediately — rebase, merge, push.
    # The session is still running, so finalize() will skip worktree/branch
    # removal but will push content to the remote.  If finalization fails
    # (e.g. no network), revert to "active" so the worktree reappears in
    # the picker on next launch.
    print(f"Finalizing {worktree_id}...")
    success = fin.finalize(worktree_id, config)
    if not success:
        output.warn(
            "Finalization failed — reverting to active. "
            "Content is committed locally; finalize will be retried on next "
            "mark-complete or via 'agent-worktrees finalize'."
        )
        tracking.update_status(record, "active")

    return 0


# ═══════════════════════════════════════════════════════════════════════════
# status
# ═══════════════════════════════════════════════════════════════════════════

def cmd_status(args: argparse.Namespace) -> int:
    tracking_path = cfg.tracking_dir()

    records = tracking.list_records(tracking_path)
    if not records:
        if args.json:
            _json_output({"worktrees": []})
            return 0
        print("No tracked worktrees.")
        return 0

    config = cfg.load_config()
    repo = config.default_repo

    # Scan for live sessions to feed into classification
    all_paths = [r.worktree_path for r in records]
    sessions.scan_sessions(all_paths)  # populates session cache
    active_paths = _build_active_paths(records)

    results: list[dict] = []
    for rec in records:
        info = git_ops.classify_worktree(
            rec.worktree_path, rec.branch,
            fetch=True, remote=repo.remote, default_branch=repo.default_branch,
            active_paths=active_paths,
        )
        info = _apply_tracking_override(rec, info)
        result_entry = _worktree_to_dict(rec, state_info=info)
        # Add display helpers for table output
        short_id = rec.worktree_id[-4:] if len(rec.worktree_id) > 4 else rec.worktree_id
        result_entry["short_id"] = short_id
        display_title = rec.title if (rec.title and rec.title != "null") else (info.title or "(none)")
        result_entry["title"] = display_title
        results.append(result_entry)

    if args.json:
        _json_output({"worktrees": results})
        return 0

    # Table output
    STATE_COLORS = {
        "active": "36", "unused": "2", "completed": "32", "wip": "33",
        "dirty": "31", "gone": "31", "orphan": "35",
    }

    print()
    print(f"🌳 {config.repo_name.replace('-', ' ').title()} — Worktree Status")
    print()
    print(f"{'ID':<6} {'State':<11} {'Ahead':<7} {'Behind':<8} Title")
    print(f"{'─'*5:<6} {'─'*10:<11} {'─'*6:<7} {'─'*7:<8} {'─'*30}")

    for r in results:
        color = STATE_COLORS.get(r.get("state", ""), "0")
        state_str = f"\033[{color}m{r.get('state', ''):<11}\033[0m" if output._COLOR else f"{r.get('state', ''):<11}"
        print(f"{r['short_id']:<6} {state_str} {r.get('ahead', ''):<7} {r.get('behind', ''):<8} {r['title']}")

    # Summary
    unused_count = sum(1 for r in results if r.get("state") == "unused")
    completed_count = sum(1 for r in results if r.get("state") == "completed")
    cleanable = unused_count + completed_count

    print()
    if cleanable > 0:
        parts = []
        if completed_count:
            parts.append(f"{completed_count} completed")
        if unused_count:
            parts.append(f"{unused_count} unused")
        print(f"{cleanable} worktree(s) can be cleaned up ({', '.join(parts)}).")
    else:
        print("All worktrees are active.")

    return 0


# ═══════════════════════════════════════════════════════════════════════════
# list — lightweight inventory from tracking records
# ═══════════════════════════════════════════════════════════════════════════

def cmd_list(args: argparse.Namespace) -> int:
    """List worktrees from tracking records.

    Cheaper than ``status`` — no git fetch or classification.
    """
    tracking_path = cfg.tracking_dir()
    status_filter = None if args.tracking_status == "all" else args.tracking_status
    records = tracking.list_records(tracking_path, status_filter=status_filter)

    if args.json:
        worktrees = [_worktree_to_dict(rec) for rec in records]
        _json_output({"worktrees": worktrees})
        return 0

    if not records:
        print("No tracked worktrees.")
        return 0

    print()
    print(f"{'ID':<42} {'Status':<12} {'Platform':<8} Title")
    print(f"{'─'*41:<42} {'─'*11:<12} {'─'*7:<8} {'─'*30}")
    for rec in records:
        short_id = rec.worktree_id[-12:] if len(rec.worktree_id) > 12 else rec.worktree_id
        title = rec.title or "(none)"
        print(f"{short_id:<42} {rec.status:<12} {rec.platform:<8} {title}")

    print(f"\n{len(records)} worktree(s).")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# create — non-interactive worktree creation
# ═══════════════════════════════════════════════════════════════════════════

def cmd_create(args: argparse.Namespace) -> int:
    """Create a new worktree non-interactively.

    When ``--json`` is passed, emits a JSON envelope with the new
    worktree info and launch plan.  The caller is responsible for
    launching Copilot — this command returns the command info only.
    """
    with output.stdout_to_stderr():
        try:
            config = cfg.load_config()
            result = _create_worktree_core(
                config, no_mux=True,
            )
        except Exception as e:
            if args.json:
                return _json_error(str(e))
            output.err(str(e))
            return 1

    if args.json:
        _json_output(result)
        return 0

    wt = result["worktree"]
    print(f"✅ Created worktree: {wt['id']}")
    print(f"   Path:   {wt['path']}")
    print(f"   Branch: {wt['branch']}")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# cleanup
# ═══════════════════════════════════════════════════════════════════════════

def cmd_cleanup(args: argparse.Namespace) -> int:
    config = cfg.load_config()
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()

    records = tracking.list_records(tracking_path)
    if not records:
        print("No tracked sessions.")
        return 0

    to_clean: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
    skipped: list[tuple[tracking.WorktreeRecord, str]] = []
    unused_count = 0
    dirty_count = 0
    wip_count = 0

    print()
    print(f"🌳 {config.repo_name.replace('-', ' ').title()} — Worktree Sessions")
    print()
    print(f"{'Worktree ID':<50} {'State':<12} {'Age':<12} Path")
    print(f"{'─'*48:<50} {'─'*10:<12} {'─'*10:<12} {'─'*30}")

    # Fetch once for accurate classification
    git_ops.fetch(repo.remote, cwd=repo.anchor)
    upstream = f"{repo.remote}/{repo.default_branch}"

    # Scan for live Copilot sessions and mux sessions
    active_paths = _build_active_paths(records)

    for rec in records:
        if rec.status == "finalized":
            state_str = "completed"
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
        elif rec.worktree_path and Path(rec.worktree_path).exists():
            info = git_ops.classify_worktree(
                rec.worktree_path, rec.branch,
                fetch=False, remote=repo.remote, default_branch=repo.default_branch,
                active_paths=active_paths,
            )
            state_str = info.state.value
        else:
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.GONE)
            state_str = "gone"

        age = _age_str(rec.started_at)
        path_display = rec.worktree_path if Path(rec.worktree_path).exists() else "(gone)"

        # Annotate state with dirty indicator when relevant
        if info.dirty > 0 and info.state != git_ops.WorktreeState.DIRTY:
            state_display = f"{state_str} ({info.dirty}△)"
        else:
            state_display = state_str
        print(f"{rec.worktree_id:<50} {state_display:<12} {age:<12} {path_display}")

        # Determine if cleanable
        cleanable = False
        skip_reason = ""

        # Hard rule: never clean a worktree with a live session
        if info.state == git_ops.WorktreeState.ACTIVE:
            skip_reason = "active Copilot session in use"
        elif rec.status == "finalized":
            # Finalized records are safe unless a live session re-opened them
            norm = _normalize_path(rec.worktree_path) if rec.worktree_path else ""
            if norm in active_paths:
                skip_reason = "active Copilot session in use"
            else:
                cleanable = True
        elif info.state == git_ops.WorktreeState.COMPLETED:
            cleanable = True
        elif info.state == git_ops.WorktreeState.GONE:
            # Safety: verify branch content is on master before deleting
            if rec.branch and not git_ops.is_branch_merged(
                rec.branch, upstream, cwd=repo.anchor,
            ):
                skip_reason = "branch has unmerged commits (worktree dir missing)"
            else:
                cleanable = True
        elif info.state == git_ops.WorktreeState.UNUSED:
            unused_count += 1
            if args.include_unused:
                cleanable = True
        elif info.state == git_ops.WorktreeState.DIRTY:
            dirty_count += 1
        elif info.state == git_ops.WorktreeState.WIP:
            wip_count += 1

        if cleanable:
            to_clean.append((rec, info))
        elif skip_reason:
            skipped.append((rec, skip_reason))

    print()

    if skipped:
        for rec, reason in skipped:
            output.warn(f"Skipping {rec.worktree_id}: {reason}")
        print()

    if not to_clean and unused_count == 0 and dirty_count == 0 and wip_count == 0 and not skipped:
        print("Nothing to clean.")
        return 0

    if to_clean:
        print(f"{len(to_clean)} session(s) eligible for cleanup.")

    if not args.include_unused and unused_count > 0:
        print(f"{unused_count} unused worktree(s) preserved — no commits, no uncommitted changes (pass --include-unused to also clean).")

    if dirty_count > 0 or wip_count > 0:
        parts = []
        if dirty_count:
            parts.append(f"{dirty_count} with uncommitted changes")
        if wip_count:
            parts.append(f"{wip_count} with unmerged commits")
        output.warn(f"{' and '.join(parts)} — not eligible for cleanup.")

    if not args.clean or not to_clean:
        if to_clean:
            print("Run with --clean to remove them.")
        return 0

    # Acquire finalization lock to prevent races with post-exit finalization
    lock_path = Path(repo.worktree_root) / ".finalize.lock"
    lock = fin.FinalizeLock(lock_path)
    try:
        lock.acquire()
    except TimeoutError:
        output.err("Timed out waiting for finalization lock — another finalization in progress?")
        return 1

    failures = 0
    try:
        for rec, info in to_clean:
            print(f"Cleaning {rec.worktree_id} ({info.state.value})...")

            if rec.worktree_path and Path(rec.worktree_path).exists():
                if not git_ops.remove_worktree(repo.anchor, rec.worktree_path):
                    output.warn(f"Could not remove worktree via git — forcing directory removal.")
                wt_dir = Path(rec.worktree_path)
                if wt_dir.exists():
                    shutil.rmtree(wt_dir, ignore_errors=True)
                    if wt_dir.exists():
                        output.warn(f"Directory still present: {wt_dir}")
                        failures += 1

            if rec.branch:
                if not git_ops.delete_branch(rec.branch, cwd=repo.anchor, force=True):
                    output.warn(f"Could not delete branch {rec.branch}")
                    failures += 1

            # Clean up Copilot permissions and trusted_folders
            if rec.worktree_path:
                permissions.merge_permissions(repo.anchor, rec.worktree_path)
                permissions.remove_trusted_folder(rec.worktree_path)

            # Remove tracking YAML
            yaml_path = tracking_path / f"{rec.worktree_id}.yaml"
            yaml_path.unlink(missing_ok=True)

            # Kill any associated tmux session
            sessions.kill_tmux_session(rec.worktree_id)

        # Prune stale worktree entries
        git_ops.prune_worktrees(cwd=repo.anchor)
    finally:
        lock.release()

    print()
    if failures:
        output.warn(f"Cleaned {len(to_clean)} session(s) with {failures} warning(s).")
    else:
        output.ok(f"Cleaned {len(to_clean)} session(s).")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# validate
# ═══════════════════════════════════════════════════════════════════════════

def cmd_validate(args: argparse.Namespace) -> int:
    worktree_path = args.worktree_path or str(Path.cwd())
    files = args.files if args.files else None

    # Load config to get validate_paths for the repo
    validate_paths: list[str] | None = None
    try:
        config = cfg.load_config()
        repo = config.default_repo
        if repo.validate_paths:
            validate_paths = repo.validate_paths
    except Exception:
        pass  # Fall back to legacy paths

    failures = val.validate_files(
        worktree_path, files,
        default_branch=args.default_branch,
        dry_run=args.dry_run,
        validate_paths=validate_paths,
    )
    return 1 if failures else 0


# ═══════════════════════════════════════════════════════════════════════════
# Argument parser
# ═══════════════════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════════════════
# install / uninstall / update / install-status
# ═══════════════════════════════════════════════════════════════════════════

def _validate_machine_registry(
    repo_dir: Path, machine: str,
) -> cfg.MachineEntry | None:
    """Look up *machine* in machines.yaml.  Returns the entry or prints
    an error and returns None."""
    try:
        registry = cfg.load_machines_yaml(repo_dir)
    except FileNotFoundError:
        output.err(f"Machine registry not found at {repo_dir / 'machines.yaml'}")
        output.info("Create machines.yaml in the repo root with an entry for this machine.")
        return None
    except ValueError as exc:
        output.err(str(exc))
        return None

    if machine not in registry:
        output.err(f"Machine '{machine}' not found in machines.yaml")
        output.info("Add an entry for this machine and retry:")
        output.info(f"  machines:")
        output.info(f"    {machine}:")
        output.info(f"      display_name: {machine.title()}")
        output.info(f'      environment: "<OS and version>"')
        output.info(f"      hostname_prefixes:")
        output.info(f"        - {machine}")
        return None

    return registry[machine]


def _deploy_copilot_instructions(
    proj_dir: Path, entry: cfg.MachineEntry,
) -> None:
    """Write or update machine instruction files from the registry.

    Deploys into the COPILOT_CUSTOM_INSTRUCTIONS_DIRS directory:

    - ``.github/instructions/machine.instructions.md`` — the filename
      pattern Copilot CLI auto-discovers from custom instruction
      directories (``<dir>/.github/instructions/**/*.instructions.md``).
    - ``AGENTS.md`` — discovered as a nested AGENTS.md in custom dirs.

    Both files have identical content.
    """
    content = cfg.render_copilot_instructions(entry)

    # Primary: .github/instructions/*.instructions.md (auto-injected)
    instr_dir = proj_dir / ".github" / "instructions"
    instr_dir.mkdir(parents=True, exist_ok=True)
    instr_path = instr_dir / "machine.instructions.md"
    if instr_path.exists() and instr_path.read_text() == content:
        output.skipped("machine.instructions.md already in sync")
    else:
        instr_path.write_text(content)
        output.changed(f"machine.instructions.md → {instr_path}")

    # Fallback: AGENTS.md (nested discovery)
    agents_path = proj_dir / "AGENTS.md"
    if agents_path.exists() and agents_path.read_text() == content:
        output.skipped("AGENTS.md already in sync")
    else:
        agents_path.write_text(content)
        output.changed(f"AGENTS.md → {agents_path}")

    # Clean up legacy files from previous deploy strategies
    for legacy_name in ("copilot-instructions.md",):
        legacy = proj_dir / legacy_name
        if legacy.exists():
            legacy.unlink()
            output.changed(f"removed legacy {legacy_name}")


def cmd_install(args: argparse.Namespace) -> int:
    """Deploy the worktree manager shared runtime + register current project."""
    project = cfg.project_name()
    output.header("Installing Agent Worktrees")

    # Prereqs
    missing = inst.check_prereqs()
    if missing:
        output.err(f"Missing prerequisites: {', '.join(missing)}")
        return 1

    # Determine repo dir (we must be running from the repo)
    repo_dir = _find_repo_dir()
    if not repo_dir:
        output.err("Cannot determine repo root. Run from within the source repo.")
        return 1

    machine = args.machine or cfg.detect_machine(repo_dir)
    plat = cfg.detect_platform()
    print(f"  Machine:  {machine}")
    print(f"  Platform: {plat}")
    print(f"  Project:  {project}")
    print(f"  Repo:     {repo_dir}")

    # Validate machine is registered
    machine_entry = _validate_machine_registry(repo_dir, machine)
    if machine_entry is None:
        return 1

    # Create shared runtime directories
    runtime_dir = cfg._home() / ".agent-worktrees"
    for d in [runtime_dir, runtime_dir / "bin", inst.local_bin()]:
        d.mkdir(parents=True, exist_ok=True)

    # Create per-project directories
    proj_dir = cfg.project_dir(project)
    for d in [proj_dir, proj_dir / "worktrees"]:
        d.mkdir(parents=True, exist_ok=True)

    # Deploy config (per-project)
    config_path = proj_dir / "config.yaml"
    if not config_path.exists() or args.force:
        _write_config(config_path, repo_dir, machine, plat, project)
    else:
        output.skipped(f"Config exists at {config_path} (use --force to overwrite)")

    # Deploy copilot-instructions.md from machine registry
    _deploy_copilot_instructions(proj_dir, machine_entry)

    # Deploy Python package (shared runtime)
    if not inst.deploy_package(repo_dir):
        return 1

    # Create venv (shared runtime)
    if not inst.create_venv():
        return 1

    # Deploy wrappers (shared runtime)
    if not inst.deploy_wrappers(repo_dir):
        return 1

    # Deploy project-specific binstubs
    if not inst.deploy_binstubs(repo_dir, project=project):
        return 1

    # Run post-install hook (project-specific, e.g. icon deployment)
    try:
        config = cfg.load_config(config_path)
        hook = config.default_repo.post_install_hook.get(plat)
        if hook:
            cmd = [
                s.replace("{repo_dir}", str(repo_dir))
                 .replace("{runtime_dir}", str(runtime_dir))
                for s in hook
            ]
            result = subprocess.run(cmd, cwd=str(repo_dir))
            if result.returncode == 0:
                output.ok("Post-install hook completed")
            else:
                output.warn(f"Post-install hook exited with code {result.returncode}")
    except Exception:
        pass  # hook is optional

    # Deploy manifest (shared runtime)
    inst.write_deploy_manifest(repo_dir, machine)

    print()
    output.ok("Installation complete")
    print(f"  Runtime:   {runtime_dir}")
    print(f"  Project:   {proj_dir}")
    print(f"  Usage:     {project}")
    return 0


def cmd_register(args: argparse.Namespace) -> int:
    """Register a project with the worktree manager (create config + binstub)."""
    project = args.project_name
    output.header(f"Registering project: {project}")

    if not cfg._PROJECT_NAME_RE.match(project):
        output.err(f"Invalid project name: {project!r}")
        return 1

    # Determine repo dir
    if getattr(args, "repo_dir", None):
        repo_dir = Path(args.repo_dir).resolve()
        if not (repo_dir / ".git").exists() and not (repo_dir / ".git").is_file():
            output.err(f"Not a git repository: {repo_dir}")
            return 1
    else:
        repo_dir = _find_repo_dir()
        if not repo_dir:
            repo_dir = Path.cwd()
            output.warn(f"Using current directory as repo root: {repo_dir}")

    machine = args.machine or cfg.detect_machine(repo_dir)
    plat = cfg.detect_platform()

    # Auto-detect default branch if not specified
    default_branch = getattr(args, "default_branch", None) or None
    if not default_branch:
        try:
            r = subprocess.run(
                ["git", "-C", str(repo_dir), "symbolic-ref",
                 "refs/remotes/origin/HEAD"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0:
                default_branch = r.stdout.strip().split("/")[-1]
        except Exception:
            pass
    if not default_branch:
        default_branch = "master"

    print(f"  Repo:     {repo_dir}")
    print(f"  Branch:   {default_branch}")
    print(f"  Machine:  {machine}")
    print(f"  Platform: {plat}")

    # Machine registry is optional — external repos may not have machines.yaml
    machine_entry: cfg.MachineEntry | None = None
    machines_yaml = repo_dir / "machines.yaml"
    if machines_yaml.exists():
        machine_entry = _validate_machine_registry(repo_dir, machine)
        if machine_entry is None:
            return 1

    # Create project directory
    proj_dir = cfg.project_dir(project)
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "worktrees").mkdir(exist_ok=True)

    # Write config
    config_path = proj_dir / "config.yaml"
    if not config_path.exists() or args.force:
        _write_config(config_path, repo_dir, machine, plat, project, default_branch)
    else:
        output.skipped(f"Config exists at {config_path} (use --force to overwrite)")

    # Deploy copilot-instructions.md from machine registry
    if machine_entry is not None:
        _deploy_copilot_instructions(proj_dir, machine_entry)

    # Generate binstub
    if not inst.deploy_binstubs(repo_dir, project=project):
        return 1

    output.ok(f"Project '{project}' registered")
    print(f"  Config:  {config_path}")
    print(f"  Usage:   {project}")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    output.header("Uninstalling Agent Worktrees")

    # Remove binstub
    lb = inst.local_bin()
    project = cfg.project_name()
    if platform.system() == "Windows":
        bs = lb / f"{project}.cmd"
    else:
        bs = lb / project
    if bs.exists():
        bs.unlink()
        output.changed(f"Removed binstub: {bs}")

    # Remove wrappers
    bd = inst.bin_dir()
    for name in ("launch-session.cmd", "launch-session.ps1", "launch-session.sh"):
        p = bd / name
        if p.exists():
            p.unlink()
    output.changed(f"Removed wrappers from {bd}")

    # Remove venv
    venv = inst.venv_dir()
    if venv.exists():
        shutil.rmtree(venv, ignore_errors=True)
        output.changed(f"Removed venv: {venv}")

    # Remove lib
    lib = inst.lib_dir()
    if lib.exists():
        shutil.rmtree(lib, ignore_errors=True)
        output.changed(f"Removed package: {lib}")

    if args.remove_config:
        base = inst.install_dir()
        if base.exists():
            shutil.rmtree(base, ignore_errors=True)
            output.changed(f"Removed {base} (config + session metadata)")
    else:
        manifest = inst.install_dir() / "deploy-manifest.json"
        if manifest.exists():
            manifest.unlink()
        output.skipped("Config and session metadata preserved")
        print("    Use --remove-config to delete everything")

    output.ok("Uninstall complete")
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    project = cfg.project_name()
    output.header("Updating Agent Worktrees")

    repo_dir = _find_repo_dir()
    if not repo_dir:
        output.err("Cannot determine repo root.")
        return 1

    if not inst.lib_dir().exists():
        output.err("Not installed — run 'install' first")
        return 1

    # Use machine from installed config (not re-detecting)
    try:
        config = cfg.load_config()
        machine = config.machine
    except Exception:
        machine = cfg.detect_machine(repo_dir)

    if not inst.deploy_package(repo_dir):
        return 1

    # Venv handling: health-check first, only rebuild when necessary.
    # Running from the managed venv locks python.exe on Windows, so
    # full recreation would fail.  Prefer upgrading deps in-place.
    recreate_venv = getattr(args, "recreate_venv", False)
    if recreate_venv:
        if inst.is_running_from_managed_venv():
            output.err("Cannot recreate venv while running from it.")
            output.err("Run from system Python or another venv.")
            return 1
        if not inst.create_venv():
            return 1
    elif not inst.check_venv_health():
        # Venv is broken — try upgrading deps first
        output.warn("Venv health check failed — attempting repair")
        if not inst.upgrade_venv_deps():
            if inst.is_running_from_managed_venv():
                output.err("Venv is unhealthy but cannot recreate while running from it.")
                output.err("Run: python -m agent_worktrees update --recreate-venv")
                output.err("(using system Python, not the managed venv)")
                return 1
            if not inst.create_venv():
                return 1
    else:
        inst.upgrade_venv_deps()

    if not inst.deploy_wrappers(repo_dir):
        return 1

    if not inst.deploy_binstubs(repo_dir, project=project):
        return 1

    # Refresh copilot-instructions.md from machine registry
    try:
        registry = cfg.load_machines_yaml(repo_dir)
        if machine in registry:
            _deploy_copilot_instructions(cfg.project_dir(project), registry[machine])
    except (FileNotFoundError, ValueError):
        pass  # machines.yaml may not exist for all projects

    inst.write_deploy_manifest(repo_dir, machine)

    output.ok(f"Update complete (source: {repo_dir})")
    return 0


def cmd_deploy_instructions(args: argparse.Namespace) -> int:
    """Deploy machine.instructions.md + AGENTS.md from machines.yaml."""
    project = cfg.project_name()
    repo_dir = _find_repo_dir()
    if not repo_dir:
        output.err("Cannot determine repo root.")
        return 1

    machine = args.machine
    if not machine:
        try:
            config = cfg.load_config()
            machine = config.machine
        except Exception:
            machine = cfg.detect_machine(repo_dir)

    try:
        registry = cfg.load_machines_yaml(repo_dir)
    except (FileNotFoundError, ValueError) as exc:
        output.err(f"Cannot load machines.yaml: {exc}")
        return 1

    if machine not in registry:
        output.err(f"Machine '{machine}' not found in machines.yaml")
        return 1

    proj_dir = cfg.project_dir(project)
    proj_dir.mkdir(parents=True, exist_ok=True)
    _deploy_copilot_instructions(proj_dir, registry[machine])
    return 0


_GET_KEYS: dict[str, str] = {
    "repo-dir":      "Anchor repo directory",
    "worktree-dir":  "Worktree root directory",
    "src-dir":       "Source root (parent of repos)",
    "config-dir":    "Per-project config directory (~/.{project})",
    "machine":       "Machine name from config",
    "platform":      "Platform (win/wsl/linux)",
    "project":       "Project name",
}


def cmd_get(args: argparse.Namespace) -> int:
    """Query project paths and config values — machine-readable output."""
    key: str = args.key

    if key == "keys":
        for k, desc in _GET_KEYS.items():
            print(f"{k:16s}  {desc}")
        return 0

    try:
        config = cfg.load_config()
    except Exception as e:
        output.err(f"Cannot load config: {e}")
        return 1

    repo = config.default_repo
    values = {
        "repo-dir":     repo.anchor,
        "worktree-dir": repo.worktree_root,
        "src-dir":      config.srcroot,
        "config-dir":   str(cfg.project_dir()),
        "machine":      config.machine,
        "platform":     config.platform,
        "project":      config.repo_name,
    }

    if key not in values:
        output.err(f"Unknown key: {key!r}. Use 'get keys' to list available keys.")
        return 1

    print(values[key])
    return 0


def cmd_install_status(args: argparse.Namespace) -> int:
    inst.show_install_status()
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# services — discovery, staleness, and update
# ═══════════════════════════════════════════════════════════════════════════


def _resolve_environment(config: cfg.Config) -> str:
    """Build the environment key from config (e.g. ``lambda-core-wsl``)."""
    plat = config.platform
    if plat in ("wsl", "windows"):
        return f"{config.machine}-{plat}"
    return config.machine


def _services_usage() -> None:
    """Print services subcommand usage."""
    project = cfg.project_name()
    print(f"Usage: {project} services <command>")
    print()
    print("Discovery:")
    print("  list [--json]                      List services for this environment")
    print("  status [--json]                    Show service deployment staleness")
    print()
    print("Single service:")
    print("  <name> [action] [flags...]         Run action via service installer")
    print("                                     (default action: status)")
    print()
    print("Batch:")
    print("  --all <action> [flags...]          Run action across all services")
    print("    --force                          Include up-to-date services")
    print("    --dry-run                        Show what would run")
    print()
    print("Examples:")
    print(f"  {project} services list")
    print(f"  {project} services permanent-record status")
    print(f"  {project} services permanent-record install")
    print(f"  {project} services --all update")
    print(f"  {project} services --all install --dry-run")
    print()
    print("Legacy:")
    print("  check-stale <install_dir> <repo>   Machine-readable staleness check")


def _installer_cmd(installer: Path, args: list[str]) -> list[str] | None:
    """Build the command to run an installer with the given args."""
    if installer.suffix == ".sh":
        return ["bash", str(installer)] + args
    if installer.suffix == ".ps1":
        return ["pwsh", "-File", str(installer)] + args
    return None


def _service_is_installed(service: svc.ServiceInfo) -> bool:
    """Check if a service's install directory exists on disk."""
    if not service.install_dir:
        return False
    return Path(service.install_dir).exists()


def cmd_services_dispatch(argv: list[str]) -> int:
    """Route services subcommands — built-in aggregates or passthrough."""
    if not argv:
        _services_usage()
        return 1

    sub = argv[0]
    rest = argv[1:]

    # Built-in aggregate commands
    if sub == "list":
        return _cmd_services_list(json_output="--json" in rest)
    if sub == "status":
        return _cmd_services_status(json_output="--json" in rest)
    if sub == "check-stale":
        if len(rest) < 2:
            output.err("Usage: services check-stale <install_dir> <repo_dir>")
            return 1
        return _cmd_services_check_stale(rest[0], rest[1])
    if sub in ("--help", "-h"):
        _services_usage()
        return 0

    # Batch: --all <action> [flags...]
    if sub == "--all":
        if not rest:
            output.err("Usage: services --all <action> [flags...]")
            return 1
        return _cmd_services_batch(rest[0], rest[1:])

    # Passthrough: <name> [action] [flags...]
    return _cmd_service_passthrough(sub, rest)


def _cmd_services_list(json_output: bool = False) -> int:
    """List services deployable to this environment."""
    repo_dir = _find_repo_dir()
    if not repo_dir:
        output.err("Cannot find repo root")
        return 1

    try:
        config = cfg.load_config()
    except Exception as e:
        output.err(f"Cannot load config: {e}")
        return 1

    env = _resolve_environment(config)
    services = svc.discover_services(repo_dir, env, service_paths=config.default_repo.service_paths or None)

    if json_output:
        data = [
            {
                "name": s.name,
                "display_name": s.display_name,
                "type": s.service_type,
                "deployment_type": s.deployment_type,
                "install_dir": s.install_dir,
                "installer": s.installer_path,
                "source_dir": s.source_dir,
            }
            for s in services
        ]
        print(json.dumps(data, indent=2))
        return 0

    output.header(f"Services ({env})")
    if not services:
        output.skipped("No services found for this environment")
        return 0

    for s in services:
        label = s.display_name or s.name
        detail = f"{s.service_type}, {s.deployment_type}"
        print(f"  {label:35s}  {output._c('dim', detail)}")

    print()
    output.info(f"{len(services)} service(s)")
    return 0


def _cmd_services_status(json_output: bool = False) -> int:
    """Show service status with staleness info."""
    repo_dir = _find_repo_dir()
    if not repo_dir:
        output.err("Cannot find repo root")
        return 1

    try:
        config = cfg.load_config()
    except Exception as e:
        output.err(f"Cannot load config: {e}")
        return 1

    env = _resolve_environment(config)
    services = svc.discover_services(repo_dir, env, service_paths=config.default_repo.service_paths or None)

    if json_output:
        data = []
        for s in services:
            st = svc.get_service_status(s, repo_dir)
            data.append({
                "name": st.service.name,
                "display_name": st.service.display_name,
                "staleness": st.staleness,
                "deployed_commit": st.deployed_commit,
                "deployed_at": st.deployed_at,
                "deployed_branch": st.deployed_branch,
                "dirty": st.dirty,
                "install_dir": st.service.install_dir,
                "source_paths": st.source_paths,
            })
        print(json.dumps(data, indent=2))
        return 0

    output.header(f"Service Status ({env})")
    if not services:
        output.skipped("No services found for this environment")
        return 0

    for s in services:
        st = svc.get_service_status(s, repo_dir)
        label = s.display_name or s.name

        if st.staleness == "current":
            commit_short = (st.deployed_commit or "?")[:10]
            output.ok(f"{label:35s}  current @ {commit_short}")
        elif st.staleness.startswith("stale:"):
            count = st.staleness.split(":")[1]
            commit_short = (st.deployed_commit or "?")[:10]
            output.changed(f"{label:35s}  {count} commit(s) behind @ {commit_short}")
        else:
            output.skipped(f"{label:35s}  unknown (no manifest)")

        if st.dirty:
            output.warn(f"{'':35s}  deployed from dirty tree")

    print()
    return 0


def _cmd_services_check_stale(install_dir_str: str, repo_dir_str: str) -> int:
    """Machine-readable staleness check (for shell integration).

    Outputs: ``current``, ``stale:N``, or ``unknown`` to stdout.
    Drop-in replacement for ``test_service_stale`` in service-utils.sh.
    """
    install_dir = Path(install_dir_str)
    repo_dir = Path(repo_dir_str)
    manifest_path = install_dir / "deploy-manifest.json"
    result = svc.check_staleness(manifest_path, repo_dir)
    print(result)
    return 0


# Actions that deploy code and benefit from pulling latest before running
_DEPLOY_ACTIONS = {"install", "update", "copy"}


def _ensure_repo_current(repo_dir: Path, config: "cfg.Config") -> None:
    """Pull latest commits into the anchor repo before deploying.

    When services are deployed from the anchor (the main clone, not a
    worktree), the anchor may be behind origin if commits were pushed
    from a worktree via ``git push origin HEAD:master``.  A fast-forward
    merge keeps the anchor in sync so installers copy the latest code.

    Worktrees are left alone — they track their own branch.
    """
    # Worktrees have a .git *file*; the anchor has a .git *directory*
    git_path = repo_dir / ".git"
    if not git_path.is_dir():
        return  # worktree — nothing to do

    remote = config.default_repo.remote or "origin"
    branch = config.default_repo.default_branch or "master"

    output.info(f"Syncing anchor repo ({remote}/{branch})…")
    try:
        git_ops.fetch(remote, cwd=repo_dir)
        result = git_ops.git(
            "merge", "--ff-only", f"{remote}/{branch}",
            cwd=repo_dir, check=False,
        )
        if result.returncode != 0:
            output.warn(
                "Anchor has local commits — fast-forward failed. "
                "Deploying from current anchor HEAD."
            )
    except Exception as exc:
        output.warn(f"Could not sync anchor: {exc}")


def _cmd_service_passthrough(name: str, action_args: list[str]) -> int:
    """Forward an action to a specific service's installer."""
    repo_dir = _find_repo_dir()
    if not repo_dir:
        output.err("Cannot find repo root")
        return 1

    try:
        config = cfg.load_config()
    except Exception as e:
        output.err(f"Cannot load config: {e}")
        return 1

    # Determine the action before discovery — the first positional arg
    action = action_args[0] if action_args else "status"
    if not action_args:
        action_args = ["status"]

    # Pull latest into anchor before deploying code
    if action in _DEPLOY_ACTIONS:
        _ensure_repo_current(repo_dir, config)

    env = _resolve_environment(config)
    services = svc.discover_services(repo_dir, env, service_paths=config.default_repo.service_paths or None)

    match = [s for s in services if s.name == name]
    if not match:
        output.err(f"Service {name!r} not found in {env}")
        if services:
            output.info("Available: " + ", ".join(s.name for s in services))
        return 1

    service = match[0]
    if not service.installer_path:
        output.err(f"{name} has no installer")
        return 1

    installer = repo_dir / service.installer_path
    if not installer.exists():
        output.err(f"Installer not found: {installer}")
        return 1

    cmd = _installer_cmd(installer, action_args)
    if not cmd:
        output.err(f"Unknown installer type: {installer.suffix}")
        return 1

    label = service.display_name or service.name
    output.header(f"{label} → {' '.join(action_args)}")

    # Stream output directly — the installer owns the terminal
    result = subprocess.run(cmd, cwd=str(repo_dir))
    return result.returncode


def _cmd_services_batch(action: str, flags: list[str]) -> int:
    """Run an action across all services for this environment."""
    repo_dir = _find_repo_dir()
    if not repo_dir:
        output.err("Cannot find repo root")
        return 1

    try:
        config = cfg.load_config()
    except Exception as e:
        output.err(f"Cannot load config: {e}")
        return 1

    # Pull latest into anchor before deploying code
    if action in _DEPLOY_ACTIONS:
        _ensure_repo_current(repo_dir, config)

    env = _resolve_environment(config)
    services = svc.discover_services(repo_dir, env, service_paths=config.default_repo.service_paths or None)

    force = "--force" in flags
    dry_run = "--dry-run" in flags
    pass_flags = [f for f in flags if f not in ("--force", "--dry-run")]

    output.header(f"Services {action} ({env})")

    if not services:
        output.skipped("No services found for this environment")
        return 0

    errors = 0
    skipped = 0
    completed = 0

    for s in services:
        label = s.display_name or s.name
        st = svc.get_service_status(s, repo_dir)
        is_installed = _service_is_installed(s)

        # Smart filtering for install/update (other actions run on all)
        if not force:
            if action == "install" and is_installed:
                skipped += 1
                continue
            if action == "update":
                if st.staleness == "current":
                    skipped += 1
                    continue
                if not is_installed:
                    output.warn(f"{label} — not installed, skipping update")
                    skipped += 1
                    continue

        if not s.installer_path:
            output.skipped(f"{label} — no installer")
            skipped += 1
            continue

        installer = repo_dir / s.installer_path
        if not installer.exists():
            output.err(f"{label} — installer missing at {installer}")
            errors += 1
            continue

        cmd_args = [action] + pass_flags
        cmd = _installer_cmd(installer, cmd_args)
        if not cmd:
            output.err(f"{label} — unknown installer type: {installer.suffix}")
            errors += 1
            continue

        if dry_run:
            output.dry_run(f"{label} → {installer.name} {' '.join(cmd_args)}")
            continue

        print()
        output.changed(f"{label} → {action}")

        result = subprocess.run(cmd, cwd=str(repo_dir))
        if result.returncode == 0:
            output.ok(f"{label} done")
            completed += 1
        else:
            output.err(f"{label} failed (rc={result.returncode})")
            errors += 1

    print()
    if completed:
        output.ok(f"{completed} service(s) completed")
    if skipped:
        output.info(f"{skipped} service(s) skipped")
    if errors:
        output.err(f"{errors} service(s) failed")
    return 1 if errors else 0


# ═══════════════════════════════════════════════════════════════════════════
# pre-launch — two-pass declarative self-update protocol
# ═══════════════════════════════════════════════════════════════════════════

# Bootstrap services that must be current before launching a session.
_BOOTSTRAP_SERVICES = ("agent-worktrees", "vault")


def cmd_pre_launch(args: argparse.Namespace) -> int:
    """Check bootstrap service staleness and return a JSON action plan.

    Returns JSON to stdout:
      {"action": "continue"}  — all bootstrap services are current
      {"action": "self-update", "updates": [...]}  — services need updating

    The shell wrapper interprets this JSON, runs the update commands,
    and re-invokes pre-launch (max 1 retry).
    """
    repo_dir = _find_repo_dir()
    if not repo_dir:
        # Can't determine staleness — proceed anyway
        print(json.dumps({"action": "continue", "reason": "no-repo"}))
        return 0

    try:
        config = cfg.load_config()
    except Exception:
        print(json.dumps({"action": "continue", "reason": "no-config"}))
        return 0

    env = _resolve_environment(config)
    all_services = svc.discover_services(repo_dir, env, service_paths=config.default_repo.service_paths or None)

    # Filter to bootstrap services only
    bootstrap = {s.name: s for s in all_services if s.name in _BOOTSTRAP_SERVICES}

    # Direct fallback for agent-worktrees: always deployed at a known
    # location, but may be missing from service.yaml for this environment
    if "agent-worktrees" not in bootstrap:
        wm_dir = cfg.install_dir()
        wm_manifest = wm_dir / "deploy-manifest.json"
        if wm_manifest.exists():
            staleness = svc.check_staleness(wm_manifest, repo_dir)
            if staleness != "current":
                # Find the installer — check manifest's installer_path first,
                # then known repo locations (current and legacy).
                installer = None
                manifest_data = svc._read_manifest(wm_manifest)
                search_dirs = [Path("plugins/agent-worktrees/scripts")]
                if manifest_data and manifest_data.get("installer_path"):
                    manifest_installer = repo_dir / manifest_data["installer_path"]
                    if manifest_installer.exists():
                        installer = manifest_installer
                if installer is None:
                    for sdir in search_dirs:
                        for iname in svc._preferred_installer_order():
                            candidate = repo_dir / sdir / iname
                            if candidate.exists():
                                installer = candidate
                                break
                        if installer:
                            break
                result = None
                if installer is not None:
                    result = _build_installer_argv(installer)
                if result is not None:
                    cmd, cmd_argv = result
                    updates: list[dict[str, str]] = [{
                        "service": "agent-worktrees",
                        "staleness": staleness,
                        "command": cmd,
                        "argv": cmd_argv,
                    }]
                    # Check discovered bootstrap services too
                    for s in bootstrap.values():
                        _append_update_if_stale(s, repo_dir, updates)
                    print(json.dumps({"action": "self-update", "updates": updates}))
                    return 0

    updates = []
    for s in bootstrap.values():
        _append_update_if_stale(s, repo_dir, updates)

    if updates:
        print(json.dumps({"action": "self-update", "updates": updates}))
    else:
        print(json.dumps({"action": "continue"}))

    return 0


def _build_installer_argv(installer: Path) -> tuple[str, list[str]] | None:
    """Build a (display_cmd, argv) pair for running an installer.

    On Windows, ``.sh`` installers must be invoked via ``wsl bash`` with the
    path converted from Windows to WSL format (``wslpath -u``).  Raw Windows
    backslash paths passed directly to ``bash`` get mangled (backslashes are
    stripped, producing ``C:DataSrc...`` instead of ``C:\\Data\\Src\\...``).
    """
    if installer.suffix == ".sh":
        if platform.system() == "Windows":
            # Convert Windows path → WSL path for bash
            try:
                r = subprocess.run(
                    ["wsl", "wslpath", "-u", str(installer)],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    wsl_path = r.stdout.strip()
                else:
                    # Fallback: manual drive-letter conversion
                    wsl_path = _win_to_wsl_path(str(installer))
            except Exception:
                wsl_path = _win_to_wsl_path(str(installer))
            cmd = f"wsl bash {wsl_path} update"
            argv = ["wsl", "bash", wsl_path, "update"]
        else:
            cmd = f"bash {installer} update"
            argv = ["bash", str(installer), "update"]
        return cmd, argv
    elif installer.suffix == ".ps1":
        cmd = f"pwsh -File {installer} update"
        argv = ["pwsh", "-File", str(installer), "update"]
        return cmd, argv
    return None


def _win_to_wsl_path(win_path: str) -> str:
    """Best-effort Windows → WSL path conversion without calling wslpath."""
    # C:\Data\Src\... → /mnt/c/Data/Src/...
    path = win_path.replace("\\", "/")
    if len(path) >= 2 and path[1] == ":":
        drive = path[0].lower()
        path = f"/mnt/{drive}{path[2:]}"
    return path


def _append_update_if_stale(
    service: svc.ServiceInfo,
    repo_dir: Path,
    updates: list[dict[str, str]],
) -> None:
    """Check staleness and append an update entry if needed."""
    st = svc.get_service_status(service, repo_dir)
    if st.staleness == "current":
        return
    if not service.installer_path:
        return
    installer = repo_dir / service.installer_path
    if not installer.exists():
        return
    result = _build_installer_argv(installer)
    if not result:
        return
    cmd, argv = result
    updates.append({
        "service": service.name,
        "staleness": st.staleness,
        "command": cmd,
        "argv": argv,
    })


def _find_repo_dir() -> Path | None:
    """Find the repo root for the current project.

    Priority order (most specific → least specific):
      1. Running script location (navigate up to git root)
      2. WORKTREE_REPO env var (falls back to APERTURE_REPO for compat)
      3. cwd git root (via git rev-parse)
      4. Config anchor (last resort — may be stale)
    """

    # 1. Running script location — walk up from __file__ to find .git
    here = Path(__file__).resolve().parent
    candidate = here
    for _ in range(8):  # limit traversal depth
        if (candidate / ".git").exists() or (candidate / ".git").is_file():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            break
        candidate = parent

    # 2. WORKTREE_REPO env (set by setup.sh during active sessions)
    env_repo = _env_get("WORKTREE_REPO")
    if env_repo:
        env_path = Path(env_repo)
        if env_path.is_dir():
            return env_path

    # 3. git rev-parse to find repo root of cwd
    try:
        r = subprocess.run(
            ["git", "-C", str(Path.cwd()), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return Path(r.stdout.strip())
    except Exception:
        pass

    # 4. Config anchor (last resort — may deploy stale code if anchor
    #    hasn't been updated, but better than failing entirely)
    try:
        config = cfg.load_config()
        anchor = Path(config.default_repo.anchor)
        if anchor.exists():
            return anchor
    except Exception:
        pass

    return None


def _write_config(
    path: Path, repo_dir: Path, machine: str, plat: str,
    project: str, default_branch: str = "master",
) -> None:
    """Write the project config YAML."""
    src_root = repo_dir.parent
    wt_root = src_root / ".worktrees" / project

    content = f"""# ~/.{project}/config.yaml
# Machine-local configuration for worktree session management.

repo_name: {project}
srcroot: {src_root}
machine: {machine}
platform: {plat}

repos:
  {project}:
    anchor: {repo_dir}
    worktree_root: {wt_root}
    default_branch: {default_branch}
    remote: origin
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    output.changed(f"Written config: {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-worktrees",
        description="Worktree session manager",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # resolve (emit JSON launch plan, then exit — shell handles execution)
    p = sub.add_parser("resolve", help="Resolve launch plan as JSON (for shell wrappers)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--recovery", action="store_true")
    p.add_argument("--no-resume", action="store_true",
                   help="Don't auto-resume the last Copilot session")
    p.add_argument("--no-mux", action="store_true",
                   help="Bypass tmux/psmux multiplexer (launch directly)")
    p.add_argument("--json", action="store_true",
                   help="Non-interactive JSON mode (requires --worktree-id)")
    p.add_argument("--worktree-id", default=None,
                   help="Worktree ID to resolve (required with --json)")
    p.add_argument("--profile", help="Copilot backend profile name (skips Tab toggle)")
    p.add_argument("copilot_args", nargs="*", default=[])

    # post-exit (run post-exit checks after Copilot exits)
    p = sub.add_parser("post-exit", help="Post-exit worktree checks (idempotent)")
    p.add_argument("worktree_id", nargs="?", default=None)

    # finalize
    p = sub.add_parser("finalize", help="Finalize a completed worktree")
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true",
                   help="JSON output mode (stdout is JSON only)")
    p.add_argument("--config", default=None)

    # mark-complete
    p = sub.add_parser("mark-complete", help="Mark a worktree as complete")
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--title", default=None)
    p.add_argument("--title-only", action="store_true")

    # status
    p = sub.add_parser("status", help="Show worktree git status")
    p.add_argument("--json", action="store_true")

    # list (lightweight inventory from tracking records)
    p = sub.add_parser("list", help="List worktrees from tracking records")
    p.add_argument("--json", action="store_true",
                   help="JSON output mode (stdout is JSON only)")
    p.add_argument("--tracking-status", default="all",
                   choices=["active", "complete", "finalized", "orphaned", "all"],
                   help="Filter by tracking status (default: all)")

    # create (non-interactive worktree creation)
    p = sub.add_parser("create", help="Create a new worktree non-interactively")
    p.add_argument("--json", action="store_true",
                   help="JSON output mode (stdout is JSON only)")

    # cleanup
    p = sub.add_parser("cleanup", help="List and clean orphaned worktrees")
    p.add_argument("--clean", action="store_true")
    p.add_argument("--include-unused", action="store_true")
    p.add_argument("--max-age-days", type=int, default=7)

    # validate
    p = sub.add_parser("validate", help="Validate core infrastructure files")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--files", nargs="*", default=None)
    p.add_argument("--worktree-path", default=None)
    p.add_argument("--default-branch", default="origin/master")

    # install
    p = sub.add_parser("install", help="Deploy worktree manager (shared runtime + project)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--machine", default=None)

    # register (new project)
    p = sub.add_parser("register", help="Register a new project with the worktree manager")
    p.add_argument("project_name", help="Project name (e.g. 'my-project')")
    p.add_argument("--repo-dir", default=None,
                   help="Path to the repository (defaults to cwd detection)")
    p.add_argument("--default-branch", default=None,
                   help="Default branch (auto-detected from origin/HEAD if omitted)")
    p.add_argument("--force", action="store_true")
    p.add_argument("--machine", default=None)

    # uninstall
    p = sub.add_parser("uninstall", help="Remove worktree manager")
    p.add_argument("--remove-config", action="store_true")

    # update
    p = sub.add_parser("update", help="Re-deploy from repo")
    p.add_argument("--recreate-venv", action="store_true",
                   help="Force full venv recreation (cannot run from managed venv)")

    # install-status
    sub.add_parser("install-status", help="Show installation status")

    # deploy-instructions
    p = sub.add_parser("deploy-instructions",
                       help="Deploy machine.instructions.md from machines.yaml")
    p.add_argument("--machine", default=None,
                   help="Machine name (auto-detected from config if omitted)")

    # get (query project paths and config values)
    p = sub.add_parser("get", help="Query project paths and config values")
    p.add_argument("key", help="Key to query (use 'keys' to list available keys)")

    # services — dispatched pre-argparse (see cmd_services_dispatch)
    # Stub entry for --help visibility only
    sub.add_parser("services", help="Service discovery and management (run 'services' for usage)")

    # pre-launch (two-pass self-update protocol)
    sub.add_parser("pre-launch", help="Check bootstrap staleness (JSON output)")

    # dev (repo development tooling)
    sp = sub.add_parser("dev", help="Dev venv and test runner")
    sp.add_argument("dev_action", nargs="?", default="status",
                    choices=["setup", "test", "status"],
                    help="Action: setup, test, or status")

    # handoff (manage handoff prompt state for auto-relaunch)
    sp = sub.add_parser("handoff", help="Manage handoff prompt state on a worktree")
    sp.add_argument("handoff_sub", choices=["set", "consume"],
                    help="set: write handoff path; consume: read and clear (JSON)")
    sp.add_argument("worktree_id", help="Worktree ID")
    sp.add_argument("prompt_path", nargs="?", default=None,
                    help="Path to handoff prompt file (required for 'set')")

    return parser


def cmd_dev(args: argparse.Namespace) -> int:
    """Dispatch to tools/dev/setup.{sh,ps1} for dev venv management."""
    repo_dir = _find_repo_dir()
    if not repo_dir:
        output.err("Cannot determine repo root.")
        return 1

    dev_action = args.dev_action if hasattr(args, "dev_action") else "status"

    if sys.platform == "win32":
        script = repo_dir / "tools" / "dev" / "setup.ps1"
        if not script.exists():
            output.err(f"Dev script not found: {script}")
            return 1
        import subprocess
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(script), dev_action],
            cwd=str(repo_dir),
        )
        return result.returncode
    else:
        script = repo_dir / "tools" / "dev" / "setup.sh"
        if not script.exists():
            output.err(f"Dev script not found: {script}")
            return 1
        os.execvp("bash", ["bash", str(script), dev_action])
        return 1  # unreachable


def cmd_handoff(args: argparse.Namespace) -> int:
    """Manage handoff prompt state on a worktree record.

    Subcommands:
        set <worktree_id> <prompt_path>  — set the handoff_prompt field
        consume <worktree_id>            — read and clear; prints JSON
    """
    sub = getattr(args, "handoff_sub", None)
    wt_id = getattr(args, "worktree_id", None)

    if sub == "set":
        prompt_path = getattr(args, "prompt_path", None)
        if not wt_id or not prompt_path:
            output.err("Usage: handoff set <worktree_id> <prompt_path>")
            return 1
        try:
            tracking.set_handoff(wt_id, prompt_path)
        except FileNotFoundError as exc:
            output.err(str(exc))
            return 1
        return 0

    if sub == "consume":
        if not wt_id:
            output.err("Usage: handoff consume <worktree_id>")
            return 1
        prompt_path = tracking.consume_handoff(wt_id)
        _json_output({"prompt_path": prompt_path})
        return 0

    output.err("Usage: handoff {set|consume} <worktree_id> [<prompt_path>]")
    return 1


COMMAND_MAP = {
    "resolve": cmd_resolve,
    "post-exit": cmd_post_exit,
    "finalize": cmd_finalize,
    "mark-complete": cmd_mark_complete,
    "status": cmd_status,
    "list": cmd_list,
    "create": cmd_create,
    "cleanup": cmd_cleanup,
    "validate": cmd_validate,
    "install": cmd_install,
    "register": cmd_register,
    "uninstall": cmd_uninstall,
    "update": cmd_update,
    "install-status": cmd_install_status,
    "deploy-instructions": cmd_deploy_instructions,
    "get": cmd_get,
    "pre-launch": cmd_pre_launch,
    "dev": cmd_dev,
    "handoff": cmd_handoff,
}


def main(argv: list[str] | None = None) -> int:
    args_list = argv if argv is not None else sys.argv[1:]

    # ── Raw pre-dispatch ──────────────────────────────────────────────
    # Handle compatibility aliases and the default "launch" action
    # BEFORE argparse, which can't represent both CLI and launch modes.

    # Strip `agent-worktrees` prefix (SSH compat:
    #   `<project> agent-worktrees cleanup` → `cleanup`)
    if args_list and args_list[0] == "agent-worktrees":
        args_list = args_list[1:]

    # No args → default launch
    if not args_list:
        return cmd_launch([])

    # --help / -h → show argparse help (not launch fallthrough)
    if args_list[0] in ("--help", "-h"):
        parser = build_parser()
        parser.print_help()
        return 0

    # Services uses manual dispatch for passthrough support —
    # argparse can't handle "unknown subcommand = service name".
    if args_list[0] == "services":
        try:
            return cmd_services_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # First arg is a known subcommand → parse normally
    if args_list[0] in COMMAND_MAP:
        parser = build_parser()
        args = parser.parse_args(args_list)
        handler = COMMAND_MAP.get(args.command)
        if not handler:
            parser.print_help()
            return 1
        try:
            return handler(args)
        except FileNotFoundError as e:
            output.err(str(e))
            return 1
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # Anything else (flags like --recovery, --no-update, or unknown) →
    # default launch with passthrough
    return cmd_launch(args_list)


if __name__ == "__main__":
    sys.exit(main())
