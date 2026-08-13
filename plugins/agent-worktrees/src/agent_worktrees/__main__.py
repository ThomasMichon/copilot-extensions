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
    agent-worktrees embody [--worktree-id <id> | --new] [--seed S]  # spawn mux+Copilot
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
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import yaml

from . import (
    activity,
    git_ops,
    locks,
    output,
    permissions,
    pr_ops,
    procs,
    prune,
    reclaim,
    sessions,
    tracking,
)
from . import claimant as claimant_mod
from . import config as cfg
from . import finalize as fin
from . import installer as inst
from . import obligations
from . import services as svc
from . import state_root as state_root_mod
from . import validate as val
from .picker import ItemKind, MenuItem, pick
from .update_stage import cmd_stage_update, discover_plugin_dir

# ── Env var migration helpers ───────────────────────────────────────────
# Phase 2 of copilot-worktrees extraction: APERTURE_* → WORKTREE_* for the
# operational flags. The APERTURE_* *identity* twins (WORKTREE_ID / WORKTREE_REPO)
# were retired with the cwd-resolution effort (Phase 3): identity resolves from
# CWD, never from an ambient env var, so those aliases had zero readers.
# Read new name first, fall back to old for backward compat.

_ENV_MIGRATION = {
    "WORKTREE_NO_UPDATE": "APERTURE_NO_UPDATE",
    "WORKTREE_NO_MUX": "APERTURE_NO_MUX",
    "WORKTREE_VERBOSE": "APERTURE_PRE_FLIGHT_VERBOSE",
}

_SESSION_BIND_PROJECT = "AGENT_WORKTREES_BIND_PROJECT"
_SESSION_BIND_WORKTREE = "AGENT_WORKTREES_BIND_WORKTREE_ID"
_SESSION_BIND_SESSION = "AGENT_WORKTREES_BIND_SESSION_ID"


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
        # Two launch shapes on Windows (copilot-extensions #102 -- launcher
        # depth):
        #   * ACP / --stdio: keep the cmd.exe -> .cmd shim. The .cmd forwards
        #     stdin verbatim, which a stdio MCP server requires
        #     (docs/patterns/cross-platform-parity.md); dropping it risks
        #     corrupting the JSON-RPC channel.
        #   * Interactive: hand off straight to `pwsh -File launch-session.ps1`,
        #     dropping the cmd.exe shim entirely -- one fewer resident process
        #     per worktree session. The .cmd's only extra job (native recovery
        #     when the venv is broken) is unreachable here anyway: reaching
        #     cmd_launch means Python already ran, so the venv is healthy.
        is_stdio = "--stdio" in passthrough or "--acp" in passthrough
        ps1 = launch_script.with_name("launch-session.ps1")
        if is_stdio or not ps1.exists():
            argv = ["cmd.exe", "/c", str(launch_script), *passthrough]
        else:
            argv = ["pwsh.exe", "-NoProfile", "-NoLogo", "-File",
                    str(ps1), *passthrough]
        # Popen + wait (never os.exec on Windows, which has no true exec and
        # would detach the child from the console): hold the console and catch
        # KeyboardInterrupt (Ctrl+C) so the child (launch-session.ps1) can finish
        # its try/finally handoff check + post-exit finalization instead of being
        # killed mid-cleanup.
        proc = subprocess.Popen(argv)
        try:
            rc = proc.wait()
        except KeyboardInterrupt:
            # Ctrl+C was sent to the entire console process group.
            # The child (pwsh -> copilot, or cmd.exe -> pwsh -> copilot in
            # stdio mode) received it too. Wait for the child to finish its
            # cleanup (handoff check, post-exit finalization) rather than
            # killing it.
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
    """Build set of normalized paths with live sessions (lock files OR mux sessions).

    The mux check is **batched**: a single ``list-sessions`` snapshot
    (:func:`sessions._list_mux_sessions`) is diffed against the records instead
    of one ``has-session`` subprocess *per worktree*. On the picker-populate hot
    path that collapses N mux spawns (each up to a 5 s timeout) into one, which
    is the dominant cost of populating the Active section. Falls back to the
    per-worktree probe only when the batch list is unavailable (old/blocked mux).
    """
    if session_ctx is None:
        session_ctx = sessions.scan_sessions_fast(records)
    active = {
        _normalize_path(p) for p, sids in session_ctx.active_sessions.items() if sids
    }
    # Live multiplexer sessions (independent of lock files), batched.
    mux_sessions = sessions._list_mux_sessions()
    if mux_sessions is not None:
        for rec in records:
            if rec.worktree_path and f"wt-{rec.worktree_id}" in mux_sessions:
                active.add(_normalize_path(rec.worktree_path))
    else:
        # Batch list unavailable (mux missing or blocked): prefer the #4057
        # cached liveness hint on the record (a free read, stamped by the
        # authoritative verify at the action moments) when it is FRESH, and only
        # fall back to the slow per-worktree has-session probe when there is no
        # usable hint. This keeps the populate cheap even when the live batch
        # can't run, without trusting a stale stamp.
        for rec in records:
            if not rec.worktree_path:
                continue
            hint = _fresh_mux_live_hint(rec)
            if hint is True:
                active.add(_normalize_path(rec.worktree_path))
            elif hint is None and sessions.has_mux_session(rec.worktree_id):
                active.add(_normalize_path(rec.worktree_path))
    # #4057/#1416 bare-resume blind spot: a bare-resumed Copilot (cwd=home) is
    # invisible to BOTH the lock scan above (its session isn't registered under
    # the worktree) and the mux batch (it has no mux), so union in the cached
    # ``bound_live`` hint -- reconciled OFF the hot path by
    # ``data_local.reconcile_bound_live`` via the authoritative machine-wide
    # ``reclaim.resolve_bound_copilots`` scan -- when FRESH and True. Strictly
    # ADDITIVE (only ever ADDS a worktree to the active set), so a stale/false
    # bound hint can never hide a live mux/lock the checks above found, and a
    # never-reconciled record (hint None) is a no-op.
    for rec in records:
        if rec.worktree_path and _fresh_bound_live_hint(rec) is True:
            active.add(_normalize_path(rec.worktree_path))
    # #4272 bridge-lock layer: a bridge-owned Copilot writes a provable-liveness
    # ``bridge.lock`` carrying its worktree id, so union in every worktree with a
    # live one -- the cheap, cwd-independent, file-first successor to the
    # off-hot-path bound_live reconciler for the #1416 bare-session case. Additive
    # + best-effort (an empty set on any hiccup).
    try:
        bridge_live = reclaim.live_bridge_worktrees()
    except Exception:
        bridge_live = set()
    if bridge_live:
        for rec in records:
            if rec.worktree_path and rec.worktree_id in bridge_live:
                active.add(_normalize_path(rec.worktree_path))
    return active


# #4057: how long a cached ``mux_live`` stamp is trusted as a populate hint.
_MUX_LIVE_HINT_TTL_SECS = 600


def _fresh_mux_live_hint(rec) -> bool | None:
    """The record's cached mux-liveness, iff still fresh; else None.

    Returns ``True``/``False`` when the record carries a ``mux_live`` stamp whose
    ``mux_live_at`` is within :data:`_MUX_LIVE_HINT_TTL_SECS`, otherwise ``None``
    (absent or stale -- caller should verify live). Never raises.
    """
    live = getattr(rec, "mux_live", None)
    if live is None:
        return None
    stamped = getattr(rec, "mux_live_at", None)
    if not stamped:
        return None
    try:
        dt = datetime.fromisoformat(str(stamped))
    except (ValueError, TypeError):
        return None
    now = datetime.now(dt.tzinfo) if dt.tzinfo is not None else datetime.now()
    if (now - dt).total_seconds() > _MUX_LIVE_HINT_TTL_SECS:
        return None
    return bool(live)


# #4057/#1416: how long a cached ``bound_live`` stamp is trusted as a populate
# hint (the bare-resume Active surfacing). Same budget as the mux hint.
_BOUND_LIVE_HINT_TTL_SECS = 600


def _fresh_bound_live_hint(rec) -> bool | None:
    """The record's cached bound-Copilot liveness, iff still fresh; else None.

    Returns ``True``/``False`` when the record carries a ``bound_live`` stamp
    whose ``bound_live_at`` is within :data:`_BOUND_LIVE_HINT_TTL_SECS`, otherwise
    ``None`` (absent/Unknown or stale). The bound signal is reconciled OFF the hot
    path (``data_local.reconcile_bound_live``); populate only ever *reads* this
    cached hint -- it never scans or writes. Never raises.
    """
    live = getattr(rec, "bound_live", None)
    if live is None:
        return None
    stamped = getattr(rec, "bound_live_at", None)
    if not stamped:
        return None
    try:
        dt = datetime.fromisoformat(str(stamped))
    except (ValueError, TypeError):
        return None
    now = datetime.now(dt.tzinfo) if dt.tzinfo is not None else datetime.now()
    if (now - dt).total_seconds() > _BOUND_LIVE_HINT_TTL_SECS:
        return None
    return bool(live)


def _apply_tracking_override(
    rec: tracking.WorktreeRecord,
    info: git_ops.WorktreeStateInfo,
) -> git_ops.WorktreeStateInfo:
    """Let tracking metadata override ambiguous git-state classification.

    A **finalized** (or complete/completed) worktree is done and prune-able:
    ``finalize`` sets that status only after verifying the work is safely on
    ``origin/<default>``, and any subsequent ``create-pr`` flips the status away
    from ``finalized``. So the tracking status is authoritative over the raw
    git-state -- trust it (#1447). Two cases it corrects:

    - **zero-commit finalize** -- the reflog has no ``commit`` entries and
      ``classify_worktree`` returns UNUSED.
    - **squash-merged finalize** -- the worktree branch still carries its
      pre-squash commits, so raw git reads ``N ahead / M behind`` (WIP) even
      though the work landed as a squash on the default branch. That "ahead" is
      the un-reconciled squash artifact, not real work-in-progress.

    A **GONE** worktree (its directory is missing) is never masked -- a missing
    checkout is real regardless of status.

    An **ACTIVE** worktree (a live mux/lock session owns it right now, per
    ``active_paths``) is never masked either: a finalized worktree the operator
    has re-opened (or a bare/bound Copilot still holding its lock) is genuinely
    live, and hiding it behind COMPLETED strands the row with no lifecycle verb
    (the bb68/ca29 status-tracking bug -- a muxed/lock-held session rendered
    FINAL). Liveness wins over the durable finalize status.
    """
    if rec.status in ("finalized", "complete", "completed"):
        if info.state not in (
            git_ops.WorktreeState.GONE,
            git_ops.WorktreeState.ACTIVE,
        ):
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
    return {
        rec.worktree_id: _classify_one_record(
            rec, repo=repo, active_paths=active_paths, session_ctx=session_ctx)
        for rec in records
    }


def _classify_one_record(
    rec: tracking.WorktreeRecord,
    *,
    repo,
    active_paths,
    session_ctx: sessions.SessionContext | None = None,
) -> git_ops.WorktreeStateInfo:
    """Classify one worktree's git state -- the per-record body of
    :func:`_classify_records`, factored out so the streaming list
    (:func:`_cmd_list_stream`) can emit classification progressively, one
    worktree at a time, instead of computing the whole batch before any row is
    sent. ``repo`` / ``active_paths`` are hoisted out of the per-record loop by
    the caller (they are the same for every record)."""
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
    return info


def _make_pr_lookup(config):
    """Build a ``lookup(repo, number) -> PullResult|None`` over the configured
    provider, for prune PR-state reconciliation. Returns None-yielding on any
    error so reconciliation is best-effort (keeps the local state)."""
    from . import providers

    prcfg = config.default_repo.pr
    api_base = getattr(prcfg, "api_base", "") or ""

    def lookup(repo, number):
        try:
            token = providers.account_token_for_slug(repo, prcfg)
        except Exception:
            token = None
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
    bare_orphan_wts: set[str] | None = None,
    bridge_live_wts: set[str] | None = None,
) -> dict:
    """Serialize a WorktreeRecord to a JSON-friendly dict.

    If ``state_info`` is provided, includes git-derived classification
    (state, ahead, behind, dirty) alongside the tracking status.

    If ``mux_info`` is provided, includes multiplexer session status
    (existence and attached client count).

    If ``session_ctx`` is provided, includes session-derived metrics
    (turn_count, session_count, latest_summary).

    If ``bare_orphan_wts`` is provided (the set of worktree ids that host a
    **bare**/un-muxed bound Copilot, from :func:`reclaim.bare_orphan_worktree_ids`),
    a matching record is flagged ``session_bare_orphan`` so the picker can mark
    the row -- a Copilot invisible to the mux fleet view (#93).
    """
    d: dict = {
        "id": rec.worktree_id,
        "branch": rec.branch,
        "path": rec.worktree_path,
        "repo": rec.repo,
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
    # #2668: expose the two orthogonal marks (resolved, so consumers read a
    # concrete value without re-deriving from kind) + whether the everyday
    # Picker/cockpit hides this worktree.
    d["interface"] = rec.resolved_interface
    d["origin"] = rec.resolved_origin
    d["picker_hidden"] = rec.is_picker_hidden
    # worktree-status-core: the agent-asserted disposition overlay so the Picker
    # can render a follow-up glyph + summary and feed the prune verdict. Absent
    # summary/status_note_at stay off the dict to keep it lean for un-annotated
    # worktrees; follow_up is always present (a plain bool the picker reads).
    d["follow_up"] = rec.follow_up
    if rec.summary:
        d["summary"] = rec.summary
    if rec.status_note_at:
        d["status_note_at"] = rec.status_note_at
    # #2178: expose the bridge caller-worktree pointer so the Picker can offer
    # "Jump to caller" from a bridge worktree.
    if rec.caller_worktree:
        d["caller_worktree"] = rec.caller_worktree
    # resource-claims: expose the backward owner link + forward outbound claim
    # list so the ledger view (and consumers like `run`) can read a worktree's
    # full claim set. Emitted only when present, keeping the envelope lean.
    if rec.owner_ref:
        d["owner_ref"] = rec.owner_ref
    # citadel paired -harness/-knowledge lifecycle (#957): expose the pair
    # linkage so the Picker can annotate/aggregate the two rows as a pair.
    # Emitted only when paired, keeping the envelope lean for the common case.
    if rec.pair_id:
        d["pair_id"] = rec.pair_id
    if rec.pair_role:
        d["pair_role"] = rec.pair_role
    if rec.pair_kind:
        d["pair_kind"] = rec.pair_kind
    if rec.resources:
        d["resources"] = [
            {
                "kind": c.kind,
                "ref": c.ref,
                "created_at": c.created_at,
                "state": c.state,
                **({"note": c.note} if c.note else {}),
            }
            for c in rec.resources
        ]
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
            rec, state_info, turn_count=_turns,
            claimant_alive=_local_claimant_alive,
            paired_sibling_final=prune.default_paired_sibling_final).bucket
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
        # two-step-restore: the session id(s) currently held by a live
        # ``inuse.<pid>.lock`` (a bound Copilot process -- mux OR bare), and the
        # most-recent session id for this worktree. The Picker shows the id (so
        # the operator can ``/resume`` it manually) and gates the Reclaim action
        # on a live lock. Both stay off the dict when absent to keep it lean.
        _live_ids = session_ctx.active_sessions.get(norm) or []
        if _live_ids:
            d["live_session_ids"] = list(_live_ids)
            d["session_lock_live"] = True
        # Stale-lock residue: ``inuse.<pid>.lock`` file(s) whose pid is no longer
        # a live Copilot (crashed/killed without cleanup). Surfaced so the Picker
        # can offer Reclaim (file-only cleanup) for a worktree with no mux and no
        # live lock -- residue that must be cleared "to the point where the pid
        # lock file is removed". Emitted only when present to keep the dict lean.
        _stale_pids = session_ctx.stale_locks.get(norm) or []
        if _stale_pids:
            d["session_lock_stale"] = True
            d["stale_lock_pids"] = list(_stale_pids)
        # GH #198: read the resume-target id from the single scan pass
        # (scan_sessions_fast folds it into SessionContext.last_session_id)
        # instead of a per-worktree full re-scan of session-state. The old
        # find_latest_session_id_fast() fell back to an O(all-sessions)
        # yaml.safe_load walk *per worktree*, so with N worktrees it was
        # O(worktrees x sessions) and could pin a core on a large tree.
        _last_sid = session_ctx.last_session_id.get(norm)
        if _last_sid:
            d["last_session_id"] = _last_sid
        # worktree-status-core: the live activity pulse (derived from the
        # agent's assistant.intent stream by the live-pulse extension). Emitted
        # only when present so un-annotated worktrees stay lean; the picker
        # ages it out (dim -> expired) and NEVER reads it as the durable
        # follow_up disposition.
        _intent = session_ctx.live_intent.get(norm)
        if _intent:
            d["live_intent"] = _intent
            _iat = session_ctx.live_intent_at.get(norm)
            if _iat:
                d["live_intent_at"] = _iat
            if session_ctx.live_intent_idle.get(norm):
                d["live_intent_idle"] = True
        # copilot-extensions#228: the graded REST state (busy/idle/
        # awaiting-operator). The crisp value comes from the live-pulse
        # extension's sidecar; when that's absent the backbone fills a coarse
        # busy/idle from a bounded events.jsonl tail -- so live_rest may be
        # present with NO extension loaded (only awaiting-operator is
        # extension-only). Enrichment only; emitted when present.
        _rest = session_ctx.live_rest.get(norm)
        if _rest:
            d["live_rest"] = _rest
            _rat = session_ctx.live_rest_at.get(norm)
            if _rat:
                d["live_rest_at"] = _rat
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
    # #93: mark a worktree hosting a bare (un-muxed) bound Copilot so the picker
    # can annotate its row with an orphan marker (a Copilot the mux fleet view
    # cannot see). Set only when true, to keep the dict lean.
    if bare_orphan_wts and rec.worktree_id in bare_orphan_wts:
        d["session_bare_orphan"] = True
    # #4057/#1416: the cached bound-Copilot liveness hint (reconciled OFF the hot
    # path). Surfaced so the picker's fast (classification-absent) pass can mark a
    # bare-resumed session ACTIVE from cache -- matching what ``_build_active_paths``
    # feeds the git-classify pass. A bare Copilot has no mux, so this is DISTINCT
    # from ``mux_session``; emitted only when fresh+True to keep the dict lean.
    if _fresh_bound_live_hint(rec) is True:
        d["session_bound_live"] = True
    # #4272 bridge-lock: a live bridge-owned Copilot for this worktree (read
    # file-first from its bridge.lock). Surfaced so the fast (classification-
    # absent) pass marks a bare/bridge session ACTIVE from the cheap file read,
    # matching what _build_active_paths feeds the git-classify pass. Set only
    # when true, to keep the dict lean.
    if bridge_live_wts and rec.worktree_id in bridge_live_wts:
        d["session_bridge_live"] = True
    return d


def _carve_paired_knowledge(
    config: cfg.Config,
    *,
    harness_id: str,
    timestamp: str,
    suffix: str,
    plat: str,
    plat_short: str,
) -> dict | None:
    """Carve/stamp the citadel knowledge-repo PAIR for a stateless harness (#957).

    When the launch repo is a **stateless harness bound to a knowledge repo**
    (``resolve_state_root`` reports ``requires_external`` + ``bound``), the
    knowledge repo's state is carved and tracked **together** with the harness
    worktree so the agent never has to remember to carve it separately:

    * **Worktree-class knowledge repo** -> carve its own worktree (shared
      ``<ts>-<suffix>`` pair stub, parallel ``worktree/<id>`` branch), write its
      tracking record cross-stamped back to the harness, and return the
      ``pair_*`` stamp (``pair_kind="worktree"``) for the HARNESS record.
    * **Non-worktree-class knowledge repo** (singleton / reference) -> no second
      worktree; return an ``anchor`` pairing stamp so the pair is recorded and
      ``state-root --pair`` resolves to the knowledge anchor.

    Returns the ``pair_*`` dict to stamp on the harness record, or ``None`` when
    no pairing applies (a normal self-hosted repo, or an unbound harness).
    **Fail-safe:** any error degrades to ``None`` (the harness carve is never
    affected) with a one-line stderr note. Set ``AGENT_WORKTREES_NO_PAIR`` to
    disable the carve-both behavior entirely.
    """
    if os.environ.get("AGENT_WORKTREES_NO_PAIR"):
        return None
    try:
        res = state_root_mod.resolve_state_root(config)
    except Exception:
        return None
    if not (res.requires_external and res.bound and res.path):
        return None

    from . import repos as repos_mod

    knowledge_name = res.repo
    knowledge_anchor = res.path
    pair_id = f"{timestamp}-{suffix}"
    harness_ref = tracking.format_claim_ref(
        config.machine, config.repo_name or "?", harness_id
    )
    entry = repos_mod.find_repo(knowledge_name)
    is_worktree_class = bool(entry) and repos_mod.normalize_class(
        entry.repo_class
    ) == "worktree"

    if not is_worktree_class:
        # Non-worktree-class knowledge -> operate on the anchor (no 2nd carve).
        anchor_ref = tracking.format_claim_ref(
            config.machine, knowledge_name, os.path.basename(
                knowledge_anchor.rstrip("/\\")
            ) or "anchor",
        )
        print(
            f"Paired knowledge repo '{knowledge_name}' is not worktree-class; "
            f"pairing at its anchor (no separate worktree).",
            file=sys.stderr,
        )
        return {
            "pair_id": pair_id,
            "pair_role": "harness",
            "pair_ref": anchor_ref,
            "pair_kind": "anchor",
        }

    # Worktree-class knowledge -> carve its paired worktree.
    knowledge_id = f"{config.machine}-{plat_short}-{timestamp}-{suffix}-k"
    knowledge_branch = f"worktree/{knowledge_id}"
    knowledge_wt_root = cfg.derive_worktree_root(knowledge_anchor)
    knowledge_wt_path = str(Path(knowledge_wt_root) / knowledge_id)
    remote = (entry.remote or "origin") if entry else "origin"
    default_branch = (entry.default_branch or "main") if entry else "main"

    Path(knowledge_wt_root).mkdir(parents=True, exist_ok=True)
    print(
        f"Fetching latest from {remote} for paired knowledge repo "
        f"'{knowledge_name}'...",
        file=sys.stderr,
    )
    git_ops.git("fetch", remote, "--quiet", cwd=knowledge_anchor, check=False)
    start_point = git_ops.resolve_start_point(
        remote, default_branch, cwd=knowledge_anchor
    )
    print(
        f"Creating paired knowledge worktree on branch {knowledge_branch}...",
        file=sys.stderr,
    )
    git_ops.create_worktree(
        knowledge_anchor, knowledge_wt_path, knowledge_branch, start_point
    )

    knowledge_ref = tracking.format_claim_ref(
        config.machine, knowledge_name, knowledge_id
    )
    tracking.create_new_record(
        worktree_id=knowledge_id,
        branch=knowledge_branch,
        worktree_path=knowledge_wt_path,
        repo=knowledge_name,
        machine=config.machine,
        platform_name=plat,
        tracking_path=cfg.tracking_dir(),
        pair_id=pair_id,
        pair_role="knowledge",
        pair_ref=harness_ref,
        pair_kind="worktree",
    )
    # Best-effort permissions + trust for the knowledge worktree.
    try:
        permissions.clone_permissions(knowledge_anchor, knowledge_wt_path)
        permissions.add_trusted_folder(knowledge_wt_path)
    except Exception:
        pass
    activity.log_event(
        "paired_knowledge_worktree_created",
        worktree_id=knowledge_id,
        branch=knowledge_branch,
    )
    return {
        "pair_id": pair_id,
        "pair_role": "harness",
        "pair_ref": knowledge_ref,
        "pair_kind": "worktree",
    }


def _journal_owner_reciprocal_claim(
    config: cfg.Config, worktree_id: str, owner_ref: str | None,
) -> bool:
    """Journal the reciprocal ``worktree`` claim onto an owner's ledger (Ph3c).

    A worktree created with an ``owner_ref`` is another worktree's outbound
    resource. This writes the **forward** half of that bidirectional link -- a
    ``worktree``-kind :class:`ResourceClaim` on the *owner*'s record whose ``ref``
    is this worktree's qualified ClaimRef -- so the owner's finalize gate sees
    the obligation and the child's finalize ``_settle_parent_obligation`` has a
    matching claim to settle. Without it the link is half-formed (backward
    ``owner_ref`` set, but the owner holds no claim), so settlement is a silent
    no-op.

    Same-machine owner -> written here; a **cross-machine** owner defers to the
    lease mirror (``_resolve_owner_ref_record_path`` returns no local path).
    Fully best-effort: any failure returns ``False`` and never raises (so it
    can never break a worktree carve). Idempotent -- ``add_resource_claim``
    dedups by ref, so a ``run``-driven create that also journals is harmless.
    Returns ``True`` only when a claim was written.
    """
    if not owner_ref:
        return False
    try:
        owner_path, _owner_wt, _err = _resolve_owner_ref_record_path(
            owner_ref, config,
        )
        if owner_path is None or not owner_path.exists():
            return False
        child_ref = tracking.format_claim_ref(
            config.machine, config.repo_name, worktree_id,
        )
        owner_rec = tracking.load_record(owner_path)
        tracking.add_resource_claim(
            owner_rec,
            tracking.ResourceClaim(
                kind="worktree", ref=child_ref,
                created_at=tracking._now_iso(), state=obligations.ACTIVE,
            ),
            save=False,
        )
        tracking.save_record(owner_rec, owner_path)
        print(
            f"Journaled this worktree as an obligation on {owner_ref}.",
            file=sys.stderr,
        )
        return True
    except Exception as exc:  # never let journaling break the carve
        print(f"owner-claim journaling failed (non-fatal): {exc}",
              file=sys.stderr)
        return False


def _create_worktree_core(
    config: cfg.Config,
    *,
    profile: cfg.CopilotProfile | None = None,
    no_mux: bool = False,
    kind: tracking.WorktreeKind = "session",
    owner: str | None = None,
    interface: tracking.WorktreeInterface | None = None,
    origin: tracking.WorktreeOrigin | None = None,
    name: str | None = None,
    parent_session: str | None = None,
    caller_worktree: str | None = None,
    owner_ref: str | None = None,
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
        interface=interface,
        origin=origin,
        # #1029: link the new worktree back to the session that spawned it, so a
        # later resume (esp. a PR/feedback worktree with no sessions of its own)
        # restores context instead of cold-starting.
        parent_session=(parent_session
                        or os.environ.get("COPILOT_AGENT_SESSION_ID") or None),
        # #2178: for a bridge spawn, record the caller worktree so the Picker can
        # jump back to it.
        caller_worktree=caller_worktree or None,
        # resource-claims: the qualified backward owner link, for a worktree
        # spun up as another worktree's outbound resource (stamped by `run` /
        # an explicit --owner-ref). Absent = unclaimed.
        owner_ref=owner_ref or None,
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
        print("Added worktree path to trustedFolders.", file=sys.stderr)

    # resource-obligation-settlement (Phase 3c): a worktree created WITH an
    # ``owner_ref`` is another worktree's outbound resource, so journal the
    # reciprocal ``worktree`` claim onto that owner's ledger (see
    # ``_journal_owner_reciprocal_claim``).
    _journal_owner_reciprocal_claim(config, worktree_id, owner_ref)

    # citadel paired -harness/-knowledge worktree lifecycle (#957): when this is
    # a stateless harness bound to a knowledge repo, carve/stamp the knowledge
    # pair together with this worktree and cross-stamp the linkage. Only for
    # plain session worktrees (never system/bridge), and fully fail-safe -- a
    # pairing failure never breaks the harness carve.
    if kind == "session":
        try:
            pair_stamp = _carve_paired_knowledge(
                config,
                harness_id=worktree_id,
                timestamp=timestamp,
                suffix=suffix,
                plat=plat,
                plat_short=plat_short,
            )
        except Exception as exc:  # pragma: no cover - defensive
            print(f"paired-knowledge carve failed (non-fatal): {exc}",
                  file=sys.stderr)
            pair_stamp = None
        if pair_stamp:
            record.pair_id = pair_stamp.get("pair_id")
            record.pair_role = pair_stamp.get("pair_role")
            record.pair_ref = pair_stamp.get("pair_ref")
            record.pair_kind = pair_stamp.get("pair_kind")
            tracking.save_record(record)

    # Build launch command (for caller to use)
    fake_args = argparse.Namespace(
        copilot_args=[], recovery=False, no_mux=no_mux,
        no_resume=False, profile=None,
    )
    launch_cmd = _build_launch_cmd(config, fake_args, worktree_path, profile=profile)
    env = _build_env(profile, _repo_session_env(config, worktree_path), work_dir=worktree_path)

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


def _self_owner_ref(work_dir: str | None) -> str | None:
    """Qualified ClaimRef of the worktree rooted at ``work_dir`` (None if not one).

    The identity a launched session advertises via ``AGENT_WORKTREES_OWNER_REF``
    so any resource it creates (a nested ``create``, a borrowed CodeSpace)
    inherits *this* worktree as owner and the finalize gate can hold it
    accountable (resource-obligation-settlement Ph6). Returns None for a
    non-worktree path (e.g. the repo anchor) -- resources created there are
    top-level, not owned -- and is fully best-effort (never raises).
    """
    if not work_dir:
        return None
    try:
        # Resolve the worktree id the authoritative way -- git-identity first
        # (root-independent, matches `get owner-ref`), then a tracked-path match.
        wd = Path(work_dir)
        wid = _worktree_id_from_git(wd) or tracking.find_worktree_id_by_cwd(str(wd))
        if not wid:
            return None
        config = cfg.load_config()
        try:
            project = cfg.project_name()
        except Exception:
            project = config.repo_name
        session = os.environ.get("COPILOT_AGENT_SESSION_ID") or None
        return tracking.format_claim_ref(config.machine, project, wid, session)
    except Exception:
        return None


def _build_env(
    profile: cfg.CopilotProfile | None,
    session_env: dict[str, str] | None = None,
    work_dir: str | None = None,
) -> dict[str, str]:
    """Build env dict with auto-injected vars, repo session_env, then profile.

    Convention-based vars (like COPILOT_CUSTOM_INSTRUCTIONS_DIRS) are set
    first, then the repo's ``session_env`` (e.g. COPILOT_FEATURE_FLAGS), then
    profile env merges on top.  For path-list vars like
    COPILOT_CUSTOM_INSTRUCTIONS_DIRS, profile values are appended rather
    than replacing the auto-injected value.

    When ``work_dir`` names a managed worktree, its qualified ClaimRef is
    exported as ``AGENT_WORKTREES_OWNER_REF`` so any resource an agent creates
    inside the launched session inherits this worktree as owner (Ph6 ambient
    owner identity). A non-worktree ``work_dir`` (the anchor) or a failure to
    resolve it simply omits the var.
    """
    env: dict[str, str] = {}

    # Auto-inject: dynamic instructions live in ~/.{project}
    project_dir = str(cfg.project_dir())
    env["COPILOT_CUSTOM_INSTRUCTIONS_DIRS"] = project_dir

    # Arm the PR-workflow git-hook shims for this launch when the repo uses PR
    # mode. The shims are inert unless AGENT_WORKTREES_HOOKS=1 is present; scope
    # it to the launched session env (not a global/user var) so external and
    # recovery git operations stay unguarded by default (#234 defect 1).
    try:
        if cfg.load_config().default_repo.pr.enabled:
            env["AGENT_WORKTREES_HOOKS"] = "1"
    except Exception:
        pass

    # Ambient owner identity (Ph6): advertise the launched worktree's own
    # ClaimRef so a resource it creates inherits it as owner. Each launch stamps
    # SELF (overwriting any inherited value), so a chain A->B->C inherits one hop
    # at each level. Best-effort; omitted for a non-worktree path (anchor).
    owner_self = _self_owner_ref(work_dir)
    if owner_self:
        env["AGENT_WORKTREES_OWNER_REF"] = owner_self

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
        # env_script: resolve like setup_hook, but its captured environment is
        # applied to the Copilot exec (see RepoConfig.env_script). Present ->
        # force the normalized default-setup launcher (never legacy) so
        # -EnvScript / --env-script is honored.
        env_script_path = repo.env_script.get(plat_key)
        resolved_env_script = ""
        if env_script_path:
            resolved_env_script = env_script_path.format(**variables)
            if not os.path.isabs(resolved_env_script):
                resolved_env_script = str(Path(anchor) / resolved_env_script)
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
                if resolved_env_script:
                    cmd += ["-EnvScript", resolved_env_script]
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
                if resolved_env_script:
                    cmd += ["--env-script", resolved_env_script]
                if recovery:
                    cmd.append("--recovery")
        elif is_windows:
            setup_path = str(Path(anchor) / "tools" / "setup" / "setup.ps1")
            legacy = Path(setup_path).is_file() and not resolved_env_script
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
            if resolved_env_script:
                cmd += ["-EnvScript", resolved_env_script]
            if recovery:
                cmd.append("-Recovery")
        else:
            setup_path = str(Path(anchor) / "tools" / "setup" / "setup.sh")
            legacy = Path(setup_path).is_file() and not resolved_env_script
            if not legacy:
                setup_path = str(inst.install_dir() / "scripts" / "default-setup.sh")
            cmd = ["bash", setup_path, "--machine", config.machine]
            if session_path_arg and not legacy:
                cmd += ["--session-path", session_path_arg]
            if resolved_env_script:
                cmd += ["--env-script", resolved_env_script]
            if recovery:
                cmd.append("--recovery")

    extra = getattr(args, "copilot_args", []) or []
    cmd.extend(extra)

    # Append profile-specific Copilot args
    profile_args = profile.copilot_args if profile and profile.copilot_args else []
    cmd.extend(profile_args)

    # Auto-approve everything so worktree sessions run without any
    # confirmation prompts.  --allow-all is equivalent to
    # --allow-all-tools --allow-all-paths --allow-all-urls, so a worktree
    # session never stalls on a tool, path, or URL prompt.  Skip ACP
    # sessions (agent-bridge manages permissions over the protocol) and
    # never duplicate an all-permissions flag the caller already supplied.
    passthrough = list(extra) + list(profile_args)
    if "--acp" not in passthrough and not any(
        a == flag
        for a in passthrough
        for flag in ("--allow-all-tools", "--allow-all", "--yolo")
    ):
        cmd.append("--allow-all")

    return cmd


def _emit_parent_context_hint(record, *, to_stderr: bool = False) -> None:
    """Surface a worktree's originating ``parent_session`` as a *hint* only.

    A worktree with no session of its own used to auto-resume its
    ``parent_session`` (#1029). But that session belongs to a *different*
    worktree, and Copilot's resume-auto-cd adopts the resumed session's
    persisted cwd -- so the tab (named after THIS worktree's id) would open in
    the parent's directory, mismatching the mux worktree id and the loaded
    path. Fix B keeps the pointer as context only: the operator can ``/resume``
    it explicitly once inside, preserving path/mux alignment.

    ``to_stderr`` routes the hint to stderr for the JSON-emitting launch path
    (a stdout write there would corrupt the JSON contract).
    """
    parent = sessions.validate_session_id(record.parent_session)
    if not parent:
        return
    msg = (
        f"   No session of its own -- originating context is {parent[:12]} "
        f"(run /resume {parent[:8]} inside to load it here)."
    )
    if to_stderr:
        sys.stderr.write(msg + "\n")
    else:
        print(msg)


def cmd_handoff_cutover(args: argparse.Namespace) -> int:
    """Live-cutover handoff: spawn a seeded successor Copilot or retire a pane.

    Two modes (JSON out on stdout either way):

    * **spawn** (default; needs ``--seed``): reconstruct this worktree's launch
      command (the same ``_build_launch_cmd`` the picker uses) for a **plain
      interactive** Copilot, open + select a NEW window in the worktree's
      ``wt-<id>`` mux session (cutting the operator over), then inject ``--seed``
      as the successor's first interactive turn via ``send-keys`` once Copilot is
      ready. The seed is typed, not passed as a launch arg -- psmux (Windows)
      cannot carry a spaces-containing pane arg. Deliberately omits ``--resume``:
      a handoff wants a FRESH context window seeded by the prompt, not the old
      transcript replayed. Returns the OLD (pre-cutover) pane id so the caller can
      retire it once the old session
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
    session_id = getattr(args, "session_id", None)
    if raw_id:
        wt_id = _resolve_worktree_id(raw_id)
    else:
        wt_id = _infer_worktree_id_from_cwd()
        # Bare-resume authoritative fallback (#4098): under a two-step "Bare
        # resume" the pane's cwd is HOME (to dodge the worktree-cwd start bug),
        # so cwd inference finds no worktree even though the session IS inside
        # its wt-<id> mux. Resolve the worktree from the session id instead --
        # the registry maps the resumed session to its worktree (the same
        # binding the sessionStart hook uses), so this is authoritative, not a
        # brittle guess. Activate the scoped bare-resume binding first (reads the
        # AGENT_WORKTREES_BIND_* env the launcher set), then match by session id.
        if not wt_id and session_id:
            wt_id = _activate_session_binding(session_id)
            if not wt_id:
                try:
                    wt_id = tracking.find_worktree_id_by_session(session_id)
                except Exception:
                    wt_id = None
        if not wt_id:
            return _json_error(
                "could not resolve a worktree id from cwd; pass --worktree-id "
                "(or --session-id for a bare-resumed session)",
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
    env = _build_env(
        None, _repo_session_env(config, record.worktree_path),
        work_dir=record.worktree_path,
    )
    # The seed is NOT passed as a launch arg. The launch wraps Copilot in
    # ``pwsh -File default-setup.ps1 ... <copilot args>`` and psmux (Windows)
    # cannot carry a spaces-containing pane arg (it word-splits, and a bare
    # ``-i`` also collides with PowerShell's ``-Information*`` params). So we
    # spawn a PLAIN interactive Copilot (no ``--resume`` either: a handoff wants
    # a FRESH context) and inject the seed as literal keystrokes once it is
    # ready (:func:`sessions.mux_seed_pane`) -- the same send-keys mechanism the
    # retire path uses, immune to every command-line quoting hazard.

    # Capture the pane to retire (the operator's current Copilot) BEFORE opening
    # the new window, which becomes the active pane. ``--old-pane`` lets the
    # extension pin its own $TMUX_PANE explicitly.
    old_pane = getattr(args, "old_pane", None) or sessions.mux_active_pane(wt_id)

    if getattr(args, "dry_run", False):
        _json_output({
            "ok": True, "dry_run": True, "session": f"wt-{wt_id}",
            "old_pane": old_pane, "work_dir": record.worktree_path,
            "cmd": list(launch_cmd), "seed_len": len(seed),
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

    new_pane = result.get("new_pane")
    # Inject the seed as the successor's first interactive turn.
    seed_result = sessions.mux_seed_pane(new_pane, seed) if new_pane else {}

    _json_output({
        "ok": True,
        "session": f"wt-{wt_id}",
        "old_pane": old_pane,
        "new_pane": new_pane,
        "seed_len": len(seed),
        "seeded": bool(seed_result.get("sent")),
        "seed_ready": bool(seed_result.get("ready")),
    })
    return 0


def cmd_embody(args: argparse.Namespace) -> int:
    """Create or resume a **detached** mux+Copilot CLI session in a worktree (D5).

    The agent-facing "embodiment" verb: spawn a durable, mux-wrapped interactive
    Copilot in a target worktree so it registers with the local bridge (Phase 1)
    and becomes viewable/messageable -- the CLI-first embodiment of
    ``visions/agent-fabric`` §lifetime-decides-embodiment, driven by an agent
    rather than a human at the picker. JSON out on stdout.

    Target selection:
      * ``--worktree-id <id>`` embodies in an existing worktree;
      * ``--new`` creates a fresh worktree first (the outlives-its-caller case).

    Resume semantics: **one live session per worktree**. If a ``wt-<id>`` mux
    session already exists, this does NOT spawn a duplicate -- it reports the
    existing session (``created=false``), honoring "to act in an occupied space,
    interrogate the occupant or embody a fresh space." Otherwise it creates the
    session detached (never attaching -- the operator or Neuron Forge attaches
    later). An optional ``--seed`` is injected as the first interactive turn via
    ``send-keys`` once Copilot is ready (the same mechanism the handoff uses).

    Verification: the caller confirms the embodiment by polling
    ``agent-bridge live-sessions?worktree_id=<id>`` -- the bundled extension
    auto-registers the new session. ``--verify-timeout`` optionally waits here
    for the mux session to come up before returning.
    """
    make_new = getattr(args, "new", False)
    raw_id = getattr(args, "worktree_id", None)
    if make_new and raw_id:
        return _json_error("--new and --worktree-id are mutually exclusive", exit_code=2)
    if not make_new and not raw_id:
        return _json_error("embody requires --worktree-id <id> or --new", exit_code=2)

    try:
        config = cfg.load_config()
    except Exception as e:
        return _json_error(str(e))

    # Resolve target worktree id + path (creating a fresh worktree for --new).
    if make_new:
        try:
            with output.stdout_to_stderr():
                created = _create_worktree_core(config, no_mux=True, kind="session")
        except Exception as e:
            return _json_error(f"failed to create worktree: {e}")
        wt_id = created["worktree"]["id"]
        work_dir = created["worktree"]["path"]
    else:
        wt_id = _resolve_worktree_id(raw_id)
        yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
        if not yaml_path.exists():
            return _json_error(f"Worktree not found: {wt_id}")
        work_dir = tracking.load_record(yaml_path).worktree_path

    # Resume: a live mux session already embodies this worktree -- don't
    # duplicate (one live session per cwd). Report it and stop.
    already = sessions.has_mux_session(wt_id)
    seed = getattr(args, "seed", None)

    if getattr(args, "dry_run", False):
        launch_cmd = _build_launch_cmd(config, args, work_dir)
        _json_output({
            "ok": True, "dry_run": True, "worktree_id": wt_id,
            "session": f"wt-{wt_id}", "work_dir": work_dir,
            "would": "resume" if already else "create",
            "cmd": list(launch_cmd), "seed_len": len(seed) if seed else 0,
        })
        return 0

    if already:
        _json_output({
            "ok": True, "worktree_id": wt_id, "session": f"wt-{wt_id}",
            "work_dir": work_dir, "created": False, "resumed": True,
            "new_pane": sessions.mux_active_pane(wt_id),
            "note": "a live mux session already embodies this worktree",
        })
        return 0

    launch_cmd = _build_launch_cmd(config, args, work_dir)
    env = _build_env(None, _repo_session_env(config, work_dir), work_dir=work_dir)
    # D4: stamp the driver so the embodied session registers a "driven by
    # <agent>" banner (legible when a human takes it over in Neuron Forge).
    driver = getattr(args, "driver", None)
    if driver:
        env["AGENT_BRIDGE_DRIVEN_BY"] = driver
    result = sessions.mux_new_session(wt_id, work_dir, launch_cmd, env)
    if not result.get("ok"):
        return _json_error(
            f"failed to create session wt-{wt_id}: {result.get('error')}",
            exit_code=4,
        )

    new_pane = result.get("new_pane")
    # A freshly-embodied session can be MCP/skill-heavy and take well over the
    # 20s handoff default to reach Copilot's input caret; seeding races that
    # load, and if it loses, the seed is never typed and the session sits idle
    # at an empty prompt. Give embody a generous, tunable seed-ready timeout so
    # a slow-loading autopilot is still driven autonomously.
    seed_ready_timeout = getattr(args, "seed_ready_timeout", None) or 180.0
    seed_result = (
        sessions.mux_seed_pane(new_pane, seed, ready_timeout=seed_ready_timeout)
        if (new_pane and seed) else {}
    )

    verified = None
    verify_timeout = getattr(args, "verify_timeout", 0.0) or 0.0
    if verify_timeout > 0:
        deadline = time.monotonic() + verify_timeout
        verified = False
        while time.monotonic() < deadline:
            if sessions.has_mux_session(wt_id):
                verified = True
                break
            time.sleep(0.3)

    _json_output({
        "ok": True,
        "worktree_id": wt_id,
        "session": f"wt-{wt_id}",
        "work_dir": work_dir,
        "created": True,
        "resumed": False,
        "new_pane": new_pane,
        "driven_by": driver,
        "seeded": bool(seed_result.get("sent")) if seed else False,
        "seed_ready": bool(seed_result.get("ready")) if seed else False,
        "seed_submitted": bool(seed_result.get("submitted")) if seed else False,
        "seed_reason": seed_result.get("reason") if seed else None,
        "mux_verified": verified,
        "verify_hint": (
            f"agent-bridge live-sessions | grep {wt_id}  "
            "(the embodied Copilot auto-registers with the local bridge)"
        ),
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
            env = _build_env(None, _repo_session_env(config, work_dir), work_dir=work_dir)

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
                        owner_ref=getattr(args, "owner_ref", None),
                    )
                except RuntimeError as e:
                    return _json_error(str(e))
                _json_output(result)
                return 0

            wt_id = _resolve_worktree_id(wt_id)  # type: ignore[possibly-undefined]
            yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
            if not yaml_path.exists():
                return _json_error(f"Worktree not found: {wt_id}")
            # Foreground RMW (#4547): reload + bump the resume stamp under the
            # blocking record lock so a concurrent Picker liveness sweep can't
            # clobber the increment (and vice versa). No I/O in the window.
            with tracking._RecordLock(yaml_path):
                record = tracking.load_record(yaml_path)
                tracking.mark_resumed(record, save=False)
                tracking.save_record(record)

            activity.log_event(
                "worktree_resumed",
                worktree_id=record.worktree_id,
                branch=record.branch,
                resume_count=record.resume_count,
            )

            launch_cmd = _build_launch_cmd(config, args, record.worktree_path)
            env = _build_env(
                None, _repo_session_env(config, record.worktree_path),
                work_dir=record.worktree_path,
            )

            # Auto-resume session
            no_resume = getattr(args, "no_resume", False)
            if not no_resume:
                last_session = sessions.find_latest_session_id_fast(
                    record.worktree_path, record.sessions,
                )
                if last_session:
                    # copilot's --resume[=value] is an optional-value option;
                    # the id MUST be attached with '=' or it is treated as a
                    # stray operand ("unknown command").
                    launch_cmd.append(f"--resume={last_session}")
                else:
                    # Fix B: never auto-resume a foreign ``parent_session`` --
                    # it belongs to a different worktree, so Copilot's
                    # resume-auto-cd would adopt its persisted cwd and launch
                    # this tab in the parent's directory (worktree id/path
                    # mismatch). Surface it as a hint only; keep this worktree's
                    # own path.
                    _emit_parent_context_hint(record, to_stderr=True)

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

        # Fail-safe (interactive picker only): if this machine has no self-entry
        # in the anchor's (possibly stale) machines.yaml, fast-forward the
        # anchor *before* the picker parses it -- otherwise the picker can't
        # identify this machine and crashes. The pull-forward that adds the
        # entry otherwise runs only *after* a successful resolve (the ``update``
        # path), so a stale anchor could never self-heal. See
        # ``_heal_stale_anchor_if_self_missing``.
        config = _heal_stale_anchor_if_self_missing(config)
        repo = config.default_repo
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
                and not r.is_picker_hidden  # tucked-away automation (origin-based)
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
               if r.is_picker_hidden]
    records = [r for r in records if r.repo == config.repo_name]

    if not records:
        _system_pause("No system worktrees.")
        return None

    active_paths = _build_active_paths(records)

    while True:
        records = [
            r for r in tracking.list_records(tracking_path)
            if r.is_picker_hidden and r.repo == config.repo_name
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
    merged_env = _build_env(profile, _repo_session_env(config, repo.anchor), work_dir=repo.anchor)
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
        # Same no-daemon cadence for the managed (system/bridge) leak GC (#1069).
        _sweep_managed_on_exit()
        # ...and for orphaned launcher shells (copilot-extensions #102).
        _sweep_launcher_shells_on_exit()
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
                if opts.get("bare_resume"):
                    remote_args.append("--bare-resume")
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
        opts = decision.get("options") or {}
        if opts.get("no_mux"):
            args.no_mux = True
        # two-step-restore "Bare resume": create the worktree's mux, but launch
        # Copilot in the HOME dir with no --resume (dodges a CLI bug that fails
        # to start Copilot inside a repo/worktree cwd). The operator finishes
        # with a manual ``/resume <id>`` (the sub-menu shows the id).
        if opts.get("bare_resume"):
            args.bare_resume = True
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

    # Foreground RMW (#4547): bump the resume stamp under the blocking record
    # lock. `record` here is the picker's (possibly cached) snapshot, so reload
    # fresh inside the lock, increment, save, then reflect the two stamped
    # fields back onto the in-memory record the launch plan below reuses.
    with tracking._RecordLock(record.yaml_path):
        fresh = tracking.load_record(record.yaml_path)
        tracking.mark_resumed(fresh, save=False)
        tracking.save_record(fresh)
    record.resume_count = fresh.resume_count
    record.last_resumed_at = fresh.last_resumed_at

    activity.log_event(
        "worktree_resumed",
        worktree_id=record.worktree_id,
        branch=record.branch,
        resume_count=record.resume_count,
    )

    # ── Execution-time fallback ladder (run AFTER the operator hits Enter) ────
    # Open / Resume / Bare resume all land here, differing only by the
    # no_mux/bare_resume options carried on the decision. The picker row those
    # options were chosen from can be stale (cached, or the fleet populate
    # missed a just-started session), so re-resolve the ONE worktree's live
    # truth here and let reality -- not the possibly-stale choice -- drive the
    # launch. Interactive launches only: the programmatic --json/--base (ACP)
    # paths need clean stdio and their explicit no_mux/no_resume respected, and
    # an ACP worktree has no mux to reattach anyway.
    _interactive = not getattr(args, "json", False) and not getattr(args, "base", False)
    if _interactive and not args.dry_run:
        try:
            _verdict = sessions.verify_worktree_active(record)
        except Exception:
            _verdict = None
        # (1) A live ``wt-<id>`` mux always wins: reattach it regardless of the
        # original choice. Without this, the Open sub-menu's no-mux toggle would
        # launch a second, detached Copilot beside the live session, and Bare
        # resume would spawn a bare Copilot in HOME next to it. Clearing both
        # overrides routes the launcher through its ``has-session`` reattach
        # branch (which ignores the launch cmd), so the operator lands back in
        # the running session -- the "never fork a live worktree" invariant.
        if _verdict is not None and _verdict.mux_live:
            if getattr(args, "no_mux", False) or getattr(args, "bare_resume", False):
                print("   ↻ Live mux session found -- reattaching it "
                      "(overriding the requested launch mode).")
            args.no_mux = False
            args.bare_resume = False
        # (2) Persist this fresh verdict to the record cache BEFORE we hand the
        # plan to the launcher, so the NEXT picker paint reflects reality even
        # if this (re)attach crashes. The scenario: the pre-Enter row was stale
        # (looked stopped -> only "Resume", no "Stop"); at Enter we discover a
        # live mux and reattach, but the mux then crashes internally. Without a
        # write-back the next launch repaints that same stale row and the
        # operator still can't "Stop" the crashing mux. Stamping the mux + bound
        # liveness we just authoritatively resolved makes the next first-paint
        # offer Open/Stop (live mux) or Reclaim (bound/bare Copilot). Same
        # authoritative source + hint the engine's menu-open stamp uses;
        # best-effort, never blocks or fails the launch.
        if _verdict is not None:
            try:
                tracking.stamp_mux_live(
                    record.worktree_id, _verdict.mux_live, refresh=True)
                tracking.stamp_bound_live(
                    record.worktree_id, bool(_verdict.live_session_ids),
                    refresh=True)
            except Exception:
                pass

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
    merged_env = _build_env(
        profile, _repo_session_env(config, record.worktree_path),
        work_dir=record.worktree_path,
    )

    # two-step-restore "Bare resume": launch Copilot in the HOME dir instead of
    # the worktree cwd, and skip --resume, so a CLI bug that fails to start
    # Copilot inside a repo/worktree directory is dodged. The mux session is
    # still named ``wt-<id>`` (correct worktree identity), and the plan carries
    # ``status_path`` (the real worktree) so the status bar renders the
    # worktree's locus + git disposition despite the HOME pane cwd; the operator
    # restores the conversation with a manual ``/resume <id>``.
    bare_resume = getattr(args, "bare_resume", False)
    plan_work_dir = record.worktree_path
    if bare_resume:
        plan_work_dir = os.path.expanduser("~")
        launch_cmd = _build_launch_cmd(config, args, plan_work_dir, profile=profile)
        merged_env = _build_env(
            profile, _repo_session_env(config, plan_work_dir),
            work_dir=plan_work_dir,
        )

    # Auto-resume target: the ONE session Open / Resume / Bare resume all agree
    # on -- the record's asserted lifecycle head when it still has on-disk
    # conversation data, else the filesystem-latest valid session (see
    # ``sessions.resolve_resume_target``). Resolved once here, freshly, so the
    # "if there's a head session, always try to resume it" fallback holds for
    # every entry mode: a plain Resume/Open passes it to ``--resume``; Bare
    # resume surfaces it as the ``/resume`` id and binds the session back to the
    # worktree. ``None`` means genuinely nothing to resume (cold start).
    resume_target = None
    if not getattr(args, "no_resume", False):
        resume_target = sessions.resolve_resume_target(record)

    no_resume = getattr(args, "no_resume", False) or bare_resume
    if not no_resume:
        if resume_target:
            # copilot's --resume[=value] is an optional-value option; the id
            # MUST be attached with '=' or it is treated as a stray operand
            # ("unknown command").
            launch_cmd.append(f"--resume={resume_target}")
            print(f"   Resuming session: {resume_target[:12]}…")
        else:
            # Fix B: never auto-resume a foreign ``parent_session`` -- it
            # belongs to a different worktree, so Copilot's resume-auto-cd
            # would adopt its persisted cwd and launch this tab in the
            # parent's directory (worktree id/path mismatch). Hint only.
            _emit_parent_context_hint(record)
    elif bare_resume:
        # Surface the id the operator will type: the resolved head session
        # (prefers the lifecycle head over pure mtime, and is stub-validated).
        print(f"   Bare resume: launching Copilot in {plan_work_dir} "
              f"(no auto-resume, dodges the worktree-cwd start bug).")
        if resume_target:
            print(f"   Inside Copilot, run:  /resume {resume_target}")

    # Bare resume first creates a temporary session in HOME, then the operator
    # switches to the intended historical session with /resume. Carry a scoped
    # binding that the session hooks consume only when the reported session ID
    # exactly matches that intended target. This stitches the resumed session
    # back to its worktree without ambient WORKTREE_* identity or accidentally
    # binding the temporary HOME session.
    if bare_resume and resume_target:
        merged_env = dict(merged_env)
        merged_env[_SESSION_BIND_PROJECT] = config.repo_name
        merged_env[_SESSION_BIND_WORKTREE] = record.worktree_id
        merged_env[_SESSION_BIND_SESSION] = resume_target

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
        "work_dir": plan_work_dir,
        # The mux status bar (status-updater --path) must render the *worktree's*
        # identity + git disposition, even when Copilot's pane cwd is elsewhere.
        # In two-step "Bare resume" ``work_dir`` is HOME (to dodge the worktree-
        # cwd start bug), so carry the real worktree path separately -- otherwise
        # the bar loses the repo:id4 locus and shows HOME's (base) state.
        "status_path": record.worktree_path,
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
        output.dry_run("Would add worktree path to trustedFolders")
        launch_cmd = _build_launch_cmd(config, args, worktree_path, profile=profile)
        merged_env = _build_env(
            profile, _repo_session_env(config, worktree_path),
            work_dir=worktree_path,
        )
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
        owner_ref=getattr(args, "owner_ref", None),
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


def _worktree_id_from_git(cwd: Path) -> str | None:
    """Return the git-internal worktree name if CWD is inside a *linked*
    worktree, else None.

    Git has no notion of a shared "worktree root": ``git worktree add <path>``
    places a worktree at an arbitrary folder and records its admin data at
    ``<main-repo>/.git/worktrees/<name>/``. From anywhere inside a linked
    worktree, ``git rev-parse --git-dir`` therefore resolves to
    ``<main-repo>/.git/worktrees/<name>`` -- and ``<name>`` is exactly the
    agent-worktrees ID (we name the git worktree after the ID). The *main*
    worktree's git-dir is plain ``.git`` (no ``worktrees/`` parent), which
    yields no id. This is layout-independent, so it survives a ``worktree_root``
    change (copilot-extensions#59).
    """
    try:
        result = git_ops.git(
            "rev-parse", "--git-dir", cwd=str(cwd), check=False, timeout=10
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    git_dir = (result.stdout or "").strip()
    if not git_dir:
        return None
    p = Path(git_dir)
    if not p.is_absolute():
        p = (cwd / p).resolve()
    # A linked worktree's git-dir is ``.../.git/worktrees/<name>``.
    if p.parent.name == "worktrees" and p.name and p.name != "worktrees":
        return p.name
    return None


def _infer_worktree_id_from_worktree_root(
    config: cfg.Config | None, cwd: Path
) -> str | None:
    """Legacy fallback: derive the ID from the first path component under the
    configured ``worktree_root``.

    Superseded by git-based + tracked-path resolution in
    :func:`_infer_worktree_id_from_cwd`; retained only as a last resort. This
    single-root assumption is exactly what copilot-extensions#59 fixed (it
    silently failed for worktrees created under a *previous* ``worktree_root``
    layout), so do not rely on it as the primary path.
    """
    try:
        if config is None:
            config = cfg.load_config()
        wt_root = Path(config.default_repo.worktree_root).resolve()
    except Exception:
        return None

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


def _infer_worktree_id_from_cwd(
    config: cfg.Config | None = None,
) -> str | None:
    """Derive the worktree ID from the current working directory.

    Identity is resolved the way git itself resolves a linked worktree --
    **independent of any configured ``worktree_root``**. Because git records a
    worktree at an arbitrary path (there is no shared "root" in git's model),
    the current worktree is identifiable from anywhere inside it, regardless of
    where it lives on disk. This keeps inference correct across a
    ``worktree_root`` layout change: worktrees created under an older root are
    still resolved (copilot-extensions#59).

    Resolution order (each root-independent; the legacy single-root scan is
    only a last resort):
      1. **git's own identity** -- ``git rev-parse --git-dir`` under a linked
         worktree is ``.../.git/worktrees/<name>``; ``<name>`` is the tracking
         ID. Authoritative even when the tracking YAML is briefly absent.
      2. **tracked-path match** -- match CWD against each record's recorded
         ``worktree_path`` (:func:`tracking.find_worktree_id_by_cwd`,
         deepest-match wins).
      3. **legacy ``worktree_root`` prefix scan** -- the pre-fix single-root
         assumption, kept only for safety.
    """
    cwd = Path.cwd().resolve()
    tdir = cfg.tracking_dir()

    # 1. Ask git. A linked worktree's git-dir names the worktree directly.
    git_id = _worktree_id_from_git(cwd)
    if git_id:
        if (tdir / f"{git_id}.yaml").exists():
            return git_id
        # Tracking YAML briefly missing: prefer a path match if one exists,
        # else trust git's authoritative identity.
        return tracking.find_worktree_id_by_cwd(str(cwd)) or git_id

    # 2. Match CWD against recorded worktree paths (root-independent).
    path_id = tracking.find_worktree_id_by_cwd(str(cwd))
    if path_id:
        return path_id

    # 3. Legacy single-root scan (last resort; the #59 failure mode).
    return _infer_worktree_id_from_worktree_root(config, cwd)


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
    _sweep_managed_on_exit()
    _sweep_launcher_shells_on_exit()


def _sweep_managed_on_exit() -> None:
    """Best-effort GC of leaked system/bridge worktrees at a lifecycle boundary.

    Same **no-daemon** cadence as the mux reap: the conservative managed sweep
    (:func:`sweep_managed_worktrees` -- provably-dead only: FINAL/UNUSED, no
    active process, no follow-up, idle past grace) runs on picker *launch* and
    session *end*, so leaked ``system``/``bridge`` worktrees don't accumulate
    without any scheduled task or monitor process (#1069). A caller worktree's
    session ending is exactly when its bridge worktree becomes reapable, so this
    boundary is where the accumulation is caught. Never raises.
    """
    try:
        report = sweep_managed_worktrees()
        removed = report.get("removed") or []
        if removed:
            output.ok(
                f"GC'd {len(removed)} leaked managed worktree(s): "
                + ", ".join(x["id"] for x in removed)
            )
    except Exception:
        pass


def _sweep_launcher_shells_on_exit() -> None:
    """Best-effort reap of orphaned launcher shells on the no-daemon cadence.

    Runs the same conservative, positive-signature predicate as the
    ``reap-shells`` command (parent-exited + idle, with service/self/live-
    descendant safety), killing for real. Shares the picker-launch + session-end
    cadence with the mux and managed sweeps so pwsh/python launcher scaffolding
    stranded by a force-closed terminal doesn't accumulate without a daemon
    (copilot-extensions #102). The ending session's own tree is always spared
    (self-preservation), and a live session's launcher is spared while its
    terminal (its parent) is alive. Never raises.
    """
    try:
        payload = reap_orphan_launcher_shells(dry_run=False)
        reaped = payload.get("reaped") or []
        if reaped:
            output.ok(
                f"Reaped {len(reaped)} orphaned launcher shell(s): "
                + ", ".join(str(p) for p in reaped)
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
            abandon=getattr(args, "abandon", False),
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
            if not yaml_path.exists():
                output.err(f"Tracking file not found for {worktree_id}")
                return 1
            if args.title:
                # Foreground RMW (#4547): load -> set title -> save under the
                # blocking record lock (no I/O in the window). Only taken when
                # there is actually a title to write.
                with tracking._RecordLock(yaml_path):
                    record = tracking.load_record(yaml_path)
                    record.title = args.title.replace("\n", " ").strip()
                    tracking.save_record(record)
                print(f"[OK] Worktree {worktree_id} title updated: {args.title}")
            else:
                output.err("--title-only requires --title")
                return 1
            return 0

        success = fin.push_changes(
            worktree_id, config,
            title=args.title,
            dry_run=args.dry_run,
            allow_unsquashed=getattr(args, "allow_unsquashed", False),
        )

        _reminder = _pr_reminder_for(
            config, "push-changes", ok=bool(success),
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
            out = {
                "worktree_id": worktree_id,
                "success": success,
                "status": final_status,
            }
            if _reminder is not None:
                out["reminder"] = _reminder.as_dict()
            _json_output(out)
        elif _reminder is not None:
            print(_reminder.text(), file=sys.stderr)

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
            draft=getattr(args, "draft", False),
            attribution=(not getattr(args, "no_attribution", False)),
            dry_run=args.dry_run,
        )

        _reminder = _pr_reminder_for(
            config, "create-pr",
            state=("created" if result.get("success") else ""),
            ok=bool(result.get("success")),
            reason=("" if result.get("success") else result.get("error", "")),
        )
        if _reminder is not None:
            result["reminder"] = _reminder.as_dict()
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
                if result.get("draft"):
                    output.warn(
                        f"PR #{result.get('number')} opened as a DRAFT "
                        f"(not yet ready for review). Run "
                        f"'agent-worktrees pr-ready' to move it out of draft."
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

        if not use_json and _reminder is not None:
            print(_reminder.text(), file=sys.stderr)
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
    """Move a PR out of draft (draft -> ready-for-review)."""
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
            n = result.get("number")
            repo = result.get("repo")
            url = result.get("url")
            if result.get("transition") == "release-legacy-hold":
                output.ok(
                    f"Removed legacy hold label from PR #{n} ({repo}): {url}. "
                    f"PR is ready for review (it does not grant merge consent -- "
                    f"use pr-merge for that)."
                )
            else:
                output.ok(
                    f"Moved PR #{n} out of draft ({repo}): {url}. "
                    f"It is now ready for review (pr-ready does not grant merge "
                    f"consent -- use pr-merge for that)."
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
        worktree_id, all_prs=getattr(args, "all", False),
        live=not getattr(args, "no_live", False), config=config,
    )
    # Attach the repo's PR-flow profile so the caller can see, at a glance,
    # which flow this repo uses and whether the pr-* verbs apply here.
    flow = _pr_flow_profile(config.default_repo)
    result["flow"] = {
        "profile": flow.profile,
        "requires_pr": flow.requires_pr,
        "merge_mode": flow.merge_mode,
        "applicable_verbs": list(flow.applicable_verbs),
        "summary": flow.summary,
    }
    # Stay-on-rails reminder: what this repo's flow allows next. Derive a coarse
    # PR state from the live verdict/merge-state when present so the reminder is
    # situational. Carried as a JSON `reminder` node and printed in human mode.
    from . import pr_contract as pc
    _live = result.get("live") if isinstance(result.get("live"), dict) else {}
    if result.get("state") == "merged" or _live.get("merge_state") == "merged":
        _state = pc.PR_STATE_MERGED
    elif _live.get("conflict"):
        _state = pc.PR_STATE_CONFLICT
    elif _live.get("verdict") == "approved":
        _state = pc.PR_STATE_APPROVED
    elif _live.get("verdict") in ("changes_requested", "change_requested"):
        _state = pc.PR_STATE_CHANGES_REQUESTED
    elif result.get("has_pr"):
        _state = pc.PR_STATE_AWAITING_REVIEW
    else:
        _state = ""
    _reminder = _pr_reminder_for(
        config, "pr-status", state=_state, ok=not result.get("error"),
    )
    if _reminder is not None:
        result["reminder"] = _reminder.as_dict()
    # First-class comment threads (opt-in). Fetched before any JSON emit so the
    # machine-readable output carries them too.
    want_threads = getattr(args, "threads", False) or getattr(args, "resolve_threads", False)
    if want_threads and result.get("has_pr"):
        result["thread_report"] = pr_ops.pr_threads(
            worktree_id, resolve=getattr(args, "resolve_threads", False),
            config=config,
        )
    if use_json:
        _json_output(result)
        return 0 if result.get("has_pr") or "error" not in result else 1
    if result.get("error"):
        output.err(result["error"])
        return 1
    print(f"  flow:     {flow.profile} -- {flow.summary}")
    if _reminder is not None:
        print(_reminder.text(), file=sys.stderr)
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
    live = result.get("live")
    if isinstance(live, dict):
        verdict = live.get("verdict") or "(none)"
        print("  live:")
        print(f"    verdict:     {verdict}")
        print(f"    merge state: {live.get('merge_state')}")
        if live.get("conflict"):
            print("    conflict:    yes (needs rebase)")
        if live.get("held"):
            print(f"    held by:     {', '.join(live['held'])}")
        if live.get("wip"):
            print("    wip:         yes")
        consent = ("present" if live.get("consent_present")
                   else ("eligible" if live.get("eligible") else "not yet"))
        print(f"    consent:     {consent}")
    if getattr(args, "all", False) and result.get("prs"):
        print(f"  all PRs ({count}):")
        for p in result["prs"]:
            num = f"#{p['number']}" if p.get("number") else "(unnumbered)"
            print(f"    - {num} [{p.get('state')}] {p.get('branch')}")
    if result.get("pull_forward_recommended"):
        print()
        output.warn("Pull-forward recommended (active PR merged):")
        print(f"  {result.get('next_action')}")

    threads = result.get("thread_report")
    if isinstance(threads, dict):
        if not threads.get("supported", True):
            output.warn(f"  threads:  unavailable ({threads.get('reason', '')})")
        else:
            active = threads.get("active_count", 0)
            print(f"  threads:  {len(threads.get('threads', []))} "
                  f"({active} active)")
            for t in threads.get("threads", []):
                if not t.get("active"):
                    continue
                loc = f" {t['file_path']}" if t.get("file_path") else ""
                print(f"    - #{t.get('id')} [{t.get('status')}]{loc}")
                for c in t.get("comments", []):
                    body = (c.get("content") or "").strip().replace("\n", " ")
                    print(f"        {c.get('author')}: {body[:200]}")
            if threads.get("resolved"):
                output.ok("  resolved active comment threads.")
            elif threads.get("resolve_error"):
                output.warn(f"  resolve failed: {threads.get('resolve_error')}")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# pr-complete
# ═══════════════════════════════════════════════════════════════════════════

def cmd_pr_complete(args: argparse.Namespace) -> int:
    """Reconcile the worktree onto the default branch after its PR merged.

    Distinct from ``finalize``: this lands the worktree *forward* (fast-forward
    past a squash-merge, or rebase to preserve new work); it does not prune the
    worktree.  See :func:`agent_worktrees.pr_complete.complete_worktree`.
    """
    from . import pr_complete

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

    result = pr_complete.complete_worktree(
        worktree_id, config, dry_run=getattr(args, "dry_run", False),
    )
    if use_json:
        _json_output(result)
        return 0 if result.get("success") else 1
    if result.get("success"):
        output.ok(result.get("message", f"pr-complete: {result.get('action')}"))
        if result.get("action") == "reset-past-squash" and result.get("backup_ref"):
            print(f"  recover the pre-complete state with: "
                  f"git reset --hard {result['backup_ref']}")
    else:
        output.err(result.get("error", "pr-complete failed."))
    return 0 if result.get("success") else 1


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
        # Foreground verb (#4547): load -> mutate -> save under the blocking
        # record lock (no I/O in the window) so a concurrent writer can't clobber
        # the manual status/title update. Skip the lock when there is nothing to
        # write (--title-only with no --title).
        if (not args.title_only) or args.title:
            with tracking._RecordLock(yaml_path):
                record = tracking.load_record(yaml_path)
                if args.title:
                    record.title = args.title.replace("\n", " ").strip()
                if not args.title_only:
                    tracking.update_status(record, "complete", save=False)
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

def _cmd_status_write(
    args: argparse.Namespace, *, summary: str | None, follow_up: bool | None,
) -> int:
    """Write mode of `status`: annotate THIS worktree's agent-asserted
    disposition (summary / follow-up). Resolves the worktree from CWD (or
    --worktree-id). Orthogonal to git/session state; see the worktree-status-core
    effort and the agent-fabric vision (disposition-is-asserted-pulse-is-derived).
    """
    config = cfg.load_config()
    worktree_id = _infer_worktree_id(getattr(args, "worktree_id", None), config)
    if not worktree_id:
        output.err(
            "Could not determine worktree ID. Run from inside a worktree "
            "or pass --worktree-id."
        )
        return 1
    worktree_id = _resolve_worktree_id(worktree_id)
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        output.err(
            f"Tracking file not found at {yaml_path}. "
            "Cannot annotate an unknown worktree."
        )
        return 1
    # Foreground verb (#4547): the whole load -> set_disposition -> save is a
    # critical RMW held under the blocking record lock, so a concurrent Picker
    # best-effort sweep skips rather than clobbering the disposition overlay.
    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        tracking.set_disposition(
            record, summary=summary, follow_up=follow_up, save=False)
        tracking.save_record(record)
    flag = "follow-ups pending" if record.follow_up else "resolved"
    msg = f"[OK] Worktree {worktree_id[-4:]} disposition: {flag}"
    if record.summary:
        msg += f" -- {record.summary}"
    print(msg)
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    # worktree-status-core: write mode. When any disposition flag is present,
    # annotate THIS worktree (from CWD) and return -- leaving the fleet-wide
    # read path (`status` / `status --json`, no write flags) untouched.
    _summary = getattr(args, "summary", None)
    _fu = getattr(args, "follow_up", False)
    _res = getattr(args, "resolved", False)
    if _fu and _res:
        output.err("Pass only one of --follow-up / --resolved.")
        return 1
    _follow = True if _fu else (False if _res else None)
    if _summary is not None or _follow is not None:
        return _cmd_status_write(args, summary=_summary, follow_up=_follow)

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


def _resolve_remote_default_branch(
    path: str,
    remote: str,
    *,
    config_default: str | None = None,
    allow_remote: bool = False,
) -> str | None:
    """Resolve a repo's default branch from the REMOTE's configuration, never a
    (possibly stale) local branch. Returns ``None`` if nothing resolves.

    Order:
      1. ``config_default`` if ``<remote>/<config_default>`` still exists (honor
         a valid explicit hint).
      2. The local ``<remote>/HEAD`` symbolic ref -- the remote's own default
         when a clone / ``git remote set-head`` recorded it (fast, offline).
      3. (``allow_remote`` only) ``git ls-remote --symref <remote> HEAD`` --
         asks the remote directly, so it works even when the local
         ``<remote>/HEAD`` was never set. Authoritative source of truth.
      4. First of ``main`` / ``master`` present as a *remote-tracking* ref
         (``<remote>/<cand>``) -- main-first, never a local head.

    Network is used only when ``allow_remote=True`` (step 3), so hot/pollable
    callers stay cheap and offline by leaving it ``False``. See dotfiles#1046.
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

    if allow_remote:
        try:
            ls = git_ops.git("ls-remote", "--symref", remote, "HEAD",
                             cwd=path, check=False, timeout=10)
        except Exception:
            ls = None
        if ls is not None and ls.returncode == 0:
            for line in ls.stdout.splitlines():
                # Format: "ref: refs/heads/<branch>\tHEAD"
                line = line.strip()
                if line.startswith("ref:") and "HEAD" in line:
                    ref = line[len("ref:"):].split("\t", 1)[0].strip()
                    if ref.startswith("refs/heads/"):
                        return ref.rsplit("/", 1)[-1]

    for cand in ("main", "master"):
        if _has(f"{remote}/{cand}"):
            return cand

    return None


def _detect_upstream_branch(
    path: str, remote: str, config_default: str | None,
) -> str | None:
    """Detect the repo's upstream default branch (``main``/``master``/...).

    Thin, **offline** wrapper over ``_resolve_remote_default_branch`` for the
    status segment, which runs in arbitrary repos and polls frequently -- so it
    must not hit the network and cannot trust the ambient config's default
    (e.g. a ``master`` project binstub polling a ``main`` repo). Falls back to
    the config default as a last-resort hint (may be stale) when nothing else
    resolves.
    """
    return _resolve_remote_default_branch(
        path, remote, config_default=config_default, allow_remote=False,
    ) or config_default


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


def _slot_superseded(active: str, mine: str, versions_root: str) -> bool:
    """Pure comparison: is ``mine`` a *different* runtime slot than the active
    one, with both living under the ``versions/`` slot root?

    Conservative by design -- returns False unless BOTH paths resolve *under*
    ``versions_root`` (so a dev/source or system-python interpreter, which has no
    version slot to compare, is never judged superseded). Separator- and
    case-normalized (``normpath``/``normcase``) so it is correct on Windows
    (``\\``, case-insensitive) and POSIX alike. Factored out (plain strings, no
    filesystem) so the decision is unit-testable without symlinked version trees.
    """
    def _norm(p: str) -> str:
        return os.path.normcase(os.path.normpath(p))

    active, mine, versions_root = _norm(active), _norm(mine), _norm(versions_root)

    def _under(p: str) -> bool:
        return p == versions_root or p.startswith(versions_root + os.sep)

    if not (_under(active) and _under(mine)):
        return False
    return mine != active


def _runtime_superseded(
    *, prefix: str | None = None, install_root: Path | None = None
) -> bool:
    """True when a newer agent-worktrees runtime has superseded the one running
    this ``status-updater`` -- i.e. the active ``versions/<current-version>``
    slot (published by the marker) no longer resolves to this process's
    ``sys.prefix`` (both under ``versions/``).

    A version update publishes a fresh ``versions/<v>`` slot via the
    ``current-version`` marker (junction-free; #1106) but cannot reap an
    already-running updater: its mux session is still live (so ``_has_session``
    keeps it serving) and a new-version updater only spawns on the next
    attach/join. Left alone, one updater per (session x version) piles up across
    every deploy (dotfiles #911 -- observed dev315..dev392 all still running for
    days). This self-check lets a superseded updater retire on its next tick; the
    next attach spawns a current-version one. Degrade-safe: any resolution error,
    a missing marker, or a non-slot interpreter keeps it serving.
    """
    try:
        root = install_root if install_root is not None else cfg.install_dir()
        versions_root = os.path.realpath(os.path.join(str(root), "versions"))
        try:
            ver = (Path(str(root)) / "current-version").read_text("utf-8").strip()
        except OSError:
            ver = ""
        if not ver:
            return False  # no marker -> cannot determine; keep serving
        active = os.path.realpath(os.path.join(str(root), "versions", ver))
        mine = os.path.realpath(prefix if prefix is not None else sys.prefix)
    except Exception:
        return False
    return _slot_superseded(active, mine, versions_root)


def _spawn_status_updater(worktree_id: str, path: str | None) -> bool:
    """Detached-spawn the status-bar updater for ``wt-<worktree_id>``.

    The updater is the background loop that keeps a muxed session's status bar
    fresh (identity in ``@aw_ctx`` once, git disposition in ``@aw_seg`` each
    tick).  Historically it was spawned *only* by the launcher at psmux
    create/join (``Start-StatusUpdater`` in launch-session.ps1).  That single
    seam left long-lived attached sessions with no way to re-seed their updater
    after it retired -- e.g. a version deploy makes every running updater
    ``_runtime_superseded``-retire (dotfiles #911), and an attached session is
    never re-run through the launcher, so its bar goes dark until the next
    manual attach.  Reseeding from the ``sessionStart`` hook (``cmd_register_
    session``) closes that gap: every new Copilot session re-asserts its own
    updater, cross-platform (one Python seam behind both the ps1 and bash
    hooks) and independent of psmux attach/join (dotfiles #915).

    Idempotent by construction: the updater's ``@aw_updater`` token guard elects
    a single live instance, so a duplicate spawned here retires on its next
    tick.  Best-effort and cheap: it no-ops (returns ``False``) unless a mux is
    present *and* a live ``wt-<id>`` session exists (so a bare / non-mux session
    never spawns a pointless loop), and never raises into the hook path.
    """
    import shutil
    import subprocess

    if not worktree_id:
        return False
    sess = f"wt-{worktree_id}"
    try:
        mux = "psmux" if shutil.which("psmux") else (
            "tmux" if shutil.which("tmux") else None)
        if not mux:
            return False
        mux_bin = shutil.which(mux) or mux
        # Only seed when this session is actually under mux -- a bare/non-mux
        # Copilot has no status bar to feed, and spawning a loop that would
        # immediately see "gone" is wasteful.
        r = subprocess.run(
            [mux_bin, "has-session", "-t", sess],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            return False

        argv = [sys.executable, "-m", "agent_worktrees", "status-updater",
                "--session", sess, "--mux", mux]
        if path:
            argv += ["--path", path]

        kwargs: dict = {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            # Never inherit the spawner's cwd.  Both spawn seams -- the launcher
            # and the sessionStart reseed hook -- run FROM the plugin payload
            # dir, and a detached child that keeps that dir as its cwd holds an
            # open directory handle that blocks ``copilot plugin update`` from
            # replacing the payload on Windows (``os error 32``: the file/dir is
            # in use).  The updater locates its worktree via ``--path``, so its
            # cwd is irrelevant to its work -- root it at HOME.
            "cwd": os.path.expanduser("~"),
        }
        if os.name == "nt":
            # Fully detach on Windows: no console window, own process group, and
            # break away from the job so it outlives the hook process.
            DETACHED_PROCESS = 0x00000008
            CREATE_NEW_PROCESS_GROUP = 0x00000200
            CREATE_NO_WINDOW = 0x08000000
            CREATE_BREAKAWAY_FROM_JOB = 0x01000000
            kwargs["creationflags"] = (
                DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
                | CREATE_NO_WINDOW | CREATE_BREAKAWAY_FROM_JOB
            )
        else:
            kwargs["start_new_session"] = True  # setsid: survive the hook exit

        subprocess.Popen(argv, **kwargs)  # detached: fixed, trusted argv
        return True
    except Exception:
        return False


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

    def _mux(*a: str) -> subprocess.CompletedProcess[str] | None:
        try:
            return subprocess.run(
                [mux_bin, *a],
                capture_output=True, text=True, timeout=15,
            )
        except Exception:
            return None

    def _session_state() -> str:
        """Tri-state liveness: ``alive`` | ``gone`` | ``unknown``.

        ``unknown`` distinguishes a *transient* mux failure (a timed-out or
        errored ``has-session`` -> ``_mux`` returned ``None``) from a definitive
        "session is gone" (the mux ran and reported non-zero).  The loop must
        NOT retire on a transient hiccup -- under a busy high-framerate TUI the
        mux can momentarily fail to answer within the timeout, and treating that
        as "gone" silently kills the bar for the rest of the session (dotfiles
        #915).  Only a definitive ``gone`` (or a long run of ``unknown``s) ends
        the loop.
        """
        r = _mux("has-session", "-t", sess)
        if r is None:
            return "unknown"
        return "alive" if r.returncode == 0 else "gone"

    # A newer runtime already active? Then this updater is a leftover from a
    # pre-update version whose session is still live -- retire immediately rather
    # than serve stale (dotfiles #911). The next attach spawns a current one.
    if _runtime_superseded():
        return 0

    def _set(opt: str, val: str) -> None:
        # Session-scoped (no -g): empirically isolated per session on psmux
        # 3.3.6 and tmux 3.4, so concurrent worktree sessions don't clobber
        # each other's bar.
        _mux("set-option", "-t", sess, opt, val)

    # Bail only on a *definitive* absence -- a transient mux hiccup at startup
    # must not abort the updater before it ever paints (dotfiles #915).
    if _session_state() == "gone":
        return 0

    # Debounce redundant spawns (dotfiles #911).  Both the launcher
    # (Start-StatusUpdater) and the sessionStart reseed hook (cmd_register_
    # session) (re)spawn an updater at session start, so without a guard every
    # session leaks a *pair* of updaters -- and a duplicate that pins the plugin
    # payload dir as its cwd blocks ``copilot plugin update`` on Windows.  If a
    # live updater already owns this session on the *current* runtime, this
    # spawn is redundant: retire now rather than run a second loop.  A superseded
    # owner (older version, mid-deploy) is deliberately NOT deferred to -- the
    # reseed must replace it, so the bar never goes dark after a deploy
    # (dotfiles #915); likewise an owner with no published prefix (a pre-upgrade
    # updater) is replaced rather than deferred to, so the transition can't wedge
    # a dark bar.
    from . import update_stage as _upd
    _owner = _mux("display-message", "-t", sess, "-p", "#{@aw_updater}")
    if _owner is not None and _owner.returncode == 0:
        _tok = (_owner.stdout or "").strip()
        if _tok.isdigit() and int(_tok) != os.getpid() and _upd._pid_alive(
                int(_tok)):
            _op = _mux(
                "display-message", "-t", sess, "-p", "#{@aw_updater_prefix}")
            _owner_prefix = (_op.stdout or "").strip() if (
                _op is not None and _op.returncode == 0) else ""
            if _owner_prefix and not _runtime_superseded(prefix=_owner_prefix):
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
    # Publish this updater's runtime slot (``sys.prefix``) alongside the pid
    # token so a later spawn can distinguish a live *current* owner (defer to it)
    # from a live but superseded one (replace it) -- see the debounce above.
    _set("@aw_updater_prefix", os.path.realpath(sys.prefix))

    # Identity (machine | env | repo:id4) is static for the session's life:
    # render once, push to @aw_ctx, never poll it again.
    try:
        _set("@aw_ctx", _render_status_context(path, plain=False))
    except Exception:
        pass

    # Disposition (DIRTY/FINAL/WIP/CONVO/...) changes as work happens: refresh
    # @aw_seg on the interval until the session ends, a newer updater takes
    # over, or a newer runtime supersedes this one (dotfiles #911).  The bar
    # itself does zero process work between updates -- the mux only re-runs the
    # strftime %H:%M clock.
    #
    # Transient mux failures (``_session_state() == "unknown"``) are tolerated:
    # they do NOT retire the updater, they only skip that tick's liveness proof.
    # The loop exits only on a definitive ``gone``, a lost ownership token, a
    # superseding runtime, or ``_MAX_TRANSIENT_STRIKES`` consecutive unknowns
    # (a genuinely wedged mux -- ~strikes*interval seconds of silence), so a
    # busy-TUI timeout can no longer silently kill the bar (dotfiles #915).
    _MAX_TRANSIENT_STRIKES = 20
    strikes = 0
    while True:
        state = _session_state()
        if state == "gone":
            break
        if state == "unknown":
            strikes += 1
            if strikes >= _MAX_TRANSIENT_STRIKES:
                break
            time.sleep(interval)
            continue
        strikes = 0  # a live answer resets the transient run
        if not _owns() or _runtime_superseded():
            break
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

def _cmd_list_stream(args: argparse.Namespace, records) -> int:
    """Emit the worktree listing as newline-delimited JSON for the Picker's
    streaming SSH consumer -- one JSON object per line, each flushed immediately.

    Frame order (mirrors the two-phase load, collapsed into one connection):

    - ``{"type":"begin","count":N}`` -- so the reader knows the roster size.
    - ``{"type":"worktree","phase":"fast","wt":{...}}`` per record, with **no**
      git classification, so enumeration paints at once.
    - when ``--classify`` is set, ``{"type":"worktree","phase":"classified",
      "wt":{...}}`` per record as its git state is computed (streamed one at a
      time via :func:`_classify_one_record`), so rows upgrade progressively
      instead of after a terminal blob.
    - ``{"type":"done","count":N}``.

    Each line is a standalone object written to the real stdout and flushed, so a
    reader over SSH sees rows as they are produced rather than all at the end."""
    out = sys.__stdout__

    def emit(obj: dict) -> None:
        out.write(json.dumps(obj, default=str))
        out.write("\n")
        out.flush()

    mux_map: dict[str, sessions.MuxInfo] = {}
    if getattr(args, "mux_details", False):
        mux_map = sessions.mux_status_many([r.worktree_id for r in records])
    session_ctx = sessions.scan_sessions_fast(records)
    # #93: the Picker's SSH consumer always requests --mux-details; derive the
    # bare (un-muxed) orphan worktrees in the same enriched pass so a *remote*
    # row carries the orphan marker. Gated on mux_details so a plain streaming
    # list never pays for the machine-wide process scan. Derived *after* the
    # begin frame (below) so the roster frame streams immediately; the name is
    # initialized here for the to_dict closure's late binding.
    bare_orphan_wts: set[str] | None = None

    def to_dict(rec, state_info):
        wt = _worktree_to_dict(
            rec, mux_info=mux_map.get(rec.worktree_id),
            session_ctx=session_ctx, state_info=state_info,
            bare_orphan_wts=bare_orphan_wts)
        title = wt.get("title")
        if not title or title == "null":
            wt["title"] = session_ctx.latest_summary.get(
                _normalize_path(rec.worktree_path))
        return wt

    emit({"type": "begin", "version": _JSON_SCHEMA_VERSION, "count": len(records)})
    # #93: derive the bare (un-muxed) orphan worktree set now -- after the roster
    # frame, so streaming's first paint is never blocked by the scan. Gated on
    # --mux-details (the Picker's flag); cheap (skips historical dirs) but kept
    # off the critical path regardless.
    if getattr(args, "mux_details", False):
        try:
            bare_orphan_wts = reclaim.bare_orphan_worktree_ids()
        except Exception:
            bare_orphan_wts = None
    for rec in records:
        emit({"type": "worktree", "phase": "fast", "wt": to_dict(rec, None)})
    if getattr(args, "classify", False):
        config = cfg.load_config()
        repo = config.default_repo
        active_paths = _build_active_paths(records, session_ctx)
        from .picker_tui.data_local import _stamp_from_raw
        for rec in records:
            info = _classify_one_record(
                rec, repo=repo, active_paths=active_paths,
                session_ctx=session_ctx)
            wt = to_dict(rec, info)
            # picker-cache-first-paint (dotfiles#948) remote write-back: warm
            # this machine's session-render cache so a future --cache-only fast
            # phase reads it directly.
            _stamp_from_raw(rec, wt, session_ctx)
            emit({"type": "worktree", "phase": "classified", "wt": wt})
    emit({"type": "done", "count": len(records)})
    return 0


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

    if getattr(args, "stream", False):
        # NDJSON streaming path (implies --json): the Picker's streaming SSH
        # consumer reads rows progressively over one connection.
        return _cmd_list_stream(args, records)

    if args.json:
        # picker-cache-first-paint (dotfiles#948): cache-only fast paint --
        # build rows from ONLY the cached session-render fields, no live scan,
        # so a remote SSH fast phase paints instantly and never re-reads
        # events.jsonl or scans processes. Never-populated -> Unknown.
        if getattr(args, "cache_only", False):
            from .picker_tui.data_local import _overlay_cached_state
            worktrees = []
            for rec in records:
                raw = _worktree_to_dict(rec)
                if rec.session_summary and not (
                        raw.get("title") and raw["title"] != "null"):
                    raw["title"] = rec.session_summary
                _overlay_cached_state(raw, rec)
                worktrees.append(raw)
            _json_output({"worktrees": worktrees})
            return 0
        mux_map: dict[str, sessions.MuxInfo] = {}
        if getattr(args, "mux_details", False):
            wt_ids = [rec.worktree_id for rec in records]
            mux_map = sessions.mux_status_many(wt_ids)
        session_ctx = sessions.scan_sessions_fast(records)
        state_map: dict[str, git_ops.WorktreeStateInfo] = {}
        if getattr(args, "classify", False):
            state_map = _classify_records(records, session_ctx)
        # #93: same enriched pass as the streaming path -- mark worktrees hosting
        # a bare (un-muxed) bound Copilot so the Picker (local or over SSH) can
        # annotate the row. Gated on --mux-details (the Picker's flag) so a plain
        # list --json stays cheap.
        bare_orphan_wts: set[str] | None = None
        if getattr(args, "mux_details", False):
            try:
                bare_orphan_wts = reclaim.bare_orphan_worktree_ids()
            except Exception:
                bare_orphan_wts = None
        worktrees = [
            _worktree_to_dict(
                rec, mux_info=mux_map.get(rec.worktree_id),
                session_ctx=session_ctx,
                state_info=state_map.get(rec.worktree_id),
                bare_orphan_wts=bare_orphan_wts,
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
        # picker-cache-first-paint (dotfiles#948) remote write-back: on the
        # authoritative classify pass, stamp each worktree's session-render cache
        # so a FUTURE --cache-only fast phase on THIS machine reads it directly.
        if getattr(args, "classify", False):
            from .picker_tui.data_local import _stamp_from_raw
            for wt_dict, rec in zip(worktrees, records, strict=True):
                _stamp_from_raw(rec, wt_dict, session_ctx)
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
# claims -- a worktree's full claim ledger (resource-claims legibility surface)
# ═══════════════════════════════════════════════════════════════════════════

def _inbound_claims(machine: str, worktree_id: str, cwd: str) -> dict:
    """Best-effort inbound tasks a worktree claims, via agent-dispatch.

    agent-worktrees does NOT depend on agent-dispatch (a-la-carte independence):
    this shells out to the ``agent-dispatch`` binstub only when present and
    degrades to ``{"available": False}`` on absence, error, or timeout. The
    dispatch call is scoped by running it in the worktree's own directory so its
    repo lane auto-resolves.
    """
    exe = shutil.which("agent-dispatch")
    if not exe:
        return {"available": False, "reason": "agent-dispatch not installed"}
    try:
        proc = subprocess.run(
            [exe, "worktree-status", "--machine", machine,
             "--worktree", worktree_id],
            cwd=cwd if cwd and Path(cwd).exists() else None,
            capture_output=True, text=True, timeout=15,
        )
    except (subprocess.SubprocessError, OSError) as e:
        return {"available": False, "reason": f"agent-dispatch call failed: {e}"}
    if proc.returncode != 0:
        return {"available": False,
                "reason": (proc.stderr or "").strip() or "agent-dispatch error"}
    try:
        data = json.loads(proc.stdout)
    except (ValueError, TypeError):
        return {"available": False, "reason": "unparseable agent-dispatch output"}
    assigned = data.get("assigned") or []
    owned = data.get("owned") or []
    return {"available": True, "assigned": assigned, "owned": owned}


def cmd_claims(args: argparse.Namespace) -> int:
    """Dispatch the claims verb: ``claims [worktree_id]`` renders a worktree's
    ledger; ``claims release <ref>`` retires one outbound claim.

    The first positional selects the mode: the literal ``release`` routes to
    :func:`_claims_release` (the rest are its ref); anything else is a
    worktree id for :func:`_claims_show`.
    """
    target = list(getattr(args, "target", None) or [])
    if target and target[0] == "add":
        if len(target) < 3:
            if args.json:
                return _json_error("claims add: usage 'add <kind> <ref>'", 2)
            output.err("claims add: usage 'add <kind> <ref>' "
                       "(kind: worktree|codespace|container|ssh|workdir|pr)")
            return 2
        return _claims_add(args, target[1], target[2])
    if target and target[0] == "release":
        if len(target) < 2:
            if args.json:
                return _json_error("claims release: missing <ref>", 2)
            output.err('claims release: missing <ref>. '
                       'Usage: claims release <ref> [--remove]')
            return 2
        return _claims_release(args, target[1])
    if target and target[0] == "settle":
        if len(target) < 2:
            if args.json:
                return _json_error("claims settle: missing <ref>", 2)
            output.err('claims settle: missing <ref>. '
                       'Usage: claims settle <ref> [--released]')
            return 2
        return _claims_settle(args, target[1])
    if target and target[0] == "sweep":
        return _claims_sweep(args)
    if target and target[0] == "cleanup":
        return _claims_cleanup(args)
    if target and target[0] == "orphans":
        return _claims_orphans(args)
    worktree_id = target[0] if target else None
    return _claims_show(args, worktree_id)


def _resolve_owner_ref_record_path(
    owner_ref: str, config: cfg.Config,
) -> tuple[Path | None, str, str | None]:
    """Resolve a qualified owner-ref to a local tracking record path.

    Returns ``(path, worktree_id, error)``. For a **same-machine** qualified ref
    ``machine/project/worktree_id`` the path is
    ``project_dir(project)/worktrees/{worktree_id}.yaml`` -- the same
    cross-project machinery ``finalize._settle_parent_obligation`` uses -- so a
    call-site whose cwd is NOT the owning worktree (e.g. agent-codespaces
    journaling a CodeSpace claim from the daemon's cwd) lands on the right
    record regardless of the current project. A **cross-machine** owner-ref
    yields ``(None, worktree_id, None)`` (no error): its ledger lives on that
    machine and settles via the lease disposition mirror, not here. A malformed
    / non-qualified ref yields an ``error``.
    """
    parsed = tracking.parse_claim_ref(owner_ref)
    if parsed is None or not parsed.is_qualified:
        return (None, "", f"--owner-ref must be a qualified "
                          f"machine/project/worktree_id ref (got {owner_ref!r})")
    if parsed.machine != config.machine:
        return (None, parsed.worktree_id, None)  # cross-machine -> lease mirror
    path = cfg.project_dir(parsed.project) / "worktrees" / f"{parsed.worktree_id}.yaml"
    return (path, parsed.worktree_id, None)


def _claims_add(args: argparse.Namespace, kind: str, ref: str) -> int:
    """Journal a new outbound resource claim on a worktree (Phase 3b-wiring).

    Completes the ledger CRUD (``show``/``add``/``settle``/``release``): records
    that this worktree owns a resource -- a borrowed CodeSpace, a container, a
    cross-repo worktree -- so the finalize obligation gate can hold it accountable.
    The claim starts ``active`` (unsettled). Dedups by ref (re-adding refreshes).

    The owner worktree is the current one unless ``--worktree`` names another in
    the current project, or ``--owner-ref <machine/project/worktree_id>`` names
    one cross-project (resolved by qualified ref -> ``project_dir/worktrees``,
    same-machine; a cross-machine owner-ref defers to the lease mirror). The
    owner-ref path is for a call-site whose cwd is not the borrowing worktree
    (e.g. agent-codespaces journaling a CodeSpace claim from the daemon's cwd).
    """
    valid_kinds = {"worktree", "codespace", "container", "ssh", "workdir", "pr"}
    if kind not in valid_kinds:
        msg = (f"claims add: unknown kind {kind!r} "
               f"(expected one of {', '.join(sorted(valid_kinds))})")
        if args.json:
            return _json_error(msg, 2)
        output.err(msg)
        return 2
    config = cfg.load_config()
    owner_ref = getattr(args, "claim_owner_ref", None)
    if owner_ref:
        rec_path, wt_id, err = _resolve_owner_ref_record_path(owner_ref, config)
        if err:
            if args.json:
                return _json_error(err, 2)
            output.err(err)
            return 2
        if rec_path is None:
            # Cross-machine owner -- its ledger is remote; the lease mirror owns
            # the disposition. Not an error: a no-op locally, surfaced for the
            # caller (agent-codespaces) to mirror via the lease --disposition.
            msg = (f"owner-ref {owner_ref} is on another machine -- claim "
                   f"deferred to the lease mirror (no local ledger write)")
            if args.json:
                _json_output({"worktree_id": wt_id, "kind": kind, "ref": ref,
                              "deferred": True, "reason": "cross-machine-owner"})
                return 0
            output.warn(msg)
            return 0
    else:
        wt_id = _infer_worktree_id(getattr(args, "release_worktree", None), config)
        rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return _json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    # Foreground verb (#4547): claim add is a critical RMW held under the
    # blocking record lock (no I/O in the window).
    with tracking._RecordLock(rec_path):
        rec = tracking.load_record(rec_path)
        claim = tracking.ResourceClaim(
            kind=kind, ref=ref, created_at=tracking._now_iso(),
            state=obligations.ACTIVE, note=getattr(args, "note", "") or "",
        )
        tracking.add_resource_claim(rec, claim, save=False)
        tracking.save_record(rec, rec_path)
    if args.json:
        _json_output({"worktree_id": wt_id, "kind": kind, "ref": ref,
                      "state": obligations.ACTIVE})
        return 0
    print(f"added outbound claim {kind}:{ref} on {wt_id}")
    return 0


def _claims_release(args: argparse.Namespace, ref: str) -> int:
    """Retire a single outbound resource claim by ref from a worktree's record.

    Default marks the matching claim ``released`` (kept for history; excluded
    from the live ledger and from reap-safety's live-claimant check);
    ``--remove`` drops the entry entirely. The owner worktree is the current one
    unless ``--worktree`` names another.
    """
    config = cfg.load_config()
    wt_id = _infer_worktree_id(getattr(args, "release_worktree", None), config)
    rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return _json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    # Foreground verb (#4547): claim release is a critical RMW held under the
    # blocking record lock (no I/O in the window).
    with tracking._RecordLock(rec_path):
        rec = tracking.load_record(rec_path)
        match = next((c for c in rec.resources if c.ref == ref), None)
        if match is None:
            if args.json:
                return _json_error(f"no outbound claim with ref: {ref}")
            output.err(f"no outbound claim with ref: {ref} on {wt_id}")
            return 1
        remove = getattr(args, "remove", False)
        if remove:
            rec.resources = [c for c in rec.resources if c.ref != ref]
            action = "removed"
        else:
            match.state = "released"
            action = "released"
        tracking.save_record(rec, rec_path)
    if args.json:
        _json_output({"worktree_id": wt_id, "ref": ref, "action": action})
        return 0
    print(f"{action} outbound claim {ref} on {wt_id}")
    return 0


def _claims_settle(args: argparse.Namespace, ref: str) -> int:
    """Settle one outbound resource claim's disposition by ref (Phase 3).

    Flips the matching claim to ``at-rest`` (default -- the resource's work is
    safe but the claim is still held) or ``released`` with ``--released``. This
    is the operator/hook entry point to the incremental-settlement primitive
    (`tracking.settle_resource_claim`) that lets a worktree's finalize gate stop
    treating the resource as unsettled. The owner worktree is the current one
    unless ``--worktree`` names another in the current project, or
    ``--owner-ref <machine/project/worktree_id>`` names one cross-project
    (resolved by qualified ref -> ``project_dir/worktrees``, same-machine; a
    cross-machine owner-ref defers to the lease mirror). The owner-ref path is
    for a hook whose cwd is not the owning worktree (e.g. agent-codespaces
    settling a CodeSpace claim on disconnect from the daemon's cwd).
    """
    config = cfg.load_config()
    owner_ref = getattr(args, "claim_owner_ref", None)
    if owner_ref:
        rec_path, wt_id, err = _resolve_owner_ref_record_path(owner_ref, config)
        if err:
            if args.json:
                return _json_error(err, 2)
            output.err(err)
            return 2
        if rec_path is None:
            # Cross-machine owner -- the lease disposition mirror owns it; a
            # no-op locally (surfaced for the caller to mirror via the lease).
            if args.json:
                _json_output({"worktree_id": wt_id, "ref": ref,
                              "deferred": True, "reason": "cross-machine-owner"})
                return 0
            output.warn(
                f"owner-ref {owner_ref} is on another machine -- settle deferred "
                f"to the lease mirror (no local ledger write)")
            return 0
    else:
        wt_id = _infer_worktree_id(getattr(args, "release_worktree", None), config)
        rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return _json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    rec = tracking.load_record(rec_path)
    disposition = (
        obligations.RELEASED if getattr(args, "released", False) else obligations.AT_REST
    )
    settled = tracking.settle_resource_claim(rec, ref, disposition, path=rec_path)
    if settled is None:
        if args.json:
            return _json_error(f"no outbound claim with ref: {ref}")
        output.err(f"no outbound claim with ref: {ref} on {wt_id}")
        return 1
    if args.json:
        _json_output({"worktree_id": wt_id, "ref": ref, "disposition": disposition})
        return 0
    print(f"settled outbound claim {ref} on {wt_id} -> {disposition}")
    return 0


def _claims_sweep(args: argparse.Namespace) -> int:
    """Never-wedge reclaim sweep over local ledgers (resource-obligation Ph4).

    Scans every local worktree record and, for each **active** ``worktree``-kind
    obligation whose child is **provably gone AND provably safe**, flips it to
    ``abandoned`` so a crashed/missed settlement can never freeze the owner's
    finalize forever. The per-claim verdict is conservative and same-machine:

    * **gone** -- the child's local record is absent, or its status is terminal
      (``finalized``/``orphaned``); an active child (a live holder may still be
      working) is *not* gone. A cross-machine child is not judgeable here
      (deferred to the lease mirror) and is left alone.
    * **safe** -- a **finalized** child is provably safe (its finalize verified
      content upstream). An ``orphaned`` child (push failed) is *unsafe*. A gone,
      **non-terminal** child (``active``/``pushed`` but its dir removed -- the
      crashed-holder case) is proven safe only when its branch content already
      landed on its project upstream (:func:`_child_branch_merged`); otherwise it
      is *unproven* and left intact.

    So the sweep reclaims the "child finalized but the parent's claim was never
    flipped" gap (a missed/failed ``_settle_parent_obligation``) **and** the
    crashed-but-merged holder, never abandoning on a guess. **Dry-run by
    default**; ``--apply`` writes. (Process-liveness of a still-present active
    holder + cross-machine reclaim are future work; today a live-dir or
    cross-machine claim is spared.)
    """
    config = cfg.load_config()
    apply = getattr(args, "apply", False)
    from . import sweep as sweep_mod
    gone_of, safe_of = sweep_mod.make_resolvers(config)

    reclaimed: list[dict[str, str]] = []
    tdir = cfg.tracking_dir()
    for rec in tracking.list_records(tdir):
        # Compute candidates without saving; apply-mode persists per record.
        before = {c.ref: c.state for c in rec.resources}
        flipped = tracking.sweep_abandoned_obligations(
            rec, gone_of=gone_of, safe_of=safe_of, save=apply,
        )
        for c in flipped:
            reclaimed.append({"owner": rec.worktree_id, "kind": c.kind,
                              "ref": c.ref})
        if flipped and not apply:
            # dry-run: restore in-memory state we mutated (no save happened)
            for c in rec.resources:
                if c.ref in before:
                    c.state = before[c.ref]

    if args.json:
        _json_output({"applied": apply, "reclaimed": reclaimed,
                      "count": len(reclaimed)})
        return 0
    if not reclaimed:
        print("claims sweep: no abandonable obligations found.")
        return 0
    verb = "Abandoned" if apply else "Would abandon (dry-run; pass --apply)"
    print(f"{verb} {len(reclaimed)} obligation(s):")
    for r in reclaimed:
        print(f"  · {r['owner']}: {r['kind']} {r['ref']}")
    return 0


def _claims_cleanup(args: argparse.Namespace) -> int:
    """Reclaim re-homed (abandoned) obligations from the durable orphanage.

    The **acting** consumer for the registry ``claims orphans`` only lists
    (resource-obligation-settlement, dotfiles#1161): for each re-homed
    obligation it disposes of the resource the obligation named (deletes an
    orphaned CodeSpace, ...) and drops the settled entry. **Dry-run by default**;
    pass ``--apply`` to act. Same-machine only (a cross-machine entry is
    surfaced for cleanup on its own box); best-effort -- a failed reclaim leaves
    the entry for a retry, so nothing is ever lost.
    """
    config = cfg.load_config()
    apply = getattr(args, "apply", False)
    from . import cleanup as cleanup_mod
    rows = cleanup_mod.cleanup_orphanage(config, apply=apply)

    reclaimed = [r for r in rows if r["status"] == "reclaimed"]
    if args.json:
        _json_output({"applied": apply, "results": rows,
                      "reclaimed": len(reclaimed), "count": len(rows)})
        return 0
    if not rows:
        print("claims cleanup: no re-homed obligations to reclaim "
              "(the orphanage is empty).")
        return 0
    verb = "Reclaimed" if apply else "Would reclaim (dry-run; pass --apply)"
    print(f"claims cleanup -- {len(rows)} orphaned obligation(s):")
    for r in rows:
        mark = {"reclaimed": "\u2713", "failed": "\u2717",
                "skipped": "\u2013", "unsupported": "?"}.get(r["status"], "?")
        line = f"  {mark} {r['kind']}: {r['ref']}  [{r['status']}]"
        if r["detail"]:
            line += f" -- {r['detail']}"
        print(line)
    if reclaimed:
        print(f"{verb}: {len(reclaimed)} of {len(rows)}.")
    return 0


def _claims_orphans(args: argparse.Namespace) -> int:
    """List the durable orphanage -- obligations re-homed by an ``--abandon``
    finalize (resource-obligation-settlement, dotfiles#1161).

    These name resources (a CodeSpace, a cross-repo worktree, a bridge session)
    whose owning worktree was abandoned rather than settled, recorded here so
    they are not silently dropped -- a cleanup/adoption pass reads this registry.
    """
    orphans = tracking.load_orphaned_obligations()
    if args.json:
        _json_output({"orphaned": orphans, "count": len(orphans)})
        return 0
    if not orphans:
        print("claims orphans: no re-homed obligations "
              "(nothing has been --abandon'd, or the registry is empty).")
        return 0
    print(f"Re-homed (abandoned) obligations -- {len(orphans)} pending cleanup:")
    for e in orphans:
        line = f"  · {e.get('kind')}: {e.get('ref')}"
        src = e.get("source_worktree")
        when = e.get("abandoned_at")
        meta = ", ".join(x for x in (f"from {src}" if src else "",
                                     f"@ {when}" if when else "") if x)
        if meta:
            line += f"  ({meta})"
        if e.get("note"):
            line += f" -- {e['note']}"
        print(line)
    return 0


def _claims_show(args: argparse.Namespace, worktree_id: str | None) -> int:
    """Render a worktree's full claim ledger (agent-fabric resource-claims).

    Outbound (authoritative, from this worktree's own record):
      * ``owner_ref`` -- this worktree is itself owned as a resource by another.
      * ``resources`` -- the outbound resources this worktree produced and owns.
    Inbound (best-effort, via agent-dispatch when installed): the tasks this
    worktree claims (assigned + owned).

    The worktree is the current one (inferred from CWD) unless an id is given.
    """
    config = cfg.load_config()
    wt_id = _infer_worktree_id(worktree_id, config)
    rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not rec_path.exists():
        if args.json:
            return _json_error(f"worktree not found: {wt_id}")
        output.err(f"worktree not found: {wt_id}")
        return 1
    rec = tracking.load_record(rec_path)

    outbound = [
        {
            "kind": c.kind,
            "ref": c.ref,
            "state": c.state,
            "created_at": c.created_at,
            **({"note": c.note} if c.note else {}),
        }
        for c in rec.resources
    ]
    inbound = _inbound_claims(rec.machine or config.machine, wt_id,
                              rec.worktree_path)

    ledger = {
        "worktree_id": wt_id,
        "repo": rec.repo,
        "machine": rec.machine,
        "owner_ref": rec.owner_ref,
        "outbound": outbound,
        "inbound": inbound,
    }

    if args.json:
        _json_output(ledger)
        return 0

    # Human-facing ledger.
    print(f"Claim ledger for {wt_id}  ({rec.repo} @ {rec.machine})")
    if rec.owner_ref:
        print(f"  owned as a resource by: {rec.owner_ref}")
    print("  Outbound (resources this worktree owns):")
    if outbound:
        for c in outbound:
            state = "" if c["state"] == "active" else f" [{c['state']}]"
            note = f"  -- {c['note']}" if c.get("note") else ""
            print(f"    - {c['kind']}: {c['ref']}{state}{note}")
    else:
        print("    (none)")
    print("  Inbound (tasks this worktree claims):")
    if not inbound.get("available"):
        print(f"    (unavailable: {inbound.get('reason', 'n/a')})")
    else:
        rows = [("assigned", t) for t in inbound.get("assigned", [])] + \
               [("owned", t) for t in inbound.get("owned", [])]
        if rows:
            for kind, t in rows:
                tid = t.get("id", "?") if isinstance(t, dict) else str(t)
                title = t.get("title", "") if isinstance(t, dict) else ""
                st = t.get("status", "") if isinstance(t, dict) else ""
                print(f"    - [{kind}] {tid} {st}  {title}".rstrip())
        else:
            print("    (none)")
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
    no_owner = getattr(args, "no_owner", False)
    # Owner resolution (Ph6): an explicit --owner-ref wins; else the ambient
    # AGENT_WORKTREES_OWNER_REF (exported at the parent worktree's launch, or by a
    # `run` wrapper); else infer the enclosing worktree from the CWD -- so a plain
    # nested `create` from inside a worktree is auto-parented, not orphaned.
    # --no-owner forces a deliberately top-level worktree; a system worktree is
    # never an outbound resource of another worktree.
    owner_ref = (
        None if (is_system or no_owner)
        else (getattr(args, "owner_ref", None)
              or os.environ.get("AGENT_WORKTREES_OWNER_REF")
              or _resolve_owner_ref() or None)
    )
    with output.stdout_to_stderr():
        try:
            config = cfg.load_config()
            result = _create_worktree_core(
                config, no_mux=True,
                kind="system" if is_system else "session",
                owner=(getattr(args, "owner", None) or getattr(args, "name", None))
                if is_system else None,
                name=getattr(args, "name", None) if is_system else None,
                interface=getattr(args, "interface", None),
                origin=getattr(args, "origin", None),
                owner_ref=owner_ref,
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
# run -- execute an inner subcommand and journal the resource it produces as an
# outbound claim on THIS (the calling) worktree (agent-fabric resource-claims)
# ═══════════════════════════════════════════════════════════════════════════

def _resolve_owner_ref() -> str | None:
    """Build this worktree's qualified owner ref from the current context.

    Resolves the calling worktree id from the CWD (git-identity first), then
    qualifies it with machine + active project (+ session) so a claim stamped
    on a resource resolves back here across repos/machines. Returns None when
    the caller is not inside a managed worktree.
    """
    try:
        config = cfg.load_config()
    except Exception:
        return None
    caller_id = _infer_worktree_id_from_cwd(config)
    if not caller_id:
        return None
    try:
        project = cfg.project_name()
    except Exception:
        project = config.repo_name
    session = os.environ.get("COPILOT_AGENT_SESSION_ID") or None
    return tracking.format_claim_ref(config.machine, project, caller_id, session)


def _claim_from_run_output(stdout: str) -> tracking.ResourceClaim | None:
    """Recognize the resource an inner command produced from its JSON output.

    Currently understands the ``create`` envelope (``{"worktree": {...}}``) and
    the bare worktree dict. Unknown shapes yield None (no forward claim; the
    backward link via the injected env still applies). New resource kinds add a
    recognizer here without a schema change.
    """
    try:
        data = json.loads(stdout)
    except (ValueError, TypeError):
        return None
    if not isinstance(data, dict):
        return None
    wt = data.get("worktree")
    if not isinstance(wt, dict):
        # Tolerate a bare worktree dict (``{"id": ..., "machine": ...}``).
        wt = data if data.get("id") and data.get("machine") else None
    if not isinstance(wt, dict):
        return None
    wid = wt.get("id")
    machine = wt.get("machine")
    project = wt.get("repo")
    if not wid or not machine:
        return None
    ref = tracking.format_claim_ref(str(machine), str(project) if project else None,
                                    str(wid))
    return tracking.ResourceClaim(
        kind="worktree",
        ref=ref,
        created_at=tracking._now_iso(),
        state="active",
    )


def _journal_run_claim(owner_ref: str, stdout: str) -> tracking.ResourceClaim | None:
    """Append the produced-resource claim to the caller's tracking record.

    The caller (owner) record lives in the active project's tracking dir --
    which is the caller's project, since ``main`` resolved context from the
    caller's CWD before ``run`` executed the (possibly cross-repo) child.
    """
    claim = _claim_from_run_output(stdout)
    if claim is None:
        return None
    parsed = tracking.parse_claim_ref(owner_ref)
    if parsed is None:
        return None
    rec_path = cfg.tracking_dir() / f"{parsed.worktree_id}.yaml"
    if not rec_path.exists():
        return None
    record = tracking.load_record(rec_path)
    tracking.add_resource_claim(record, claim, save=False)
    tracking.save_record(record, rec_path)
    return claim


def cmd_run(args: argparse.Namespace) -> int:
    """Run an inner subcommand and journal the resource it produces as an
    outbound claim on THIS worktree.

    Run it from the OWNER worktree: it resolves this worktree's identity from
    its own context, executes the (possibly cross-repo) inner subcommand with
    ``AGENT_WORKTREES_OWNER_REF`` injected -- so a resource-creating command
    stamps the *backward* owner link -- then parses the child's JSON output to
    journal the *forward* ``ResourceClaim`` here. The child's stdout is passed
    through verbatim and its exit code is propagated, so ``run`` is transparent.
    """
    raw = list(getattr(args, "inner_command", None) or [])
    if not raw:
        output.err('run: no subcommand given. Usage: run "<subcommand ...>"')
        return 2
    # Accept either a single quoted string or trailing tokens.
    cmd_str = raw[0] if len(raw) == 1 else " ".join(raw)

    owner_ref = getattr(args, "owner_ref", None) or _resolve_owner_ref()
    if not owner_ref:
        output.err(
            "run: could not resolve the calling worktree from the current "
            "directory -- running the inner command WITHOUT journaling a claim. "
            "Run from inside a managed worktree, or pass --owner-ref.")

    child_env = dict(os.environ)
    if owner_ref:
        child_env["AGENT_WORKTREES_OWNER_REF"] = owner_ref

    # Capture stdout (to parse the produced resource) while stderr streams
    # through; re-emit stdout verbatim so callers/pipes see the child output.
    try:
        proc = subprocess.run(
            cmd_str, shell=True, env=child_env,
            stdout=subprocess.PIPE, text=True,
        )
    except Exception as e:
        output.err(f"run: failed to execute inner command: {e}")
        return 1
    child_stdout = proc.stdout or ""
    sys.stdout.write(child_stdout)
    sys.stdout.flush()

    if owner_ref and proc.returncode == 0 and child_stdout.strip():
        try:
            claim = _journal_run_claim(owner_ref, child_stdout)
            if claim is not None:
                output.err(f"run: journaled outbound claim {claim.ref} "
                           f"({claim.kind}) on {owner_ref}")
        except Exception as e:
            output.err(f"run: could not journal claim: {e}")

    return proc.returncode


def cmd_claimant_liveness(args: argparse.Namespace) -> int:
    """Report SAME-MACHINE claimant liveness for an owner_ref (resource-claims).

    The SSH endpoint that :func:`claimant.resolve_claimant_alive` invokes on an
    owner's machine: it resolves the owner locally there and emits a tri-state
    ``alive`` (``true`` alive / ``false`` gone / ``null`` unknown). Uses the
    local-only resolver -- never a further remote hop -- so it always terminates.
    """
    alive = claimant_mod.local_claimant_alive(args.owner_ref)
    if getattr(args, "json", False):
        _json_output({"owner_ref": args.owner_ref, "alive": alive})
        return 0
    label = {True: "alive", False: "gone", None: "unknown"}[alive]
    print(f"{args.owner_ref}: {label}")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
# cleanup
# ═══════════════════════════════════════════════════════════════════════════

def _local_claimant_alive(owner_ref: str) -> bool | None:
    """Same-machine claimant-liveness probe (thin alias, resource-claims).

    Delegates to :func:`claimant.local_claimant_alive`. Kept as a module-level
    name for the fast, no-SSH display paths (list bucket, cleanup print line).
    The reap *decision* uses the remote-capable
    :func:`claimant.resolve_claimant_alive` instead.
    """
    return claimant_mod.local_claimant_alive(owner_ref)


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
        # Best-effort reconcile write (#4547): a status-render side effect.
        # Reconcile unlocked (provider I/O), then re-apply the deltas onto a
        # fresh snapshot under a best-effort lock so a concurrent foreground
        # verb is never clobbered; skip on contention (self-heals next pass).
        prune.reconcile_and_persist_best_effort(rec, lookup)

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
                claimant_alive=claimant_mod.resolve_claimant_alive,
                paired_sibling_final=prune.default_paired_sibling_final,
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
            # #4057: the wt-<id> mux is confirmably gone now (a successful,
            # idle-gated kill), so clear the cached liveness -- the "inactive at
            # reap/post-exit" write-point. This reaper is the shared sweep run at
            # BOTH lifecycle boundaries (session-end via _sweep_orphans_on_exit
            # and picker-launch), so it also covers post-exit transitively. A
            # value change, so it always persists (no throttle needed).
            tracking.stamp_mux_live(wt_id, False, sync=True)
            try:
                activity.log_event(
                    "mux_session_reaped", worktree_id=wt_id, reason=reason)
            except Exception:
                pass
        else:
            errors.append({"id": wt_id, "reason": f"kill failed ({reason})"})

    return {"available": True, "reaped": reaped,
            "skipped": skipped, "errors": errors}


# ═══════════════════════════════════════════════════════════════════════════
# Orphaned launcher-shell reaper (copilot-extensions #102)
# ═══════════════════════════════════════════════════════════════════════════
# After a worktree session ends cleanly its launcher shells (the pwsh running
# launch-session.ps1 and the `python -m agent_worktrees` waiter) exit with it.
# But a *force-closed* terminal (window closed with the X, a dropped SSH pipe)
# can strand them: the console dies, the shells are re-parented away from a now-
# dead pid, nothing runs under them -- yet they pin memory indefinitely. This
# sweep reclaims those, closing the same intent as the mux reaper
# (visions/agent-fabric §Features/reclaim-idle-process).
#
# SAFETY -- this KILLS processes, so it is engineered to fail SAFE. A live
# telemetry sampler was once wrongly killed because a non-elevated query made a
# hidden scheduled-task service (blank command line, exited parent) look exactly
# like an orphan. The lesson is baked in as independent layers, EVERY one of
# which must pass before a pid is even a candidate:
#   1. POSITIVE signature only. A pid is a candidate ONLY if its command line
#      positively matches an agent-worktrees launcher marker. A service with a
#      blank/absent command line can NEVER match -- we never reap "things that
#      merely look orphaned".
#   2. Service/daemon veto. A session-0 (service) pid, or one whose command line
#      bears a daemon/service/ACP marker, is skipped even if it matched (1).
#   3. Liveness gate. A shell with a live descendant (copilot/node, or a mux
#      client) is a LIVE session and is always spared.
#   4. Self-preservation. The reaper never touches its own process tree.
#   5. Orphan + idle gates. Only a shell whose parent has exited AND that has
#      been alive past the grace window is eligible.
#   6. Dry-run by DEFAULT. Unlike the mux reaper, nothing is killed unless the
#      caller explicitly passes --yes; the default is a report.

REAP_SHELL_GRACE_SECS = 3600  # 1h: an orphaned launcher shell must be this old

# Process image names this reaper is willing to consider (lowercased).
_LAUNCHER_SHELL_NAMES = frozenset({
    "pwsh.exe", "powershell.exe", "python.exe",
    "pwsh", "powershell", "python", "python3",
})
# Command-line substrings that POSITIVELY identify an agent-worktrees launcher
# shell (lowercased match). Nothing is EVER reaped without one of these.
_LAUNCHER_SIGNATURES = ("launch-session", "-m agent_worktrees",
                        "agent_worktrees.__main__")
# Command-line substrings that VETO a reap even when a launcher signature is
# present -- services/daemons, ACP/stdio sessions, and the reaper's own verbs.
_LAUNCHER_REAP_VETOES = (
    "serve-service", "agent_dispatch", "agent-dispatch", "telemetry",
    "status-updater", "vault", "--acp", "--stdio",
    "reap-shells", "reap_shells", "reap-sessions",
)
# Descendant image names that mark a LIVE session under a launcher shell ->
# spare it. Deliberately broad: over-sparing is safe, over-reaping is not.
_LIVE_DESCENDANT_NAMES = ("copilot", "node", "tmux", "psmux")
# Concrete process images the enumerators must snapshot **in addition to**
# _LAUNCHER_SHELL_NAMES, purely so the live-descendant veto above can see them.
# They are never reap candidates (the candidate loop gates on
# _LAUNCHER_SHELL_NAMES); they exist only to make the parent/child table
# complete. Without them the veto is dead code: a launcher whose foreground
# child is `psmux attach-session` looked childless, so an attached, working
# session was reaped out from under its terminal -- killing the launcher shell
# while its mux client kept rendering, leaving the pane painted but the console
# handed back to the parent shell.
_LIVE_DESCENDANT_IMAGES = frozenset({
    "copilot.exe", "node.exe", "tmux.exe", "psmux.exe",
    "copilot", "node", "tmux", "psmux",
})


def select_orphan_launcher_shells(
    procs: list[dict], *, now: float, idle_grace_secs: float, self_pid: int,
    pid_alive: Callable[[int], bool] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Pure predicate: partition launcher shells into (reap, skipped).

    ``procs`` is a list of process dicts with keys ``pid``, ``ppid``, ``name``,
    ``cmdline``, ``create_epoch`` (float|None), ``session_id`` (int). Pure and
    deterministic -- no process I/O of its own -- so the full safety predicate is
    unit testable. ``skipped`` entries carry a ``reason`` for legibility.

    ``pid_alive`` is the **injected** parent-liveness probe. ``procs`` is a
    filtered snapshot (launcher shells plus live-descendant witnesses), so
    membership in it cannot answer "is this pid alive?": a launcher started from
    ``cmd.exe``/``bash``/Windows Terminal has a parent that was never enumerated
    and so looks parentless, i.e. an orphan. Callers with a real process table
    pass a probe (``locks.pid_alive``); when it is ``None`` the check degrades to
    the snapshot-membership test, which keeps this function pure for tests.
    """
    by_pid = {int(p["pid"]): p for p in procs if p.get("pid") is not None}
    children: dict[int, list[int]] = {}
    for p in procs:
        children.setdefault(int(p.get("ppid", -1) or -1), []).append(int(p["pid"]))

    def _descendants(pid: int) -> set[int]:
        out: set[int] = set()
        stack = list(children.get(pid, []))
        while stack:
            c = stack.pop()
            if c in out or c == pid:
                continue
            out.add(c)
            stack.extend(children.get(c, []))
        return out

    def _ancestors(pid: int) -> set[int]:
        out: set[int] = set()
        cur, guard = pid, 0
        while cur in by_pid and guard < 128:
            pp = int(by_pid[cur].get("ppid", -1) or -1)
            if pp in out or pp <= 0:
                break
            out.add(pp)
            cur = pp
            guard += 1
        return out

    self_tree = {self_pid} | _descendants(self_pid) | _ancestors(self_pid)

    reap: list[dict] = []
    skipped: list[dict] = []
    for p in procs:
        pid = int(p["pid"])
        name = (p.get("name") or "").lower()
        cmd = (p.get("cmdline") or "").lower()
        if name not in _LAUNCHER_SHELL_NAMES:
            continue  # not a shell we manage -- ignored silently, never listed
        if not any(sig in cmd for sig in _LAUNCHER_SIGNATURES):
            continue  # (1) no positive launcher signature -> never a candidate
        if pid in self_tree:
            skipped.append({"pid": pid, "reason": "self"})
            continue
        sid = p.get("session_id", -1)
        if int(sid if sid is not None else -1) == 0:
            skipped.append({"pid": pid, "reason": "service-session"})  # (2)
            continue
        if any(v in cmd for v in _LAUNCHER_REAP_VETOES):
            skipped.append({"pid": pid, "reason": "service-marker"})  # (2)
            continue
        live = False
        for d in _descendants(pid):
            dn = (by_pid.get(d, {}).get("name") or "").lower()
            if any(m in dn for m in _LIVE_DESCENDANT_NAMES):
                live = True
                break
        if live:
            skipped.append({"pid": pid, "reason": "live-descendant"})  # (3)
            continue
        ppid = int(p.get("ppid", -1) or -1)
        parent_alive = (ppid in by_pid if pid_alive is None
                        else (ppid > 0 and bool(pid_alive(ppid))))
        if ppid > 0 and parent_alive:
            skipped.append({"pid": pid, "reason": "parent-alive"})  # (5)
            continue
        ce = p.get("create_epoch")
        if ce is None:
            skipped.append({"pid": pid, "reason": "age-unknown"})
            continue
        if now - float(ce) < idle_grace_secs:
            skipped.append({"pid": pid, "reason": "fresh"})  # (5)
            continue
        reap.append(p)
    return reap, skipped


def _enumerate_launcher_shells() -> list[dict] | None:
    """Snapshot launcher shells **plus live-session witness processes**.

    Returns a list of ``{pid, ppid, name, cmdline, create_epoch, session_id}``
    dicts, or ``None`` if enumeration is unavailable. Best-effort and never
    raises. The witness images (``_LIVE_DESCENDANT_IMAGES``: psmux/tmux/copilot/
    node) are included so :func:`select_orphan_launcher_shells` can see a live
    child; they are never reap candidates themselves.
    """
    if platform.system() == "Windows":
        return _enumerate_launcher_shells_windows()
    return _enumerate_launcher_shells_posix()


def _enumerate_launcher_shells_windows() -> list[dict] | None:
    names = sorted(n for n in (_LAUNCHER_SHELL_NAMES | _LIVE_DESCENDANT_IMAGES)
                   if n.endswith(".exe"))
    where = " OR ".join(f"Name='{n}'" for n in names)
    ps = (
        "Get-CimInstance Win32_Process -Filter "
        f"\"{where}\" | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine,SessionId,"
        "@{n='Create';e={try{([DateTimeOffset]$_.CreationDate)"
        ".ToUnixTimeSeconds()}catch{$null}}} | ConvertTo-Json -Compress -Depth 3"
    )
    try:
        out = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    raw = (out.stdout or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if isinstance(data, dict):
        data = [data]
    procs: list[dict] = []
    for d in data:
        try:
            procs.append({
                "pid": int(d.get("ProcessId")),
                "ppid": int(d.get("ParentProcessId") or -1),
                "name": (d.get("Name") or "").lower(),
                "cmdline": d.get("CommandLine") or "",
                "create_epoch": (float(d["Create"]) if d.get("Create") is not None
                                 else None),
                "session_id": int(d.get("SessionId") or -1),
            })
        except (TypeError, ValueError):
            continue
    return procs


def _enumerate_launcher_shells_posix() -> list[dict] | None:
    proc = Path("/proc")
    try:
        entries = [e for e in proc.iterdir() if e.name.isdigit()]
    except OSError:
        return None
    try:
        clk = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError):
        clk = 100
    boot = _proc_boot_time()
    procs: list[dict] = []
    for entry in entries:
        pid = int(entry.name)
        try:
            comm = (entry / "comm").read_text(errors="ignore").strip().lower()
        except OSError:
            continue
        if comm not in _LAUNCHER_SHELL_NAMES and comm not in _LIVE_DESCENDANT_IMAGES:
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(
                errors="ignore").strip()
        except OSError:
            cmdline = ""
        ppid, sid, start_ticks = -1, -1, None
        try:
            stat = (entry / "stat").read_text(errors="ignore")
            rparen = stat.rfind(")")
            rest = stat[rparen + 1:].split()
            # After comm: state(0) ppid(1) pgrp(2) session(3) ... starttime(19).
            ppid = int(rest[1])
            sid = int(rest[3])
            start_ticks = int(rest[19])
        except (OSError, IndexError, ValueError):
            pass
        create_epoch = (boot + (start_ticks / clk)
                        if (boot and start_ticks is not None) else None)
        procs.append({
            "pid": pid, "ppid": ppid, "name": comm, "cmdline": cmdline,
            "create_epoch": create_epoch, "session_id": sid,
        })
    return procs


def _proc_boot_time() -> float | None:
    try:
        for line in Path("/proc/stat").read_text(errors="ignore").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    return None


def reap_orphan_launcher_shells(
    *, dry_run: bool = True, idle_grace_secs: float = REAP_SHELL_GRACE_SECS,
    now: float | None = None, processes: list[dict] | None = None,
) -> dict:
    """Reap orphaned agent-worktrees launcher shells (pwsh/python).

    Conservative and dry-run by default: only shells that pass every safety
    layer in :func:`select_orphan_launcher_shells` are candidates, and none are
    killed unless ``dry_run=False``. ``processes`` may be injected for testing.

    Parent liveness is probed against the **real** process table
    (:func:`locks.pid_alive`) whenever this function did the enumeration itself,
    so a launcher whose terminal is a non-enumerated image (``cmd.exe``,
    ``bash``, Windows Terminal) is correctly seen as parented rather than
    orphaned. Injected ``processes`` keep the snapshot-membership fallback, so
    tests stay hermetic and deterministic.

    Returns::

        {"available": bool,                # False when enumeration is impossible
         "reaped":     [pid, ...],         # killed (or would-be, in dry-run)
         "candidates": [{"pid","cmdline"}] # the reap set, for report/preview
         "skipped":    [{"pid","reason"}],
         "errors":     [{"pid","reason"}]}
    """
    now = time.time() if now is None else now
    injected = processes is not None
    proc_list = processes if injected else _enumerate_launcher_shells()
    if proc_list is None:
        return {"available": False, "reaped": [], "candidates": [],
                "skipped": [], "errors": []}
    reap, skipped = select_orphan_launcher_shells(
        proc_list, now=now, idle_grace_secs=idle_grace_secs, self_pid=os.getpid(),
        pid_alive=None if injected else locks.pid_alive)
    reaped: list[int] = []
    errors: list[dict] = []
    for p in reap:
        pid = int(p["pid"])
        if dry_run:
            reaped.append(pid)
            continue
        if procs.terminate_pid(pid):
            reaped.append(pid)
            try:
                activity.log_event("launcher_shell_reaped", pid=pid,
                                   cmdline=(p.get("cmdline") or "")[:200])
            except Exception:
                pass
        else:
            errors.append({"pid": pid, "reason": "kill failed"})
    candidates = [{"pid": int(p["pid"]), "cmdline": p.get("cmdline") or ""}
                  for p in reap]
    return {"available": True, "reaped": reaped, "candidates": candidates,
            "skipped": skipped, "errors": errors}


def cmd_reap_shells(args: argparse.Namespace) -> int:
    """``reap-shells`` -- reap orphaned launcher shells (copilot-extensions #102).

    Reports candidates by default; requires ``--yes`` to actually terminate.
    """
    grace_hours = getattr(args, "grace_hours", None)
    kwargs: dict = {"dry_run": not getattr(args, "yes", False)}
    if grace_hours is not None:
        kwargs["idle_grace_secs"] = float(grace_hours) * 3600
    payload = reap_orphan_launcher_shells(**kwargs)
    if getattr(args, "json", False):
        _json_output(payload)
        return 0
    if not payload["available"]:
        print("Process enumeration unavailable -- nothing to reap.")
        return 0
    dry = not getattr(args, "yes", False)
    verb = "Would reap" if dry else "Reaped"
    ids = payload["reaped"]
    print(f"{verb} {len(ids)} orphaned launcher shell(s)"
          + (":" if ids else "."))
    for c in payload["candidates"]:
        print(f"  pid {c['pid']}: {c['cmdline'][:100]}")
    for e in payload["errors"]:
        print(f"  ! pid {e['pid']}: {e['reason']}")
    if dry and ids:
        print("Re-run with --yes to terminate the shells above.")
    return 0


def _remove_managed_worktree(rec, repo, tracking_path: Path) -> list[str]:
    """Tear down one managed (system/bridge) worktree: mux, git worktree,
    branch, tracking record. Mirrors ``cmd_remove_system``'s removal steps.
    Returns any non-fatal warnings."""
    warns: list[str] = []
    try:
        sessions.kill_tmux_session(rec.worktree_id)  # normally none for a reap
    except Exception:
        pass
    if rec.worktree_path and Path(rec.worktree_path).exists():
        try:
            git_ops.remove_worktree(repo.anchor, rec.worktree_path)
        except Exception as exc:
            warns.append(f"worktree remove failed: {exc}")
    if rec.branch:
        git_ops.git("branch", "-D", rec.branch, cwd=repo.anchor, check=False)
    yaml_path = tracking_path / f"{rec.worktree_id}.yaml"
    try:
        yaml_path.unlink()
    except OSError:
        pass
    return warns


def sweep_managed_worktrees(*, dry_run: bool = False,
                            min_idle_secs: float | None = None,
                            now: float | None = None) -> dict:
    """GC leaked **system/bridge** worktrees (the daemon-owned kinds routine
    cleanup skips -- issue #1069).

    Reaps only the *provably dead* ones -- **FINAL or UNUSED, no active process
    (mux/session/attached), no follow-up flag, idle past the grace window** --
    via :func:`gc.classify_managed_worktree`. A dirty/WIP tree, a live session,
    an attached client, a follow-up mark, or a still-fresh worktree is spared.
    Returns ``{removed: [{id, reason}], skipped: [{id, reason}]}``.
    """
    from . import gc as gc_mod

    if min_idle_secs is None:
        min_idle_secs = gc_mod.MANAGED_GC_GRACE_SECS
    now = time.time() if now is None else now

    config = cfg.load_config()
    repo = config.default_repo
    tracking_path = cfg.tracking_dir()
    records = tracking.list_records(tracking_path)
    managed = [r for r in records if r.kind in tracking.MANAGED_KINDS]

    result: dict = {"removed": [], "skipped": []}
    if not managed:
        return result

    mux = sessions._list_mux_sessions() or {}
    activity_by_name = sessions._mux_session_activity()
    session_ctx = sessions.scan_sessions_fast(managed)
    active_paths = _build_active_paths(managed, session_ctx)

    for rec in managed:
        name = f"wt-{rec.worktree_id}"
        has_live_mux = name in mux
        attached = bool(mux.get(name))
        norm = _normalize_path(rec.worktree_path) if rec.worktree_path else ""
        has_live_session = norm in session_ctx.active_sessions

        if rec.worktree_path and Path(rec.worktree_path).exists():
            info = git_ops.classify_worktree(
                rec.worktree_path, rec.branch, fetch=False,
                remote=repo.remote, default_branch=repo.default_branch,
                active_paths=active_paths,
            )
            git_state = info.state.value
        elif rec.status in ("finalized", "complete", "completed"):
            git_state = "completed"
        else:
            git_state = "gone"

        last_active = activity_by_name.get(name)
        if last_active is None:
            last_active = _iso_epoch(rec.last_resumed_at) or _iso_epoch(rec.started_at)
        idle_secs = None if last_active is None else (now - last_active)

        verdict = gc_mod.classify_managed_worktree(
            worktree_id=rec.worktree_id, kind=rec.kind,
            follow_up=rec.follow_up, status=rec.status, git_state=git_state,
            has_live_mux=has_live_mux, attached=attached,
            has_live_session=has_live_session, idle_secs=idle_secs,
            min_idle_secs=min_idle_secs,
        )
        if verdict.action == "skip":
            result["skipped"].append({"id": rec.worktree_id, "reason": verdict.reason})
            continue
        if dry_run:
            result["removed"].append(
                {"id": rec.worktree_id, "reason": f"would remove ({verdict.reason})"})
            continue
        warns = _remove_managed_worktree(rec, repo, tracking_path)
        try:
            activity.log_event("managed_worktree_gc",
                               worktree_id=rec.worktree_id, reason=verdict.reason)
        except Exception:
            pass
        reason = verdict.reason + (f"; {'; '.join(warns)}" if warns else "")
        result["removed"].append({"id": rec.worktree_id, "reason": reason})

    return result


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


def cmd_reclaim(args: argparse.Namespace) -> int:
    """``reclaim`` -- free the exact Copilot process(es) bound to a session.

    Resolves the authoritative pid<->session<->worktree binding from Copilot's
    own ``inuse.<pid>.lock`` files (see :mod:`agent_worktrees.reclaim`) and
    terminates *only* the matched process(es) and their Copilot child tree --
    never a sibling session, never an unrelated process that merely shares a
    working directory. The reclaim primitive for **bare** orphans (a Copilot
    launched straight in a terminal, invisible to the ``wt-<id>`` mux fleet
    view) whose terminal was closed or wedged; freeing one loses nothing, since
    the session stays resumable from its on-disk state.

    Target selection (at least one, else cwd is inferred):
      * ``--session-id <id>``  -- one session (exact or unambiguous prefix);
      * ``--worktree-id <id>`` -- every session bound to that worktree;
      * ``--all``              -- every bound Copilot on the machine.
    ``--bare-only`` restricts to un-muxed orphans (the common intent).

    Safety: the process subtree containing *this* command is never reaped, and
    without ``--yes`` the command is a dry run (prints the plan, kills nothing)
    -- confirm-before-destroy. JSON out with ``--json``.
    """
    session_id = getattr(args, "session_id", None)
    raw_wt = getattr(args, "worktree_id", None)
    want_all = getattr(args, "all", False)
    as_json = getattr(args, "json", False)

    wt_id: str | None = None
    wt_path: str | None = None

    def _wt_path(wid: str) -> str | None:
        yaml_path = cfg.tracking_dir() / f"{wid}.yaml"
        if yaml_path.exists():
            try:
                return tracking.load_record(yaml_path).worktree_path
            except Exception:
                return None
        return None

    if raw_wt:
        wt_id = _resolve_worktree_id(raw_wt)
        wt_path = _wt_path(wt_id)
    elif not session_id and not want_all:
        # No explicit target -- infer the worktree from the current directory.
        wt_id = _infer_worktree_id_from_cwd()
        if not wt_id:
            return _json_error(
                "no --session-id/--worktree-id/--all and cwd is not a worktree",
                exit_code=2,
            )
        wt_path = _wt_path(wt_id)

    table = reclaim.build_process_table()
    found = reclaim.resolve_bound_copilots(
        session_id=session_id, worktree_id=wt_id,
        worktree_path=wt_path, table=table,
    )
    if getattr(args, "bare_only", False):
        # "un-muxed orphans": every bound Copilot Stop cannot reach -- a walkable
        # non-mux ancestry ("bare") AND one whose homing could not be positively
        # classified ("unknown", e.g. the pid missing from a racing process-table
        # snapshot). Only a positively mux-homed session is left to restart/Stop,
        # so a live-but-unclassifiable bound Copilot is still reclaimable instead
        # of stranding its worktree ACTIVE with no lifecycle verb.
        found = [f for f in found if f["homing"] != "mux"]

    # Safety guard: never reap the process subtree that contains this very
    # command (it runs as a child of the orchestrating Copilot).
    me = os.getpid()
    targets: list[dict] = []
    self_skipped: list[dict] = []
    for f in found:
        subtree = {f["pid"]} | reclaim.descendants_of(f["pid"], table)
        (self_skipped if me in subtree else targets).append(f)

    do_kill = getattr(args, "yes", False)
    reaped: list[dict] = []
    if do_kill and targets:
        reaped = reclaim.reap_bound_copilots(targets, table=table)

    # Clear residual inuse.<pid>.lock files (parity with reclaim_one, the local
    # picker path): force-remove the pids we just terminated plus any stale-pid
    # residue for this worktree, so a killed/crashed session leaves ZERO lock
    # behind. Only on a real kill (never a dry run) and only when scoped to a
    # worktree (a --session-id/--all sweep leaves lock GC to its own worktree
    # pass). A live muxed sibling's lock is preserved by clear_lock_residue.
    cleared: list[dict] = []
    if do_kill and (wt_id or wt_path):
        cleared = reclaim.clear_lock_residue(
            worktree_id=wt_id, worktree_path=wt_path,
            force_pids={r["pid"] for r in reaped if r.get("killed")},
            table=table,
        )

    payload = {
        "ok": True,
        "action": "reclaim" if do_kill else "dry-run",
        "filters": {
            "session_id": session_id, "worktree_id": wt_id,
            "all": want_all, "bare_only": getattr(args, "bare_only", False),
        },
        "targets": targets,
        "self_skipped": self_skipped,
        "reaped": reaped,
        "locks_cleared": cleared,
    }

    if as_json:
        _json_output(payload)
        return 0

    if not targets:
        print("No live Copilot process matched (nothing to reclaim).")
        for s in self_skipped:
            print(f"  (skipped self: {s['session_id'][:8]} pid {s['pid']})")
        return 0

    verb = "Reclaimed" if do_kill else "Would reclaim"
    print(f"{verb} {len(targets)} bound Copilot process(es):")
    for t in targets:
        wt = t["worktree_id"] or "?"
        line = f"  {t['session_id'][:8]}  pid {t['pid']:<6} [{t['homing']}]  {wt}"
        if do_kill:
            r = next((x for x in reaped if x["pid"] == t["pid"]), None)
            if r:
                mark = "killed" if r["killed"] else "FAILED"
                line += f"  -> {mark} (+{r['children_killed']} children)"
        print(line)
    for s in self_skipped:
        print(f"  (skipped self: {s['session_id'][:8]} pid {s['pid']})")
    if not do_kill:
        print("\nDry run -- pass --yes to actually terminate these processes.")
    return 0


def cmd_remux(args: argparse.Namespace) -> int:
    """``remux`` -- reparent a bare Copilot into its ``wt-<id>`` tmux pane.

    Linux/WSL only (delegates to :func:`agent_worktrees.remux.remux_bare_copilot`).
    Target selection mirrors ``reclaim``: ``--session-id``, ``--worktree-id``, or
    inferred from the current worktree cwd. The bound-but-BARE Copilot is adopted
    into a tmux pane via ``reptyr`` (no conversation lost) instead of being
    reaped-and-resumed. JSON with ``--json``; a hard, clear no-op on Windows.
    """
    from . import remux as _remux

    session_id = getattr(args, "session_id", None)
    raw_wt = getattr(args, "worktree_id", None)
    as_json = getattr(args, "json", False)

    def _wt_path(wid: str) -> str | None:
        yaml_path = cfg.tracking_dir() / f"{wid}.yaml"
        if yaml_path.exists():
            try:
                return tracking.load_record(yaml_path).worktree_path
            except Exception:
                return None
        return None

    wt_id: str | None = None
    wt_path: str | None = None
    if raw_wt:
        wt_id = _resolve_worktree_id(raw_wt)
        wt_path = _wt_path(wt_id)
    elif not session_id:
        wt_id = _infer_worktree_id_from_cwd()
        if not wt_id:
            return _json_error(
                "no --session-id/--worktree-id and cwd is not a worktree",
                exit_code=2)
        wt_path = _wt_path(wt_id)

    result = _remux.remux_bare_copilot(
        worktree_id=wt_id, session_id=session_id, worktree_path=wt_path,
        force_sudo=getattr(args, "force_sudo", None))

    if as_json:
        _json_output(result)
        return 0 if result.get("ok") else 1

    if not result.get("ok"):
        output.err(result.get("reason", "re-mux failed"))
        return 1
    sess, pid = result.get("session"), result.get("pid")
    if result.get("verified"):
        output.ok(f"Re-muxed pid {pid} into {sess} (pane {result.get('pane')}). "
                  f"Attach:  tmux attach -t {sess}")
    else:
        output.info(f"Opened a reptyr pane in {sess} for pid {pid} -- "
                    f"{result.get('reason')}. Attach to check:  "
                    f"tmux attach -t {sess}")
    return 0


def reclaim_one(worktree_id: str, *, bare_only: bool = True) -> dict:
    """Reap the bound Copilot process(es) for one worktree (Picker "Reclaim").

    The in-process executor behind the Picker's per-row **Reclaim** action:
    resolves the exact Copilot process(es) bound to *worktree_id*'s session(s)
    via :func:`reclaim.resolve_bound_copilots` and terminates them (and their
    Copilot child tree). ``bare_only`` (default) restricts to **un-muxed**
    orphans -- a bound Copilot with no mux ancestor, i.e. homing ``bare`` OR
    ``unknown`` (an un-walkable/racing ancestry that still is not positively
    mux-homed) -- so a healthy muxed sibling is left to the graceful
    ``restart``/Stop path while a live-but-unclassifiable bound Copilot is still
    reclaimable. Never reaps the process subtree containing this command. Returns
    a JSON-able ``{ok, worktree_id, targets, reaped}``.
    """
    table = reclaim.build_process_table()
    found = reclaim.resolve_bound_copilots(worktree_id=worktree_id, table=table)
    if bare_only:
        found = [f for f in found if f["homing"] != "mux"]
    me = os.getpid()
    targets = [
        f for f in found
        if me not in ({f["pid"]} | reclaim.descendants_of(f["pid"], table))
    ]
    reaped = reclaim.reap_bound_copilots(targets, table=table) if targets else []
    ok = all(r["killed"] for r in reaped) if reaped else True
    # Clear residual inuse.<pid>.lock files so the worktree ends with ZERO
    # residue -- "to the point where the pid lock file is removed". Force-remove
    # the pids we just terminated (the OS may not have reaped them yet, so a
    # liveness re-check could still read them alive) plus any pre-existing
    # stale-pid residue; a live muxed sibling's lock is preserved.
    cleared = reclaim.clear_lock_residue(
        worktree_id=worktree_id,
        force_pids={r["pid"] for r in reaped if r.get("killed")},
        table=table,
    )
    return {
        "ok": ok, "worktree_id": worktree_id,
        "targets": len(targets), "reaped": reaped,
        "locks_cleared": cleared,
    }


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
            # Best-effort reconcile write (#4547): reconcile unlocked (provider
            # I/O), then re-apply the deltas onto a fresh snapshot under a
            # best-effort lock so a concurrent foreground verb is never
            # clobbered; skip on contention (self-heals next pass).
            prune.reconcile_and_persist_best_effort(rec, pr_lookup)

        verdict = prune.assess(rec, info, turn_count=turns,
                               claimant_alive=_local_claimant_alive)

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
                claimant_alive=claimant_mod.resolve_claimant_alive,
                paired_sibling_final=prune.default_paired_sibling_final,
            )
            cleanable = disp.cleanable
            if disp.bucket == "active":
                skip_reason = "active Copilot session in use"
            elif disp.bucket == "claimed":
                skip_reason = disp.reason
            elif disp.bucket == "open-pr":
                skip_reason = disp.reason
            elif disp.bucket == "closed-unmerged":
                skip_reason = disp.reason
            elif disp.bucket == "paired-pending":
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


def _print_gc_orphans(report: dict, dry_run: bool) -> None:
    """Human-facing summary of the orphan-directory sweep."""
    removed = report.get("removed", [])
    skipped = report.get("skipped", [])
    print()
    print("🧹 Orphan directories -- on disk, not a git worktree, not tracked")
    if not report.get("scanned"):
        print("  none found.")
        return
    verb = "would remove" if dry_run else "removed"
    for item in removed:
        print(f"  ✓ {verb}: {item['path']}  ({item['reason']})")
    for item in skipped:
        output.warn(f"  skipped: {item['path']}  ({item['reason']})")
    print()
    if dry_run:
        print(f"{len(removed)} orphan dir(s) would be removed, "
              f"{len(skipped)} skipped.")
    else:
        output.ok(f"Removed {len(removed)} orphan dir(s); "
                  f"{len(skipped)} skipped.")


def _print_gc_managed(report: dict, dry_run: bool) -> None:
    """Human-facing summary of the managed (system/bridge) worktree sweep."""
    removed = report.get("removed", [])
    skipped = report.get("skipped", [])
    print()
    print("🧹 Managed worktrees -- leaked system/bridge (dead, final/unused)")
    if not removed and not skipped:
        print("  none tracked.")
        return
    verb = "would remove" if dry_run else "removed"
    for item in removed:
        print(f"  ✓ {verb}: {item['id']}  ({item['reason']})")
    for item in skipped:
        print(f"  · kept: {item['id']}  ({item['reason']})")
    print()
    if dry_run:
        print(f"{len(removed)} managed worktree(s) would be reaped, "
              f"{len(skipped)} kept.")
    else:
        output.ok(f"Reaped {len(removed)} managed worktree(s); "
                  f"{len(skipped)} kept.")


def _print_gc_shells(report: dict, dry_run: bool) -> None:
    """Human-facing summary of the orphaned launcher-shell reap."""
    reaped = report.get("reaped", [])
    candidates = report.get("candidates", [])
    print()
    print("🧹 Launcher shells -- orphaned pwsh/python scaffolding (stranded)")
    if not report.get("available"):
        print("  (skipped or process enumeration unavailable.)")
        return
    if not reaped:
        print("  none found.")
        return
    verb = "would reap" if dry_run else "reaped"
    for c in candidates:
        print(f"  ✓ {verb}: pid {c['pid']}  {c['cmdline'][:80]}")
    for e in report.get("errors", []):
        output.warn(f"  ! pid {e['pid']}: {e['reason']}")
    print()
    if dry_run:
        print(f"{len(reaped)} orphaned launcher shell(s) would be reaped.")
    else:
        output.ok(f"Reaped {len(reaped)} orphaned launcher shell(s).")


def cmd_gc(args: argparse.Namespace) -> int:
    """Garbage-collect this project's worktrees on this machine.

    One command, three sweeps, then a prune:

      1. **Tracked reap** -- the same prune-safety verdict as ``cleanup``
         (finalized/merged/clean; ``--include-unused`` / ``--include-conversations``
         widen it). Never touches a dirty / ahead / follow-up / active / system
         worktree. ``--dry-run`` lists without removing.
      2. **Managed (system/bridge) sweep** -- reaps *leaked* daemon-owned
         worktrees that routine cleanup skips: only the provably dead ones
         (FINAL or UNUSED, no active mux/session/attach, no follow-up, idle past
         the grace window). Skip with ``--no-managed`` (#1069).
      3. **Orphan-directory sweep** -- removes *effectively-empty* on-disk
         directories under the worktree roots that are neither a registered git
         worktree nor a tracking record (leftovers from interrupted/forced
         removals), with a locked-directory retry/skip; a leftover holding real
         files is reported, never auto-deleted.
      4. **Orphaned launcher-shell reap** -- terminates pwsh/python
         ``-m agent_worktrees`` scaffolding stranded by a force-closed terminal
         (parent exited, nothing live under it, idle past the grace window).
         Service-safe (positive launcher-signature only). Skip with
         ``--no-reap-shells`` (copilot-extensions #102).
      5. ``git worktree prune`` to drop stale registrations.

    Idempotent: a second run right after the first finds nothing to do.

    ``--json`` reports the managed + orphan + shell sweeps (machine-readable);
    the tracked reap runs in text mode.
    """
    from . import gc as gc_mod

    config = cfg.load_config()
    repo = config.default_repo
    records = tracking.list_records(cfg.tracking_dir())
    dry = getattr(args, "dry_run", False)
    json_mode = getattr(args, "json", False)
    orphans_only = getattr(args, "orphans_only", False)
    do_managed = not getattr(args, "no_managed", False) and not orphans_only
    do_shells = not getattr(args, "no_reap_shells", False) and not orphans_only

    # 1. Tracked reap -- reuse the cleanup verdict machinery (one fetch, full
    #    safety). Skipped in --json mode (its output is text) and --orphans-only.
    if not json_mode and not orphans_only:
        cmd_cleanup(argparse.Namespace(
            clean=not dry, worktree_id=None, force=False, json=False,
            include_unused=getattr(args, "include_unused", False),
            include_conversations=getattr(args, "include_conversations", False),
            reconcile_prs=getattr(args, "reconcile_prs", False),
            max_age_days=getattr(args, "max_age_days", 7),
        ))

    # 2. Managed (system/bridge) leak sweep -- the daemon-owned kinds cleanup
    #    skips. Only provably-dead ones are reaped (#1069).
    grace_hours = getattr(args, "managed_grace_hours", None)
    managed_kwargs = {"dry_run": dry}
    if grace_hours is not None:
        managed_kwargs["min_idle_secs"] = float(grace_hours) * 3600
    managed = sweep_managed_worktrees(**managed_kwargs) if do_managed \
        else {"removed": [], "skipped": []}

    # 3. Orphan-directory sweep (the GC-specific capability).
    orphans = gc_mod.sweep_orphans(repo, records, dry_run=dry)

    # 4. Orphaned launcher-shell reap (machine-wide; #102). Service-safe and
    #    idle-gated -- only pwsh/python launcher scaffolding stranded by a
    #    force-closed terminal is reaped.
    shells_grace = getattr(args, "reap_shells_grace_hours", None)
    shell_kwargs = {"dry_run": dry}
    if shells_grace is not None:
        shell_kwargs["idle_grace_secs"] = float(shells_grace) * 3600
    shells = reap_orphan_launcher_shells(**shell_kwargs) if do_shells \
        else {"available": False, "reaped": [], "candidates": [],
              "skipped": [], "errors": []}

    # 5. Prune stale worktree registrations.
    if not dry:
        git_ops.prune_worktrees(cwd=repo.anchor)

    if json_mode:
        print(json.dumps(
            {"dry_run": dry, "repo": config.repo_name,
             "managed": managed, "orphans": orphans, "shells": shells},
            indent=2))
        return 0
    if do_managed:
        _print_gc_managed(managed, dry)
    _print_gc_orphans(orphans, dry)
    if do_shells:
        _print_gc_shells(shells, dry)
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


def finalize_one(wt_id: str) -> dict:
    """Finalize a single worktree by ID; return a JSON-ready result dict.

    The pure result-returning core shared by the picker's in-process local
    Finalize executor (the ``finalize --worktree-id --json`` CLI has its own
    path). ``validate_and_finalize`` never squashes/rebases/pushes -- for the
    conversation-only / unused worktrees the picker offers Finalize on, it
    verifies nothing is unpushed (there is nothing) and removes the worktree.

    Its human-readable output is suppressed: the picker runs this on a
    background thread under a live Textual screen, so stray prints would corrupt
    the render. Only the structured outcome is returned.
    """
    import contextlib
    import io

    try:
        config = cfg.load_config()
    except Exception as e:
        return {"worktree_id": wt_id, "success": False, "ok": False,
                "reason": str(e) or "config load failed"}
    wt_id = _resolve_worktree_id(wt_id)
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink):
            success = fin.validate_and_finalize(wt_id, config)
    except Exception as e:
        return {"worktree_id": wt_id, "success": False, "ok": False,
                "reason": (str(e) or type(e).__name__)}
    status = "finalized"
    try:
        yaml_path = cfg.tracking_dir() / f"{wt_id}.yaml"
        if yaml_path.exists():
            status = tracking.load_record(yaml_path).status
    except Exception:
        pass
    return {"worktree_id": wt_id, "success": bool(success),
            "ok": bool(success), "status": status}


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
        if managed:
            sels = profiles_mod.normalize_selection(
                profiles_mod.load_selection(cfg_path), machine, env)
        else:
            # Unmanaged -> report the DEFAULT column (minimal per-agent + bare
            # cross-machine), computed from the roster candidates. The Picker
            # keys off ``managed`` (False) and renders the default itself, so
            # these targets are for human/JSON legibility.
            from .picker_tui import roster
            candidates = [
                profiles_mod.TargetSel(m, e, kind)
                for (m, e) in roster.target_envs()
                for kind in ("agent", "shell")
            ]
            sels = profiles_mod.default_selection(candidates, machine, env)
        payload = {
            "machine": machine,
            "env": env,
            "managed": managed,
            "targets": [s.as_dict() for s in sels],
        }
        if as_json:
            _json_output(payload)
        else:
            state = ("managed" if managed
                     else "default (minimal + bare cross-machine)")
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
    future work). Returns True only when a mirror actually ran **and succeeded**
    (the fragment was regenerated) -- so ``profiles apply`` / the Picker report
    ``mirrored`` honestly rather than masking a failed refresh (dotfiles#563).
    """
    if platform.system() != "Windows":
        return False
    try:
        return _refresh_terminal_profiles()
    except Exception:
        return False


def cmd_terminal_fragment(args: argparse.Namespace) -> int:
    """Preview the Windows Terminal fragment this machine's config would emit.

    Reads the same local sources the installer's ``Build-TerminalFragment``
    consults (``repos.yaml`` / ``projects.yaml`` / per-project ``machines.yaml``
    + ``config.yaml``) and prints the fragment **without deploying it**. Use it
    to see why a project does or does not get a Terminal profile.

    Output modes:
      * default   -- the fragment JSON exactly as it would be written.
      * --explain -- a per-project decision trace (managed/unmanaged, agent
                     exposure, and each emitted profile name/target).
    """
    from . import terminal_fragment as tf

    machine = getattr(args, "machine", None)
    if not machine:
        try:
            machine = cfg.load_config().machine
        except Exception:
            machine = None
    if not machine:
        output.err(
            "Could not resolve this machine's key. Run from a managed repo or "
            "pass --machine <key>.")
        return 1

    try:
        current = cfg.project_name()
    except Exception:
        current = None

    if getattr(args, "doctor", False):
        return _terminal_fragment_doctor(machine, current)

    result = tf.preview_local(machine, current_project=current)

    if getattr(args, "explain", False):
        print(f"Terminal fragment preview for '{machine}' "
              f"({len(result.profiles)} profile(s) across "
              f"{len(result.plans)} project(s)):\n")
        for plan in result.plans:
            state = ("unmanaged -> default column" if plan.unmanaged_default
                     else "managed selection")
            agent = "agent-exposed" if plan.agent_exposed else "no-agent"
            print(f"- {plan.display} [{plan.name}]  ({state}; {agent})")
            if not plan.profiles:
                print("    (no profiles emitted)")
            for p in plan.profiles:
                print(f"    - {p.name!r}  <{p.kind}>  {p.commandline}")
            print()
        return 0

    print(json.dumps(result.fragment(), indent=2))
    return 0


def _terminal_fragment_doctor(machine: str, current: str | None) -> int:
    """Read-only report of Windows Terminal state drift vs. the fragment.

    Surfaces the two failure modes the delta-based ``Sync-TerminalState`` could
    leave behind and never self-heal: fragment profiles WT is *hiding* (in the
    fragment + ``generatedProfiles`` but missing from ``settings.json``), and
    orphaned ``generatedProfiles`` cruft. Never mutates WT state -- the fix is
    applied by the installer's convergent sync on the next ``update``.
    """
    from . import terminal_fragment as tf

    diag = tf.diagnose_wt_state()
    if diag is None:
        output.warn("Windows Terminal state unavailable "
                    "(non-Windows, or WT not installed).")
        return 0

    result = tf.preview_local(machine, current_project=current)
    frag_names = {p.guid.lower(): p.name for p in result.profiles}

    print(f"Windows Terminal state doctor for '{machine}':")
    print(f"  fragment profiles : {diag.fragment_count}")
    print(f"  settings profiles : {diag.settings_count}")
    print(f"  generatedProfiles : {diag.generated_count}")

    if diag.hidden:
        print(f"\n  HIDDEN -- in fragment + generatedProfiles but not in "
              f"settings.json ({len(diag.hidden)}):")
        for g in diag.hidden:
            print(f"    - {frag_names.get(g, g)}  {g}")
        print("    -> the next 'update' will prune these from generatedProfiles "
              "so WT re-discovers them.")
    if diag.orphans:
        print(f"\n  ORPHANS -- generatedProfiles entries in no fragment and not "
              f"materialized ({len(diag.orphans)}): accumulated cruft.")
        if diag.reclaimable_orphans:
            print(f"    - {len(diag.reclaimable_orphans)} reclaimable (ours) -> "
                  f"the next 'update' prunes these automatically.")
        if diag.foreign_orphans:
            print(f"    - {len(diag.foreign_orphans)} kept (v4/v5 GUIDs -- "
                  f"WT built-in / random profiles; never auto-pruned).")
    if diag.duplicate_names:
        print("\n  DUPLICATE profile names in settings.json "
              "(often a legacy stand-alone fragment colliding with the "
              "generated one):")
        for name, count in diag.duplicate_names:
            print(f"    - {name!r} x{count}")

    if diag.healthy:
        print("\n  OK -- no hidden or duplicate profiles detected.")
    return 0


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
        # ``--local`` forces the local-only source (data_local) instead of the
        # multi-machine SSH source -- needed for an isolated sandbox preview,
        # where no mesh repo/roster is resolvable (data_ssh would raise).
        if getattr(args, "picker_local", False):
            live = False
        else:
            live = not _in_ssh_session()
        decision = picker_tui.run_tui_picker(live=live, mock_mode=True)
        if as_json:
            _json_output({"mock": True, "decision": decision})
        else:
            output.info(f"mock picker exited · decision: {decision!r}")
        return 0

    if action == "screenshot":
        # Deterministic headless capture of the picker for auditing: render the
        # current worktree state to an SVG "screenshot" (or a character grid)
        # with no live terminal and no human watching. Realizes visions/picker
        # Features/auditable-testable-rendering.
        import sys as _sys

        from .picker_tui import capture as _capture

        live = bool(getattr(args, "live", False))
        fmt = getattr(args, "picker_format", "svg")
        out = getattr(args, "out", None)
        pivot = getattr(args, "picker_pivot", None)
        wait_pivot = float(getattr(args, "picker_wait", 0.0) or 0.0)
        if live:
            from .picker_tui import data_ssh as _source
        else:
            from .picker_tui import data_local as _source
        caps = _capture.capture(
            _source, live=live, pivot=pivot, wait_pivot=wait_pivot,
        )
        content = caps[fmt]
        if out:
            Path(out).write_text(content, encoding="utf-8")
            if as_json:
                _json_output({"screenshot": out, "format": fmt,
                              "bytes": len(content)})
            else:
                output.ok(f"picker {fmt} screenshot -> {out} "
                          f"({len(content)} bytes)")
        else:
            _sys.stdout.write(content)
            if not content.endswith("\n"):
                _sys.stdout.write("\n")
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
        output.err(f"Machine registry not found at {cfg.machines_yaml_path(repo_dir)}")
        output.info("Create .agent-worktrees/machines.yaml with an entry for this machine.")
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

# worktree-status-core: MIGRATED to the session-conduct sessionStart hook
# (dotfiles#1054 / effort instructions-to-hooks). The guidance text now lives in
# ``plugins/agent-worktrees/scripts/conduct/worktree-conduct.md`` and is emitted
# as ``additionalContext`` by the cwd-gated session-conduct hook, instead of
# being materialized into ``~/.{project}/.github/instructions/``. The deploy path
# now only retires any stale copy of the old file (see
# :func:`_remove_managed_instruction`).


# account-conduct: MIGRATED to the session-conduct sessionStart hook
# (dotfiles#1053 / effort instructions-to-hooks). The guidance text now lives in
# ``plugins/agent-worktrees/scripts/conduct/account-conduct.md`` and is emitted
# as ``additionalContext`` by the session-conduct hook -- cwd-gated to managed
# projects and launch-path-independent -- instead of being materialized into
# ``~/.{project}/.github/instructions/`` and loaded via
# COPILOT_CUSTOM_INSTRUCTIONS_DIRS. The deploy path now only retires any stale
# copy of the old file (see :func:`_remove_managed_instruction`).


def _remove_managed_file(path: Path, label: str) -> None:
    """Remove a previously-deployed managed file (idempotent, marker-guarded).

    Only removes a file carrying the agent-worktrees ownership marker, so an
    unmarked user file is never touched. Used to retire content that has migrated
    to a sessionStart hook.
    """
    if not path.exists():
        return
    try:
        if _INSTRUCTION_MARKER in path.read_text():
            path.unlink()
            output.changed(f"removed migrated {label} (now a sessionStart hook)")
    except OSError:
        pass


def _remove_managed_instruction(proj_dir: Path, name: str) -> None:
    """Remove a previously-deployed managed ``*.instructions.md`` (idempotent).

    Marker-guarded: only removes a file carrying the agent-worktrees ownership
    marker, so an unmarked user file is never touched. Used to retire fragments
    that have migrated to a sessionStart hook.
    """
    _remove_managed_file(proj_dir / ".github" / "instructions" / name, name)


def _gh_env_for_repo(target: str) -> tuple[dict[str, str], str | None, bool]:
    """Build the environment for running ``gh`` against *target* under the
    account that owns it, via **token injection** (never ``gh auth switch``).

    ``gh``'s active account is global per-machine, so switching it is racy on a
    shared box. Instead resolve ``account_for_github_slug(target)`` and mint its
    token (``gh auth token --user <login>``), injected as ``GH_TOKEN`` -- a
    side-effect-free, race-safe override that leaves the active account alone.

    Returns ``(env, login, injected)``: a copy of ``os.environ`` with
    ``GH_TOKEN`` set when an account resolved *and* a token minted; ``login`` is
    the resolved account (or None); ``injected`` reports whether ``GH_TOKEN``
    was set. When nothing resolves the ambient env is returned unchanged (caller
    falls back to the active gh account).
    """
    from . import repos

    env = dict(os.environ)
    login = repos.account_for_github_slug(target)
    injected = False
    if login:
        token = git_ops.gh_token_for_account(login)
        if token:
            env["GH_TOKEN"] = token
            injected = True
    return env, login, injected


# ── Temporary: extension-reload "Loading…/Resuming…" hang warning ──────────
# A warning about the CAR extension-reload generation-race hang
# (github/copilot-agent-runtime#13492; fix: #13494). Migrated off the per-project
# COPILOT_CUSTOM_INSTRUCTIONS_DIRS file to the ``session-ext-reload`` sessionStart
# hook (dotfiles#1055): the canonical text now lives in
# ``scripts/ext-reload-hang.md`` (deployed to ~/.agent-worktrees/bin/ and emitted
# as additionalContext). That hook is NOT strictly cwd-gated -- it also fires at
# cwd=~/ so it reaches a **Bare resume** session, the exact scenario this warning
# covers, which is why it could not ride the cwd-gated session-conduct injector.
#
# TEMPORARY: retire the whole feature (fragment, both session-ext-reload scripts,
# their hooks.json entry + installer copy, and the retirement call below) once the
# #13494 fix has shipped and rolled out everywhere.


def _deploy_copilot_instructions(
    proj_dir: Path, entry: cfg.MachineEntry,
    project: str = "",
) -> None:
    """Retire migrated managed instruction files + clean up legacy artifacts.

    All the guidance this used to materialize into the
    COPILOT_CUSTOM_INSTRUCTIONS_DIRS directory now arrives via sessionStart hooks
    that emit ``additionalContext`` (effort instructions-to-hooks):

    - machine identity (``machine.instructions.md`` + the nested-discovery
      ``AGENTS.md``) -> the ``session-machine`` hook / ``machine-context`` command
      (dotfiles#1056), computed live from ``machines.yaml``;
    - account- and worktree-conduct -> the ``session-conduct`` hook.

    So this function no longer *writes* those files; it retires any stale copy we
    previously deployed (marker-guarded, so unmarked user files are never
    touched). ``entry`` is retained for call-site compatibility but unused. The
    ext-reload-hang warning is now delivered via the ``session-ext-reload``
    sessionStart hook too (dotfiles#1055), so its per-project file is retired here
    as well.
    """
    # Machine identity migrated to the session-machine sessionStart hook
    # (dotfiles#1056): retire the stale per-project file + nested AGENTS.md.
    instr_dir = proj_dir / ".github" / "instructions"
    _remove_managed_instruction(proj_dir, "machine.instructions.md")
    _remove_managed_file(proj_dir / "AGENTS.md", "AGENTS.md")

    # worktree-conduct migrated to the session-conduct sessionStart hook
    # (dotfiles#1054): retire any stale per-project file we used to deploy.
    _remove_managed_instruction(proj_dir, "worktree-conduct.instructions.md")

    # account-conduct migrated to the session-conduct sessionStart hook
    # (dotfiles#1053): retire any stale per-project file we used to deploy.
    _remove_managed_instruction(proj_dir, "account-conduct.instructions.md")

    # Temporary: the ext-reload hang warning migrated to the session-ext-reload
    # sessionStart hook (dotfiles#1055); retire any stale per-project file.
    _remove_managed_instruction(proj_dir, "ext-reload-hang.instructions.md")

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
        proj_dir / ".github" / "instructions" / "worktree-conduct.instructions.md",
        proj_dir / ".github" / "instructions" / "account-conduct.instructions.md",
        proj_dir / ".github" / "instructions" / "ext-reload-hang.instructions.md",
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


def _ensure_ado_pr_cli(pr_cfg) -> None:
    """Provision the Azure DevOps CLI prereq for a repo that manages PRs via ADO.

    ``create-pr``/``pr-merge`` shell out to ``az repos pr ...`` (the
    ``azure-devops`` provider), which needs the ``azure-devops`` az extension.
    A machine missing it makes ``az`` prompt interactively and fail under
    automation, so provision it at install/adopt time. Best-effort: warn,
    never abort.
    """
    try:
        if not (pr_cfg.enabled and pr_cfg.provider == "azure-devops"):
            return
        from .providers.azure_devops import ensure_cli_ready
        ok, msg = ensure_cli_ready()
        (output.ok if ok else output.warn)(f"Azure DevOps CLI: {msg}")
    except Exception as e:  # best-effort preflight -- never block install/adopt
        output.warn(f"Could not verify Azure DevOps CLI setup: {e}")


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

    # PR-workflow git hooks are an ADOPT concern (register), NOT install.
    # install/update are machine-local and read-only w.r.t. the repo's git: if
    # PR mode is on but the managed shims are missing or a stale core.hooksPath
    # shadows them, WARN -- never inject or mutate repo git here. Arming (and
    # clearing a stale core.hooksPath) happens on adopt: run '<repo> register'
    # or 'agent-worktrees hook install'.
    try:
        cfg_for_hooks = cfg.load_config(config_path)
        if cfg_for_hooks.default_repo.pr.enabled:
            from . import hooks as _hooks
            present, stale = _hooks.hook_health(repo_dir)
            if not present:
                output.warn(
                    "PR mode is enabled but the PR-workflow git hooks are not "
                    "installed. Re-adopt the repo ('agent-worktrees register') "
                    "or run 'agent-worktrees hook install' to arm them."
                )
            if stale:
                output.warn(
                    f"core.hooksPath is set to '{stale}', which shadows the "
                    "managed .git/hooks shims -- the PR-workflow guard will not "
                    "run. Re-adopt ('agent-worktrees register') to clear it."
                )
    except Exception as e:
        output.warn(f"Could not check git-hook health: {e}")

    # Azure DevOps PR provider prereq (machine-local): ensure the az
    # 'azure-devops' extension so create-pr/pr-merge work on this machine.
    try:
        _ensure_ado_pr_cli(cfg.load_config(config_path).default_repo.pr)
    except Exception:
        pass

    print()
    output.ok("Installation complete")
    print(f"  Runtime:   {runtime_dir}")
    print(f"  Project:   {proj_dir}")
    print(f"  Usage:     {project}")
    return 0


def _resolve_terminal_install_script() -> Path | None:
    """Locate ``install.ps1`` for the Windows Terminal profile refresh.

    Resolution order (first existing wins):
      1. The deploy-manifest's ``plugin_source`` -- authoritative when set, but
         the marketplace-install flow leaves it empty (dotfiles#211), so it is
         only a hint, not a hard gate.
      2. The installed plugin dir (``~/.copilot/installed-plugins/...``) as
         discovered by :func:`update_stage.discover_plugin_dir` -- the robust fallback
         that does not depend on manifest correctness.
      3. The running module's own plugin root (``<plugin>/scripts/install.ps1``)
         -- covers direct/dev runs from a checkout.

    Returns the first candidate whose ``scripts/install.ps1`` exists, else None.
    """
    candidates: list[Path] = []

    # 1. deploy-manifest plugin_source (may be empty after a marketplace install)
    manifest_path = cfg.install_dir() / "deploy-manifest.json"
    if manifest_path.exists():
        try:
            m = json.loads(manifest_path.read_text())
            plugin_source = m.get("plugin_source")
            if plugin_source:
                candidates.append(Path(plugin_source) / "scripts" / "install.ps1")
        except Exception:
            pass

    # 2. installed plugin dir (marketplace or _direct) -- robust fallback
    try:
        plugin_dir, _layout = discover_plugin_dir()
        if plugin_dir:
            candidates.append(plugin_dir / "scripts" / "install.ps1")
    except Exception:
        pass

    # 3. the running module's own scripts dir (src/agent_worktrees -> plugin root)
    try:
        candidates.append(
            Path(__file__).resolve().parents[2] / "scripts" / "install.ps1"
        )
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
    """Regenerate the Windows Terminal fragment from the saved selection.

    Delegates to the PowerShell installer's narrow ``refresh-profiles`` action
    (dotfiles#563), which regenerates **only** the WT fragment (Deploy-Shortcuts)
    for the active project. The old path shelled the whole ``update`` action
    (venv redeploy, pip install, binstub reconcile, psmux, instruction deploy --
    ~60s+ in practice) under a 30s subprocess timeout, so it was killed long
    before the fragment was rebuilt and the ``TimeoutExpired`` was swallowed into
    a warning. Returns ``True`` only when the refresh actually succeeded (exit
    code 0) so callers (the Picker Apply, ``profiles apply``) report mirror
    status honestly instead of a blanket ``mirrored: true``.

    The installer script is resolved via :func:`_resolve_terminal_install_script`
    so an empty deploy-manifest ``plugin_source`` (marketplace install) no longer
    silently no-ops the refresh; when it genuinely cannot be found we emit a
    warning rather than returning silently (dotfiles#211).
    """
    install_script = _resolve_terminal_install_script()
    if install_script is None:
        output.warn(
            "Could not refresh Windows Terminal profiles: install.ps1 not found "
            "(checked deploy-manifest plugin_source and installed-plugin dir)"
        )
        return False

    cmd = ["pwsh", "-NoProfile", "-File", str(install_script),
           "refresh-profiles"]
    # Pass the active project explicitly so the installer regenerates the
    # fragment for the right context instead of relying on CWD/env inference in
    # the subprocess: the mirror often runs from a worktree dir whose basename
    # does not map to a project config.
    try:
        cmd += ["-ProjectName", cfg.project_name()]
    except Exception:
        pass

    try:
        # A fragment-only regen is fast; the generous timeout only guards a cold
        # PowerShell start + YAML parse and (unlike the old 30s cap on the full
        # ``update``) comfortably outlasts the work, so we never kill it
        # mid-write.
        #
        # Decode defensively: the installer's captured stdout/stderr is not
        # guaranteed UTF-8 (a redirected PowerShell pipe honors
        # ``[Console]::OutputEncoding``, which can be an OEM/ANSI codepage under
        # which glyphs like the box-drawing headers or a project/path name emit
        # non-UTF-8 bytes). With the default strict ``text=True`` a stray byte
        # (e.g. 0xfb) raised ``UnicodeDecodeError`` inside subprocess's reader
        # thread -- a noisy traceback even though the refresh itself succeeded.
        # ``errors="replace"`` keeps the capture robust regardless of the child's
        # console codepage.
        result = subprocess.run(
            cmd, capture_output=True, encoding="utf-8", errors="replace",
            timeout=180,
        )
    except Exception:
        output.warn("Could not refresh Windows Terminal profiles")
        return False

    if result.returncode != 0:
        output.warn(
            "Could not refresh Windows Terminal profiles "
            f"(installer exited {result.returncode})"
        )
        return False
    output.ok("Windows Terminal profiles refreshed")
    return True


def cmd_repair(args: argparse.Namespace) -> int:
    """Repair this machine's agent-worktrees integration in place.

    Two independently-selectable targets (default: **both**):

    * ``--terminal`` -- regenerate the Windows Terminal fragment and reconcile
      live WT state: heal fragment profiles WT is hiding (in the fragment +
      ``generatedProfiles`` but missing from ``settings.json``) and reclaim our
      accumulated ``generatedProfiles`` orphans. Windows-only (a no-op else).
    * ``--binstubs`` -- redeploy every registered project's ``~/.local/bin``
      launcher (add/refresh missing or stale) and remove stale ones.

    Unlike ``update``, ``repair`` never touches the plugin/runtime version -- it
    only reconciles local *deployed state*, so it is the right tool when the
    Terminal dropdown or a binstub is wrong but the runtime is already current
    (``update`` would otherwise version-skip the installer). It is idempotent
    and safe to re-run.
    """
    want_terminal = getattr(args, "terminal", False)
    want_binstubs = getattr(args, "binstubs", False)
    if not want_terminal and not want_binstubs:
        want_terminal = want_binstubs = True  # neither flag -> repair both

    rc = 0

    if want_binstubs:
        output.header("Repairing project binstubs")
        try:
            inst.reconcile_binstubs()
        except Exception as e:  # noqa: BLE001 -- report, don't abort the other target
            output.err(f"Binstub repair failed: {e}")
            rc = 1

    if want_terminal:
        output.header("Repairing Windows Terminal profiles")
        if platform.system() != "Windows":
            output.skipped("Terminal profile repair is Windows-only -- skipped")
        else:
            from . import terminal_fragment as tf

            diag = tf.diagnose_wt_state()
            if diag is not None:
                if diag.hidden:
                    output.info(f"Will heal {len(diag.hidden)} hidden fragment "
                                "profile(s)")
                if diag.reclaimable_orphans:
                    output.info(f"Will reclaim {len(diag.reclaimable_orphans)} "
                                "orphaned generatedProfiles GUID(s)")
                for name, count in diag.duplicate_names:
                    output.warn(f"Duplicate profile {name!r} x{count} in "
                                "settings.json -- a separate stand-alone fragment "
                                "shares the name; not auto-resolved here")
                if diag.healthy and not diag.reclaimable_orphans:
                    output.ok("Windows Terminal state already clean")
            if not _refresh_terminal_profiles():
                rc = 1
            elif diag is not None and (diag.hidden or diag.reclaimable_orphans):
                # The installer's Sync-TerminalState logs the concrete counts and
                # the WT-running caveat; surface the follow-through explicitly.
                output.info("If Windows Terminal was open, close it fully and "
                            "reopen for the healed profiles to appear.")

    return rc


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

    # Auto-detect default branch if not specified. Respect the REMOTE's
    # configured default -- never a stale local `master` (dotfiles#1046):
    # origin/HEAD, else `ls-remote --symref` (authoritative even when the local
    # origin/HEAD was never set), else a main-first remote-ref probe.
    default_branch = getattr(args, "default_branch", None) or None
    if not default_branch:
        default_branch = _resolve_remote_default_branch(
            str(repo_dir), "origin", allow_remote=True)
    if not default_branch:
        # No remote signal (remote-less or offline repo) -- probe LOCAL heads,
        # main-first. Never fall back to the current branch, which is often a
        # feature branch in worktree workflows and would record the wrong default.
        for candidate in ("main", "master"):
            r = git_ops.git("rev-parse", "--verify", "--quiet",
                            f"refs/heads/{candidate}",
                            cwd=str(repo_dir), check=False)
            if r.returncode == 0:
                default_branch = candidate
                break
    if not default_branch:
        # Undeterminable -- ask explicitly rather than guessing.
        output.warn(
            "Could not detect default branch "
            "(no remote default, no local main or master branch)"
        )
        branch_input = input("  Default branch name: ").strip()
        if branch_input:
            default_branch = branch_input
        else:
            default_branch = "main"
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
    # Resolve agent exposure up front: explicit flags win, else the repos.yaml
    # classification, else default ON (adopting a repo means working in it).
    # A no-agent adoption is worked programmatically (create --json) rather than
    # launched from the terminal dropdown, so it also gets NO terminal profile.
    if getattr(args, "no_agent", False):
        expose_agent = False
    elif getattr(args, "agent", False):
        expose_agent = True
    else:
        from . import repos as _repos
        _entry = _repos.find_repo(project)
        expose_agent = _entry.agent if _entry else True

    if not config_path.exists() or args.force:
        _write_config(
            config_path, repo_dir, machine, plat, project, default_branch,
            headless=getattr(args, "headless", False),
            no_terminal_profile=not expose_agent,
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

    # #282: ensure the repo has a repos.yaml entry so CWD->project discovery
    # resolves it. projects.yaml is deliberately lean -- it defers identity and
    # location (anchor / default_branch) to repos.yaml, the single owning store
    # -- and the reverse-lookup that answers "which project am I in?" keys off
    # repos.yaml's per-platform anchor. Without an entry a freshly registered
    # repo is reachable only via its own binstub or --project, never a bare
    # `agent-worktrees <verb>` from its own directory (the confusing "not adopted
    # yet -- run register" after register already ran). Record the anchor under
    # the CURRENT platform (so a WSL adoption is filed under 'wsl', not 'linux'),
    # merging into any existing entry and preserving a deliberate non-worktree
    # class (add_repo only upgrades away from the 'reference' default).
    try:
        from . import repos as _repos_reg
        _existing_repo = _repos_reg.find_repo(project)
        _reg_class = (
            _existing_repo.repo_class
            if _existing_repo is not None
            and _existing_repo.repo_class != "reference"
            else "worktree"
        )
        _reg_remote_url = ""
        _rru = subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if _rru.returncode == 0:
            _reg_remote_url = _rru.stdout.strip()
        _repos_reg.add_repo(
            project,
            str(repo_dir),
            repo_class=_reg_class,
            remote=_reg_remote_url,
            default_branch=default_branch,
            agent=expose_agent,
            plat=plat,
        )
    except Exception as _e:
        output.warn(f"Could not record repos.yaml entry for '{project}': {_e}")

    # #537: adopting a repo is the moment to pin its gh account. If the account
    # only resolves by falling back to an org owner that isn't an authenticated
    # gh account, clarify it (prompt / persist an account_map entry) now rather
    # than letting a later gh/CodeSpace op fail on an unusable derived login.
    try:
        _reg_remote = ""
        _rr = subprocess.run(
            ["git", "-C", str(repo_dir), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        if _rr.returncode == 0:
            _reg_remote = _rr.stdout.strip()
        if _reg_remote:
            from . import repos as _repos_acct
            _explicit = ""
            _entry_acct = _repos_acct.find_repo(project)
            if _entry_acct is not None:
                _explicit = _entry_acct.account
            _clarify_registration_account(_reg_remote, project, _explicit)
    except Exception:
        pass

    # PR-workflow git hooks -- an ADOPT concern. Adopting a repo (which you own)
    # is the one flow permitted to mutate its git: clear a stale core.hooksPath
    # that would shadow the managed shims, then inject/refresh them into the
    # shared .git/hooks. Gated on PR mode so adopting a direct-push repo never
    # touches its hooks; inert at runtime unless AGENT_WORKTREES_HOOKS=1.
    try:
        cfg_for_hooks = cfg.load_config(config_path)
        if cfg_for_hooks.default_repo.pr.enabled:
            from . import hooks as _hooks
            cleared = _hooks.clear_stale_hooks_path(repo_dir)
            if cleared:
                output.changed(
                    f"Cleared stale core.hooksPath ('{cleared}') that shadowed "
                    "the managed .git/hooks shims"
                )
            installed_hooks = _hooks.install_hooks(repo_dir)
            if installed_hooks:
                output.ok(
                    f"PR-workflow git hooks installed ({', '.join(installed_hooks)})"
                )
    except Exception as e:
        output.warn(f"Could not install git hooks: {e}")

    # Azure DevOps PR provider needs the az 'azure-devops' extension; provision
    # it at adopt time so the first create-pr doesn't fail on a fresh machine.
    try:
        _ensure_ado_pr_cli(cfg.load_config(config_path).default_repo.pr)
    except Exception:
        pass

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


def _resolve_copilot() -> str | None:
    """Resolve a runnable Copilot CLI executable, or ``None``.

    Thin delegate to the shared resolver in ``reconcile`` (used by both this
    update flow and the session-start provision path). Never installs Copilot --
    see ``reconcile.resolve_copilot`` (dotfiles#990).
    """
    from . import reconcile as _rc
    return _rc.resolve_copilot()


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
            [_resolve_copilot() or "copilot", "plugin", "update", plugin_ref],
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
    except OSError:
        # FileNotFoundError (no `copilot` on PATH) or PermissionError /
        # ENOEXEC -- e.g. under WSL interop a bare `copilot` resolves to a
        # non-executable Windows entry (dotfiles#990). Skip, don't crash.
        output.warn("'copilot' CLI not found or not executable -- "
                    "skipping plugin update")
    except subprocess.TimeoutExpired:
        output.warn("Plugin update timed out -- continuing with installed version")

    # Step 1b -- refresh EVERY registered copilot-extensions plugin payload.
    # All plugin payloads are updated first (this step), then service payloads /
    # runtimes below. This is the fix for the "phantom deploy" (aperture-labs
    # #2554): payload-only plugins (runtimeScope: none) such as context-handoff
    # were never touched by update, so they stayed on a stale version. The
    # agent-worktrees payload update above stays first (it provides this flow);
    # read_enabled_plugins already excludes agent-worktrees so it is not
    # double-updated here.
    _update_registered_plugins()

    # Step 2 -- find the installed plugin directory
    plugin_dir = _find_installed_plugin_dir()
    if not plugin_dir:
        output.err("Cannot find installed plugin directory")
        output.err("Expected at ~/.copilot/installed-plugins/copilot-extensions/"
                    "agent-worktrees/")
        return 1

    output.info(f"Plugin source: {plugin_dir}")

    # Step 3 -- run the platform-specific installer from the plugin dir,
    # unless the deployed runtime already matches the freshly-pulled payload.
    # The devN version tracks commit content, so equal versions mean the
    # runtime is already current -- skip the (slow) re-deploy for speed
    # (dotfiles#443's "quick skip"). --force always re-deploys; an unknown
    # deployed version (no deploy-manifest) counts as drift and deploys, so we
    # never skip on uncertainty.
    from . import reconcile as _reconcile

    plat = cfg.detect_platform()
    force = getattr(args, "force", False)
    aw_payload_ver = _reconcile.payload_version(plugin_dir)
    aw_deployed_ver = _reconcile.runtime_deployed_version("agent-worktrees")
    version_match = (
        (not force) and aw_payload_ver and aw_payload_ver == aw_deployed_ver
    )
    # The bin/ hook shims deploy independently of the runtime version, so a
    # version match is NOT proof they are current -- a payload can add a new
    # shim (resolve-runtime.ps1, #1106) or a partial deploy can bump the runtime
    # slot without redeploying them, silently breaking the sessionStart reseed
    # (empty Mux status bar, dotfiles #1171). Don't quick-skip on shim drift.
    hooks_drifted = bool(version_match) and _reconcile.hook_shims_drifted(plugin_dir)
    if version_match and not hooks_drifted:
        output.ok(f"Runtime already at {aw_deployed_ver} -- skipping installer "
                  "(use --force to re-deploy)")
        # The full installer is skipped, but LIVE Windows Terminal state drifts
        # independently of our version -- a fragment profile WT is hiding, or
        # accumulated generatedProfiles cruft. That reconciliation must NOT be
        # gated behind the version skip, or a plain `update` on an already-current
        # runtime would never repair WT state (the very thing this flow exists to
        # do). Run the narrow, fast terminal refresh (regenerate fragment +
        # Sync-TerminalState heal/reclaim) instead; it is Windows-only and cheap.
        if plat == "windows":
            _refresh_terminal_profiles()
    else:
        if hooks_drifted:
            output.warn(
                f"Runtime already at {aw_deployed_ver}, but deployed hook shims "
                "have drifted from the payload -- re-deploying (bin/ hook shims "
                "deploy independently of the runtime version; dotfiles #1171)")
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

    # Step 3.5 -- reconcile EVERY registered project binstub against the current
    # template, no matter which project's lens invoked `update`. Step 3's installer
    # and the global stub only refresh the current project + the global launcher, so
    # a binstub-template migration (e.g. the Windows .venv -> current-version marker
    # move, #1085/#1106) would otherwise leave *other* projects' binstubs stale until
    # each was re-registered. `update` owns fleet-wide binstub health.
    try:
        inst.reconcile_binstubs()
    except Exception as e:  # noqa: BLE001 -- a stub refresh must never fail update
        output.warn(f"Binstub reconcile skipped: {e}")

    # Step 4 -- update registered sibling modules (agent-bridge, etc.)
    skip_modules = getattr(args, "skip_modules", None)
    _update_modules(plugin_dir, plat, skip_modules, force=force)

    # Step 4.5 -- rebuild the RUNTIME for every other enabled plugin whose
    # runtime the steps above never touch. ``_update_modules`` only runs the
    # installer for ``modules.json`` (agent-bridge); agent-worktrees itself is
    # Step 3. So a runtime plugin like agent-codespaces gets its PAYLOAD
    # refreshed (Step 1) but its versioned venv rebuild is otherwise deferred to
    # a later launch reconcile -- meaning `update` (and even `--force`) could
    # leave it serving stale code under a mismatched version (dotfiles #1025).
    # This closes that gap: reconcile those runtimes here, version-keyed by
    # default and force-reinstalled under ``--force``.
    _reconcile_registered_runtimes(plugin_dir, plat, skip_modules, force=force)

    # Step 5 -- fast-forward the managed repo anchor(s) so in-repo config
    # bindings deploy alongside the plugin update (not just on next launch).
    if not getattr(args, "no_anchor_sync", False):
        _fast_forward_project_anchors()

    return 0


def _refresh_marketplace(marketplace: str) -> None:
    """Refresh the local marketplace catalog (best-effort, non-fatal).

    ``copilot plugin update <name>`` resolves the target version from the
    locally cached marketplace catalog, so a stale catalog can hide a
    freshly-published version. Refreshing the catalog once before the
    per-plugin loop makes new versions visible. Any failure (offline,
    timeout, missing CLI) warns and continues.
    """
    try:
        r = subprocess.run(
            [_resolve_copilot() or "copilot", "plugin", "marketplace", "update", marketplace],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            output.warn("Marketplace refresh returned non-zero -- continuing")
    except OSError:
        output.warn("'copilot' CLI not found or not executable -- "
                    "skipping marketplace refresh")
    except subprocess.TimeoutExpired:
        output.warn("Marketplace refresh timed out -- continuing")


def _update_one_plugin_payload(name: str, marketplace: str) -> str:
    """Update (or install) a single copilot-extensions plugin payload.

    Idempotent and network-facing. Chooses ``update`` when the payload is
    already installed, else ``install``; on a failed ``update`` for a plugin
    that is not actually installed it falls back to ``install``. Never raises:
    a single plugin's failure is reported as a short status string so the
    caller can continue with the rest.

    Returns one of ``"OK"``, ``"OK (installed)"``, or an error description.
    """
    from . import reconcile

    ref = f"{name}@{marketplace}"
    installed = reconcile.installed_payload_dir(name) is not None
    verb = "update" if installed else "install"
    try:
        r = subprocess.run(
            [_resolve_copilot() or "copilot", "plugin", verb, ref],
            capture_output=True, text=True, timeout=120,
        )
    except OSError:
        output.warn("'copilot' CLI not found or not executable -- "
                    "skipping plugin payload update")
        return "copilot CLI not found or not executable"
    except subprocess.TimeoutExpired:
        output.warn(f"Plugin {verb} for {name} timed out -- continuing")
        return "timed out"

    if r.returncode == 0:
        for line in r.stdout.strip().splitlines():
            output.ok(line)
        return "OK" if installed else "OK (installed)"

    # Non-zero. If we tried to update but the plugin was not actually
    # installed, fall back to a fresh install.
    if installed:
        output.warn(f"Plugin update for {name} returned non-zero "
                    f"(continuing with installed version)")
        return f"update exited {r.returncode}"

    output.info(f"Plugin install for {name} returned non-zero -- retrying")
    try:
        r2 = subprocess.run(
            [_resolve_copilot() or "copilot", "plugin", "install", ref],
            capture_output=True, text=True, timeout=120,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        output.warn(f"Plugin install retry for {name} failed: {exc}")
        return "install retry failed"
    if r2.returncode == 0:
        for line in r2.stdout.strip().splitlines():
            output.ok(line)
        return "OK (installed)"
    output.warn(f"Plugin install for {name} returned non-zero (skipping)")
    return f"install exited {r2.returncode}"


def _update_registered_plugins() -> None:
    """Update every copilot-extensions plugin registered for the managed repo(s).

    ``update`` must refresh EVERY registered plugin's payload -- including
    payload-only plugins (``runtimeScope: none``) such as ``context-handoff``,
    which the module/runtime steps never touch. This enumerates the plugins
    enabled in each managed repo's settings
    (``reconcile.read_enabled_plugins``, the authoritative registered list,
    which already excludes ``agent-worktrees`` itself), refreshes the
    marketplace catalog once, then runs ``copilot plugin update`` (or
    ``install`` when missing) for each. Payloads only -- runtimes are handled
    afterward by ``_update_modules`` and the anchor reconcile.

    Best-effort and idempotent: a single plugin's failure warns and continues;
    an already-current plugin is a no-op. No resolvable project config (e.g. a
    generic install) is a silent no-op.
    """
    from . import reconcile

    try:
        config = cfg.load_config()
    except Exception:
        # No resolvable project config -- nothing to enumerate.
        return

    repos = config.repos or {}
    if not repos:
        return

    names: set[str] = set()
    seen_anchors: set[str] = set()
    for repo in repos.values():
        anchor = repo.anchor
        if not anchor or anchor in seen_anchors:
            continue
        seen_anchors.add(anchor)
        try:
            names.update(reconcile.read_enabled_plugins(Path(anchor)))
        except Exception as exc:
            output.warn(f"Could not read enabled plugins from {anchor}: {exc}")

    if not names:
        return

    output.header("Updating Registered Plugin Payloads")
    _refresh_marketplace(reconcile.MARKETPLACE)

    results: list[tuple[str, str]] = []
    for name in sorted(names):
        results.append((name, _update_one_plugin_payload(name, reconcile.MARKETPLACE)))

    output.header("Plugin Payload Update Summary")
    for name, status in results:
        if status.startswith("OK"):
            output.ok(name if status == "OK" else f"{name} ({status})")
        else:
            output.warn(f"{name}: {status}")


def _module_names(plugin_dir: Path) -> set[str]:
    """The sibling-module names handled by :func:`_update_modules` (``modules.json``).

    These runtimes (e.g. agent-bridge) are deployed by the module step, so the
    registered-runtime reconcile below must exclude them to avoid a redundant
    (and, for a daemon, disruptive) second install. Best-effort: an unreadable
    manifest yields the empty set."""
    manifest = plugin_dir / "modules.json"
    try:
        data = json.loads(manifest.read_text())
    except Exception:
        return set()
    out: set[str] = set()
    for mod in data.get("modules", []) or []:
        name = mod.get("name")
        if name:
            out.add(str(name))
    return out


def _reconcile_registered_runtimes(
    plugin_dir: Path,
    platform: str,
    skip_modules: list[str] | None = None,
    *,
    force: bool = False,
) -> None:
    """Rebuild the runtime venv of every enabled plugin the other steps skip.

    ``update`` runs a runtime installer only for agent-worktrees (Step 3) and
    the ``modules.json`` services (``_update_modules``). Every other enabled
    runtime plugin -- agent-codespaces, agent-containers, agent-dispatch, … --
    only gets its PAYLOAD refreshed, and its versioned venv rebuild is deferred
    to a later launch reconcile (which is version-keyed). So a plain ``update``
    could leave such a plugin serving stale code, and ``--force`` -- which users
    reach for precisely to fix that -- never reached them either (dotfiles
    #1025). This reconciles them here, mirroring the launch-path reconciler:

    * **version-keyed** by default -- run the plugin's ``scripts/install.*
      update`` only when its deployed runtime version differs from its freshly
      refreshed payload version (or no runtime is deployed yet);
    * **forced** under ``--force`` -- run the installer regardless of version,
      so a same-version content drift (a dev checkout, or a marketplace artifact
      whose stamp lagged) is repaired. The installer force-reinstalls the
      package, so fresh bytes always land.

    Best-effort and idempotent: a plugin's failure warns and continues; no
    resolvable config (a generic install) is a silent no-op. Payload-only
    plugins (``runtimeScope: none``) and the module/self runtimes are skipped.
    """
    from . import reconcile

    try:
        config = cfg.load_config()
    except Exception:
        return
    repos = config.repos or {}
    if not repos:
        return

    # skip_modules semantics mirror _update_modules: [] => skip all.
    if skip_modules is not None and len(skip_modules) == 0:
        return

    names: set[str] = set()
    seen_anchors: set[str] = set()
    for repo in repos.values():
        anchor = repo.anchor
        if not anchor or anchor in seen_anchors:
            continue
        seen_anchors.add(anchor)
        try:
            names.update(reconcile.read_enabled_plugins(Path(anchor)))
        except Exception:
            continue

    # Exclude runtimes handled elsewhere (module services + agent-worktrees) and
    # any explicitly skipped names.
    excluded = _module_names(plugin_dir) | {"agent-worktrees"}
    if skip_modules:
        excluded |= set(skip_modules)
    names -= excluded
    if not names:
        return

    results: list[tuple[str, str]] = []
    for name in sorted(names):
        results.append(
            (name, _reconcile_one_runtime(name, platform, force=force))
        )

    acted = [(n, s) for n, s in results if s not in ("SKIPPED (current)", "payload-only")]
    if acted:
        output.header("Registered Runtime Reconcile")
        for name, status in acted:
            if status.startswith("OK"):
                output.ok(f"{name} ({status})")
            else:
                output.warn(f"{name}: {status}")


def _reconcile_one_runtime(name: str, platform: str, *, force: bool) -> str:
    """Reconcile a single registered plugin's runtime; returns a status string.

    Never raises. ``"payload-only"`` when the plugin has no runtime;
    ``"SKIPPED (current)"`` when version-keyed and already current; ``"OK …"``
    on a run; else a short error."""
    from . import reconcile

    pdir = reconcile.installed_payload_dir(name)
    if pdir is None:
        return "payload not installed"
    scope = reconcile.manifest_runtime_scope(pdir) or "none"
    if scope == "none":
        return "payload-only"

    if not force:
        pver = reconcile.payload_version(pdir)
        dver = reconcile.runtime_deployed_version(name)
        if pver and dver and reconcile._versions_equal(dver, pver):
            return "SKIPPED (current)"

    if platform == "windows":
        installer = pdir / "scripts" / "install.ps1"
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if not shell:
            return "powershell not found"
        argv = [shell, "-NoProfile", "-ExecutionPolicy", "Bypass",
                "-File", str(installer), "update"]
    else:
        installer = pdir / "scripts" / "install.sh"
        argv = ["bash", str(installer), "update"]
    if not installer.exists():
        return "installer not found"

    output.header(f"Reconciling Runtime: {name}"
                  + (" (forced)" if force else ""))
    try:
        r = subprocess.run(argv, cwd=pdir, timeout=300)
    except subprocess.TimeoutExpired:
        return "timed out"
    except Exception as exc:  # never abort the loop
        return str(exc)[:120]
    return "OK" if r.returncode == 0 else f"installer exited {r.returncode}"


def _fast_forward_project_anchors() -> None:
    """Fast-forward each managed repo's anchor to its upstream default branch.

    ``update`` refreshes the plugin payload from the marketplace, but the repo
    **anchor checkout** -- the source of truth for in-repo
    ``.agent-worktrees/config.yaml`` bindings -- is otherwise only synced on the
    next picker launch or a manual ``git pull``. That lag lets a freshly-rolled
    command silently no-op on a machine whose anchor still predates a new config
    binding. Closing it here makes in-repo config deploy alongside the plugin.

    Strictly a fast-forward, and only when the anchor is **on the default
    branch, clean, and behind** -- mirroring ``git_ops.fast_forward_worktree``'s
    safety. A dirty, ahead, diverged, or detached anchor, or one checked out on
    a non-default branch, is left untouched. Best-effort: any failure (no
    config, offline fetch) is non-fatal and never aborts the update.
    """
    try:
        config = cfg.load_config()
    except Exception:
        # No resolvable project config (e.g. generic install) -- nothing to do.
        return

    repos = config.repos or {}
    if not repos:
        return

    output.header("Syncing repo anchor(s)")
    seen: set[str] = set()
    for repo in repos.values():
        anchor = repo.anchor
        if not anchor or anchor in seen:
            continue
        seen.add(anchor)

        anchor_path = Path(anchor)
        if not (anchor_path / ".git").exists():
            output.warn(f"Anchor not checked out, skipping: {anchor}")
            continue

        # Only ever advance an anchor that is *on* its default branch. A
        # non-default checkout is intentional operator state -- never retarget
        # it (fast_forward_worktree alone would ff a 0-ahead feature branch).
        current = git_ops.current_branch(anchor_path)
        if current is None:
            output.info(f"{anchor}: detached HEAD -- skipped")
            continue
        if current != repo.default_branch:
            output.info(
                f"{anchor}: on '{current}', not '{repo.default_branch}' -- skipped"
            )
            continue

        ff = git_ops.fast_forward_worktree(
            anchor_path,
            remote=repo.remote,
            default_branch=repo.default_branch,
            do_fetch=True,
        )
        if ff.updated:
            output.ok(
                f"{anchor}: fast-forwarded {ff.behind} commit(s) to "
                f"{repo.remote}/{repo.default_branch}"
            )
        elif ff.reason in ("up-to-date",):
            output.ok(f"{anchor}: up to date")
        elif ff.reason in ("dirty", "ahead", "diverged"):
            output.info(f"{anchor}: {ff.reason} -- left untouched")
        else:
            output.info(f"{anchor}: not synced ({ff.reason})")


def _self_entry_present(config: cfg.Config) -> bool:
    """Whether this machine has a self-entry in the anchor's machines.yaml.

    Best-effort: if the registry can't be read (missing/malformed) we return
    ``True`` so the heal (and its network fetch) never fires on an unrelated I/O
    error -- only a genuinely absent self-entry should trigger a pull-forward.
    """
    try:
        entries = cfg.load_machines_yaml(config.default_repo.anchor)
    except Exception:
        return True
    if not entries:
        return True
    return cfg.find_machine_entry(entries, socket.gethostname()) is not None


def _heal_stale_anchor_if_self_missing(config: cfg.Config) -> cfg.Config:
    """Break the launch catch-22 for a stale anchor missing this machine.

    The interactive picker parses the anchor's ``machines.yaml`` to identify
    this machine. If the anchor checkout is stale and predates this machine's
    onboarding there is no self-entry and the picker crashes -- yet the anchor
    fast-forward that would add the entry otherwise runs only *after* a
    successful resolve (the ``update`` path). That is a catch-22: the config can
    never self-heal because the crash aborts the launch before the pull-forward.

    Fail-safe: when this machine's self-entry is missing, best-effort
    fast-forward the project anchor(s) *before* the picker reads the registry,
    then reload config so the picker sees the fresh roster. Strictly a
    fast-forward of a clean, on-default, behind anchor (see
    ``_fast_forward_project_anchors``); any failure (offline, dirty, ahead, or a
    still-absent entry afterward) is non-fatal -- the defensive local-source
    fallback in ``picker_tui.data_ssh._build_sources`` keeps the picker from
    crashing even if the entry never materializes.

    Returns the (possibly reloaded) config.
    """
    try:
        if _self_entry_present(config):
            return config
        output.info(
            "This machine is not in machines.yaml -- fast-forwarding the "
            "anchor before the picker (stale-config self-heal)."
        )
        _fast_forward_project_anchors()
        return cfg.load_config()
    except Exception as exc:  # never break the launch
        output.warn(f"Anchor self-heal skipped: {exc}")
        return config


def _update_modules(
    plugin_dir: Path,
    platform: str,
    skip_modules: list[str] | None,
    force: bool = False,
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
                [_resolve_copilot() or "copilot", "plugin", "update", plugin_ref],
                capture_output=True, text=True, timeout=120,
            )
            if r.returncode == 0:
                for line in r.stdout.strip().splitlines():
                    output.ok(line)
            else:
                output.warn(f"Plugin update for {name} returned non-zero "
                            f"(continuing with installed version)")
        except OSError:
            output.warn("'copilot' CLI not found or not executable -- "
                        "skipping plugin refresh")
        except subprocess.TimeoutExpired:
            output.warn(f"Plugin update for {name} timed out -- "
                        "continuing with installed version")

        if not module_dir.is_dir():
            output.warn(f"Module '{name}' source not found: {module_dir}")
            results.append((name, "source dir not found"))
            continue

        # Quick skip (dotfiles#443): the payload was just refreshed above, so
        # if the module's deployed runtime version already matches its payload
        # version, its installer is a no-op -- skip the (slow) re-deploy. Only
        # skip when we can positively confirm equality; an unknown deployed
        # version (no deploy-manifest) falls through and re-deploys. --force
        # always re-deploys.
        if not force:
            from . import reconcile as _reconcile
            mod_payload_ver = _reconcile.payload_version(module_dir)
            mod_deployed_ver = _reconcile.runtime_deployed_version(name)
            if mod_payload_ver and mod_payload_ver == mod_deployed_ver:
                output.ok(f"{name} already at {mod_deployed_ver} -- "
                          "skipping installer")
                results.append((name, "SKIPPED (current)"))
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
        # A zero-downtime module (declares "zeroDowntimeUpdate": true in its
        # plugin.json -- e.g. agent-bridge) redeploys via its ZDD cutover on a
        # version bump instead of a disruptive stop-and-swap, so a live daemon
        # hosting sessions is never dropped. Carry the same -ZeroDowntime flag the
        # launch-path reconciler passes (see reconcile.runtime_installer_argv), so
        # a redeploy ALWAYS cuts over regardless of which trigger drove it.
        # Windows only: the switch lives in install.ps1 (install.sh has none), and
        # install.ps1 still downgrades to a classic start when no daemon is
        # running, so passing it is always safe.
        update_args = ["update"]
        if platform == "windows":
            from . import reconcile as _reconcile
            if _reconcile._zero_downtime_update(module_dir):
                update_args.append("-ZeroDowntime")
        output.header(f"Updating Module: {name}")
        try:
            r = subprocess.run(
                [*shell_prefix, *update_args],
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


def cmd_machine_context(args: argparse.Namespace) -> int:
    """sessionStart hook entrypoint: emit machine identity as additionalContext.

    Renders the same machine-identity block that used to be materialized into
    ``machine.instructions.md`` / the nested ``AGENTS.md`` (dotfiles#1056), but
    computes it **live** at session start and emits it via the hook -- so it loads
    under any launch path, with no file on disk. cwd-gated: prints ``{}`` when the
    session is not inside an agent-worktrees-managed project with a resolvable
    ``machines.yaml`` entry, so a globally-loaded plugin never leaks machine
    identity into unrelated repos. Prints exactly one JSON object to stdout.
    """
    import json

    def _empty() -> int:
        print("{}")
        return 0

    # Resolve the active project from cwd the same way ``main()`` does for
    # project-requiring commands -- the robust registry/anchor resolver, not
    # cfg.project_name() (which raises off-project). machine-context is a
    # _NO_PROJECT_COMMANDS entry (so it dispatches even under Bare resume), so we
    # must run that resolution ourselves before load_config().
    try:
        project, _assumed = _resolve_active_project(None)
    except Exception:
        return _empty()
    if not project:
        return _empty()
    try:
        cfg.set_active_project(project)
    except Exception:
        pass

    try:
        config = cfg.load_config()
    except Exception:
        return _empty()

    project = getattr(config, "repo_name", "") or project
    machine = getattr(config, "machine", "") or ""
    if not project or not machine:
        return _empty()

    try:
        repo_dir = config.default_repo.anchor
    except Exception:
        repo_dir = _find_repo_dir()
    if not repo_dir:
        return _empty()

    try:
        registry = cfg.load_machines_yaml(repo_dir)
    except (FileNotFoundError, ValueError):
        return _empty()

    entry = cfg.find_machine_entry(registry, machine)
    if entry is None:
        return _empty()

    try:
        raw = cfg.render_copilot_instructions(entry, project=project).rstrip()
    except Exception:
        return _empty()
    if not raw:
        return _empty()

    print(json.dumps({"additionalContext": raw}))
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
    "owner-ref":     "This worktree's qualified claim ref "
                     "(machine/project/worktree_id[#session]) -- the cross-machine "
                     "holder identity for resource leases; empty if not in a worktree",
    "repo-remote":   "Canonical remote URL of this repo (registry remote; falls "
                     "back to git origin) -- the device-independent repo key",
    "lease-origin":  "Resolved Git-ref lease store origin URL -- the "
                     "**harness identity** shared by every agent of this harness "
                     "(the in-CodeSpace cross-harness fence key); empty if "
                     "unresolvable",
    "pr-enabled":    "Whether PR mode is enabled (true/false)",
    "pr-required":   "Whether PRs are required, blocking direct-to-master (true/false)",
    "pr-provider":   "PR provider (gitea|github|azure-devops) when PR mode is on",
    "pr-profile":    "PR-flow profile: direct|pr-human-merge|pr-agent-merge (check first)",
}


def _resolve_repo_remote(config: cfg.Config, repo: cfg.RepoConfig) -> str:
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


def _resolve_lease_origin() -> str:
    """Resolve the Git-ref lease store origin URL -- the **harness identity**.

    This is the pushable store repo URL that ``lease_config`` derives (the
    ``AGENT_WORKTREES_LEASE_ORIGIN`` override, else the bound control-plane /
    knowledge repo's origin, else the current project's default-repo remote).
    Because every agent of one harness resolves the **same** origin, it is the
    natural cross-harness identity for the in-CodeSpace lockfile fence
    (git-ref-resource-leases Phase 4): a marker written by a *different* harness
    carries a different origin. Guarded -- any resolution failure yields ``""``
    so a consumer degrades to no fence rather than erroring.
    """
    try:
        from . import lease_config
        return lease_config.load_lease_settings().origin
    except Exception:
        return ""


def _pr_flow_profile(repo: cfg.RepoConfig):
    """Derive this repo's PR-flow profile from its config (pure, no network).

    Wraps :func:`pr_contract.classify_pr_flow` with the repo's ``pr`` binding so
    every surface (``get pr-profile``, ``pr-status``, ``pr-merge``) reports the
    same profile: ``direct`` | ``pr-human-merge`` | ``pr-agent-merge`` |
    ``pr-self-merge``.
    """
    from . import pr_contract as pc

    prc = repo.pr
    return pc.classify_pr_flow(
        enabled=prc.enabled,
        required=prc.required,
        provider=prc.provider,
        automerge_label=getattr(prc, "automerge_label", ""),
        reviewer=getattr(prc, "reviewer", ""),
        review_blocking=getattr(prc, "review_blocking", False),
        review_latency_hint=getattr(prc, "review_latency_hint", ""),
        self_approve=getattr(prc, "self_approve", False),
        merge_actor=getattr(prc, "merge_actor", ""),
        conflict_retriggers_review=getattr(prc, "conflict_retriggers_review", True),
        branch_update_strategy=getattr(prc, "branch_update_strategy", "rebase"),
        merge_strategy=getattr(prc, "merge_strategy", "squash"),
        prefer_auto_merge=getattr(prc, "prefer_auto_merge", True),
    )


def _pr_reminder_for(
    config, verb: str, *, ok: bool = True, state: str = "", reason: str = "",
):
    """Build this repo's stay-on-rails PR reminder for ``verb`` (or ``None``).

    Fail-open: any error (no repo, classification failure) yields ``None`` so a
    reminder never perturbs the verb it rides along with.
    """
    from . import pr_contract as pc

    try:
        flow = _pr_flow_profile(config.default_repo)
        return pc.pr_reminder(flow, verb, state, ok=ok, reason=reason)
    except Exception:
        return None


def _emit_pr_reminder(reminder, *, use_json: bool, result: dict | None = None) -> None:
    """Surface a PR reminder: as a ``reminder`` node in ``result`` (JSON mode)
    or as a short block on stderr (human mode). No-op when ``reminder`` is None.
    """
    if reminder is None:
        return
    if use_json:
        if result is not None:
            result["reminder"] = reminder.as_dict()
    else:
        print(reminder.text(), file=sys.stderr)


def cmd_get(args: argparse.Namespace) -> int:
    """Query project paths and config values -- machine-readable output."""
    key: str = args.key

    if key == "keys":
        for k, desc in _GET_KEYS.items():
            print(f"{k:16s}  {desc}")
        return 0

    # #4098 binding-first: activate the scoped bare-resume session binding BEFORE
    # load_config, so a session launched with cwd=HOME (bare resume) still
    # resolves its project from the AGENT_WORKTREES_BIND_* env the launcher set,
    # rather than failing or defaulting. Best-effort: no binding -> no-op.
    session_id = getattr(args, "session_id", None)
    session_wt_id = None
    if session_id:
        session_wt_id = _activate_session_binding(session_id)

    try:
        config = cfg.load_config()
    except Exception as e:
        output.err(f"Cannot load config: {e}")
        return 1

    repo = config.default_repo

    # Current worktree root: resolve purely from CWD (git-like), via the dev107
    # resolver. Empty when the caller is at the anchor or outside any worktree.
    wt_id = _infer_worktree_id_from_cwd(config)
    # Binding-first fallback: cwd is HOME under bare resume, so cwd inference
    # finds nothing even though the session IS bound to a worktree. Resolve it
    # from the session id -- the scoped binding first, then the session->worktree
    # registry (now that a project is active) -- authoritative, not a cwd guess.
    if not wt_id and session_id:
        if not session_wt_id:
            try:
                session_wt_id = tracking.find_worktree_id_by_session(session_id)
            except Exception:
                session_wt_id = None
        wt_id = session_wt_id
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
        "owner-ref":    (
            tracking.format_claim_ref(config.machine, config.repo_name, wt_id,
                                      session_id)
            if wt_id else ""
        ),
        "repo-remote":  _resolve_repo_remote(config, repo),
        "lease-origin": _resolve_lease_origin(),
        "pr-enabled":    "true" if repo.pr.enabled else "false",
        "pr-required":   "true" if repo.pr.required else "false",
        "pr-provider":   repo.pr.provider if repo.pr.enabled else "",
        "pr-profile":    _pr_flow_profile(repo).profile,
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
    "run": "run",
    "claims": "claims",
    "claimant-liveness": "claimant-liveness",
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
    print("  pr-ready [id]          Move a PR out of draft (ready-for-review)", file=out)
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
    print("     [--account LOGIN] [--tags a,b] [--contributing PATH]")
    print("     [--agent|--no-agent]")
    print("  remove <name>                       Remove a repo from the registry")
    print("  clone <remote> [--name N]           Clone a repo to srcroot and register")
    print("     [--target PATH]")
    print("  srcroot [--set PATH]                Show or set the source root")
    print("     [--platform windows|wsl|linux]")
    print("  migrate [--default-class C]         Import legacy ~/.git-repos")
    print("  status [--tag T] [--class C]        Show branch/dirty/ahead-behind")
    print("  sync [--tag T] [--class C]          Fetch + fast-forward (skips dirty)")
    print("  doctor [--fix] [--json]             Reconcile projects.yaml <-> repos.yaml")
    print("  account [list|set <owner> <login>|unset <owner>]")
    print("                                      Decoupled owner->gh-login map (account_map)")
    print("  account-for [owner|owner/name]      Print the resolved gh login (exit 1 if none)")
    print("  gh [owner|owner/name] [--] <args>   Run gh under that repo's account (token-inject)")
    print("  allow-edits <repo> --reason <why>   Break-glass: temporarily allow direct edits")
    print("     [--minutes N] | --list | <repo> --revoke   to a guarded repo (default 10m, max 60m)")
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


def _clarify_registration_account(
    remote: str, name: str, explicit_account: str = "",
) -> None:
    """Ensure a repo's gh account is unambiguous at register/adopt/add time.

    When the account resolves only by falling back to a github *owner* that is
    not an authenticated ``gh`` account (i.e. an org), interactively prompt for
    the account and persist it as an ``account_map`` entry; headless, warn with
    the exact remedy. No-op when the account already resolves (explicit / map /
    sibling) or the remote is non-GitHub. See dotfiles #537.
    """
    from . import git_ops, repos

    try:
        res = repos.resolve_registration_account(remote, explicit_account)
    except Exception:
        return

    if not res.needs_clarify:
        # Surface a resolved non-owner account for transparency.
        if res.source in ("account_map", "sibling") and res.login:
            output.info(f"  account:  {res.login} (via {res.source})")
        return

    owner = res.owner or ""
    accounts = git_ops.list_gh_accounts()
    interactive = sys.stdin is not None and sys.stdin.isatty()

    if not interactive:
        remedy = f"repos account set {owner} <login>"
        extra = f"  (authenticated: {', '.join(accounts)})" if accounts else ""
        output.warn(
            f"'{name}': owner '{owner}' is not an authenticated gh account "
            f"(likely an org); its gh/CodeSpace ops would use an unusable "
            f"derived login. Pin one: {remedy}{extra}"
        )
        return

    output.warn(
        f"Repo owner '{owner}' is not an authenticated gh account (likely an "
        f"org). Which gh account should repos under '{owner}' use?"
    )
    for i, a in enumerate(accounts, 1):
        print(f"    {i}) {a}")
    if accounts:
        print("    (enter a number or a login; blank to skip)")
    try:
        choice = input(f"  Account for '{owner}': ").strip()
    except EOFError:
        choice = ""
    if not choice:
        output.warn(
            f"Skipped -- set later with: repos account set {owner} <login>"
        )
        return
    if choice.isdigit() and accounts:
        idx = int(choice) - 1
        if 0 <= idx < len(accounts):
            choice = accounts[idx]
    repos.set_account_map(owner, choice)


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
                        "account": e.account,
                        "resolved_account": repos.resolve_account(e),
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
                acct = repos.resolve_account(e)
                if acct:
                    src = "explicit" if e.account else "derived"
                    print(f"  {'':25} {'':20} account: {acct} ({src})")
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
                "[--default-branch B] [--account LOGIN] [--tags a,b] "
                "[--contributing PATH] [--agent|--no-agent]"
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
        account = _opt("--account") or ""
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
            account=account,
            agent=agent_flag,
        )
        _clarify_registration_account(remote, name, account)
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
        if entry:
            _clarify_registration_account(entry.remote, entry.name, entry.account)
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

    if sub == "account-for":
        # Resolve the effective gh account for an owner or owner/name slug.
        # Prints the login on stdout (exit 0); prints nothing + exit 1 when no
        # preference resolves (caller then uses ambient auth). The programmatic
        # primitive agent-codespaces (and other tools) shell out to. The slug is
        # inferred from the active project when omitted.
        target = rest[0] if rest and not rest[0].startswith("-") else None
        json_out = "--json" in rest
        if not target:
            target = _infer_active_repo_slug(cfg.load_config())
        if not target:
            output.err("Usage: repos account-for [owner|owner/name]  "
                       "(inferred from the active project when omitted)")
            return 1
        login = repos.account_for_github_slug(target)
        # Suppress the bare-owner echo: when the owner isn't a github owner or
        # maps to itself with no catalog/registry backing, that's still a valid
        # login (owner==account). Only treat empty as "no preference".
        if json_out:
            _json_output({"target": target, "account": login})
            return 0 if login else 1
        if login:
            print(login)
            return 0
        return 1

    if sub == "gh":
        # Run `gh` against a repo under the account that owns it, via token
        # injection -- race-safe on a shared box where the active gh account is
        # global per-machine. Usage: repos gh [owner/name] [--] <gh args>
        # The repo is inferred from the active project when the first token is
        # `--` (an explicit "no target" marker), so `repos gh -- issue list`
        # works from inside the repo without naming it.
        args = list(rest)
        target = None
        if args and args[0] == "--":
            # Explicit "no target" -> infer; everything after -- is gh args.
            gh_args = args[1:]
        elif args and not args[0].startswith("-"):
            target = args[0]
            gh_args = args[1:]
            if gh_args and gh_args[0] == "--":
                gh_args = gh_args[1:]
        else:
            # Leading flag (e.g. `repos gh --json ...`): infer the target, treat
            # the rest as gh args.
            gh_args = args
        if target is None:
            target = _infer_active_repo_slug(cfg.load_config())
        if not target or not gh_args:
            output.err("Usage: repos gh [owner|owner/name] [--] <gh args...>  "
                       "(repo inferred from the active project when omitted)")
            return 1
        if shutil.which("gh") is None:
            output.err("gh CLI not found on PATH")
            return 1
        env, login, injected = _gh_env_for_repo(target)
        if login and not injected:
            output.warn(
                f"could not mint a gh token for '{login}'; using ambient auth"
            )
        return subprocess.run(["gh", *gh_args], env=env).returncode

    if sub == "account":
        # Manage the decoupled owner->login map (account_map in repos.yaml).
        acsub = rest[0] if rest else "list"
        acrest = rest[1:] if rest else []
        if acsub == "list":
            registry = repos.read_registry()
            json_out = "--json" in acrest
            if json_out:
                _json_output({"account_map": dict(registry.account_map)})
                return 0
            if not registry.account_map:
                print("No account_map entries.")
                print("Add one with: repos account set <owner> <login>")
                return 0
            output.header("Account map (owner -> gh login)")
            for owner in sorted(registry.account_map.keys()):
                print(f"  {owner:<24} -> {registry.account_map[owner]}")
            return 0
        if acsub == "set":
            if len(acrest) < 2:
                output.err("Usage: repos account set <owner> <login>")
                return 1
            repos.set_account_map(acrest[0], acrest[1])
            return 0
        if acsub in ("unset", "remove", "rm"):
            if not acrest:
                output.err("Usage: repos account unset <owner>")
                return 1
            if repos.unset_account_map(acrest[0]):
                return 0
            output.err(f"No account_map entry for '{acrest[0]}'")
            return 1
        output.err(f"Unknown 'repos account' subcommand: {acsub}")
        output.info("Usage: repos account [list|set <owner> <login>|unset <owner>]")
        return 1

    if sub == "allow-edits":
        from . import allow_edits

        json_out = "--json" in rest
        do_list = "--list" in rest
        do_revoke = "--revoke" in rest
        reason = None
        minutes = None
        positional: list[str] = []
        i = 0
        while i < len(rest):
            tok = rest[i]
            if tok == "--reason" and i + 1 < len(rest):
                reason = rest[i + 1]
                i += 2
                continue
            if tok == "--minutes" and i + 1 < len(rest):
                minutes = rest[i + 1]
                i += 2
                continue
            if tok in ("--json", "--list", "--revoke"):
                i += 1
                continue
            positional.append(tok)
            i += 1

        # --list: show active grants (no repo needed)
        if do_list:
            grants = allow_edits.list_active()
            if json_out:
                _json_output({"grants": [
                    {"repo": g.repo, "expires_at_ms": g.expires_at_ms,
                     "remaining_seconds": g.remaining_seconds, "minutes": g.minutes,
                     "reason": g.reason, "session": g.session}
                    for g in grants
                ]})
            elif not grants:
                print("No active edit grants.")
            else:
                output.header("Active edit grants (break-glass)")
                for g in grants:
                    mins = max(0, g.remaining_seconds // 60)
                    print(f"  {g.repo:<25} {mins}m left   {g.reason}")
            return 0

        repo = positional[0] if positional else None
        if not repo:
            output.err(
                "Usage: repos allow-edits <repo> --reason <why> [--minutes N] "
                "| --list | <repo> --revoke")
            return 1

        # --revoke <repo>
        if do_revoke:
            removed = allow_edits.revoke(repo)
            if json_out:
                _json_output({"repo": repo, "revoked": removed})
            elif removed:
                output.ok(f"Revoked edit grant for '{repo}'.")
            else:
                output.info(f"No active edit grant for '{repo}'.")
            return 0

        # grant: requires a real reason
        if not reason or len(reason.strip()) < allow_edits.MIN_REASON_LEN:
            msg = (f"repos allow-edits requires --reason (>= {allow_edits.MIN_REASON_LEN} chars) "
                   "explaining why delegation cannot be used.")
            return _json_error(msg) if json_out else (output.err(msg) or 1)

        entry = repos.find_repo(repo)
        g = allow_edits.grant(repo, reason.strip(), minutes)
        note = "" if entry else (
            f" (note: '{repo}' is not in the repos registry — nothing may be guarding it)")
        if json_out:
            _json_output({"repo": repo, "expires_at_ms": g.expires_at_ms,
                          "minutes": g.minutes, "reason": g.reason,
                          "known": entry is not None})
        else:
            output.warn(
                f"BREAK-GLASS: direct edits to '{repo}' allowed for "
                f"{g.minutes}m — reason: {g.reason}")
            expires = datetime.fromtimestamp(g.expires_at_ms / 1000).strftime("%H:%M:%S")
            output.info(
                f"Grant expires at {expires}. Prefer delegation for anything "
                f"the repo's own agent could do.{note}")
        return 0

    output.err(f"Unknown repos subcommand: {sub}")
    _repos_usage()
    return 1


def _accounts_usage() -> None:
    try:
        project = cfg.project_name()
    except Exception:
        project = "agent-worktrees"
    print(f"Usage: {project} accounts <command>")
    print()
    print("Catalog of gh account identities and their (re)login flows")
    print("(~/.agent-worktrees/accounts.yaml). The owner->account MAP lives in")
    print("repos.yaml (see 'repos account'); this catalog describes the logins")
    print("that map points at -- host, expected scopes, and how to (re)login.")
    print()
    print("Commands:")
    print("  list                                List catalogued accounts")
    print("  show <login>                        Show one account's details")
    print("  set <login> [--host H] [--scopes a,b] [--login-flow CMD] [--notes T]")
    print("                                      Add or update an account entry")
    print("  remove <login>                      Remove an account entry")
    print()
    print("Examples:")
    print(f"  {project} accounts set ThomasMichon --scopes codespace,repo,workflow \\")
    print("      --login-flow 'gh auth login -h github.com'")
    print(f"  {project} accounts list")


def cmd_accounts_dispatch(argv: list[str]) -> int:
    """Route the top-level ``accounts`` catalog subcommands."""
    from . import accounts

    if argv and argv[0] in ("--help", "-h"):
        _accounts_usage()
        return 0
    sub = argv[0] if argv else "list"
    rest = argv[1:] if argv else []
    if "--help" in rest or "-h" in rest:
        _accounts_usage()
        return 0

    def _opt(flag: str) -> str | None:
        if flag in rest:
            idx = rest.index(flag)
            if idx + 1 < len(rest):
                return rest[idx + 1]
        return None

    if sub == "list":
        entries = accounts.list_accounts()
        if "--json" in rest:
            _json_output({"accounts": [
                {"login": e.login, "host": e.host, "scopes": e.scopes,
                 "login_flow": e.login_flow, "notes": e.notes}
                for e in entries]})
            return 0
        if not entries:
            print("No accounts catalogued.")
            print("Add one with: accounts set <login> [--scopes ...] [--login-flow ...]")
            return 0
        output.header("Accounts catalog")
        for e in entries:
            scopes = ",".join(e.scopes) if e.scopes else "(none)"
            print(f"  {e.login:<20} host={e.host}  scopes={scopes}")
            if e.login_flow:
                print(f"  {'':20} login: {e.login_flow}")
        return 0

    if sub == "show":
        if not rest or rest[0].startswith("-"):
            output.err("Usage: accounts show <login>")
            return 1
        e = accounts.find_account(rest[0])
        if not e:
            output.err(f"No account '{rest[0]}' in accounts.yaml")
            return 1
        if "--json" in rest:
            _json_output({"login": e.login, "host": e.host, "scopes": e.scopes,
                          "login_flow": e.login_flow, "notes": e.notes})
            return 0
        output.header(f"Account: {e.login}")
        print(f"  host:       {e.host}")
        print(f"  scopes:     {','.join(e.scopes) if e.scopes else '(none)'}")
        print(f"  login_flow: {e.login_flow or '(none)'}")
        if e.notes:
            print(f"  notes:      {e.notes}")
        return 0

    if sub == "set":
        if not rest or rest[0].startswith("-"):
            output.err("Usage: accounts set <login> [--host H] [--scopes a,b] "
                       "[--login-flow CMD] [--notes T]")
            return 1
        login = rest[0]
        raw_scopes = _opt("--scopes")
        scopes = (
            [s.strip() for s in raw_scopes.split(",") if s.strip()]
            if raw_scopes is not None else None
        )
        accounts.set_account(
            login,
            host=_opt("--host"),
            scopes=scopes,
            login_flow=_opt("--login-flow"),
            notes=_opt("--notes"),
        )
        return 0

    if sub in ("remove", "rm"):
        if not rest or rest[0].startswith("-"):
            output.err("Usage: accounts remove <login>")
            return 1
        if accounts.remove_account(rest[0]):
            return 0
        output.err(f"No account '{rest[0]}' in accounts.yaml")
        return 1

    output.err(f"Unknown accounts subcommand: {sub}")
    _accounts_usage()
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
    print("     [--ownership owned|internal|external] [--owner ACCOUNT]")
    print("     [--locus L] [--machines a,b] [--primary] [--no-scaffold]")
    print("     [--cs-repo R] [--cs-machine M] [--cs-location L]")
    print("     [--cs-workspace DIR]                                (codespace locus)")
    print("     [--container-repo R] [--container-workspace DIR]")
    print("     [--container-machines a,b]                          (container locus)")
    print("  remove <name>                       Unlink (leaves the narrative doc)")
    print("  doc <name>                          Print (scaffold if missing) the narrative")
    print("  doctor [--json]                     Validate entries (repos exist, machines")
    print("                                      + venues valid, local checkouts registered)")
    print("  primary [<name>]                    Show or set the primary related repo")
    print("  resolve [<name>]                    How to work on it from here (locus plan)")
    print("  classify [<name>|--all] [--overwrite]   Derive ownership from gh accounts +")
    print("                                      remote and persist (unset entries only)")
    print("  owners [--json]                     List wholly-owned targets "
          "(ownership=owned) from the control-plane index")
    print()
    print("Any command takes [--repo PATH] to target a specific checkout")
    print("(default: the git repo containing the current directory).")
    print()
    print("Locus (where work happens): local | machine:<key> | codespace | container")
    print("Delegate (how to hand off): agent-bridge | agent-codespaces | agent-containers | none")
    print("Ownership (attribution posture): owned | internal | external "
          "(derived once at registration, then authoritative)")


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


def _related_config_source_anchors(base_anchor: str) -> list[str]:
    """Ordered ``.agent-*`` config-source anchor paths for a related lookup.

    The E1e **knowledge overlay** (config-graft): the base (harness / launch)
    anchor plus the bound **knowledge-repo** config overlay when the launch repo
    requires an external state root, so ``related list/show/resolve/doc`` union
    the harness's base ``related.yaml`` with the knowledge repo's entries. This is
    the config-READ axis (distinct from the state-root WRITE destination; it only
    reuses the state-root resolver to locate the knowledge checkout). Fail-safe ->
    ``[base_anchor]``.
    """
    try:
        srcs = state_root_mod.config_source_anchors(
            cfg.load_config(), base_anchor=base_anchor
        )
        anchors = [s.anchor for s in srcs if s.anchor]
    except Exception:
        anchors = []
    if not anchors:
        anchors = [base_anchor]
    elif os.path.abspath(anchors[0]) != os.path.abspath(base_anchor):
        anchors.insert(0, base_anchor)
    # Installed-plugin config-graft: plugins that ship
    # ``.agent-worktrees/related.yaml`` are the LOWEST-precedence layer, so they
    # go ahead of the base/knowledge anchors (later anchors overlay earlier ones).
    # Merely installing e.g. ``odsp-web-harness`` then contributes the odsp-web
    # CodeSpace locus, which any base/knowledge/user entry can still override.
    try:
        from . import related as _related_mod
        existing = {os.path.abspath(a) for a in anchors}
        plugin_anchors = [
            p
            for p in _related_mod.installed_plugin_related_anchors()
            if os.path.abspath(p) not in existing
        ]
    except Exception:
        plugin_anchors = []
    return [*plugin_anchors, *anchors]


def _related_lookup_anchors(
    rest: list[str], anchor: str, name: str,
) -> tuple[list[str], bool]:
    """Config-source anchors to read ``name`` from for a read-only lookup.

    Layers two mechanisms:

    * the **knowledge overlay** (config-graft) -- the base anchor plus the bound
      knowledge-repo config overlay (see :func:`_related_config_source_anchors`); and
    * the **control-plane fallback** -- ``related`` is cwd-directional, so running
      a lookup from *inside* a coordinated repo's own checkout reads that repo's
      (usually empty) POV and dead-ends. When ``name`` isn't found across the
      base config sources -- and the caller didn't pin an explicit ``--repo`` --
      fall back to the **control-plane project's** index (grafted the same way).

    Returns ``(anchors, via_control_plane)``. Fail-safe: any inability to resolve
    the control plane leaves the base anchors unchanged.
    """
    from . import related
    anchors = _related_config_source_anchors(anchor)
    if _related_opt(rest, "--repo"):
        return anchors, False
    if related.get_related_grafted(anchors, name) is not None:
        return anchors, False
    cp = related.find_control_plane_anchor()
    if cp and os.path.abspath(cp) != os.path.abspath(anchor):
        cp_anchors = _related_config_source_anchors(cp)
        if related.get_related_grafted(cp_anchors, name) is not None:
            return cp_anchors, True
    return anchors, False


def _state_root_pair(json_out: bool) -> int:
    """Resolve the paired (harness/knowledge sibling) worktree of the cwd.

    The citadel paired-worktree resolver (#957): find the current worktree from
    the cwd, then load its recorded sibling (``pair_ref``). Prints the sibling's
    checkout path (or a JSON summary). Exit ``3`` when the current directory is
    not a tracked worktree, or the worktree is unpaired, or the sibling record
    cannot be loaded -- callers must NOT assume a pair on non-zero.
    """
    cwd = os.getcwd()
    wt_id = tracking.find_worktree_id_by_cwd(cwd)
    rec = tracking.load_record_by_id(wt_id) if wt_id else None
    if rec is None:
        msg = "current directory is not a tracked worktree"
        if json_out:
            print(json.dumps({"paired": False, "error": msg}, indent=2))
        else:
            print(msg, file=sys.stderr)
        return 3
    if not rec.is_paired:
        msg = f"worktree '{rec.worktree_id}' is not paired"
        if json_out:
            print(json.dumps(
                {"paired": False, "worktree_id": rec.worktree_id, "error": msg},
                indent=2,
            ))
        else:
            print(msg, file=sys.stderr)
        return 3
    # Anchor pairing: a non-worktree-class knowledge repo has no sibling record;
    # resolve to its checkout via the normal state-root resolution.
    if rec.pair_kind == "anchor":
        res = state_root_mod.resolve_state_root(cfg.load_config())
        if not res.path:
            msg = (res.error
                   or f"anchor pair '{rec.pair_ref}' could not be resolved")
            if json_out:
                print(json.dumps(
                    {
                        "paired": True, "pair_id": rec.pair_id,
                        "pair_ref": rec.pair_ref, "pair_kind": "anchor",
                        "sibling_path": None, "error": msg,
                    },
                    indent=2,
                ))
            else:
                print(msg, file=sys.stderr)
            return 3
        if json_out:
            print(json.dumps(
                {
                    "paired": True,
                    "pair_id": rec.pair_id,
                    "self": {
                        "worktree_id": rec.worktree_id,
                        "role": rec.pair_role,
                        "path": rec.worktree_path,
                    },
                    "sibling": {
                        "worktree_id": None,
                        "role": "knowledge",
                        "path": res.path,
                        "kind": "anchor",
                        "status": None,
                    },
                },
                indent=2,
            ))
        else:
            print(res.path)
        return 0
    sibling = tracking.find_paired_record(rec)
    if sibling is None:
        ref = rec.pair_ref or "?"
        msg = f"paired sibling '{ref}' has no local record on this machine"
        if json_out:
            print(json.dumps(
                {
                    "paired": True,
                    "worktree_id": rec.worktree_id,
                    "pair_id": rec.pair_id,
                    "pair_role": rec.pair_role,
                    "pair_ref": rec.pair_ref,
                    "pair_kind": rec.pair_kind,
                    "sibling_path": None,
                    "error": msg,
                },
                indent=2,
            ))
        else:
            print(msg, file=sys.stderr)
        return 3
    if json_out:
        print(json.dumps(
            {
                "paired": True,
                "pair_id": rec.pair_id,
                "self": {
                    "worktree_id": rec.worktree_id,
                    "role": rec.pair_role,
                    "path": rec.worktree_path,
                },
                "sibling": {
                    "worktree_id": sibling.worktree_id,
                    "role": sibling.pair_role,
                    "path": sibling.worktree_path,
                    "kind": rec.pair_kind,
                    "status": sibling.status,
                },
            },
            indent=2,
        ))
    else:
        print(sibling.worktree_path)
    return 0


def cmd_state_root_dispatch(argv: list[str]) -> int:
    """Resolve the state root (where efforts/visions/logs should be written).

    The seam behind the **stateless harness** split: a stateless harness routes
    personal-state writes to its bound knowledge repo, while a normal repo is its
    own state home. Consumed by the ``efforts``/``visions``/``agent-logger``
    plugin skills so they never hardcode "the launch repo".

    Exit codes: ``0`` when a root is resolved, ``3`` when it is unbound /
    unresolvable (callers must NOT write on non-zero).
    """
    p = argparse.ArgumentParser(
        prog="agent-worktrees state-root",
        description=(
            "Resolve the repo checkout where personal state (efforts, visions, "
            "logs) should be written for the current launch context."
        ),
    )
    p.add_argument(
        "--json", action="store_true",
        help="Emit the full resolution as JSON (state_root/source/repo/"
             "stateless/bound/error) instead of the bare path.",
    )
    p.add_argument(
        "--repo", default=None, metavar="NAME",
        help="Explicit override: resolve this registered repo's checkout "
             "(target the harness itself or a product repo, ignoring the "
             "stateless binding).",
    )
    p.add_argument(
        "--pair", action="store_true",
        help="Resolve the PAIRED worktree (the citadel -harness/-knowledge "
             "sibling of the current worktree): print the sibling's checkout "
             "path, or JSON (pair_id/role/sibling id/role/path/kind) with "
             "--json. Exit 3 when the current worktree is unpaired/untracked.",
    )
    p.add_argument(
        "--conduct", action="store_true",
        help="Emit the sessionStart \"the user's state repo\" definition "
             "(Markdown) binding the term to the resolved checkout, for the "
             "session-conduct hook. Always exits 0 (prints an unbound notice "
             "when no state repo is bound).",
    )
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    if args.pair:
        return _state_root_pair(args.json)

    config = cfg.load_config()
    res = state_root_mod.resolve_state_root(config, repo_override=args.repo)

    if args.conduct:
        print(state_root_mod.state_repo_definition(res))
        return 0

    if args.json:
        print(json.dumps(res.as_dict(), indent=2))
    else:
        if res.path:
            print(res.path)
        if res.error:
            print(res.error, file=sys.stderr)
    return 0 if res.path else 3


def _hunt_checkout(name: str) -> str | None:
    """Best-effort: find a local checkout for ``name`` under a known source root.

    Powers the ``local_repo_unregistered`` remediation ("go hunt for it") without
    a network call: look for a git checkout directory named like the repo under
    each registry source root. Returns the path if exactly one plausible match is
    found, else None (ambiguous / not found -> the agent asks the user). Never
    raises.
    """
    try:
        from . import repos as _repos
        registry = _repos.read_registry()
    except Exception:
        return None
    plat = cfg.detect_platform()
    roots: list[Path] = []
    try:
        root = registry.srcroot.get(plat) if hasattr(registry, "srcroot") else None
        if root:
            roots.append(Path(root))
    except Exception:
        pass
    # Common worktree-adjacent layout: <srcroot> holds anchors directly.
    candidates: list[Path] = []
    for root in roots:
        try:
            if not root.is_dir():
                continue
            for child in root.iterdir():
                if child.name.lower() == name.lower() and (child / ".git").exists():
                    candidates.append(child)
        except Exception:
            continue
    # Deduplicate by resolved path.
    seen: set[str] = set()
    uniq: list[Path] = []
    for c in candidates:
        key = os.path.normcase(str(c.resolve()))
        if key not in seen:
            seen.add(key)
            uniq.append(c)
    return str(uniq[0]) if len(uniq) == 1 else None


def _related_doctor(anchor: str, rest: list[str], json_out: bool) -> int:
    """Validate the related.yaml at ``anchor`` against reality (report-first).

    Verifies -- on THIS machine -- that each entry points at a validly-existing
    repo, names only in-system machines, and has provisionable venues; the
    headline check flags an entry that claims a local checkout the machine's own
    ``repos.yaml`` can't locate. Never edits related.yaml: removal/clone/register
    are the agent's follow-up with the user.
    """
    from . import related, repos

    anchors = _related_config_source_anchors(anchor)
    rc = related.read_related_grafted(anchors)

    try:
        current_machine = cfg.detect_machine(anchor)
    except Exception:
        current_machine = ""

    # Valid-machine predicate from machines.yaml (key / alias / hostname / name).
    machines_known_available = True
    machine_entries: dict = {}
    try:
        machine_entries = cfg.load_machines_yaml(anchor)
    except Exception:
        machines_known_available = False

    def _machine_known(key: str) -> bool:
        if not machines_known_available:
            return True
        return cfg.find_machine_entry(machine_entries, key) is not None

    def _registry_has(name: str) -> bool:
        return repos.find_repo(name) is not None

    def _registry_remote(name: str) -> str:
        e = repos.find_repo(name)
        return (e.remote if e else "") or ""

    findings = related.diagnose_related(
        rc,
        current_machine=current_machine,
        machine_known=_machine_known,
        machines_known_available=machines_known_available,
        registry_has=_registry_has,
        registry_remote=_registry_remote,
    )

    # Best-effort checkout hunt for the headline finding, so the agent can offer
    # a concrete `repos add` instead of asking the user to locate it blindly.
    for f in findings:
        if f.kind == "local_repo_unregistered":
            found = _hunt_checkout(f.name)
            if found:
                f.candidate_path = found
                f.suggested_actions.insert(
                    0, f"register the checkout found here: `repos add {f.name} "
                       f"{found} --class <class>`")

    if json_out:
        _json_output({
            "current_machine": current_machine,
            "machines_yaml_available": machines_known_available,
            "findings": [
                {
                    "name": f.name, "kind": f.kind, "severity": f.severity,
                    "detail": f.detail, "suggested_actions": f.suggested_actions,
                    "candidate_path": f.candidate_path,
                }
                for f in findings
            ],
        })
    else:
        _render_related_findings(findings, current_machine)

    # Errors (a venue that literally can't provision) gate the exit code;
    # warnings/info are for the agent to act on, not a hard failure.
    has_error = any(f.severity == related.SEV_ERROR for f in findings)
    return 1 if has_error else 0


def _render_related_findings(findings: list, current_machine: str) -> None:
    """Human render for `related doctor` (grouped by severity)."""
    from . import related
    output.header(f"related doctor  (machine: {current_machine or '?'})")
    if not findings:
        output.ok("All related entries validate: repos exist, machines and "
                  "venues are valid.")
        return
    order = {related.SEV_ERROR: 0, related.SEV_WARNING: 1, related.SEV_INFO: 2}
    icon = {related.SEV_ERROR: "✗", related.SEV_WARNING: "⚠️ ",
            related.SEV_INFO: "•"}
    for f in sorted(findings, key=lambda x: (order.get(x.severity, 9), x.name)):
        print(f"  {icon.get(f.severity, '-')} [{f.kind}] {f.detail}")
        if f.candidate_path:
            print(f"      found checkout: {f.candidate_path}")
        for a in f.suggested_actions:
            print(f"      - {a}")
    print()
    errs = sum(1 for f in findings if f.severity == related.SEV_ERROR)
    warns = sum(1 for f in findings if f.severity == related.SEV_WARNING)
    infos = sum(1 for f in findings if f.severity == related.SEV_INFO)
    print(f"  {errs} error(s), {warns} warning(s), {infos} info.")
    if warns or errs:
        output.info("Report-only: `related doctor` never edits related.yaml. "
                    "Resolve each with the user (locate / provide URL / clone / "
                    "register), and remove an entry only with their approval.")


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

    # `owners` is a GLOBAL query -- the operator's wholly-owned targets live in
    # the control-plane index (the authority), independent of the session's cwd.
    # Resolve it directly and read only that (no cwd union, which could let a
    # stale anchor override a fresher one), so it works from ANY cwd -- exactly
    # what an ambient consumer like the AI-attribution hook needs. Handled before
    # the cwd-anchor guard so a neutral cwd (no adopted project) still answers.
    if sub == "owners":
        json_out = "--json" in rest
        try:
            cp = related.find_control_plane_anchor()
        except Exception:
            cp = None
        base = cp or _related_anchor(rest)   # fall back to cwd if no control plane
        owners = (related.owned_targets_grafted(_related_config_source_anchors(base))
                  if base else [])
        if json_out:
            _json_output({"owned": owners, "count": len(owners),
                          "source": "control-plane" if cp else "cwd"})
        elif not owners:
            print("No wholly-owned related targets (ownership=owned).")
        else:
            output.header("Wholly-owned targets")
            for t in owners:
                print(f"  {t['name']:<24} {t['slug'] or t['remote'] or '-'}")
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
        anchors = _related_config_source_anchors(anchor)
        entries = related.list_related_grafted(anchors, role=role)
        primary = related.get_primary_grafted(anchors)
        if json_out:
            _json_output({
                "primary": primary,
                "related": [
                    {
                        "name": e.name, "role": e.role, "summary": e.summary,
                        "doc": e.doc, "delegate": e.delegate,
                        "ownership": related.effective_ownership(e),
                        "owner": e.owner,
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

    if sub == "doctor":
        return _related_doctor(anchor, rest, json_out)

    if sub == "show":
        if not rest or rest[0].startswith("-"):
            output.err("Usage: related show <name>")
            return 1
        name = rest[0]
        anchors, _via_cp = _related_lookup_anchors(rest, anchor, name)
        e = related.get_related_grafted(anchors, name)
        if e is None:
            output.err(f"'{name}' is not a related repo.")
            return 1
        reg = repos.find_repo(name)
        if json_out:
            _json_output({
                "name": e.name, "role": e.role, "summary": e.summary,
                "doc": e.doc, "delegate": e.delegate,
                "ownership": related.effective_ownership(e),
                "ownership_explicit": e.ownership,
                "owner": e.owner,
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
        _own = related.effective_ownership(e)
        if _own:
            _osrc = "explicit" if e.ownership else "derived"
            print(f"  ownership: {_own} ({_osrc})"
                  + (f"  owner={e.owner}" if e.owner else ""))
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
            ownership=related.normalize_ownership(_related_opt(rest, "--ownership", "")),
            owner=(_related_opt(rest, "--owner", "") or "").strip(),
        )
        # Derive the ownership posture ONCE, here at registration, when the
        # operator did not state it explicitly -- baked into related.yaml and
        # thereafter authoritative (consumers never re-inspect live gh accounts).
        if not entry.ownership:
            derived, owner = related.classify_ownership(name)
            if derived:
                entry.ownership = derived
            if owner and not entry.owner:
                entry.owner = owner
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
        anchors, _via_cp = _related_lookup_anchors(rest, anchor, name)
        e = related.get_related_grafted(anchors, name)
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
            if related.get_related_grafted(
                _related_config_source_anchors(anchor), name
            ) is None:
                output.err(f"'{name}' is not a related repo. Link it first.")
                return 1
            related.set_primary(anchor, name)
            output.ok(f"primary = {name}")
        else:
            print(
                related.get_primary_grafted(
                    _related_config_source_anchors(anchor)
                ) or "(unset)"
            )
        return 0

    if sub == "resolve":
        from . import doctor
        explicit_name = rest[0] if rest and not rest[0].startswith("-") else None
        anchors = _related_config_source_anchors(anchor)
        name = explicit_name or related.get_primary_grafted(anchors)
        via_cp = False
        if not name and not _related_opt(rest, "--repo"):
            # Bare `resolve` from a repo with no primary of its own: fall back to
            # the control-plane index's primary so it still resolves something.
            cp = related.find_control_plane_anchor()
            if cp and os.path.abspath(cp) != os.path.abspath(anchor):
                cp_anchors = _related_config_source_anchors(cp)
                cp_primary = related.get_primary_grafted(cp_anchors)
                if cp_primary:
                    anchors, name, via_cp = cp_anchors, cp_primary, True
        if not name:
            output.err("Usage: related resolve <name>  (or set a primary first)")
            return 1
        if explicit_name:
            anchors, via_cp = _related_lookup_anchors(rest, anchor, name)
        entry = related.get_related_grafted(anchors, name)
        if entry is None:
            output.err(f"'{name}' is not a related repo.")
            return 1
        reg = repos.find_repo(name)
        try:
            current_machine = cfg.detect_machine(anchors[0])
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
                "account": repos.resolve_account(reg),
                "ownership": related.effective_ownership(entry),
                "owner": entry.owner,
                "delegate_via": resn.delegate_via,
                "current_machine": current_machine,
                "steps": resn.steps,
                "notes": resn.notes,
                "explore": resn.explore,
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
        _acct = repos.resolve_account(reg)
        if _acct:
            _asrc = "explicit" if (reg and reg.account) else "derived"
            print(f"  account:  {_acct} ({_asrc})")
        if resn.delegate_via:
            print(f"  delegate: {resn.delegate_via}")
        print(f"  machine:  {current_machine or '(unknown)'}")
        for n in resn.notes:
            output.warn(n)
        print()
        if resn.explore:
            print("  Explore (read/understand the code):")
            for e in resn.explore:
                print(f"    - {e}")
            print()
        print("  Plan:")
        for s in resn.steps:
            print(f"    - {s}")
        return 0

    if sub == "classify":
        target = rest[0] if rest and not rest[0].startswith("-") else None
        overwrite = "--overwrite" in rest
        if target and target != "--all":
            derived, owner = related.classify_ownership(target)
            existing = related.get_related(anchor, target)
            if existing is None:
                output.err(f"'{target}' is not a related repo.")
                return 1
            if existing.ownership and not overwrite:
                if json_out:
                    _json_output({"name": target, "ownership": existing.ownership,
                                  "owner": existing.owner, "changed": False,
                                  "reason": "already set (use --overwrite)"})
                else:
                    output.info(f"'{target}' ownership already set to "
                                f"'{existing.ownership}' (use --overwrite to re-derive).")
                return 0
            if not derived:
                if json_out:
                    _json_output({"name": target, "ownership": "", "changed": False,
                                  "reason": "underivable -- set explicitly with --ownership"})
                else:
                    output.warn(f"Could not derive ownership for '{target}' from its "
                                f"remote -- set it explicitly with "
                                f"`related add {target} --ownership <owned|internal|external>`.")
                return 0
            related.upsert_related(anchor, related.RelatedEntry(
                name=target, ownership=derived, owner=owner))
            if json_out:
                _json_output({"name": target, "ownership": derived, "owner": owner,
                              "changed": True})
            else:
                output.ok(f"'{target}' ownership = {derived}"
                          + (f" (owner {owner})" if owner else ""))
            return 0
        # --all (or bare): backfill every entry.
        changed = related.classify_all(anchor, overwrite=overwrite)
        if json_out:
            _json_output({"changed": changed, "count": len(changed)})
        elif not changed:
            output.info("No ownership changes (all entries classified, or "
                        "underivable). Pass --overwrite to re-derive explicit ones.")
        else:
            output.header("Ownership classified")
            for c in changed:
                print(f"  {c['name']:<24} {c['before']} -> {c['after']}")
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
    no_terminal_profile: bool = False,
) -> None:
    """Write the machine-local per-project config YAML.

    Machine-wide fields (srcroot/machine/platform/copilot_profiles) live in the
    global ~/.agent-worktrees/config.yaml; repo settings may live in-repo at
    <anchor>/.agent-worktrees/config.yaml. This file keeps only the project
    marker and machine paths (anchor / worktree_root) plus repo defaults that a
    foreign repo without in-repo config still needs.

    ``no_terminal_profile`` seeds an explicit empty ``terminal_profiles: []`` so
    the Windows-Terminal generator emits **no** profile for this project (used
    for a ``--no-agent`` adoption: worktree-managed + binstub, but nothing to
    launch from the terminal dropdown). An *absent* key applies the **default
    column** (minimal per-agent + bare cross-machine), so the empty list must be
    written explicitly to suppress.
    """
    wt_root = f"{repo_dir}.worktrees"

    headless_line = "headless: true\n" if headless else ""
    # Explicit empty selection = "no terminal profile for this project".
    # Absent would apply the default column (self launcher + remote shells), so
    # it must be written out to suppress.
    terminal_block = (
        "\n# No Windows Terminal profile for this project (--no-agent adoption):\n"
        "# an empty selection suppresses generation (absent applies the default).\n"
        "terminal_profiles: []\n"
        if no_terminal_profile else ""
    )
    content = f"""# ~/.{project}/config.yaml
# Machine-local config for {project} (overrides + machine paths only).
# Machine-wide defaults -> ~/.agent-worktrees/config.yaml.
# Repo settings may live in-repo -> <anchor>/.agent-worktrees/config.yaml.

repo_name: {project}
{headless_line}{terminal_block}
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
        description=(
            "Worktree session manager. Context resolves from the current "
            "directory, the way Git does: the target worktree and its anchor "
            "repo are discovered from CWD, never from ambient environment "
            "variables or the branch name."
        ),
        epilog=(
            "Global options (accepted before any command):\n"
            "  -p, --project NAME  Operate as if CWD were project NAME's anchor "
            "repo. When already inside one of NAME's worktrees, acts on that "
            "worktree (git '-C' semantics); otherwise resolves against NAME's "
            "anchor. This is what a project's own binstub injects, and it lets "
            "you act on another project's worktrees without leaving this one.\n"
            "  --version           Show build info and exit.\n"
            "\n"
            "With neither --project nor a project binstub, context is "
            "auto-derived from CWD (a managed repo must be discoverable from "
            "here, like Git's 'not a git repository')."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # resolve (emit JSON launch plan, then exit -- shell handles execution)
    p = sub.add_parser("resolve", help="Resolve launch plan as JSON (for shell wrappers)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--recovery", action="store_true")
    p.add_argument("--no-resume", action="store_true",
                   help="Don't auto-resume the last Copilot session")
    p.add_argument("--bare-resume", action="store_true",
                   help="Two-step restore: create the worktree's mux, but launch "
                        "Copilot in the HOME dir with no --resume (dodges a CLI "
                        "bug that fails to start Copilot inside a repo/worktree "
                        "cwd). Finish with a manual '/resume <id>' inside.")
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
    p.add_argument("--owner-ref", default=None, dest="owner_ref",
                   help="With --new: qualified ref "
                        "(machine/project/worktree_id[#session]) of the worktree "
                        "that owns this one as an outbound resource -- stamps the "
                        "new worktree's owner_ref so its finalize settles the "
                        "owner's claim (resource-obligation-settlement). For a "
                        "bridge spawn, the dispatching (caller) worktree's ref.")
    p.add_argument("copilot_args", nargs="*", default=[])

    # post-exit (run post-exit checks after Copilot exits)
    p = sub.add_parser("post-exit", help="Post-exit worktree checks (idempotent)")
    p.add_argument("worktree_id", nargs="?", default=None)

    # session-lock (session-state lattice: bridge/mux liveness marker, #4272)
    p = sub.add_parser(
        "session-lock",
        help="Write/remove a session-state lattice lock -- a provable-liveness "
             "marker beside Copilot's inuse lock, so the picker reads a "
             "bridge/mux session's liveness file-first",
    )
    p.add_argument("action", choices=["write", "remove"])
    p.add_argument("--session", required=True,
                   help="Copilot session id (the session-state dir name)")
    p.add_argument("--worktree", default=None,
                   help="Worktree id this session is bound to (recorded in the "
                        "lock for cwd-independent attribution)")
    p.add_argument("--pid", type=int, default=None,
                   help="Owner process pid whose liveness the lock proves "
                        "(e.g. the bridge-owned Copilot child); default: caller")
    p.add_argument("--kind", default="bridge", choices=["bridge"],
                   help="Lattice layer (default: bridge)")
    p.add_argument("--json", action="store_true", help="JSON output mode")

    # finalize
    p = sub.add_parser(
        "finalize",
        help="Validate the branch's content is on upstream; prune the worktree only when idle",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--abandon", action="store_true",
                   help="Finalize past the obligation gate even when the worktree "
                        "still owns unsettled outbound resources, re-homing them "
                        "for cleanup/adoption (resource-obligation-settlement).")
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
        aliases=["pr-create"],
        help="Squash worktree commits, create + push a feature branch for a PR "
             "(pr-create is the pr-* family alias)",
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
    p.add_argument("--draft", action="store_true",
                   help="Open the PR as a native DRAFT (not yet ready for "
                        "review). 'pr-ready' moves it out of draft. Lets you "
                        "iterate on the open PR before requesting review.")
    p.add_argument("--hold", action="store_true", dest="hold",
                   help="Deprecated alias for --draft (the old do-not-merge "
                        "label hold is retired in favour of native draft state).")
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

    # pr-ready (move a PR out of draft -> ready-for-review)
    p = sub.add_parser(
        "pr-ready",
        help="Move a PR out of draft (draft -> ready-for-review). Does NOT "
             "grant merge consent -- use pr-merge for that.",
    )
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
        help="Show tracked PR metadata + live verdict/conflict/merge state "
             "(reconciles against the provider; recommends pull-forward when "
             "the active PR has merged)",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--all", action="store_true",
                   help="List every tracked PR, not just the active one")
    p.add_argument("--no-live", action="store_true", dest="no_live",
                   help="Skip the live provider read (tracked metadata only)")
    p.add_argument("--threads", action="store_true",
                   help="Also list the PR's review comment threads")
    p.add_argument("--resolve-threads", action="store_true", dest="resolve_threads",
                   help="Mark active comment threads resolved (implies --threads)")
    p.add_argument("--json", action="store_true", help="JSON output mode")
    p.add_argument("--config", default=None)

    # pr-complete (post-merge reconcile: ff past a squash-merge, or rebase)
    p = sub.add_parser(
        "pr-complete",
        help="Reconcile the worktree after its PR merged (fast-forward past the "
             "squash-merge, or rebase to preserve new work). Distinct from finalize.",
    )
    p.add_argument("worktree_id", nargs="?", default=None)
    p.add_argument("--dry-run", action="store_true", dest="dry_run",
                   help="Report the action that would be taken; change nothing")
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
    p = sub.add_parser("status", help="Show worktree git status (read); "
                       "annotate this worktree's disposition (write)")
    p.add_argument("--json", action="store_true")
    p.add_argument("--mux-details", action="store_true",
                   help="Include mux session attached/detached status (JSON only)")
    # worktree-status-core: write mode -- annotate THIS worktree's agent-asserted
    # disposition. Any of these switches the command from the fleet read to a
    # per-worktree write (resolved from CWD, or --worktree-id).
    p.add_argument("--summary", default=None,
                   help="Set this worktree's one-line disposition summary (write mode)")
    p.add_argument("--follow-up", dest="follow_up", action="store_true",
                   help="Flag this worktree as having actionable follow-ups (write mode)")
    p.add_argument("--resolved", action="store_true",
                   help="Clear the follow-up flag -- this worktree is resolved (write mode)")
    p.add_argument("--worktree-id", default=None,
                   help="Target worktree id for write mode (default: inferred from CWD)")

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
    p.add_argument("--session-id", dest="session_id", default=None,
                   help="Resumed session id -- authoritative worktree fallback "
                        "when cwd is HOME (bare resume); resolves the worktree "
                        "from the session registry")
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
    # embody (D5: agent-initiated CLI embodiment -- detached mux+Copilot spawn)
    p = sub.add_parser(
        "embody",
        help="Create or resume a DETACHED mux+Copilot CLI session in a worktree "
             "(the agent-facing embodiment verb; auto-registers with the bridge)",
    )
    g = p.add_mutually_exclusive_group()
    g.add_argument("--worktree-id", dest="worktree_id", default=None,
                   help="Embody in this existing worktree")
    g.add_argument("--new", action="store_true",
                   help="Create a fresh worktree first, then embody in it")
    p.add_argument("--seed", default=None,
                   help="Seed prompt injected as the session's first "
                        "interactive turn once Copilot is ready")
    p.add_argument("--seed-ready-timeout", dest="seed_ready_timeout",
                   type=float, default=180.0, metavar="SECONDS",
                   help="How long to wait for Copilot's input prompt before "
                        "typing the --seed (default 180). A fresh MCP/skill-heavy "
                        "autopilot can take much longer than the fast handoff "
                        "default to become ready; if this is too short the seed "
                        "is never delivered and the session idles at an empty "
                        "prompt")
    p.add_argument("--driver", default=None,
                   help="Label of the agent steering this session; stamps the "
                        "'driven by <agent>' banner (AGENT_BRIDGE_DRIVEN_BY) so "
                        "a human taking over in Neuron Forge sees who's at the "
                        "wheel")
    p.add_argument("--verify-timeout", dest="verify_timeout", type=float,
                   default=0.0, metavar="SECONDS",
                   help="Wait up to N seconds for the mux session to come up "
                        "before returning (default 0: don't wait)")
    p.add_argument("--recovery", action="store_true",
                   help="Use the repo's recovery launch command")
    p.add_argument("--dry-run", action="store_true",
                   help="Print the resolved plan without spawning anything")
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
    p.add_argument("--cache-only", action="store_true",
                   help="Cache-only fast paint (picker-cache-first-paint, "
                        "dotfiles#948): build JSON rows from ONLY the cached "
                        "session-render fields in each tracking record -- no "
                        "events.jsonl scan, no process/mux scan, no git "
                        "classify. Never-populated worktrees render Unknown. "
                        "Used by the Picker's SSH fast phase; a --classify "
                        "populate later fills + writes the cache back.")
    p.add_argument("--stream", action="store_true",
                   help="Emit newline-delimited JSON (one worktree per line, "
                        "flushed) for the Picker's streaming SSH consumer: a "
                        "begin frame, fast (unclassified) rows, then classified "
                        "rows (with --classify), then a done frame. Implies "
                        "--json.")

    # claims (a worktree's full claim ledger: outbound resources + inbound tasks)
    p = sub.add_parser(
        "claims",
        help="Show a worktree's full claim ledger (outbound resources + its "
             "owner + inbound tasks; best-effort via agent-dispatch). Defaults "
             "to the current worktree; pass an id for another. "
             "`claims release <ref>` retires one outbound claim.",
    )
    p.add_argument("target", nargs="*", default=None,
                   help="[worktree_id] to show, OR 'add <kind> <ref>' to journal "
                        "a new outbound claim, OR 'release <ref>' to retire one, "
                        "OR 'settle <ref>' to mark it at-rest (settled) / released, "
                        "OR 'sweep' to reclaim provably-gone+safe obligations "
                        "(never-wedge), OR 'orphans' to list obligations re-homed "
                        "by an --abandon finalize (pending cleanup), OR 'cleanup' "
                        "to reclaim those re-homed obligations (delete the "
                        "orphaned resource; --apply to act)")
    p.add_argument("--remove", action="store_true",
                   help="with release: drop the claim entry entirely instead of "
                        "marking it released")
    p.add_argument("--apply", action="store_true",
                   help="with sweep/cleanup: write the abandonments / reclaim the "
                        "orphaned resources (default: dry-run preview only)")
    p.add_argument("--note", default="",
                   help="with add: an optional human label for the claim")
    p.add_argument("--released", action="store_true",
                   help="with settle: mark the claim released rather than at-rest")
    p.add_argument("--worktree", default=None, dest="release_worktree",
                   help="with release/settle: the owner worktree (default: current)")
    p.add_argument("--owner-ref", default=None, dest="claim_owner_ref",
                   help="with add/settle: journal/settle onto the owner named by "
                        "this qualified ref (machine/project/worktree_id) instead "
                        "of the current project's cwd-inferred worktree -- resolves "
                        "cross-project on THIS machine (a cross-machine owner is "
                        "deferred to the lease mirror). For a call-site (e.g. "
                        "agent-codespaces on CodeSpace borrow/disconnect) whose cwd "
                        "is not the borrowing worktree.")
    p.add_argument("--json", action="store_true",
                   help="JSON output mode (stdout is JSON only)")

    # claimant-liveness (SSH endpoint for cross-machine reap-safety: report
    # whether an owner_ref's worktree is alive ON THIS machine)
    p = sub.add_parser(
        "claimant-liveness",
        help="Report same-machine liveness of an owner_ref "
             "(machine/project/worktree_id) as a tri-state alive/gone/unknown. "
             "The endpoint the reaper's cross-machine claimant probe calls over "
             "SSH; not typically run by hand.",
    )
    p.add_argument("owner_ref",
                   help="Qualified owner ref (machine/project/worktree_id[#session])")
    p.add_argument("--json", action="store_true",
                   help="JSON output mode (stdout is JSON only)")

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
    p.add_argument("--interface", default=None, choices=["cli", "acp"],
                   help="Stamp the worktree's interface mark (cli|acp). Default: "
                        "derived from kind (bridge=acp, else cli). See #2668.")
    p.add_argument("--origin", default=None, choices=["user", "system", "delegate"],
                   help="Stamp who kicked the work off (user|system|delegate). "
                        "user = operator (NF/Picker), delegate = agent-spawned, "
                        "system = background/daemon. Default: derived from kind + "
                        "caller. Governs Picker/cockpit visibility. See #2668.")
    p.add_argument("--owner-ref", default=None, dest="owner_ref",
                   help="Qualified ref (machine/project/worktree_id[#session]) of "
                        "the worktree that owns this one as an outbound resource. "
                        "Usually injected by `run` via AGENT_WORKTREES_OWNER_REF; "
                        "the flag is the low-level primitive for scripts/tools "
                        "that already know both sides.")
    p.add_argument("--no-owner", action="store_true", dest="no_owner",
                   help="Create a deliberately top-level worktree: do NOT inherit "
                        "an owner from AGENT_WORKTREES_OWNER_REF or the CWD, so no "
                        "parent's finalize gate is held on it (Ph6).")
    p.add_argument("--json", action="store_true",
                   help="JSON output mode (stdout is JSON only)")

    # run (execute an inner subcommand; journal the resource it produces as an
    # outbound claim on THIS worktree -- resource-claims)
    p = sub.add_parser(
        "run",
        help="Run an inner (possibly cross-repo) subcommand and journal the "
             "resource it produces as an outbound claim on THIS worktree "
             '(e.g. run "<other-project> create --json")',
    )
    p.add_argument("--owner-ref", default=None, dest="owner_ref",
                   help="Override the auto-resolved owner ref "
                        "(machine/project/worktree_id[#session]) for the calling "
                        "worktree. Default: resolved from the current directory.")
    p.add_argument("inner_command", nargs=argparse.REMAINDER,
                   help='The inner subcommand to run, as a quoted string or '
                        'trailing tokens (e.g. "copilot-extensions create --json")')
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

    # gc (garbage-collect worktrees: tracked reap + orphan-directory sweep)
    p = sub.add_parser(
        "gc",
        help="Garbage-collect worktrees: tracked reap (cleanup verdict) + "
             "on-disk orphan-directory sweep + git worktree prune")
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would be removed without removing anything")
    p.add_argument("--json", action="store_true",
                   help="Emit the orphan-sweep report as JSON (the tracked reap "
                        "runs in text mode)")
    p.add_argument("--orphans-only", action="store_true",
                   help="Only sweep orphan directories; skip the tracked reap "
                        "and the managed (system/bridge) sweep")
    p.add_argument("--no-managed", action="store_true",
                   help="Skip the managed (system/bridge) leak sweep")
    p.add_argument("--no-reap-shells", action="store_true",
                   help="Skip the orphaned launcher-shell reap (pwsh/python "
                        "scaffolding stranded by a force-closed terminal)")
    p.add_argument("--reap-shells-grace-hours", type=float, default=None,
                   help="Idle window before an orphaned launcher shell is "
                        "eligible (default 1h); a fresh one is always spared")
    p.add_argument("--managed-grace-hours", type=float, default=None,
                   help="Idle window before a dead managed worktree is reaped "
                        "(default 1h); a still-fresh one is always spared")
    p.add_argument("--include-unused", action="store_true",
                   help="Also reap truly-empty tracked worktrees (no commits, "
                        "zero conversation turns)")
    p.add_argument("--include-conversations", action="store_true",
                   help="Also reap conversation-only worktrees (no commits but "
                        "the session held turns); implies --include-unused")
    p.add_argument("--reconcile-prs", action="store_true",
                   help="Refresh tracked PR state from the provider before "
                        "deciding (heals stale 'open' PRs merged externally)")
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

    # reap-shells (GC orphaned launcher shells -- copilot-extensions #102)
    p = sub.add_parser(
        "reap-shells",
        help="Reap orphaned agent-worktrees launcher shells (pwsh/python left "
             "by a force-closed terminal). Reports candidates by default; only "
             "kills with --yes. Positive-signature + service-safe + idle-gated.")
    p.add_argument("--yes", action="store_true",
                   help="Actually terminate the shells (default is a dry-run "
                        "report -- nothing is killed without this flag)")
    p.add_argument("--grace-hours", type=float, default=None,
                   help="Minimum age before an orphaned shell is eligible "
                        "(default 1h)")
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

    # reclaim (free the exact Copilot process bound to a session/worktree)
    p = sub.add_parser(
        "reclaim",
        help="Free the exact Copilot process(es) bound to a session/worktree, "
             "resolved from Copilot's own inuse.<pid>.lock claim -- precise, "
             "never splashing onto a sibling session or a worktree that merely "
             "shares a cwd. The primitive for BARE orphans (a Copilot launched "
             "straight in a terminal, invisible to the wt-<id> mux fleet view). "
             "Dry-run by default; pass --yes to terminate. Freeing an idle "
             "orphan loses nothing -- the session stays resumable.")
    p.add_argument("--session-id", default=None,
                   help="Target one session (exact dir name or unambiguous "
                        "prefix)")
    p.add_argument("--worktree-id", default=None,
                   help="Target every session bound to this worktree id "
                        "(default: infer from cwd)")
    p.add_argument("--all", action="store_true",
                   help="Target every bound Copilot on the machine")
    p.add_argument("--bare-only", action="store_true",
                   help="Restrict to un-muxed orphans (homing bare or "
                        "unclassifiable) -- the common intent; leaves only "
                        "positively mux-homed sessions to restart/reap")
    p.add_argument("--yes", action="store_true",
                   help="Actually terminate the matched processes (without it, "
                        "the command is a dry run that kills nothing)")
    p.add_argument("--json", action="store_true",
                   help="Emit a single JSON result object")

    # remux (Linux/WSL: adopt a bare Copilot into its wt-<id> tmux pane)
    p = sub.add_parser(
        "remux",
        help="Linux/WSL only: reparent a running BARE (un-muxed) Copilot into "
             "its wt-<id> tmux pane via reptyr, so no conversation is lost and "
             "the session rejoins the mux fleet. Companion to `reclaim` (which "
             "reaps-and-resumes). A clear no-op on Windows (ConPTY cannot adopt "
             "a running process).")
    p.add_argument("--session-id", default=None,
                   help="Target one session (exact dir name or unambiguous "
                        "prefix)")
    p.add_argument("--worktree-id", default=None,
                   help="Target the bare Copilot bound to this worktree id "
                        "(default: infer from cwd)")
    p.add_argument("--sudo", dest="force_sudo",
                   action=argparse.BooleanOptionalAction, default=None,
                   help="Force (--sudo) or forbid (--no-sudo) running reptyr "
                        "under sudo -A. Needed when the yama ptrace_scope "
                        "forbids attaching a non-descendant; auto-detected by "
                        "default.")
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

    # terminal-fragment (preview the generated Windows Terminal fragment)
    p = sub.add_parser(
        "terminal-fragment",
        help="Preview the Windows Terminal fragment this machine's config "
             "would emit (no deploy)")
    p.add_argument("--machine", default=None,
                   help="Machine key to preview as (default: this machine "
                        "from config)")
    p.add_argument("--explain", action="store_true",
                   help="Per-project decision trace instead of the raw "
                        "fragment JSON")
    p.add_argument("--doctor", action="store_true",
                   help="Read-only report of live Windows Terminal state drift "
                        "(hidden/orphaned/duplicate profiles); no mutation")

    # repair (reconcile local deployed state: terminal profiles + binstubs)
    p = sub.add_parser(
        "repair",
        help="Repair local integration in place -- regenerate Windows Terminal "
             "profiles (heal hidden + reclaim orphans) and redeploy project "
             "binstubs. Version-independent (unlike 'update').")
    p.add_argument("--terminal", action="store_true",
                   help="Repair only Windows Terminal profiles (default: both "
                        "terminal and binstubs)")
    p.add_argument("--binstubs", action="store_true",
                   help="Repair only project binstubs (default: both terminal "
                        "and binstubs)")

    # picker (Textual picker is default everywhere; disable = machine opt-out)
    p = sub.add_parser("picker",
                       help="Inspect / opt out of the Textual worktree picker "
                            "(the default) for this machine")
    p.add_argument("picker_action",
                   choices=["enable", "disable", "status", "mock", "screenshot"],
                   nargs="?", default="status",
                   help="the Textual picker is the default everywhere; "
                        "disable writes new_picker:false to opt this machine out "
                        "to the legacy picker, enable restores the default "
                        "(~/.agent-worktrees/config.yaml); status (default) "
                        "reports the effective value; mock launches the picker "
                        "in the mock dev sandbox (real data, simulated actions, "
                        "no side effects); screenshot renders the picker "
                        "headlessly and captures it for auditing")
    p.add_argument("--json", action="store_true", help="Emit a JSON result")
    p.add_argument("--out", default=None,
                   help="screenshot: write the capture to this file "
                        "(default: stdout)")
    p.add_argument("--format", dest="picker_format",
                   choices=["svg", "text", "ansi"], default="svg",
                   help="screenshot format: svg (audit screenshot), text (plain "
                        "character grid), ansi (colour-aware grid)")
    p.add_argument("--live", action="store_true",
                   help="screenshot: render the multi-machine SSH source "
                        "instead of the local-only source")
    p.add_argument("--pivot", dest="picker_pivot", default=None,
                   help="screenshot: switch to this pivot (top tab) before "
                        "capturing, e.g. 'CodeSpaces' (case-insensitive; "
                        "unknown labels capture the default Worktrees tab)")
    p.add_argument("--wait", dest="picker_wait", type=float, default=0.0,
                   help="screenshot: with --pivot, seconds to wait for a "
                        "registered pivot's background list to finish loading "
                        "so the capture shows real rows (default: 0 = no wait)")
    p.add_argument("--local", dest="picker_local", action="store_true",
                   help="mock: force the local-only source (data_local) instead "
                        "of the multi-machine SSH source -- for an isolated "
                        "sandbox preview with no resolvable mesh repo/roster")

    # validate
    p = sub.add_parser("validate", help="Validate core infrastructure files")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--files", nargs="*", default=None)
    p.add_argument("--worktree-path", default=None)
    p.add_argument("--default-branch", default="origin/master")

    # config-migrate (machine-local config schema versioning)
    p = sub.add_parser(
        "config-migrate",
        help="Migrate machine-local config schemas in ~/.agent-worktrees/ (idempotent)",
    )
    p.add_argument("--quiet", action="store_true", help="Suppress per-file output")

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
    p.add_argument("--no-anchor-sync", action="store_true",
                   help="Skip fast-forwarding the managed repo anchor(s) after update")
    p.add_argument("--force", action="store_true",
                   help="Re-deploy every runtime installer even when the "
                        "deployed version already matches the payload "
                        "(default: skip already-current runtimes for speed)")

    # install-status
    sub.add_parser("install-status", help="Show installation status")

    # deploy-instructions
    p = sub.add_parser("deploy-instructions",
                       help="Retire migrated managed instruction files (machine identity now via the session-machine hook)")
    p.add_argument("--machine", default=None,
                   help="Machine name (auto-detected from config if omitted)")

    # machine-context (sessionStart hook: emit machine identity as additionalContext)
    sub.add_parser("machine-context",
                   help="Emit machine identity as sessionStart additionalContext (hook entrypoint; cwd-gated)")

    # get (query project paths and config values)
    p = sub.add_parser("get", help="Query project paths and config values")
    p.add_argument("key", help="Key to query (use 'keys' to list available keys)")
    p.add_argument("--session-id", dest="session_id", default=None,
                   help="Resolve worktree-scoped keys (worktree-dir) from this "
                        "session when cwd is HOME (bare resume) -- binding-first, "
                        "not cwd inference")

    # services -- dispatched pre-argparse (see cmd_services_dispatch)
    # Stub entry for --help visibility only
    sub.add_parser("services", help="Service discovery and management (run 'services' for usage)")

    # repos -- dispatched pre-argparse (see cmd_repos_dispatch)
    sub.add_parser("repos", help="Repos registry and source roots (run 'repos' for usage)")

    # accounts -- dispatched pre-argparse (see cmd_accounts_dispatch)
    sub.add_parser("accounts", help="gh account identity catalog (run 'accounts' for usage)")

    # related -- dispatched pre-argparse (see cmd_related_dispatch)
    sub.add_parser("related", help="Per-project related repos (run 'related' for usage)")

    # state-root -- dispatched pre-argparse (see cmd_state_root_dispatch)
    sp = sub.add_parser(
        "state-root",
        help="Resolve where efforts/visions/logs are written (stateless-harness "
             "aware; --json / --repo NAME)")
    sp.add_argument("--json", action="store_true",
                    help="Emit the full resolution as JSON")
    sp.add_argument("--repo", default=None, metavar="NAME",
                    help="Explicit override: resolve this registered repo")

    # git -- dispatched pre-argparse (see cmd_git_dispatch)
    sub.add_parser("git", help="Git collaboration primitives (run 'git' for usage)")

    # pr-watch / pr -- dispatched pre-argparse (see cmd_pr_watch_dispatch /
    # cmd_pr_dispatch). Registered here only so they surface in --help.
    sub.add_parser("pr-watch",
                   help="Block until a PR moves (run 'pr-watch' for usage)")
    sub.add_parser("pr-merge",
                   help="Signal merge consent on an approved PR (run 'pr-merge' for usage)")
    sub.add_parser("pr-research",
                   help="Inspect a repo's provider settings -> policy matrix (read-only)")
    sub.add_parser("pr", help="Author-side PR command family (run 'pr' for usage)")

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
    sp.add_argument("--apply", action="store_true",
                    help="Execute the plan in-process (2-pass) instead of printing "
                         "it. Used by the provision-check sessionStart shim.")
    sp.add_argument("--peek", action="store_true",
                    help="Print the plan WITHOUT persisting the reconcile cache "
                         "(read-only preview; no throttle side effects).")

    # reconcile-binstubs (project launchers in ~/.local/bin vs projects.yaml)
    sub.add_parser(
        "reconcile-binstubs",
        help="Reconcile ~/.local/bin project binstubs against projects.yaml "
             "(add for every registered project, remove deregistered ones)")

    # register-project-entry (single Python owner of the projects.yaml write --
    # both installers call this instead of reimplementing the registry logic)
    sp = sub.add_parser(
        "register-project-entry",
        help="Write a lean projects.yaml entry (installer-invoked; the single "
             "Python owner of the registry write)")
    # Positional (NOT --project): main() pre-pops a global --project/-p flag as
    # the active-project selector, so a --project flag here would be swallowed
    # before argparse dispatches to this subparser.
    sp.add_argument("project", help="Project name")
    sp.add_argument("--repo-dir", default=None,
                    help="Anchor dir (only for WSL path capture; not persisted)")
    sp.add_argument("--display-name", default=None,
                    help="Harness display casing override")
    _ea = sp.add_mutually_exclusive_group()
    _ea.add_argument("--expose-agent", dest="expose_agent",
                     action="store_true", default=None,
                     help="Force agent exposure on (default: from repos.yaml)")
    _ea.add_argument("--no-expose-agent", dest="expose_agent",
                     action="store_false",
                     help="Force reference-only (no agent)")
    sp.add_argument("--base-repo", dest="base_repo", action="store_true",
                    default=None, help="Mark base-repo (no-worktree) adoption")
    sp.add_argument("--elevated", dest="elevated", action="store_true",
                    default=None, help="Mark elevated agent context")
    sp.add_argument("--wsl-state", default=None,
                    choices=["adopted", "bootstrap"], help="WSL adoption state")
    sp.add_argument("--wsl-distro", default=None, help="WSL distro name")
    sp.add_argument("--wsl-path", default=None, help="Repo anchor path in WSL")

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
    sp.add_argument("--launch-id", dest="launch_id", default=None,
                    help="Launch-flow correlation id (from WORKTREE_LAUNCH_ID)")

    sp = sub.add_parser("deregister-session",
                        help="Mark a Copilot session as ended on a worktree")
    sp.add_argument("--worktree-id", default=None,
                    help="Worktree ID (resolved from cwd or launch binding when omitted)")
    sp.add_argument("--session-id", required=True, help="Copilot session ID")
    sp.add_argument("--launch-id", dest="launch_id", default=None,
                    help="Launch-flow correlation id (from WORKTREE_LAUNCH_ID)")

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

    # head-session -- a worktree's asserted head session + lifecycle state (JSON)
    sp = sub.add_parser(
        "head-session",
        help="Show a worktree's asserted head (current) session + state (JSON)",
    )
    sp.add_argument("--worktree", "--worktree-id", dest="worktree_id",
                    required=True, help="Worktree ID (full or 4-char suffix)")
    sp.add_argument("--json", action="store_true",
                    help="Emit JSON (default; accepted for caller compatibility)")

    # conclude-session -- assert a session's conclusion (handed-off | concluded)
    sp = sub.add_parser(
        "conclude-session",
        help="Assert a session concluded (handed-off|concluded); advances the "
             "head off it (JSON) -- the durable write context-handoff's cutover "
             "shells to",
    )
    sp.add_argument("--worktree", "--worktree-id", dest="worktree_id",
                    required=True, help="Worktree ID (full or 4-char suffix)")
    sp.add_argument("--session", "--session-id", dest="session_id",
                    required=True, help="Copilot session ID to conclude")
    sp.add_argument("--state", choices=["handed-off", "concluded"],
                    default="handed-off",
                    help="Conclusion kind (default: handed-off)")
    sp.add_argument("--json", action="store_true",
                    help="Emit JSON (default; accepted for caller compatibility)")

    # link-succession -- write the two-way predecessor<->successor handoff link
    sp = sub.add_parser(
        "link-succession",
        help="Write the two-way predecessor<->successor link, conclude the "
             "predecessor, and move the head to the successor (JSON)",
    )
    sp.add_argument("--worktree", "--worktree-id", dest="worktree_id",
                    required=True, help="Worktree ID (full or 4-char suffix)")
    sp.add_argument("--predecessor", required=True,
                    help="The outgoing session ID (marked handed-off)")
    sp.add_argument("--successor", required=True,
                    help="The incoming session ID (the new head)")
    sp.add_argument("--predecessor-state", dest="predecessor_state",
                    choices=["handed-off", "concluded"], default="handed-off",
                    help="Predecessor conclusion kind (default: handed-off)")
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

    # recent-messages -- a worktree's latest session's last N conversation turns
    sp = sub.add_parser(
        "recent-messages",
        help="Show a worktree's latest session's last N conversation messages "
             "(JSON) -- the read-side companion to the disposition summary",
    )
    sp.add_argument("--worktree", "--worktree-id", dest="worktree_id",
                    required=True, help="Worktree ID (full or 4-char suffix)")
    sp.add_argument("--limit", type=int, default=3,
                    help="How many of the most recent messages to return "
                         "(default: 3)")
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
    sp.add_argument("--fetch", action="store_true",
                    help="Refresh the upstream ref before the behind-count "
                         "(slower; unneeded post pre-launch fetch)")
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
    sp.add_argument("--launch-id", dest="launch_id", default=None,
                    help="Filter to a single launch flow (correlation id)")
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
    sp.add_argument("--launch-id", dest="launch_id", default=None,
                    help="Launch-flow correlation id")
    sp.add_argument("--source", default="launcher")
    sp.add_argument("--field", action="append", default=[],
                    help="Extra context as key=value (repeatable)")

    # doctor -- diagnose (and with --fix) repair worktree/session health
    sp = sub.add_parser(
        "doctor",
        help="Diagnose (and with --fix, repair) worktree/session health: "
             "corrupt tracking records, empty session registries, stale "
             "status, orphaned empty session shells, cwd/path misalignment.",
    )
    sp.add_argument("--fix", action="store_true",
                    help="Apply non-destructive repairs (YAML integrity, "
                         "registry/title backfill, stale status). Default: "
                         "report only.")
    sp.add_argument("--gc-sessions", action="store_true", dest="gc_sessions",
                    help="With --fix, also delete empty (0-user-message) "
                         "session-state shells and purge their session-store "
                         "rows (destructive; guarded by age/lock/current/"
                         "registered).")
    sp.add_argument("--json", action="store_true",
                    help="Emit the health report as JSON.")

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

    if not wt_id:
        wt_id = _activate_session_binding(session_id)

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
        launch_id=getattr(args, "launch_id", None) or os.environ.get("WORKTREE_LAUNCH_ID"),
    )
    # Re-seed the status-bar updater for this session's mux (best-effort, no-op
    # off-mux).  The launcher spawns it at psmux create/join, but an attached
    # long-lived session is never re-run through the launcher -- so after a
    # deploy retires the old updater (dotfiles #911) the bar stays dark.  The
    # sessionStart hook re-asserts it every session; the @aw_updater token guard
    # keeps it single-instance (dotfiles #915).
    #
    # The updater renders the worktree the ``--path`` points at, so prefer the
    # hook payload's cwd (the worktree); fall back to the tracking record when
    # cwd is absent (e.g. wt_id came from the env, bare-resume path) so we never
    # hand the updater the plugin install dir.
    upd_path = cwd
    if not upd_path:
        try:
            rec_path = cfg.tracking_dir() / f"{wt_id}.yaml"
            if rec_path.exists():
                upd_path = tracking.load_record(rec_path).worktree_path
        except Exception:
            upd_path = None
    _spawn_status_updater(wt_id, upd_path)
    return 0


def _activate_session_binding(session_id: str | None) -> str | None:
    """Activate and return a scoped bare-resume worktree binding.

    The launcher publishes the tuple only for two-step bare resume. The exact
    session-id match is load-bearing: the initial temporary HOME session must
    remain unbound; only the historical session selected by ``/resume`` adopts
    the intended project/worktree context.
    """
    if not session_id or os.environ.get(_SESSION_BIND_SESSION) != session_id:
        return None
    worktree_id = os.environ.get(_SESSION_BIND_WORKTREE)
    project = os.environ.get(_SESSION_BIND_PROJECT)
    if not worktree_id or not project:
        return None
    try:
        resolved, _anchor = _resolve_active_project(project)
        if not resolved:
            return None
        cfg.set_active_project(resolved)
    except Exception:
        return None
    return worktree_id


def cmd_deregister_session(args: argparse.Namespace) -> int:
    """Mark a Copilot session as ended on a worktree (hook-invoked).

    Also captures the session summary/name from workspace.yaml and
    persists it to the tracking YAML ``title`` field (if not already
    set), ensuring the title survives session-state directory cleanup.
    """
    wt_id = getattr(args, "worktree_id", None)
    session_id = getattr(args, "session_id", None)
    if not wt_id:
        wt_id = _activate_session_binding(session_id)
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
        launch_id=getattr(args, "launch_id", None) or os.environ.get("WORKTREE_LAUNCH_ID"),
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


def _run_backfill(tracking_path: Path) -> dict:
    """Registry + title backfill core (shared by ``backfill-sessions`` and
    ``doctor``). Writes discovered session ids into empty registries and fills
    missing titles from the newest session's summary. Returns counts."""
    records = tracking.list_records(tracking_path)

    # --- Pass 1: session registry (only records with empty sessions) ---
    need_backfill = [r for r in records if not r.sessions]
    discovered: dict[str, list[str]] = {}
    sess_updated = 0
    if need_backfill:
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

    return {
        "scanned": len(need_backfill),
        "sessions": sum(len(v) for v in discovered.values()),
        "worktrees": len(discovered),
        "registry": sess_updated,
        "titles": titled,
    }


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
    r = _run_backfill(cfg.tracking_dir())
    if r["scanned"]:
        print(f"Scanning session-state for {r['scanned']} worktree(s)...")
    print(
        f"Backfilled {r['sessions']} session(s) across "
        f"{r['worktrees']} worktree(s); "
        f"{r['registry']} registry + {r['titles']} title record(s) updated"
    )
    return 0


def _current_session_ids() -> set[str]:
    """Session ids that must never be GC'd: the running agent's own session."""
    sid = os.environ.get("COPILOT_AGENT_SESSION_ID")
    return {sid} if sid else set()


def cmd_doctor(args: argparse.Namespace) -> int:
    """``doctor`` -- diagnose (and with ``--fix``, repair) worktree/session
    health for this project.

    Read-only by default. ``--fix`` applies non-destructive repairs
    (YAML integrity, registry/title backfill, stale status). ``--gc-sessions``
    (with ``--fix``) additionally removes empty session-state shells and purges
    their orphaned ``session-store.db`` rows. ``--json`` emits the report.
    """
    from . import health

    apply = getattr(args, "fix", False)
    do_gc = getattr(args, "gc_sessions", False)
    json_mode = getattr(args, "json", False)

    tracking_dir = cfg.tracking_dir()
    session_dir = sessions._session_state_dir()
    store_db = health.default_store_db(session_dir)

    # 1. YAML integrity -- FIRST so a repaired record is visible to the passes
    #    below (list_records silently skips an unparseable file).
    yaml_findings = health.repair_yaml_integrity(tracking_dir, apply=apply)

    # 2. Registry + title backfill (delegates to the shared core when applying;
    #    read-only scan otherwise -- backfill_sessions never writes).
    if apply:
        backfill = _run_backfill(tracking_dir)
    else:
        recs = tracking.list_records(tracking_dir)
        need = [r for r in recs if not r.sessions]
        disc = sessions.backfill_sessions(need) if need else {}
        backfill = {
            "scanned": len(need),
            "sessions": sum(len(v) for v in disc.values()),
            "worktrees": len(disc),
            "registry": len(disc),
            "titles": len([r for r in recs if not (r.title and r.title != "null")]),
        }

    records = tracking.list_records(tracking_dir)

    # 3. Stale status: active + completed_at -> complete
    stale = health.find_stale_status(records)
    stale_fixed = 0
    if apply:
        for r in stale:
            r.status = "complete"
            tracking.save_record(r)
            stale_fixed += 1

    # 4. Empty session-state shells (never GC the current or a registered one)
    exclude = health.registered_session_ids(records) | _current_session_ids()
    shells = health.find_empty_session_shells(
        session_dir, exclude_ids=frozenset(exclude))
    gc_result = health.gc_empty_shells(
        session_dir, store_db, shells, apply=(apply and do_gc))

    # 5. Alignment audit (report-only)
    misaligned = health.audit_alignment(records, session_dir)

    # 6. Bare (un-muxed) Copilot orphans -- machine-wide surfacing (report-only).
    #    Reclaiming stays operator-initiated: a bare session may be a live,
    #    actively-used non-mux terminal with no safe auto-reap signal, so doctor
    #    only *surfaces* it and points at the `reclaim` verb.
    bare_orphans = reclaim.find_bare_orphans()

    # 7. Runtime version lag (#533 Part C, report-only): a live daemon/coordinator
    #    still serving an older version than the installed payload. The launch
    #    path heals this (running-aware reconcile + zero-downtime cutover), but a
    #    running session can't restart its own daemon mid-turn -- surface it so the
    #    operator can `service restart` sooner instead of lagging silently.
    try:
        from . import reconcile as _reconcile

        repo_dir = _find_repo_dir()
        runtime_lag = _reconcile.running_version_lag(Path(repo_dir)) if repo_dir else []
    except Exception:
        runtime_lag = []

    try:
        proj_name = cfg.project_name()
    except Exception:
        proj_name = ""

    report = {
        "project": proj_name,
        "mode": "fix" if apply else "report",
        "yaml_integrity": {
            "bad": len(yaml_findings),
            "repairable": sum(1 for f in yaml_findings if f.repairable),
            "repaired": sum(1 for f in yaml_findings if f.repaired),
            "files": [
                {"file": f.path.name, "error": f.error,
                 "repairable": f.repairable, "repaired": f.repaired}
                for f in yaml_findings
            ],
        },
        "backfill": backfill,
        "stale_status": {"found": len(stale), "fixed": stale_fixed,
                         "ids": [r.worktree_id for r in stale]},
        "empty_sessions": gc_result,
        "misaligned": {"count": len(misaligned), "worktrees": misaligned},
        "bare_orphans": {"count": len(bare_orphans), "items": bare_orphans},
        "runtime_lag": runtime_lag,
    }

    if json_mode:
        _json_output(report)
        return 0

    _render_doctor_report(report, applied=apply, gc_applied=(apply and do_gc))
    return 0


def _render_doctor_report(report: dict, *, applied: bool, gc_applied: bool) -> None:
    chk = "\u2713"
    print(f"Worktree/session doctor ({'fix' if applied else 'report-only'})")

    yi = report["yaml_integrity"]
    if yi["bad"]:
        tail = f", {yi['repaired']} repaired" if applied else \
            f", {yi['repairable']} repairable"
        print(f"  ! Corrupt tracking records: {yi['bad']}{tail}")
        for f in yi["files"]:
            mark = "fixed" if f["repaired"] else (
                "repairable" if f["repairable"] else "manual")
            print(f"      - {f['file']} [{mark}] {f['error']}")
    else:
        print("  \u2713 Tracking records parse cleanly")

    bf = report["backfill"]
    if applied:
        print(f"  \u2713 Backfill: {bf['registry']} registry + "
              f"{bf['titles']} title record(s) updated "
              f"({bf['sessions']} session(s))")
    else:
        print(f"  \u2022 Backfill candidates: {bf['worktrees']} worktree(s) "
              f"w/ discoverable sessions, {bf['titles']} missing title(s)")

    ss = report["stale_status"]
    if ss["found"]:
        print(f"  {chk if applied else '!'} Stale status "
              f"(active + completed_at): {ss['found']} "
              f"{'fixed' if applied else 'found'} -> {', '.join(ss['ids'][:8])}")
    else:
        print(f"  {chk} No stale statuses")

    es = report["empty_sessions"]
    if es["count"]:
        if gc_applied:
            print(f"  \u2713 Empty session shells: removed {es['removed_dirs']} "
                  f"dir(s), purged {es['removed_rows']} store row(s)")
        else:
            hint = "" if applied else " (needs --fix --gc-sessions)"
            print(f"  \u2022 Empty session shells: {es['count']} "
                  f"candidate(s){hint}")
    else:
        print("  \u2713 No orphaned empty session shells")

    mis = report["misaligned"]
    if mis["count"]:
        print(f"  \u2022 Alignment audit: {mis['count']} session-less "
              f"worktree(s) point at a foreign parent cwd "
              f"(resume handled by Fix; informational)")
    else:
        print("  \u2713 No worktree/path misalignment")

    bo = report.get("bare_orphans", {"count": 0, "items": []})
    if bo["count"]:
        print(f"  \u2022 Bare (un-muxed) Copilot orphan(s): {bo['count']} "
              f"machine-wide (invisible to the mux fleet view)")
        for o in bo["items"][:8]:
            wt = o.get("worktree_id") or "?"
            print(f"      - {o['session_id'][:8]}  pid {o['pid']:<6} {wt}")
        print("      reclaim: agent-worktrees reclaim --worktree-id <id> "
              "--bare-only  (or --all)")
    else:
        print("  \u2713 No bare (un-muxed) Copilot orphans")

    lag = report.get("runtime_lag") or []
    if lag:
        print(f"  ! Runtime version lag: {len(lag)} service(s) serving older "
              f"code than installed")
        for entry in lag:
            print(f"      - {entry['service']}: running {entry['running']} but "
                  f"{entry['payload']} installed -> "
                  f"{entry['service']} service restart")
        print("      (a new launch heals this automatically; restart to "
              "converge this running session sooner)")
    else:
        print("  \u2713 Runtime services match installed payload")


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
    head_session: str | None = None
    for rec in records:
        for s in sessions.list_worktree_sessions(rec):
            s["worktree_id"] = rec.worktree_id
            result.append(s)
    # session-lifecycle: when scoped to ONE worktree, surface its asserted head
    # on the envelope so a consumer (agent-bridge -> Neuron Forge) can resolve
    # the current session without re-deriving it. Per-session ``is_head`` (from
    # list_worktree_sessions) covers the all-worktrees case. Derived from the
    # ground-layer record; no rival pointer (agent-fabric derive-dont-duplicate).
    if wt_id and records:
        head_session = records[0].resolved_head_session

    _json_output({"sessions": result, "head_session": head_session})
    return 0


def _all_tracking_dirs() -> list[Path]:
    """Every project's worktree-tracking dir on this machine (dedup, ordered).

    The active project (resolved from CWD, when there is one) comes first, then
    every project in the projects registry. This lets a **project-agnostic
    caller** -- notably the agent-bridge daemon, whose CWD is unrelated to the
    worktree it is guarding -- resolve a worktree by id without first knowing
    which project owns it. Never raises: a project that cannot resolve a dir is
    skipped.
    """
    dirs: list[Path] = []
    seen: set[Path] = set()

    def _add(d: Path | None) -> None:
        if d is not None and d not in seen:
            seen.add(d)
            dirs.append(d)

    try:
        _add(cfg.tracking_dir())
    except Exception:
        pass
    try:
        projects = inst.read_projects_registry().get("projects", {})
    except Exception:
        projects = {}
    for name in projects:
        try:
            _add(cfg.project_dir(name) / "worktrees")
        except Exception:
            continue
    return dirs


def _find_tracking_file(raw_id: str) -> Path | None:
    """Locate a worktree's tracking YAML across **all** projects, or None.

    Exact stem match wins globally; a unique 4-char (or longer) suffix match is
    the fallback. An ambiguous suffix (or no match) returns None -- the caller
    then treats the worktree as untracked (fail-open), never guessing.
    """
    import re
    if re.search(r"[/\\]|\.\.", raw_id):
        return None
    tdirs = _all_tracking_dirs()
    for tdir in tdirs:
        exact = tdir / f"{raw_id}.yaml"
        if exact.exists():
            return exact
    matches: list[Path] = []
    for tdir in tdirs:
        if not tdir.exists():
            continue
        matches += [p for p in tdir.glob("*.yaml") if p.stem.endswith(raw_id)]
    return matches[0] if len(matches) == 1 else None


def cmd_head_session(args: argparse.Namespace) -> int:
    """Emit a worktree's **asserted head session** and its lifecycle state (JSON).

    The ground-layer read that higher layers (agent-bridge's create guard,
    context-handoff) **derive** the current session from -- the source of truth
    for "which session is current in this worktree," so no other layer keeps a
    rival pointer (agent-fabric ``derive-dont-duplicate``).

    Output envelope::

        {"version": 1, "worktree_id": "<id>", "tracked": bool,
         "head_session": "<session-id>" | null, "active": bool,
         "state": "active" | "handed-off" | "concluded" | null}

    - ``head_session`` is ``WorktreeRecord.resolved_head_session`` -- the stored
      head when it is still un-concluded, else the newest non-concluded session
      (today's "latest is current" fallback), else null.
    - ``active`` is ``head_session is not None`` -- i.e. the worktree has a
      current, un-concluded session that a fresh create would collide with.
    - ``tracked`` is False when no tracking record exists for the worktree (an
      unknown / untracked worktree): a fail-open signal so a consumer treats it
      as "no head to guard."

    Resolves the worktree across **all** projects (see :func:`_find_tracking_file`)
    so the agent-bridge daemon can call it from any CWD. An unknown worktree is
    **not** an error (exit 0, ``tracked: false``): a guard that cannot find a
    record must fail *open*, not refuse the create.
    """
    raw = args.worktree_id
    yaml_path = _find_tracking_file(raw)
    if yaml_path is None:
        _json_output({
            "worktree_id": raw,
            "tracked": False,
            "head_session": None,
            "active": False,
            "state": None,
        })
        return 0
    record = tracking.load_record(yaml_path)
    head = record.resolved_head_session
    entry = record.session_entry(head) if head else None
    _json_output({
        "worktree_id": record.worktree_id or raw,
        "tracked": True,
        "head_session": head,
        "active": head is not None,
        "state": (entry.state if entry is not None else None),
    })
    return 0


def cmd_conclude_session(args: argparse.Namespace) -> int:
    """Assert a session's conclusion (``handed-off`` | ``concluded``) -- JSON out.

    The ground-layer WRITE that context-handoff's live cutover shells to so the
    retired session leaves a durable, asserted lifecycle record -- not merely a
    killed pane. Concluding the outgoing session ``handed-off`` advances the head
    off it (``resolved_head_session`` derives the newest survivor, or None),
    which is what closes the spent-baton replay: neither a stale replay nor the
    agent-bridge create guard treats the worktree as still holding the concluded
    session. The successor completes the two-way link when it registers
    (:func:`tracking.register_session`).

    Resolves the worktree across all projects (a higher-layer caller's CWD is
    unrelated to the worktree). Unlike the read-only ``head-session``, an unknown
    worktree or session is a real error here -- a mutation must not silently
    no-op.
    """
    raw = args.worktree_id
    yaml_path = _find_tracking_file(raw)
    if yaml_path is None:
        return _json_error(f"Worktree not found: {raw}")
    state = getattr(args, "state", "handed-off")
    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        try:
            tracking.conclude_session(
                record, args.session_id, state=state, save=False)
        except tracking.SessionLifecycleError as e:
            return _json_error(str(e))
        # Persist to the RESOLVED path, not ``record.yaml_path`` -- this verb is
        # project-agnostic (``_find_tracking_file`` searches every project), and
        # runs with no active project, so a bare ``save_record`` would recompute
        # the wrong (or an unresolvable) path.
        tracking.save_record(record, yaml_path)
    record = tracking.load_record(yaml_path)
    entry = record.session_entry(args.session_id)
    _json_output({
        "worktree_id": record.worktree_id or raw,
        "session": args.session_id,
        "state": (entry.state if entry is not None else None),
        "head_session": record.resolved_head_session,
    })
    return 0


def cmd_link_succession(args: argparse.Namespace) -> int:
    """Write the durable two-way handoff link and move the head -- JSON out.

    The explicit ground-layer form of ``tracking.link_succession``: chains
    ``predecessor -> successor`` in both directions, concludes the predecessor
    (default ``handed-off``), and moves the head to the successor. Both sessions
    must already be tracked -- so this is for callers that know BOTH ids (e.g. an
    explicit, non-cutover handoff or a manual repair). The live cutover instead
    concludes the predecessor via ``conclude-session`` and lets
    ``register_session`` stamp the successor half once its id exists.
    """
    raw = args.worktree_id
    yaml_path = _find_tracking_file(raw)
    if yaml_path is None:
        return _json_error(f"Worktree not found: {raw}")
    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        try:
            tracking.link_succession(
                record, args.predecessor, args.successor,
                predecessor_state=getattr(
                    args, "predecessor_state", "handed-off"),
                save=False,
            )
        except tracking.SessionLifecycleError as e:
            return _json_error(str(e))
        # Persist to the RESOLVED path (see cmd_conclude_session): this verb is
        # project-agnostic and runs with no active project.
        tracking.save_record(record, yaml_path)
    record = tracking.load_record(yaml_path)
    pred = record.session_entry(args.predecessor)
    _json_output({
        "worktree_id": record.worktree_id or raw,
        "predecessor": args.predecessor,
        "successor": args.successor,
        "predecessor_state": (pred.state if pred is not None else None),
        "head_session": record.resolved_head_session,
    })
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


def cmd_recent_messages(args: argparse.Namespace) -> int:
    """Emit a worktree's latest session's last N conversation messages as JSON.

    The read-side companion to the disposition ``summary`` overlay: when the
    agent-asserted summary never accumulated, this derives recent context
    straight from the worktree's newest session ``events.jsonl``. Accepts a full
    worktree id or its 4-char suffix. An unknown worktree is a JSON error; a
    known worktree with no session yields an empty ``messages`` list.
    """
    wt_id = _resolve_worktree_id(args.worktree_id)
    records = tracking.list_records(cfg.tracking_dir())
    rec = next((r for r in records if r.worktree_id == wt_id), None)
    if rec is None:
        return _json_error(f"No worktree found: {args.worktree_id}")
    payload = sessions.recent_worktree_messages(rec, limit=getattr(args, "limit", 3))
    payload["worktree_id"] = rec.worktree_id
    _json_output(payload)
    return 0


def cmd_reconcile_plugins(args: argparse.Namespace) -> int:
    """Reconcile repo-configured copilot-extensions plugins (JSON action plan).

    Reads the anchor repo's ``.github/copilot/settings.json`` ``enabledPlugins``
    and emits a declarative action plan (same shape as ``pre-launch``): ensure
    each plugin's payload is installed, and its runtime is deployed per the
    plugin's ``runtimeScope`` + facility machine gate. The launcher executes the
    ``argv`` vectors and re-invokes for a second pass (payload, then runtime).

    With ``--apply`` the plan is executed **in-process** (the same 2-pass loop),
    so a session that did not go through the worktree launcher still
    self-provisions an enabled plugin's runtime (dotfiles #693). With ``--peek``
    the plan is printed WITHOUT persisting the reconcile cache (a read-only
    preview the provision-check shim uses to decide whether to spawn the apply
    worker, with no throttle side effects).

    Never fails the launch: any error degrades to ``{"action": "continue"}``.
    """
    from . import reconcile

    repo_override = getattr(args, "repo", None)
    repo_dir = repo_override or _find_repo_dir()
    if not repo_dir:
        print(json.dumps({"action": "continue", "reason": "no-repo"}))
        return 0

    machine = getattr(args, "machine", None)

    if getattr(args, "apply", False):
        # Execute in-process (background self-provisioning path). Log to stderr
        # so a detached worker's output lands in the redirected setup log.
        def _log(msg: str) -> None:
            print(msg, file=sys.stderr, flush=True)

        try:
            summary = reconcile.apply_plan(Path(repo_dir), machine=machine, log=_log)
        except Exception as e:  # never raise from a background provision
            print(f"provision: error: {e}", file=sys.stderr)
            return 0
        print(json.dumps(summary))
        return 0

    try:
        plan = reconcile.build_plan(
            Path(repo_dir), machine=machine, save=not getattr(args, "peek", False)
        )
    except Exception as e:  # never break the launch
        print(json.dumps({"action": "continue", "reason": f"error: {e}"}))
        return 0

    print(json.dumps(plan))
    return 0


def cmd_reconcile_binstubs(args: argparse.Namespace) -> int:
    """Reconcile ~/.local/bin project binstubs against the projects registry."""
    inst.reconcile_binstubs()
    return 0


def cmd_register_project_entry(args: argparse.Namespace) -> int:
    """Write a lean projects.yaml entry -- the single Python owner of the
    registry write, invoked by both platform installers.

    ``expose_agent`` is authoritative in ``repos.yaml`` (the identity registry):
    unless explicitly forced with ``--expose-agent`` / ``--no-expose-agent``, it
    is resolved from the repo's ``agent`` classification, so a reference-only
    adoption stays reference-only across a re-register. All other fields default
    to preserve-existing in :func:`installer.register_project`.
    """
    project = args.project
    expose = getattr(args, "expose_agent", None)
    if expose is None:
        try:
            from . import repos as _repos
            entry = _repos.find_repo(project)
            if entry is not None:
                expose = entry.agent
        except Exception:
            expose = None

    inst.register_project(
        project,
        repo_dir=getattr(args, "repo_dir", None),
        expose_agent=expose,
        base_repo=getattr(args, "base_repo", None),
        elevated=getattr(args, "elevated", None),
        display_name=getattr(args, "display_name", None),
        wsl_state=getattr(args, "wsl_state", None),
        wsl_distro=getattr(args, "wsl_distro", None),
        wsl_path=getattr(args, "wsl_path", None),
    )
    return 0


def cmd_anchor_check(args: argparse.Namespace) -> int:
    """Check anchor repo for uncommitted work and stash entries."""
    from . import anchor_hygiene

    repo_path = getattr(args, "repo_path", None) or os.getcwd()
    use_json = getattr(args, "json", False)
    quiet = getattr(args, "quiet", False)
    strict = getattr(args, "strict", False)
    fetch = getattr(args, "fetch", False)

    try:
        report = anchor_hygiene.check_anchor(repo_path, fetch=fetch)
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


def cmd_config_migrate(args: argparse.Namespace) -> int:
    """Migrate machine-local config schemas in place (install/update eager path).

    Stamps/upgrades the ``schema_version`` on ``~/.agent-worktrees/{config,repos,
    projects}.yaml``. Idempotent and atomic per file; machine-local only (never
    touches repo-committed config -- that is an ``adopt`` concern). A per-file
    problem (malformed YAML, a file newer than this build) is reported, not
    fatal. Safe no-op when the vendored ``config_migrate`` library is absent.
    """
    from . import config, config_migrations

    quiet = getattr(args, "quiet", False)
    if not config_migrations.available():
        if not quiet:
            output.warn("config-migrate: migration library unavailable; skipping")
        return 0

    results = config_migrations.run_migrations(config.install_dir())
    if not quiet:
        print(config_migrations.summarize(results))
    return 0


def cmd_session_lock(args: argparse.Namespace) -> int:
    """Write or remove a session-state lattice lock (#4272).

    The producer-facing CLI for a per-session provable-liveness lock, so an
    out-of-process owner (agent-bridge, which already shells to
    ``agent-worktrees resolve``) can mark/unmark a Copilot session's liveness
    without importing this package. The lock lives beside Copilot's own
    ``inuse.<pid>.lock`` in ``<session-state>/<session>/<kind>.lock`` -- the
    lattice-in-one-dir shape the picker reads file-first.

    ``write`` records the OWNER process (``--pid``, e.g. the bridge-owned Copilot
    child) plus its start-time and the bound ``--worktree`` id, so a reader can
    both prove liveness and attribute the session cwd-independently (the #1416
    bare-session fix). ``remove`` clears it at clean teardown. Best-effort:
    never raises; a write failure returns nonzero.
    """
    from . import locks, sessions

    state_dir = sessions._session_state_dir()
    lock_path = state_dir / args.session / f"{args.kind}.lock"
    if args.action == "remove":
        locks.remove_lock(lock_path)
        if getattr(args, "json", False):
            print(json.dumps({"ok": True, "action": "remove",
                              "path": str(lock_path)}))
        return 0
    # write
    extra: dict = {"kind": args.kind, "session_id": args.session}
    if args.worktree:
        extra["worktree_id"] = args.worktree
    ok = locks.write_lock(lock_path, pid=args.pid, extra=extra)
    if getattr(args, "json", False):
        print(json.dumps({"ok": ok, "action": "write", "path": str(lock_path),
                          "worktree_id": args.worktree, "pid": args.pid}))
    elif not ok:
        print(f"session-lock: failed to write {lock_path}", file=sys.stderr)
    return 0 if ok else 1


COMMAND_MAP = {
    "resolve": cmd_resolve,
    "post-exit": cmd_post_exit,
    "session-lock": cmd_session_lock,
    "finalize": cmd_finalize,
    "push-changes": cmd_push_changes,
    "create-pr": cmd_create_pr,
    "pr-create": cmd_create_pr,  # pr-* family alias (also rewritten pre-argparse)
    "set-pr": cmd_set_pr,
    "pr-ready": cmd_pr_ready,
    "pr-status": cmd_pr_status,
    "pr-complete": cmd_pr_complete,
    "mark-complete": cmd_mark_complete,
    "status": cmd_status,
    "status-segment": cmd_status_segment,
    "status-context": cmd_status_context,
    "status-updater": cmd_status_updater,
    "handoff-cutover": cmd_handoff_cutover,
    "embody": cmd_embody,
    "list": cmd_list,
    "claims": cmd_claims,
    "claimant-liveness": cmd_claimant_liveness,
    "create": cmd_create,
    "run": cmd_run,
    "remove-system": cmd_remove_system,
    "cleanup": cmd_cleanup,
    "gc": cmd_gc,
    "reap-sessions": cmd_reap_sessions,
    "reap-shells": cmd_reap_shells,
    "reclaim": cmd_reclaim,
    "remux": cmd_remux,
    "restart": cmd_restart,
    "sync": cmd_sync,
    "profiles": cmd_profiles,
    "terminal-fragment": cmd_terminal_fragment,
    "repair": cmd_repair,
    "picker": cmd_picker,
    "validate": cmd_validate,
    "config-migrate": cmd_config_migrate,
    "install": cmd_install,
    "register": cmd_register,
    "unregister": cmd_uninstall,
    "uninstall": cmd_uninstall,
    "update": cmd_update,
    "install-status": cmd_install_status,
    "deploy-instructions": cmd_deploy_instructions,
    "machine-context": cmd_machine_context,
    "get": cmd_get,
    "pre-launch": cmd_pre_launch,
    "stage-update": cmd_stage_update,
    "reconcile-plugins": cmd_reconcile_plugins,
    "reconcile-binstubs": cmd_reconcile_binstubs,
    "register-project-entry": cmd_register_project_entry,
    "dev": cmd_dev,
    "register-session": cmd_register_session,
    "deregister-session": cmd_deregister_session,
    "backfill-sessions": cmd_backfill_sessions,
    "doctor": cmd_doctor,
    "list-sessions": cmd_list_sessions,
    "head-session": cmd_head_session,
    "conclude-session": cmd_conclude_session,
    "link-succession": cmd_link_succession,
    "session-transcript": cmd_session_transcript,
    "recent-messages": cmd_recent_messages,
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


# ── `<repo> <slug>` command-surface router ───────────────────────────────────
# The router DERIVES its routable set from the installed ``agent-<slug>``
# binstubs (so a newly-installed agent-* plugin auto-gets a `<repo> <slug>`
# namespace), unioned with a curated core set as a floor. A leading token that
# names a routable slug -- and is NOT a real worktrees verb (the collision guard)
# -- is dispatched to that sibling plugin. `worktrees` folds back into this
# binstub so `<repo> worktrees <verb>` == the bare `<repo> <verb>` alias.
_CORE_SLUGS = frozenset({
    "worktrees", "bridge", "ssh", "dispatch",
    "codespaces", "containers", "logger", "vault", "mcp",
})

# Slugs whose sibling plugin consumes a top-level ``--project`` (bridge overrides
# its remote-resolve target project; codespaces chdir's to the project checkout).
# The router injects ``--project <repo>`` only for these; every other slug routes
# as a cwd-preserving alias (so plugins that don't declare --project never see it
# and can't argparse-error on it).
_PROJECT_ARG_SLUGS = frozenset({"bridge", "codespaces"})


def _installed_sibling_slugs() -> set[str]:
    """Discover ``<slug>`` for every installed ``agent-<slug>`` binstub in
    ~/.local/bin, so the routable set is derived from what's installed rather than
    hardcoded. Excludes ``agent-worktrees`` itself (which folds back)."""
    import re as _re

    slugs: set[str] = set()
    try:
        entries = list(inst.local_bin().iterdir())
    except OSError:
        return slugs
    for p in entries:
        m = _re.match(
            r"^agent-([a-z0-9][a-z0-9-]*?)(?:\.(?:ps1|cmd|exe|sh))?$",
            p.name.lower(),
        )
        if m and m.group(1) != "worktrees":
            slugs.add(m.group(1))
    return slugs


_WORKTREES_VERBS: set[str] | None = None


def _worktrees_verbs() -> set[str]:
    """The agent-worktrees subcommand names (cached). The router excludes these
    from routing so a plugin slug can never shadow a real worktrees verb."""
    global _WORKTREES_VERBS
    if _WORKTREES_VERBS is None:
        import argparse

        try:
            parser = build_parser()
            subs = [a for a in parser._actions
                    if isinstance(a, argparse._SubParsersAction)]
            _WORKTREES_VERBS = set(subs[0].choices) if subs else set()
        except Exception:
            _WORKTREES_VERBS = set()
    return _WORKTREES_VERBS


def _canonical_slug(tok: str) -> str | None:
    """Map a leading token to a routable slug, tolerating singular/plural
    variance so the surface is forgiving of the plugins' inconsistent
    pluralization (``bridge``/``ssh`` singular; ``codespaces``/``containers``/
    ``worktrees`` plural). Returns the canonical slug (matching the actual
    ``agent-<slug>`` binstub) or None. The caller still gates on the collision
    guard (a real worktrees verb is never passed here)."""
    if tok in _CORE_SLUGS:
        return tok
    siblings = _installed_sibling_slugs()
    if tok in siblings:
        return tok
    # Toggle a trailing 's' and retry (codespace<->codespaces, worktree<->worktrees).
    alt = tok[:-1] if (tok.endswith("s") and len(tok) > 1) else tok + "s"
    if alt in _CORE_SLUGS or alt in siblings:
        return alt
    return None


def _sibling_binstub(slug: str) -> Path | None:
    """Locate the ``agent-<slug>`` binstub in ~/.local/bin (it runs in its own
    venv, so the router shells out to it rather than importing it)."""
    lb = inst.local_bin()
    cand = lb / (f"agent-{slug}.ps1" if platform.system() == "Windows"
                 else f"agent-{slug}")
    return cand if cand.exists() else None


def _route_to_sibling_plugin(slug: str, project: str | None,
                             rest: list[str]) -> int:
    """Re-dispatch ``<repo> <slug> …`` to the ``agent-<slug>`` binstub,
    project-pinned when a project is known. Returns the child's exit code."""
    stub = _sibling_binstub(slug)
    if stub is None:
        print(
            f"  \u2717 '{slug}' needs the agent-{slug} command, which is not "
            f"installed here.\n"
            f"  \u2717 Install its plugin, or run 'agent-worktrees --help' for "
            f"local commands.",
            file=sys.stderr,
        )
        return 1
    forwarded: list[str] = []
    child_env = os.environ.copy()
    if project:
        forwarded += ["--project", project]
        # Mark this --project as ROUTER-INJECTED (synthetic), so the sibling can
        # distinguish it from a user-typed explicit --project. The router injects
        # --project uniformly for _PROJECT_ARG_SLUGS, including on that plugin's
        # fleet-global verbs where it's a no-op; the sibling tolerates a *routed*
        # no-op silently but should bounce an *explicit* one (#1080).
        child_env["AGENT_WORKTREES_PROJECT_ROUTED"] = "1"
    else:
        # Authoritative: the marker means "THIS route injected --project". Never
        # let a stale/exported marker from the parent env leak to the child --
        # otherwise the child would treat a user's explicit --project (forwarded
        # in `rest`) as routed and silently ignore it (#1080). Don't trust
        # ambient env for identity.
        child_env.pop("AGENT_WORKTREES_PROJECT_ROUTED", None)
    forwarded += list(rest)
    if platform.system() == "Windows":
        pwsh = shutil.which("pwsh") or shutil.which("powershell") or "pwsh"
        cmd = [pwsh, "-NoProfile", "-NoLogo", "-File", str(stub), *forwarded]
    else:
        cmd = [str(stub), *forwarded]
    return subprocess.run(cmd, env=child_env).returncode


def _safe_cwd() -> Path | None:
    """Return ``Path.cwd()``, or ``None`` if the current directory is gone.

    ``os.getcwd()`` raises ``FileNotFoundError`` when the process's working
    directory has been removed out from under it. This happens when a plugin
    hook re-invokes this CLI during ``copilot plugin update``: Copilot deletes
    and re-vendors the payload directory the hook inherited as its cwd, so the
    hook subprocess ends up with a vanished cwd. Treat that as "no project
    context" rather than crashing startup (dotfiles#989).
    """
    try:
        return Path.cwd()
    except OSError:
        return None


def _git_toplevel(path: Path | None) -> Path | None:
    """Return the git toplevel of ``path`` resolved to its anchor, or None.

    ``path`` may be ``None`` (e.g. ``_safe_cwd()`` returned ``None`` because the
    caller's cwd was deleted); in that case there is nothing to resolve.
    """
    if path is None:
        return None
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
# Commands that run WITHOUT resolving a project context. ``reap-sessions`` is
# deliberately NOT here: although it enumerates mux sessions machine-wide, it
# correlates each against a project's tracking records (``cfg.tracking_dir()``
# -> ``project_name()``), so it must resolve a project like ``cleanup``/``gc``.
# Listing it here made the bare ``agent-worktrees reap-sessions`` binstub crash
# with a RuntimeError deep in ``project_name()`` (no project ever resolved);
# now it resolves from CWD, or from the ``--project`` a project binstub injects,
# and balks helpfully when neither is available.
_NO_PROJECT_COMMANDS = {
    "--version", "-V", "--help", "-h", "repos", "accounts", "related", "install", "register", "hook",
    "picker", "reap-shells", "status-updater", "restart", "register-session",
    "head-session", "conclude-session", "link-succession", "config-migrate",
    "session-lock", "machine-context",
}


# Machine-global verbs where ``--project`` can NEVER matter: the pure registries
# / info (``repos``/``accounts``), the cross-project ``picker``, and
# ``--version``/``--help``. A HAND-TYPED ``--project`` on these is silently
# ignored today (accept-and-ignore) -- the #1080 foot-gun -- so we bounce it.
# Deliberately a CONSERVATIVE subset of ``_NO_PROJECT_COMMANDS``: the other
# no-project verbs (``install``/``register``/``restart``/``status-updater``/
# ``session-*``) MAY consume an explicit ``--project``, so they are left to
# accept-and-ignore rather than risk bouncing a legitimate service/setup call.
# Machine-global verbs where ``--project`` has no effect: the pure registries /
# info (``repos``/``accounts``), the cross-project ``picker``, and
# ``--version``/``--help``. Passing ``--project`` to these does nothing;
# silently swallowing it is a minor foot-gun, but a HARD failure here is far
# worse: the ``<repo>`` binstub injects ``--project`` on every invocation, and an
# older, already-deployed binstub can't be expected to opt in to any marker. So
# the guard is a *soft, non-fatal note*, fired only when the project name is
# unregistered (a real binstub always injects a REGISTERED project, so normal
# ``<repo> repos`` etc. never warn -- no binstub/env cooperation required).
_PROJECT_IRRELEVANT_COMMANDS = frozenset({
    "repos", "accounts", "picker", "--version", "-V", "--help", "-h",
})


def _is_registered_project(name: str) -> bool:
    """True if *name* is a known adopted project or a registered repo.

    A real project binstub only ever injects a REGISTERED project as
    ``--project``, so this is how the guard tells a legitimate (binstub-injected
    or real) project name from a likely hand-typed mistake -- without requiring
    any binstub or environment cooperation.
    """
    try:
        if name in inst.read_projects_registry().get("projects", {}):
            return True
    except Exception:
        pass
    try:
        from . import repos as _repos
        if name in _repos.read_registry().repos:
            return True
    except Exception:
        pass
    return False


def _guard_project_scope(project_override: str | None,
                         command: str | None) -> None:
    """Softly note a likely-mistaken ``--project`` on a machine-global verb.

    ``--project`` has no effect on a machine-global verb
    (``repos``/``accounts``/``picker``/``--version``/``--help``). Rather than
    silently ignore an explicit one, emit a **soft, non-fatal** stderr note --
    but ONLY when the project name is not registered, since a real project
    binstub always injects a registered project. This never blocks and needs no
    binstub/env cooperation, so an older deployed binstub keeps working
    unchanged. (An earlier revision hard-bounced and relied on an
    ``AGENT_WORKTREES_PROJECT_ROUTED`` binstub marker; that broke stale binstubs
    on global verbs -- see the follow-up to #1108.)
    """
    # Defensive hygiene: never let a stray routed marker (set by the sibling
    # router in other flows) leak to child processes spawned by this verb.
    os.environ.pop("AGENT_WORKTREES_PROJECT_ROUTED", None)
    if not project_override:
        return
    if command not in _PROJECT_IRRELEVANT_COMMANDS:
        return
    if _is_registered_project(project_override):
        return
    print(
        f"note: --project {project_override!r} has no effect on the "
        f"machine-global command '{command}' and is not a known project; "
        f"ignoring it.",
        file=sys.stderr,
    )


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
    top = _git_toplevel(_safe_cwd())
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
    cwd_anchor = _git_toplevel(_safe_cwd())
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
    cwd_anchor = _git_toplevel(_safe_cwd())

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


def _pr_watch_usage() -> None:
    out = sys.stderr
    print("Usage: <project> pr-watch <wait|cursor> <owner/name> <pr> [options]", file=out)
    print(file=out)
    print("Block until a pull request moves, then wake the caller. The review", file=out)
    print("backend (host, token) is the repo's PR binding (.agent-worktrees/", file=out)
    print("config.yaml: provider / api_base / token_command).", file=out)
    print(file=out)
    print("  wait <repo> <pr>    Block until a transition or timeout.", file=out)
    print("    --until LIST      Comma-list of transitions or 'any'", file=out)
    print("                      (default: changes_requested,approved,conflict,", file=out)
    print("                       mergeable,checks_failed,approval_dismissed,", file=out)
    print("                       merged,closed)", file=out)
    print("    --since CURSOR     Baseline cursor (race-proof); omit to auto-baseline", file=out)
    print("    --timeout SECS     Max seconds to block (0 = no limit; default 3600)", file=out)
    print("    --interval SECS    Poll interval (> 0; default 20)", file=out)
    print("    --json             Emit only the result JSON on stdout", file=out)
    print("  cursor <repo> <pr>  Print the current baseline cursor for a PR.", file=out)
    print(file=out)
    print("  Overrides: --host URL (api base), --token TOKEN.", file=out)
    print(file=out)
    print("  One-shot read: `wait ... --timeout 1` returns the current-state", file=out)
    print("  snapshot (verdict/merge/consent) even on timeout; or use the", file=out)
    print("  worktree-scoped `pr-status` for the same live state without waiting.", file=out)
    print(file=out)
    print("  The result payload carries a 'merge' block describing what stands", file=out)
    print("  between the PR and a merge -- act on it after a review lands:", file=out)
    print("    needs_consent   true => approved+unblocked but the merge-consent", file=out)
    print("                    label is not applied yet; YOU must add it (the PR", file=out)
    print("                    will NOT merge on its own).", file=out)
    print("    consent_action  apply | already | skip  (+ 'reason').", file=out)
    print("    clear_to_merge  true when only consent stands in the way.", file=out)


def _pr_parse_repo(value: str) -> str:
    if value.count("/") != 1 or not all(value.split("/")):
        raise ValueError("repo must be a 'owner/name' (or ADO 'project/repo') slug")
    return value


def _infer_active_repo_slug(config: cfg.Config) -> str | None:
    """Provider-correct PR repo slug for the active project, or None.

    Lets the pr-* / ``repos`` verbs omit the explicit ``owner/name`` positional:
    resolves the active project's canonical remote (``_resolve_repo_remote`` --
    the registry remote, else the anchor's git origin) and parses the hosting
    slug from the URL. Provider-correct for **both** GitHub (``owner/name``) and
    Azure DevOps (``project/repo`` -- e.g. ``ExampleProject/example-repo``).
    Returns None when the remote is unresolvable (caller then requires the
    positional).
    """
    try:
        remote = _resolve_repo_remote(config, config.default_repo)
    except Exception:
        return None
    return git_ops.slug_from_url(remote)


def _classify_pr_operands(operands: list[str]) -> tuple[str | None, int | None]:
    """Split free-form pr-merge operands into ``(repo_slug, pr_number)``.

    A token containing ``/`` is the provider repo slug (``owner/name`` or ADO
    ``project/repo``); an all-digit token is the PR number. This is what lets
    the repo be **omitted** (inferred from the active project) while a bare PR
    number is never mistaken for a slug -- e.g. ``pr-merge 2333486`` resolves to
    ``(None, 2333486)``, not a bogus repo. Raises ``ValueError`` on a duplicate
    or unrecognized token.
    """
    repo: str | None = None
    pr: int | None = None
    for tok in operands:
        if "/" in tok:
            if repo is not None:
                raise ValueError(f"unexpected extra repo argument {tok!r}")
            repo = _pr_parse_repo(tok)
        elif tok.isdigit():
            if pr is not None:
                raise ValueError(f"unexpected extra PR number {tok!r}")
            pr = int(tok)
        else:
            raise ValueError(
                f"unrecognized argument {tok!r} (expected a repo slug "
                "-- owner/name or ADO project/repo -- or a PR number)"
            )
    return repo, pr


def cmd_pr_watch_dispatch(argv: list[str]) -> int:
    """Route `pr-watch` verbs (wait / cursor) -- the provider-generic watcher.

    The network+timing loop lives in :mod:`agent_worktrees.pr_watch`; the pure
    transition logic in :mod:`agent_worktrees.pr_contract`; the provider read in
    the provider plugins. This dispatcher wires the CLI onto the repo's PR
    binding (host/token/provider from config).
    """
    import json as _json

    from . import pr_contract as pc
    from . import pr_watch as prw
    from .providers import ProviderError

    if not argv or argv[0] in ("--help", "-h", "help"):
        _pr_watch_usage()
        return 0 if argv and argv[0] in ("--help", "-h", "help") else 1

    verb = argv[0]
    if verb not in ("wait", "cursor"):
        output.err(f"Unknown pr-watch subcommand: {verb}")
        _pr_watch_usage()
        return 1

    p = argparse.ArgumentParser(prog=f"pr-watch {verb}", add_help=True)
    p.add_argument("repo", type=_pr_parse_repo, nargs="?", default=None,
                   help="repo slug -- owner/name or ADO project/repo (optional; "
                        "inferred from the active project)")
    p.add_argument("pr", type=int, help="PR number")
    p.add_argument("--host", default="",
                   help="API base URL override (else the binding's api_base)")
    p.add_argument("--token", default=None, help="Provider token override (else the binding)")
    p.add_argument("--config", default=None)
    if verb == "wait":
        p.add_argument("--until", default=",".join(pc.DEFAULT_UNTIL),
                       help="comma-list of transitions or 'any'")
        p.add_argument("--since", default=None, help="baseline cursor (omit to auto-baseline)")
        p.add_argument("--timeout", type=float, default=3600.0,
                       help="max seconds to block (0 = no limit)")
        p.add_argument("--interval", type=float, default=20.0, help="poll interval seconds")
        p.add_argument("--json", action="store_true", help="emit only the result JSON")
    try:
        args = p.parse_args(argv[1:])
    except SystemExit as exc:
        return int(exc.code or 0)

    # Parse/validate --until.
    if verb == "wait":
        raw = args.until.strip().lower()
        if raw == "any":
            until = ["any"]
        else:
            until = [v.strip() for v in args.until.split(",") if v.strip()]
            bad = [v for v in until if v not in pc.ALL_TRANSITIONS]
            if bad:
                output.err(f"unknown transition(s) {bad}; choose from "
                           f"{', '.join(pc.ALL_TRANSITIONS)} or 'any'")
                return 2
        if args.timeout < 0:
            output.err("--timeout must be >= 0 (0 = no limit)")
            return 2
        if args.interval <= 0:
            output.err("--interval must be > 0")
            return 2

    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
        prcfg = config.default_repo.pr
        if args.repo is None:
            args.repo = _infer_active_repo_slug(config)
            if not args.repo:
                output.err("pr-watch: could not infer the repo from the active "
                           "project; pass an explicit repo slug")
                return 2
        fetch = prw.build_fetch(prcfg, args.repo, args.pr,
                                api_base=args.host, token=args.token)
        if verb == "cursor":
            snap = fetch()
            print(pc.Baseline.from_snapshot(snap).to_cursor())
            return 0

        baseline = pc.Baseline.from_cursor(args.since) if args.since else None
        if not args.json:
            mode = f"since {args.since}" if args.since else "auto-baseline"
            print(f"pr-watch: watching {args.repo}#{args.pr} for [{', '.join(until)}] "
                  f"({mode}, every {args.interval:g}s, timeout {args.timeout:g}s)",
                  file=sys.stderr)
        result = prw.run_wait(
            repo=args.repo, pr=args.pr, until=until, baseline=baseline,
            fetch=fetch, timeout=args.timeout, interval=args.interval,
            automerge_label=getattr(prcfg, "automerge_label", "") or "",
            hold_labels=tuple(getattr(prcfg, "hold_labels", ()) or ()),
            wip_title_prefixes=tuple(getattr(prcfg, "wip_title_prefixes", ()) or ()),
            approval_required=bool(getattr(prcfg, "approval_required", True)),
            on_error=lambda e: print(f"pr-watch: poll error (will retry): {e}",
                                     file=sys.stderr),
        )
        if not result.matched:
            # #3486: a timeout still carries the current-state snapshot (verdict
            # / merge state / consent / labels) when a poll succeeded, so a
            # short-timeout pr-watch doubles as a one-shot read. Preserve the
            # legacy {repo, pr, timed_out} keys and enrich with the snapshot.
            payload = dict(result.payload) if result.payload else {}
            payload.setdefault("repo", args.repo)
            payload.setdefault("pr", args.pr)
            payload["timed_out"] = True
            print(_json.dumps(payload))
            if not args.json:
                print(f"pr-watch: timed out after {args.timeout:g}s", file=sys.stderr)
                merge = payload.get("merge") or {}
                if merge:
                    conflict = " (conflict)" if merge.get("conflict") else ""
                    print(
                        f"pr-watch: current state -> verdict "
                        f"{merge.get('verdict', '?')}, merge "
                        f"{merge.get('merge_state', '?')}, consent "
                        f"{merge.get('consent_action', '?')}{conflict}",
                        file=sys.stderr,
                    )
                    print(
                        "pr-watch: (this is a one-shot read; `pr-status` gives "
                        "the same live state without waiting)", file=sys.stderr,
                    )
            return 124
        print(_json.dumps(result.payload))
        if not args.json:
            print(f"pr-watch: {args.repo}#{args.pr} -> "
                  f"{', '.join(result.payload['transitions'])}", file=sys.stderr)
            # Surface the next action so a woken caller doesn't assume "approved
            # == done": an approved+unblocked PR still needs merge consent.
            merge = result.payload.get("merge") or {}
            if merge.get("needs_consent"):
                label = merge.get("consent_label") or "the merge-consent label"
                print(f"pr-watch: NEXT -> grant merge consent (add label "
                      f"'{label}') -- the PR will not merge until you do "
                      f"({merge.get('reason', '')})", file=sys.stderr)
            elif merge.get("consent_action") == "already":
                print("pr-watch: merge consent already granted; the merge gate "
                      "will proceed", file=sys.stderr)
        return 0
    except ProviderError as exc:
        output.err(f"pr-watch: {exc}")
        return 3
    except ValueError as exc:
        output.err(f"pr-watch: {exc}")
        return 2


def _pr_merge_usage() -> None:
    out = sys.stderr
    print("Usage: <project> pr-merge <owner/name> <pr> [options]", file=out)
    print("       <project> pr-merge <owner/name> --all [options]", file=out)
    print(file=out)
    print("Signal merge consent on an APPROVED PR by applying the repo's", file=out)
    print("merge-consent label (the .agent-worktrees/config.yaml binding", file=out)
    print("automerge_label; facility: auto-merge). Applies by default; it never", file=out)
    print("merges -- the review gate still decides. Only eligible PRs are", file=out)
    print("touched (approved at head, mergeable, not draft/WIP, no hold label,", file=out)
    print("targeting the default branch).", file=out)
    print(file=out)
    print("  <pr>            Consent to one PR (the author path).", file=out)
    print("  --all           Sweep every open PR (transition-helper mode).", file=out)
    print("  --now           (submitter-self-merge repos only) merge <pr>", file=out)
    print("                  directly now (squash). Refused where the submitter", file=out)
    print("                  does not self-merge -- use pr-watch/pr-status there.", file=out)
    print("  --dry-run       Preview classification only; apply nothing.", file=out)
    print("  --loop          (sweep) Repeat until no PR remains eligible.", file=out)
    print("  --interval S    (sweep+loop) Seconds between passes (default 30).", file=out)
    print("  --max-passes N  (sweep+loop) Cap passes (0 = unbounded).", file=out)
    print("  --json          Emit the result JSON on stdout.", file=out)
    print("  Overrides: --host URL (api base), --token TOKEN.", file=out)


def _pr_merge_print_human(summary: dict) -> None:
    mode = "APPLY" if summary["apply"] else "preview (dry-run)"
    output_line = (f"pr-merge [{mode}] {summary['repo']}: {summary['open']} open, "
                   f"{summary['eligible']} eligible for auto-merge")
    print(output_line, file=sys.stderr)
    for d in summary["decisions"]:
        if d["action"] == "apply":
            if not summary["apply"]:
                mark = "+ auto-merge"
            elif d.get("applied"):
                mark = "APPLIED"
            else:
                mark = f"FAILED ({d.get('error', '')})"
        elif d["action"] == "already":
            mark = "already"
        else:
            mark = f"skip: {d.get('reason', '')}"
        print(f"  #{d['pr']:<6} {mark:<14} {d.get('title', '')}", file=sys.stderr)


def _pr_merge_now(args, prcfg, flow, *, apply: bool) -> int:
    """Perform (or preview) a direct submitter self-merge -- ``pr-merge --now``.

    Only a **pr-self-merge** repo (the owner of a PR-required repo with a
    non-blocking bot review) may self-merge. Honoring the repo's ``prefer_auto_merge``
    policy (#225, default on): it first tries the provider's native CI-gated
    auto-merge (``enable_auto_merge`` -- the PR lands on its own once required
    checks pass) and falls back to an immediate squash merge (``merge_pull``,
    ``--admin`` past the non-blocking gate) only where auto-merge is unavailable
    or ``prefer_auto_merge`` is off. Either way it is the OWNER's sanctioned
    self-merge verb, never an agent ad-hoc bypass. Any other profile is
    refused-with-reminder, steering the agent to the sanctioned wait/consent path.
    Returns a shell exit code (0 success, 1 merge failure, 2 refusal/usage).
    """
    import json as _json

    from . import pr_contract as pc
    from .providers import ProviderError, account_token_for_slug, get_provider

    if args.sweep:
        output.err("pr-merge --now: name a single PR number (not --all).")
        return 2

    # Only submitter-self-merge repos self-merge. Refuse elsewhere with a
    # reminder that names the sanctioned path (never a raw provider merge).
    if flow.profile != pc.PROFILE_PR_SELF_MERGE:
        reason = (
            "--now performs a direct submitter self-merge; this repo's PR-flow "
            f"profile is '{flow.profile}', which does not self-merge"
        )
        rem = pc.pr_reminder(flow, "pr-merge", ok=False, reason=reason)
        if args.json:
            print(_json.dumps({
                "repo": args.repo, "pr": args.pr,
                "error": "--now not applicable to this repo's flow",
                "flow_profile": flow.profile, "merge_mode": flow.merge_mode,
                "applied": False, "reminder": rem.as_dict(),
            }))
        else:
            output.err(f"pr-merge --now: {reason}. Nothing merged.")
            print(rem.text(), file=sys.stderr)
        return 2

    provider = get_provider(getattr(prcfg, "provider", "gitea") or "gitea")
    base = (args.host or getattr(prcfg, "api_base", "") or "").strip()
    prefer_auto = bool(getattr(prcfg, "prefer_auto_merge", True))

    if not apply:  # --dry-run: preview only, merge nothing.
        rem = pc.pr_reminder(flow, "pr-merge", ok=True)
        would = (
            "request CI-gated native auto-merge (fallback: direct squash-merge)"
            if prefer_auto else "squash-merge directly (submitter self-merge)"
        )
        if args.json:
            print(_json.dumps({
                "repo": args.repo, "pr": args.pr, "action": "dry-run",
                "would": would, "prefer_auto_merge": prefer_auto,
                "flow_profile": flow.profile, "applied": False,
                "reminder": rem.as_dict(),
            }))
        else:
            output.ok(
                f"pr-merge --now (dry-run): would {would} for PR #{args.pr} in "
                f"{args.repo}. Nothing merged."
            )
            print(rem.text(), file=sys.stderr)
        return 0

    tok = args.token if args.token is not None else account_token_for_slug(args.repo, prcfg)

    # Policy: prefer the provider's native CI-gated auto-merge (so the merge
    # waits on required checks) and fall back to an immediate self-merge only
    # where the provider offers no auto-merge or it can't be armed (#225).
    auto_armed = False
    if prefer_auto:
        try:
            auto_err = provider.enable_auto_merge(
                args.repo, args.pr, squash=True, api_base=base, token=tok,
            )
        except ProviderError as exc:
            auto_err = str(exc)
        auto_armed = not auto_err

    if auto_armed:
        # Auto-merge is armed -- the PR is NOT merged yet; it lands when checks
        # pass. Steer the agent to watch for the merge, not to finalize.
        rem = pc.pr_reminder(flow, "pr-watch", ok=True)
        if args.json:
            print(_json.dumps({
                "repo": args.repo, "pr": args.pr, "action": "auto-merge",
                "applied": True, "merged": False, "flow_profile": flow.profile,
                "reminder": rem.as_dict(),
            }))
        else:
            output.ok(
                f"pr-merge --now: armed CI-gated auto-merge on PR #{args.pr} in "
                f"{args.repo} (squash). It merges when required checks pass -- "
                f"`pr-watch` wakes you on merge or regression."
            )
            print(rem.text(), file=sys.stderr)
        return 0

    try:
        err = provider.merge_pull(
            args.repo, args.pr, squash=True, admin=True, api_base=base, token=tok,
        )
    except ProviderError as exc:
        err = str(exc)

    if err:
        rem = pc.pr_reminder(flow, "pr-merge", ok=False,
                             reason="the direct merge did not complete")
        if args.json:
            print(_json.dumps({
                "repo": args.repo, "pr": args.pr, "action": "merge",
                "applied": False, "error": err, "flow_profile": flow.profile,
                "reminder": rem.as_dict(),
            }))
        else:
            output.err(
                f"pr-merge --now: failed to merge PR #{args.pr} in {args.repo}: {err}"
            )
            print(rem.text(), file=sys.stderr)
        return 1

    rem = pc.pr_reminder(flow, "pr-merge", state=pc.PR_STATE_MERGED, ok=True)
    if args.json:
        print(_json.dumps({
            "repo": args.repo, "pr": args.pr, "action": "merge",
            "applied": True, "flow_profile": flow.profile,
            "reminder": rem.as_dict(),
        }))
    else:
        output.ok(
            f"pr-merge --now: squash-merged PR #{args.pr} in {args.repo} "
            f"(submitter self-merge). Run `finalize` to clean up the worktree."
        )
        print(rem.text(), file=sys.stderr)
    return 0


def cmd_pr_merge_dispatch(argv: list[str]) -> int:
    """Route `pr-merge` -- signal merge consent (apply the consent label).

    The pure eligibility classifier lives in :mod:`agent_worktrees.pr_contract`
    (``classify_state``); the apply/sweep orchestration in
    :mod:`agent_worktrees.pr_merge`; the label-apply in the provider. The
    consent-label vocabulary is the repo's PR binding (``automerge_label`` etc.).
    """
    import json as _json

    from . import pr_merge as pm
    from . import pr_contract as pc
    from .providers import ProviderError

    if argv and argv[0] in ("--help", "-h", "help"):
        _pr_merge_usage()
        return 0

    p = argparse.ArgumentParser(prog="pr-merge", add_help=True)
    p.add_argument("operands", nargs="*", metavar="[repo] [pr]",
                   help="repo slug -- owner/name or ADO project/repo (optional; "
                        "inferred from the active project) -- and/or PR number, "
                        "in any order")
    p.add_argument("--all", action="store_true", dest="sweep",
                   help="sweep every open PR (transition-helper mode)")
    p.add_argument("--dry-run", action="store_true",
                   help="preview classification only; apply nothing")
    p.add_argument("--loop", action="store_true",
                   help="(sweep) repeat until no PR remains eligible")
    p.add_argument("--interval", type=float, default=30.0)
    p.add_argument("--max-passes", type=int, default=0, dest="max_passes")
    p.add_argument("--host", default="", help="API base URL override")
    p.add_argument("--token", default=None, help="Provider token override")
    p.add_argument("--now", action="store_true",
                   help="(submitter-self-merge repos) merge the PR directly now "
                        "(squash); refused where the submitter does not self-merge")
    p.add_argument("--json", action="store_true", help="emit the result JSON")
    p.add_argument("--config", default=None)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    # A repo slug (contains '/') and/or a PR number (all digits) may be given in
    # any order; the slug is optional and inferred below when omitted, so a bare
    # `pr-merge <#>` is never mistaken for a repo.
    try:
        args.repo, args.pr = _classify_pr_operands(args.operands)
    except ValueError as exc:
        output.err(f"pr-merge: {exc}")
        return 2

    if args.sweep and args.pr is not None:
        output.err("pr-merge: pass either a <pr> or --all, not both")
        return 2
    if not args.sweep and args.pr is None:
        output.err("pr-merge: provide a PR number, or --all to sweep")
        return 2

    apply = not args.dry_run
    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
        if args.repo is None:
            args.repo = _infer_active_repo_slug(config)
            if not args.repo:
                output.err("pr-merge: could not infer the repo from the active "
                           "project; pass an explicit repo slug")
                return 2
        repo_cfg = config.default_repo
        prcfg = repo_cfg.pr
        default_branch = repo_cfg.default_branch
        flow = _pr_flow_profile(repo_cfg)

        # --now: perform the direct submitter self-merge (pr-self-merge repos).
        if args.now:
            return _pr_merge_now(args, prcfg, flow, apply=apply)

        # A submitter-self-merge repo has no consent label: bare `pr-merge` is a
        # no-op here. Refuse-with-reminder and point at the sanctioned `--now`
        # verb (handled above), NOT the human-merge/stale-anchor path below.
        if flow.profile == pc.PROFILE_PR_SELF_MERGE:
            rem = pc.pr_reminder(
                flow, "pr-merge", ok=False,
                reason="this repo merges directly (self-merge); bare pr-merge does nothing",
            )
            if args.json:
                print(_json.dumps({
                    "repo": args.repo,
                    "error": "self-merge repo: use pr-merge --now",
                    "flow_profile": flow.profile,
                    "merge_mode": flow.merge_mode,
                    "applies": False,
                    "hint": "submitter-self-merge repo: merge directly with "
                            "`pr-merge <#> --now`",
                    "reminder": rem.as_dict(),
                }))
            else:
                output.err(
                    "pr-merge: this repo's PR-flow profile is 'pr-self-merge' -- "
                    "the submitter merges directly. Bare pr-merge applies no "
                    "consent label here; merge now with `pr-merge <#> --now`. "
                    "Nothing applied."
                )
                print(rem.text(), file=sys.stderr)
            return 2

        if not getattr(prcfg, "automerge_label", ""):
            # No merge-consent label bound -> pr-merge cannot apply consent here.
            # Two distinct causes wear the same face; name both and point at the
            # right process for each (see the repo's PR-flow profile):
            #   (a) HUMAN-MERGE repo -- PR-gated, but a human approves + merges.
            #       pr-merge legitimately does not apply; open the PR, address
            #       review (pr-watch), and let a human merge.
            #   (b) STALE ANCHOR -- a repo that *should* have the binding, whose
            #       checkout hasn't pulled it yet. Refresh the anchor and retry;
            #       do NOT hand-merge or escalate.
            # (pr-self-merge repos are handled above; this path is human-merge.)
            msg = (
                "pr-merge: no merge-consent label (pr.automerge_label) is bound "
                f"in this repo's .agent-worktrees/config.yaml on this machine. "
                f"This repo's PR-flow profile is '{flow.profile}'. Two cases:\n"
                "  - Human-merge repo (expected): PR-gated but a HUMAN approves "
                "and merges -- pr-merge does not apply. Open the PR (create-pr), "
                "address review with pr-watch, then a human merges. Check the "
                "repo's CONTRIBUTING / review process for who merges.\n"
                "  - Stale anchor (if you EXPECTED an auto-merge label here): "
                "this checkout is likely behind -- update the anchor "
                "('aperture-labs update' / 'git sync' on the anchor) so the "
                "binding is present, then retry pr-merge. Do NOT hand-merge or "
                "escalate to an admin. Nothing applied."
            )
            if args.json:
                print(_json.dumps({
                    "repo": args.repo,
                    "error": "no automerge_label binding",
                    "flow_profile": flow.profile,
                    "merge_mode": flow.merge_mode,
                    "applies": False,
                    "hint": ("human-merge repo: a human merges (pr-merge N/A); "
                             "OR stale anchor if an auto-merge label was "
                             "expected -- update the anchor and retry"),
                    "reminder": pc.pr_reminder(
                        flow, "pr-merge", ok=False,
                        reason="no merge-consent label bound",
                    ).as_dict(),
                }))
            else:
                output.err(msg)
                _rem = pc.pr_reminder(
                    flow, "pr-merge", ok=False,
                    reason="no merge-consent label bound",
                )
                print(_rem.text(), file=sys.stderr)
            return 2

        if args.sweep:
            summary = pm.run_sweep(
                prcfg, args.repo, api_base=args.host, token=args.token, apply=apply,
                loop=args.loop, interval=args.interval, max_passes=args.max_passes,
                default_branch=default_branch,
            )
        else:
            row = pm.merge_one(
                prcfg, args.repo, args.pr, api_base=args.host, token=args.token,
                apply=apply, default_branch=default_branch,
            )
            eligible = 1 if row["action"] == "apply" else 0
            applied = 1 if row.get("applied") else 0
            failed = 1 if (row["action"] == "apply" and apply and not row.get("applied")) else 0
            summary = {
                "repo": args.repo, "open": 1, "eligible": eligible,
                "applied": applied, "failed": failed, "apply": apply,
                "decisions": [row],
            }

        if args.json:
            print(_json.dumps(summary))
        else:
            _pr_merge_print_human(summary)

        # Single-PR (author) mode: the operator named one PR and expects a
        # concrete state transition. A "skip" here means the action does NOT
        # apply to that PR (unapproved, not mergeable, draft/WIP, already merged,
        # hold label, wrong base) -- that is an ERROR, not success. A no-op must
        # never masquerade as success (issue #2779 / the defect that stranded
        # PR #2774). --all sweep mode legitimately skips ineligible PRs.
        if not args.sweep:
            row = summary["decisions"][0]
            action = row["action"]
            n = row["pr"]
            if action == "skip":
                if not args.json:
                    output.err(
                        f"pr-merge: PR #{n} in {args.repo} is not eligible for "
                        f"merge consent ({row.get('reason', 'ineligible')}); no "
                        f"consent applied. pr-merge only applies to an APPROVED, "
                        f"mergeable, non-draft PR -- nothing changed."
                    )
                return 1
            if action == "apply" and apply and not row.get("applied"):
                if not args.json:
                    output.err(
                        f"pr-merge: failed to apply merge consent to PR #{n} in "
                        f"{args.repo}: {row.get('error', 'unknown error')}"
                    )
                return 1
            if not args.json and apply:
                if action == "already":
                    output.ok(
                        f"pr-merge: merge consent already granted on PR #{n} in "
                        f"{args.repo}; the review gate will merge when satisfied."
                    )
                elif action == "apply" and row.get("applied"):
                    output.ok(
                        f"pr-merge: applied auto-merge consent to PR #{n} in "
                        f"{args.repo}; the review gate will merge when satisfied."
                    )
            return 0

        return 1 if summary["failed"] else 0
    except ProviderError as exc:
        output.err(f"pr-merge: {exc}")
        return 3
    except ValueError as exc:
        output.err(f"pr-merge: {exc}")
        return 2


def _pr_usage() -> None:
    out = sys.stderr
    print("Usage: <project> pr <verb> [args...]", file=out)
    print(file=out)
    print("Author-side PR command family (verbs also available flat as pr-*):", file=out)
    print("  create   Open a PR from the worktree (= create-pr)", file=out)
    print("  watch    Block until the PR moves (= pr-watch)", file=out)
    print("  merge    Signal merge consent on an approved PR (= pr-merge)", file=out)
    print("  status   Read tracked PR metadata (= pr-status)", file=out)
    print("  complete Reconcile the worktree after merge (= pr-complete)", file=out)
    print("  ready    Move a PR out of draft, ready-for-review (= pr-ready)", file=out)
    print("  research Inspect the repo's provider settings -> policy matrix "
          "(= pr-research)", file=out)


def cmd_pr_research_dispatch(argv: list[str]) -> int:
    """Route ``pr-research`` -- read the repo's live provider settings and derive
    the policy matrix to match (#225). Read-only: it PRINTS the suggested ``pr:``
    policy keys (never writes config), so an operator/agent can align the config
    with the repo's real settings instead of a guess.
    """
    import json as _json

    from . import pr_contract as pc
    from .providers import ProviderError, account_token_for_slug, get_provider

    if argv and argv[0] in ("--help", "-h", "help"):
        print("Usage: <project> pr-research [repo] [--default-branch B] "
              "[--host URL] [--token T] [--json]", file=sys.stderr)
        print(file=sys.stderr)
        print("Read the repo's live provider settings (allowed merge methods, "
              "native auto-merge,", file=sys.stderr)
        print("delete-branch-on-merge, required reviews/checks) and derive the "
              "repo-overridable", file=sys.stderr)
        print("`pr:` policy matrix to match. Read-only -- prints a suggestion; "
              "writes nothing. The repo is inferred from the active project when "
              "omitted.", file=sys.stderr)
        return 0

    p = argparse.ArgumentParser(prog="pr-research", add_help=True)
    p.add_argument("repo", type=_pr_parse_repo, nargs="?", default=None,
                   help="repo slug -- owner/name or ADO project/repo (optional; "
                        "inferred from the active project)")
    p.add_argument("--default-branch", default="", dest="default_branch",
                   help="branch to read protection from (defaults to repo config)")
    p.add_argument("--host", default="", help="API base URL override")
    p.add_argument("--token", default=None, help="Provider token override")
    p.add_argument("--json", action="store_true", help="emit the result JSON")
    p.add_argument("--config", default=None)
    try:
        args = p.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)

    try:
        config = cfg.load_config(Path(args.config) if args.config else None)
        if args.repo is None:
            args.repo = _infer_active_repo_slug(config)
            if not args.repo:
                raise ValueError("could not infer the repo from the active "
                                 "project; pass an explicit repo slug")
        repo_cfg = config.default_repo
        prcfg = repo_cfg.pr
        provider = get_provider(getattr(prcfg, "provider", "gitea") or "gitea")
        base = (args.host or getattr(prcfg, "api_base", "") or "").strip()
        branch = args.default_branch or repo_cfg.default_branch or ""
        tok = args.token if args.token is not None else \
            account_token_for_slug(args.repo, prcfg)
        policy = provider.get_repo_policy(
            args.repo, default_branch=branch, api_base=base, token=tok)
    except ProviderError as exc:
        output.err(f"pr-research: {exc}")
        return 1
    except Exception as exc:  # config / resolution failure
        output.err(f"pr-research: {exc}")
        return 1

    matrix = pc.derive_policy_matrix(policy)
    settings = {
        "supported": policy.supported,
        "allow_squash": policy.allow_squash,
        "allow_merge_commit": policy.allow_merge_commit,
        "allow_rebase": policy.allow_rebase,
        "allow_auto_merge": policy.allow_auto_merge,
        "delete_branch_on_merge": policy.delete_branch_on_merge,
        "required_approving_reviews": policy.required_approving_reviews,
        "has_required_status_checks": policy.has_required_status_checks,
    }
    if args.json:
        print(_json.dumps({
            "repo": args.repo, "provider": getattr(prcfg, "provider", ""),
            "supported": policy.supported, "error": policy.error,
            "settings": settings, "suggested_matrix": matrix,
        }))
        return 0 if policy.supported else 1

    if not policy.supported:
        output.err(f"pr-research: {policy.error}")
        return 1
    output.header(f"PR-policy research: {args.repo}")
    print("  Live settings:")
    for k, v in settings.items():
        if k == "supported":
            continue
        print(f"    {k}: {v}")
    print("  Suggested pr: policy (drop into .agent-worktrees/config.yaml):")
    if matrix:
        for k, v in matrix.items():
            print(f"    {k}: {str(v).lower() if isinstance(v, bool) else v}")
    else:
        print("    (no confident derivation -- keep the defaults)")
    return 0


# pr <verb> namespace -> canonical top-level verb (or manual dispatcher).
_PR_NAMESPACE = {
    "create": "create-pr",
    "status": "pr-status",
    "complete": "pr-complete",
    "ready": "pr-ready",
}


def cmd_pr_dispatch(argv: list[str]) -> int:
    """Route the `pr <verb>` namespace onto the flat pr-* command family."""
    if not argv or argv[0] in ("--help", "-h", "help"):
        _pr_usage()
        return 0 if argv and argv[0] in ("--help", "-h", "help") else 1
    verb = argv[0]
    if verb == "watch":
        return cmd_pr_watch_dispatch(argv[1:])
    if verb == "merge":
        return cmd_pr_merge_dispatch(argv[1:])
    if verb == "research":
        return cmd_pr_research_dispatch(argv[1:])
    canonical = _PR_NAMESPACE.get(verb)
    if not canonical:
        output.err(f"Unknown pr subcommand: {verb}")
        _pr_usage()
        return 1
    parser = build_parser()
    try:
        args = parser.parse_args([canonical, *argv[1:]])
    except SystemExit as exc:
        return int(exc.code or 0)
    handler = COMMAND_MAP.get(args.command)
    if not handler:
        _pr_usage()
        return 1
    return handler(args)



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

    # Back-compat / family alias: `pr-create` is the pr-* family name for
    # `create-pr` (Phase 4 of the pr-command-family effort). Rewrite it to the
    # canonical verb before argparse so both spellings share one handler; the
    # original `create-pr` stays fully live.
    if args_list and args_list[0] == "pr-create":
        args_list = ["create-pr", *args_list[1:]]

    # ── Resolve the active project + assumed CWD (git-like) ──────────────
    # Context is discovered from the current directory, or an explicit
    # --project (which means "assume CWD is that project's anchor repo").
    # Ambient $WORKTREE_PROJECT / $WORKTREE_ID are NOT trusted for identity --
    # resolution is a pure function of where you are, not inherited session env.
    args_list, _proj = _extract_project_flag(args_list)

    # ── `<repo> <slug>` command-surface router ───────────────────────────
    # A leading token naming a routable sibling plugin (the routable set is
    # DERIVED: the curated core set ∪ installed agent-<slug> binstubs, excluding
    # real worktrees verbs) is a plugin namespace: `<repo> <slug> …` →
    # `agent-<slug> …`. Singular/plural variants are tolerated (`<repo> codespace`
    # == `<repo> codespaces`). `--project <repo>` is injected only for plugins
    # that consume it (_PROJECT_ARG_SLUGS); other slugs route as a cwd-preserving
    # alias. `worktrees` (and `worktree`) folds back into this binstub. See the
    # command-surface effort.
    if args_list:
        _canon = (None if args_list[0] in _worktrees_verbs()
                  else _canonical_slug(args_list[0]))
        if _canon == "worktrees":
            args_list = args_list[1:]
        elif _canon is not None:
            _sib_project = _proj if _canon in _PROJECT_ARG_SLUGS else None
            return _route_to_sibling_plugin(_canon, _sib_project, args_list[1:])

    # --project has no effect on a machine-global verb (repos/accounts/picker/
    # --version/--help). Softly note a likely-mistaken explicit one (only when it
    # names an unregistered project) -- never bounce, and never require any
    # binstub/env cooperation, so older deployed binstubs keep working. Runs
    # after the sibling router (siblings return above).
    _guard_project_scope(_proj, args_list[0] if args_list else None)

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

    if _project:
        cfg.set_active_project(_project)
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

    # Identity is a pure function of CWD + optional --project (threaded in
    # process via set_active_project); no ambient $WORKTREE_PROJECT is consulted
    # (cwd-resolution Phase 3).
    has_project = bool(cfg.active_project())

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

    # Accounts (gh identity catalog) -- manual dispatch.
    if args_list[0] == "accounts":
        try:
            return cmd_accounts_dispatch(args_list[1:])
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

    # state-root (resolve where efforts/visions/logs are written) -- manual
    # dispatch (needs project context; see cmd_state_root_dispatch).
    if args_list[0] == "state-root":
        try:
            return cmd_state_root_dispatch(args_list[1:])
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

    # pr-watch -- provider-generic PR review-callback watcher (manual dispatch:
    # sub-subcommands wait/cursor + owner/name + pr).
    if args_list[0] == "pr-watch":
        try:
            return cmd_pr_watch_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # pr-merge -- signal merge consent (apply the consent label). Manual
    # dispatch (owner/name + pr | --all).
    if args_list[0] == "pr-merge":
        try:
            return cmd_pr_merge_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # pr-research -- read the repo's live provider settings -> policy matrix
    # (#225). Read-only manual dispatch (owner/name).
    if args_list[0] == "pr-research":
        try:
            return cmd_pr_research_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # pr <verb> namespace -- sugar over the flat pr-* command family.
    if args_list[0] == "pr":
        try:
            return cmd_pr_dispatch(args_list[1:])
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

    # lease -- Git-ref resource lease store (atomic cross-machine, same-harness).
    # Manual dispatch: its own argparse subcommands (acquire/renew/release/
    # inspect/list) with resource kind+key positionals.
    if args_list[0] == "lease":
        from . import lease_cli
        try:
            return lease_cli.run_lease(args_list[1:])
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
