"""CLI entry point -- subcommand dispatcher for agent-worktrees.

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
    agent-worktrees repos list [--type project|repo] [--json]
    agent-worktrees repos find <name>
    agent-worktrees repos srcroot [--set PATH] [--platform P]
    agent-worktrees pre-launch
    agent-worktrees reconcile-plugins [--machine M]

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
from datetime import datetime
from pathlib import Path

import yaml

from . import activity, git_ops, output, permissions, pr_ops, sessions, tracking
from . import config as cfg
from . import finalize as fin
from . import installer as inst
from . import services as svc
from . import validate as val
from .picker import ItemKind, MenuItem, pick

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
# Default launch -- exec into launch-session.sh when no subcommand given
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
        # On Windows, use cmd.exe to run the .cmd launcher.
        # Use Popen + wait so we can catch KeyboardInterrupt (Ctrl+C)
        # and still let the child finish its cleanup. launch-session.ps1
        # has a try/finally that checks for handoff state and runs
        # post-exit finalization -- we must not kill it prematurely.
        proc = subprocess.Popen(
            ["cmd.exe", "/c", str(launch_script), *passthrough],
        )
        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            # Ctrl+C was sent to the entire console process group.
            # The child (cmd.exe -> pwsh -> copilot) received it too.
            # Wait for the child to finish its cleanup (handoff check,
            # post-exit finalization) rather than killing it.
            try:
                rc = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                rc = 130  # 128 + SIGINT(2)
        sys.exit(rc)
    else:
        os.execvp("bash", ["bash", str(launch_script), *passthrough])
    return 1  # unreachable -- os.execvp replaces process


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


def _epoch_or_zero(iso: str) -> float:
    """Parse an ISO timestamp to epoch seconds for sorting (0.0 on failure).

    Handles both the naive-local ``started_at`` form and the UTC ``Z``
    form written to ``workspace.yaml``.  ``datetime.timestamp()`` treats a
    naive value as local time, which matches how ``started_at`` is written.
    """
    if not iso:
        return 0.0
    try:
        s = iso.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return 0.0


def _activity_age_str(iso: str) -> str | None:
    """Human-readable age from a session ``updated_at`` (UTC, may end in Z).

    Unlike ``_age_str`` (which expects naive local timestamps), this
    tolerates the ``Z`` suffix and tz-aware values written by the Copilot
    CLI to ``workspace.yaml``.  Returns None when *iso* is empty/unparseable.
    """
    if not iso:
        return None
    try:
        s = iso.strip()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        now = datetime.now(dt.tzinfo) if dt.tzinfo is not None else datetime.now()
        minutes = int((now - dt).total_seconds() / 60)
        if minutes < 0:
            minutes = 0
        if minutes >= 1440:
            return f"{minutes // 1440}d ago"
        if minutes >= 60:
            return f"{minutes // 60}h ago"
        return f"{minutes}m ago"
    except Exception:
        return None


def _normalize_path(p: str) -> str:
    """Normalize for comparison -- strip trailing separators."""
    return p.rstrip("/\\")


def _build_active_paths(
    records: list[tracking.WorktreeRecord],
    session_ctx: sessions.SessionContext | None = None,
) -> set[str]:
    """Build set of normalized paths with live sessions (lock files OR mux sessions)."""
    if session_ctx is None:
        session_ctx = sessions.scan_sessions_fast(records)
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
    records ``status: finalized`` -- trust it.
    """
    if info.state == git_ops.WorktreeState.UNUSED and rec.status == "finalized":
        return dataclasses.replace(info, state=git_ops.WorktreeState.COMPLETED)
    return info


# ═══════════════════════════════════════════════════════════════════════════
# resolve -- JSON launch plan (Python exits before Copilot starts)
# ═══════════════════════════════════════════════════════════════════════════

def _emit_plan(plan: dict) -> None:
    """Write the JSON launch plan to the real stdout (not the swapped one).

    For exec actions, injects COPILOT_CUSTOM_INSTRUCTIONS_DIRS pointing
    to the project dir so machine+repo-specific instructions are loaded
    without polluting other repos on the same machine.
    """
    if plan.get("action") == "exec":
        env = plan.setdefault("env", {})
        env.setdefault(
            "COPILOT_CUSTOM_INSTRUCTIONS_DIRS", str(cfg.project_dir())
        )
    sys.__stdout__.write(json.dumps(plan) + "\n")
    sys.__stdout__.flush()


# ═══════════════════════════════════════════════════════════════════════════
# JSON output helpers -- shared by all --json modes
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
    mux_info: sessions.MuxInfo | None = None,
    session_ctx: sessions.SessionContext | None = None,
) -> dict:
    """Serialize a WorktreeRecord to a JSON-friendly dict.

    If ``state_info`` is provided, includes git-derived classification
    (state, ahead, behind, dirty) alongside the tracking status.

    If ``mux_info`` is provided, includes multiplexer session status
    (existence and attached client count).

    If ``session_ctx`` is provided, includes session-derived metrics
    (turn_count, session_count, latest_summary).
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
    if rec.kind == "system":
        d["kind"] = rec.kind
        if rec.owner:
            d["owner"] = rec.owner
    if state_info is not None:
        d["state"] = state_info.state.value
        d["ahead"] = state_info.ahead
        d["behind"] = state_info.behind
        d["dirty"] = state_info.dirty
        if state_info.branch_drift and state_info.current_branch:
            d["current_branch"] = state_info.current_branch
            d["branch_drift"] = True
    if mux_info is not None:
        d["mux_session"] = mux_info.exists
        d["mux_clients"] = mux_info.clients
        d["mux_attached"] = mux_info.attached
    if session_ctx is not None:
        norm = _normalize_path(rec.worktree_path)
        d["turn_count"] = session_ctx.turn_count.get(norm, 0)
        d["session_count"] = session_ctx.session_count.get(norm, 0)
    return d


def _create_worktree_core(
    config: cfg.Config,
    *,
    profile: cfg.CopilotProfile | None = None,
    no_mux: bool = False,
    kind: tracking.WorktreeKind = "session",
    owner: str | None = None,
    name: str | None = None,
) -> dict:
    """Create a new worktree and return a dict with worktree info + launch plan.

    Performs the side-effects (fetch, git worktree add, tracking YAML,
    permissions) but does NOT launch copilot.  Returns a dict suitable
    for JSON serialization.

    ``kind="system"`` marks the worktree as daemon-owned (hidden from the
    Picker, exempt from routine cleanup); ``owner``/``name`` label it for the
    System-menu browse view.

    Raises ``RuntimeError`` on failure.
    """
    repo = config.default_repo
    plat = cfg.detect_platform()
    plat_short = "win" if plat == "windows" else plat

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = secrets.token_hex(2)
    if kind == "system":
        # Recognizable id for daemon worktrees: sys-<name>-<ts>-<suffix>.
        slug = _slugify(name or owner or "daemon")
        worktree_id = f"sys-{slug}-{timestamp}-{suffix}"
    else:
        worktree_id = f"{config.machine}-{plat_short}-{timestamp}-{suffix}"
    branch = f"worktree/{worktree_id}"
    worktree_path = str(Path(repo.worktree_root) / worktree_id)

    # Ensure root exists
    Path(repo.worktree_root).mkdir(parents=True, exist_ok=True)

    # Fetch (best-effort) and pick a start point that actually resolves --
    # a repo with no remote or no fetched default branch falls back to the
    # local default branch or HEAD instead of failing on <remote>/<branch>.
    print(f"Fetching latest from {repo.remote}...", file=sys.stderr)
    git_ops.git("fetch", repo.remote, "--quiet", cwd=repo.anchor, check=False)

    start_point = git_ops.resolve_start_point(
        repo.remote, repo.default_branch, cwd=repo.anchor
    )
    if start_point != f"{repo.remote}/{repo.default_branch}":
        print(
            f"Note: '{repo.remote}/{repo.default_branch}' not found; "
            f"branching from '{start_point}' instead.",
            file=sys.stderr,
        )

    print(f"Creating worktree on branch {branch}...", file=sys.stderr)
    git_ops.create_worktree(repo.anchor, worktree_path, branch, start_point)

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
        kind=kind,
        owner=owner,
    )

    # Clone permissions
    if permissions.clone_permissions(repo.anchor, worktree_path):
        print("Copied Copilot permissions to worktree path.", file=sys.stderr)

    activity.log_event(
        "worktree_created",
        worktree_id=worktree_id,
        branch=branch,
    )

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
        # Legacy fallback -- repo-specific setup script, then default.
        # Always resolve from the anchor repo so that worktrees pinned
        # to an older commit still pick up the latest setup script
        # (the anchor is fetched before every launch).
        anchor = repo.anchor
        if platform.system() == "Windows":
            setup_path = str(Path(anchor) / "tools" / "setup" / "setup.ps1")
            if not Path(setup_path).is_file():
                setup_path = str(inst.install_dir() / "scripts" / "default-setup.ps1")
            cmd = [
                "pwsh.exe", "-NoProfile", "-NoLogo", "-File",
                setup_path, "-Machine", config.machine,
            ]
            if recovery:
                cmd.append("-Recovery")
        else:
            setup_path = str(Path(anchor) / "tools" / "setup" / "setup.sh")
            if not Path(setup_path).is_file():
                setup_path = str(inst.install_dir() / "scripts" / "default-setup.sh")
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

    With ``--base``, resolves for the anchor repo directly (no picker, no
    worktree).  Used by agent-bridge to launch ACP agents with credentials.
    ``--base`` implies ``--no-mux`` and ``--no-resume``.

    With ``--new``, skips the interactive picker and creates a new
    worktree.  Used by agent-bridge for non-interactive SSH sessions.
    ``--new`` implies ``--no-mux``.

    When stdin is not a TTY and no non-interactive flag is set
    (``--json``, ``--base``, ``--new``), resolve errors out instead of
    running the picker.  Use ``--new`` to create a worktree
    non-interactively, or ``--json --worktree-id <id>`` to resume one.
    """
    use_json = getattr(args, "json", False)
    use_base = getattr(args, "base", False)
    use_new = getattr(args, "new_worktree", False) or getattr(args, "auto", False)

    if use_json:
        args.no_mux = True
        # Validate required args before any I/O
        wt_id = getattr(args, "worktree_id", None)
        if wt_id and use_new:
            return _json_error("--worktree-id and --new are mutually exclusive")
        if not wt_id and not use_new:
            return _json_error("--json requires --worktree-id or --new")

    if use_base:
        args.no_mux = True
        args.no_resume = True

    if use_new:
        args.no_mux = True

    with output.stdout_to_stderr():
        # Base-repo (no-worktree) projects resolve against the anchor directly,
        # regardless of --json/--new/--worktree-id. Configured via
        # repos.<name>.base_repo in the user-local ~/.<project>/config.yaml
        # overlay, so repos that can't support worktrees (e.g. an enlistment
        # monorepo) can still back an agent-bridge ACP agent without writing any
        # config into the repo. Any config/lookup failure falls through to the
        # normal worktree flow unchanged.
        try:
            _base_cfg = cfg.load_config()
            _is_base_repo = _base_cfg.default_repo.base_repo
        except Exception:
            _base_cfg, _is_base_repo = None, False
        if _is_base_repo and _base_cfg is not None:
            base_profile = _resolve_profile(_base_cfg, args)
            return _resolve_base_repo(_base_cfg, args, profile=base_profile)

        if use_base:
            try:
                config = cfg.load_config()
            except Exception as e:
                return _json_error(str(e))

            repo = config.default_repo
            work_dir = repo.anchor
            launch_cmd = _build_launch_cmd(config, args, work_dir)
            env = _build_env(None)

            _emit_plan({
                "action": "exec",
                "work_dir": work_dir,
                "cmd": launch_cmd,
                "env": env,
                "post_exit": False,
                "no_mux": True,
            })
            return 0

        if use_json:
            try:
                config = cfg.load_config()
            except Exception as e:
                return _json_error(str(e))

            if use_new:
                # --json --new: create a new worktree, return JSON plan
                profile = _resolve_profile(config, args)
                try:
                    result = _create_worktree_core(
                        config, profile=profile, no_mux=True,
                    )
                except RuntimeError as e:
                    return _json_error(str(e))
                _json_output(result)
                return 0

            wt_id = _resolve_worktree_id(wt_id)  # type: ignore[possibly-undefined]
            yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
            if not yaml_path.exists():
                return _json_error(f"Worktree not found: {wt_id}")
            record = tracking.load_record(yaml_path)
            tracking.mark_resumed(record)

            activity.log_event(
                "worktree_resumed",
                worktree_id=record.worktree_id,
                branch=record.branch,
                resume_count=record.resume_count,
            )

            launch_cmd = _build_launch_cmd(config, args, record.worktree_path)
            env = _build_env(None)

            # Auto-resume session
            no_resume = getattr(args, "no_resume", False)
            if not no_resume:
                last_session = sessions.find_latest_session_id_fast(
                    record.worktree_path, record.sessions,
                )
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

        # Non-interactive: require explicit --new to create a worktree.
        # Without a TTY the picker can't run, so error out with guidance.
        if not use_new and not sys.stdin.isatty():
            output.err("No TTY detected and no worktree specified.")
            output.err("Use --new to create a new worktree,")
            output.err("or --json --worktree-id <id> to resume an existing one.")
            output.err("Run 'agent-worktrees list' to see available worktrees.")
            return 1

        # Non-interactive resume by worktree ID (used by agent-bridge SSH
        # for session roll -- resume existing worktree without creating new).
        wt_id_noninteractive = getattr(args, "worktree_id", None)
        if wt_id_noninteractive:
            wt_id_noninteractive = _resolve_worktree_id(wt_id_noninteractive)
            yaml_path = cfg.tracking_dir() / f"{wt_id_noninteractive}.yaml"
            if not yaml_path.exists():
                output.err(f"Worktree not found: {wt_id_noninteractive}")
                return 1
            record = tracking.load_record(yaml_path)
            args.no_mux = True
            profile = _resolve_profile(config, args)
            return _resolve_resume(record, config, args, profile=profile)

        if use_new:
            profile = _resolve_profile(config, args)
            return _resolve_new(config, args, profile=profile)

        # --machine <remote> flag: skip picker entirely, emit SSH handoff
        requested_machine = getattr(args, "machine", None)
        if requested_machine and requested_machine != config.machine:
            rc = _try_machine_handoff(config, requested_machine)
            if rc is not None:
                return rc

        tracking_path = cfg.tracking_dir()
        tracking_path.mkdir(parents=True, exist_ok=True)
        current_platform = cfg.detect_platform()

        # Picker loop -- re-enters after system menu actions
        while True:

            # Load active worktrees (include "complete" -- these are worktrees
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

            # Include pushed worktrees whose directories still exist.
            # "pushed" is a transient finalization *condition* (content is on
            # upstream) -- NOT a terminal/completed state.  The worktree may
            # still have a live session and remains resumable, so it must
            # appear in the picker.  Session-aware classification surfaces it
            # as ACTIVE when a live session is detected; otherwise it falls
            # into the completed bucket like any other fully-upstream tree.
            pushed_records = tracking.list_records(
                tracking_path, status_filter="pushed", platform_filter=current_platform,
            )
            pushed_still_present = [
                r for r in pushed_records if Path(r.worktree_path).exists()
            ]
            records = records + pushed_still_present

            records = [
                r for r in records
                if Path(r.worktree_path).exists()
                and (Path(r.worktree_path) / ".git").exists()
                and r.kind != "system"  # daemon-owned; hidden from the Picker
            ]

            # Scan for live Copilot sessions and mux sessions
            session_ctx = sessions.scan_sessions_fast(records)
            active_paths = _build_active_paths(records, session_ctx)

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

            # Sort every bucket by most-recent activity first: prefer the
            # latest session's updated_at, falling back to the worktree's
            # started_at.  Descending so the freshest worktrees lead.
            def _bucket_sort_key(
                pair: tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo],
                session_ctx: sessions.SessionContext = session_ctx,
            ) -> float:
                rec, _ = pair
                norm = _normalize_path(rec.worktree_path)
                iso = session_ctx.last_activity.get(norm) or rec.started_at or ""
                return _epoch_or_zero(iso)

            for _bucket in (active_wts, recent_wts, unused_wts, completed_wts):
                _bucket.sort(key=_bucket_sort_key, reverse=True)

            # Build picker menu
            menu_items: list[MenuItem] = []

            def _wt_label(
                rec: tracking.WorktreeRecord,
                info: git_ops.WorktreeStateInfo,
                icon: str,
                session_ctx: sessions.SessionContext = session_ctx,
            ) -> str:
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

                # Inline sync status vs the default branch: ↑ahead / ↓behind.
                # Surfaces stale worktrees (↓N) at a glance so they can be
                # updated before resuming.  Counts reflect the last fetch.
                sync_tag = ""
                if info.ahead and info.behind:
                    sync_tag = f" ↑{info.ahead}↓{info.behind}"
                elif info.behind:
                    sync_tag = f" ↓{info.behind}"
                elif info.ahead:
                    sync_tag = f" ↑{info.ahead}"

                state_tag = (
                    f" [{info.state.value}]"
                    if info.state in (
                        git_ops.WorktreeState.UNUSED,
                        git_ops.WorktreeState.COMPLETED,
                    )
                    else ""
                )
                short_id = rec.worktree_id[-4:] if len(rec.worktree_id) > 4 else rec.worktree_id
                return f"{icon} …{short_id}  ({age}{resume}){tag}{drift_tag}{sync_tag}{state_tag}"

            def _wt_subtitle(
                rec: tracking.WorktreeRecord,
                info: git_ops.WorktreeStateInfo,
                session_ctx: sessions.SessionContext = session_ctx,
            ) -> str | None:
                """Resolve the best available title + live metadata for a worktree.

                Metadata (turn count, context-window %, last-activity age)
                is appended in parentheses, e.g.
                ``Fix the picker (12 turns · 43% ctx · 5m ago)``.
                """
                norm = _normalize_path(rec.worktree_path)
                turns = session_ctx.turn_count.get(norm, 0)
                pct = session_ctx.context_pct.get(norm)
                age = _activity_age_str(session_ctx.last_activity.get(norm, ""))

                meta: list[str] = []
                if turns > 0:
                    meta.append(f"{turns} turn{'s' if turns != 1 else ''}")
                if pct is not None:
                    meta.append(f"{pct}% ctx")
                if age:
                    meta.append(age)
                meta_tag = f" ({' · '.join(meta)})" if meta else ""

                title = ""
                if rec.title and rec.title != "null":
                    title = rec.title
                elif norm in session_ctx.latest_summary:
                    title = session_ctx.latest_summary[norm]
                elif info.title:
                    title = info.title
                if title:
                    return " ".join(title.split()) + meta_tag
                # Last resort: lead with session count so it isn't blank
                count = session_ctx.session_count.get(norm, 0)
                if count > 0:
                    parts = [f"{count} session{'s' if count != 1 else ''}"]
                    parts.extend(meta)
                    return f"({' · '.join(parts)})"
                return meta_tag.strip() or None

            for rec, info in active_wts:
                menu_items.append(MenuItem(
                    label=_wt_label(rec, info, "🟢"),
                    subtitle=_wt_subtitle(rec, info),
                    kind=ItemKind.NORMAL, value=("worktree", rec),
                ))

            if active_wts:
                menu_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))

            new_idx = len(menu_items)
            menu_items.append(
                MenuItem(label="✨ New worktree", kind=ItemKind.ACTION, value=("new", None))
            )

            # "Other machines" sub-menu entry (only if remotes exist)
            remote_machines = _load_remote_machines(config)
            if remote_machines:
                menu_items.append(MenuItem(
                    label="🖥 Other machines  ▸",
                    kind=ItemKind.ACTION,
                    value=("machines", None),
                ))

            menu_items.append(
                MenuItem(
                    label="📂 Base repo (no worktree)",
                    kind=ItemKind.ACTION, value=("base", None),
                )
            )

            if recent_wts:
                menu_items.append(
                    MenuItem(label="─── recent ─────────────────────", kind=ItemKind.SEPARATOR)
                )
            for rec, info in recent_wts:
                menu_items.append(MenuItem(
                    label=_wt_label(rec, info, "🌳"),
                    subtitle=_wt_subtitle(rec, info),
                    kind=ItemKind.NORMAL, value=("worktree", rec),
                ))

            if unused_wts:
                menu_items.append(
                    MenuItem(label="─── unused ─────────────────────", kind=ItemKind.SEPARATOR)
                )
                for rec, info in unused_wts:
                    menu_items.append(MenuItem(
                        label=_wt_label(rec, info, "⬜"),
                        subtitle=_wt_subtitle(rec, info),
                        kind=ItemKind.DIMMED, value=("worktree", rec),
                    ))

            if completed_wts:
                menu_items.append(
                    MenuItem(label="─── completed ──────────────────", kind=ItemKind.SEPARATOR)
                )
                for rec, info in completed_wts:
                    menu_items.append(MenuItem(
                        label=_wt_label(rec, info, "✅"),
                        subtitle=_wt_subtitle(rec, info),
                        kind=ItemKind.DIMMED, value=("worktree", rec),
                    ))

            # System menu item
            menu_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
            menu_items.append(
                MenuItem(label="⚙ System menu", kind=ItemKind.ACTION, value=("system", None))
            )

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
                title=f"🌳 {config.repo_name.replace('-', ' ').title()} -- Worktree Picker",
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

            # --- Remote machine SSH handoff ---
            if action == "remote":
                entry = value  # type: ignore[assignment]
                ssh_alias = _resolve_ssh_alias(entry)
                project = cfg.project_name()
                print(f"   Connecting to {entry.display_name} via {ssh_alias}...")
                _emit_plan({
                    "action": "remote",
                    "ssh_alias": ssh_alias,
                    "remote_command": project,
                    "machine": entry.key,
                    "display_name": entry.display_name,
                })
                return 0

            # --- Other machines sub-menu ---
            if action == "machines":
                result_machine = _run_machine_menu(config)
                if result_machine is not None:
                    return result_machine
                continue  # back to main picker

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
        MenuItem(label="⬆ Update stale worktrees", kind=ItemKind.ACTION, value="update"),
        MenuItem(label="📊 Worktree status", kind=ItemKind.ACTION, value="status"),
        MenuItem(label="🛠 System worktrees (daemon-owned)", kind=ItemKind.ACTION,
                 value="system-worktrees"),
        MenuItem(label="", kind=ItemKind.SEPARATOR),
        MenuItem(label="↩ Back to picker", kind=ItemKind.ACTION, value="back"),
    ]

    result = pick(
        system_items,
        title=f"⚙ {config.repo_name.replace('-', ' ').title()} -- System Menu",
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

    if action == "update":
        return _system_update(config)

    if action == "status":
        return _system_status(config)

    if action == "system-worktrees":
        return _system_worktrees_browse(config)

    return None


def _run_machine_menu(config: cfg.Config) -> int | None:
    """Show the remote machines sub-menu.

    Each SSH environment on each remote machine gets its own entry
    (e.g., Borealis Windows and Borealis WSL are separate choices).

    Returns an exit code if a remote machine was selected and the plan
    was emitted, or None to return to the main picker.
    """
    remote_machines = _load_remote_machines(config)
    if not remote_machines:
        return None

    machine_items: list[MenuItem] = []
    # Track (machine_entry, ssh_env) for each item
    machine_values: list[tuple[cfg.MachineEntry, cfg.SSHEnvironment]] = []

    for entry, envs in remote_machines:
        if len(envs) == 1:
            # Single environment -- show machine name only
            ssh_env = envs[0]
            subtitle = f"{entry.environment} -- {entry.role}" if entry.role else entry.environment
            machine_items.append(MenuItem(
                label=f"🖥 {entry.display_name}",
                subtitle=subtitle,
                kind=ItemKind.NORMAL,
                value=len(machine_values),
            ))
            machine_values.append((entry, ssh_env))
        else:
            # Multiple environments -- one entry per SSH env
            for ssh_env in envs:
                env_label = ssh_env.name.upper() if ssh_env.name else ssh_env.alias
                shell_tag = f" ({ssh_env.shell})" if ssh_env.shell else ""
                machine_items.append(MenuItem(
                    label=f"🖥 {entry.display_name} ({env_label})",
                    subtitle=(
                        f"{ssh_env.alias}{shell_tag} -- {entry.role}"
                        if entry.role else ssh_env.alias + shell_tag
                    ),
                    kind=ItemKind.NORMAL,
                    value=len(machine_values),
                ))
                machine_values.append((entry, ssh_env))

    machine_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
    machine_items.append(MenuItem(
        label="↩ Back to picker",
        kind=ItemKind.ACTION,
        value=-1,
    ))

    result = pick(
        machine_items,
        title=f"🖥 {config.repo_name.replace('-', ' ').title()} -- Other Machines",
        subtitle="Use ↑↓, Enter to connect, Esc back",
        default=0,
    )

    if result.selected < 0:
        return None  # Esc -- back to picker

    val = machine_items[result.selected].value
    if val == -1:
        return None  # "Back" item

    entry, ssh_env = machine_values[val]  # type: ignore[index]
    project = cfg.project_name()
    print(f"   Connecting to {entry.display_name} via {ssh_env.alias}...")
    _emit_plan({
        "action": "remote",
        "ssh_alias": ssh_env.alias,
        "remote_command": project,
        "machine": entry.key,
        "display_name": entry.display_name,
    })
    return 0


def _system_cleanup(config: cfg.Config) -> int | None:
    """Compact cleanup flow for the system menu -- picker-style UX."""
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    records = tracking.list_records(tracking_path)

    if not records:
        _system_pause("No tracked worktrees.")
        return None

    # Exclude daemon-owned system worktrees; they have their own browse/
    # force-remove flow and must never be swept by routine cleanup.
    records = [r for r in records if r.kind != "system"]
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
        if rec.worktree_path and Path(rec.worktree_path).exists():
            info = git_ops.classify_worktree(
                rec.worktree_path, rec.branch,
                fetch=False, remote=repo.remote, default_branch=repo.default_branch,
                active_paths=active_paths,
            )
            info = _apply_tracking_override(rec, info)
        elif rec.status == "finalized":
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
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
        _system_pause("Nothing to clean -- all worktrees are active or have unmerged work.")
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
        title="🧹 Cleanup -- select action",
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


def _system_update(config: cfg.Config) -> int | None:
    """Fast-forward stale worktrees to the default branch (FF-only).

    Fetches once, then offers a single-worktree update or an "update all
    eligible" batch.  Only clean worktrees that are strictly behind with no
    local commits are eligible; dirty/ahead/diverged worktrees are never
    touched and never fast-forwarded.
    """
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    records = tracking.list_records(
        tracking_path, status_filter="active",
        platform_filter=cfg.detect_platform(),
    )
    records = [r for r in records if r.worktree_path and Path(r.worktree_path).exists()]
    # System worktrees are recreated fresh per daemon run; never FF them here.
    records = [r for r in records if r.kind != "system"]

    if not records:
        _system_pause("No tracked worktrees.")
        return None

    # One fetch refreshes the shared upstream ref for every worktree of this
    # repo, so per-worktree classification can run with fetch=False.
    if git_ops.has_remote(repo.remote, cwd=repo.anchor):
        try:
            git_ops.fetch(repo.remote, cwd=repo.anchor)
        except Exception:
            pass

    active_paths = _build_active_paths(records)

    eligible: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
    for rec in records:
        info = git_ops.classify_worktree(
            rec.worktree_path, rec.branch,
            fetch=False, remote=repo.remote, default_branch=repo.default_branch,
            active_paths=active_paths,
        )
        info = _apply_tracking_override(rec, info)
        # Never auto-update a worktree with a live session under it.
        if info.state == git_ops.WorktreeState.ACTIVE:
            continue
        if git_ops.can_fast_forward(info):
            eligible.append((rec, info))

    if not eligible:
        _system_pause("All worktrees are up to date.")
        return None

    # Build the update picker: "update all" + one row per eligible worktree.
    while True:
        update_items: list[MenuItem] = [
            MenuItem(
                label=f"⬆ Update all ({len(eligible)} eligible)",
                kind=ItemKind.ACTION, value="all",
            ),
            MenuItem(label="", kind=ItemKind.SEPARATOR),
        ]
        index_map: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
        for rec, info in eligible:
            short_id = rec.worktree_id[-4:] if len(rec.worktree_id) > 4 else rec.worktree_id
            update_items.append(MenuItem(
                label=f"⬜ …{short_id}  ↓{info.behind}",
                subtitle=_age_str(rec.started_at) + " old",
                kind=ItemKind.NORMAL, value=len(index_map),
            ))
            index_map.append((rec, info))

        update_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
        update_items.append(MenuItem(label="↩ Back", kind=ItemKind.ACTION, value="back"))

        result = pick(
            update_items,
            title=f"⬆ {config.repo_name.replace('-', ' ').title()} -- Update Worktrees",
            subtitle="Use ↑↓, Enter to fast-forward, Esc back",
            default=0,
        )

        if result.selected < 0:
            return None

        choice = update_items[result.selected].value
        if choice == "back":
            return None

        if choice == "all":
            targets = list(eligible)
        else:
            targets = [index_map[choice]]  # type: ignore[index]

        updated = 0
        skipped = 0
        for rec, _info in targets:
            ff = git_ops.fast_forward_worktree(
                rec.worktree_path,
                remote=repo.remote,
                default_branch=repo.default_branch,
                do_fetch=False,  # already fetched once above
            )
            if ff.updated:
                updated += 1
            else:
                skipped += 1

        # Drop the just-updated worktrees from the eligible set.
        done_paths = {r.worktree_path for r, _ in targets}
        eligible = [(r, i) for r, i in eligible if r.worktree_path not in done_paths]

        msg = f"Fast-forwarded {updated} worktree{'s' if updated != 1 else ''}"
        if skipped:
            msg += f", skipped {skipped}"
        if not eligible:
            _system_pause(msg + ". All up to date.")
            return None
        _system_pause(msg + ".")


def _system_status(config: cfg.Config) -> int | None:
    """Compact status view for the system menu."""
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    records = tracking.list_records(tracking_path)

    if not records:
        _system_pause("No tracked worktrees.")
        return None

    session_ctx = sessions.scan_sessions_fast(records)
    active_paths = _build_active_paths(records, session_ctx)

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
        norm = _normalize_path(rec.worktree_path)
        title = rec.title if (rec.title and rec.title != "null") else None
        if not title and norm in session_ctx.latest_summary:
            title = session_ctx.latest_summary[norm]
        if not title and info.title:
            title = info.title
        subtitle = " ".join(title.split()) if title else None

        status_items.append(MenuItem(
            label=label, subtitle=subtitle,
            kind=ItemKind.DIMMED, value=None,
        ))

    status_items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
    status_items.append(MenuItem(label="↩ Back", kind=ItemKind.ACTION, value="back"))

    pick(
        status_items,
        title=f"📊 {config.repo_name.replace('-', ' ').title()} -- Status",
        subtitle="Esc or Enter to return",
        default=len(status_items) - 1,
    )
    return None


def _system_pause(msg: str) -> None:
    """Show a brief message via a single-item picker (press Enter to dismiss)."""
    items = [MenuItem(label=f"↩ {msg}", kind=ItemKind.ACTION, value="ok")]
    pick(items, title="", subtitle="Enter to return", default=0)


def _system_worktrees_browse(config: cfg.Config) -> int | None:
    """Browse daemon-owned system worktrees and force-remove leaked ones.

    System worktrees are created per work-session by background services and
    torn down by their owner. One left behind (a crashed or amok daemon) is
    never reaped by routine cleanup -- this view is the manual safety net.
    A live session marks a worktree as likely in-use; an old, session-less one
    is flagged as likely leaked.
    """
    tracking_path = cfg.tracking_dir()
    records = tracking.list_records(tracking_path, kind_filter="system")
    records = [r for r in records if r.repo == config.repo_name]

    if not records:
        _system_pause("No system worktrees.")
        return None

    active_paths = _build_active_paths(records)

    while True:
        records = [
            r for r in tracking.list_records(tracking_path, kind_filter="system")
            if r.repo == config.repo_name
        ]
        if not records:
            _system_pause("No system worktrees remain.")
            return None

        items: list[MenuItem] = []
        for rec in records:
            live = _normalize_path(rec.worktree_path) in active_paths
            gone = not (rec.worktree_path and Path(rec.worktree_path).exists())
            owner = rec.owner or "?"
            if live:
                tag = "live"
            elif gone:
                tag = "missing dir"
            else:
                tag = "likely leaked"
            items.append(MenuItem(
                label=f"🛠 {owner} · {rec.worktree_id}",
                subtitle=f"{tag} · {_age_str(rec.started_at)} · {rec.worktree_path}",
                kind=ItemKind.DIMMED if live else ItemKind.NORMAL,
                value=rec.worktree_id,
            ))
        items.append(MenuItem(label="", kind=ItemKind.SEPARATOR))
        items.append(MenuItem(label="↩ Back", kind=ItemKind.ACTION, value="back"))

        result = pick(
            items,
            title="🛠 System Worktrees -- daemon-owned",
            subtitle="Enter to force-remove a leaked one, Esc back",
            default=0,
        )
        if result.selected < 0:
            return None
        choice = items[result.selected].value
        if choice == "back":
            return None

        # Confirm force-remove of the selected worktree.
        sel = next((r for r in records if r.worktree_id == choice), None)
        if sel is None:
            continue
        sel_live = _normalize_path(sel.worktree_path) in active_paths
        warn = "  ⚠ has a LIVE session -- removing may disrupt a running daemon" if sel_live else ""
        confirm = pick(
            [
                MenuItem(label=f"🗑 Force-remove {sel.worktree_id}", kind=ItemKind.ACTION,
                         value="yes", subtitle=warn or None),
                MenuItem(label="↩ Cancel", kind=ItemKind.ACTION, value="no"),
            ],
            title="Force-remove system worktree?",
            subtitle="This deletes the git worktree + tracking record",
            default=1,
        )
        if confirm.selected != 0:
            continue  # cancelled

        rc = cmd_remove_system(argparse.Namespace(worktree_id=sel.worktree_id, json=False))
        _system_pause("Removed." if rc == 0 else "Remove failed (see logs).")
        # loop re-lists remaining system worktrees



# ═══════════════════════════════════════════════════════════════════════════
# Machine picker -- select target machine before worktree resolution
# ═══════════════════════════════════════════════════════════════════════════

def _load_remote_machines(
    config: cfg.Config,
) -> list[tuple[cfg.MachineEntry, list[cfg.SSHEnvironment]]]:
    """Load machines/environments reachable via SSH from the picker.

    Returns a list of (machine, ssh_environments) tuples. For remote
    machines, all SSH environments are included. For the local machine,
    only environments that differ from the current platform are included
    (e.g., WSL when running on Windows).

    Filters by ssh_ready=True, copilot=True, and non-empty environments.
    """
    repo = config.default_repo
    try:
        machines = cfg.load_machines_yaml(repo.anchor)
    except (FileNotFoundError, ValueError):
        return []

    local_key = config.machine
    current_platform = cfg.detect_platform()
    result: list[tuple[cfg.MachineEntry, list[cfg.SSHEnvironment]]] = []

    for key, entry in machines.items():
        if not entry.ssh_ready or not entry.ssh_environments or not entry.copilot:
            continue

        if key == local_key:
            # Local machine: only include other-platform environments
            other_envs = [
                e for e in entry.ssh_environments
                if e.name != current_platform
            ]
            if other_envs:
                result.append((entry, other_envs))
        else:
            result.append((entry, entry.ssh_environments))

    return result


def _try_machine_handoff(
    config: cfg.Config,
    machine_name: str,
) -> int | None:
    """Handle --machine flag for a remote machine.

    Returns an exit code if the remote plan was emitted, or None if the
    machine wasn't found (caller should error).
    """
    remote_targets = _load_remote_machines(config)
    # Build a lookup from machine key to entry
    entry_map = {entry.key: (entry, envs) for entry, envs in remote_targets}

    if machine_name not in entry_map:
        # Also check by alias
        found = None
        for entry, envs in remote_targets:
            if entry.alias and entry.alias.lower() == machine_name.lower():
                found = (entry, envs)
                break
        if not found:
            output.err(f"Unknown or unreachable remote machine: {machine_name}")
            all_machines = _load_all_machine_keys(config)
            if all_machines:
                output.err("Available: " + ", ".join(all_machines))
            return 1
        entry, envs = found
    else:
        entry, envs = entry_map[machine_name]

    ssh_alias = _resolve_ssh_alias(entry)
    project = cfg.project_name()
    _emit_plan({
        "action": "remote",
        "ssh_alias": ssh_alias,
        "remote_command": project,
        "machine": entry.key,
        "display_name": entry.display_name,
    })
    return 0


def _load_all_machine_keys(config: cfg.Config) -> list[str]:
    """Load all machine keys from machines.yaml for error messages."""
    repo = config.default_repo
    try:
        machines = cfg.load_machines_yaml(repo.anchor)
        return list(machines.keys())
    except (FileNotFoundError, ValueError):
        return []


def _resolve_ssh_alias(entry: cfg.MachineEntry) -> str:
    """Pick the best SSH alias for a remote machine.

    Prefers the primary platform environment (windows for Windows machines,
    linux/wsl for Linux/WSL machines).  Falls back to the first available
    SSH environment.
    """
    if not entry.ssh_environments:
        return entry.key

    # Prefer 'windows' env for Windows machines, 'linux' for Linux
    env_lower = entry.environment.lower()
    if "windows" in env_lower:
        for ssh_env in entry.ssh_environments:
            if ssh_env.name == "windows":
                return ssh_env.alias
    else:
        for ssh_env in entry.ssh_environments:
            if ssh_env.name in ("linux", "wsl"):
                return ssh_env.alias

    return entry.ssh_environments[0].alias


def _resolve_profile(
    config: cfg.Config,
    args: argparse.Namespace,
) -> cfg.CopilotProfile | None:
    """Resolve --profile flag to a CopilotProfile object."""
    requested = getattr(args, "profile", None)
    if not requested:
        return None
    profiles = config.copilot_profiles or [cfg.DEFAULT_PROFILE]
    for p in profiles:
        if p.name == requested:
            return p
    return None


def _resolve_base_repo(
    config: cfg.Config,
    args: argparse.Namespace,
    profile: cfg.CopilotProfile | None = None,
) -> int:
    """Resolve launch plan for base repo mode."""
    repo = config.default_repo
    print()
    print("📂 Base Repo Mode -- No Worktree")
    print(f"   Path: {repo.anchor}")
    print()
    output.warn("Commits will go directly to the current branch.")
    print()

    dirty = git_ops.get_dirty_files(repo.anchor) if sys.stdin.isatty() else []
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
            env_str = ", ".join(f"{k}={v}" for k, v in merged_env.items())
            output.dry_run(f"Would set env: {env_str}")
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

    activity.log_event(
        "worktree_resumed",
        worktree_id=record.worktree_id,
        branch=record.branch,
        resume_count=record.resume_count,
    )

    # Auto-fast-forward a stale-but-clean worktree before launch so the
    # session (and any setup script) sees an up-to-date tree.  This is a
    # fast-forward only -- a worktree with local commits or uncommitted
    # changes is never touched.  Skipped under --dry-run, when the
    # auto_fast_forward config flag is off, or with --no-fast-forward.
    if (
        not args.dry_run
        and getattr(config, "auto_fast_forward", True)
        and not getattr(args, "no_fast_forward", False)
    ):
        repo = config.default_repo
        ff = git_ops.fast_forward_worktree(
            record.worktree_path,
            remote=repo.remote,
            default_branch=repo.default_branch,
            do_fetch=True,
        )
        if ff.updated:
            plural = "s" if ff.behind != 1 else ""
            print(
                f"   ⬆ Fast-forwarded {ff.behind} commit{plural} to "
                f"{repo.remote}/{repo.default_branch}"
            )
        elif ff.reason in ("ahead", "diverged"):
            print(f"   ⚠ Local commits present -- skipping auto-update ({ff.reason})")

    launch_cmd = _build_launch_cmd(config, args, record.worktree_path, profile=profile)
    merged_env = _build_env(profile)

    # Auto-resume: find the most recent Copilot session for this worktree
    # and pass --resume <session-id> so the user picks up where they left off.
    no_resume = getattr(args, "no_resume", False)
    if not no_resume:
        last_session = sessions.find_latest_session_id_fast(
            record.worktree_path, record.sessions,
        )
        if last_session:
            launch_cmd.extend(["--resume", last_session])
            print(f"   Resuming session: {last_session[:12]}…")

    print()

    if args.dry_run:
        output.dry_run(f"Would launch: {' '.join(launch_cmd)}")
        if merged_env:
            env_str = ", ".join(f"{k}={v}" for k, v in merged_env.items())
            output.dry_run(f"Would set env: {env_str}")
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
    print(f"🌳 {config.repo_name.replace('-', ' ').title()} -- New Worktree")
    print(f"   Worktree: {worktree_id}")
    print(f"   Path:     {worktree_path}")
    print()

    if args.dry_run:
        output.dry_run(f"Would fetch from {repo.remote}")
        output.dry_run(f"Would create worktree at {worktree_path} on branch {branch}")
        output.dry_run("Would write tracking YAML")
        output.dry_run("Would clone permissions")
        output.dry_run("Would add worktree path to trusted_folders")
        launch_cmd = _build_launch_cmd(config, args, worktree_path, profile=profile)
        merged_env = _build_env(profile)
        output.dry_run(f"Would launch: {' '.join(launch_cmd)}")
        if merged_env:
            env_str = ", ".join(f"{k}={v}" for k, v in merged_env.items())
            output.dry_run(f"Would set env: {env_str}")
        print()
        output.ok("Dry run complete -- no changes made")
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
# Worktree-ID inference -- shared by finalize, post-exit, mark-complete
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
         ``APERTURE_WORKTREE_ID`` for backward compat; last resort -- may
         be stale in long-lived tmux servers or sub-agent contexts)

    Git branch is intentionally *not* used for identification -- worktrees
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

    # Exact match -- fast path
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

    # No tracking match -- return as-is (caller will fail on missing YAML)
    return raw_id


# ═══════════════════════════════════════════════════════════════════════════
# post-exit -- finalization after Copilot exits
# ═══════════════════════════════════════════════════════════════════════════

def cmd_post_exit(args: argparse.Namespace) -> int:
    """Run post-exit checks on a worktree after Copilot exits. Idempotent."""
    config = cfg.load_config()
    worktree_id = _infer_worktree_id(args.worktree_id, config)
    if not worktree_id:
        output.err(
            "Could not determine worktree ID. Pass it explicitly "
            "or run from inside a worktree."
        )
        return 1
    worktree_id = _resolve_worktree_id(worktree_id)

    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        output.warn(f"No tracking record for {worktree_id} -- skipping post-exit.")
        return 0

    try:
        record = tracking.load_record(yaml_path)
    except Exception as e:
        output.err(f"Failed to load record {worktree_id}: {e}")
        return 1

    # Already finalized -- nothing to do
    if record.status == "finalized":
        output.ok(f"Worktree {worktree_id} already finalized.")
        return 0

    return _post_exit_gate(record, config)


def _post_exit_gate(record: tracking.WorktreeRecord, config: cfg.Config) -> int:
    """Check post-exit state and trigger finalization if the session is complete.

    Returns 0 on success or skip, 1 on finalization failure.
    """
    worktree_id = record.worktree_id

    if record.status in ("complete", "pushed"):
        print(f"Session {worktree_id} ready for finalization -- validating...")
        success = fin.validate_and_finalize(worktree_id, config)
        if success:
            return 0
        output.err(
            f"Finalization failed for {worktree_id}. "
            f"Run 'agent-worktrees finalize' to retry."
        )
        return 1

    if record.status == "orphaned":
        output.warn(
            f"Session {worktree_id} is orphaned (previous push failed). "
            f"Run 'agent-worktrees push-changes' to retry pushing, "
            f"then 'agent-worktrees finalize' to clean up."
        )
        return 0

    # status == "active" -- session wasn't marked complete
    print(
        f"Session {worktree_id} is still active (not pushed/completed). "
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
            msg = (
                "Could not determine worktree ID. Pass it explicitly "
                "or run from inside a worktree."
            )
            if use_json:
                return _json_error(msg)
            output.err(msg)
            return 1
        worktree_id = _resolve_worktree_id(worktree_id)
        success = fin.validate_and_finalize(
            worktree_id, config, dry_run=args.dry_run,
        )

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
# push-changes
# ═══════════════════════════════════════════════════════════════════════════

def cmd_push_changes(args: argparse.Namespace) -> int:
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
            msg = (
                "Could not determine worktree ID. Pass it explicitly "
                "or run from inside a worktree."
            )
            if use_json:
                return _json_error(msg)
            output.err(msg)
            return 1
        worktree_id = _resolve_worktree_id(worktree_id)

        # --title-only: just set the title, don't push
        if getattr(args, "title_only", False):
            yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
            if yaml_path.exists():
                record = tracking.load_record(yaml_path)
                if args.title:
                    record.title = args.title.replace("\n", " ").strip()
                    tracking.save_record(record)
                print(f"[OK] Worktree {worktree_id} title updated: {args.title}")
            else:
                output.err(f"Tracking file not found for {worktree_id}")
                return 1
            return 0

        success = fin.push_changes(
            worktree_id, config,
            title=args.title,
            dry_run=args.dry_run,
            allow_unsquashed=getattr(args, "allow_unsquashed", False),
        )

        if use_json:
            yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
            final_status = "pushed"
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
# create-pr
# ═══════════════════════════════════════════════════════════════════════════

def cmd_create_pr(args: argparse.Namespace) -> int:
    """Squash worktree commits, create + push a feature branch for a PR.

    The CLI owns the git operations only.  After this succeeds, the agent
    delegates actual PR creation to the configured provider sub-agent
    (``pr.provider``) and records the result via ``set-pr``.
    """
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
            msg = (
                "Could not determine worktree ID. Pass it explicitly "
                "or run from inside a worktree."
            )
            if use_json:
                return _json_error(msg)
            output.err(msg)
            return 1
        worktree_id = _resolve_worktree_id(worktree_id)

        result = pr_ops.create_pr(
            worktree_id, config,
            title=args.title,
            branch=args.branch,
            dry_run=args.dry_run,
        )

        if use_json:
            _json_output(result)
        elif result.get("success"):
            branch = result.get("branch", "")
            remote = result.get("remote", "")
            provider = result.get("provider", "")
            output.ok(f"Feature branch '{branch}' pushed to {remote}.")
            print(
                f"  base: {result.get('base_sha', '')[:10]}  "
                f"head: {result.get('head_sha', '')[:10]}"
            )
            print(
                f"Next: delegate PR creation to the '{provider}' provider, "
                f"then record it with:\n"
                f"  agent-worktrees set-pr {worktree_id} --url <URL> --number <N>"
            )
        else:
            output.err(result.get("error", "create-pr failed."))

        return 0 if result.get("success") else 1
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


# ═══════════════════════════════════════════════════════════════════════════
# set-pr / pr-status
# ═══════════════════════════════════════════════════════════════════════════

def cmd_set_pr(args: argparse.Namespace) -> int:
    """Record PR metadata (URL/number/state/provider) from the sub-agent."""
    use_json = getattr(args, "json", False)
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
    except Exception as e:
        if use_json:
            return _json_error(str(e))
        raise
    worktree_id = _infer_worktree_id(args.worktree_id, config)
    if not worktree_id:
        msg = ("Could not determine worktree ID. Pass it explicitly "
               "or run from inside a worktree.")
        return _json_error(msg) if use_json else (output.err(msg) or 1)
    worktree_id = _resolve_worktree_id(worktree_id)

    result = pr_ops.set_pr(
        worktree_id,
        url=args.url,
        number=args.number,
        state=args.state,
        provider=args.provider,
        branch=args.branch,
    )
    if use_json:
        _json_output(result)
    elif result.get("success"):
        output.ok(
            f"Recorded PR for {worktree_id}: "
            f"#{result.get('number')} ({result.get('state')}) {result.get('url')}"
        )
    else:
        output.err(result.get("error", "set-pr failed."))
    return 0 if result.get("success") else 1


def cmd_pr_status(args: argparse.Namespace) -> int:
    """Read the tracked PR metadata for a worktree."""
    use_json = getattr(args, "json", False)
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
    except Exception as e:
        if use_json:
            return _json_error(str(e))
        raise
    worktree_id = _infer_worktree_id(args.worktree_id, config)
    if not worktree_id:
        msg = ("Could not determine worktree ID. Pass it explicitly "
               "or run from inside a worktree.")
        return _json_error(msg) if use_json else (output.err(msg) or 1)
    worktree_id = _resolve_worktree_id(worktree_id)

    result = pr_ops.pr_status(worktree_id)
    if use_json:
        _json_output(result)
        return 0 if result.get("has_pr") or "error" not in result else 1
    if result.get("error"):
        output.err(result["error"])
        return 1
    if not result.get("has_pr"):
        print(f"{worktree_id}: no PR recorded (direct-push or not yet created).")
        return 0
    print(f"PR for {worktree_id}:")
    print(f"  state:    {result.get('state')}")
    print(f"  branch:   {result.get('branch')}")
    print(f"  number:   {result.get('number')}")
    print(f"  url:      {result.get('url')}")
    print(f"  provider: {result.get('provider')}")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# mark-complete
# ═══════════════════════════════════════════════════════════════════════════

def cmd_mark_complete(args: argparse.Namespace) -> int:
    """Manual recovery only -- set tracking status without pushing or finalizing."""
    config = cfg.load_config()
    worktree_id = _infer_worktree_id(args.worktree_id, config)

    if not worktree_id:
        output.err(
            "Could not determine worktree ID. Pass it explicitly "
            "or run from inside a worktree."
        )
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
        print(f"[OK] Worktree {worktree_id} title updated: {args.title}")
        return 0

    msg = f"[OK] Worktree {worktree_id} marked complete (status flag only)."
    if args.title:
        msg += f" Title: {args.title}"
    print(msg)
    print(
        "NOTE: This only sets the tracking flag. Content has NOT been pushed. "
        "For normal sign-off, use 'agent-worktrees push-changes' + "
        "'agent-worktrees finalize' instead."
    )

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
    session_ctx = sessions.scan_sessions_fast(records)
    active_paths = _build_active_paths(records, session_ctx)

    # Mux status (batch query if requested)
    mux_map: dict[str, sessions.MuxInfo] = {}
    if getattr(args, "mux_details", False):
        wt_ids = [rec.worktree_id for rec in records]
        mux_map = sessions.mux_status_many(wt_ids)

    results: list[dict] = []
    for rec in records:
        info = git_ops.classify_worktree(
            rec.worktree_path, rec.branch,
            fetch=True, remote=repo.remote, default_branch=repo.default_branch,
            active_paths=active_paths,
        )
        info = _apply_tracking_override(rec, info)
        result_entry = _worktree_to_dict(
            rec, state_info=info, mux_info=mux_map.get(rec.worktree_id),
            session_ctx=session_ctx,
        )
        # Add display helpers for table output
        short_id = rec.worktree_id[-4:] if len(rec.worktree_id) > 4 else rec.worktree_id
        result_entry["short_id"] = short_id
        display_title = rec.title if (rec.title and rec.title != "null") else None
        if not display_title:
            norm = _normalize_path(rec.worktree_path)
            display_title = session_ctx.latest_summary.get(norm)
        if not display_title:
            display_title = info.title or "(none)"
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
    print(f"🌳 {config.repo_name.replace('-', ' ').title()} -- Worktree Status")
    print()
    print(f"{'ID':<6} {'State':<11} {'Ahead':<7} {'Behind':<8} Title")
    print(f"{'─'*5:<6} {'─'*10:<11} {'─'*6:<7} {'─'*7:<8} {'─'*30}")

    for r in results:
        color = STATE_COLORS.get(r.get("state", ""), "0")
        state_str = (
            f"\033[{color}m{r.get('state', ''):<11}\033[0m"
            if output._COLOR else f"{r.get('state', ''):<11}"
        )
        print(
            f"{r['short_id']:<6} {state_str} {r.get('ahead', ''):<7} "
            f"{r.get('behind', ''):<8} {r['title']}"
        )

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
# list -- lightweight inventory from tracking records
# ═══════════════════════════════════════════════════════════════════════════

def cmd_list(args: argparse.Namespace) -> int:
    """List worktrees from tracking records.

    By default, applies the same filters as the interactive picker:
    only worktrees for the current platform whose directories still
    exist on disk.  Pass ``--all`` to skip existence checks, or
    ``--tracking-status`` / ``--include-other-platforms`` for finer
    control.
    """
    tracking_path = cfg.tracking_dir()
    status_filter = None if args.tracking_status == "all" else args.tracking_status

    if getattr(args, "include_other_platforms", False):
        platform_filter = None
    else:
        platform_filter = cfg.detect_platform()

    records = tracking.list_records(
        tracking_path,
        status_filter=status_filter,
        platform_filter=platform_filter,
    )

    # Unless --all is passed, filter to worktrees that still exist on disk
    # (matching the picker's behaviour).
    if not getattr(args, "all", False):
        records = [
            r for r in records
            if r.worktree_path
            and Path(r.worktree_path).exists()
            and (Path(r.worktree_path) / ".git").exists()
        ]

    if args.json:
        mux_map: dict[str, sessions.MuxInfo] = {}
        if getattr(args, "mux_details", False):
            wt_ids = [rec.worktree_id for rec in records]
            mux_map = sessions.mux_status_many(wt_ids)
        session_ctx = sessions.scan_sessions_fast(records)
        worktrees = [
            _worktree_to_dict(
                rec, mux_info=mux_map.get(rec.worktree_id),
                session_ctx=session_ctx,
            )
            for rec in records
        ]
        # Enrich titles from session data (same cascade as table output)
        for wt_dict, rec in zip(worktrees, records, strict=True):
            title = wt_dict.get("title")
            if not title or title == "null":
                norm = _normalize_path(rec.worktree_path)
                title = session_ctx.latest_summary.get(norm)
            wt_dict["title"] = title
        _json_output({"worktrees": worktrees})
        return 0

    if not records:
        print("No tracked worktrees.")
        return 0

    # Light session scan for display text (names/summaries)
    session_ctx = sessions.scan_sessions_fast(records)

    print()
    print(f"{'ID':<42} {'Status':<12} {'Platform':<8} Title")
    print(f"{'─'*41:<42} {'─'*11:<12} {'─'*7:<8} {'─'*30}")
    for rec in records:
        short_id = rec.worktree_id[-12:] if len(rec.worktree_id) > 12 else rec.worktree_id
        title = rec.title if (rec.title and rec.title != "null") else None
        if not title:
            norm = _normalize_path(rec.worktree_path)
            title = session_ctx.latest_summary.get(norm)
        if not title:
            title = "(none)"
        print(f"{short_id:<42} {rec.status:<12} {rec.platform:<8} {title}")

    print(f"\n{len(records)} worktree(s).")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# create -- non-interactive worktree creation
# ═══════════════════════════════════════════════════════════════════════════

def _slugify(text: str) -> str:
    """Lowercase, keep alnum/dash, collapse the rest to single dashes."""
    import re
    s = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return s or "daemon"


def cmd_create_system(args: argparse.Namespace) -> int:
    """Create a daemon-owned *system* worktree (hidden from the Picker).

    Per-session model: a background service calls this for each work run, uses
    the returned path, then tears it down with ``remove-system``. System
    worktrees are exempt from routine cleanup; leaked ones (crashed daemon) are
    force-removable via the System menu or ``remove-system``.
    """
    with output.stdout_to_stderr():
        try:
            config = cfg.load_config()
            result = _create_worktree_core(
                config, no_mux=True, kind="system",
                owner=getattr(args, "owner", None) or getattr(args, "name", None),
                name=getattr(args, "name", None),
            )
        except Exception as e:
            if getattr(args, "json", False):
                return _json_error(str(e))
            output.err(str(e))
            return 1

    if getattr(args, "json", False):
        _json_output(result)
        return 0

    wt = result["worktree"]
    print(f"✅ Created system worktree: {wt['id']}")
    print(f"   Path:   {wt['path']}")
    print(f"   Branch: {wt['branch']}")
    return 0


def cmd_remove_system(args: argparse.Namespace) -> int:
    """Remove a system worktree by id (git worktree + tracking record).

    Refuses non-system worktrees. Used by daemons at end-of-run and by the
    System-menu browse view to reap leaked worktrees.
    """
    config = cfg.load_config()
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    wt_id = getattr(args, "worktree_id", None)
    if not wt_id:
        output.err("remove-system requires a worktree id")
        return 2

    yaml_path = tracking_path / f"{wt_id}.yaml"
    if not yaml_path.exists():
        output.err(f"no such worktree: {wt_id}")
        return 1
    rec = tracking.load_record(yaml_path)
    if rec.kind != "system":
        output.err(f"{wt_id} is not a system worktree (kind={rec.kind}); refusing")
        return 1

    if rec.worktree_path and Path(rec.worktree_path).exists():
        git_ops.remove_worktree(repo.anchor, rec.worktree_path)
    if rec.branch:
        git_ops.git("branch", "-D", rec.branch, cwd=repo.anchor, check=False)
    try:
        yaml_path.unlink()
    except OSError:
        pass
    activity.log_event("system_worktree_removed", worktree_id=wt_id)
    if getattr(args, "json", False):
        _json_output({"removed": wt_id})
    else:
        print(f"✅ Removed system worktree: {wt_id}")
    return 0


def cmd_create(args: argparse.Namespace) -> int:
    """Create a new worktree non-interactively.

    When ``--json`` is passed, emits a JSON envelope with the new
    worktree info and launch plan.  The caller is responsible for
    launching Copilot -- this command returns the command info only.
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

    # System worktrees are daemon-owned and torn down by their owning service;
    # never auto-removed here (a routine cleanup must not yank one out from
    # under a running daemon). Force-removal lives in the ":" System menu.
    records = [r for r in records if r.kind != "system"]
    if not records:
        print("No tracked sessions.")
        return 0

    to_clean: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
    skipped: list[tuple[tracking.WorktreeRecord, str]] = []
    unused_count = 0
    dirty_count = 0
    wip_count = 0

    print()
    print(f"🌳 {config.repo_name.replace('-', ' ').title()} -- Worktree Sessions")
    print()
    print(f"{'Worktree ID':<50} {'State':<12} {'Age':<12} Path")
    print(f"{'─'*48:<50} {'─'*10:<12} {'─'*10:<12} {'─'*30}")

    # Fetch once for accurate classification (skip gracefully if there is no
    # remote -- a local-only repo must not crash cleanup).
    if git_ops.has_remote(repo.remote, cwd=repo.anchor):
        git_ops.fetch(repo.remote, cwd=repo.anchor)
    upstream = f"{repo.remote}/{repo.default_branch}"

    # Scan for live Copilot sessions and mux sessions
    active_paths = _build_active_paths(records)

    for rec in records:
        if rec.worktree_path and Path(rec.worktree_path).exists():
            info = git_ops.classify_worktree(
                rec.worktree_path, rec.branch,
                fetch=False, remote=repo.remote, default_branch=repo.default_branch,
                active_paths=active_paths,
            )
            info = _apply_tracking_override(rec, info)
            state_str = info.state.value
        elif rec.status == "finalized":
            state_str = "completed"
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
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
        elif rec.status == "finalized" or info.state == git_ops.WorktreeState.COMPLETED:
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
        print(
            f"{unused_count} unused worktree(s) preserved -- no commits, "
            "no uncommitted changes (pass --include-unused to also clean)."
        )

    if dirty_count > 0 or wip_count > 0:
        parts = []
        if dirty_count:
            parts.append(f"{dirty_count} with uncommitted changes")
        if wip_count:
            parts.append(f"{wip_count} with unmerged commits")
        output.warn(f"{' and '.join(parts)} -- not eligible for cleanup.")

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
        output.err("Timed out waiting for finalization lock -- another finalization in progress?")
        return 1

    failures = 0
    try:
        for rec, info in to_clean:
            print(f"Cleaning {rec.worktree_id} ({info.state.value})...")

            if rec.worktree_path and Path(rec.worktree_path).exists():
                if not git_ops.remove_worktree(repo.anchor, rec.worktree_path):
                    output.warn("Could not remove worktree via git -- forcing directory removal.")
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

            activity.log_event(
                "worktree_reaped",
                worktree_id=rec.worktree_id,
                branch=rec.branch,
                state=info.state.value,
            )

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
    """Look up *machine* in machines.yaml by key or alias.  Returns the
    entry or prints an error and returns None."""
    try:
        registry = cfg.load_machines_yaml(repo_dir)
    except FileNotFoundError:
        output.err(f"Machine registry not found at {repo_dir / 'machines.yaml'}")
        output.info("Create machines.yaml in the repo root with an entry for this machine.")
        return None
    except ValueError as exc:
        output.err(str(exc))
        return None

    entry = cfg.find_machine_entry(registry, machine)
    if entry is None:
        output.err(f"Machine '{machine}' not found in machines.yaml")
        output.info("Add an entry for this machine and retry:")
        output.info("  machines:")
        output.info(f"    {machine}:")
        output.info(f"      display_name: {machine.title()}")
        output.info('      environment: "<OS and version>"')
        output.info(
            '      # alias: "<facility-name>"  '
            '# colloquial name if different from hostname'
        )
        return None

    return entry


# Ownership marker embedded in generated instruction files
_INSTRUCTION_MARKER = "<!-- managed by agent-worktrees -->"


def _deploy_copilot_instructions(
    proj_dir: Path, entry: cfg.MachineEntry,
    project: str = "",
) -> None:
    """Write or update machine instruction files from the registry.

    Deploys into the COPILOT_CUSTOM_INSTRUCTIONS_DIRS directory:

    - ``.github/instructions/machine.instructions.md`` -- machine identity,
      project name, and binstub info.
    - ``AGENTS.md`` -- discovered as a nested AGENTS.md in custom dirs
      (machine identity content, same as machine.instructions.md).

    All files are tagged with an ownership marker so stale files can be
    identified and cleaned up.
    """
    raw = cfg.render_copilot_instructions(entry, project=project)
    content = f"{_INSTRUCTION_MARKER}\n{raw}"

    # Primary: .github/instructions/*.instructions.md (auto-injected)
    instr_dir = proj_dir / ".github" / "instructions"
    instr_dir.mkdir(parents=True, exist_ok=True)
    instr_path = instr_dir / "machine.instructions.md"
    if instr_path.exists() and instr_path.read_text() == content:
        output.skipped("machine.instructions.md already in sync")
    else:
        instr_path.write_text(content)
        output.changed(f"machine.instructions.md -> {instr_path}")

    # Fallback: AGENTS.md (nested discovery)
    agents_path = proj_dir / "AGENTS.md"
    if agents_path.exists() and agents_path.read_text() == content:
        output.skipped("AGENTS.md already in sync")
    else:
        agents_path.write_text(content)
        output.changed(f"AGENTS.md -> {agents_path}")

    # Clean up stale ssh.instructions.md from previous versions
    ssh_instr_path = instr_dir / "ssh.instructions.md"
    if ssh_instr_path.exists():
        try:
            text = ssh_instr_path.read_text()
            if _INSTRUCTION_MARKER in text:
                ssh_instr_path.unlink()
                output.changed("removed stale ssh.instructions.md (now a skill)")
        except OSError:
            pass

    # Clean up legacy files from previous deploy strategies
    for legacy_name in ("copilot-instructions.md",):
        legacy = proj_dir / legacy_name
        if legacy.exists():
            legacy.unlink()
            output.changed(f"removed legacy {legacy_name}")


def _cleanup_stale_instructions(proj_dir: Path) -> None:
    """Remove generated instruction files when machines.yaml is absent.

    Only removes files that contain the agent-worktrees ownership marker,
    so user-created instruction files are preserved.
    """
    candidates = [
        proj_dir / ".github" / "instructions" / "machine.instructions.md",
        proj_dir / ".github" / "instructions" / "ssh.instructions.md",
        proj_dir / "AGENTS.md",
    ]
    for path in candidates:
        if path.exists():
            try:
                content = path.read_text()
                if _INSTRUCTION_MARKER in content:
                    path.unlink()
                    output.changed(f"removed stale {path.name} (no machines.yaml)")
            except OSError:
                pass


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

    # Machine registry is optional -- repos without machines.yaml still work
    machine_entry: cfg.MachineEntry | None = None
    machines_yaml = repo_dir / "machines.yaml"
    if machines_yaml.exists():
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

    # Deploy copilot-instructions.md from machine registry (if available)
    if machine_entry is not None:
        _deploy_copilot_instructions(proj_dir, machine_entry, project=project)
    else:
        _cleanup_stale_instructions(proj_dir)

    # Create venv first (shared runtime) -- package install targets the venv
    if not inst.create_venv():
        return 1

    # Deploy Python package into the venv (shared runtime)
    if not inst.deploy_package(repo_dir):
        return 1

    # Deploy wrappers (shared runtime)
    if not inst.deploy_wrappers(repo_dir):
        return 1

    # Deploy project-specific binstubs
    if not inst.deploy_binstubs(repo_dir, project=project):
        return 1

    # Update projects registry
    inst.register_project(project, repo_dir=repo_dir)

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

    # Install PR-workflow git hook shims into the anchor's shared hooks dir.
    # Gated on PR mode so deploying to a direct-push repo never touches its
    # hooks. Inert unless AGENT_WORKTREES_HOOKS=1 even when installed.
    try:
        cfg_for_hooks = cfg.load_config(config_path)
        if cfg_for_hooks.default_repo.pr.enabled:
            from . import hooks as _hooks
            installed_hooks = _hooks.install_hooks(repo_dir)
            if installed_hooks:
                output.ok(
                    f"PR-workflow git hooks installed ({', '.join(installed_hooks)})"
                )
    except Exception as e:
        output.warn(f"Could not install git hooks: {e}")

    print()
    output.ok("Installation complete")
    print(f"  Runtime:   {runtime_dir}")
    print(f"  Project:   {proj_dir}")
    print(f"  Usage:     {project}")
    return 0


def _refresh_terminal_profiles() -> None:
    """Re-run the install.ps1 terminal-profile generator if available.

    After adopting a new project, the WT fragment needs to be regenerated
    to include the new project's profile.  Delegates to the PowerShell
    installer's Deploy-Shortcuts function via a lightweight wrapper call.
    """
    install_dir = cfg.install_dir()
    manifest_path = install_dir / "deploy-manifest.json"
    if not manifest_path.exists():
        return

    try:
        m = json.loads(manifest_path.read_text())
        plugin_source = m.get("plugin_source")
        if not plugin_source or not Path(plugin_source).exists():
            return

        install_script = Path(plugin_source) / "scripts" / "install.ps1"
        if not install_script.exists():
            return

        # The install script's "update" action regenerates terminal profiles
        # Use a targeted powershell invocation that just refreshes shortcuts
        subprocess.run(
            ["pwsh", "-NoProfile", "-File", str(install_script), "update"],
            capture_output=True, text=True, timeout=30,
        )
        output.ok("Windows Terminal profiles refreshed")
    except Exception:
        output.warn("Could not refresh Windows Terminal profiles")


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
        # For `register`, the current directory is authoritative -- resolve the
        # git root of cwd first. _find_repo_dir() walks up from the installed
        # module location (~/.agent-worktrees/...) before checking cwd, which
        # can resolve to an unrelated repo (e.g. when $HOME itself is a git
        # repo, as with dotfiles-in-$HOME setups).
        repo_dir = None
        try:
            r = subprocess.run(
                ["git", "-C", str(Path.cwd()), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5,
            )
            if r.returncode == 0 and r.stdout.strip():
                # Normalize through resolve_to_anchor so that running from
                # inside a linked worktree resolves back to the main checkout,
                # matching _find_repo_dir()'s behavior. Without this, registering
                # from an active worktree would anchor to the ephemeral path.
                repo_dir = git_ops.resolve_to_anchor(
                    Path(r.stdout.strip()).resolve()
                )
        except Exception:
            pass
        if not repo_dir:
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
        # origin/HEAD unset -- do NOT fall back to the current branch, which is
        # often a feature branch in worktree workflows and would silently record
        # the wrong default. Probe for a conventional default branch instead.
        for candidate in ("master", "main"):
            try:
                r = subprocess.run(
                    ["git", "-C", str(repo_dir), "rev-parse", "--verify",
                     f"refs/heads/{candidate}"],
                    capture_output=True, text=True, timeout=5,
                )
                if r.returncode == 0:
                    default_branch = candidate
                    break
            except Exception:
                pass
    if not default_branch:
        # No conventional default found -- ask explicitly rather than guessing.
        output.warn(
            "Could not detect default branch "
            "(no origin/HEAD, no master or main branch)"
        )
        branch_input = input("  Default branch name: ").strip()
        if branch_input:
            default_branch = branch_input
        else:
            default_branch = "master"
            output.warn(f"Assuming default branch: {default_branch}")

    print(f"  Repo:     {repo_dir}")
    print(f"  Branch:   {default_branch}")
    print(f"  Machine:  {machine}")
    print(f"  Platform: {plat}")

    # Machine registry is optional -- external repos may not have machines.yaml
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
        _write_config(
            config_path, repo_dir, machine, plat, project, default_branch,
            headless=getattr(args, "headless", False),
        )
    else:
        output.skipped(f"Config exists at {config_path} (use --force to overwrite)")

    # Deploy copilot-instructions.md from machine registry
    if machine_entry is not None:
        _deploy_copilot_instructions(proj_dir, machine_entry, project=project)
    else:
        _cleanup_stale_instructions(proj_dir)

    # Generate binstub
    if not inst.deploy_binstubs(repo_dir, project=project):
        return 1

    # Update projects registry -- include WSL state only when actually in WSL
    wsl_state: str | None = None
    wsl_distro: str | None = None
    wsl_path: str | None = None
    wsl_distro_name = os.environ.get("WSL_DISTRO_NAME")
    if wsl_distro_name:
        wsl_state = "adopted"
        wsl_distro = wsl_distro_name
        wsl_path = str(repo_dir)
    # Resolve agent exposure: explicit flags win, else the repos.yaml
    # classification, else default ON (adopting a repo means working in it).
    if getattr(args, "no_agent", False):
        expose_agent = False
    elif getattr(args, "agent", False):
        expose_agent = True
    else:
        from . import repos as _repos
        _entry = _repos.find_repo(project)
        expose_agent = _entry.agent if _entry else True

    inst.register_project(
        project,
        repo_dir=repo_dir,
        default_branch=default_branch,
        expose_agent=expose_agent,
        base_repo=getattr(args, "base_repo", False),
        elevated=getattr(args, "elevated", False),
        wsl_state=wsl_state,
        wsl_distro=wsl_distro,
        wsl_path=wsl_path,
    )

    # Refresh Windows Terminal profiles if installed via install.ps1
    if plat == "windows":
        _refresh_terminal_profiles()

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
    """Update agent-worktrees via the Copilot CLI plugin system.

    1. Run ``copilot plugin update`` to fetch the latest plugin version.
    2. Locate the installed plugin directory.
    3. Run the platform-specific installer from the freshly updated plugin.
    """
    output.header("Updating Agent Worktrees")

    if getattr(args, "recreate_venv", False):
        output.warn("--recreate-venv is not supported by the plugin-based "
                     "update flow; use 'agent-worktrees install' instead")

    # Step 1 -- update the Copilot CLI plugin (pulls latest from marketplace)
    plugin_ref = "agent-worktrees@copilot-extensions"
    output.info(f"Updating plugin: {plugin_ref}")
    try:
        r = subprocess.run(
            ["copilot", "plugin", "update", plugin_ref],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            for line in r.stdout.strip().splitlines():
                output.ok(line)
        else:
            detail = "\n".join(
                x for x in [r.stdout.strip(), r.stderr.strip()] if x
            )
            output.warn(f"Plugin update returned non-zero:\n{detail}")
    except FileNotFoundError:
        output.warn("'copilot' CLI not found -- skipping plugin update")
    except subprocess.TimeoutExpired:
        output.warn("Plugin update timed out -- continuing with installed version")

    # Step 2 -- find the installed plugin directory
    plugin_dir = _find_installed_plugin_dir()
    if not plugin_dir:
        output.err("Cannot find installed plugin directory")
        output.err("Expected at ~/.copilot/installed-plugins/copilot-extensions/"
                    "agent-worktrees/")
        return 1

    output.info(f"Plugin source: {plugin_dir}")

    # Step 3 -- run the platform-specific installer from the plugin dir
    plat = cfg.detect_platform()
    if plat == "windows":
        installer = plugin_dir / "scripts" / "install.ps1"
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            output.err("PowerShell not found")
            return 1
        argv = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(installer), "update"]
    else:
        installer = plugin_dir / "scripts" / "install.sh"
        argv = ["bash", str(installer), "update"]

    if not installer.exists():
        output.err(f"Installer not found: {installer}")
        return 1

    result = subprocess.run(argv, cwd=plugin_dir, timeout=300)
    if result.returncode != 0:
        return result.returncode

    # Step 4 -- update registered sibling modules (agent-bridge, etc.)
    skip_modules = getattr(args, "skip_modules", None)
    _update_modules(plugin_dir, plat, skip_modules)

    return 0


def _update_modules(
    plugin_dir: Path,
    platform: str,
    skip_modules: list[str] | None,
) -> None:
    """Update sibling modules registered in modules.json.

    Modules are updated in the order listed in the manifest.  Failures
    are warned but do not abort the overall update.

    Each module must follow the standard installer convention:
    - ``scripts/install.{ps1,sh}`` with ``install``, ``update``,
      ``status`` verbs.
    - ``status`` exits 0 if installed, non-zero if not.
    - On first encounter, runs ``install``; thereafter ``update``.

    Args:
        plugin_dir: Path to the installed agent-worktrees plugin directory.
        platform: ``"windows"`` or ``"linux"``.
        skip_modules: ``None`` = update all, ``[]`` = skip all,
            ``["name", ...]`` = skip named modules.
    """
    manifest = plugin_dir / "modules.json"
    if not manifest.exists():
        return

    try:
        data = json.loads(manifest.read_text())
    except Exception as exc:
        output.warn(f"Failed to parse modules.json: {exc}")
        return

    modules = data.get("modules", [])
    if not modules:
        return

    # --skip-modules with no names => skip all
    if skip_modules is not None and len(skip_modules) == 0:
        output.info("Skipping all module updates (--skip-modules)")
        return

    extensions_root = plugin_dir.parent
    results: list[tuple[str, str]] = []  # (name, "OK" | "SKIPPED" | error)

    for mod in modules:
        name = mod.get("name", "unknown")

        if skip_modules and name in skip_modules:
            output.info(f"Skipping module: {name}")
            results.append((name, "SKIPPED"))
            continue

        source = mod.get("source", name)
        module_dir = extensions_root / source

        # Refresh the module's installed files via copilot plugin update.
        # copilot plugin update only refreshes the named plugin, so sibling
        # module directories go stale unless explicitly updated.
        plugin_ref = f"{name}@copilot-extensions"
        try:
            r = subprocess.run(
                ["copilot", "plugin", "update", plugin_ref],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0:
                for line in r.stdout.strip().splitlines():
                    output.ok(line)
            else:
                output.warn(f"Plugin update for {name} returned non-zero "
                            f"(continuing with installed version)")
        except FileNotFoundError:
            output.warn("'copilot' CLI not found -- skipping plugin refresh")
        except subprocess.TimeoutExpired:
            output.warn(f"Plugin update for {name} timed out -- "
                        "continuing with installed version")

        if not module_dir.is_dir():
            output.warn(f"Module '{name}' source not found: {module_dir}")
            results.append((name, "source dir not found"))
            continue

        # Locate the platform installer (convention: scripts/install.{ps1,sh})
        if platform == "windows":
            installer = module_dir / "scripts" / "install.ps1"
        else:
            installer = module_dir / "scripts" / "install.sh"

        if not installer.exists():
            output.warn(f"Module '{name}' installer not found: {installer}")
            results.append((name, "installer not found"))
            continue

        # Determine shell prefix
        if platform == "windows":
            shell = shutil.which("pwsh") or shutil.which("powershell")
            if not shell:
                output.warn(f"Module '{name}': PowerShell not found")
                results.append((name, "powershell not found"))
                continue
            shell_prefix = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                            "-File", str(installer)]
        else:
            shell_prefix = ["bash", str(installer)]

        # Try update first; if it fails, fall back to install.
        # This is more robust than relying on the status command's exit
        # code, since the installed module scripts may be stale (only the
        # host plugin's files are refreshed by copilot plugin update).
        output.header(f"Updating Module: {name}")
        try:
            r = subprocess.run(
                [*shell_prefix, "update"],
                cwd=module_dir, timeout=300,
            )
            if r.returncode == 0:
                results.append((name, "OK"))
                continue
        except subprocess.TimeoutExpired:
            output.warn(f"Module '{name}' update timed out")
            results.append((name, "timed out"))
            continue
        except Exception as exc:
            output.warn(f"Module '{name}' update failed: {exc}")
            results.append((name, str(exc)))
            continue

        # Update failed -- attempt fresh install
        output.info(f"Module '{name}' update failed (not installed?), trying install...")
        try:
            r = subprocess.run(
                [*shell_prefix, "install"],
                cwd=module_dir, timeout=300,
            )
            if r.returncode == 0:
                results.append((name, "OK (installed)"))
            else:
                output.warn(f"Module '{name}' install exited with code {r.returncode}")
                results.append((name, f"install exited {r.returncode}"))
        except subprocess.TimeoutExpired:
            output.warn(f"Module '{name}' install timed out")
            results.append((name, "timed out"))
        except Exception as exc:
            output.warn(f"Module '{name}' install failed: {exc}")
            results.append((name, str(exc)))

    # Summary
    if results:
        output.header("Module Update Summary")
        for name, status in results:
            if status == "OK":
                output.ok(f"{name}")
            elif status == "SKIPPED":
                output.info(f"{name} (skipped)")
            else:
                output.warn(f"{name}: {status}")


def _find_installed_plugin_dir() -> Path | None:
    """Locate the agent-worktrees plugin in the Copilot CLI install tree.

    Checks the standard marketplace layout first, then the legacy
    ``_direct`` layout, and finally scans all subdirectories for a
    matching ``plugin.json``.
    """
    plugins_root = Path.home() / ".copilot" / "installed-plugins"

    # Primary: marketplace layout
    candidate = plugins_root / "copilot-extensions" / "agent-worktrees"
    if candidate.is_dir() and (candidate / "plugin.json").exists():
        return candidate

    # Legacy _direct layout (older Copilot CLI versions)
    direct = plugins_root / "_direct"
    if direct.is_dir():
        for d in direct.iterdir():
            if d.is_dir() and "agent-worktrees" in d.name:
                if (d / "plugin.json").exists():
                    return d

    # Fallback: scan everything
    if plugins_root.is_dir():
        for pj in plugins_root.rglob("plugin.json"):
            try:
                data = json.loads(pj.read_text(encoding="utf-8"))
                if data.get("name") == "agent-worktrees":
                    return pj.parent
            except Exception:
                continue

    return None


def cmd_deploy_instructions(args: argparse.Namespace) -> int:
    """Deploy machine + SSH instruction files from machines.yaml."""
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
    except FileNotFoundError:
        output.skipped("No machines.yaml found (optional)")
        _cleanup_stale_instructions(cfg.project_dir(project))
        return 0
    except ValueError as exc:
        output.err(f"Cannot load machines.yaml: {exc}")
        return 1

    if machine not in registry:
        output.err(f"Machine '{machine}' not found in machines.yaml")
        return 1

    proj_dir = cfg.project_dir(project)
    proj_dir.mkdir(parents=True, exist_ok=True)
    _deploy_copilot_instructions(
        proj_dir, registry[machine], project=project,
    )
    return 0


_GET_KEYS: dict[str, str] = {
    "repo-dir":      "Anchor repo directory",
    "worktree-dir":  "Worktree root directory",
    "src-dir":       "Source root (parent of repos)",
    "config-dir":    "Per-project config directory (~/.{project})",
    "machine":       "Machine name from config",
    "platform":      "Platform (win/wsl/linux)",
    "project":       "Project name",
    "pr-enabled":    "Whether PR mode is enabled (true/false)",
    "pr-provider":   "PR provider (gitea|github|azure-devops) when PR mode is on",
}


def cmd_get(args: argparse.Namespace) -> int:
    """Query project paths and config values -- machine-readable output."""
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
        "pr-enabled":    "true" if repo.pr.enabled else "false",
        "pr-provider":   repo.pr.provider if repo.pr.enabled else "",
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
# services -- discovery, staleness, and update
# ═══════════════════════════════════════════════════════════════════════════


def _resolve_environment(config: cfg.Config) -> str:
    """Build the environment key from config (e.g. ``myhost-wsl``)."""
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
        return ["bash", str(installer), *args]
    if installer.suffix == ".ps1":
        return ["pwsh", "-File", str(installer), *args]
    return None


def _service_is_installed(service: svc.ServiceInfo) -> bool:
    """Check if a service's install directory exists on disk."""
    if not service.install_dir:
        return False
    return Path(service.install_dir).exists()


# Worktree namespace verb -> canonical top-level command.
_WORKTREE_VERBS = {
    "create": "create",
    "create-system": "create-system",
    "remove-system": "remove-system",
    "list": "list",
    "status": "status",
    "push": "push-changes",
    "push-changes": "push-changes",
    "create-pr": "create-pr",
    "finalize": "finalize",
    "cleanup": "cleanup",
}


def _worktree_usage() -> None:
    out = sys.stderr
    print("Usage: <project> worktree <command> [args...]", file=out)
    print(file=out)
    print("Non-launching worktree lifecycle commands:", file=out)
    print("  create [--json]        Create a worktree; print id + dir (no launch)", file=out)
    print(
        "  create-system --name N [--owner O] [--json]  "
        "Create a daemon-owned worktree (hidden from Picker)", file=out)
    print(
        "  remove-system <id> [--json]  "
        "Tear down a system worktree by id", file=out)
    print("  list [--json]          List this project's worktrees", file=out)
    print("  status <id>            Show a worktree's git status", file=out)
    print("  push <id> [--title T]  Squash, rebase, and push to the default branch", file=out)
    print(
        "  create-pr [id] [--title T] [--branch B]  "
        "PR mode: squash + push a feature branch", file=out)
    print("  finalize [id]          Validate content on upstream and clean up", file=out)
    print("  cleanup                List and remove orphaned/finalized worktrees", file=out)


def cmd_worktree_dispatch(argv: list[str]) -> int:
    """Route ``worktree`` subcommands to the canonical lifecycle handlers.

    A discoverable, repo-mechanical alias over existing top-level commands
    (``create``/``list``/``status``/``push-changes``/``finalize``/``cleanup``)
    -- none of which launch Copilot. Existing top-level verbs keep working.
    """
    if not argv or argv[0] in ("-h", "--help", "help"):
        _worktree_usage()
        return 0 if argv and argv[0] in ("-h", "--help", "help") else 1

    verb = argv[0]
    canonical = _WORKTREE_VERBS.get(verb)
    if not canonical:
        output.err(f"Unknown worktree subcommand: {verb}")
        _worktree_usage()
        return 1

    parser = build_parser()
    try:
        args = parser.parse_args([canonical, *argv[1:]])
    except SystemExit as exc:
        return int(exc.code or 0)
    handler = COMMAND_MAP.get(args.command)
    if not handler:
        _worktree_usage()
        return 1
    return handler(args)


def cmd_services_dispatch(argv: list[str]) -> int:
    """Route services subcommands -- built-in aggregates or passthrough."""
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
    services = svc.discover_services(
        repo_dir, env,
        service_paths=config.default_repo.service_paths or None,
    )

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
                "auto_update": s.auto_update,
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
    services = svc.discover_services(
        repo_dir, env,
        service_paths=config.default_repo.service_paths or None,
    )

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


def _ensure_repo_current(repo_dir: Path, config: cfg.Config) -> None:
    """Pull latest commits into the anchor repo before deploying.

    When services are deployed from the anchor (the main clone, not a
    worktree), the anchor may be behind origin if commits were pushed
    from a worktree via ``git push origin HEAD:master``.  A fast-forward
    merge keeps the anchor in sync so installers copy the latest code.

    Worktrees are left alone -- they track their own branch.
    """
    # Worktrees have a .git *file*; the anchor has a .git *directory*
    git_path = repo_dir / ".git"
    if not git_path.is_dir():
        return  # worktree -- nothing to do

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
                "Anchor has local commits -- fast-forward failed. "
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

    # Determine the action before discovery -- the first positional arg
    action = action_args[0] if action_args else "status"
    if not action_args:
        action_args = ["status"]

    # Pull latest into anchor before deploying code
    if action in _DEPLOY_ACTIONS:
        _ensure_repo_current(repo_dir, config)

    env = _resolve_environment(config)
    services = svc.discover_services(
        repo_dir, env,
        service_paths=config.default_repo.service_paths or None,
    )

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

    # Stream output directly -- the installer owns the terminal
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
    services = svc.discover_services(
        repo_dir, env,
        service_paths=config.default_repo.service_paths or None,
    )

    force = "--force" in flags
    dry_run = "--dry-run" in flags
    # --dry-run is binstub-only; --force is used by the binstub for
    # staleness filtering AND forwarded to installers for config drift.
    pass_flags = [f for f in flags if f not in ("--dry-run",)]

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
            # VAV-owned services (extensions.agent-worktrees.auto_update:false)
            # are deployed by another owner; skip them in automatic update/
            # install sweeps. Explicit `services <name> <action>` still runs,
            # and `--force` overrides this.
            if action in ("install", "update") and not s.auto_update:
                output.skipped(f"{label} -- managed elsewhere (auto_update: false)")
                skipped += 1
                continue
            if action == "install" and is_installed:
                skipped += 1
                continue
            if action == "update":
                if st.staleness == "current":
                    skipped += 1
                    continue
                if not is_installed:
                    output.warn(f"{label} -- not installed, skipping update")
                    skipped += 1
                    continue

        if not s.installer_path:
            output.skipped(f"{label} -- no installer")
            skipped += 1
            continue

        installer = repo_dir / s.installer_path
        if not installer.exists():
            output.err(f"{label} -- installer missing at {installer}")
            errors += 1
            continue

        cmd_args = [action, *pass_flags]
        cmd = _installer_cmd(installer, cmd_args)
        if not cmd:
            output.err(f"{label} -- unknown installer type: {installer.suffix}")
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
# pre-launch -- two-pass declarative self-update protocol
# ═══════════════════════════════════════════════════════════════════════════
# Repos registry
# ═══════════════════════════════════════════════════════════════════════════


def _repos_usage() -> None:
    """Print repos subcommand usage."""
    project = cfg.project_name()
    print(f"Usage: {project} repos <command>")
    print()
    print("Commands:")
    print("  list [--class reference|singleton|worktree]   List known repositories")
    print("  find <name>                         Resolve a repo to its local path")
    print("  add <name> <path>                   Register a repo at a known path")
    print("     [--class C] [--remote URL] [--default-branch B]")
    print("     [--tags a,b] [--contributing PATH] [--agent|--no-agent]")
    print("  remove <name>                       Remove a repo from the registry")
    print("  clone <remote> [--name N]           Clone a repo to srcroot and register")
    print("     [--target PATH]")
    print("  srcroot [--set PATH]                Show or set the source root")
    print("     [--platform windows|wsl|linux]")
    print("  migrate [--default-class C]         Import legacy ~/.git-repos")
    print("  status [--tag T] [--class C]        Show branch/dirty/ahead-behind")
    print("  sync [--tag T] [--class C]          Fetch + fast-forward (skips dirty)")
    print()
    print("Repo classes:")
    print("  reference   read-only; resolve/clone/index only; never edited")
    print("  singleton   single anchor checkout; no worktree isolation")
    print("  worktree    full agent-worktrees lifecycle; concurrent-flow safe")
    print()
    print("Examples:")
    print(f"  {project} repos list")
    print(f"  {project} repos migrate")
    print(f"  {project} repos find dotfiles")
    print(f"  {project} repos add my-lib D:\\Src\\my-lib --class reference")
    print(f"  {project} repos sync --tag facility")


def cmd_repos_dispatch(argv: list[str]) -> int:
    """Route repos subcommands."""
    from . import repos

    if not argv or argv[0] in ("--help", "-h"):
        _repos_usage()
        return 0 if argv else 1

    sub = argv[0]
    rest = argv[1:]

    if sub == "list":
        class_filter = None
        for flag in ("--class", "--type"):
            if flag in rest:
                idx = rest.index(flag)
                if idx + 1 < len(rest):
                    class_filter = rest[idx + 1]
        json_out = "--json" in rest
        entries = repos.list_repos(class_filter=class_filter)
        if json_out:
            _json_output({
                "repos": [
                    {
                        "name": e.name,
                        "class": e.repo_class,
                        "remote": e.remote,
                        "default_branch": e.default_branch,
                        "tags": e.tags,
                        "contributing": e.contributing,
                        "agent": e.agent,
                        "paths": e.paths,
                    }
                    for e in entries
                ],
            })
        elif not entries:
            print("No repos registered.")
            print("Add one with: repos add <name> <path> --class <class>")
            print("Or import the legacy registry with: repos migrate")
        else:
            plat = repos._current_platform()
            output.header("Repos Registry")
            for e in entries:
                tag = f"[{e.repo_class}]" if e.agent else f"[{e.repo_class} no-agent]"
                local = e.local_path(plat) or "(no local path)"
                print(f"  {e.name:<25} {tag:<20} {local}")
                if e.remote:
                    print(f"  {'':25} {'':20} {e.remote}")
        return 0

    if sub == "find":
        if not rest:
            output.err("Usage: repos find <name>")
            return 1
        name = rest[0]
        json_out = "--json" in rest
        path = repos.resolve_path(name)
        if path:
            if json_out:
                _json_output({"name": name, "path": path})
            else:
                print(path)
            return 0
        else:
            entry = repos.find_repo(name)
            if entry and entry.remote:
                msg = f"Repo '{name}' has no local path. Clone with: repos clone {entry.remote}"
            else:
                msg = f"Repo '{name}' not found in registry"
            if json_out:
                return _json_error(msg)
            output.err(msg)
            return 1

    if sub == "add":
        if len(rest) < 2:
            output.err(
                "Usage: repos add <name> <path> "
                "[--class reference|singleton|worktree] [--remote URL] "
                "[--default-branch B] [--tags a,b] [--contributing PATH] "
                "[--agent|--no-agent]"
            )
            return 1
        name, path = rest[0], rest[1]
        rclass = "reference"
        remote = ""
        default_branch = ""
        tags: list[str] = []
        contributing = ""

        def _opt(flag: str) -> str | None:
            if flag in rest:
                idx = rest.index(flag)
                if idx + 1 < len(rest):
                    return rest[idx + 1]
            return None

        # --class is canonical; --type is a legacy alias.
        rclass = _opt("--class") or _opt("--type") or rclass
        remote = _opt("--remote") or remote
        default_branch = _opt("--default-branch") or default_branch
        contributing = _opt("--contributing") or contributing
        raw_tags = _opt("--tags")
        if raw_tags:
            tags = [t.strip() for t in raw_tags.split(",") if t.strip()]

        agent_flag: bool | None = None
        if "--no-agent" in rest:
            agent_flag = False
        elif "--agent" in rest:
            agent_flag = True

        repos.add_repo(
            name, path,
            repo_class=rclass,
            remote=remote,
            default_branch=default_branch,
            tags=tags,
            contributing=contributing,
            agent=agent_flag,
        )
        return 0

    if sub == "remove":
        if not rest:
            output.err("Usage: repos remove <name>")
            return 1
        if repos.remove_repo(rest[0]):
            return 0
        output.err(f"Repo '{rest[0]}' not found in registry")
        return 1

    if sub == "clone":
        if not rest:
            output.err("Usage: repos clone <remote> [--name N] [--target PATH]")
            return 1
        remote = rest[0]
        name = None
        target = None
        if "--name" in rest:
            idx = rest.index("--name")
            if idx + 1 < len(rest):
                name = rest[idx + 1]
        if "--target" in rest:
            idx = rest.index("--target")
            if idx + 1 < len(rest):
                target = rest[idx + 1]
        entry = repos.clone_repo(remote, name=name, target=target)
        return 0 if entry else 1

    if sub == "srcroot":
        plat_arg = None
        if "--platform" in rest:
            idx = rest.index("--platform")
            if idx + 1 < len(rest):
                plat_arg = rest[idx + 1]
        if "--set" in rest:
            idx = rest.index("--set")
            if idx + 1 < len(rest):
                repos.set_srcroot(rest[idx + 1], plat=plat_arg)
                return 0
            output.err("--set requires a path")
            return 1
        # Show current srcroot
        registry = repos.read_registry()
        if registry.srcroot:
            for p, v in sorted(registry.srcroot.items()):
                marker = " ←" if p == (plat_arg or repos._current_platform()) else ""
                print(f"  {p}: {v}{marker}")
        else:
            print("No source roots configured.")
            print("Set one with: repos srcroot --set <path>")
        return 0

    if sub == "migrate":
        default_class = "singleton"
        if "--default-class" in rest:
            idx = rest.index("--default-class")
            if idx + 1 < len(rest):
                default_class = rest[idx + 1]
        migrated, skipped = repos.migrate_git_repos(default_class=default_class)
        if migrated == 0 and skipped == 0:
            return 1
        output.ok(f"Migrated {migrated} repo(s) from ~/.git-repos "
                  f"({skipped} skipped) into repos.yaml")
        output.info("~/.git-repos was left in place; remove it once you have "
                    "verified the migration.")
        return 0

    if sub == "status":
        tag = None
        class_filter = None
        if "--tag" in rest:
            idx = rest.index("--tag")
            if idx + 1 < len(rest):
                tag = rest[idx + 1]
        for flag in ("--class", "--type"):
            if flag in rest:
                idx = rest.index(flag)
                if idx + 1 < len(rest):
                    class_filter = rest[idx + 1]
        json_out = "--json" in rest
        statuses = repos.status_all(tag=tag, class_filter=class_filter)
        if json_out:
            _json_output({
                "repos": [
                    {
                        "name": s.name, "class": s.repo_class,
                        "present": s.present, "branch": s.branch,
                        "dirty": s.dirty, "ahead": s.ahead,
                        "behind": s.behind, "path": s.path, "error": s.error,
                    }
                    for s in statuses
                ],
            })
            return 0
        if not statuses:
            print("No repos registered.")
            return 0
        output.header("Repos Status")
        for s in statuses:
            if not s.present:
                print(f"  {s.name:<25} [{s.repo_class:<9}] MISSING")
                continue
            flags = []
            if s.dirty:
                flags.append("dirty")
            if s.ahead:
                flags.append(f"+{s.ahead}")
            if s.behind:
                flags.append(f"-{s.behind}")
            state = ", ".join(flags) if flags else "clean"
            print(f"  {s.name:<25} [{s.repo_class:<9}] {s.branch:<18} {state}")
        return 0

    if sub == "sync":
        tag = None
        class_filter = None
        if "--tag" in rest:
            idx = rest.index("--tag")
            if idx + 1 < len(rest):
                tag = rest[idx + 1]
        for flag in ("--class", "--type"):
            if flag in rest:
                idx = rest.index(flag)
                if idx + 1 < len(rest):
                    class_filter = rest[idx + 1]
        results = repos.sync_all(tag=tag, class_filter=class_filter)
        if not results:
            print("No repos registered.")
            return 0
        output.header("Repos Sync")
        had_error = False
        for name, state, detail in results:
            if state == "synced":
                output.ok(f"{name}: {detail}")
            elif state in ("skipped", "missing"):
                output.info(f"{name}: {state} ({detail})")
            else:
                had_error = True
                output.err(f"{name}: {detail}")
        return 1 if had_error else 0

    output.err(f"Unknown repos subcommand: {sub}")
    _repos_usage()
    return 1


# ═══════════════════════════════════════════════════════════════════════════

# Bootstrap services that must be current before launching a session.
_BOOTSTRAP_SERVICES = ("agent-worktrees", "vault")


def cmd_pre_launch(args: argparse.Namespace) -> int:
    """Check bootstrap service staleness and return a JSON action plan.

    Returns JSON to stdout:
      {"action": "continue"}  -- all bootstrap services are current
      {"action": "self-update", "updates": [...]}  -- services need updating

    The shell wrapper interprets this JSON, runs the update commands,
    and re-invokes pre-launch (max 1 retry).
    """
    repo_dir = _find_repo_dir()
    if not repo_dir:
        # Can't determine staleness -- proceed anyway
        print(json.dumps({"action": "continue", "reason": "no-repo"}))
        return 0

    try:
        config = cfg.load_config()
    except Exception:
        print(json.dumps({"action": "continue", "reason": "no-config"}))
        return 0

    env = _resolve_environment(config)
    all_services = svc.discover_services(
        repo_dir, env,
        service_paths=config.default_repo.service_paths or None,
    )

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
                # Find the installer -- check manifest's installer_path first,
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

    On Windows, only ``.ps1`` installers are supported.  ``.sh`` installers
    are skipped to avoid invoking WSL (which can hang when unavailable).
    If an ``.sh`` installer is given on Windows, attempts to find a ``.ps1``
    sibling in the same directory.
    """
    if installer.suffix == ".sh":
        if platform.system() == "Windows":
            # Don't invoke WSL -- look for a .ps1 sibling instead
            ps1_sibling = installer.with_name("install.ps1")
            if ps1_sibling.exists():
                installer = ps1_sibling
            else:
                return None
        else:
            cmd = f"bash {installer} update"
            argv = ["bash", str(installer), "update"]
            return cmd, argv
    if installer.suffix == ".ps1":
        cmd = f"pwsh -File {installer} update"
        argv = ["pwsh", "-File", str(installer), "update"]
        return cmd, argv
    return None


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
      4. Config anchor (last resort -- may be stale)

    All paths are resolved through :func:`git_ops.resolve_to_anchor` so
    that running from inside a git worktree returns the main checkout,
    not the ephemeral worktree path.
    """

    # 1. Running script location -- walk up from __file__ to find .git
    #    Only useful when running from a dev checkout inside the repo.
    #    When installed (under ~/.agent-worktrees/), the walk would escape
    #    the install tree and hit unrelated git repos (e.g. a stray .git
    #    in $HOME).  Stop at the install dir boundary to prevent this.
    here = Path(__file__).resolve().parent
    _install_root = cfg.install_dir().resolve()
    candidate = here
    for _ in range(8):  # limit traversal depth
        if (candidate / ".git").exists() or (candidate / ".git").is_file():
            return git_ops.resolve_to_anchor(candidate)
        parent = candidate.parent
        if parent == candidate:
            break
        # Stop before escaping the install tree -- if our code lives
        # under ~/.agent-worktrees/, there's no project repo above it.
        if candidate == _install_root:
            break
        candidate = parent

    # 2. WORKTREE_REPO env (set by setup.sh during active sessions)
    env_repo = _env_get("WORKTREE_REPO")
    if env_repo:
        env_path = Path(env_repo)
        if env_path.is_dir():
            return git_ops.resolve_to_anchor(env_path)

    # 3. git rev-parse to find repo root of cwd
    try:
        r = subprocess.run(
            ["git", "-C", str(Path.cwd()), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return git_ops.resolve_to_anchor(Path(r.stdout.strip()))
    except Exception:
        pass

    # 4. Config anchor (last resort -- may deploy stale code if anchor
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
    project: str, default_branch: str = "master", *, headless: bool = False,
) -> None:
    """Write the project config YAML."""
    src_root = repo_dir.parent
    wt_root = f"{repo_dir}.worktrees"

    headless_line = "headless: true\n" if headless else ""
    content = f"""# ~/.{project}/config.yaml
# Machine-local configuration for worktree session management.

repo_name: {project}
srcroot: {src_root}
machine: {machine}
platform: {plat}
{headless_line}
repos:
  {project}:
    anchor: {repo_dir}
    # worktree_root defaults to {wt_root} -- a sibling
    # <anchor>.worktrees dir, matching Copilot CLI's /worktree layout.
    # Uncomment and set an absolute path to override.
    default_branch: {default_branch}
    remote: origin
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    output.changed(f"Written config: {path}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-worktrees",
        description="Worktree session manager (use --version for build info)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # resolve (emit JSON launch plan, then exit -- shell handles execution)
    p = sub.add_parser("resolve", help="Resolve launch plan as JSON (for shell wrappers)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--recovery", action="store_true")
    p.add_argument("--no-resume", action="store_true",
                   help="Don't auto-resume the last Copilot session")
    p.add_argument("--no-mux", action="store_true",
                   help="Bypass tmux/psmux multiplexer (launch directly)")
    p.add_argument("--no-fast-forward", action="store_true",
                   help="Don't auto-fast-forward a stale clean worktree on resume")
    p.add_argument("--json", action="store_true",
                   help="Non-interactive JSON mode (requires --worktree-id)")
    p.add_argument("--worktree-id", default=None,
                   help="Worktree ID to resolve (required with --json)")
    p.add_argument("--base", action="store_true",
                   help="Resolve for the anchor repo (no picker, no worktree)")
    p.add_argument("--auto", action="store_true",
                   help=argparse.SUPPRESS)  # deprecated alias for --new
    p.add_argument("--new", action="store_true", dest="new_worktree",
                   help="Create a new worktree (non-interactive, implies --no-mux)")
    p.add_argument("--profile", help="Copilot backend profile name (skips Tab toggle)")
    p.add_argument("--machine", default=None,
                   help="Target machine name (bypasses machine picker)")
    p.add_argument("copilot_args", nargs="*", default=[])

    # post-exit (run post-exit checks after Copilot exits)
    p = sub.add_parser("post-exit", help="Post-exit worktree checks (idempotent)")
    p.add_argument("worktree_id", nargs="?", default=None)

    # finalize
    p = sub.add_parser(
        "finalize",
        help="Validate the branch's content is on upstream; prune the worktree only when idle",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true",
                   help="JSON output mode (stdout is JSON only)")
    p.add_argument("--config", default=None)

    # push-changes
    p = sub.add_parser("push-changes", help="Push worktree changes to remote default branch")
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--title", default=None, help="Set worktree title")
    p.add_argument("--title-only", action="store_true",
                   help="Set title without pushing (worktree stays active)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--allow-unsquashed", action="store_true",
                   help="If the pre-squash step fails, push the individual "
                        "commits instead of aborting. Off by default -- a "
                        "squash failure must never silently push every commit "
                        "to the shared default branch (issue #783).")
    p.add_argument("--json", action="store_true",
                   help="JSON output mode (stdout is JSON only)")
    p.add_argument("--config", default=None)

    # create-pr (PR-workflow: squash, create + push feature branch)
    p = sub.add_parser(
        "create-pr",
        help="Squash worktree commits, create + push a feature branch for a PR",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--title", default=None,
                   help="Title for the squashed commit / PR slug")
    p.add_argument("--branch", default=None,
                   help="Override the generated feature branch name")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--json", action="store_true",
                   help="JSON output mode (stdout is JSON only)")
    p.add_argument("--config", default=None)

    # set-pr (record PR metadata from the provider sub-agent)
    p = sub.add_parser("set-pr", help="Record PR metadata (URL/number/state) on a worktree")
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--url", default=None, help="PR URL")
    p.add_argument("--number", type=int, default=None, help="PR number")
    p.add_argument("--state", default=None,
                   choices=["creating", "open", "merged", "closed"],
                   help="PR lifecycle state")
    p.add_argument("--provider", default=None, help="PR provider (gitea|github|azure-devops)")
    p.add_argument("--branch", default=None, help="Feature branch name (if not already recorded)")
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    # pr-status (read tracked PR metadata)
    p = sub.add_parser("pr-status", help="Show tracked PR metadata for a worktree")
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    # mark-complete (manual recovery only -- hidden from normal help)
    p = sub.add_parser(
        "mark-complete",
        help=argparse.SUPPRESS,
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--title", default=None)
    p.add_argument("--title-only", action="store_true")

    # status
    p = sub.add_parser("status", help="Show worktree git status")
    p.add_argument("--json", action="store_true")
    p.add_argument("--mux-details", action="store_true",
                   help="Include mux session attached/detached status (JSON only)")

    # list (lightweight inventory from tracking records)
    p = sub.add_parser("list", help="List worktrees from tracking records")
    p.add_argument("--json", action="store_true",
                   help="JSON output mode (stdout is JSON only)")
    p.add_argument("--mux-details", action="store_true",
                   help="Include mux session attached/detached status (JSON only)")
    p.add_argument("--tracking-status", default="all",
                   choices=["active", "complete", "finalized", "orphaned", "all"],
                   help="Filter by tracking status (default: all)")
    p.add_argument("--all", action="store_true",
                   help="Include worktrees whose directories no longer exist on disk")
    p.add_argument("--include-other-platforms", action="store_true",
                   help="Include worktrees from other platforms (e.g. Windows when on Linux)")

    # create (non-interactive worktree creation)
    p = sub.add_parser("create", help="Create a new worktree non-interactively")
    p.add_argument("--json", action="store_true",
                   help="JSON output mode (stdout is JSON only)")

    # create-system (daemon-owned worktree; hidden from the Picker)
    p = sub.add_parser("create-system",
                       help="Create a daemon-owned system worktree (hidden from Picker)")
    p.add_argument("--name", default=None,
                   help="Short slug for the worktree id (e.g. the service name)")
    p.add_argument("--owner", default=None,
                   help="Owning service name (recorded for the browse view)")
    p.add_argument("--json", action="store_true",
                   help="JSON output mode (stdout is JSON only)")

    # remove-system (tear down a system worktree by id)
    p = sub.add_parser("remove-system", help="Remove a system worktree by id")
    p.add_argument("worktree_id", help="Worktree id to remove")
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
    p.add_argument("--headless", action="store_true",
                   help="Adopt as a CLI-only project: the bare binstub lists "
                        "worktrees instead of launching an interactive session")
    p.add_argument("--no-agent", action="store_true",
                   help="Adopt without an agent-bridge agent (reference-style: "
                        "worktree-managed but no agent). Default is to expose one.")
    p.add_argument("--agent", action="store_true",
                   help="Force exposing an agent-bridge agent (overrides a "
                        "repos.yaml agent:false classification).")
    p.add_argument("--base-repo", action="store_true",
                   help="Adopt in base-repo (no-worktree) mode: the anchor "
                        "checkout is used directly and no worktree is created. "
                        "For repos that can't support worktrees (e.g. an "
                        "enlistment monorepo). Also set repos.<name>.base_repo "
                        "in the user-local ~/.<project>/config.yaml.")
    p.add_argument("--elevated", action="store_true",
                   help="Record that agent-bridge should run this project's "
                        "agent in an elevated (admin) context.")

    # uninstall
    p = sub.add_parser("uninstall", help="Remove worktree manager")
    p.add_argument("--remove-config", action="store_true")

    # update
    p = sub.add_parser("update", help="Re-deploy from repo")
    p.add_argument("--recreate-venv", action="store_true",
                   help="Force full venv recreation (cannot run from managed venv)")
    p.add_argument("--skip-modules", nargs="*", default=None,
                   metavar="MODULE",
                   help="Skip module updates (all if no names given, or named modules)")

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

    # services -- dispatched pre-argparse (see cmd_services_dispatch)
    # Stub entry for --help visibility only
    sub.add_parser("services", help="Service discovery and management (run 'services' for usage)")

    # repos -- dispatched pre-argparse (see cmd_repos_dispatch)
    sub.add_parser("repos", help="Repos registry and source roots (run 'repos' for usage)")

    # pre-launch (two-pass self-update protocol)
    sub.add_parser("pre-launch", help="Check bootstrap staleness (JSON output)")

    # reconcile-plugins (repo-configured plugin payload + runtime reconcile)
    sp = sub.add_parser(
        "reconcile-plugins",
        help="Reconcile repo enabledPlugins payloads + gated runtimes (JSON)")
    sp.add_argument("--machine", default=None,
                    help="Machine name (auto-detected from hostname if omitted)")
    sp.add_argument("--repo", default=None,
                    help="Repo path to reconcile (defaults to the resolved anchor)")

    # dev (repo development tooling)
    sp = sub.add_parser("dev", help="Dev venv and test runner")
    sp.add_argument("dev_action", nargs="?", default="status",
                    choices=["setup", "test", "status"],
                    help="Action: setup, test, or status")

    # register-session / deregister-session (called from hooks)
    sp = sub.add_parser("register-session",
                        help="Register a Copilot session against a worktree")
    sp.add_argument("--worktree-id", default=None,
                    help="Worktree ID (resolved from --cwd when omitted)")
    sp.add_argument("--session-id", default=None,
                    help="Copilot session ID (read from --stdin payload when omitted)")
    sp.add_argument("--cwd", default=None,
                    help="Session cwd, used to resolve the worktree when --worktree-id is absent")
    sp.add_argument("--stdin", action="store_true",
                    help="Read the Copilot sessionStart JSON payload from stdin")
    sp.add_argument("--pid", type=int, default=None,
                    help="PID of the Copilot process (diagnostic only)")

    sp = sub.add_parser("deregister-session",
                        help="Mark a Copilot session as ended on a worktree")
    sp.add_argument("--worktree-id", required=True, help="Worktree ID")
    sp.add_argument("--session-id", required=True, help="Copilot session ID")

    # backfill-sessions (one-time registry population)
    sub.add_parser("backfill-sessions",
                   help="Populate empty session registries from session-state data")

    # anchor-check (anchor repo hygiene)
    sp = sub.add_parser("anchor-check",
                        help="Check anchor repo for uncommitted work and stash entries")
    sp.add_argument("--json", action="store_true",
                    help="JSON output mode (stdout is JSON only)")
    sp.add_argument("--quiet", action="store_true",
                    help="Only print if issues are found")
    sp.add_argument("--strict", action="store_true",
                    help="Exit nonzero if anchor is not clean")
    sp.add_argument("--repo-path", default=None,
                    help="Path inside a repo (defaults to cwd)")

    # activity -- view the high-level worktree lifecycle log
    sp = sub.add_parser(
        "activity",
        help="View the worktree/session lifecycle activity log",
    )
    sp.add_argument("--since", default=None,
                    help="Only show events newer than this (e.g. 2d, 12h, "
                         "30m, or an ISO date). Default: all retained.")
    sp.add_argument("--worktree-id", default=None,
                    help="Filter to a single worktree id")
    sp.add_argument("--event", default=None,
                    help="Filter to a single event type")
    sp.add_argument("--lines", type=int, default=None,
                    help="Show only the most recent N events")
    sp.add_argument("--json", action="store_true",
                    help="Emit one JSON object per line instead of a table")

    # activity-log -- append a single event (launcher/hook hook-invoked)
    sp = sub.add_parser(
        "activity-log",
        help="Append one lifecycle event to the activity log (internal)",
    )
    sp.add_argument("event", help="Event name")
    sp.add_argument("--worktree-id", default=None)
    sp.add_argument("--session-id", default=None)
    sp.add_argument("--source", default="launcher")
    sp.add_argument("--field", action="append", default=[],
                    help="Extra context as key=value (repeatable)")

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


def _read_hook_stdin() -> dict | None:
    """Read and parse the Copilot hook JSON payload from stdin (best-effort).

    The Copilot CLI pipes a JSON object (sessionStart: ``{sessionId, cwd,
    source, ...}``) to the hook command's stdin.  Returns the parsed dict,
    or None when there is no payload / it isn't valid JSON.  Never raises.
    """
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return None
        raw = sys.stdin.read()
    except Exception:
        return None
    if not raw or not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def cmd_register_session(args: argparse.Namespace) -> int:
    """Register a Copilot session against a worktree (hook-invoked).

    Robust to the sessionStart hook environment, where
    ``COPILOT_AGENT_SESSION_ID`` is NOT reliably exported.  With
    ``--stdin`` the Copilot CLI's JSON payload is read from stdin and used
    to fill in any missing ``--session-id`` / ``--cwd``.  The worktree is
    resolved from ``--worktree-id`` or, failing that, from the cwd.

    Any "nothing to do" condition (no session id, or cwd not under a
    tracked worktree) is a silent success so the hook never surfaces an
    error to the user.
    """
    wt_id = getattr(args, "worktree_id", None)
    session_id = getattr(args, "session_id", None)
    cwd = getattr(args, "cwd", None)
    pid = getattr(args, "pid", None)

    if getattr(args, "stdin", False):
        payload = _read_hook_stdin()
        if payload:
            session_id = session_id or payload.get("sessionId")
            cwd = cwd or payload.get("cwd")

    # Last-resort env fallback (set for tool subprocesses, not the hook).
    if not session_id:
        session_id = os.environ.get("COPILOT_AGENT_SESSION_ID") or None
    if not session_id:
        return 0  # nothing to register -- silent no-op

    if not wt_id and cwd:
        wt_id = tracking.find_worktree_id_by_cwd(cwd)
    if not wt_id:
        return 0  # cwd isn't a tracked worktree (base repo / unrelated dir)

    try:
        tracking.register_session(wt_id, session_id, pid=pid)
    except Exception as e:
        output.err(f"Failed to register session: {e}")
        return 1
    activity.log_event(
        "session_started",
        worktree_id=wt_id,
        session_id=session_id,
    )
    return 0


def cmd_deregister_session(args: argparse.Namespace) -> int:
    """Mark a Copilot session as ended on a worktree (hook-invoked).

    Also captures the session summary/name from workspace.yaml and
    persists it to the tracking YAML ``title`` field (if not already
    set), ensuring the title survives session-state directory cleanup.
    """
    wt_id = getattr(args, "worktree_id", None)
    session_id = getattr(args, "session_id", None)
    if not wt_id or not session_id:
        output.err("Usage: deregister-session --worktree-id ID --session-id ID")
        return 1
    try:
        tracking.deregister_session(wt_id, session_id)
        # Capture session title from workspace.yaml → tracking YAML
        _capture_session_title(wt_id, session_id)
    except Exception as e:
        output.err(f"Failed to deregister session: {e}")
        return 1
    activity.log_event(
        "session_ended",
        worktree_id=wt_id,
        session_id=session_id,
    )
    return 0


def _capture_session_title(worktree_id: str, session_id: str) -> None:
    """Read summary/name from the session's workspace.yaml and persist it
    to the tracking YAML ``title`` field if not already set.

    This ensures the worktree retains a descriptive title even after the
    Copilot session-state directory is cleaned up.
    """
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return

    rec = tracking.load_record(yaml_path)
    if rec.title and rec.title != "null":
        return  # already has a title

    # Read summary/name from the session's workspace.yaml
    session_dir = sessions._session_state_dir() / session_id
    ws_file = session_dir / "workspace.yaml"
    if not ws_file.exists():
        return

    try:
        with open(ws_file, encoding="utf-8") as f:
            ws_data = yaml.safe_load(f)
    except Exception:
        return

    if not ws_data or not isinstance(ws_data, dict):
        return

    _placeholder = ("", "|-", "|", ">-", ">", "null", "Untitled")
    display_text = ""
    summary = ws_data.get("summary", "")
    if isinstance(summary, str) and summary.strip() and summary not in _placeholder:
        display_text = summary.strip()
    if not display_text:
        name = ws_data.get("name", "")
        if isinstance(name, str) and name.strip() and name not in _placeholder:
            display_text = name.strip()

    if display_text:
        rec.title = display_text
        tracking.save_record(rec)


def cmd_backfill_sessions(args: argparse.Namespace) -> int:
    """Populate empty session registries from existing session-state data."""
    tracking_path = cfg.tracking_dir()
    records = tracking.list_records(tracking_path)

    # Only backfill records with empty sessions lists
    need_backfill = [r for r in records if not r.sessions]
    if not need_backfill:
        output.ok("All worktree records already have session data")
        return 0

    print(f"Scanning session-state for {len(need_backfill)} worktree(s)...")
    discovered = sessions.backfill_sessions(need_backfill)

    updated = 0
    for rec in need_backfill:
        sids = discovered.get(rec.worktree_id, [])
        if not sids:
            # Mark as indexed (empty list) so we don't rescan
            if rec.sessions is None:
                rec.sessions = []
                tracking.save_record(rec)
                updated += 1
            continue

        rec.sessions = [
            tracking.SessionEntry(session_id=sid, started_at="")
            for sid in sids
        ]
        tracking.save_record(rec)
        updated += 1

    total_sessions = sum(len(v) for v in discovered.values())
    print(
        f"Backfilled {total_sessions} session(s) "
        f"across {len(discovered)} worktree(s) "
        f"({updated} record(s) updated)"
    )
    return 0


def cmd_reconcile_plugins(args: argparse.Namespace) -> int:
    """Reconcile repo-configured copilot-extensions plugins (JSON action plan).

    Reads the anchor repo's ``.github/copilot/settings.json`` ``enabledPlugins``
    and emits a declarative action plan (same shape as ``pre-launch``): ensure
    each plugin's payload is installed, and its runtime is deployed per the
    plugin's ``runtimeScope`` + facility machine gate. The launcher executes the
    ``argv`` vectors and re-invokes for a second pass (payload, then runtime).

    Never fails the launch: any error degrades to ``{"action": "continue"}``.
    """
    from . import reconcile

    repo_override = getattr(args, "repo", None)
    repo_dir = repo_override or _find_repo_dir()
    if not repo_dir:
        print(json.dumps({"action": "continue", "reason": "no-repo"}))
        return 0

    machine = getattr(args, "machine", None)
    try:
        plan = reconcile.build_plan(Path(repo_dir), machine=machine)
    except Exception as e:  # never break the launch
        print(json.dumps({"action": "continue", "reason": f"error: {e}"}))
        return 0

    print(json.dumps(plan))
    return 0


def cmd_anchor_check(args: argparse.Namespace) -> int:
    """Check anchor repo for uncommitted work and stash entries."""
    from . import anchor_hygiene

    repo_path = getattr(args, "repo_path", None) or os.getcwd()
    use_json = getattr(args, "json", False)
    quiet = getattr(args, "quiet", False)
    strict = getattr(args, "strict", False)

    try:
        report = anchor_hygiene.check_anchor(repo_path)
    except Exception as e:
        if use_json:
            json.dump({"version": 1, "error": str(e)}, sys.stdout)
            print()
        else:
            output.err(f"Anchor check failed: {e}")
        return 1

    if use_json:
        json.dump(anchor_hygiene.report_as_json(report), sys.stdout, indent=2)
        print()
    else:
        anchor_hygiene.report_anchor_state(report, quiet=quiet)

    if strict and not report.is_clean:
        return 1
    return 0


COMMAND_MAP = {
    "resolve": cmd_resolve,
    "post-exit": cmd_post_exit,
    "finalize": cmd_finalize,
    "push-changes": cmd_push_changes,
    "create-pr": cmd_create_pr,
    "set-pr": cmd_set_pr,
    "pr-status": cmd_pr_status,
    "mark-complete": cmd_mark_complete,
    "status": cmd_status,
    "list": cmd_list,
    "create": cmd_create,
    "create-system": cmd_create_system,
    "remove-system": cmd_remove_system,
    "cleanup": cmd_cleanup,
    "validate": cmd_validate,
    "install": cmd_install,
    "register": cmd_register,
    "unregister": cmd_uninstall,
    "uninstall": cmd_uninstall,
    "update": cmd_update,
    "install-status": cmd_install_status,
    "deploy-instructions": cmd_deploy_instructions,
    "get": cmd_get,
    "pre-launch": cmd_pre_launch,
    "reconcile-plugins": cmd_reconcile_plugins,
    "dev": cmd_dev,
    "register-session": cmd_register_session,
    "deregister-session": cmd_deregister_session,
    "backfill-sessions": cmd_backfill_sessions,
    "anchor-check": cmd_anchor_check,
    "activity": activity.cmd_activity,
    "activity-log": activity.cmd_activity_log,
}


def _print_boot_provenance() -> None:
    """Print extended boot provenance checks for migration verification."""
    home = Path.home()
    install = cfg.install_dir()
    checks: list[tuple[str, bool, str]] = []

    # 1. Runtime package identity
    pkg_dir = install / "lib" / "agent_worktrees"
    has_new = pkg_dir.is_dir()
    checks.append(("runtime", has_new,
                    f"agent_worktrees at {pkg_dir}" if has_new
                    else "agent_worktrees package NOT FOUND"))

    # 2. Old worktree_manager remnants
    old_pkg = install / "lib" / "worktree_manager"
    old_venv = install / ".venv"
    if platform.system() == "Windows":
        old_venv_pkg = old_venv / "Lib" / "site-packages" / "worktree_manager"
    else:
        # Find the python version dir dynamically
        old_venv_pkg = None
        sp = old_venv / "lib"
        if sp.is_dir():
            for child in sp.iterdir():
                cand = child / "site-packages" / "worktree_manager"
                if cand.is_dir():
                    old_venv_pkg = cand
                    break
        if old_venv_pkg is None:
            old_venv_pkg = old_venv / "lib" / "python3" / "site-packages" / "worktree_manager"
    has_old = old_pkg.is_dir() or old_venv_pkg.is_dir()
    checks.append(("no-legacy-pkg", not has_old,
                    "no worktree_manager remnants" if not has_old
                    else f"OLD package found: {old_pkg if old_pkg.is_dir() else old_venv_pkg}"))

    # 3. Plugin hook wired
    hook_found = False
    plugins_root = home / ".copilot" / "installed-plugins"
    if plugins_root.is_dir():
        for hooks_json in plugins_root.rglob("hooks.json"):
            try:
                data = json.loads(hooks_json.read_text(encoding="utf-8"))
                hooks = data.get("hooks", {})
                for hook_list in hooks.values():
                    if not isinstance(hook_list, list):
                        continue
                    for hook in hook_list:
                        cmd = (hook.get("powershell") or "") + (hook.get("bash") or "")
                        if "bootstrap-check" in cmd:
                            hook_found = True
                            break
            except Exception:
                pass
    checks.append(("session-hook", hook_found,
                    "bootstrap-check wired in sessionStart" if hook_found
                    else "sessionStart hook NOT FOUND"))

    # 4. Binstub resolution
    binstub_ok = False
    binstub_detail = "not found"
    project = cfg.project_name()
    if platform.system() == "Windows":
        binstub = home / ".local" / "bin" / f"{project}.cmd"
    else:
        binstub = home / ".local" / "bin" / project
    if binstub.is_file():
        content = binstub.read_text(errors="replace")
        if "agent_worktrees" in content or "agent-worktrees" in content:
            binstub_ok = True
            binstub_detail = f"routes through agent-worktrees ({binstub})"
        elif "worktree_manager" in content:
            binstub_detail = f"STILL routes through worktree_manager ({binstub})"
        else:
            binstub_detail = f"unknown routing ({binstub})"
    checks.append(("binstub", binstub_ok, binstub_detail))

    # 5. Deploy manifest consistency
    manifest_path = install / "deploy-manifest.json"
    manifest_ok = False
    manifest_detail = "not found"
    if manifest_path.is_file():
        try:
            m = json.loads(manifest_path.read_text())
            m_commit = ((m.get("source") or {}).get("commit") or m.get("commit") or "")[:10]
            try:
                from ._build_info import BUILD_INFO
                b_commit = (BUILD_INFO.get("commit") or "")[:10]
            except ImportError:
                b_commit = ""
            if m_commit and b_commit and m_commit == b_commit:
                manifest_ok = True
                manifest_detail = f"manifest commit {m_commit} matches build info"
            elif m_commit and b_commit:
                manifest_detail = f"MISMATCH: manifest={m_commit} build={b_commit}"
            else:
                manifest_ok = True
                manifest_detail = f"commit {m_commit or '?'}"
        except Exception as exc:
            manifest_detail = f"parse error: {exc}"
    checks.append(("manifest", manifest_ok, manifest_detail))

    # Print results
    print("")
    all_ok = True
    for name, ok, detail in checks:
        status = "[OK]" if ok else "[FAIL]"
        if not ok:
            all_ok = False
        print(f"  {status:6s} {name}: {detail}")
    print("")
    status = "PASS" if all_ok else "FAIL"
    detail = "verified" if all_ok else "has issues"
    print(f"  {status}: boot provenance {detail}")


def _extract_project_flag(args_list: list[str]) -> tuple[list[str], str | None]:
    """Pop a global --project/-p flag from args, returning (remaining, value).

    Supports ``--project NAME``, ``--project=NAME``, ``-p NAME``. Only the
    first occurrence is consumed; the rest pass through to the subcommand.
    """
    out: list[str] = []
    project: str | None = None
    i = 0
    while i < len(args_list):
        arg = args_list[i]
        if project is None and arg in ("--project", "-p"):
            if i + 1 < len(args_list):
                project = args_list[i + 1]
                i += 2
                continue
            i += 1
            continue
        if project is None and arg.startswith("--project="):
            project = arg.split("=", 1)[1]
            i += 1
            continue
        out.append(arg)
        i += 1
    return out, (project.strip() if project else None)


def _git_toplevel(path: Path) -> Path | None:
    """Return the git toplevel of ``path`` resolved to its anchor, or None."""
    try:
        r = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return git_ops.resolve_to_anchor(Path(r.stdout.strip()).resolve())
    except Exception:
        pass
    return None


# Commands that work without a project context (no load_config/project_name).
_NO_PROJECT_COMMANDS = {
    "--version", "-V", "--help", "-h", "repos", "install", "register", "hook",
}


def cmd_help_unrouted(requested: str | None = None) -> int:
    """Help shown when ``agent-worktrees`` runs without project context.

    Prints the grouped command catalog, explains why it balked, and
    recommends the most likely next step based on the current directory
    and the set of adopted projects.
    """
    out = sys.stderr
    print("agent-worktrees -- worktree session lifecycle manager", file=out)
    print(file=out)
    if requested:
        print(
            f"No project context for '{requested}'. You ran the generic "
            f"'agent-worktrees' binstub rather than a project binstub, and "
            f"no --project was given.",
            file=out,
        )
    else:
        print(
            "No project context. You ran the generic 'agent-worktrees' "
            "binstub rather than a project binstub (e.g. 'my-control-harness').",
            file=out,
        )
    print(file=out)

    print("Commands:", file=out)
    groups = [
        ("Worktree lifecycle",
         "worktree, create, list, status, push-changes, finalize, cleanup"),
        ("Project / install",
         "register, install, uninstall, update, install-status, get, validate"),
        ("Namespaces", "services ..., repos ..."),
        ("Diagnostics", "activity"),
        ("Info", "--version, --help"),
    ]
    for title, cmds in groups:
        print(f"  {title + ':':<22}{cmds}", file=out)
    print(file=out)

    # Ranked recommendation from cwd + adopted projects.
    try:
        projects = inst.read_projects_registry().get("projects", {})
    except Exception:
        projects = {}
    cwd_anchor = _git_toplevel(Path.cwd())

    matched: str | None = None
    if cwd_anchor is not None:
        cwd_norm = _normalize_path(str(cwd_anchor))
        for name, entry in projects.items():
            anchor = entry.get("anchor") if isinstance(entry, dict) else None
            if not anchor:
                continue
            if _normalize_path(str(Path(anchor).resolve())) == cwd_norm:
                matched = name
                break

    print("Recommended next step:", file=out)
    if matched:
        print(
            f"  You are inside the '{matched}' project. Run:\n"
            f"    {matched}                         # interactive picker\n"
            f"    agent-worktrees --project {matched} worktree list",
            file=out,
        )
    elif cwd_anchor is not None:
        print(
            f"  This git repo ({cwd_anchor.name}) is not adopted yet. Adopt it:\n"
            f"    agent-worktrees register {cwd_anchor.name}",
            file=out,
        )
    elif projects:
        names = ", ".join(sorted(projects))
        print(
            f"  Pick an adopted project (run its binstub or use --project):\n"
            f"    Adopted: {names}\n"
            f"    e.g. agent-worktrees --project {sorted(projects)[0]} worktree list",
            file=out,
        )
    else:
        print(
            "  No projects adopted yet. From inside a git repo, run:\n"
            "    agent-worktrees register <name>",
            file=out,
        )
    return 1


def _is_headless_project() -> bool:
    """Return True if the active project is configured headless (CLI-only)."""
    try:
        return cfg.load_config().headless
    except Exception:
        return False


def cmd_headless_bare() -> int:
    """Bare invocation of a headless project's binstub.

    Headless projects are driven via CLI and never launch an interactive
    Copilot session. Show the project's worktrees and the available
    lifecycle commands instead.
    """
    try:
        project = cfg.project_name()
    except Exception:
        project = "<project>"
    print(
        f"'{project}' is a headless (CLI-only) project -- it is driven via "
        f"worktree commands, not an interactive session.",
        file=sys.stderr,
    )
    print(file=sys.stderr)
    rc = cmd_worktree_dispatch(["list"])
    print(file=sys.stderr)
    print(f"Manage it with: {project} worktree <create|status|push|finalize|cleanup>",
          file=sys.stderr)
    return rc


def main(argv: list[str] | None = None) -> int:
    output.ensure_utf8_stdio()
    args_list = argv if argv is not None else sys.argv[1:]

    # ── Raw pre-dispatch ──────────────────────────────────────────────
    # Handle compatibility aliases and the default "launch" action
    # BEFORE argparse, which can't represent both CLI and launch modes.

    # Strip `agent-worktrees` prefix (SSH compat:
    #   `<project> agent-worktrees cleanup` → `cleanup`)
    if args_list and args_list[0] == "agent-worktrees":
        args_list = args_list[1:]

    # Global --project/-p flag: set WORKTREE_PROJECT in-process so any
    # subcommand works without the per-project binstub.
    args_list, _proj = _extract_project_flag(args_list)
    if _proj:
        os.environ["WORKTREE_PROJECT"] = _proj
        # #25: an explicit --project selects a project that is, in general,
        # *different* from the worktree the caller's session is running in.
        # The inherited WORKTREE_ID / APERTURE_WORKTREE_ID belong to that
        # caller's session, not to this project, so blank them and let
        # worktree-id resolution fall back to the current working directory.
        # (Bare `agent-worktrees <cmd>` with no --project still inherits the
        # session's WORKTREE_ID -- that is the intended "operate on my current
        # worktree" path.)
        os.environ.pop("WORKTREE_ID", None)
        os.environ.pop("APERTURE_WORKTREE_ID", None)

    has_project = bool(os.environ.get("WORKTREE_PROJECT", "").strip())

    # No args → launch (with project) or helpful balk (without).
    if not args_list:
        if has_project:
            if _is_headless_project():
                return cmd_headless_bare()
            return cmd_launch([])
        return cmd_help_unrouted()

    # A project-requiring subcommand without any project context → balk
    # helpfully instead of raising a bare RuntimeError deep in load_config.
    if not has_project and args_list[0] not in _NO_PROJECT_COMMANDS \
            and not args_list[0].startswith("-"):
        return cmd_help_unrouted(requested=args_list[0])

    # --version / -V → print version + build info + boot provenance
    if args_list[0] in ("--version", "-V"):
        try:
            from ._build_info import BUILD_INFO
        except ImportError:
            BUILD_INFO = {"version": "?.?.?", "commit": "unknown",
                          "build_timestamp": "unknown"}
        v = BUILD_INFO.get("version", "?.?.?")
        c = BUILD_INFO.get("commit", "unknown")[:10]
        ts = BUILD_INFO.get("build_timestamp", "unknown")
        br = BUILD_INFO.get("branch", "unknown")
        print(f"agent-worktrees {v}  commit {c}  branch {br}  built {ts}")
        # Also show deploy manifest if available
        manifest_path = cfg.install_dir() / "deploy-manifest.json"
        if manifest_path.exists():
            try:
                m = json.loads(manifest_path.read_text())
                dep_at = m.get("deployed_at", "?")
                dirty = " (DIRTY)" if m.get("dirty") else ""
                src = m.get("plugin_source", "?")
                print(f"deployed {dep_at}{dirty}  source {src}")
            except Exception:
                pass

        # --version --source: extended boot provenance checks
        if len(args_list) > 1 and args_list[1] in ("--source", "--check"):
            _print_boot_provenance()

        return 0

    # --help / -h → show argparse help (not launch fallthrough)
    if args_list[0] in ("--help", "-h"):
        parser = build_parser()
        parser.print_help()
        return 0

    # Services uses manual dispatch for passthrough support --
    # argparse can't handle "unknown subcommand = service name".
    if args_list[0] == "services":
        try:
            return cmd_services_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # Repos uses manual dispatch for subcommand flexibility.
    if args_list[0] == "repos":
        try:
            return cmd_repos_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # Worktree namespace -- groups the non-launching lifecycle verbs as a
    # discoverable alias over the existing top-level commands.
    if args_list[0] == "worktree":
        try:
            return cmd_worktree_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # Hook guardrails (manual dispatch: hook name + git passthrough args).
    if args_list[0] == "hook":
        from . import hooks as _hooks
        name = args_list[1] if len(args_list) > 1 else ""
        return _hooks.run_hook(name, args_list[2:])

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

    # Reject unrecognized bare-word subcommands -- only --flags pass
    # through to the launch flow.  Without this guard, typos and
    # non-existent namespaces (e.g. "worktrees") silently fall into
    # cmd_launch -> resolve, which may spawn an unwanted worktree.
    if not args_list[0].startswith("-"):
        output.err(f"Unknown subcommand: {args_list[0]}")
        output.err("Run 'agent-worktrees --help' for available commands.")
        return 1

    # Anything else (flags like --recovery, --no-update, or unknown) →
    # default launch with passthrough
    return cmd_launch(args_list)


if __name__ == "__main__":
    sys.exit(main())
