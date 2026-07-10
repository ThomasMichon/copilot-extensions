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
    agent-worktrees create [--json]       # programmatic: make a worktree, no launch
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
import threading
import time
from datetime import datetime
from pathlib import Path

import yaml

from . import activity, git_ops, output, permissions, pr_ops, procs, prune, sessions, tracking
from . import config as cfg
from . import finalize as fin
from . import installer as inst
from . import services as svc
from . import validate as val
from .picker import ItemKind, MenuItem, pick
from .update_stage import cmd_stage_update

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


def _classify_records(
    records: list[tracking.WorktreeRecord],
    session_ctx: sessions.SessionContext | None = None,
) -> dict[str, git_ops.WorktreeStateInfo]:
    """Classify each worktree's git state, keyed by worktree id.

    Mirrors the cleanup/picker classification loop so ``list --json --classify``
    emits the same ``state`` the status segment and picker use -- including the
    session-derived ``CONVO`` refinement of ``UNUSED`` (a clean, commit-less
    worktree whose session held conversation turns), applied here via
    :func:`git_ops.refine_state_with_session` when a ``session_ctx`` is given.
    Classification runs **where git access exists** -- so a remote machine
    carries its own worktree states in ``list --json`` over SSH (the local
    picker cannot git-classify a remote worktree). No fetch (``behind`` reflects
    the last fetch); ~5 git calls per existing worktree, hence opt-in.
    """
    config = cfg.load_config()
    repo = config.default_repo
    active_paths = _build_active_paths(records, session_ctx)
    out: dict[str, git_ops.WorktreeStateInfo] = {}
    for rec in records:
        if rec.worktree_path and Path(rec.worktree_path).exists():
            info = git_ops.classify_worktree(
                rec.worktree_path, rec.branch,
                fetch=False, remote=repo.remote,
                default_branch=repo.default_branch, active_paths=active_paths,
            )
            info = _apply_tracking_override(rec, info)
        elif rec.status == "finalized":
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
        else:
            info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.GONE)
        # Layer the session-derived CONVO refinement so this data contract
        # reports the same display state the tmux status bar does.
        if session_ctx is not None:
            turns = session_ctx.turn_count.get(
                _normalize_path(rec.worktree_path), 0,
            )
            if turns:
                info = dataclasses.replace(
                    info,
                    state=git_ops.refine_state_with_session(info.state, turns),
                )
        out[rec.worktree_id] = info
    return out


def _make_pr_lookup(config):
    """Build a ``lookup(repo, number) -> PullResult|None`` over the configured
    provider, for prune PR-state reconciliation. Returns None-yielding on any
    error so reconciliation is best-effort (keeps the local state)."""
    from . import providers

    prcfg = config.default_repo.pr
    try:
        token = providers.resolve_token(prcfg)
    except Exception:
        token = None
    api_base = getattr(prcfg, "api_base", "") or ""

    def lookup(repo, number):
        try:
            provider = providers.get_provider(prcfg.provider)
            return provider.get_pull(repo, number, api_base=api_base, token=token)
        except Exception:
            return None

    return lookup


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


def _sync_status_tag(info: git_ops.WorktreeStateInfo) -> str:
    """Build the picker's inline sync tag (``↑ahead`` / ``↓behind``).

    Surfaces stale worktrees (``↓N``) at a glance so they can be updated
    before resuming.  Counts reflect the last fetch.

    For a COMPLETED worktree the ahead-count is misleading: its content is
    already on the default branch (git-cherry / blob comparison confirmed
    it), but a squash-merge leaves the local branch carrying the pre-squash
    commits, so the raw ``ahead`` stays > 0.  Suppress the ``↑ahead`` half
    there so a merged-but-not-yet-cleaned worktree no longer renders as
    diverged (#1106).
    """
    show_ahead = bool(info.ahead) and info.state != git_ops.WorktreeState.COMPLETED
    if show_ahead and info.behind:
        return f" ↑{info.ahead}↓{info.behind}"
    if info.behind:
        return f" ↓{info.behind}"
    if show_ahead:
        return f" ↑{info.ahead}"
    return ""


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
    if rec.kind in tracking.MANAGED_KINDS:
        d["kind"] = rec.kind
        if rec.owner:
            d["owner"] = rec.owner
    # #2178: expose the bridge caller-worktree pointer so the Picker can offer
    # "Jump to caller" from a bridge worktree.
    if rec.caller_worktree:
        d["caller_worktree"] = rec.caller_worktree
    if state_info is not None:
        d["state"] = state_info.state.value
        d["ahead"] = state_info.ahead
        d["behind"] = state_info.behind
        d["dirty"] = state_info.dirty
        if state_info.branch_drift and state_info.current_branch:
            d["current_branch"] = state_info.current_branch
            d["branch_drift"] = True
        # Authoritative maintenance hints (single source of truth: prune.py +
        # git_ops.can_fast_forward), so the picker's Cleanup/Sync scope dialogs
        # never re-derive eligibility from display heuristics. The bucket is
        # flag-independent; the executor still re-checks safety per worktree.
        _turns = (
            session_ctx.turn_count.get(_normalize_path(rec.worktree_path), 0)
            if session_ctx is not None else 0
        )
        d["cleanup_bucket"] = prune.cleanup_disposition(
            rec, state_info, turn_count=_turns).bucket
        d["ff_eligible"] = (
            git_ops.can_fast_forward(state_info)
            and state_info.state != git_ops.WorktreeState.ACTIVE
        )
    if mux_info is not None:
        d["mux_session"] = mux_info.exists
        d["mux_clients"] = mux_info.clients
        d["mux_attached"] = mux_info.attached
    if session_ctx is not None:
        norm = _normalize_path(rec.worktree_path)
        d["turn_count"] = session_ctx.turn_count.get(norm, 0)
        d["session_count"] = session_ctx.session_count.get(norm, 0)
        # Overall-summary slot: prefer the persisted title (curated by
        # finalize/PR or captured by the status-updater/deregister hook), but
        # fall back to the live session summary so a worktree whose title has
        # not been persisted yet still reads meaningfully instead of
        # "(untitled)".  Mirrors the fallback used by the status/list paths.
        if not (d.get("title") and d["title"] != "null"):
            summary = session_ctx.latest_summary.get(norm)
            if summary:
                d["title"] = summary
    # PR metadata: the active PR (back-compat ``pr``) plus the full list and a
    # count so consumers can see serial/parallel PRs at a glance.
    if rec.prs:
        active = rec.active_pr()
        d["pr"] = pr_ops._pr_to_dict(active) if active is not None else None
        d["prs"] = [pr_ops._pr_to_dict(p) for p in rec.prs]
        d["pr_count"] = len(rec.prs)
    return d


def _create_worktree_core(
    config: cfg.Config,
    *,
    profile: cfg.CopilotProfile | None = None,
    no_mux: bool = False,
    kind: tracking.WorktreeKind = "session",
    owner: str | None = None,
    name: str | None = None,
    parent_session: str | None = None,
    caller_worktree: str | None = None,
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
        # #1029: link the new worktree back to the session that spawned it, so a
        # later resume (esp. a PR/feedback worktree with no sessions of its own)
        # restores context instead of cold-starting.
        parent_session=(parent_session
                        or os.environ.get("COPILOT_AGENT_SESSION_ID") or None),
        # #2178: for a bridge spawn, record the caller worktree so the Picker can
        # jump back to it.
        caller_worktree=caller_worktree or None,
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
    env = _build_env(profile, _repo_session_env(config, worktree_path))

    return {
        "worktree": _worktree_to_dict(record),
        "launch": {
            "action": "exec",
            "work_dir": worktree_path,
            "cmd": launch_cmd,
            "env": env,
            "worktree_id": worktree_id,
            "post_exit": True,
            "no_mux": no_mux,
        },
    }


def _build_env(
    profile: cfg.CopilotProfile | None,
    session_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build env dict with auto-injected vars, repo session_env, then profile.

    Convention-based vars (like COPILOT_CUSTOM_INSTRUCTIONS_DIRS) are set
    first, then the repo's ``session_env`` (e.g. COPILOT_FEATURE_FLAGS), then
    profile env merges on top.  For path-list vars like
    COPILOT_CUSTOM_INSTRUCTIONS_DIRS, profile values are appended rather
    than replacing the auto-injected value.
    """
    env: dict[str, str] = {}

    # Auto-inject: dynamic instructions live in ~/.{project}
    project_dir = str(cfg.project_dir())
    env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] = project_dir

    # Repo-declared session env (below the profile so a profile can override).
    if session_env:
        env.update(session_env)

    # Merge profile env, appending for path-list keys
    if profile and profile.env:
        _PATH_LIST_KEYS = {"COPILOT_CUSTOM_INSTRUCTIONS_DIRS"}
        for k, v in profile.env.items():
            if k in _PATH_LIST_KEYS and k in env:
                env[k] = env[k] + os.pathsep + v
            else:
                env[k] = v

    return env


def _repo_session_env(config: cfg.Config, work_dir: str = "") -> dict[str, str]:
    """The default repo's ``session_env``, with values templated.

    Values may reference ``{work_dir}``, ``{anchor}``, ``{machine}``,
    ``{repo_name}``, and ``{home}`` -- so a repo can express a per-machine path
    (e.g. ``SUDO_ASKPASS: "{home}/.local/bin/vault-askpass"``) portably. A value
    with an unrecognized placeholder is passed through unchanged rather than
    raising.
    """
    try:
        raw = config.default_repo.session_env
    except Exception:
        return {}
    if not raw:
        return {}
    variables = {
        "work_dir": work_dir,
        "anchor": config.default_repo.anchor,
        "machine": config.machine,
        "repo_name": config.repo_name,
        "home": os.path.expanduser("~"),
    }
    out: dict[str, str] = {}
    for k, v in raw.items():
        try:
            out[k] = v.format(**variables)
        except (KeyError, IndexError, ValueError):
            out[k] = v
    return out


def _build_launch_cmd(
    config: cfg.Config,
    args: argparse.Namespace,
    work_dir: str,
    profile: cfg.CopilotProfile | None = None,
) -> list[str]:
    """Build the launch command from config or fallback convention.

    If the repo config has ``launch`` / ``launch_recovery`` entries for
    the current platform, those are used with variable substitution.
    Otherwise, in precedence order: a repo ``setup_hook`` selects the
    **normalized** launch (the default-setup launcher runs the repo hook, then
    execs Copilot); else a legacy ``tools/setup/setup.{ps1,sh}`` is run as the
    session command; else the plugin's ``default-setup.{ps1,sh}``.
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
        # No config-driven launch template. Three sub-cases, in precedence:
        #   1. NORMALIZED: repo declares a setup_hook -> the default-setup
        #      launcher runs the hook (context by arg, not env), then execs
        #      Copilot. This inverts the legacy setup.ps1-as-launch flow.
        #   2. LEGACY: repo ships tools/setup/setup.{ps1,sh} -> run it as the
        #      session command (it execs Copilot itself). Unchanged behavior.
        #   3. DEFAULT: neither -> the plugin's default-setup launcher.
        # Resolve from the anchor repo so a worktree pinned to an older commit
        # still picks up the latest setup script (anchor is fetched pre-launch).
        anchor = repo.anchor
        variables = {
            "work_dir": work_dir,
            "anchor": anchor,
            "machine": config.machine,
            "repo_name": config.repo_name,
        }
        session_dirs = [
            d.format(**variables) for d in repo.session_path.get(plat_key, [])
        ]
        session_path_arg = os.pathsep.join(session_dirs) if session_dirs else ""
        hook_path = repo.setup_hook.get(plat_key)
        is_windows = platform.system() == "Windows"

        if hook_path:
            # (1) Normalized launch via the default-setup launcher + repo hook.
            resolved_hook = hook_path.format(**variables)
            if not os.path.isabs(resolved_hook):
                resolved_hook = str(Path(anchor) / resolved_hook)
            if is_windows:
                launcher = str(inst.install_dir() / "scripts" / "default-setup.ps1")
                cmd = [
                    "pwsh.exe", "-NoProfile", "-NoLogo", "-File",
                    launcher, "-Machine", config.machine,
                    "-SetupHook", resolved_hook,
                ]
                if session_path_arg:
                    cmd += ["-SessionPath", session_path_arg]
                if recovery:
                    cmd.append("-Recovery")
            else:
                launcher = str(inst.install_dir() / "scripts" / "default-setup.sh")
                cmd = [
                    "bash", launcher, "--machine", config.machine,
                    "--setup-hook", resolved_hook,
                ]
                if session_path_arg:
                    cmd += ["--session-path", session_path_arg]
                if recovery:
                    cmd.append("--recovery")
        elif is_windows:
            setup_path = str(Path(anchor) / "tools" / "setup" / "setup.ps1")
            legacy = Path(setup_path).is_file()
            if not legacy:
                setup_path = str(inst.install_dir() / "scripts" / "default-setup.ps1")
            cmd = [
                "pwsh.exe", "-NoProfile", "-NoLogo", "-File",
                setup_path, "-Machine", config.machine,
            ]
            # session_path is only understood by the default-setup launcher;
            # never pass it to a legacy setup.ps1 (unknown params would leak
            # through to Copilot as bogus args).
            if session_path_arg and not legacy:
                cmd += ["-SessionPath", session_path_arg]
            if recovery:
                cmd.append("-Recovery")
        else:
            setup_path = str(Path(anchor) / "tools" / "setup" / "setup.sh")
            legacy = Path(setup_path).is_file()
            if not legacy:
                setup_path = str(inst.install_dir() / "scripts" / "default-setup.sh")
            cmd = ["bash", setup_path, "--machine", config.machine]
            if session_path_arg and not legacy:
                cmd += ["--session-path", session_path_arg]
            if recovery:
                cmd.append("--recovery")

    extra = getattr(args, "copilot_args", []) or []
    cmd.extend(extra)

    # Append profile-specific Copilot args
    profile_args = profile.copilot_args if profile and profile.copilot_args else []
    cmd.extend(profile_args)

    # Auto-approve tools so worktree sessions run without per-tool
    # confirmation prompts.  Skip ACP sessions (agent-bridge manages
    # permissions over the protocol) and never duplicate an
    # all-permissions flag the caller already supplied.
    passthrough = list(extra) + list(profile_args)
    if "--acp" not in passthrough and not any(
        a == flag
        for a in passthrough
        for flag in ("--allow-all-tools", "--allow-all", "--yolo")
    ):
        cmd.append("--allow-all-tools")

    return cmd


def cmd_handoff_cutover(args: argparse.Namespace) -> int:
    """Live-cutover handoff: spawn a seeded successor Copilot or retire a pane.

    Two modes (JSON out on stdout either way):

    * **spawn** (default; needs ``--seed``): reconstruct this worktree's launch
      command (the same ``_build_launch_cmd`` the picker uses) **plus** a
      trailing ``-i <seed>`` -- an *interactive* seeded first turn, never ``-p``
      (which would run headless and exit) -- then open + select a NEW window in
      the worktree's ``wt-<id>`` mux session so the operator is cut over to the
      successor. Deliberately omits ``--resume``: a handoff wants a FRESH context
      window seeded by the prompt, not the old transcript replayed. Returns the
      OLD (pre-cutover) pane id so the caller can retire it once the old session
      reaches agent-stop.
    * **retire** (``--retire-pane <id>``): double-Ctrl-C that specific pane
      (Copilot's native clean quit), hard-killing it only if it will not exit.

    The mux choreography lives here (agent-worktrees owns launch + mux); the
    context-handoff extension is a thin trigger that shells out to this command.
    """
    # ── Retire mode ──────────────────────────────────────────────────────
    retire_pane = getattr(args, "retire_pane", None)
    if retire_pane:
        result = sessions.mux_retire_pane(retire_pane)
        _json_output(result)
        return 0 if result.get("ok") else 1

    # ── Spawn / cutover mode ─────────────────────────────────────────────
    seed = getattr(args, "seed", None)
    if not seed:
        return _json_error("handoff-cutover requires --seed (or --retire-pane)")

    raw_id = getattr(args, "worktree_id", None)
    if raw_id:
        wt_id = _resolve_worktree_id(raw_id)
    else:
        wt_id = _infer_worktree_id_from_cwd()
        if not wt_id:
            return _json_error(
                "could not resolve a worktree id from cwd; pass --worktree-id",
                exit_code=2,
            )

    # A live cutover needs a mux session to cut into. Without one, the caller
    # (extension) must fall back to the store-task-and-reply flow.
    if not sessions.has_mux_session(wt_id):
        return _json_error(f"no mux session wt-{wt_id}; not under mux", exit_code=3)

    try:
        config = cfg.load_config()
    except Exception as e:
        return _json_error(str(e))

    yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not yaml_path.exists():
        return _json_error(f"Worktree not found: {wt_id}")
    record = tracking.load_record(yaml_path)

    launch_cmd = _build_launch_cmd(config, args, record.worktree_path)
    env = _build_env(None, _repo_session_env(config, record.worktree_path))
    # Seed the successor's first interactive turn (never --resume: fresh context).
    launch_cmd = list(launch_cmd) + ["-i", seed]

    # Capture the pane to retire (the operator's current Copilot) BEFORE adding
    # the new window, which would become the active pane. ``--old-pane`` lets the
    # extension pin its own $TMUX_PANE explicitly.
    old_pane = getattr(args, "old_pane", None) or sessions.mux_active_pane(wt_id)

    if getattr(args, "dry_run", False):
        _json_output({
            "ok": True, "dry_run": True, "session": f"wt-{wt_id}",
            "old_pane": old_pane, "work_dir": record.worktree_path,
            "cmd": launch_cmd,
        })
        return 0

    result = sessions.mux_new_window(
        wt_id, record.worktree_path, launch_cmd, env,
    )
    if not result.get("ok"):
        return _json_error(
            f"failed to open successor window: {result.get('error')}",
            exit_code=4,
        )

    _json_output({
        "ok": True,
        "session": f"wt-{wt_id}",
        "old_pane": old_pane,
        "new_pane": result.get("new_pane"),
        "seed_len": len(seed),
    })
    return 0


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
    worktree.  Used by agent-bridge for non-interactive SSH sessions and by
    the picker's cross-env "New worktree" handoff. ``--new`` gets a muxed
    session unless ``--no-mux`` is passed (agent-bridge passes it; it also
    uses ``--json``, which forces ``--no-mux``).

    When stdin is not a TTY and no worktree is specified, resolve errors out
    instead of running the picker.  Note ``--new`` on its own still launches a
    *muxed interactive* session, so it is refused without a TTY: an agent
    running non-interactively cannot attach to the tmux/psmux session and would
    leak a terminal.  Programmatic callers (agents, daemons) should instead use
    ``agent-worktrees create [--json]`` -- it creates a worktree and prints its
    id + path WITHOUT launching Copilot or a mux session -- then resume later
    with ``--json --worktree-id <id>``.
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

    # Guard: refuse a muxed ``--new`` launch when there is no TTY.  ``--new``
    # creates a worktree AND launches an *interactive* (tmux/psmux) session, so
    # an agent that discovers ``<project> --new`` and runs it from inside a tool
    # call (no controlling terminal) would spawn a detached, un-attachable mux
    # session plus a stray terminal process -- the exact misuse this blocks.
    # There is no legitimate non-TTY muxed ``--new``: the picker's cross-env
    # handoff runs it over ``ssh -t`` (a TTY is present) and agent-bridge passes
    # ``--no-mux`` / ``--json`` (which force clean stdio).  Point the caller at
    # the programmatic command instead.
    if (use_new and not use_json and not use_base
            and not getattr(args, "no_mux", False)
            and not sys.stdin.isatty()):
        output.err("Refusing '--new' without a TTY: it launches an interactive "
                   "tmux/psmux session that a non-interactive caller cannot "
                   "attach to (and would leak a terminal + mux session).")
        output.err("To create a worktree programmatically (no launch, no mux):")
        output.err("    agent-worktrees create --json")
        output.err("Then start Copilot in the returned path, or resume later:")
        output.err("    agent-worktrees resolve --json --worktree-id <id>")
        return 2

    # NOTE: ``--new`` does NOT force ``--no-mux``. A new worktree gets a muxed
    # session like a resume (the cross-env/cross-machine "New worktree" picker
    # handoff runs ``<project> --new`` over ``ssh -t`` and wants tmux/psmux just
    # like a local launch). Callers that need clean stdio pass ``--no-mux``
    # explicitly (agent-bridge does, and also uses ``--json`` which forces it).

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
            env = _build_env(None, _repo_session_env(config, work_dir))

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
                        kind="bridge" if getattr(args, "bridge", False)
                        else "session",
                        parent_session=getattr(args, "parent_session", None),
                        caller_worktree=getattr(args, "caller_worktree", None),
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
            env = _build_env(None, _repo_session_env(config, record.worktree_path))

            # Auto-resume session
            no_resume = getattr(args, "no_resume", False)
            if not no_resume:
                last_session = sessions.find_latest_session_id_fast(
                    record.worktree_path, record.sessions,
                )
                if not last_session:
                    # #1029: no session of its own -- fall back to the originating
                    # session so a PR/feedback worktree resumes with context.
                    last_session = sessions.validate_session_id(record.parent_session)
                if last_session:
                    # copilot's --resume[=value] is an optional-value option;
                    # the id MUST be attached with '=' or it is treated as a
                    # stray operand ("unknown command").
                    launch_cmd.append(f"--resume={last_session}")

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

        # Non-interactive: without a TTY the picker can't run. Steer
        # programmatic callers to ``create`` (no launch, no mux) rather than
        # ``--new`` (which launches a muxed interactive session).
        if not use_new and not sys.stdin.isatty():
            output.err("No TTY detected and no worktree specified.")
            output.err("To create a worktree programmatically (no launch, no "
                       "tmux/psmux session):")
            output.err("    agent-worktrees create --json")
            output.err("To resume an existing worktree non-interactively:")
            output.err("    agent-worktrees resolve --json --worktree-id <id>")
            output.err("Run 'agent-worktrees list' to see available worktrees.")
            return 1

        # Resume a specific worktree by ID without the picker. Two callers:
        #   * agent-bridge SSH session-roll -- passes ``--no-mux`` (clean stdio
        #     for ACP) explicitly, so it does not rely on a forced default.
        #   * the TUI picker's cross-machine "Open" handoff -- runs
        #     ``<project> --worktree-id <id>`` over ``ssh -t`` to launch the
        #     remote worktree's session *interactively, with the normal mux*.
        # So respect the actual ``--no-mux`` flag here rather than forcing it;
        # an interactive open gets a muxed session like a local launch.
        wt_id_noninteractive = getattr(args, "worktree_id", None)
        if wt_id_noninteractive:
            wt_id_noninteractive = _resolve_worktree_id(wt_id_noninteractive)
            yaml_path = cfg.tracking_dir() / f"{wt_id_noninteractive}.yaml"
            if not yaml_path.exists():
                output.err(f"Worktree not found: {wt_id_noninteractive}")
                return 1
            record = tracking.load_record(yaml_path)
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

        # Textual picker -- the DEFAULT everywhere (no opt-in). A machine can
        # opt out to the legacy ANSI picker below via `picker disable`
        # (new_picker: false) or the AGENT_WORKTREES_LEGACY_PICKER rollback env;
        # Windows-over-SSH auto-falls-back (_new_picker_blocked_by_ssh).
        from . import picker_tui
        if picker_tui.new_picker_enabled(config) and not _new_picker_blocked_by_ssh():
            return _run_new_picker(config, args)

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
                and r.kind not in tracking.MANAGED_KINDS  # agent-owned; hidden
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
                sync_tag = _sync_status_tag(info)

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
    records = [r for r in records if r.kind not in tracking.MANAGED_KINDS]
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
            # A worktree with a still-open PR is never reapable, even when its
            # current HEAD's content is on master (a sibling PR merged): the
            # open PR is still in review and its branch is the recovery source.
            if not rec.has_live_pr():
                cleanable.append((rec, info))
        elif info.state == git_ops.WorktreeState.GONE:
            if not rec.has_live_pr() and (not rec.branch or git_ops.is_branch_merged(
                rec.branch, upstream, cwd=repo.anchor,
            )):
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
    # System/bridge worktrees are recreated fresh per run; never FF them here.
    records = [r for r in records if r.kind not in tracking.MANAGED_KINDS]

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
    records = [r for r in tracking.list_records(tracking_path)
               if r.kind in tracking.MANAGED_KINDS]
    records = [r for r in records if r.repo == config.repo_name]

    if not records:
        _system_pause("No system worktrees.")
        return None

    active_paths = _build_active_paths(records)

    while True:
        records = [
            r for r in tracking.list_records(tracking_path)
            if r.kind in tracking.MANAGED_KINDS and r.repo == config.repo_name
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
        warn = (
            "  ⚠ has a LIVE session -- removing may disrupt a running daemon"
            if sel_live else ""
        )
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

    # Don't offer cross-machine handoffs from inside an SSH session -- it would
    # be a double hop. Show only this host's worktrees.
    if _in_ssh_session():
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


def _new_picker_blocked_by_ssh() -> bool:
    """The Textual picker can't read the keyboard over Windows OpenSSH.

    Textual's Windows input driver reads key events via
    ``ReadConsoleInputW(GetStdHandle(STD_INPUT_HANDLE))`` (see
    ``textual/drivers/win32.py``); those records are not delivered through the
    Windows OpenSSH ConPTY input path, so the TUI renders but is completely
    unresponsive to the keyboard. Linux/WSL over SSH is unaffected (the Unix
    driver reads the pty directly via ``os.read``). So over SSH **on Windows**
    we fall back to the legacy ANSI picker, whose ``msvcrt`` input works over
    the ConPTY (it's what the fleet has used over SSH all along).
    """
    return _in_ssh_session() and cfg.detect_platform() == "windows"


# Picker env labels (engine: "Win" | "WSL" | "Linux") -> machines.yaml ssh
# environment names.
_ENV_LABEL_TO_NAME = {"win": "windows", "wsl": "wsl", "linux": "linux"}


def _in_ssh_session() -> bool:
    """True when this process was reached over SSH.

    Used to avoid offering cross-machine handoffs from inside an SSH session
    (which would create a confusing double hop). ``SSH_CONNECTION`` is set by
    both OpenSSH on Linux and the Windows OpenSSH server; ``SSH_TTY`` /
    ``SSH_CLIENT`` are checked as fallbacks.
    """
    return bool(
        os.environ.get("SSH_CONNECTION")
        or os.environ.get("SSH_TTY")
        or os.environ.get("SSH_CLIENT")
    )


def _emit_remote_plan_for_env(
    config: cfg.Config,
    machine_display: str,
    env_label: str,
    remote_args: list[str] | None = None,
) -> int | None:
    """Emit a remote SSH handoff plan for a specific machine **and env**.

    The TUI picker labels a target by ``machines.yaml`` display name *and* env
    ("Lambda-Core WSL"). The legacy ``_try_machine_handoff`` only knows the
    machine and resolves the machine's *primary* alias via
    ``_resolve_ssh_alias`` -- so picking "Lambda-Core WSL" on a Windows host
    would hand off to the Windows alias (SSHing the host back into itself and
    hanging). This resolves the **env-specific** alias instead.

    ``remote_args`` are appended to the project binstub in the remote command,
    so the remote launches **straight through** into the chosen action instead
    of re-opening its own picker. For example ``["--worktree-id", "<id>"]``
    resumes that worktree interactively on the far side; ``["--new"]`` creates
    one there. Without them the remote just opens its picker (the old
    behavior).

    Returns an exit code if the plan was emitted, or ``None`` if the machine /
    env could not be resolved (caller should error).
    """
    repo = config.default_repo
    try:
        entries = cfg.load_machines_yaml(repo.anchor)
    except (FileNotFoundError, ValueError):
        return None

    key = _machine_key_for_display(config, machine_display)
    entry = entries.get(key)
    if entry is None:
        nl = (machine_display or "").lower()
        for k, e in entries.items():
            if (
                k.lower() == nl
                or e.display_name.lower() == nl
                or (e.alias and e.alias.lower() == nl)
            ):
                entry, key = e, k
                break
    if entry is None or not entry.ssh_environments:
        return None

    want = _ENV_LABEL_TO_NAME.get((env_label or "").lower())
    ssh_alias = ""
    if want:
        for e in entry.ssh_environments:
            if e.name == want:
                ssh_alias = e.alias
                break
    if not ssh_alias:
        # No env match -- fall back to the machine's primary alias.
        ssh_alias = _resolve_ssh_alias(entry)

    project = cfg.project_name()
    # The remote runs this string under its login shell (ssh -t alias "<cmd>");
    # worktree ids and flags are shell-safe tokens, so a simple join is fine.
    remote_command = " ".join([project, *remote_args]) if remote_args else project
    display = f"{entry.display_name} {env_label}".strip()
    _emit_plan({
        "action": "remote",
        "ssh_alias": ssh_alias,
        "remote_command": remote_command,
        "machine": entry.key,
        "display_name": display,
    })
    return 0


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
    merged_env = _build_env(profile, _repo_session_env(config, repo.anchor))
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


def _machine_key_for_display(config: cfg.Config, name: str) -> str:
    """Resolve a picker machine label (display name / key / alias) to its key.

    The TUI labels machines by ``machines.yaml`` display name; the SSH-handoff
    helpers match by key or alias. Returns *name* unchanged if no entry matches.
    """
    repo = config.default_repo
    try:
        entries = cfg.load_machines_yaml(repo.anchor)
    except (FileNotFoundError, ValueError):
        return name
    nl = name.lower()
    for key, entry in entries.items():
        if (key.lower() == nl
                or (entry.alias and entry.alias.lower() == nl)
                or entry.display_name.lower() == nl):
            return key
    return name


def _run_new_picker(config: cfg.Config, args: argparse.Namespace) -> int:
    """Run the Textual worktree picker and resolve its launch decision.

    Maps the picker's decision dict onto the existing resume/create/remote
    code paths. Cleanup/Sync/Stop/profiles actions run **for real** in-TUI
    (they mutate worktrees / terminal profiles) and never emit a launch
    decision, so they never reach here. (Their simulated no-op counterparts run
    only in the explicit ``picker mock`` dev sandbox.)
    """
    from . import picker_tui

    # Reap orphaned mux sessions (finalized / gone / untracked) so a dead
    # worktree is never presented as a live, resumable session (issue #713).
    # Run it on a background thread so the mux enumeration never delays the
    # picker appearing -- interaction must not wait on startup housekeeping
    # (#1432). Best-effort: a reap hiccup never touches the picker.
    def _reap_bg():
        try:
            reap_orphan_mux_sessions()
        except Exception:
            pass
    threading.Thread(target=_reap_bg, name="reap-orphans", daemon=True).start()

    # Avoid a confusing double hop: when this picker is itself running over SSH,
    # don't fan out to other machines -- show only the local source (no remote
    # tabs / handoffs). See issue: "a process should know it's accessed via SSH."
    live = not _in_ssh_session()
    decision = picker_tui.run_tui_picker(live=live)
    if not decision:
        print("Cancelled.")
        _emit_plan({"action": "none", "exit_code": 0})
        return 0

    action = decision.get("action")
    profile = _resolve_profile(config, args)

    if action == "refresh":
        # The picker's refresh icon (#1430): apply the staged update and
        # relaunch. The picker runs from the runtime venv the update replaces,
        # so it can't apply in place -- hand back to the launcher, which applies
        # then re-execs resolve on the new version.
        _emit_plan({"action": "refresh"})
        return 0

    # A selection on another machine hands off over SSH. The picker's target
    # carries machine *and* env, so resolve the env-specific SSH alias (not the
    # machine's primary -- see ``_emit_remote_plan_for_env``). The selected
    # action is forwarded as binstub args so the remote launches **straight
    # through** into that worktree's interactive session instead of re-opening
    # its own picker (resume -> ``--worktree-id <id>``; new -> ``--new``).
    if not decision.get("is_local", True):
        machine = decision.get("machine") or ""
        env_label = decision.get("env") or ""
        opts = decision.get("options") or {}
        remote_args: list[str] = []
        if action == "resume":
            wt_id = decision.get("worktree_id")
            if wt_id:
                remote_args = ["--worktree-id", str(wt_id)]
                if opts.get("no_mux"):
                    remote_args.append("--no-mux")
        elif action == "new":
            remote_args = ["--new"]
            if opts.get("no_mux"):
                remote_args.append("--no-mux")
        # Other actions (or a resume with no id) fall back to opening the
        # remote picker (empty remote_args).
        rc = _emit_remote_plan_for_env(config, machine, env_label, remote_args)
        if rc is not None:
            return rc
        output.err(
            f"Unknown or unreachable remote machine: {machine} {env_label}".strip()
        )
        return 1

    if action == "resume":
        wt_id = decision.get("worktree_id")
        if not wt_id:
            output.err("Picker returned a resume decision with no worktree id.")
            return 1
        # The Open sub-menu's No-mux toggle (picker #1343) launches without the
        # PSMux/TMux wrapper.
        if (decision.get("options") or {}).get("no_mux"):
            args.no_mux = True
        wt_id = _resolve_worktree_id(wt_id)
        yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
        if not yaml_path.exists():
            output.err(f"Worktree not found: {wt_id}")
            return 1
        record = tracking.load_record(yaml_path)
        return _resolve_resume(record, config, args, profile=profile)

    if action == "new":
        opts = decision.get("options") or {}
        if opts.get("no_mux"):
            args.no_mux = True
        if opts.get("anchor"):
            return _resolve_base_repo(config, args, profile=profile)
        # 'bare' and 'local_model' have no backend yet -- ignored for now.
        return _resolve_new(config, args, profile=profile)

    output.err(f"Picker returned an unsupported decision: {action!r}")
    return 1


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
    merged_env = _build_env(profile, _repo_session_env(config, record.worktree_path))

    # Auto-resume: find the most recent Copilot session for this worktree
    # and pass --resume=<session-id> so the user picks up where they left off.
    no_resume = getattr(args, "no_resume", False)
    if not no_resume:
        last_session = sessions.find_latest_session_id_fast(
            record.worktree_path, record.sessions,
        )
        if not last_session:
            # #1029: no session of its own -- fall back to the originating
            # session so a PR/feedback worktree resumes with context.
            last_session = sessions.validate_session_id(record.parent_session)
        if last_session:
            # copilot's --resume[=value] is an optional-value option; the id
            # MUST be attached with '=' or it is treated as a stray operand
            # ("unknown command").
            launch_cmd.append(f"--resume={last_session}")
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
        merged_env = _build_env(profile, _repo_session_env(config, worktree_path))
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
        parent_session=getattr(args, "parent_session", None),
        caller_worktree=getattr(args, "caller_worktree", None),
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
    """Return the worktree ID from an explicit arg or the current directory.

    Resolution order:
      1. Explicit value passed on the CLI
      2. The current working directory under the configured ``worktree_root``

    Identity is resolved **purely from the directory**, the way git resolves
    its repo. Ambient ``$WORKTREE_ID`` / ``$APERTURE_WORKTREE_ID`` are **not**
    consulted -- they were the source of cross-session/cross-repo contamination.
    Git branch is likewise never used: worktrees may switch to feature branches,
    so the branch name is not a reliable indicator of which worktree we are in.

    When ``--project`` targets a project the caller is not already inside,
    ``main()`` has ``chdir``-ed to that project's anchor -- which is not under
    ``worktree_root`` -- so cross-project calls yield ``None`` (name the worktree
    explicitly). When the caller *is* inside one of the project's worktrees, the
    real CWD identifies it.

    Returns None if neither source yields a worktree ID.
    """
    if explicit:
        return explicit

    return _infer_worktree_id_from_cwd(config)


def _infer_worktree_id_from_cwd(
    config: cfg.Config | None = None,
) -> str | None:
    """Derive worktree ID from the current working directory.

    If the CWD sits directly inside ``worktree_root``, the first path component
    under that root is the worktree ID.  Validated against the tracking
    directory to avoid false positives.
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

def _sweep_orphans_on_exit() -> None:
    """Best-effort idle-gated orphan-mux sweep at the *session-end* boundary
    (#713/#2149).

    agent-worktrees runs **no persistent monitor process**. Orphaned mux+Copilot
    sessions of finalized/gone worktrees are reaped on a cadence at the two
    natural lifecycle boundaries instead: on picker *launch* (the sweep in
    :func:`_run_new_picker`) and here, when a session *ends*. Both reuse the same
    idle-gated predicate in :func:`reap_orphan_mux_sessions` -- an attached,
    system-owned, still-active, or recently-busy session is always spared, so a
    worktree finalized-from-inside while its Copilot is still working is never
    killed. This closes the "reaped only when you next open the picker" gap
    without a daemon or scheduled task. Never raises.
    """
    try:
        payload = reap_orphan_mux_sessions()
        reaped = payload.get("reaped") or []
        if reaped:
            output.ok(
                f"Reaped {len(reaped)} idle orphan mux session(s): "
                f"{', '.join(reaped)}"
            )
    except Exception:
        pass


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
        _sweep_orphans_on_exit()
        return 0

    try:
        record = tracking.load_record(yaml_path)
    except Exception as e:
        output.err(f"Failed to load record {worktree_id}: {e}")
        return 1

    # Already finalized -- nothing to finalize, but still sweep idle orphans at
    # this session-end boundary (#713/#2149).
    if record.status == "finalized":
        output.ok(f"Worktree {worktree_id} already finalized.")
        rc = 0
    else:
        rc = _post_exit_gate(record, config)

    _sweep_orphans_on_exit()
    return rc


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

        body = getattr(args, "body", None)
        body_file = getattr(args, "body_file", None)
        if body_file:
            try:
                body = Path(body_file).read_text(encoding="utf-8")
            except OSError as e:
                msg = f"Could not read --body-file '{body_file}': {e}"
                return _json_error(msg) if use_json else (output.err(msg) or 1)

        result = pr_ops.create_pr(
            worktree_id, config,
            title=args.title,
            branch=args.branch,
            target_repo=getattr(args, "repo", None),
            new=getattr(args, "new", False),
            body=body,
            open_pr=(False if getattr(args, "no_open", False) else None),
            hold=getattr(args, "hold", False),
            attribution=(not getattr(args, "no_attribution", False)),
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
            if result.get("pr_opened"):
                output.ok(
                    f"Opened PR #{result.get('number')} via '{provider}': "
                    f"{result.get('url')}"
                )
                if result.get("held"):
                    output.warn(
                        f"PR #{result.get('number')} opened HELD (do-not-merge). "
                        "Run 'agent-worktrees pr-ready' to release it for merge."
                    )
                if result.get("pr_label_error"):
                    output.warn(
                        f"PR opened, but a label did not apply: "
                        f"{result.get('pr_label_error')}. Re-apply the label(s) "
                        f"via the '{provider}' provider."
                    )
            elif result.get("pr_open_error"):
                output.warn(
                    f"Branch pushed, but auto-open failed: "
                    f"{result.get('pr_open_error')}"
                )
                print(
                    f"Open the PR via the '{provider}' provider, then record it:\n"
                    f"  agent-worktrees set-pr {worktree_id} --url <URL> --number <N>"
                )
            else:
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
        select_number=getattr(args, "pr", None),
        select_branch=getattr(args, "select_branch", None),
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


def cmd_pr_ready(args: argparse.Namespace) -> int:
    """Release a held PR by removing the merge-only hold label."""
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

        result = pr_ops.pr_ready(
            worktree_id, config,
            target_repo=getattr(args, "repo", None),
            pr_number=getattr(args, "pr", None),
        )
        if use_json:
            _json_output(result)
        elif result.get("success"):
            output.ok(
                f"Released PR #{result.get('number')} for merge "
                f"({result.get('repo')}): {result.get('url')}"
            )
        else:
            output.err(result.get("error", "pr-ready failed."))
        return 0 if result.get("success") else 1
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


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

    result = pr_ops.pr_status(
        worktree_id, all_prs=getattr(args, "all", False), config=config,
    )
    if use_json:
        _json_output(result)
        return 0 if result.get("has_pr") or "error" not in result else 1
    if result.get("error"):
        output.err(result["error"])
        return 1
    if not result.get("has_pr"):
        print(f"{worktree_id}: no PR recorded (direct-push or not yet created).")
        return 0
    count = result.get("pr_count", 1)
    print(f"PR for {worktree_id} (active of {count}):")
    print(f"  state:    {result.get('state')}")
    print(f"  branch:   {result.get('branch')}")
    print(f"  number:   {result.get('number')}")
    print(f"  url:      {result.get('url')}")
    print(f"  provider: {result.get('provider')}")
    if result.get("repo"):
        print(f"  repo:     {result.get('repo')}")
    if getattr(args, "all", False) and result.get("prs"):
        print(f"  all PRs ({count}):")
        for p in result["prs"]:
            num = f"#{p['number']}" if p.get("number") else "(unnumbered)"
            print(f"    - {num} [{p.get('state')}] {p.get('branch')}")
    if result.get("pull_forward_recommended"):
        print()
        output.warn("Pull-forward recommended (active PR merged):")
        print(f"  {result.get('next_action')}")
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
# status-segment -- one styled line for a tmux/psmux status bar
# ═══════════════════════════════════════════════════════════════════════════

# Git state -> (256-color background, short label) for the status-bar block.
# CONVO is the session-derived refinement of UNUSED (see
# git_ops.refine_state_with_session): a clean, commit-less worktree whose
# session held conversation turns reads as a distinct teal block, not grey
# UNUSED.  Both the status bar and `list --json --classify` resolve to this
# same WorktreeState set.
_SEGMENT_STYLE: dict[git_ops.WorktreeState, tuple[str, str]] = {
    git_ops.WorktreeState.DIRTY:     ("colour160", "DIRTY"),   # red
    git_ops.WorktreeState.WIP:       ("colour178", "WIP"),     # amber
    git_ops.WorktreeState.COMPLETED: ("colour034", "FINAL"),   # green
    git_ops.WorktreeState.UNUSED:    ("colour244", "UNUSED"),  # grey
    git_ops.WorktreeState.CONVO:     ("colour037", "CONVO"),   # teal
    git_ops.WorktreeState.ORPHAN:    ("colour129", "ORPHAN"),  # magenta
    git_ops.WorktreeState.ACTIVE:    ("colour039", "ACTIVE"),  # blue
    git_ops.WorktreeState.GONE:      ("colour238", "GONE"),    # dark grey
    git_ops.WorktreeState.UNKNOWN:   ("colour238", "?"),       # dark grey
}

_SEGMENT_TITLE_MAX = 48


def _find_record_for_path(path: str) -> tracking.WorktreeRecord | None:
    """Return the tracking record whose worktree path matches ``path``."""
    try:
        norm = _normalize_path(path)
        for r in tracking.list_records(cfg.tracking_dir()):
            if r.worktree_path and _normalize_path(r.worktree_path) == norm:
                return r
    except Exception:
        pass
    return None


def _detect_upstream_branch(
    path: str, remote: str, config_default: str | None,
) -> str | None:
    """Detect the repo's upstream default branch (``main``/``master``/...).

    The status segment runs in arbitrary repos, so it cannot trust the
    ambient project config's default branch (e.g. a ``master`` project
    binstub polling a ``main`` repo).  Resolution order:

    1. The config default, if ``<remote>/<default>`` actually exists.
    2. ``<remote>/HEAD`` symbolic ref (the remote's own default).
    3. First of ``main`` / ``master`` that exists as a remote branch.
    4. The config default as a last-resort hint (may be stale).
    """
    def _has(ref: str) -> bool:
        r = git_ops.git("rev-parse", "--verify", "--quiet", ref,
                        cwd=path, check=False)
        return r.returncode == 0

    if config_default and _has(f"{remote}/{config_default}"):
        return config_default

    head = git_ops.git("symbolic-ref", f"refs/remotes/{remote}/HEAD",
                        cwd=path, check=False)
    if head.returncode == 0 and head.stdout.strip():
        return head.stdout.strip().rsplit("/", 1)[-1]

    for cand in ("main", "master"):
        if _has(f"{remote}/{cand}"):
            return cand

    return config_default


def _resolve_segment_title(
    rec: tracking.WorktreeRecord | None,
    path: str,
    info: git_ops.WorktreeStateInfo,
    ctx: sessions.SessionContext | None = None,
) -> str:
    """Resolve a worktree's display title cheaply (single-record scan).

    Priority: explicit tracking title -> latest session summary -> last
    commit subject.  Returns "" when nothing is available.  Truncated to
    keep the status bar readable.  Pass a precomputed ``ctx`` (from
    :func:`sessions.scan_sessions_fast`) to avoid a second scan.
    """
    title = ""
    if rec and rec.title and rec.title != "null":
        title = rec.title
    if not title and rec is not None:
        try:
            if ctx is None:
                ctx = sessions.scan_sessions_fast([rec])
            title = ctx.latest_summary.get(_normalize_path(path), "") or ""
        except Exception:
            title = ""
    if not title:
        title = info.title or ""
    if len(title) > _SEGMENT_TITLE_MAX:
        title = title[: _SEGMENT_TITLE_MAX - 1].rstrip() + "\u2026"
    return title


def _persist_segment_title(
    rec: tracking.WorktreeRecord,
    path: str,
    ctx: sessions.SessionContext | None,
) -> None:
    """Persist the live session overall-summary into the worktree's ``title``.

    The ``title`` field is the single slot the Picker reads, so the
    status-updater -- which already resolves the title every tick -- lands it
    there instead of only painting the mux status bar.  This keeps the overall
    summary alive after the Copilot session-state directory is cleaned up
    (when the live ``latest_summary`` is no longer derivable).

    Distinct from the live "latest action" disposition (DIRTY/WIP/CONVO),
    which stays ephemeral in ``@aw_seg`` -- this only persists the slow,
    overall summary.

    Only the session summary is persisted (never the commit-subject fallback,
    which would lock in a poor title), and a finalized/completed worktree's
    curated PR/squash title is left untouched.  A no-op when nothing changed,
    so per-tick writes don't churn the YAML.
    """
    if ctx is None:
        return
    if (rec.status or "").lower() in ("finalized", "complete", "completed"):
        return  # curated title -- don't clobber
    summary = ctx.latest_summary.get(_normalize_path(path), "")
    if not summary or summary == "null":
        return
    if (rec.title or "") == summary:
        return
    try:
        rec.title = summary
        tracking.save_record(rec)
    except Exception:
        pass


def _render_status_segment(
    path: str | None = None,
    fetch: bool = False,
    plain: bool = False,
    no_title: bool = False,
    persist_title: bool = False,
) -> str:
    """Render one styled status-bar segment for the worktree at the path/cwd.

    Returns the segment string (empty outside a git worktree).  The
    ``status-updater`` loop calls this in-process to refresh a session's
    ``@aw_seg`` option; ``cmd_status_segment`` is the thin print wrapper.

    Historically polled directly from a multiplexer status line::

        set -g status-right '#(agent-worktrees status-segment)'

    -- but that spawns a process per render, which psmux runs synchronously
    in the paint path (no #() caching like tmux), tanking responsiveness.
    The status bar now reads a precomputed ``#{@aw_seg}`` instead, refreshed
    off the paint path by ``status-updater``.

    Classifies the worktree's git disposition relative to its upstream
    default branch -- independent of any live session -- and prints::

        <title> #[bg=<color>] <STATE><sync> #[default]

    States: ``DIRTY`` (uncommitted changes or commits ahead of upstream),
    ``FINAL`` (clean, work landed / fast-forwardable to upstream),
    ``UNUSED`` (clean, no work and no conversation since the fork point),
    ``CONVO`` (clean, no commits but the session held conversation turns --
    annotated with the turn count), ``WIP`` (clean, commits ahead whose
    content is not yet upstream), ``ORPHAN`` (no merge base with upstream).
    ``<sync>`` is the picker's ``↑ahead``/``↓behind`` tag.

    Fetch-free by default so it is cheap enough to poll on a short
    ``status-interval``; pass ``--fetch`` to refresh behind-counts from the
    remote.  Prints nothing (exit 0) outside a git worktree so a
    misconfigured status line never spams errors into the bar.
    """
    target = str(Path(path).resolve()) if path else os.getcwd()

    # Remote / default-branch.  The config gives a hint, but the segment may
    # run in any repo, so the real upstream branch is detected from git
    # (a `master` project binstub must still classify a `main` repo).
    remote, config_default = "origin", None
    try:
        repo = cfg.load_config().default_repo
        remote, config_default = repo.remote, repo.default_branch
    except Exception:
        pass
    default_branch = _detect_upstream_branch(target, remote, config_default) \
        or config_default or "master"

    rec = _find_record_for_path(target)
    branch = rec.branch if rec else (
        git_ops._get_current_branch_safe(target) or "HEAD"
    )

    try:
        info = git_ops.classify_worktree(
            target, branch, fetch=bool(fetch),
            remote=remote, default_branch=default_branch,
            active_paths=None,  # raw git disposition -- never ACTIVE
        )
    except Exception:
        return ""  # not a worktree / git failure -> empty bar, no noise

    if info.state == git_ops.WorktreeState.GONE:
        return ""

    if rec is not None:
        info = _apply_tracking_override(rec, info)

    # Session activity: scan once and reuse for both the turn-count
    # refinement and the title.  An UNUSED worktree (no commits) that held
    # conversation turns is "conversation-only" -- surface it distinctly so
    # it isn't mistaken for an idle/unused tree.
    ctx = None
    turns = 0
    if rec is not None:
        try:
            ctx = sessions.scan_sessions_fast([rec])
            turns = ctx.turn_count.get(_normalize_path(target), 0)
        except Exception:
            ctx, turns = None, 0

    sync = _sync_status_tag(info)
    state = git_ops.refine_state_with_session(info.state, turns)

    if state == git_ops.WorktreeState.CONVO:
        bg, label = _SEGMENT_STYLE[state]
        tag = f" {turns}\U0001f4ac"  # turn count + speech-balloon glyph
    else:
        bg, label = _SEGMENT_STYLE.get(
            state, ("colour238", state.value.upper())
        )
        tag = sync

    if plain:
        block = f"[{label}{tag}]"
    else:
        block = f"#[bg={bg},fg=colour015,bold] {label}{tag} #[default]"

    parts: list[str] = []
    if not no_title:
        title = _resolve_segment_title(rec, target, info, ctx)
        if persist_title and rec is not None:
            _persist_segment_title(rec, target, ctx)
        if title:
            parts.append(title)
    parts.append(block)
    return " ".join(parts)


def cmd_status_segment(args: argparse.Namespace) -> int:
    """Print the worktree status-bar segment (thin wrapper over the renderer)."""
    line = _render_status_segment(
        args.path, fetch=bool(args.fetch),
        plain=bool(args.plain), no_title=bool(args.no_title),
    )
    if line:
        print(line)
    return 0


def _platform_short(platform: str) -> str:
    """Map a stored platform name to its short worktree-id code.

    Mirrors the ``plat_short`` used when minting worktree ids
    (``windows`` -> ``win``; ``wsl`` / ``linux`` unchanged) so the status
    bar's environment label matches the id on disk.
    """
    return "win" if platform == "windows" else platform


# Environment badge background by OS type (darker colors -- white text on
# top stays readable).  Keyed on the short platform code from
# ``_platform_short``; unknown environments fall back to dark grey.
_ENV_BG: dict[str, str] = {
    "win":   "colour025",  # Windows -- dark blue
    "wsl":   "colour055",  # WSL -- purple
    "linux": "colour130",  # Linux -- dark orange
}


def _render_status_context(path: str | None = None, plain: bool = False) -> str:
    """Render the left status-bar segment: machine, environment, repo:id.

    Returns the identity string (empty when no fields resolve).  Static for
    a session's lifetime, so ``status-updater`` renders it once into
    ``@aw_ctx``; ``cmd_status_context`` is the thin print wrapper.

    Renders three identity fields for the worktree the path is in::

        <machine>  <env-badge>  <repo>:<id4>

    where ``<machine>`` is the host designation (black text), ``<env>`` is
    the platform short code (``win``/``wsl``/``linux``, matching the
    worktree id) rendered as a colored badge keyed on OS type, and
    ``<id4>`` is the worktree id's 4-char suffix (its "last 4 digits").
    Values come from the worktree's tracking record when the path is
    inside a tracked worktree, falling back to live host detection.
    """
    target = str(Path(path).resolve()) if path else os.getcwd()
    rec = _find_record_for_path(target)

    machine = (rec.machine if rec and rec.machine else "") \
        or cfg.detect_machine()
    platform = (rec.platform if rec and rec.platform else "") \
        or cfg.detect_platform()
    env = _platform_short(platform)

    repo = rec.repo if rec else ""
    suffix = rec.worktree_id.rsplit("-", 1)[-1] if rec and rec.worktree_id \
        else ""
    locus = f"{repo}:{suffix}" if repo and suffix else (repo or "")

    fields = [f for f in (machine, env, locus) if f]
    if not fields:
        return ""

    if plain:
        return "  ".join(fields)

    bg = _ENV_BG.get(env, "colour238")
    styled: list[str] = []
    if machine:
        styled.append(f"#[fg=colour016,nobold]{machine}#[default]")
    if env:
        styled.append(f"#[bg={bg},fg=colour015,bold] {env} #[default]")
    if locus:
        styled.append(f"#[fg=colour016,bold]{locus}#[default]")
    # Lead with a style directive so the 1-char left padding is not trimmed.
    return "#[default] " + " ".join(styled)


def cmd_status_context(args: argparse.Namespace) -> int:
    """Print the left identity segment (thin wrapper over the renderer)."""
    line = _render_status_context(args.path, plain=bool(args.plain))
    if line:
        print(line)
    return 0


def _activate_project_for_path(path: str | None) -> None:
    """Resolve + thread the active project in-process from a worktree path.

    ``status-updater`` is a ``_NO_PROJECT_COMMANDS`` entry, so ``main()``
    deliberately skips CWD-based project resolution for it -- but the updater
    *does* know its target worktree via ``--path``.  Without an active project,
    ``cfg.tracking_dir()`` raises inside ``_find_record_for_path`` (which
    swallows it and returns ``None``), so every status-bar field that comes
    only from the tracking record -- the ``repo:id4`` identity locus and the
    session title -- silently disappears from the bar.

    Resolve the project git-like from the path's anchor (the same reverse
    lookup ``main()`` uses for CWD) and set it in process, so the status
    renderers can find the worktree's record.  A no-op when a project is
    already active or the path is not inside an adopted repo.
    """
    if cfg.active_project():
        return
    try:
        anchor = _git_toplevel(Path(path) if path else Path.cwd())
        if anchor is None:
            return
        name = _reverse_lookup_project(anchor)
        if name:
            cfg.set_active_project(name)
    except Exception:
        pass


def cmd_status_updater(args: argparse.Namespace) -> int:
    """Keep a session's status-bar vars fresh without per-render spawns.

    The status bar references precomputed user options -- ``#{@aw_ctx}``
    (identity, static) and ``#{@aw_seg}`` (git disposition, dynamic) --
    instead of polling ``#(agent-worktrees ...)``.  psmux runs ``#()`` jobs
    synchronously in the paint path (no tmux-style caching), so a
    600 ms-class binstub spawn per repaint under Copilot's high-framerate
    TUI made muxed sessions unusable.  This long-lived loop moves that cost
    off the paint path: it renders **in-process** (paying Python import once,
    never re-spawning the binstub) and only ever shells out to the cheap,
    native ``set-option`` / ``has-session`` mux verbs.

    Identity is rendered once into ``@aw_ctx``; disposition is refreshed into
    ``@aw_seg`` every ``--interval`` seconds until the session ends.  Launched
    detached by the session launcher; safe to (re)spawn on every attach/join --
    an ``@aw_updater`` token elects a single live updater per session and older
    ones retire on their next tick.
    """
    import shutil
    import subprocess
    import time

    sess = args.session
    if not sess:
        return 2

    mux = args.mux or ("psmux" if shutil.which("psmux") else "tmux")
    mux_bin = shutil.which(mux) or mux
    path = args.path or os.getcwd()
    interval = args.interval if args.interval and args.interval >= 2 else 15

    # status-updater is a no-project command, so main() never resolved a
    # project for us -- but the status renderers need one to find the
    # worktree's tracking record (repo:id locus + session title).  Resolve it
    # git-like from --path before rendering anything.
    _activate_project_for_path(path)

    def _mux(*a: str) -> "subprocess.CompletedProcess[str] | None":
        try:
            return subprocess.run(
                [mux_bin, *a],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            return None

    def _has_session() -> bool:
        r = _mux("has-session", "-t", sess)
        return r is not None and r.returncode == 0

    def _set(opt: str, val: str) -> None:
        # Session-scoped (no -g): empirically isolated per session on psmux
        # 3.3.6 and tmux 3.4, so concurrent worktree sessions don't clobber
        # each other's bar.
        _mux("set-option", "-t", sess, opt, val)

    if not _has_session():
        return 0

    # Single-instance guard.  The launcher may (re)spawn an updater on every
    # attach/join, so each updater claims @aw_updater with its own token; a
    # newer updater overwrites it and the older one retires on its next tick.
    # Cheaper and more portable than pid-liveness checks, and it doubles as the
    # tmux/psmux equivalent of the old flock guard.
    token = str(os.getpid())

    def _owns() -> bool:
        r = _mux("display-message", "-t", sess, "-p", "#{@aw_updater}")
        if r is None or r.returncode != 0:
            return True  # can't read the token -> assume ownership, keep serving
        return r.stdout.strip() == token

    _set("@aw_updater", token)

    # Identity (machine | env | repo:id4) is static for the session's life:
    # render once, push to @aw_ctx, never poll it again.
    try:
        _set("@aw_ctx", _render_status_context(path, plain=False))
    except Exception:
        pass

    # Disposition (DIRTY/FINAL/WIP/CONVO/...) changes as work happens: refresh
    # @aw_seg on the interval until the session ends or a newer updater takes
    # over.  The bar itself does zero process work between updates -- the mux
    # only re-runs the strftime %H:%M clock.
    while _has_session() and _owns():
        try:
            seg = _render_status_segment(
                path, fetch=False, plain=False, no_title=False,
                persist_title=True,
            )
        except Exception:
            seg = ""
        _set("@aw_seg", seg)
        time.sleep(interval)
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
        state_map: dict[str, git_ops.WorktreeStateInfo] = {}
        if getattr(args, "classify", False):
            state_map = _classify_records(records, session_ctx)
        worktrees = [
            _worktree_to_dict(
                rec, mux_info=mux_map.get(rec.worktree_id),
                session_ctx=session_ctx,
                state_info=state_map.get(rec.worktree_id),
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
    if rec.kind not in tracking.MANAGED_KINDS:
        output.err(f"{wt_id} is not a managed (system/bridge) worktree "
                   f"(kind={rec.kind}); refusing")
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

    Default creates a normal (``session``) worktree and emits a JSON envelope
    with the new worktree info and launch plan; the caller launches Copilot.

    ``--system`` instead creates a daemon-owned worktree (``--name``/``--owner``
    label it): hidden from the launch Picker, exempt from routine cleanup, and
    torn down per-run via ``remove-system``. System worktrees never launch
    Copilot -- a daemon uses only the returned ``path``.
    """
    is_system = getattr(args, "system", False)
    with output.stdout_to_stderr():
        try:
            config = cfg.load_config()
            result = _create_worktree_core(
                config, no_mux=True,
                kind="system" if is_system else "session",
                owner=(getattr(args, "owner", None) or getattr(args, "name", None))
                if is_system else None,
                name=getattr(args, "name", None) if is_system else None,
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
    label = "system worktree" if is_system else "worktree"
    print(f"✅ Created {label}: {wt['id']}")
    print(f"   Path:   {wt['path']}")
    print(f"   Branch: {wt['branch']}")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# cleanup
# ═══════════════════════════════════════════════════════════════════════════

def _reap_worktree(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
    repo: cfg.RepoConfig,
    tracking_path: Path,
) -> tuple[int, list[str]]:
    """Remove one worktree: dir + branch + perms + tracking + tmux session.

    Returns ``(failures, warnings)``. The caller must hold the finalization
    lock. Shared by the batch ``cmd_cleanup`` loop and the per-worktree
    (``--worktree-id``) path so both reap identically.
    """
    warnings: list[str] = []
    failures = 0

    if rec.worktree_path and Path(rec.worktree_path).exists():
        # Tear down the owning mux session first, then terminate any lingering
        # process whose cwd is still rooted in the worktree (a stray gh, a
        # status-updater, a leftover shell). On Windows an open cwd handle keeps
        # the directory locked, so this must happen *before* rmtree or the dir
        # is left behind as an empty shell (issue dotfiles#139).
        sessions.kill_tmux_session(rec.worktree_id)
        try:
            killed = procs.terminate_processes_under(rec.worktree_path)
        except Exception:
            killed = []
        if killed:
            names = ", ".join(
                f"{k['name'] or '?'}({k['pid']})" for k in killed if k["killed"])
            if names:
                warnings.append(f"Terminated lingering process(es): {names}")
            activity.log_event(
                "worktree_procs_terminated",
                worktree_id=rec.worktree_id,
                count=sum(1 for k in killed if k["killed"]),
            )

        if not git_ops.remove_worktree(repo.anchor, rec.worktree_path):
            warnings.append(
                "Could not remove worktree via git -- forcing directory removal.")
        wt_dir = Path(rec.worktree_path)
        if wt_dir.exists():
            # Locks may release a beat after the holding process dies; retry the
            # tree removal briefly before giving up.
            for attempt in range(4):
                shutil.rmtree(wt_dir, ignore_errors=True)
                if not wt_dir.exists():
                    break
                time.sleep(0.25 * (attempt + 1))
            if wt_dir.exists():
                warnings.append(f"Directory still present: {wt_dir}")
                failures += 1

    if rec.branch:
        if not git_ops.delete_branch(rec.branch, cwd=repo.anchor, force=True):
            warnings.append(f"Could not delete branch {rec.branch}")
            failures += 1

    # Clean up Copilot permissions and trusted_folders
    if rec.worktree_path:
        permissions.merge_permissions(repo.anchor, rec.worktree_path)
        permissions.remove_trusted_folder(rec.worktree_path)

    # Remove tracking YAML
    (tracking_path / f"{rec.worktree_id}.yaml").unlink(missing_ok=True)

    activity.log_event(
        "worktree_reaped",
        worktree_id=rec.worktree_id,
        branch=rec.branch,
        state=info.state.value,
    )
    return failures, warnings


def reap_one(
    wt_id: str,
    *,
    force: bool = False,
    include_unused: bool = False,
    include_conversations: bool = False,
    reconcile_prs: bool = False,
) -> dict:
    """Reap a single worktree by ID and return a JSON-ready result dict.

    Re-checks prune-safety (defense in depth: the picker only sends cleanable
    ids, but a stray call must never reap unsafe work) unless ``force``; an
    active session is never reaped even with ``force``. This is the pure
    result-returning core shared by the ``cleanup --worktree-id`` CLI and the
    picker's in-process local Cleanup executor.
    """
    config = cfg.load_config()
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()

    wt_id = _resolve_worktree_id(wt_id)
    yaml_path = tracking_path / f"{wt_id}.yaml"

    def _result(payload: dict) -> dict:
        payload.setdefault("worktree_id", wt_id)
        return payload

    if not yaml_path.exists():
        return _result({"ok": False, "removed": False, "skipped": False,
                        "reason": f"worktree not found: {wt_id}"})
    rec = tracking.load_record(yaml_path)
    if rec.kind in tracking.MANAGED_KINDS:
        return _result({"ok": False, "removed": False, "skipped": True,
                        "reason": f"agent-owned {rec.kind} worktree "
                        "(use the System menu)"})

    if git_ops.has_remote(repo.remote, cwd=repo.anchor):
        git_ops.fetch(repo.remote, cwd=repo.anchor)
    upstream = f"{repo.remote}/{repo.default_branch}"

    session_ctx = sessions.scan_sessions_fast([rec])
    active_paths = _build_active_paths([rec], session_ctx)
    turns = session_ctx.turn_count.get(_normalize_path(rec.worktree_path), 0)

    if reconcile_prs and rec.prs:
        lookup = _make_pr_lookup(config)
        if prune.reconcile_pr_states(rec, lookup):
            try:
                tracking.save_record(rec)
            except OSError:
                pass

    if rec.worktree_path and Path(rec.worktree_path).exists():
        info = git_ops.classify_worktree(
            rec.worktree_path, rec.branch, fetch=False,
            remote=repo.remote, default_branch=repo.default_branch,
            active_paths=active_paths,
        )
        info = _apply_tracking_override(rec, info)
    elif rec.status == "finalized":
        info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    else:
        info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.GONE)

    # An active session is never reaped, even with force.
    if info.state == git_ops.WorktreeState.ACTIVE:
        return _result({"ok": False, "removed": False, "skipped": True,
                        "reason": "active Copilot session in use",
                        "bucket": "active"})
    if not force:
        if info.state == git_ops.WorktreeState.GONE:
            if rec.branch and not git_ops.is_branch_merged(
                rec.branch, upstream, cwd=repo.anchor,
            ):
                return _result({"ok": False, "removed": False, "skipped": True,
                                "reason": "branch has unmerged commits "
                                "(worktree dir missing)"})
        else:
            disp = prune.cleanup_disposition(
                rec, info, turn_count=turns,
                include_unused=include_unused,
                include_conversations=include_conversations,
            )
            if not disp.cleanable:
                return _result({"ok": False, "removed": False, "skipped": True,
                                "reason": disp.reason, "bucket": disp.bucket})

    lock = fin.FinalizeLock(Path(repo.worktree_root) / ".finalize.lock")
    try:
        lock.acquire()
    except TimeoutError:
        return _result({"ok": False, "removed": False, "skipped": False,
                        "reason": "timed out waiting for finalization lock"})
    try:
        failures, warnings = _reap_worktree(rec, info, repo, tracking_path)
        git_ops.prune_worktrees(cwd=repo.anchor)
    finally:
        lock.release()

    return _result({"ok": failures == 0, "removed": True, "skipped": False,
                    "state": info.state.value, "warnings": warnings})


def _iso_epoch(ts: str | None) -> float | None:
    """Parse an ISO-8601 tracking timestamp to epoch seconds, or ``None``."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).timestamp()
    except (ValueError, TypeError):
        return None


# #713: a finalized/idle session is only reaped once it has been quiet this
# long, so a session whose Copilot is still working (mid-turn, background task,
# scheduled prompt -> fresh pane activity) is never killed out from under it.
# The operator sets the window; the future inactivity monitor reuses it.
REAP_IDLE_GRACE_SECS = 6 * 3600


def reap_orphan_mux_sessions(*, dry_run: bool = False,
                             only_id: str | None = None,
                             idle_grace_secs: float = REAP_IDLE_GRACE_SECS,
                             now: float | None = None) -> dict:
    """Reap leaked tmux/psmux sessions whose worktree is gone or done **and idle**.

    Enumerates live ``wt-<id>`` multiplexer sessions and kills those that no
    longer have an owning, resumable worktree -- the *finalized-still-present*
    orphans plus untracked / path-missing leaks (issue #713) -- **but only once
    the session has been quiet for ``idle_grace_secs``**. Without the idle gate a
    finalized-from-inside session whose Copilot is still working (you finalized
    the PR but the agent is mid-task, or a scheduled prompt is pending) would be
    killed the moment it's unattended; closing a tab is meant to *preserve* a
    live session, not end it. The same predicate runs at both worktree lifecycle
    boundaries -- picker launch (:func:`_run_new_picker`) and session end
    (:func:`_sweep_orphans_on_exit`, #2149) -- so idle orphans are reaped on a
    natural cadence with **no persistent timer or daemon**.

    ``only_id`` restricts the sweep to a **single** worktree's session; the exact
    same spare-attached/system/active/**busy** predicate is applied.

    **Conservative by design** -- a session is never reaped when:

    - a terminal client is **attached** (a human is using it),
    - its worktree record is ``kind: system`` (daemon-owned), or
    - its worktree is still **active** (tracked, dir present), or
    - it has been **active within the grace window** (fresh pane activity => the
      Copilot inside is busy), or the activity signal is **unknown** (never risk
      killing a session we can't prove is idle).

    Returns a JSON-ready dict::

        {"available": bool,                  # False when no mux is installed
         "reaped": ["<id>", ...],
         "skipped": [{"id": "<id>",
                      "reason": "attached|system|active|busy|activity-unknown"}, ...],
         "errors":  [{"id": "<id>", "reason": "..."}, ...]}
    """
    all_sessions = sessions._list_mux_sessions()
    if all_sessions is None:
        return {"available": False, "reaped": [], "skipped": [], "errors": []}

    now = time.time() if now is None else now
    activity_by_name = sessions._mux_session_activity()
    tracking_path = cfg.tracking_dir()
    by_id: dict[str, tracking.WorktreeRecord] = {
        rec.worktree_id: rec for rec in tracking.list_records(tracking_path)
    }

    reaped: list[str] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    for name, attached in all_sessions.items():
        if not name.startswith("wt-"):
            continue
        wt_id = name[len("wt-"):]
        if only_id is not None and wt_id != only_id:
            continue
        if attached and attached > 0:
            skipped.append({"id": wt_id, "reason": "attached"})
            continue
        rec = by_id.get(wt_id)
        if rec is None:
            reason = "untracked"
        elif rec.kind in tracking.MANAGED_KINDS:
            skipped.append({"id": wt_id, "reason": rec.kind})
            continue
        elif rec.status in ("finalized", "complete", "completed"):
            reason = rec.status
        elif not (rec.worktree_path and Path(rec.worktree_path).exists()):
            reason = "gone"
        else:
            skipped.append({"id": wt_id, "reason": "active"})
            continue
        # Idle gate (#713): never reap a session that is still busy. Prefer the
        # mux's real pane-activity clock; fall back to the tracking record's
        # last-resumed/started time; if nothing is knowable, spare it.
        last_active = activity_by_name.get(name)
        if last_active is None and rec is not None:
            last_active = _iso_epoch(rec.last_resumed_at) or _iso_epoch(rec.started_at)
        if last_active is None:
            skipped.append({"id": wt_id, "reason": "activity-unknown"})
            continue
        if now - last_active < idle_grace_secs:
            skipped.append({"id": wt_id, "reason": "busy"})
            continue
        if dry_run:
            reaped.append(wt_id)
            continue
        if sessions.kill_tmux_session(wt_id):
            reaped.append(wt_id)
            try:
                activity.log_event(
                    "mux_session_reaped", worktree_id=wt_id, reason=reason)
            except Exception:
                pass
        else:
            errors.append({"id": wt_id, "reason": f"kill failed ({reason})"})

    return {"available": True, "reaped": reaped,
            "skipped": skipped, "errors": errors}


def cmd_reap_sessions(args: argparse.Namespace) -> int:
    """``reap-sessions`` -- sweep orphaned mux sessions (issue #713).

    With ``--id`` it targets a single worktree, applying the identical
    spare-attached/system/active/busy predicate as the full sweep.
    """
    dry = getattr(args, "dry_run", False)
    only_id = getattr(args, "id", None)
    grace_hours = getattr(args, "grace_hours", None)
    kwargs = {"dry_run": dry, "only_id": only_id}
    if grace_hours is not None:
        kwargs["idle_grace_secs"] = float(grace_hours) * 3600
    payload = reap_orphan_mux_sessions(**kwargs)
    if getattr(args, "json", False):
        _json_output(payload)
        return 0
    if not payload["available"]:
        print("No multiplexer available -- nothing to reap.")
        return 0
    verb = "Would reap" if dry else "Reaped"
    ids = payload["reaped"]
    print(f"{verb} {len(ids)} orphaned mux session(s): "
          + (", ".join(ids) if ids else "(none)"))
    for e in payload["errors"]:
        print(f"  ! {e['id']}: {e['reason']}")
    return 0


def cmd_restart(args: argparse.Namespace) -> int:
    """``restart <id>`` -- stop a worktree's interactive Copilot, keep the worktree.

    The shared primitive behind the Picker "Stop" action and NF "Take over":
    graceful double-Ctrl-C quit (Copilot's native clean exit), falling back to a
    hard mux kill-session. Relaunch / ACP-resume is the caller's job. (The CLI
    verb stays ``restart``; the picker labels it "Stop".)
    """
    payload = sessions.restart_worktree_copilot(
        args.worktree_id,
        graceful=not getattr(args, "no_graceful", False),
        settle_timeout=getattr(args, "settle_timeout", 6.0),
    )
    if getattr(args, "json", False):
        _json_output(payload)
        return 0 if payload["ok"] else 1
    wt = payload["worktree_id"]
    if not payload["had_session"]:
        print(f"{wt}: no interactive Copilot running (nothing to stop).")
        return 0
    if payload["method"] == "graceful":
        print(f"{wt}: Copilot quit gracefully (double Ctrl-C).")
    elif payload["method"] == "hard":
        print(f"{wt}: Copilot hard-stopped (mux kill-session).")
    else:
        print(f"{wt}: failed to stop the interactive Copilot.")
    return 0 if payload["ok"] else 1


def _cleanup_one(args: argparse.Namespace) -> int:
    """``cleanup --worktree-id <id>`` -- thin CLI wrapper over :func:`reap_one`."""
    payload = reap_one(
        args.worktree_id,
        force=getattr(args, "force", False),
        include_unused=getattr(args, "include_unused", False),
        include_conversations=getattr(args, "include_conversations", False),
        reconcile_prs=getattr(args, "reconcile_prs", False),
    )
    if getattr(args, "json", False):
        _json_output(payload)
    else:
        tag = "removed" if payload.get("removed") else (
            "skipped" if payload.get("skipped") else "error")
        line = f"{payload['worktree_id']}: {tag}"
        if payload.get("reason"):
            line += f" -- {payload['reason']}"
        print(line)
    return 0 if payload.get("ok") else 1


def cmd_cleanup(args: argparse.Namespace) -> int:
    if getattr(args, "worktree_id", None):
        return _cleanup_one(args)

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
    records = [r for r in records if r.kind not in tracking.MANAGED_KINDS]
    if not records:
        print("No tracked sessions.")
        return 0

    to_clean: list[tuple[tracking.WorktreeRecord, git_ops.WorktreeStateInfo]] = []
    skipped: list[tuple[tracking.WorktreeRecord, str]] = []
    unused_count = 0
    conversation_count = 0
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
    session_ctx = sessions.scan_sessions_fast(records)
    active_paths = _build_active_paths(records, session_ctx)

    # Optional: heal stale tracked PR state from the provider (network) so a
    # PR merged externally (local record still "open") is recognized as landed.
    pr_lookup = _make_pr_lookup(config) if getattr(args, "reconcile_prs", False) else None

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

        # Compute prune-safety verdict (combines git state, PR records, and
        # session activity) -- drives the cleanup decision and enriches display.
        norm = _normalize_path(rec.worktree_path)
        turns = session_ctx.turn_count.get(norm, 0)

        # Heal stale PR state from the provider before assessing (opt-in).
        if pr_lookup is not None and rec.prs:
            if prune.reconcile_pr_states(rec, pr_lookup):
                try:
                    tracking.save_record(rec)
                except OSError:
                    pass

        verdict = prune.assess(rec, info, turn_count=turns)

        # Annotate state with dirty indicator / turn count when relevant
        if info.dirty > 0 and info.state != git_ops.WorktreeState.DIRTY:
            state_display = f"{state_str} ({info.dirty}△)"
        elif verdict.category == "conversation-only":
            state_display = f"{state_str} ({turns}💬)"
        else:
            state_display = state_str
        print(f"{rec.worktree_id:<50} {state_display:<12} {age:<12} {path_display}")

        # Determine if cleanable
        cleanable = False
        skip_reason = ""
        include_conversations = getattr(args, "include_conversations", False)

        if info.state == git_ops.WorktreeState.GONE:
            # Directory missing -- verify branch content is on master first.
            if rec.branch and not git_ops.is_branch_merged(
                rec.branch, upstream, cwd=repo.anchor,
            ):
                skip_reason = "branch has unmerged commits (worktree dir missing)"
            else:
                cleanable = True
        else:
            disp = prune.cleanup_disposition(
                rec, info, turn_count=turns,
                include_unused=args.include_unused,
                include_conversations=include_conversations,
            )
            cleanable = disp.cleanable
            if disp.bucket == "active":
                skip_reason = "active Copilot session in use"
            elif disp.bucket == "open-pr":
                skip_reason = disp.reason
            elif disp.bucket == "closed-unmerged":
                skip_reason = disp.reason
            elif disp.bucket == "unused" and not cleanable:
                unused_count += 1
            elif disp.bucket == "conversation" and not cleanable:
                conversation_count += 1
            elif disp.bucket == "dirty":
                dirty_count += 1
            elif disp.bucket == "wip":
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

    if (not to_clean and unused_count == 0 and conversation_count == 0
            and dirty_count == 0 and wip_count == 0 and not skipped):
        print("Nothing to clean.")
        return 0

    if to_clean:
        print(f"{len(to_clean)} session(s) eligible for cleanup.")

    if not args.include_unused and unused_count > 0:
        print(
            f"{unused_count} unused worktree(s) preserved -- no commits, "
            "no uncommitted changes (pass --include-unused to also clean)."
        )

    if not getattr(args, "include_conversations", False) and conversation_count > 0:
        print(
            f"{conversation_count} conversation-only worktree(s) preserved -- "
            "no commits, but the session held conversation turns (pass "
            "--include-conversations to also clean)."
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
            f, warns = _reap_worktree(rec, info, repo, tracking_path)
            for w in warns:
                output.warn(w)
            failures += f

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
# sync (fast-forward worktrees to the default branch)
# ═══════════════════════════════════════════════════════════════════════════

def _sync_one_record(
    rec: tracking.WorktreeRecord,
    repo: cfg.RepoConfig,
    active_paths: set[str],
) -> dict:
    """Fast-forward one worktree (FF-only, never an active session).

    Returns a JSON-ready result dict ``{worktree_id, updated, reason, behind}``.
    ``reason`` is the git_ops FF reason (updated / up-to-date / ahead / diverged
    / dirty / detached / orphan / gone / no-upstream / ff-failed), or ``active``
    when a live session owns the worktree.
    """
    if not (rec.worktree_path and Path(rec.worktree_path).exists()):
        return {"worktree_id": rec.worktree_id, "updated": False,
                "reason": "gone", "behind": 0}
    info = git_ops.classify_worktree(
        rec.worktree_path, rec.branch, fetch=False,
        remote=repo.remote, default_branch=repo.default_branch,
        active_paths=active_paths,
    )
    info = _apply_tracking_override(rec, info)
    if info.state == git_ops.WorktreeState.ACTIVE:
        return {"worktree_id": rec.worktree_id, "updated": False,
                "reason": "active", "behind": info.behind}
    ff = git_ops.fast_forward_worktree(
        rec.worktree_path, remote=repo.remote,
        default_branch=repo.default_branch, do_fetch=False,
    )
    return {"worktree_id": rec.worktree_id, "updated": ff.updated,
            "reason": ff.reason, "behind": ff.behind}


def sync_one(wt_id: str) -> dict:
    """Fast-forward a single worktree by ID; return a JSON-ready result dict.

    The pure result-returning core shared by the ``sync --worktree-id`` CLI and
    the picker's in-process local Sync executor.
    """
    config = cfg.load_config()
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    wt_id = _resolve_worktree_id(wt_id)
    yaml_path = tracking_path / f"{wt_id}.yaml"
    if not yaml_path.exists():
        return {"worktree_id": wt_id, "updated": False,
                "reason": "not-found", "behind": 0}
    rec = tracking.load_record(yaml_path)
    if git_ops.has_remote(repo.remote, cwd=repo.anchor):
        try:
            git_ops.fetch(repo.remote, cwd=repo.anchor)
        except Exception:
            pass
    session_ctx = sessions.scan_sessions_fast([rec])
    active_paths = _build_active_paths([rec], session_ctx)
    return _sync_one_record(rec, repo, active_paths)


def cmd_sync(args: argparse.Namespace) -> int:
    """Fast-forward worktrees to their upstream default branch (FF-only).

    ``--worktree-id <id>`` syncs one (and emits a single JSON object with
    ``--json``); otherwise every active worktree on this machine is synced.
    SSH-able: the picker's per-item Sync progress calls ``--worktree-id --json``
    per remote row. Never rebases, never touches an ahead/dirty/diverged or
    active worktree -- those come back with a skip ``reason``.
    """
    config = cfg.load_config()
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    as_json = getattr(args, "json", False)
    single = getattr(args, "worktree_id", None)

    if single:
        wt_id = _resolve_worktree_id(single)
        yaml_path = tracking_path / f"{wt_id}.yaml"
        if not yaml_path.exists():
            res = {"worktree_id": wt_id, "updated": False,
                   "reason": "not-found", "behind": 0}
            if as_json:
                _json_output(res)
            else:
                print(f"{wt_id}: not-found")
            return 1
        records = [tracking.load_record(yaml_path)]
    else:
        records = tracking.list_records(
            tracking_path, status_filter="active",
            platform_filter=cfg.detect_platform(),
        )
        records = [
            r for r in records
            if r.kind not in tracking.MANAGED_KINDS
            and r.worktree_path and Path(r.worktree_path).exists()
        ]

    # One fetch refreshes the shared upstream ref for every worktree of this
    # repo; per-worktree classification then runs with fetch=False.
    if git_ops.has_remote(repo.remote, cwd=repo.anchor):
        try:
            git_ops.fetch(repo.remote, cwd=repo.anchor)
        except Exception:
            pass

    session_ctx = sessions.scan_sessions_fast(records)
    active_paths = _build_active_paths(records, session_ctx)

    results = [_sync_one_record(rec, repo, active_paths) for rec in records]

    if as_json:
        _json_output(results[0] if single else {"results": results})
    elif not results:
        print("No worktrees to sync.")
    else:
        for r in results:
            tag = f"updated ↑{r.get('behind', 0)}" if r.get("updated") \
                else r.get("reason", "?")
            print(f"{r['worktree_id']}: {tag}")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# profiles (terminal-profile selection -- the Picker's Profiles grid column)
# ═══════════════════════════════════════════════════════════════════════════

def _profiles_host() -> tuple[str, str]:
    """This machine's (display_name, env_label) in roster vocabulary."""
    from .picker_tui import roster
    return roster.local_host()


def cmd_profiles(args: argparse.Namespace) -> int:
    """Read or write this machine's terminal-profile column for the repo.

    ``get`` emits this host's selected launch targets (its column of the host x
    target matrix) as JSON. ``apply --set <json>`` persists a new column into
    ``~/.<project>/config.yaml`` and, unless ``--no-mirror``, regenerates the
    terminal profiles to match. Both are SSH-able so the Picker can read/write
    a remote host's column over its facility alias.
    """
    from . import profiles as profiles_mod

    action = getattr(args, "profiles_action", "get")
    as_json = getattr(args, "json", False)
    cfg_path = cfg.default_config_path()
    machine, env = _profiles_host()

    if action == "get":
        managed = profiles_mod.has_selection(cfg_path)
        sels = profiles_mod.normalize_selection(
            profiles_mod.load_selection(cfg_path), machine, env)
        payload = {
            "machine": machine,
            "env": env,
            "managed": managed,
            "targets": [s.as_dict() for s in sels],
        }
        if as_json:
            _json_output(payload)
        else:
            state = "managed" if managed else "legacy (all profiles)"
            print(f"Terminal profiles for {machine} {env} [{state}]:")
            for s in sels:
                lock = " (self, locked)" if (
                    s.machine == machine and s.env == env and s.kind == "agent"
                ) else ""
                print(f"  - {s.machine} {s.env} · {s.kind}{lock}")
        return 0

    # action == "apply"
    raw = getattr(args, "set", None)
    if raw is None:
        msg = "profiles apply requires --set '<json-array>'"
        if as_json:
            _json_error(msg)
        else:
            output.err(msg)
        return 2
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as e:
        msg = f"invalid --set JSON: {e}"
        if as_json:
            _json_error(msg)
        else:
            output.err(msg)
        return 2
    if not isinstance(parsed, list):
        msg = "--set must be a JSON array of {machine, env, kind} objects"
        if as_json:
            _json_error(msg)
        else:
            output.err(msg)
        return 2
    sels = [
        profiles_mod.TargetSel(
            str(o.get("machine", "")).strip(),
            str(o.get("env", "")).strip(),
            str(o.get("kind", "agent")).strip().lower(),
        )
        for o in parsed if isinstance(o, dict)
    ]
    written = profiles_mod.save_selection(
        cfg_path, sels, self_machine=machine, self_env=env)

    mirrored = False
    if not getattr(args, "no_mirror", False):
        mirrored = _mirror_terminal_profiles()

    payload = {
        "machine": machine,
        "env": env,
        "targets": [s.as_dict() for s in written],
        "mirrored": mirrored,
    }
    if as_json:
        _json_output(payload)
    else:
        output.ok(f"Saved {len(written)} terminal profile(s) for {machine} {env}"
                  + (" · mirrored" if mirrored else ""))
    return 0


def _mirror_terminal_profiles() -> bool:
    """Regenerate the local terminal profiles from the saved selection.

    Mirroring is a Windows-only concern today (Windows Terminal fragment via
    the installer); on WSL/Linux hosts it is a no-op (Tabby/Linux mirroring is
    future work). Returns True when a mirror actually ran.
    """
    if platform.system() != "Windows":
        return False
    try:
        _refresh_terminal_profiles()
        return True
    except Exception:
        return False


# ═══════════════════════════════════════════════════════════════════════════
# picker -- persistent new-picker opt-in (machine-wide global config)
# ═══════════════════════════════════════════════════════════════════════════

def _set_global_config_key(key: str, value) -> Path:
    """Read-modify-write one top-level key into the global machine config.

    Preserves every other key. Creates the file (and parent) if absent.
    Returns the path written.
    """
    import yaml as _yaml

    gpath = cfg.global_config_path()
    data: dict = {}
    if gpath.exists():
        try:
            with open(gpath, encoding="utf-8") as f:
                loaded = _yaml.safe_load(f)
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, _yaml.YAMLError):
            data = {}
    data[key] = value
    gpath.parent.mkdir(parents=True, exist_ok=True)
    with open(gpath, "w", encoding="utf-8") as f:
        _yaml.safe_dump(data, f, sort_keys=False, default_flow_style=False)
    return gpath


def cmd_picker(args: argparse.Namespace) -> int:
    """Inspect / opt out of the Textual picker for this machine.

    The Textual picker is the **default everywhere**. ``disable`` writes
    ``new_picker: false`` into the machine-wide global config
    (``~/.agent-worktrees/config.yaml``) to opt this machine *out* to the legacy
    ANSI picker; ``enable`` restores the default. ``status`` reports the
    effective value and where it comes from; ``mock`` launches the picker in the
    mock dev sandbox. SSH-able so a fleet migration can flip it per machine.
    """
    from . import picker_tui

    action = getattr(args, "picker_action", "status")
    as_json = getattr(args, "json", False)

    if action in ("enable", "disable"):
        val = action == "enable"
        gpath = _set_global_config_key("new_picker", val)
        if as_json:
            _json_output({"new_picker": val, "path": str(gpath)})
        else:
            output.ok(f"new_picker = {str(val).lower()} ({gpath})")
        return 0

    if action == "mock":
        # Explicit dev sandbox: launch the picker in mock mode -- real data is
        # shown but every mutating action (Cleanup / Sync / Stop / profiles
        # Apply) is simulated with no side effects. This is the ONLY sanctioned
        # way to run the picker's mock behaviors; a normal launch is always
        # real. Prints the resulting launch decision instead of acting on it.
        live = not _in_ssh_session()
        decision = picker_tui.run_tui_picker(live=live, mock_mode=True)
        if as_json:
            _json_output({"mock": True, "decision": decision})
        else:
            output.info(f"mock picker exited · decision: {decision!r}")
        return 0

    # status
    persisted = None
    try:
        persisted = bool(cfg.load_config().new_picker)
    except Exception:
        # No project context -- read the global config directly (default True:
        # the picker is on unless a machine explicitly opted out).
        import yaml as _yaml
        gpath = cfg.global_config_path()
        if gpath.exists():
            try:
                with open(gpath, encoding="utf-8") as f:
                    raw = _yaml.safe_load(f)
                if isinstance(raw, dict):
                    persisted = bool(raw.get("new_picker", True))
            except (OSError, _yaml.YAMLError):
                persisted = None
    effective = picker_tui.new_picker_enabled(
        type("_C", (), {"new_picker": bool(persisted)})())
    env_override = None
    if os.environ.get("AGENT_WORKTREES_LEGACY_PICKER"):
        env_override = "AGENT_WORKTREES_LEGACY_PICKER"
    elif os.environ.get("AGENT_WORKTREES_NEW_PICKER"):
        env_override = "AGENT_WORKTREES_NEW_PICKER"
    if as_json:
        _json_output({"new_picker": bool(persisted), "effective": effective,
                      "env_override": env_override})
    else:
        print(f"new_picker (persisted): {str(bool(persisted)).lower()}")
        print(f"effective:              {str(effective).lower()}"
              + (f"  (env override: {env_override})" if env_override else ""))
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

    # Deploy global machine-wide config (lowest tier), then per-project config
    config_path = proj_dir / "config.yaml"
    _write_global_config(machine, plat, repo_dir.parent)
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

    # Update projects registry. Honor the repos.yaml agent-exposure
    # classification (default ON) so a repo marked ``agent: false`` (e.g. a
    # contributor/owner repo that hosts no agent) is registered reference-only
    # instead of silently exposing a same-machine agent on (re-)install.
    from . import repos as _repos
    _entry = _repos.find_repo(project)
    _expose_agent = _entry.agent if _entry else True
    inst.register_project(project, repo_dir=repo_dir, expose_agent=_expose_agent)

    # Reconcile all project binstubs against the registry (add missing, incl.
    # the .ps1 primary on Windows; remove stubs for deregistered projects).
    inst.reconcile_binstubs()

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

    # Write global machine-wide config (lowest tier), then per-project config
    config_path = proj_dir / "config.yaml"
    _write_global_config(machine, plat, repo_dir.parent)
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
    "worktree-dir":  "Current worktree root (the worktree you are in; empty if not inside one)",
    "worktrees-root": "Parent directory that holds all worktrees (formerly 'worktree-dir')",
    "src-dir":       "Source root (parent of repos)",
    "config-dir":    "Per-project config directory (~/.{project})",
    "machine":       "Machine name from config",
    "platform":      "Platform (win/wsl/linux)",
    "project":       "Project name",
    "repo-remote":   "Canonical remote URL of this repo (registry remote; falls back to git origin) -- the device-independent repo key",
    "pr-enabled":    "Whether PR mode is enabled (true/false)",
    "pr-required":   "Whether PRs are required, blocking direct-to-master (true/false)",
    "pr-provider":   "PR provider (gitea|github|azure-devops) when PR mode is on",
}


def _resolve_repo_remote(config: "cfg.Config", repo: "cfg.RepoConfig") -> str:
    """Canonical remote URL for the active repo -- the device-independent key.

    Prefers the **registry** remote for this project (curated and consistent
    across machines, so a shared consumer keys every device the same way), and
    falls back to the anchor's ``git remote get-url origin`` when the project is
    not in the repos registry. Returns ``""`` when neither resolves.
    """
    from . import repos
    try:
        entry = repos.find_repo(config.repo_name)
        if entry and entry.remote:
            return entry.remote
        result = git_ops.git("remote", "get-url", "origin", cwd=repo.anchor, check=False)
        if result.returncode == 0:
            return result.stdout.strip()
    except OSError:
        # anchor may not exist yet (e.g. a freshly-configured project); the
        # remote is simply unknown rather than an error.
        pass
    return ""


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

    # Current worktree root: resolve purely from CWD (git-like), via the dev107
    # resolver. Empty when the caller is at the anchor or outside any worktree.
    wt_id = _infer_worktree_id_from_cwd(config)
    current_worktree = str(Path(repo.worktree_root) / wt_id) if wt_id else ""

    values = {
        "repo-dir":     repo.anchor,
        "worktree-dir": current_worktree,
        "worktrees-root": repo.worktree_root,
        "src-dir":      config.srcroot,
        "config-dir":   str(cfg.project_dir()),
        "machine":      config.machine,
        "platform":     config.platform,
        "project":      config.repo_name,
        "repo-remote":  _resolve_repo_remote(config, repo),
        "pr-enabled":    "true" if repo.pr.enabled else "false",
        "pr-required":   "true" if repo.pr.required else "false",
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
    "remove-system": "remove-system",
    "list": "list",
    "status": "status",
    "status-segment": "status-segment",
    "status-context": "status-context",
    "status-updater": "status-updater",
    "push": "push-changes",
    "push-changes": "push-changes",
    "create-pr": "create-pr",
    "pr-ready": "pr-ready",
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
        "  create --system --name N [--owner O]  "
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
    print("  pr-ready [id]          Release a held PR for merge", file=out)
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
    # `repos` is a no-project command, so usage must render even without
    # project context. Fall back to the generic binstub name rather than
    # raising when WORKTREE_PROJECT is unset.
    try:
        project = cfg.project_name()
    except Exception:
        project = "agent-worktrees"
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
    print("  doctor [--fix] [--json]             Reconcile projects.yaml <-> repos.yaml")
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

    # A subcommand-level help flag (e.g. `repos clone --help`) must show usage,
    # never be consumed as a positional value (a remote, name, or path).
    if "--help" in rest or "-h" in rest:
        _repos_usage()
        return 0

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

    if sub == "doctor":
        from . import doctor
        do_fix = "--fix" in rest
        json_out = "--json" in rest
        findings = doctor.reconcile(fix=do_fix)
        if json_out:
            _json_output({
                "fixed": do_fix,
                "findings": [
                    {
                        "repo": f.repo,
                        "kind": f.kind,
                        "severity": f.severity,
                        "detail": f.detail,
                        "fixable": f.fixable,
                        "fix_detail": f.fix_detail,
                        "fixed": f.fixed,
                    }
                    for f in findings
                ],
            })
        else:
            doctor.render(findings, fixed_mode=do_fix)
        # Unresolved errors -> non-zero exit so callers/CI can gate on it.
        unresolved = [
            f for f in findings if f.severity == doctor.SEV_ERROR and not f.fixed
        ]
        return 1 if unresolved else 0

    output.err(f"Unknown repos subcommand: {sub}")
    _repos_usage()
    return 1


def _related_usage() -> None:
    """Print related subcommand usage."""
    project = cfg.active_project() or "agent-worktrees"
    print(f"Usage: {project} related <command>")
    print()
    print("Per-project, directional 'related repos' index (this repo's POV),")
    print("committed at <repo>/.agent-worktrees/related.yaml. Keys reference the")
    print("global repos registry; entries add role + locus + delegate + a narrative.")
    print()
    print("Commands:")
    print("  list [--role R] [--json]            List related repos (and the primary)")
    print("  show <name> [--json]                Show a related repo (+ registry context)")
    print("  add <name>                          Link a related repo + scaffold its doc")
    print("     [--role R] [--summary S] [--doc PATH] [--delegate D]")
    print("     [--locus L] [--machines a,b] [--primary] [--no-scaffold]")
    print("     [--cs-repo R] [--cs-machine M] [--cs-location L]")
    print("     [--cs-workspace DIR]                                (codespace locus)")
    print("     [--container-repo R] [--container-workspace DIR]")
    print("     [--container-machines a,b]                          (container locus)")
    print("  remove <name>                       Unlink (leaves the narrative doc)")
    print("  doc <name>                          Print (scaffold if missing) the narrative")
    print("  primary [<name>]                    Show or set the primary related repo")
    print("  resolve [<name>]                    How to work on it from here (locus plan)")
    print()
    print("Any command takes [--repo PATH] to target a specific checkout")
    print("(default: the git repo containing the current directory).")
    print()
    print("Locus (where work happens): local | machine:<key> | codespace | container")
    print("Delegate (how to hand off): agent-bridge | agent-codespaces | agent-containers | none")


def _related_opt(rest: list[str], flag: str, default: str | None = None) -> str | None:
    """Return the value following ``flag`` in ``rest`` (or ``default``)."""
    if flag in rest:
        i = rest.index(flag)
        if i + 1 < len(rest):
            return rest[i + 1]
    return default


def _related_anchor(rest: list[str]) -> str | None:
    """Resolve the repo to operate on: --repo > git toplevel of cwd > project anchor."""
    explicit = _related_opt(rest, "--repo")
    if explicit:
        return explicit
    try:
        cp = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=10,
        )
        if cp.returncode == 0 and cp.stdout.strip():
            return cp.stdout.strip()
    except Exception:
        pass
    try:
        return cfg.load_config().default_repo.anchor
    except Exception:
        return None


def _related_lookup_anchor(
    rest: list[str], anchor: str, name: str,
) -> tuple[str, bool]:
    """Anchor to read ``name`` from for a read-only lookup, with a fallback.

    ``related`` is **cwd-directional**: it reads the ``related.yaml`` of the repo
    containing the current directory. Running a lookup from *inside* a
    coordinated repo's own checkout therefore reads that repo's (usually empty)
    POV and dead-ends. When the cwd anchor doesn't list ``name`` -- and the
    caller didn't pin an explicit ``--repo`` -- fall back to the **control-plane
    project's** index (the repo whose ``machines.yaml`` declares
    ``control_plane.project``), which is the canonical directional index.

    Returns ``(effective_anchor, via_control_plane)``. Fail-safe: any inability
    to resolve the control plane leaves the original anchor unchanged.
    """
    from . import related
    if _related_opt(rest, "--repo"):
        return anchor, False
    if related.get_related(anchor, name) is not None:
        return anchor, False
    cp = related.find_control_plane_anchor()
    if (cp and os.path.abspath(cp) != os.path.abspath(anchor)
            and related.get_related(cp, name) is not None):
        return cp, True
    return anchor, False


def cmd_related_dispatch(argv: list[str]) -> int:
    """Route related subcommands (per-project related-repos index)."""
    from . import related, repos

    if not argv or argv[0] in ("--help", "-h"):
        _related_usage()
        return 0 if argv else 1

    sub = argv[0]
    rest = argv[1:]
    if "--help" in rest or "-h" in rest:
        _related_usage()
        return 0

    anchor = _related_anchor(rest)
    if not anchor:
        output.err(
            "Could not resolve the current repo. Run inside a repo, or pass "
            "--repo <path>."
        )
        return 1

    json_out = "--json" in rest

    if sub == "list":
        role = _related_opt(rest, "--role")
        entries = related.list_related(anchor, role=role)
        primary = related.get_primary(anchor)
        if json_out:
            _json_output({
                "primary": primary,
                "related": [
                    {
                        "name": e.name, "role": e.role, "summary": e.summary,
                        "doc": e.doc, "delegate": e.delegate,
                        "locus": {
                            "preferred": e.locus.preferred,
                            "machines": e.locus.machines,
                            "codespace": e.locus.codespace,
                            "container": e.locus.container,
                        },
                    }
                    for e in entries
                ],
            })
        elif not entries:
            print("No related repos linked.")
            print(f"Link one with: {cfg.active_project() or 'agent-worktrees'} related add <name>")
        else:
            output.header("Related Repos")
            for e in entries:
                star = "  *primary" if e.name == primary else ""
                loc = e.locus.preferred or "-"
                print(f"  {e.name:<24} {e.role or '-':<11} locus={loc}{star}")
        return 0

    if sub == "show":
        if not rest or rest[0].startswith("-"):
            output.err("Usage: related show <name>")
            return 1
        name = rest[0]
        anchor, _via_cp = _related_lookup_anchor(rest, anchor, name)
        e = related.get_related(anchor, name)
        if e is None:
            output.err(f"'{name}' is not a related repo.")
            return 1
        reg = repos.find_repo(name)
        if json_out:
            _json_output({
                "name": e.name, "role": e.role, "summary": e.summary,
                "doc": e.doc, "delegate": e.delegate,
                "locus": {
                    "preferred": e.locus.preferred,
                    "machines": e.locus.machines,
                    "codespace": e.locus.codespace,
                    "container": e.locus.container,
                },
                "registry": None if reg is None else {
                    "class": reg.repo_class, "remote": reg.remote,
                    "path": reg.local_path(),
                },
            })
            return 0
        output.header(f"Related: {e.name}")
        print(f"  role:     {e.role or '-'}")
        print(f"  summary:  {e.summary or '-'}")
        print(f"  locus:    {e.locus.preferred or '-'}"
              + (f"  machines={e.locus.machines}" if e.locus.machines else "")
              + (f"  codespace={e.locus.codespace}" if e.locus.codespace else "")
              + (f"  container={e.locus.container}" if e.locus.container else ""))
        print(f"  delegate: {e.delegate or '-'}")
        print(f"  doc:      {related.doc_abs_path(anchor, e)}")
        if reg is None:
            output.warn(f"'{name}' is not in the repos registry "
                        f"(add it with: repos add {name} <path> --class <class>)")
        else:
            print(f"  registry: [{reg.repo_class}] {reg.local_path() or '(no local path)'}")
            if reg.remote:
                print(f"            {reg.remote}")
        return 0

    if sub == "add":
        if not rest or rest[0].startswith("-"):
            output.err("Usage: related add <name> [--role ...] [--locus ...] ...")
            return 1
        name = rest[0]
        machines_csv = _related_opt(rest, "--machines", "") or ""
        machines = [m.strip() for m in machines_csv.split(",") if m.strip()]
        codespace: dict = {}
        for flag, key in (("--cs-repo", "repo"), ("--cs-machine", "machine"),
                          ("--cs-location", "location"),
                          ("--cs-workspace", "workspace_folder")):
            v = _related_opt(rest, flag)
            if v:
                codespace[key] = v
        container: dict = {}
        for flag, key in (("--container-repo", "repo"),
                          ("--container-workspace", "workspace_folder")):
            v = _related_opt(rest, flag)
            if v:
                container[key] = v
        ct_machines_csv = _related_opt(rest, "--container-machines", "") or ""
        ct_machines = [m.strip() for m in ct_machines_csv.split(",") if m.strip()]
        if ct_machines:
            container["machines"] = ct_machines
        entry = related.RelatedEntry(
            name=name,
            role=related.normalize_role(_related_opt(rest, "--role", "")),
            summary=_related_opt(rest, "--summary", "") or "",
            doc=_related_opt(rest, "--doc", "") or "",
            locus=related.Locus(
                preferred=(_related_opt(rest, "--locus", "") or "").strip(),
                machines=machines,
                codespace=codespace,
                container=container,
            ),
            delegate=related.normalize_delegate(_related_opt(rest, "--delegate", "")),
        )
        if repos.find_repo(name) is None:
            output.warn(
                f"'{name}' is not in the repos registry. Link recorded anyway; "
                f"register it with: {cfg.active_project() or 'agent-worktrees'} "
                f"repos add {name} <path> --class <class>"
            )
        related.upsert_related(anchor, entry)
        if "--primary" in rest:
            related.set_primary(anchor, name)
        output.ok(f"Linked related repo '{name}'.")
        if "--no-scaffold" not in rest:
            saved = related.get_related(anchor, name) or entry
            path, created = related.scaffold_doc(anchor, saved)
            if created:
                output.ok(f"Scaffolded narrative: {path}")
            else:
                output.info(f"Narrative exists: {path}")
        return 0

    if sub == "remove":
        if not rest or rest[0].startswith("-"):
            output.err("Usage: related remove <name>")
            return 1
        name = rest[0]
        if related.remove_related(anchor, name):
            output.ok(f"Unlinked related repo '{name}' (narrative doc left in place).")
            return 0
        output.err(f"'{name}' is not a related repo.")
        return 1

    if sub == "doc":
        if not rest or rest[0].startswith("-"):
            output.err("Usage: related doc <name>")
            return 1
        name = rest[0]
        anchor, _via_cp = _related_lookup_anchor(rest, anchor, name)
        e = related.get_related(anchor, name)
        if e is None:
            output.err(f"'{name}' is not a related repo. Link it first: "
                       f"related add {name}")
            return 1
        path, created = related.scaffold_doc(anchor, e)
        print(path)
        if created:
            output.ok("(scaffolded)")
        return 0

    if sub == "primary":
        if rest and not rest[0].startswith("-"):
            name = rest[0]
            if related.get_related(anchor, name) is None:
                output.err(f"'{name}' is not a related repo. Link it first.")
                return 1
            related.set_primary(anchor, name)
            output.ok(f"primary = {name}")
        else:
            print(related.get_primary(anchor) or "(unset)")
        return 0

    if sub == "resolve":
        from . import doctor
        explicit_name = rest[0] if rest and not rest[0].startswith("-") else None
        name = explicit_name or related.get_primary(anchor)
        via_cp = False
        if not name and not _related_opt(rest, "--repo"):
            # Bare `resolve` from a repo with no primary of its own: fall back to
            # the control-plane index's primary so it still resolves something.
            cp = related.find_control_plane_anchor()
            if cp and os.path.abspath(cp) != os.path.abspath(anchor):
                cp_primary = related.get_primary(cp)
                if cp_primary:
                    anchor, name, via_cp = cp, cp_primary, True
        if not name:
            output.err("Usage: related resolve <name>  (or set a primary first)")
            return 1
        if explicit_name:
            anchor, via_cp = _related_lookup_anchor(rest, anchor, name)
        entry = related.get_related(anchor, name)
        if entry is None:
            output.err(f"'{name}' is not a related repo.")
            return 1
        reg = repos.find_repo(name)
        try:
            current_machine = cfg.detect_machine(anchor)
        except Exception:
            current_machine = ""
        try:
            projects = doctor._read_projects()
        except Exception:
            projects = {}
        adopted = name in projects
        base_repo = bool(projects.get(name, {}).get("base_repo", False))
        resn = related.build_resolution(
            entry,
            current_machine=current_machine,
            repo_class=(reg.repo_class if reg else None),
            repo_path=(reg.local_path() if reg else None),
            adopted=adopted,
            base_repo=base_repo,
        )
        if json_out:
            _json_output({
                "name": resn.name,
                "locus_kind": resn.locus_kind,
                "target_machine": resn.target_machine,
                "available_here": resn.available_here,
                "editing_model": resn.editing_model,
                "base_repo": base_repo,
                "delegate_via": resn.delegate_via,
                "current_machine": current_machine,
                "steps": resn.steps,
                "notes": resn.notes,
                "via_control_plane": via_cp,
            })
            return 0
        output.header(f"Resolve: {resn.name}")
        if via_cp:
            output.info(
                "(resolved via the control-plane index -- this repo's own "
                "related.yaml does not list it)")
        if entry.summary:
            print(f"  {entry.summary}")
        avail = "" if resn.available_here else "  (not available here)"
        print(f"  locus:    {entry.locus.preferred or 'local'}{avail}")
        print(f"  class:    {reg.repo_class if reg else '(not in registry)'}"
              + (f"  [{resn.editing_model}]" if resn.editing_model else ""))
        if reg and reg.local_path():
            print(f"  path:     {reg.local_path()}")
        if resn.delegate_via:
            print(f"  delegate: {resn.delegate_via}")
        print(f"  machine:  {current_machine or '(unknown)'}")
        for n in resn.notes:
            output.warn(n)
        print()
        print("  Plan:")
        for s in resn.steps:
            print(f"    - {s}")
        return 0

    output.err(f"Unknown related subcommand: {sub}")
    _related_usage()
    return 1


# ═══════════════════════════════════════════════════════════════════════════

# Bootstrap services that must be current before launching a session.
_BOOTSTRAP_SERVICES = ("agent-worktrees", "vault")


def plan_pre_launch() -> dict:
    """Check bootstrap service staleness and return an action plan dict.

    Returns:
      {"action": "continue"}  -- all bootstrap services are current
      {"action": "self-update", "updates": [...]}  -- services need updating

    Consumed both by ``cmd_pre_launch`` (which prints it as JSON for the shell
    wrapper) and by the background ``stage-update`` worker (which folds the
    ``updates`` into the staged pending-apply plan). The launcher executes the
    ``argv`` vectors and re-invokes pre-launch (max 1 retry).
    """
    repo_dir = _find_repo_dir()
    if not repo_dir:
        # Can't determine staleness -- proceed anyway
        return {"action": "continue", "reason": "no-repo"}

    try:
        config = cfg.load_config()
    except Exception:
        return {"action": "continue", "reason": "no-config"}

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
                    return {"action": "self-update", "updates": updates}

    updates = []
    for s in bootstrap.values():
        _append_update_if_stale(s, repo_dir, updates)

    if updates:
        return {"action": "self-update", "updates": updates}
    return {"action": "continue"}


def cmd_pre_launch(args: argparse.Namespace) -> int:
    """Emit the pre-launch staleness plan as JSON (see ``plan_pre_launch``)."""
    print(json.dumps(plan_pre_launch()))
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
      2. The (assumed) CWD git root (via git rev-parse)
      3. Config anchor (last resort -- may be stale)

    Resolution is from the directory, not ambient env: the former
    ``WORKTREE_REPO`` / ``APERTURE_REPO`` env fallback has been removed (it was
    a cross-session contamination source). All paths are resolved through
    :func:`git_ops.resolve_to_anchor` so that running from inside a git worktree
    returns the main checkout, not the ephemeral worktree path.
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

    # 2. git rev-parse to find repo root of the current directory
    try:
        r = subprocess.run(
            ["git", "-C", str(Path.cwd()), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0:
            return git_ops.resolve_to_anchor(Path(r.stdout.strip()))
    except Exception:
        pass

    # 3. Config anchor (last resort -- may deploy stale code if anchor
    #    hasn't been updated, but better than failing entirely)
    try:
        config = cfg.load_config()
        anchor = Path(config.default_repo.anchor)
        if anchor.exists():
            return anchor
    except Exception:
        pass

    return None


def _write_global_config(
    machine: str, plat: str, srcroot: Path | str,
) -> None:
    """Scaffold the global machine-wide config (~/.agent-worktrees/config.yaml).

    Carries machine-wide base settings (srcroot/machine/platform) plus
    user-authored copilot_profiles -- the lowest config tier. This file is
    **user-owned**: the installer scaffolds it once when missing, then **never**
    overwrites it -- not even with ``--force`` (which targets installer-owned
    artifacts, not the user's global base settings). The only thing that should
    ever rewrite it is a deliberate schema migration. Always skips an existing
    file so user-added profiles are never clobbered.
    """
    path = cfg.global_config_path()
    if path.exists():
        output.skipped(f"Global config exists at {path} (user-owned, left as-is)")
        return
    content = f"""# ~/.agent-worktrees/config.yaml
# GLOBAL machine-wide agent-worktrees config (lowest precedence tier).
#
# Machine-wide defaults shared across every project on this machine. Per-repo
# settings layer on top: <anchor>/.agent-worktrees/config.yaml (the repo's own
# config) then ~/.<project>/config.yaml (machine-local override).

srcroot: {srcroot}
machine: {machine}
platform: {plat}

# Copilot backend profiles -- machine-wide (Tab to cycle in the picker).
# User-authored; uncomment and edit. Example:
# copilot_profiles:
#   - name: cloud
#     label: "Cloud (GitHub)"
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    output.changed(f"Written global config: {path}")


def _write_config(
    path: Path, repo_dir: Path, machine: str, plat: str,
    project: str, default_branch: str = "master", *, headless: bool = False,
) -> None:
    """Write the machine-local per-project config YAML.

    Machine-wide fields (srcroot/machine/platform/copilot_profiles) live in the
    global ~/.agent-worktrees/config.yaml; repo settings may live in-repo at
    <anchor>/.agent-worktrees/config.yaml. This file keeps only the project
    marker and machine paths (anchor / worktree_root) plus repo defaults that a
    foreign repo without in-repo config still needs.
    """
    wt_root = f"{repo_dir}.worktrees"

    headless_line = "headless: true\n" if headless else ""
    content = f"""# ~/.{project}/config.yaml
# Machine-local config for {project} (overrides + machine paths only).
# Machine-wide defaults -> ~/.agent-worktrees/config.yaml.
# Repo settings may live in-repo -> <anchor>/.agent-worktrees/config.yaml.

repo_name: {project}
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
                   help="Create a worktree AND launch an interactive (muxed) "
                        "session in it -- for humans and TTY handoffs (refused "
                        "without a TTY). Agents/daemons should use "
                        "'agent-worktrees create --json' instead (no launch, no mux).")
    p.add_argument("--bridge", action="store_true",
                   help="With --new: mark the worktree as agent-bridge-owned "
                        "(kind=bridge: hidden from the Picker by default, exempt "
                        "from routine cleanup)")
    p.add_argument("--profile", help="Copilot backend profile name (skips Tab toggle)")
    p.add_argument("--machine", default=None,
                   help="Target machine name (bypasses machine picker)")
    p.add_argument("--parent-session", default=None, dest="parent_session",
                   help="With --new: session id that originated this worktree's "
                        "work, recorded so a later resume restores context (#1029). "
                        "Defaults to $COPILOT_AGENT_SESSION_ID.")
    p.add_argument("--caller-worktree", default=None, dest="caller_worktree",
                   help="With --new: the caller worktree id that requested this "
                        "(bridge) worktree, recorded so the Picker can jump back "
                        "to it (#2178).")
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
    p.add_argument("--repo", default=None,
                   help="Target repo 'owner/name' for the PR (default: the worktree repo)")
    p.add_argument("--new", action="store_true",
                   help="Force a brand-new PR (fresh branch) even if a live PR is open")
    p.add_argument("--body", default=None,
                   help="PR body text (a source-attribution marker is appended)")
    p.add_argument("--body-file", default=None, dest="body_file",
                   help="Read the PR body from a file")
    p.add_argument("--no-open", action="store_true", dest="no_open",
                   help="Push the branch only; do not auto-open the PR via the provider")
    p.add_argument("--hold", action="store_true",
                   help="Open the PR held (do-not-merge): reviewed but not merged "
                        "until 'pr-ready'. Lets you iterate on the open PR.")
    p.add_argument("--no-attribution", action="store_true", dest="no_attribution",
                   help="Do not embed the source-worktree attribution marker in the PR body")
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
    p.add_argument("--pr", type=int, default=None,
                   help="Select which tracked PR to update by number (default: the active PR)")
    p.add_argument("--select-branch", default=None, dest="select_branch",
                   help="Select which tracked PR to update by feature branch")
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    # pr-ready (remove the merge-only hold label)
    p = sub.add_parser("pr-ready", help="Release a held PR for merge")
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--repo", default=None,
                   help="Target repo 'owner/name' for the PR (default: tracked repo)")
    p.add_argument("--pr", type=int, default=None,
                   help="Select which tracked PR to release by number")
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    # pr-status (read tracked PR metadata)
    p = sub.add_parser(
        "pr-status",
        help="Show tracked PR metadata (reconciles against the provider; "
             "recommends pull-forward when the active PR has merged)",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--all", action="store_true",
                   help="List every tracked PR, not just the active one")
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

    # status-segment (one styled line for a tmux/psmux status bar)
    p = sub.add_parser(
        "status-segment",
        help="Print a tmux/psmux status-bar segment for the worktree at cwd",
    )
    p.add_argument("--path", default=None,
                   help="Worktree path to classify (default: current directory)")
    p.add_argument("--fetch", action="store_true",
                   help="Fetch before classifying (refreshes behind-counts; slower)")
    p.add_argument("--plain", action="store_true",
                   help="Plain text without tmux #[style] directives")
    p.add_argument("--no-title", action="store_true",
                   help="Omit the worktree title; show only the state block")

    # status-context (left status-bar segment: machine / env / repo:id)
    p = sub.add_parser(
        "status-context",
        help="Print a tmux/psmux left status segment (machine, env, repo:id)",
    )
    p.add_argument("--path", default=None,
                   help="Worktree path to describe (default: current directory)")
    p.add_argument("--plain", action="store_true",
                   help="Plain text without tmux #[style] directives")

    # status-updater (background loop: refresh @aw_ctx/@aw_seg off the paint path)
    p = sub.add_parser(
        "status-updater",
        help="Background loop: keep a session's @aw_ctx/@aw_seg status vars "
             "fresh (no per-render binstub spawns)",
    )
    p.add_argument("--session", required=True,
                   help="Mux session name to update (e.g. wt-<id>)")
    p.add_argument("--mux", default=None, choices=["psmux", "tmux"],
                   help="Multiplexer binary (default: auto-detect)")
    p.add_argument("--path", default=None,
                   help="Worktree path to classify (default: current directory)")
    p.add_argument("--interval", type=int, default=15,
                   help="Disposition refresh cadence in seconds (min 2)")
    # handoff-cutover (live-cutover handoff: seeded successor window + pane retire)
    p = sub.add_parser(
        "handoff-cutover",
        help="Live handoff: spawn a seeded successor Copilot in a new mux "
             "window (cut over to it), or retire an old pane",
    )
    p.add_argument("--seed", default=None,
                   help="Seed prompt for the successor's first interactive "
                        "turn (copilot -i). Required in spawn mode.")
    p.add_argument("--worktree-id", dest="worktree_id", default=None,
                   help="Target worktree (default: infer from cwd)")
    p.add_argument("--old-pane", dest="old_pane", default=None,
                   help="Explicit pane id to report as the old pane "
                        "(default: the session's active pane)")
    p.add_argument("--retire-pane", dest="retire_pane", default=None,
                   help="Retire mode: double-Ctrl-C this pane id (Copilot's "
                        "clean quit) and report whether it exited")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the resolved plan without opening a window")
    p.add_argument("--json", action="store_true",
                   help="JSON output mode (stdout is JSON only; always on)")
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
    p.add_argument("--classify", action="store_true",
                   help="Include git state classification (state/ahead/behind/"
                        "dirty; JSON only). Slower: ~5 git calls per worktree.")

    # create (non-interactive worktree creation; --system for daemon-owned)
    p = sub.add_parser(
        "create",
        help="Create a worktree programmatically (no launch, no mux) -- the "
             "path for agents/daemons; prints id + dir (add --json for a plan)",
    )
    p.add_argument("--system", action="store_true",
                   help="Create a daemon-owned worktree (hidden from Picker, "
                        "cleanup-exempt; tear down with remove-system)")
    p.add_argument("--name", default=None,
                   help="With --system: short slug for the worktree id (e.g. the service name)")
    p.add_argument("--owner", default=None,
                   help="With --system: owning service name (recorded for the browse view)")
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
    p.add_argument("--worktree-id", default=None,
                   help="Clean a single worktree by ID (non-interactive, "
                        "re-checks prune-safety; pair with --json for the "
                        "picker's per-item progress)")
    p.add_argument("--force", action="store_true",
                   help="With --worktree-id: reap even if prune-safety would "
                        "skip it (still refuses an active session)")
    p.add_argument("--json", action="store_true",
                   help="With --worktree-id: emit a single JSON result object")
    p.add_argument("--include-unused", action="store_true",
                   help="Also clean truly-empty worktrees (no commits, "
                        "zero conversation turns)")
    p.add_argument("--include-conversations", action="store_true",
                   help="Also clean conversation-only worktrees (no commits "
                        "but the session held turns); implies --include-unused")
    p.add_argument("--reconcile-prs", action="store_true",
                   help="Refresh tracked PR state from the provider before "
                        "deciding (heals stale 'open' PRs merged externally); "
                        "requires network + provider credentials")
    p.add_argument("--max-age-days", type=int, default=7)

    # reap-sessions (GC orphaned tmux/psmux sessions -- issue #713)
    p = sub.add_parser(
        "reap-sessions",
        help="Reap leaked tmux/psmux sessions whose worktree is finalized, "
             "gone, or untracked AND has been idle past the grace window "
             "(never touches attached, active, or busy sessions)")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would be reaped without killing anything")
    p.add_argument("--id", default=None,
                   help="Target a single worktree id; same spare-attached/"
                        "active/busy predicate as the full sweep")
    p.add_argument("--grace-hours", type=float, default=None,
                   help="Idle window before a finalized/idle session is "
                        "eligible (default 6h); a busy session is never reaped")
    p.add_argument("--json", action="store_true",
                   help="Emit a single JSON result object")

    # restart (terminate a worktree's interactive Copilot, keep the worktree)
    p = sub.add_parser(
        "restart",
        help="Stop a worktree's interactive Copilot (graceful double Ctrl-C, "
             "then mux kill-session) -- keeps the worktree on disk. The shared "
             "primitive behind the Picker 'Stop' action and NF 'Take over'; "
             "relaunch/ACP-resume is performed by the caller.")
    p.add_argument("worktree_id", help="Worktree id whose Copilot to stop")
    p.add_argument("--no-graceful", action="store_true",
                   help="Skip the graceful double-Ctrl-C quit; hard-kill the "
                        "mux session immediately")
    p.add_argument("--settle-timeout", type=float, default=6.0,
                   help="Seconds to wait for a graceful quit before hard-killing "
                        "(default: 6.0)")
    p.add_argument("--json", action="store_true",
                   help="Emit a single JSON result object")

    # sync (fast-forward worktrees to the default branch, FF-only)
    p = sub.add_parser("sync", help="Fast-forward worktrees to the default branch")
    p.add_argument("--worktree-id", default=None,
                   help="Sync a single worktree by ID (default: all active "
                        "worktrees on this machine)")
    p.add_argument("--all", action="store_true",
                   help="Sync every active worktree (the default when no "
                        "--worktree-id is given)")
    p.add_argument("--json", action="store_true",
                   help="Emit JSON results (a single object with --worktree-id, "
                        "else {\"results\": [...]})")

    # profiles (terminal-profile selection -- the Picker's Profiles grid column)
    p = sub.add_parser("profiles",
                       help="Read or write this machine's terminal-profile "
                            "selection (the Picker's Profiles column)")
    p.add_argument("profiles_action", choices=["get", "apply"],
                   help="get: emit this host's selected launch targets; "
                        "apply: persist a new selection (--set) and mirror it")
    p.add_argument("--set", default=None,
                   help="With apply: a JSON array of {machine, env, kind} "
                        "objects -- the new column for this host (the locked "
                        "self·agent target is always included)")
    p.add_argument("--no-mirror", action="store_true",
                   help="With apply: persist the selection but skip "
                        "regenerating the terminal profiles")
    p.add_argument("--json", action="store_true",
                   help="Emit a JSON result object")

    # picker (Textual picker is default everywhere; disable = machine opt-out)
    p = sub.add_parser("picker",
                       help="Inspect / opt out of the Textual worktree picker "
                            "(the default) for this machine")
    p.add_argument("picker_action", choices=["enable", "disable", "status", "mock"],
                   nargs="?", default="status",
                   help="the Textual picker is the default everywhere; "
                        "disable writes new_picker:false to opt this machine out "
                        "to the legacy picker, enable restores the default "
                        "(~/.agent-worktrees/config.yaml); status (default) "
                        "reports the effective value; mock launches the picker "
                        "in the mock dev sandbox (real data, simulated actions, "
                        "no side effects)")
    p.add_argument("--json", action="store_true", help="Emit a JSON result")

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

    # related -- dispatched pre-argparse (see cmd_related_dispatch)
    sub.add_parser("related", help="Per-project related repos (run 'related' for usage)")

    # git -- dispatched pre-argparse (see cmd_git_dispatch)
    sub.add_parser("git", help="Git collaboration primitives (run 'git' for usage)")

    # pre-launch (two-pass self-update protocol)
    sub.add_parser("pre-launch", help="Check bootstrap staleness (JSON output)")

    # stage-update (background marketplace download; #1430 stage-then-join)
    sp = sub.add_parser(
        "stage-update",
        help="Background-stage the plugin marketplace update (JSON status)")
    sp.add_argument("--status", default=None,
                    help="Status file path (defaults to ~/.agent-worktrees/updater-status.json)")
    sp.add_argument("--json", action="store_true", help="Echo the status dict to stdout")

    # reconcile-plugins (repo-configured plugin payload + runtime reconcile)
    sp = sub.add_parser(
        "reconcile-plugins",
        help="Reconcile repo enabledPlugins payloads + gated runtimes (JSON)")
    sp.add_argument("--machine", default=None,
                    help="Machine name (auto-detected from hostname if omitted)")
    sp.add_argument("--repo", default=None,
                    help="Repo path to reconcile (defaults to the resolved anchor)")

    # reconcile-binstubs (project launchers in ~/.local/bin vs projects.yaml)
    sub.add_parser(
        "reconcile-binstubs",
        help="Reconcile ~/.local/bin project binstubs against projects.yaml "
             "(add for every registered project, remove deregistered ones)")

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

    # list-sessions -- enumerate a worktree's Copilot sessions as JSON
    sp = sub.add_parser(
        "list-sessions",
        help="List a worktree's Copilot sessions with metadata (JSON)",
    )
    sp.add_argument("--worktree", "--worktree-id", dest="worktree_id", default=None,
                    help="Worktree ID to scope to (default: all worktrees)")
    sp.add_argument("--json", action="store_true",
                    help="Emit JSON (default; accepted for caller compatibility)")

    # session-transcript -- emit a session's renderable events as JSON
    sp = sub.add_parser(
        "session-transcript",
        help="Emit a Copilot session's renderable transcript events (JSON)",
    )
    sp.add_argument("session_id", help="Copilot session ID")
    sp.add_argument("--json", action="store_true",
                    help="Emit JSON (default; accepted for caller compatibility)")

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
        # The sessionStart hook runs from the *plugin install dir*, not the
        # worktree, and register-session is a no-project command -- so main()
        # never resolved a project and cfg.tracking_dir() (used by the lookup
        # below) would raise.  The payload's cwd *is* the worktree, so resolve
        # project context from it before the lookup (mirrors status-updater's
        # _activate_project_for_path fix).  Guard the lookup so a cwd outside
        # any adopted project stays a silent no-op rather than an error.
        _activate_project_for_path(cwd)
        try:
            wt_id = tracking.find_worktree_id_by_cwd(cwd)
        except Exception:
            return 0
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
    # Infer the worktree from CWD (git-like) when not passed explicitly -- the
    # sessionEnd hook runs in the worktree, so no ambient WORKTREE_ID is needed.
    if not wt_id:
        wt_id = _infer_worktree_id(None)
    if wt_id:
        wt_id = _resolve_worktree_id(wt_id)
    if not wt_id or not session_id:
        output.err(
            "Usage: deregister-session --session-id ID "
            "[--worktree-id ID | run from inside the worktree]"
        )
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


def _capture_session_title(worktree_id: str, session_id: str) -> bool:
    """Read summary/name from the session's workspace.yaml and persist it
    to the tracking YAML ``title`` field if not already set.

    This ensures the worktree retains a descriptive title even after the
    Copilot session-state directory is cleaned up.  Returns ``True`` when a
    title was written, ``False`` otherwise (already titled, session-state
    gone, or no usable summary) -- so callers can try sessions newest-first.
    """
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return False

    rec = tracking.load_record(yaml_path)
    if rec.title and rec.title != "null":
        return False  # already has a title

    # Read summary/name from the session's workspace.yaml
    session_dir = sessions._session_state_dir() / session_id
    ws_file = session_dir / "workspace.yaml"
    if not ws_file.exists():
        return False
    # Detached parent-continuation sessions (subconscious / rem-agent runs)
    # reuse the parent's cwd and carry the generic "Apply context_board ..."
    # prompt as their name/summary -- never a meaningful worktree title.
    if sessions._is_detached_session(session_dir):
        return False

    try:
        with open(ws_file, encoding="utf-8") as f:
            ws_data = yaml.safe_load(f)
    except Exception:
        return False

    if not ws_data or not isinstance(ws_data, dict):
        return False

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
        return True
    return False


def cmd_backfill_sessions(args: argparse.Namespace) -> int:
    """Populate empty session registries -- and the Picker's title slot --
    from existing session-state data.

    Two independent passes:

      1. **Session registry** -- records with an empty ``sessions`` list get
         their discovered session ids written back.

      2. **Title (overall summary)** -- records lacking a ``title`` get one
         captured from their newest session's ``workspace.yaml`` (newest
         first, falling back to older sessions whose state still exists), so
         the Picker shows the summary instead of "(untitled)".  Runs even
         when every record already has session data -- e.g. after an earlier
         sessions-only backfill that left titles null.
    """
    tracking_path = cfg.tracking_dir()
    records = tracking.list_records(tracking_path)

    # --- Pass 1: session registry (only records with empty sessions) ---
    need_backfill = [r for r in records if not r.sessions]
    discovered: dict[str, list[str]] = {}
    sess_updated = 0
    if need_backfill:
        print(f"Scanning session-state for {len(need_backfill)} worktree(s)...")
        discovered = sessions.backfill_sessions(need_backfill)
        for rec in need_backfill:
            sids = discovered.get(rec.worktree_id, [])
            if not sids:
                # Mark as indexed (empty list) so we don't rescan
                if rec.sessions is None:
                    rec.sessions = []
                    tracking.save_record(rec)
                    sess_updated += 1
                continue

            rec.sessions = [
                tracking.SessionEntry(session_id=sid, started_at="")
                for sid in sids
            ]
            tracking.save_record(rec)
            sess_updated += 1

    # --- Pass 2: title slot (any record still lacking a title) ---
    # Use the same scan the Picker reads from (skips detached subconscious
    # sessions and picks the newest summary by updated_at), so a backfilled
    # title matches exactly what the Picker/status-bar would otherwise derive
    # live -- and survives later session-state cleanup.
    titled = 0
    title_targets = [r for r in records if not (r.title and r.title != "null")]
    if title_targets:
        tctx = sessions.scan_sessions_fast(title_targets)
        for rec in title_targets:
            summary = tctx.latest_summary.get(
                _normalize_path(rec.worktree_path), "")
            if summary and summary != "null":
                rec.title = summary
                tracking.save_record(rec)
                titled += 1

    total_sessions = sum(len(v) for v in discovered.values())
    print(
        f"Backfilled {total_sessions} session(s) across "
        f"{len(discovered)} worktree(s); "
        f"{sess_updated} registry + {titled} title record(s) updated"
    )
    return 0


def cmd_list_sessions(args: argparse.Namespace) -> int:
    """List a worktree's Copilot sessions with metadata as JSON.

    Scopes to a single worktree with ``--worktree ID``; without it,
    enumerates sessions across all tracked worktrees.  Each session entry
    is decorated with its ``worktree_id``.  Always emits the versioned
    JSON envelope (machine-facing -- consumed by agent-bridge).
    """
    tracking_path = cfg.tracking_dir()
    wt_id = getattr(args, "worktree_id", None)
    records = tracking.list_records(tracking_path)
    if wt_id:
        records = [r for r in records if r.worktree_id == wt_id]
        if not records:
            return _json_error(f"No worktree found: {wt_id}")

    result: list[dict] = []
    for rec in records:
        for s in sessions.list_worktree_sessions(rec):
            s["worktree_id"] = rec.worktree_id
            result.append(s)

    _json_output({"sessions": result})
    return 0


def cmd_session_transcript(args: argparse.Namespace) -> int:
    """Emit a single session's renderable transcript events as JSON.

    Reads the session's ``events.jsonl`` from local session-state and
    returns the renderable event subset.  An absent/empty session yields
    an empty ``events`` list (not an error) so callers can treat "no
    transcript" uniformly.
    """
    session_id = args.session_id
    events = sessions.read_session_transcript(session_id)
    _json_output({"session_id": session_id, "events": events})
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


def cmd_reconcile_binstubs(args: argparse.Namespace) -> int:
    """Reconcile ~/.local/bin project binstubs against the projects registry."""
    inst.reconcile_binstubs()
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
    "pr-ready": cmd_pr_ready,
    "pr-status": cmd_pr_status,
    "mark-complete": cmd_mark_complete,
    "status": cmd_status,
    "status-segment": cmd_status_segment,
    "status-context": cmd_status_context,
    "status-updater": cmd_status_updater,
    "handoff-cutover": cmd_handoff_cutover,
    "list": cmd_list,
    "create": cmd_create,
    "remove-system": cmd_remove_system,
    "cleanup": cmd_cleanup,
    "reap-sessions": cmd_reap_sessions,
    "restart": cmd_restart,
    "sync": cmd_sync,
    "profiles": cmd_profiles,
    "picker": cmd_picker,
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
    "stage-update": cmd_stage_update,
    "reconcile-plugins": cmd_reconcile_plugins,
    "reconcile-binstubs": cmd_reconcile_binstubs,
    "dev": cmd_dev,
    "register-session": cmd_register_session,
    "deregister-session": cmd_deregister_session,
    "backfill-sessions": cmd_backfill_sessions,
    "list-sessions": cmd_list_sessions,
    "session-transcript": cmd_session_transcript,
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
# register-session is hook-invoked from the *plugin install dir* (not a
# worktree), so CWD-based project resolution in main() would balk; it resolves
# its own project from the sessionStart payload's cwd instead (see
# cmd_register_session -> _activate_project_for_path).
_NO_PROJECT_COMMANDS = {
    "--version", "-V", "--help", "-h", "repos", "install", "register", "hook",
    "picker", "reap-sessions", "status-updater", "restart", "register-session",
}


def _anchor_for_project(name: str) -> Path | None:
    """Return the anchor checkout path for project *name*, or ``None``.

    Prefers the projects registry, falling back to the repos registry. Used to
    realize ``--project X`` as "assume CWD is X's anchor repo".
    """
    try:
        projects = inst.read_projects_registry().get("projects", {})
        entry = projects.get(name)
        if isinstance(entry, dict) and entry.get("anchor"):
            p = Path(entry["anchor"])
            if p.is_dir():
                return p.resolve()
    except Exception:
        pass
    try:
        anchor = cfg._resolve_anchor_from_registry(name, cfg.detect_platform())
        if anchor and Path(anchor).is_dir():
            return Path(anchor).resolve()
    except Exception:
        pass
    return None


def _reverse_lookup_project(anchor: Path) -> str | None:
    """Map an anchor checkout path back to its adopted project name, or ``None``.

    This is the git-like "which project am I in?" query: given the anchor of the
    repo discovered from CWD, find the adopted project whose anchor it is.
    Case-insensitive on Windows (via ``git_ops._normalize_wt_path``).
    """
    target = git_ops._normalize_wt_path(str(anchor))
    try:
        projects = inst.read_projects_registry().get("projects", {})
    except Exception:
        projects = {}
    for name, entry in projects.items():
        a = entry.get("anchor") if isinstance(entry, dict) else None
        if a and git_ops._normalize_wt_path(str(Path(a))) == target:
            return name
    try:
        from . import repos as _repos

        registry = _repos.read_registry()
        plat = cfg.detect_platform()
        for name in registry.repos:
            a = registry.repos[name].local_path(plat)
            if a and git_ops._normalize_wt_path(str(Path(a))) == target:
                return name
    except Exception:
        pass
    return None


def _cwd_is_inside_project(anchor: Path) -> bool:
    """Return True if the current directory belongs to the repo at *anchor*.

    Uses the git toplevel of CWD, resolved to its anchor, compared
    case-insensitively on Windows.
    """
    top = _git_toplevel(Path.cwd())
    if top is None:
        return False
    return git_ops._normalize_wt_path(str(top)) == git_ops._normalize_wt_path(str(anchor))


def _resolve_active_project(
    project_override: str | None,
) -> tuple[str | None, Path | None]:
    """Resolve ``(project, anchor)`` the way git resolves its repo.

    - ``--project X`` -> ``(X, anchor(X))``.
    - otherwise -> reverse-lookup the project from the real CWD's git anchor,
      returning ``(project, None)``.

    Returns ``(None, None)`` when nothing resolves (caller balks helpfully).
    Branch names and ambient env vars are never consulted. The caller decides
    whether to ``chdir`` to the anchor (see ``main()``): a project binstub run
    from *inside* one of its worktrees keeps the current directory (acting on
    that worktree), while one run from an unrelated directory changes to the
    project's anchor.
    """
    if project_override:
        return project_override, _anchor_for_project(project_override)
    cwd_anchor = _git_toplevel(Path.cwd())
    if cwd_anchor is not None:
        name = _reverse_lookup_project(cwd_anchor)
        if name:
            return name, None
    return None, None


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
            f"Could not resolve a project for '{requested}'. Context is "
            f"discovered from the current directory (like git), but this "
            f"directory is not inside an adopted repo or worktree, and no "
            f"--project was given.",
            file=out,
        )
    else:
        print(
            "Could not resolve a project. Context is discovered from the "
            "current directory (like git), but this directory is not inside "
            "an adopted repo or worktree. Run from inside one, use a project "
            "binstub, or pass --project <name>.",
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


# ═══════════════════════════════════════════════════════════════════════════
# git -- collaboration primitives (sync / feature-branch / merge-to-feature)
# ═══════════════════════════════════════════════════════════════════════════

def _git_usage() -> None:
    output.header("agent-worktrees git -- collaboration primitives")
    print("  Usage: agent-worktrees git <command> [options]")
    print()
    print("  Commands:")
    print("    sync                  Rebase the worktree branch forward onto the")
    print("                          updated remote default branch (build on top")
    print("                          of a just-merged PR). Mid-flight: no push.")
    print("    feature-branch <name> Create/update [--push] or --sync a durable")
    print("                          shared feature branch (feature/<name>).")
    print("    merge-to-feature <name>")
    print("                          Rebase + ff-merge this worktree's branch into")
    print("                          the shared feature branch and push it (the")
    print("                          delegate handoff). --no-push to stop at ff.")
    print()
    print("  Common options: [--worktree-id ID] [--config PATH] [--dry-run] [--json]")
    print()
    print("  See the 'git-collaboration' skill for the full boundary -- which git")
    print("  operations to wrap vs. run directly.")


def _git_resolve_target(rest: list[str], use_json: bool):
    """Resolve (config, worktree_id) for a git sub-group command.

    Returns ``(config, worktree_id)`` or ``(None, <rc>)`` on error -- callers
    check ``config is None`` and return the int.
    """
    config_arg = None
    worktree_id_arg = None
    if "--config" in rest:
        i = rest.index("--config")
        if i + 1 < len(rest):
            config_arg = rest[i + 1]
    if "--worktree-id" in rest:
        i = rest.index("--worktree-id")
        if i + 1 < len(rest):
            worktree_id_arg = rest[i + 1]
    try:
        config = cfg.load_config(Path(config_arg) if config_arg else None)
    except Exception as e:
        if use_json:
            return None, _json_error(str(e))
        raise
    worktree_id = _infer_worktree_id(worktree_id_arg, config)
    if not worktree_id:
        msg = (
            "Could not determine worktree ID. Pass --worktree-id or run from "
            "inside a worktree."
        )
        if use_json:
            return None, _json_error(msg)
        output.err(msg)
        return None, 1
    return config, _resolve_worktree_id(worktree_id)


def _git_positional(rest: list[str]) -> str | None:
    """First non-flag, non-option-value token (the <name> argument)."""
    value_flags = {"--worktree-id", "--config"}
    skip = False
    for tok in rest:
        if skip:
            skip = False
            continue
        if tok in value_flags:
            skip = True
            continue
        if tok.startswith("-"):
            continue
        return tok
    return None


def cmd_git_sync(rest: list[str]) -> int:
    if "--help" in rest or "-h" in rest:
        print(
            "Usage: agent-worktrees git sync "
            "[--worktree-id ID] [--config PATH] [--dry-run] [--json]"
        )
        return 0
    dry_run = "--dry-run" in rest
    use_json = "--json" in rest
    from . import git_collab

    ctx = output.stdout_to_stderr() if use_json else None
    if ctx is not None:
        ctx.__enter__()
    try:
        config, wid = _git_resolve_target(rest, use_json)
        if config is None:
            return wid
        ok = git_collab.sync_forward(wid, config, dry_run=dry_run)
        if use_json:
            _json_output({"worktree_id": wid, "synced": ok})
        return 0 if ok else 1
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def cmd_git_feature_branch(rest: list[str]) -> int:
    if "--help" in rest or "-h" in rest:
        print(
            "Usage: agent-worktrees git feature-branch <name> [--push] [--sync] "
            "[--worktree-id ID] [--config PATH] [--dry-run] [--json]"
        )
        return 0
    name = _git_positional(rest)
    if not name:
        output.err("Usage: agent-worktrees git feature-branch <name> [--push] [--sync]")
        return 1
    push = "--push" in rest
    sync = "--sync" in rest
    dry_run = "--dry-run" in rest
    use_json = "--json" in rest
    if push and sync:
        output.err("--push and --sync are mutually exclusive.")
        return 1
    from . import git_collab

    ctx = output.stdout_to_stderr() if use_json else None
    if ctx is not None:
        ctx.__enter__()
    try:
        config, wid = _git_resolve_target(rest, use_json)
        if config is None:
            return wid
        ok = git_collab.manage_feature_branch(
            wid, config, name, push=push, sync=sync, dry_run=dry_run,
        )
        if use_json:
            _json_output({"worktree_id": wid, "feature": name, "ok": ok})
        return 0 if ok else 1
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def cmd_git_merge_to_feature(rest: list[str]) -> int:
    if "--help" in rest or "-h" in rest:
        print(
            "Usage: agent-worktrees git merge-to-feature <name> [--no-push] "
            "[--worktree-id ID] [--config PATH] [--dry-run] [--json]"
        )
        return 0
    name = _git_positional(rest)
    if not name:
        output.err("Usage: agent-worktrees git merge-to-feature <name> [--no-push]")
        return 1
    push = "--no-push" not in rest
    dry_run = "--dry-run" in rest
    use_json = "--json" in rest
    from . import git_collab

    ctx = output.stdout_to_stderr() if use_json else None
    if ctx is not None:
        ctx.__enter__()
    try:
        config, wid = _git_resolve_target(rest, use_json)
        if config is None:
            return wid
        ok = git_collab.merge_to_feature(wid, config, name, push=push, dry_run=dry_run)
        if use_json:
            _json_output({"worktree_id": wid, "feature": name, "merged": ok})
        return 0 if ok else 1
    finally:
        if ctx is not None:
            ctx.__exit__(None, None, None)


def cmd_git_dispatch(argv: list[str]) -> int:
    """Route `git` sub-group verbs (git-collaboration primitives)."""
    if not argv or argv[0] in ("--help", "-h"):
        _git_usage()
        return 0 if argv else 1
    sub = argv[0]
    rest = argv[1:]
    if sub == "sync":
        return cmd_git_sync(rest)
    if sub == "feature-branch":
        return cmd_git_feature_branch(rest)
    if sub == "merge-to-feature":
        return cmd_git_merge_to_feature(rest)
    output.err(f"Unknown git subcommand: {sub}")
    _git_usage()
    return 1


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

    # ── Resolve the active project + assumed CWD (git-like) ──────────────
    # Context is discovered from the current directory, or an explicit
    # --project (which means "assume CWD is that project's anchor repo").
    # Ambient $WORKTREE_PROJECT / $WORKTREE_ID are NOT trusted for identity --
    # resolution is a pure function of where you are, not inherited session env.
    args_list, _proj = _extract_project_flag(args_list)

    # Only auto-derive from CWD for project-requiring commands (skip the git
    # subprocess for global no-project commands and bare flags).
    _needs_project = not (
        args_list
        and (args_list[0] in _NO_PROJECT_COMMANDS or args_list[0].startswith("-"))
    )

    if _proj:
        _project, _assumed = _resolve_active_project(_proj)
    elif _needs_project:
        _project, _assumed = _resolve_active_project(None)
    else:
        _project, _assumed = None, None

    if _proj:
        _project, _assumed = _resolve_active_project(_proj)
    elif _needs_project:
        _project, _assumed = _resolve_active_project(None)
    else:
        _project, _assumed = None, None

    if _project:
        cfg.set_active_project(_project)
        os.environ["WORKTREE_PROJECT"] = _project
        # git-like `-C`: when --project targets a project the caller is NOT
        # already inside, change to its anchor so every downstream path
        # (worktree-id inference, repo discovery, git subprocesses) resolves
        # consistently. When the caller IS inside one of the project's
        # worktrees, keep the current directory so the binstub acts on THAT
        # worktree (the common sign-off case: `<project> push-changes`).
        if _proj and _assumed is not None and not _cwd_is_inside_project(_assumed):
            try:
                os.chdir(_assumed)
            except OSError:
                pass

    has_project = bool(cfg.active_project()) or bool(
        os.environ.get("WORKTREE_PROJECT", "").strip()
    )

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

    # Related (per-project related repos) -- manual dispatch.
    if args_list[0] == "related":
        try:
            return cmd_related_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # git -- collaboration primitives (manual dispatch).
    if args_list[0] == "git":
        try:
            return cmd_git_dispatch(args_list[1:])
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
        except (FileNotFoundError, ValueError) as e:
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
