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
    agent-worktrees finalize [worktree-id | --worktree-id ID] [--dry-run] [--json]
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
    agent-worktrees config-root [--destination PATH] [--json]
    agent-worktrees knowledge compose-plugins [--json] [--cwd PATH]
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
import contextlib
import dataclasses
import hashlib
import importlib
import json
import os
import platform
import secrets
import shutil
import signal
import socket as _socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

# `-m agent_worktrees` (the binstub's own invocation, and this module's
# normal entry point) executes this file under the name ``"__main__"``, a
# SEPARATE sys.modules entry from its real qualified name
# ``agent_worktrees.__main__``. Late in this file, `pr_cli.py` does
# `from . import __main__ as core` -- an ordinary submodule import that,
# absent this alias, finds no `agent_worktrees.__main__` entry yet and
# re-executes this entire module FROM THE TOP under that qualified name.
# That second execution reaches its own `from . import pr_cli, ...` line
# while the FIRST pr_cli import is still mid-load (frozen at its own
# `from . import __main__ as core` line), so it gets back the same
# partially-initialized `pr_cli` module and crashes attribute-binding a name
# `pr_cli` hasn't defined yet ("partially initialized module ... has no
# attribute"). Aliasing the qualified name to the module already executing
# -- BEFORE any submodule import can trigger the problem -- makes `pr_cli`'s
# import a no-op cache hit instead of a second full execution. Fixes #2650.
sys.modules.setdefault(f"{__package__}.__main__", sys.modules[__name__])

from agent_procutil import (
    detached_kwargs,
    windowless_daemon_kwargs as _windowless_daemon_kwargs_impl,
    windowless_python_env,
)

from . import (
    activity,
    codename_tracking,
    disposition_history,
    effort_focus,
    front_door_cli,
    git_ops,
    handoff_trace,
    list_cache,  # noqa: F401 -- compatibility re-export for extracted status-monitor CLI
    locks,
    obligations,
    output,
    pane_lifecycle,
    permissions,
    pr_config,
    pr_ops,
    procs,
    profile_assignment,
    prune,
    reciprocal_presentation,
    reclaim,
    sessions,
    sessions_pane_retire,
    tracking,
)
from . import claimant as claimant_mod
from . import config as cfg
from . import finalize as fin
from . import installer as inst
from . import loop_governance as loop_governance_mod  # noqa: F401 -- compatibility re-export
from . import services as _svc
from . import session_context as session_context_mod  # noqa: F401 -- compatibility re-export
from . import state_root as state_root_mod
from .update_stage import cmd_stage_update, discover_plugin_dir as _discover_plugin_dir

# front_door_cli's own names are re-exported eagerly (not deferred to
# _load_full_command_surface() below) because main() calls several of them
# -- _extract_project_flag, _guard_project_scope, _resolve_active_project,
# etc. -- unconditionally for EVERY invocation, regardless of which
# subcommand (or none) was requested, before any lazy dispatch decision can
# be made. This is the "small always-on core" the lazy-dispatch design
# deliberately keeps eager.
_extract_project_flag = front_door_cli._extract_project_flag
_installed_sibling_slugs = front_door_cli._installed_sibling_slugs
_CORE_SLUGS = front_door_cli._CORE_SLUGS
_PROJECT_ARG_SLUGS = front_door_cli._PROJECT_ARG_SLUGS
_worktrees_verbs = front_door_cli._worktrees_verbs
_canonical_slug = front_door_cli._canonical_slug
_sibling_binstub = front_door_cli._sibling_binstub
_route_to_sibling_plugin = front_door_cli._route_to_sibling_plugin
_safe_cwd = front_door_cli._safe_cwd
_git_toplevel = front_door_cli._git_toplevel
_NO_PROJECT_COMMANDS = front_door_cli._NO_PROJECT_COMMANDS
_is_no_project_invocation = front_door_cli._is_no_project_invocation
_is_registered_project = front_door_cli._is_registered_project
_guard_project_scope = front_door_cli._guard_project_scope
_anchor_for_project = front_door_cli._anchor_for_project
_reverse_lookup_project = front_door_cli._reverse_lookup_project
_cwd_is_inside_project = front_door_cli._cwd_is_inside_project
_resolve_active_project = front_door_cli._resolve_active_project
cmd_help_unrouted = front_door_cli.cmd_help_unrouted
_worktree_manager_path = front_door_cli._worktree_manager_path
_launch_probe_env = front_door_cli._launch_probe_env
_probe_worktree_manager_version = front_door_cli._probe_worktree_manager_version
_usable_worktree_manager = front_door_cli._usable_worktree_manager
_WORKTREE_MANAGER_ENGINE_ARGV_ENV = front_door_cli._WORKTREE_MANAGER_ENGINE_ARGV_ENV
_WORKTREE_MANAGER_REPO_URL = front_door_cli._WORKTREE_MANAGER_REPO_URL
_WORKTREE_MANAGER_INSTALL_SH = front_door_cli._WORKTREE_MANAGER_INSTALL_SH
_WORKTREE_MANAGER_INSTALL_PS1 = front_door_cli._WORKTREE_MANAGER_INSTALL_PS1
_worktree_manager_root = front_door_cli._worktree_manager_root
_current_version_slot = front_door_cli._current_version_slot
_usable_worktree_manager_launcher_dir = front_door_cli._usable_worktree_manager_launcher_dir
_agent_worktrees_launch_command = front_door_cli._agent_worktrees_launch_command
_resolve_direct_launch_plan = front_door_cli._resolve_direct_launch_plan
_wait_for_launch_child = front_door_cli._wait_for_launch_child
_run_post_exit_for_direct_launch = front_door_cli._run_post_exit_for_direct_launch
_run_direct_launch_fallback = front_door_cli._run_direct_launch_fallback
_exec_worktree_manager = front_door_cli._exec_worktree_manager
_bundled_picker_available = front_door_cli._bundled_picker_available
cmd_manager_install_trigger = front_door_cli.cmd_manager_install_trigger
_is_headless_project = front_door_cli._is_headless_project
_is_noninteractive_invocation = front_door_cli._is_noninteractive_invocation
cmd_noninteractive_bare = front_door_cli.cmd_noninteractive_bare
cmd_headless_bare = front_door_cli.cmd_headless_bare
dispatch_bare_invocation = front_door_cli.dispatch_bare_invocation

# ── Env var helpers ─────────────────────────────────────────────────────
# Operational flags are read from their WORKTREE_* names. The legacy APERTURE_*
# aliases (a copilot-worktrees extraction transition shim) have been removed --
# identity resolves from CWD, never from an ambient env var.

_SESSION_BIND_PROJECT = "AGENT_WORKTREES_BIND_PROJECT"
_SESSION_BIND_WORKTREE = "AGENT_WORKTREES_BIND_WORKTREE_ID"
_SESSION_BIND_SESSION = "AGENT_WORKTREES_BIND_SESSION_ID"
_GOVERNANCE_BACKOFF_SECONDS = 10.0
_SESSION_HANDOFF_TOKEN = "AGENT_WORKTREES_HANDOFF_TOKEN"
_NO_AUTO_CLEAN_ENV = "AGENT_WORKTREES_NO_AUTO_CLEAN"
_AUTO_CLEAN_GRACE_ENV = "AGENT_WORKTREES_AUTO_CLEAN_GRACE_SECS"
_INVOCATION_CWD: Path | None = None
_UPDATE_CONTEXT_ENV = "AGENT_WORKTREES_UPDATE_CONTEXT"


def windowless_daemon_kwargs(**kwargs):
    return _windowless_daemon_kwargs_impl(**kwargs)


def _env_get(new_name: str) -> str | None:
    """Read an env var by name (an empty value is treated as unset)."""
    return os.environ.get(new_name) or None


def _env_set(new_name: str, value: str) -> None:
    """Set an env var by name."""
    os.environ[new_name] = value


# ═══════════════════════════════════════════════════════════════════════════
# Default launch -- exec into launch-session.sh when no subcommand given
# ═══════════════════════════════════════════════════════════════════════════


def _fire_launch_resolution_watchdog() -> None:
    """Self-terminate a hung bare-launch invocation (copilot-extensions #<TBD>).

    The pre-handoff resolution in ``cmd_launch`` (project/config resolution,
    filesystem probes, an optional git call for the recovery anchor) has been
    observed to survive its hosting console being closed mid-call and hang
    indefinitely at near-zero CPU rather than exit -- a known CPython-on-
    Windows interaction where the installed console-control handler reports
    the close "handled" and returns immediately even though the blocked
    native call (e.g. a git subprocess wait) is never actually interrupted, so
    the process never gets a chance to notice and exit on its own. Whatever
    the trigger, nothing downstream can be trusted to notice a stall here, so
    this watchdog guarantees a hard ceiling: ``os._exit`` bypasses the stuck
    call entirely rather than trying to unwind through it.
    """
    try:
        sys.stderr.write(
            "[agent-worktrees] bare-launch resolution exceeded "
            f"{_LAUNCH_RESOLUTION_TIMEOUT_SECS}s -- self-terminating a likely "
            "hang instead of blocking forever.\n"
        )
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(1)


# How long cmd_launch's pre-handoff resolution (before it hands the console off
# to launch-session / the direct fallback, where a long or indefinite wait is
# expected and healthy) may run before the watchdog above assumes it is stuck.
# Generous for a cold git call on a slow disk/network, nowhere close to a
# human-perceptible delay for the normal case.
_LAUNCH_RESOLUTION_TIMEOUT_SECS = 60.0


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

    # Bounds the resolution below (never the handoff itself, which cancels
    # this before blocking on the child/session -- see the docstring above).
    # The `finally` is the safety net: cancel unconditionally on EVERY exit
    # path (normal return, sys.exit, or an unexpected exception raised
    # anywhere below) -- an explicit .cancel() at a specific handoff point is
    # a documentation aid, never the only thing standing between a failure
    # here and a real, unmocked timer left armed in the background.
    _watchdog = threading.Timer(
        _LAUNCH_RESOLUTION_TIMEOUT_SECS, _fire_launch_resolution_watchdog
    )
    _watchdog.daemon = True
    _watchdog.start()
    try:
        launch_project = cfg.active_project()
        launcher_args = (
            ["--project", launch_project, *passthrough] if launch_project else passthrough
        )

        # Resolve launch support from the same validated runtime root that
        # entered this Python process. An explicit cell must never fall back
        # to legacy.
        from . import registry_paths

        context = registry_paths.installation_context()
        inst_dir = (
            Path(context["pluginRoot"])
            if context is not None
            else cfg.install_dir()
        )
        if context is not None:
            _env_set("AGENT_WORKTREES_LAUNCH_RUNTIME_ROOT", str(inst_dir))
            try:
                _env_set(
                    "AGENT_WORKTREES_LAUNCH_RECOVERY_ANCHOR",
                    cfg.load_config(project=launch_project).default_repo.anchor,
                )
            except (OSError, RuntimeError, ValueError):
                os.environ.pop("AGENT_WORKTREES_LAUNCH_RECOVERY_ANCHOR", None)
        plat = cfg.detect_platform()
        legacy_name = "launch-session.cmd" if plat == "windows" else "launch-session.sh"
        # Phase 3b Sub-slice 2a Step 2 cutover complete (efforts/active/
        # worktree-manager-control-plane/phase-3b-mux-relocation.md): the
        # relocated Worktree Manager path has been live on real hardware for
        # months (multiple self-updated versions observed), so the in-plugin
        # launch-session/pane-wrapper rollback tier is retired along with the
        # files themselves. agent-worktrees never re-derives a mux launcher of
        # its own; the ONLY tiers are the relocated Worktree Manager launcher
        # (a "bring your own mux" controller today, generalized later per the
        # session-hosting vision) and the direct, non-mux fallback below.
        candidate = _usable_worktree_manager_launcher_dir()
        launch_bin = candidate if candidate is not None and (candidate / legacy_name).exists() else None
        if launch_bin is None:
            output.warn(
                "No launch-session script found (neither the relocated "
                "Worktree Manager launcher nor the in-plugin fallback); "
                "launching Copilot directly without mux."
            )
            return _run_direct_launch_fallback(launch_project, passthrough)

        if plat == "windows":
            launch_script = launch_bin / "launch-session.cmd"
        else:
            launch_script = launch_bin / "launch-session.sh"

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
                argv = ["cmd.exe", "/c", str(launch_script), *launcher_args]
            else:
                argv = ["pwsh.exe", "-NoProfile", "-NoLogo", "-File", str(ps1), *launcher_args]
            # Popen + wait (never os.exec on Windows, which has no true exec and
            # would detach the child from the console): hold the console and catch
            # KeyboardInterrupt (Ctrl+C) so the child (launch-session.ps1) can finish
            # its try/finally handoff check + post-exit finalization instead of being
            # killed mid-cleanup.
            proc = subprocess.Popen(argv)
            _watchdog.cancel()  # resolution is done -- the wait below is expected
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
            os.execvp("bash", ["bash", str(launch_script), *launcher_args])
        return 1  # unreachable -- os.execvp replaces process
    finally:
        _watchdog.cancel()


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


def _hosted_session_blocks_cleanup(record) -> bool:
    """Whether an external session may still depend on this checkout."""
    if (
        getattr(record, "session_backend_opaque", False)
        or getattr(record, "execution_leg_opaque", False)
    ):
        return True
    leg = tracking.derive_execution_leg(record)
    return leg is not None and leg.state in {"active", "unknown"}


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
    active = {_normalize_path(p) for p, sids in session_ctx.active_sessions.items() if sids}
    for rec in records:
        if rec.worktree_path and _hosted_session_blocks_cleanup(rec):
            active.add(_normalize_path(rec.worktree_path))
    # Live multiplexer sessions (independent of lock files), batched.
    mux_sessions = sessions._list_mux_sessions()
    if mux_sessions is not None:
        for rec in records:
            if rec.worktree_path and sessions.mux_session_name(rec.worktree_id) in mux_sessions:
                active.add(_normalize_path(rec.worktree_path))
    else:
        # Batch list unavailable (mux missing or blocked): prefer the #4057
        # cached liveness hint on the record (a free read, stamped by the
        # authoritative verify at the action moments) when it is a FRESH
        # ``True`` -- that short-circuits the slow per-worktree probe. A
        # missing hint OR a fresh cached ``False`` both still fall through to
        # the authoritative ``has_mux_session`` probe (cleanup-toctou-
        # revalidation effort): a cached negative is not proof a session
        # hasn't attached since the stamp, so it must never be trusted on its
        # own to skip the fallback check.
        for rec in records:
            if not rec.worktree_path:
                continue
            hint = _fresh_mux_live_hint(rec)
            if hint is True:
                active.add(_normalize_path(rec.worktree_path))
            elif sessions.has_mux_session(rec.worktree_id):
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

    A worktree with **any uncommitted content** is never masked either --
    checked two ways so neither a missing count nor a stale state label can
    slip through:

    - ``info.state == DIRTY`` (the direct classifier's own read), kept as an
      explicit fallback because some callers reconstruct a
      ``WorktreeStateInfo`` from a **cached** ``git_state`` string (e.g.
      ``_classify_from_cache``) without repopulating ``dirty`` -- that path
      can hand this function ``state == DIRTY, dirty == 0``, and checking
      only the count would miss it.
    - ``info.dirty > 0`` (the count directly), needed because
      ``_classify_git_state`` can also return ``ORPHAN`` (no merge base)
      while still reporting a nonzero ``dirty`` count -- state-matching
      alone would miss *that* case.

    Uncommitted working-tree changes made *after* finalization are new, real,
    unlanded content that the stale ``finalized``/``complete``/``completed``
    tracking status knows nothing about -- unlike the two corrected cases
    above, this isn't a classifier misreading already-landed work, it's
    genuinely unlanded work. Masking it to COMPLETED would let plain
    ``cleanup --clean`` delete it with no ``--force`` at all.
    """
    if rec.status in ("finalized", "complete", "completed"):
        if (
            info.state != git_ops.WorktreeState.DIRTY
            and info.dirty == 0
            and info.state not in (
                git_ops.WorktreeState.GONE,
                git_ops.WorktreeState.ACTIVE,
            )
        ):
            return dataclasses.replace(info, state=git_ops.WorktreeState.COMPLETED)
    return info


def _classify_records(
    records: list[tracking.WorktreeRecord],
    session_ctx: sessions.SessionContext | None = None,
    *,
    daemon_filters: dict | None = None,
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

    Resident-daemon fast path (Phase 4d, #2323): when ``daemon_filters`` names
    the exact ``status_filter``/``platform_filter``/``all`` values the caller
    used to resolve ``records`` (only ``list --json --classify`` opts in), this
    first tries the resident status-monitor's classify daemon (see
    :mod:`classify_daemon`) via the full boot-wait sequence
    (:func:`classify_daemon.classify_with_boot`) -- booting one via
    :func:`_ensure_status_monitor` when none is currently publishing a
    classify endpoint, waiting up to ``classify_daemon.BOOT_WAIT_S`` -- so
    concurrent classify passes for the same project + filter set coalesce
    onto one resident execution instead of each independently racing the
    lease below. ``daemon_filters=None`` (every other caller) skips the
    daemon entirely -- unchanged behavior. Any miss (no resident monitor,
    boot-wait timeout, unreachable, past its deadline, an error resolving the
    project, or -- critically -- a successful-but-malformed/incomplete
    response whose worktree-id set doesn't match ``records`` exactly) falls
    through to the lease-guarded path exactly as if no daemon existed; a
    partial or garbled daemon answer is never trusted over rendering nothing
    for an affected row.

    Guarded by a per-project :class:`single_instance_lease.SingleInstance`
    (plugin-process-hygiene Phase 4c, #2315): this is the shared entry point
    behind BOTH the Picker's in-process classify pass and a standalone
    ``list --json --classify`` invocation, so two independently-launched
    callers for the same project can otherwise race this same batch of ~5
    git-calls-per-worktree concurrently -- observed live as a Worktree Manager
    display flicker (a row's ``state`` transiently absent mid-race renders a
    generic, potentially wrong heuristic instead of its last-known-correct
    value). A caller that loses the race waits (bounded) for the winner to
    finish, then re-reads its repaired session-render cache -- see
    :func:`_classify_records_wait_then_cache` -- rather than computing its own
    competing pass or rendering a worse answer than before. The lease is
    advisory only: any lease-layer failure (not contention) degrades to
    classifying live, exactly as before this guard existed.
    """
    if daemon_filters is not None:
        try:
            project = cfg.project_name()
        except Exception:
            project = ""
        if project:
            from . import classify_daemon
            from . import locks as _locks

            key = "|".join(
                (
                    project,
                    str(daemon_filters.get("status_filter")),
                    str(daemon_filters.get("platform_filter")),
                    str(bool(daemon_filters.get("all"))),
                )
            )
            payload = {"project": project, **daemon_filters}
            expected_ids = {rec.worktree_id for rec in records}

            def _fallback() -> dict:
                return _serialize_classify_map(
                    _classify_records_lease_guarded(records, session_ctx)
                )

            raw = classify_daemon.classify_with_boot(
                read_lock_data=lambda: _locks.read_lock(_monitor_lock_path()),
                # Honor the resident-monitor opt-out (AGENT_WORKTREES_STATUS_
                # MONITOR=0): booting one just to serve this classify request
                # would defeat a caller's explicit choice to stay per-session
                # inline-only. A dial-only attempt (no boot) still runs, so an
                # already-live monitor from before the opt-out was set is
                # still used if reachable.
                ensure_monitor=_ensure_status_monitor if _status_monitor_enabled() else None,
                key=key,
                payload=payload,
                fallback=_fallback,
            )
            try:
                decoded = _deserialize_classify_map(raw, strict=True)
            except Exception:
                decoded = None
            if decoded is not None and set(decoded) == expected_ids:
                return decoded
            # The daemon answered, but with a shape/id-set mismatch (a
            # different-version daemon, a partial compute, or corruption in
            # transit) -- never render a partial/garbled state_map. Re-run
            # the unchanged lease-guarded path directly, exactly as if the
            # daemon had never answered at all.
            return _classify_records_lease_guarded(records, session_ctx)
    return _classify_records_lease_guarded(records, session_ctx)


def _serialize_classify_map(
    state_map: dict[str, git_ops.WorktreeStateInfo],
) -> dict[str, dict]:
    """Wire-serialize a classify result for the coalescing daemon's protocol."""
    return {wt_id: dataclasses.asdict(info) for wt_id, info in state_map.items()}


def _deserialize_classify_map(
    raw: dict, *, strict: bool = False
) -> dict[str, git_ops.WorktreeStateInfo]:
    """Inverse of :func:`_serialize_classify_map`.

    ``strict=False`` (the default) is tolerant of a malformed entry (skipped
    rather than raised). ``strict=True`` raises on the first malformed entry
    instead -- used by :func:`_classify_records`'s daemon fast path, which
    must treat *any* shape problem as a daemon miss (triggering its own
    lease-guarded re-run) rather than silently rendering a partial result.
    """
    out: dict[str, git_ops.WorktreeStateInfo] = {}
    if not isinstance(raw, dict):
        if strict:
            raise ValueError("classify daemon result is not a dict")
        return out
    for wt_id, info_dict in raw.items():
        if not isinstance(info_dict, dict):
            if strict:
                raise ValueError(f"classify daemon result for {wt_id!r} is not a dict")
            continue
        try:
            state = git_ops.WorktreeState(info_dict.get("state"))
        except ValueError:
            if strict:
                raise
            state = git_ops.WorktreeState.UNKNOWN
        try:
            out[wt_id] = git_ops.WorktreeStateInfo(
                state=state,
                ahead=int(info_dict.get("ahead") or 0),
                behind=int(info_dict.get("behind") or 0),
                dirty=int(info_dict.get("dirty") or 0),
                title=str(info_dict.get("title") or ""),
                current_branch=info_dict.get("current_branch"),
                branch_drift=bool(info_dict.get("branch_drift", False)),
            )
        except (TypeError, ValueError):
            if strict:
                raise
            continue
    return out


def _classify_records_lease_guarded(
    records: list[tracking.WorktreeRecord],
    session_ctx: sessions.SessionContext | None = None,
) -> dict[str, git_ops.WorktreeStateInfo]:
    """The pre-#2323 lease-guarded classify path, factored out so
    :func:`_classify_records`'s daemon fast path can fall back to it
    directly."""
    try:
        lock_dir = cfg.project_dir()
    except Exception:
        lock_dir = None
    if lock_dir is None:
        return _classify_records_live(records, session_ctx)

    from single_instance_lease import AlreadyRunningError, SingleInstance

    lease = SingleInstance(lock_dir, service="classify")
    try:
        lease.acquire()
    except AlreadyRunningError:
        return _classify_records_wait_then_cache(records, session_ctx, lease)
    except OSError:
        # The lease layer itself failed (e.g. can't create the lock file) --
        # never let an advisory optimization block correctness.
        return _classify_records_live(records, session_ctx)
    try:
        return _classify_records_live(records, session_ctx)
    finally:
        lease.release()


def _classify_daemon_compute(kind: str, payload: dict) -> dict:
    """Resident classify daemon's ``compute`` callback (Phase 4d, #2323).

    Runs inside the resident status-monitor process. Resolves + classifies
    the named project's own records entirely here from ``payload``'s
    identity + filters -- never trusts records serialized by a caller (see
    :mod:`classify_daemon`'s own module docstring for the contract). Mirrors
    ``_list_records_for_args``'s exact filter semantics (including the
    existing-worktree-with-a-``.git``-dir check unless ``all`` is set) so a
    caller gets an identical result whether or not the daemon answered it.
    Any exception here propagates to the coalescing server, which surfaces
    it to every joined caller -- each caller's own
    :func:`classify_daemon.classify_via_daemon` treats that identically to
    "no daemon" and runs its own fallback, so a daemon-side bug degrades to
    correctness, never a wrong answer.
    """
    project = str(payload.get("project") or "")
    if not project:
        raise ValueError("classify request missing project")
    status_filter = payload.get("status_filter")
    platform_filter = payload.get("platform_filter")
    tracking_path = cfg.project_dir(project) / "worktrees"
    records = tracking.list_records(
        tracking_path,
        status_filter=status_filter,
        platform_filter=platform_filter,
    )
    if not payload.get("all"):
        records = [
            r
            for r in records
            if r.worktree_path
            and Path(r.worktree_path).exists()
            and (Path(r.worktree_path) / ".git").exists()
        ]
    session_ctx = sessions.scan_sessions_fast(records)
    config = cfg.load_config(path=cfg.project_dir(project) / "config.yaml", project=project)
    repo = config.default_repo
    active_paths = _build_active_paths(records, session_ctx)
    state_map = {
        rec.worktree_id: _classify_one_record(
            rec, repo=repo, active_paths=active_paths, session_ctx=session_ctx
        )
        for rec in records
    }
    return _serialize_classify_map(state_map)


from . import worktree_status_audit  # noqa: E402 -- re-export position matches original definition site
from . import worktree_status_compute as _worktree_status_compute_mod  # noqa: E402 -- same

_worktree_status_fact = _worktree_status_compute_mod._worktree_status_fact
_worktree_status_compute = _worktree_status_compute_mod.compute
cmd_worktree_status_audit = worktree_status_audit.cmd_worktree_status_audit


def _classify_records_live(
    records: list[tracking.WorktreeRecord],
    session_ctx: sessions.SessionContext | None = None,
) -> dict[str, git_ops.WorktreeStateInfo]:
    """The actual ~5-git-calls-per-worktree batch classification.

    Factored out of :func:`_classify_records` so the lease wrapper can call it
    only when it actually won the race; a losing caller never reaches this.
    """
    config = cfg.load_config()
    repo = config.default_repo
    active_paths = _build_active_paths(records, session_ctx)
    return {
        rec.worktree_id: _classify_one_record(
            rec, repo=repo, active_paths=active_paths, session_ctx=session_ctx
        ) for rec in records
    }


def _classify_records_wait_then_cache(
    records: list[tracking.WorktreeRecord],
    session_ctx: sessions.SessionContext | None,
    lease,
    *,
    timeout: float = 15.0,
    poll: float = 0.25,
) -> dict[str, git_ops.WorktreeStateInfo]:
    """Wait for the lease winner, then answer from its repaired cache.

    Polls the same lease non-blockingly (the library has no blocking-wait
    primitive) until it becomes free -- meaning the winner released it, i.e.
    finished its pass and (best-effort) stamped the session-render cache --
    or ``timeout`` elapses. Either way, answers from **cache**, never by
    running a competing live classification: a timeout degrades to whatever is
    currently cached (possibly still the pre-race value), which is never worse
    than what this caller had before, unlike the "state absent -> WIP" fallback
    it replaces.
    """
    from single_instance_lease import AlreadyRunningError

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            lease.acquire()
        except AlreadyRunningError:
            time.sleep(poll)
            continue
        else:
            lease.release()  # We don't do the work; just detected completion.
            break
    return _classify_from_cache(records, session_ctx)


def _classify_from_cache(
    records: list[tracking.WorktreeRecord],
    session_ctx: sessions.SessionContext | None = None,
) -> dict[str, git_ops.WorktreeStateInfo]:
    """Build a classification map from each record's on-disk cached state.

    Re-reads every record fresh (not the possibly-stale in-memory copy the
    caller already held before waiting) so a just-completed winner's stamp is
    visible. Degrades to ``UNKNOWN`` only when a record has genuinely never
    been classified before -- never invents ``WIP`` or another guessed state.
    """
    out: dict[str, git_ops.WorktreeStateInfo] = {}
    for rec in records:
        try:
            fresh = tracking.load_record(cfg.tracking_dir() / f"{rec.worktree_id}.yaml")
        except Exception:
            fresh = rec
        cached = fresh.git_state
        if cached:
            try:
                state = git_ops.WorktreeState(cached)
            except ValueError:
                state = git_ops.WorktreeState.UNKNOWN
        elif fresh.status == "finalized":
            state = git_ops.WorktreeState.COMPLETED
        else:
            state = git_ops.WorktreeState.UNKNOWN
        if session_ctx is not None:
            state = git_ops.refine_state_with_session(state, fresh.session_turns or 0)
        out[rec.worktree_id] = git_ops.WorktreeStateInfo(state=state)
    return out


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
            rec.worktree_path,
            rec.branch,
            fetch=False,
            remote=repo.remote,
            default_branch=repo.default_branch,
            active_paths=active_paths,
        )
        info = _apply_tracking_override(rec, info)
    elif rec.status == "finalized":
        info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    else:
        info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.GONE)
    # Layer the session-derived CONVO refinement so this data contract
    # reports the same display state the tmux status bar does.
    if session_ctx is not None:
        turns = session_ctx.turn_count.get(_normalize_path(rec.worktree_path), 0,)
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
    project = cfg.active_project()
    if project:
        plan.setdefault("project", project)
    if plan.get("action") == "exec":
        env = plan.setdefault("env", {})
        env.setdefault("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", str(cfg.project_dir()))
    sys.__stdout__.write(json.dumps(plan) + "\n")
    sys.__stdout__.flush()


# ═══════════════════════════════════════════════════════════════════════════
# JSON output helpers -- shared by all --json modes
# ═══════════════════════════════════════════════════════════════════════════

# Moved to output.py (module-size split); re-exported here since nothing about
# these needs to be defined in the entry-point module itself.
_JSON_SCHEMA_VERSION = output._JSON_SCHEMA_VERSION
_json_output = output._json_output
_json_error = output._json_error


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


# Moved to tracking.py (module-size split); re-exported here for existing
# unqualified call sites in this module and for back-compat test access.
_controller_metadata = tracking._controller_metadata
_controller_findings = tracking._controller_findings


def _worktree_to_dict(
    rec: tracking.WorktreeRecord,
    *,
    state_info: git_ops.WorktreeStateInfo | None = None,
    mux_info: sessions.MuxInfo | None = None,
    session_ctx: sessions.SessionContext | None = None,
    bare_orphan_wts: set[str] | None = None,
    bridge_live_wts: set[str] | None = None,
    include_profile_assignment_history: bool = False,
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
    # #3307 Phase 3: the timestamp of the worktree's most recent actual
    # resume (``tracking.py``'s ``last_resumed_at``, updated whenever a
    # session is genuinely resumed) -- distinct from ``started_at`` (when the
    # worktree was FIRST created). The Picker's Recent-section sort needs this
    # to rank by real recency-of-use rather than original creation age; a
    # never-resumed worktree simply omits the field (callers fall back to
    # ``started_at``).
    if rec.last_resumed_at:
        d["last_resumed_at"] = rec.last_resumed_at
    if rec.codename:
        d["codename"] = rec.codename
    # Session ownership is durable record state, not live-scan enrichment.
    # Cache-only Picker rows must therefore carry the registered session count
    # and current head before any events/mux/process scan runs.  The head is the
    # asserted succession pointer; a newer transcript timestamp or a surviving
    # predecessor mux must never replace it.
    registered_sessions = getattr(rec, "sessions", None)
    if registered_sessions is not None:
        d["session_count"] = len(registered_sessions)
    head_session = getattr(rec, "resolved_head_session", None)
    if head_session:
        d["last_session_id"] = head_session
    if rec.execution_leg_opaque or rec.session_backend_opaque:
        # Legacy ``session_backend:`` opaque records still surface through the
        # generic execution-leg envelope so callers never branch on the old key.
        d["execution_leg"] = {"opaque": True, "state": "unknown"}
        d["execution_leg_live"] = True
    else:
        execution_leg = tracking.derive_execution_leg(rec)
        if execution_leg is not None:
            d["execution_leg"] = execution_leg.to_dict()
            d["execution_leg_live"] = execution_leg.state in {"active", "unknown"}
            if not head_session and execution_leg.state != "disposed":
                session_id = execution_leg.blob.get("session_id")
                if isinstance(session_id, str) and session_id:
                    d["last_session_id"] = session_id
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
    if not rec.checkout_managed:
        d["checkout_managed"] = False
    d["picker_hidden"] = rec.is_picker_hidden
    if rec.bound_agent:
        d["bound_agent"] = rec.bound_agent
    if rec.dispatch_attempt is not None:
        d["dispatch_attempt"] = rec.dispatch_attempt.to_dict()
    # worktree-status-core: the agent-asserted disposition overlay so the Picker
    # can render a follow-up glyph + summary and feed the prune verdict. Absent
    # summary/status_note_at stay off the dict to keep it lean for un-annotated
    # worktrees; follow_up is always present (a plain bool the picker reads).
    effort_state = None
    if rec.active_effort is not None:
        effort_state = effort_focus.inspect_effort(Path(rec.worktree_path), rec.active_effort)
        d["active_effort"] = effort_state.to_dict()
    d["follow_up"] = rec.follow_up or bool(effort_state and effort_state.active)
    effective_summary = (
        effort_state.summary if effort_state is not None and effort_state.active else rec.summary
    )
    if effective_summary:
        d["summary"] = effective_summary
    if rec.status_note_at:
        d["status_note_at"] = rec.status_note_at
    # #2178: expose the bridge caller-worktree pointer so the Picker can offer
    # "Jump to caller" from a bridge worktree.
    if rec.caller_worktree:
        d["caller_worktree"] = rec.caller_worktree
    controller_findings: list[dict[str, object]] = []
    if rec.controllers or rec.controller_revision:
        d["controller_revision"] = rec.controller_revision
        d["controllers"] = _controller_metadata(rec)
        controller_findings = _controller_findings(rec)
        d["controller_findings"] = controller_findings
    d["reciprocal_relation"] = reciprocal_presentation.derive(rec, controller_findings,)
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
    if getattr(rec, "profile_assignments", None):
        current_assignment = profile_assignment.assignment_for_session(
            rec, rec.resolved_head_session
        )
        d["current_profile_assignment"] = (
            profile_assignment.metadata(current_assignment)
            if current_assignment is not None
            else None
        )
        if include_profile_assignment_history:
            d["profile_assignments"] = [
                profile_assignment.metadata(assignment) for assignment in rec.profile_assignments
            ]
            d["latest_profile_assignment"] = profile_assignment.metadata(
                rec.profile_assignments[-1]
            )
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
            if session_ctx is not None
            else 0
        )
        _disposition = prune.cleanup_disposition(
            rec,
            state_info,
            turn_count=_turns,
            claimant_alive=_local_claimant_alive,
            paired_sibling_final=prune.default_paired_sibling_final,
        )
        d["cleanup_bucket"] = _disposition.bucket
        # worktree-finality-and-obligations Phase 4: the canonical closure
        # descriptor, additive alongside the legacy `cleanup_bucket`/`state`
        # fields above (not yet a replacement -- see the effort's Phase 5).
        # "refreshed" requires a fetch to have both been requested AND
        # succeeded (`state_info.fetch_requested and not
        # state_info.fetch_failed`, Phase 5 follow-up) -- `fetch_failed`
        # alone is insufficient, since it stays False when no fetch was ever
        # attempted (every current caller here classifies with
        # `fetch=False`), which would otherwise let an ordinary fetch-free
        # `list --json --classify` masquerade as refreshed evidence.
        _fetch_fresh = state_info.fetch_requested and not state_info.fetch_failed
        if _fetch_fresh:
            # Phase 9: this call's own fresh fetch also counts as current
            # evidence for every OTHER worktree of this repo -- record it in
            # the repo-scoped ledger rather than letting only this call
            # benefit from it.
            tracking.record_repo_fetch_confirmed(rec.repo)
        d["closure"] = prune.assemble_closure_descriptor(
            rec,
            state_info,
            _disposition,
            held_claims=sum(1 for c in rec.resources if c.is_live),
            open_follow_ups=tracking.effective_open_follow_up_count(rec),
            evidence_mode="refreshed" if _fetch_fresh else "cached",
            turn_count=_turns,
            repo_fetch_fresh=tracking.is_repo_fetch_fresh(rec.repo),
        ).to_dict()
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
        # Legacy records with no registry retain the scan-derived count.  Once
        # a registry exists, its journal is authoritative even when a session
        # directory is temporarily unavailable.
        if registered_sessions is None:
            d["session_count"] = session_ctx.session_count.get(norm, 0)
        # two-step-restore: the session id(s) currently held by a live
        # ``inuse.<pid>.lock`` (a bound Copilot process -- mux OR bare), and the
        # current durable head for this worktree. The Picker shows the head id
        # (so the operator can ``/resume`` it manually) and gates the Reclaim
        # action on a live lock. Both stay off the dict when absent to keep it
        # lean.
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


def _execution_leg_payload(
    worktree_id: str,
    binding: tracking.ExecutionLegBinding | None,
    *,
    legacy: bool = False,
) -> dict[str, object]:
    return {
        "worktree_id": worktree_id,
        "execution_leg": binding.to_dict() if binding is not None else None, "legacy": legacy,
    }


def _read_execution_leg_blob(blob_file: str) -> dict[str, object]:
    if blob_file == "-":
        raw = sys.stdin.read()
    else:
        raw = Path(blob_file).read_text(encoding="utf-8")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("execution-leg blob must be a JSON object")
    return value


def _execution_leg_reservation_path(yaml_path: Path) -> Path:
    return yaml_path.with_suffix(".execution-leg-reservation.json")


def _read_execution_leg_reservation(path: Path) -> dict[str, object]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("execution-leg reservation is invalid")
    return value


_EXECUTION_LEG_RESERVATION_DEFAULT_SECONDS = 300
_EXECUTION_LEG_RESERVATION_MAX_SECONDS = 900


def _reservation_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"execution-leg reservation {field} is invalid")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"execution-leg reservation {field} is invalid")
    return parsed.astimezone(timezone.utc)


def _reservation_expired(
    reservation: dict[str, object],
    *,
    now: datetime,
) -> bool:
    return _reservation_timestamp(
        reservation.get("expires_at"),
        field="expires_at",
    ) <= now


def _reservation_owner_liveness(
    reservation: dict[str, object],
) -> bool | None:
    pid = reservation.get("owner_pid")
    if not isinstance(pid, int) or pid <= 0:
        return None
    if not locks.pid_alive(pid):
        return False
    recorded = reservation.get("owner_start_time")
    if not recorded:
        return True
    current = locks.process_start_time(pid)
    if current is None:
        return True
    return str(current) == str(recorded)


def _write_execution_leg_reservation(
    path: Path,
    value: dict[str, object],
) -> None:
    temp_path = path.with_name(f"{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        temp_path.write_text(json.dumps(value, separators=(",", ":")), encoding="utf-8",)
        os.replace(temp_path, path)
    finally:
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass


def _binding_from_payload(value: object) -> tracking.ExecutionLegBinding | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("execution-leg reservation contains an invalid binding")
    provider = value.get("provider")
    blob = value.get("blob", {})
    if not isinstance(provider, str) or not provider or not isinstance(blob, dict):
        raise ValueError("execution-leg reservation contains an invalid binding")
    state = str(value.get("state") or "unknown")
    if state not in {"active", "disposed", "unknown"}:
        state = "unknown"
    return tracking.ExecutionLegBinding(
        provider=provider,
        state=state,
        binding_revision=int(value.get("binding_revision") or 0),
        blob=dict(blob),
    )


def cmd_execution_leg(args) -> int:
    """Inspect or mutate a provider-neutral execution-leg binding."""
    config = cfg.load_config()
    worktree_id = _resolve_worktree_id(args.worktree_id)
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return _json_error(f"Worktree not found: {worktree_id}")

    try:
        if args.action == "get":
            with tracking._RecordLock(yaml_path):
                record = tracking.load_record(yaml_path)
            if record.execution_leg_opaque or record.session_backend_opaque:
                raise ValueError("worktree uses an unsupported hosted-session schema")
            binding = tracking.derive_execution_leg(record)
            _json_output(
                _execution_leg_payload(
                    worktree_id,
                    binding,
                    legacy=record.execution_leg is None and binding is not None,
                )
            )
            return 0

        if args.action == "clear" and args.if_match_revision is None:
            raise ValueError("execution-leg clear requires --if-match-revision")
        if args.action == "set":
            if not args.provider:
                raise ValueError("execution-leg set requires --provider")
            if args.binding_revision is None or args.binding_revision <= 0:
                raise ValueError("execution-leg set requires a positive --binding-revision")
            if not args.blob_file:
                raise ValueError("execution-leg set requires --blob-file")
        if args.action in {"reserve", "renew"}:
            lease_seconds = getattr(
                args,
                "lease_seconds",
                _EXECUTION_LEG_RESERVATION_DEFAULT_SECONDS,
            )
            if not 1 <= lease_seconds <= _EXECUTION_LEG_RESERVATION_MAX_SECONDS:
                raise ValueError(
                    f"execution-leg {args.action} --lease-seconds must be between "
                    f"1 and {_EXECUTION_LEG_RESERVATION_MAX_SECONDS}"
                )
        if args.action == "reserve":
            if not args.provider:
                raise ValueError("execution-leg reserve requires --provider")
            if getattr(args, "operation", None) not in {"ensure", "dispose"}:
                raise ValueError("execution-leg reserve requires --operation ensure|dispose")
            if not getattr(args, "reservation_owner", None):
                raise ValueError("execution-leg reserve requires --reservation-owner")
        if args.action in {"renew", "release"} and not getattr(
            args, "reservation_token", None
        ):
            raise ValueError(f"execution-leg {args.action} requires --reservation-token")

        mutation_lock = yaml_path.with_suffix(".execution-leg.yaml")
        reservation_path = _execution_leg_reservation_path(yaml_path)
        with tracking._RecordLock(
            mutation_lock,
            timeout=120,
            require_sidecar=True,
        ):
            record = tracking.load_record(yaml_path)
            repo = _repo_for_record(config, record) or config.default_repo
            lifecycle_lock = fin.FinalizeLock(Path(repo.worktree_root) / ".finalize.lock")
            lifecycle_lock.acquire()
            try:
                if not yaml_path.exists():
                    raise ValueError(f"worktree record disappeared: {worktree_id}")
                with tracking._RecordLock(yaml_path):
                    latest = tracking.load_record(yaml_path)
                    if latest.execution_leg_opaque:
                        raise ValueError("worktree uses a newer unsupported execution_leg schema")
                    if latest.session_backend_opaque:
                        raise ValueError(
                            "worktree uses a newer unsupported session_backend schema"
                        )
                    current = tracking.derive_execution_leg(latest)
                    current_revision = (current.binding_revision if current is not None else 0)
                    reservation = (
                        _read_execution_leg_reservation(reservation_path)
                        if reservation_path.exists()
                        else None
                    )
                    if reservation is not None and reservation.get("phase") in {
                        "committing",
                        "releasing",
                    }:
                        result = _binding_from_payload(reservation.get("result_execution_leg"))
                        current_payload = (current.to_dict() if current is not None else None)
                        result_payload = (result.to_dict() if result is not None else None)
                        if current_payload == result_payload:
                            supplied_token = getattr(args, "reservation_token", None)
                            if (
                                args.action in {"set", "clear", "release"}
                                and reservation.get("token") != supplied_token
                            ):
                                raise ValueError("execution-leg reservation token mismatch")
                            reservation_path.unlink()
                            reservation = None
                            if args.action in {"set", "clear", "release"}:
                                _json_output(_execution_leg_payload(worktree_id, current))
                                return 0
                        elif (int(reservation.get("reserved_revision") or -1) != current_revision):
                            raise ValueError(
                                "execution-leg reservation transition cannot be "
                                "reconciled"
                            )

                    if args.action == "reserve":
                        if reservation is not None:
                            now = datetime.now(timezone.utc)
                            owner_live = _reservation_owner_liveness(reservation)
                            if owner_live is True or (
                                owner_live is None
                                and not _reservation_expired(reservation, now=now)
                            ):
                                owner = str(reservation.get("owner") or "<unknown>")
                                expires_at = str(reservation.get("expires_at") or "<unknown>")
                                raise ValueError(
                                    "execution-leg lifecycle operation is already "
                                    f"reserved by {owner} until {expires_at}"
                                )
                            previous = _binding_from_payload(
                                reservation.get("previous_execution_leg")
                            )
                            current_payload = (current.to_dict() if current is not None else None)
                            previous_payload = (
                                previous.to_dict() if previous is not None else None
                            )
                            if (
                                reservation.get("phase") == "prepared"
                                and current_payload == previous_payload
                            ):
                                reservation_path.unlink()
                                reservation = None
                            elif (
                                int(reservation.get("reserved_revision") or -1)
                                != current_revision
                            ):
                                raise ValueError(
                                    "expired execution-leg reservation cannot be "
                                    "reconciled because its revision changed"
                                )
                            else:
                                current = previous
                                current_revision = (
                                    current.binding_revision
                                    if current is not None
                                    else max(
                                        0,
                                        int(
                                            reservation.get(
                                                "reserved_revision"
                                            ) or 0
                                        ) - 1,
                                    )
                                )
                        if reservation is not None and (
                            int(reservation.get("reserved_revision") or -1)
                            != current_revision + 1
                        ):
                            raise ValueError(
                                "expired execution-leg reservation cannot be "
                                "reconciled because its revision changed"
                            )
                        if (
                            current is not None
                            and current.state in {"active", "unknown"}
                            and current.provider != args.provider
                        ):
                            raise ValueError(
                                "worktree is already owned by execution-leg "
                                f"provider {current.provider}"
                            )
                        now = datetime.now(timezone.utc)
                        expires_at = now + timedelta(seconds=args.lease_seconds)
                        token = secrets.token_hex(24)
                        previous = current.to_dict() if current is not None else None
                        binding = tracking.ExecutionLegBinding(
                            provider=args.provider,
                            state="unknown",
                            binding_revision=(
                                int(reservation.get("reserved_revision") or 0) + 1
                                if reservation is not None
                                else current_revision + 1
                            ),
                            blob=dict(current.blob) if current is not None else {},
                        )
                        reservation_value = {
                            "version": 1,
                            "phase": "prepared",
                            "owner": args.reservation_owner,
                            "owner_pid": getattr(
                                args, "reservation_owner_pid", None
                            ),
                            "owner_start_time": getattr(
                                args, "reservation_owner_start_time", None
                            ),
                            "token": token,
                            "operation": args.operation,
                            "provider": args.provider,
                            "created_at": now.isoformat(timespec="seconds"),
                            "expires_at": expires_at.isoformat(timespec="seconds"),
                            "reserved_revision": binding.binding_revision,
                            "previous_execution_leg": previous,
                        }
                        _write_execution_leg_reservation(reservation_path, reservation_value,)
                        latest.execution_leg = binding
                        latest.execution_leg_opaque = False
                        latest.execution_leg_raw = None
                        latest.session_backend = None
                        latest.session_backend_opaque = False
                        latest.session_backend_raw = None
                        tracking._save_record_unlocked(
                            latest,
                            yaml_path,
                            preserve_handoff_reservations=False,
                        )
                        reservation_value["phase"] = "reserved"
                        _write_execution_leg_reservation(reservation_path, reservation_value,)
                        _json_output({
                            **_execution_leg_payload(worktree_id, binding),
                            "reservation_token": token,
                            "reservation_owner": args.reservation_owner,
                            "created_at": now.isoformat(timespec="seconds"),
                            "expires_at": expires_at.isoformat(timespec="seconds"),
                            "operation": args.operation,
                            "previous_execution_leg": previous,
                        })
                        return 0

                    if args.action == "renew":
                        if reservation is None:
                            raise ValueError("execution-leg lifecycle operation is not reserved")
                        if reservation.get("token") != args.reservation_token:
                            raise ValueError("execution-leg reservation token mismatch")
                        if (int(reservation.get("reserved_revision") or -1) != current_revision):
                            raise ValueError("execution-leg reservation revision changed")
                        now = datetime.now(timezone.utc)
                        reservation["expires_at"] = (
                            now + timedelta(seconds=args.lease_seconds)
                        ).isoformat(timespec="seconds")
                        _write_execution_leg_reservation(reservation_path, reservation,)
                        _json_output({
                            **_execution_leg_payload(worktree_id, current),
                            "reservation_token": args.reservation_token,
                            "expires_at": reservation["expires_at"],
                        })
                        return 0

                    if args.action == "release":
                        if reservation is None:
                            raise ValueError("execution-leg lifecycle operation is not reserved")
                        if reservation.get("token") != getattr(
                            args, "reservation_token", None
                        ):
                            raise ValueError("execution-leg reservation token mismatch")
                        if (int(reservation.get("reserved_revision") or -1) != current_revision):
                            raise ValueError("execution-leg reservation revision changed")
                        binding = _binding_from_payload(reservation.get("previous_execution_leg"))
                        if binding is not None:
                            binding.binding_revision = current_revision + 1
                        latest.execution_leg = binding
                        latest.execution_leg_opaque = False
                        latest.execution_leg_raw = None
                        latest.session_backend = None
                        latest.session_backend_opaque = False
                        latest.session_backend_raw = None
                        reservation["phase"] = "releasing"
                        reservation["result_execution_leg"] = (
                            binding.to_dict() if binding is not None else None
                        )
                        _write_execution_leg_reservation(reservation_path, reservation,)
                        tracking._save_record_unlocked(
                            latest,
                            yaml_path,
                            preserve_handoff_reservations=False,
                        )
                        reservation_path.unlink()
                        _json_output(_execution_leg_payload(worktree_id, binding))
                        return 0

                    if reservation is not None:
                        supplied_token = getattr(args, "reservation_token", None)
                        if reservation.get("token") != supplied_token:
                            if supplied_token:
                                raise ValueError("execution-leg reservation token mismatch")
                            raise ValueError("execution-leg lifecycle operation is reserved")
                        if (int(reservation.get("reserved_revision") or -1) != current_revision):
                            raise ValueError("execution-leg reservation revision changed")
                    elif getattr(args, "reservation_token", None):
                        raise ValueError("execution-leg lifecycle operation is not reserved")
                    expected = args.if_match_revision
                    if expected is not None and expected != current_revision:
                        raise ValueError(
                            "execution-leg revision mismatch: "
                            f"expected {expected}, found {current_revision}"
                        )
                    if (
                        args.action in {"set", "clear"}
                        and current is not None
                        and current.state in {"active", "unknown"}
                    ):
                        if reservation is None:
                            raise ValueError(
                                "destructive mutation of an active execution leg "
                                "requires its provider-owning reservation"
                            )
                        reservation_provider = reservation.get("provider")
                        if reservation_provider != current.provider:
                            raise ValueError(
                                "execution-leg reservation does not own the "
                                f"current provider {current.provider}"
                            )
                        if args.provider != reservation_provider:
                            raise ValueError(
                                "execution-leg mutation provider must match the "
                                f"owning reservation provider {reservation_provider}"
                            )

                    if args.action == "clear":
                        latest.execution_leg = None
                        latest.execution_leg_opaque = False
                        latest.execution_leg_raw = None
                        latest.session_backend = None
                        latest.session_backend_opaque = False
                        latest.session_backend_raw = None
                        binding = None
                    else:
                        if (
                            latest.status in {"finalizing", "finalized"}
                            and args.state in {"active", "unknown"}
                        ):
                            raise ValueError(
                                "cannot activate an execution leg for a finalizing "
                                "or finalized worktree"
                            )
                        if args.binding_revision <= current_revision:
                            raise ValueError(
                                "execution-leg binding revision must increase: "
                                f"current {current_revision}, requested "
                                f"{args.binding_revision}"
                            )
                        binding = tracking.ExecutionLegBinding(
                            provider=args.provider,
                            state=args.state,
                            binding_revision=args.binding_revision,
                            blob=_read_execution_leg_blob(args.blob_file),
                        )
                        latest.execution_leg = binding
                        latest.execution_leg_opaque = False
                        latest.execution_leg_raw = None
                        latest.session_backend = None
                        latest.session_backend_opaque = False
                        latest.session_backend_raw = None
                    if reservation is not None:
                        reservation["phase"] = "committing"
                        reservation["result_execution_leg"] = (
                            binding.to_dict() if binding is not None else None
                        )
                        _write_execution_leg_reservation(reservation_path, reservation,)
                    tracking._save_record_unlocked(
                        latest,
                        yaml_path,
                        preserve_handoff_reservations=False,
                    )
                    if reservation is not None:
                        reservation_path.unlink()
            finally:
                lifecycle_lock.release()
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return _json_error(str(exc), exit_code=3)

    _json_output(_execution_leg_payload(worktree_id, binding))
    return 0


def _unsupported_hosted_launch(
    record: tracking.WorktreeRecord | None,
    operation: str,
) -> str:
    """Fail-closed message for launch paths that do not own hosted sessions."""
    if record is not None and _hosted_session_blocks_cleanup(record):
        return (
            f"{operation} cannot launch directly while the worktree has a "
            "live or unknown hosted-session binding"
        )
    return ""


def _paired_knowledge_allocation_preflight(config: cfg.Config) -> None:
    """Preflight the paired-knowledge codename-allocation policy with NO
    side effect (round-6 review finding).

    Mirrors the same is-this-a-bound-worktree-class-stateless-harness
    resolution `_carve_paired_knowledge` performs, purely to decide
    whether that function's eventual allocation would be policy-blocked
    -- so `_create_worktree_core` can call this BEFORE it creates the
    harness's own worktree/branch/record, closing the gap where a
    paired-knowledge policy violation only surfaced after those harness
    side effects already existed (with no rollback).

    Raises :class:`codename_tracking.CodenameAttributionPolicyError` on a
    genuine policy violation. Any OTHER failure (state-root resolution,
    config load, an unregistered knowledge repo) is swallowed -- this is
    advisory-only pre-validation; `_carve_paired_knowledge`'s own
    preflight and second (lock-held) revalidation remain the actual
    fail-closed gate for the allocation itself.
    """
    if os.environ.get("AGENT_WORKTREES_NO_PAIR"):
        return
    try:
        res = state_root_mod.resolve_state_root(config)
    except Exception:
        return
    if not (res.requires_external and res.bound and res.path):
        return

    from . import repos as repos_mod

    entry = repos_mod.find_repo(res.repo)
    is_worktree_class = bool(entry) and repos_mod.normalize_class(entry.repo_class) in (
        "worktree", "knowledge",
    )
    if not is_worktree_class:
        return
    try:
        knowledge_config = cfg.load_config(project=res.repo)
        knowledge_policy_kwargs = codename_tracking.allocation_policy_kwargs_for_repo(
            knowledge_config
        )
    except Exception:
        return
    codename_tracking.check_allocation_policy(**knowledge_policy_kwargs)


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
    harness_ref = tracking.format_claim_ref(config.machine, config.repo_name or "?", harness_id)
    entry = repos_mod.find_repo(knowledge_name)
    is_worktree_class = bool(entry) and repos_mod.normalize_class(entry.repo_class) in (
        "worktree", "knowledge",
    )
    remote = git_ops.resolve_remote_name(
        (entry.remote or "origin") if entry else "origin",
        cwd=knowledge_anchor,
    )
    default_branch = (entry.default_branch or "main") if entry else "main"
    prepared = _prepare_worktree_source(
        knowledge_anchor,
        remote=remote,
        default_branch=default_branch,
        fast_forward_anchor=getattr(config, "auto_fast_forward", True),
        label=f"paired knowledge repo '{knowledge_name}'",
    )

    if not is_worktree_class:
        # Non-worktree-class knowledge -> operate on the anchor (no 2nd carve).
        anchor_ref = tracking.format_claim_ref(
            config.machine,
            knowledge_name,
            os.path.basename(knowledge_anchor.rstrip("/\\")) or "anchor",
        )
        print(
            f"Paired knowledge repo '{knowledge_name}' is not worktree-class; "
            f"pairing at its anchor (no separate worktree).",
            file=sys.stderr,
        )
        return {
            "pair_id": pair_id, "pair_role": "harness", "pair_ref": anchor_ref,
            "pair_kind": "anchor",
        }

    # Worktree-class knowledge -> carve its paired worktree.
    knowledge_id = f"{config.machine}-{plat_short}-{timestamp}-{suffix}-k"
    knowledge_branch = f"worktree/{knowledge_id}"
    knowledge_wt_root = cfg.derive_worktree_root(knowledge_anchor)
    knowledge_wt_path = str(Path(knowledge_wt_root) / knowledge_id)
    knowledge_ref = tracking.format_claim_ref(config.machine, knowledge_name, knowledge_id)
    knowledge_tracking_path = cfg.project_dir(knowledge_name) / "worktrees"

    # pr-attribution-codenames Phase 2 (#2838): assign the paired knowledge
    # worktree's own codename (scoped to its own project's tracking
    # directory and wordlist, not a share of the harness's) BEFORE the git
    # worktree/branch is created -- an allocation failure (an exhausted
    # finite configured wordlist) must never leave an orphaned checkout with
    # no tracking record. Assignment + the eventual record-write share one
    # allocation lock (see codename_tracking.py) so a concurrent carve can
    # never pick the same candidate.
    #
    # codename-attribution-by-default (round-10/11 findings): the
    # allocation-policy check below is deliberately OUTSIDE this
    # config-load try/except -- an earlier design had it inside, where a
    # bare `except Exception` swallowed the policy violation into a silent
    # `knowledge_wordlist = None` fallback, letting this path allocate a
    # codename (with codename_source="built-in") for exactly the
    # custom-wordlist/unconfigured-source_attribution repo the policy
    # exists to block. A config-load failure degrades ONLY the wordlist
    # resolution (matching this function's existing fail-safe posture);
    # it never silently authorizes an otherwise-blocked allocation.
    try:
        knowledge_config = cfg.load_config(project=knowledge_name)
        knowledge_wordlist = codename_tracking.wordlist_for_repo(knowledge_config)
        knowledge_policy_kwargs = codename_tracking.allocation_policy_kwargs_for_repo(
            knowledge_config
        )
    except Exception:
        knowledge_wordlist = None
        knowledge_policy_kwargs = {
            "codename_source": "built-in", "pr_enabled": False,
            "source_attribution_configured": False,
        }

    # Preflight (round-12/13 finding): validate BEFORE any side effect this
    # function performs (worktree, branch, or record) -- the `create` flow
    # already persisted the HARNESS worktree/branch/record before calling
    # this function, so failing here does not roll those back (no rollback
    # protocol is implemented; see the second revalidation below for the
    # orphan-naming backstop).
    codename_tracking.check_allocation_policy(**knowledge_policy_kwargs)

    with codename_tracking.allocation_lock(knowledge_tracking_path):
        # Second revalidation (round-13/14 finding, sharpened by a PR
        # #3037 review finding) immediately before the first side effect
        # below -- RECOMPUTES the policy kwargs from a freshly-loaded
        # config, never reuses the pre-lock snapshot above: a config
        # change landing in the window between the preflight and this
        # point must be observed here, or the "shrinks the TOCTOU window"
        # claim is hollow (the earlier code re-checked the SAME stale
        # values, which can never disagree with themselves). If hit, the
        # harness worktree/branch created by the caller before this
        # function ran is now orphaned (no automatic rollback) -- name it
        # explicitly so the operator can remove it.
        try:
            fresh_knowledge_config = cfg.load_config(project=knowledge_name)
            fresh_policy_kwargs = codename_tracking.allocation_policy_kwargs_for_repo(
                fresh_knowledge_config
            )
            knowledge_wordlist = codename_tracking.wordlist_for_repo(
                fresh_knowledge_config
            )
        except Exception:
            # PR #3037 review finding: a reload failure here must NEVER
            # silently fall back to the pre-lock snapshot -- if config
            # changed from an explicit opt-in to an unconfigured custom
            # wordlist in the interval, reusing the (permissive) stale
            # snapshot would still authorize an allocation the fresh state
            # would have blocked. Degrade to the conservative,
            # ALWAYS-non-authorizing state instead (never the reverse):
            # unconditionally forces `check_allocation_policy` to raise,
            # so a transient reload failure fails the operation closed
            # rather than silently authorizing it.
            fresh_policy_kwargs = {
                "codename_source": "custom", "pr_enabled": True,
                "source_attribution_configured": False,
            }
        try:
            codename_tracking.check_allocation_policy(**fresh_policy_kwargs)
        except codename_tracking.CodenameAttributionPolicyError as exc:
            harness_worktree_path = str(
                Path(config.default_repo.worktree_root) / harness_id
            )
            raise codename_tracking.CodenameAttributionPolicyError(
                f"{exc} A harness worktree/branch was already created "
                "before this policy violation was detected and is left in "
                f"place (no automatic rollback): worktree="
                f"{harness_worktree_path!r} branch={f'worktree/{harness_id}'!r}. "
                "Remove it manually with `git worktree remove` if unwanted."
            ) from exc
        knowledge_codename = codename_tracking.assign_new_codename(
            knowledge_tracking_path, knowledge_wordlist
        )

        Path(knowledge_wt_root).mkdir(parents=True, exist_ok=True)
        print(
            f"Creating paired knowledge worktree on branch {knowledge_branch}...",
            file=sys.stderr,
        )
        git_ops.create_worktree(
            knowledge_anchor,
            knowledge_wt_path,
            knowledge_branch,
            prepared.start_point,
        )

        tracking.create_new_record(
            worktree_id=knowledge_id,
            branch=knowledge_branch,
            worktree_path=knowledge_wt_path,
            repo=knowledge_name,
            machine=config.machine,
            platform_name=plat,
            tracking_path=knowledge_tracking_path,
            pair_id=pair_id,
            pair_role="knowledge",
            pair_ref=harness_ref,
            pair_kind="worktree",
            codename=knowledge_codename,
            codename_source=fresh_policy_kwargs["codename_source"],
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
        "pair_id": pair_id, "pair_role": "harness", "pair_ref": knowledge_ref,
        "pair_kind": "worktree",
    }


def _stamp_and_compose_paired_knowledge(
    config: cfg.Config,
    record: tracking.WorktreeRecord,
    worktree_path: str,
    pair_stamp: dict | None,
) -> dict | None:
    """Persist a new pair before composing its launch-time plugin overlay."""
    if not pair_stamp:
        return None
    record.pair_id = pair_stamp.get("pair_id")
    record.pair_role = pair_stamp.get("pair_role")
    record.pair_ref = pair_stamp.get("pair_ref")
    record.pair_kind = pair_stamp.get("pair_kind")
    tracking.save_record(record)

    from . import knowledge_plugins

    try:
        return knowledge_plugins.compose_from_pair(cwd=worktree_path, config=config)
    except knowledge_plugins.KnowledgePluginError as exc:
        # The pair is already durable. Keep create's resource identity usable
        # and let the normal launcher retry its strict pre-Copilot preflight.
        print(
            f"paired-knowledge plugin composition failed; the launch preflight will retry: {exc}",
            file=sys.stderr,
        )
        return {"action": "error", "error": str(exc)}


def _journal_owner_reciprocal_claim(
    config: cfg.Config,
    worktree_id: str,
    owner_ref: str | None,
    *,
    owner_locked: bool = False,
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
        owner_path, _owner_wt, _err = _resolve_owner_ref_record_path(owner_ref, config,)
        if owner_path is None or not owner_path.exists():
            return False
        child_ref = tracking.format_claim_ref(
            config.machine,
            config.repo_name,
            worktree_id,
        )

        def _write_claim() -> None:
            owner_rec = tracking.load_record(owner_path)
            tracking.add_resource_claim(
                owner_rec,
                tracking.ResourceClaim(
                    kind="worktree",
                    ref=child_ref,
                    created_at=tracking._now_iso(),
                    state=obligations.ACTIVE,
                ),
                save=False,
            )
            tracking.save_record(owner_rec, owner_path)

        if owner_locked:
            _write_claim()
        else:
            with tracking._RecordLock(owner_path, require_sidecar=True):
                _write_claim()
        print(f"Journaled this worktree as an obligation on {owner_ref}.", file=sys.stderr,)
        return True
    except Exception as exc:  # never let journaling break the carve
        print(f"owner-claim journaling failed (non-fatal): {exc}", file=sys.stderr)
        return False


def _prepare_worktree_source(
    anchor: str | Path,
    *,
    remote: str,
    default_branch: str,
    fast_forward_anchor: bool,
    label: str,
) -> git_ops.WorktreeBaseResult:
    """Prepare one source checkout and report degraded freshness honestly."""
    print(f"Fetching latest from {remote} for {label}...", file=sys.stderr)
    prepared = git_ops.prepare_worktree_base(
        anchor,
        remote=remote,
        default_branch=default_branch,
        fast_forward_anchor=fast_forward_anchor,
    )
    if prepared.fetch_error:
        print(
            f"Note: fetch failed for {label}; using last-known "
            f"'{prepared.start_point}' ({prepared.fetch_error}).",
            file=sys.stderr,
        )
    if prepared.anchor.updated:
        print(
            f"Fast-forwarded {label} anchor by {prepared.anchor.behind} commit(s).",
            file=sys.stderr,
        )
    elif prepared.anchor.reason in {
        "dirty",
        "ahead",
        "diverged",
        "detached",
        "non-default-branch",
        "ff-failed",
    }:
        print(
            f"Note: {label} anchor is {prepared.anchor.reason}; left untouched.",
            file=sys.stderr,
        )
    if prepared.start_point != f"{remote}/{default_branch}":
        print(
            f"Note: '{remote}/{default_branch}' not found for {label}; "
            f"branching from '{prepared.start_point}' instead.",
            file=sys.stderr,
        )
    return prepared


def _creation_parent_session(
    explicit: str | None,
    *,
    inherit_ambient: bool,
) -> str | None:
    if explicit is not None:
        return explicit
    if inherit_ambient:
        return os.environ.get("COPILOT_AGENT_SESSION_ID") or None
    return None


def _create_worktree_core(
    config: cfg.Config,
    *,
    profile: cfg.CopilotProfile | None = None,
    profile_is_explicit: bool = True,
    no_mux: bool = False,
    kind: tracking.WorktreeKind = "session",
    owner: str | None = None,
    interface: tracking.WorktreeInterface | None = None,
    origin: tracking.WorktreeOrigin | None = None,
    name: str | None = None,
    parent_session: str | None = None,
    inherit_parent_session: bool = True,
    caller_worktree: str | None = None,
    owner_ref: str | None = None,
    dispatch_attempt: dict[str, object] | None = None,
    launch_preflight: LaunchPreflight | None = None,
    recovery: bool = False,
    bound_agent: str | None = None,
    no_pair: bool = False,
) -> dict:
    """Create a new worktree and return a dict with worktree info + launch plan.

    Performs the side-effects (fetch, git worktree add, tracking YAML,
    permissions) but does NOT launch copilot.  Returns a dict suitable
    for JSON serialization.

    ``kind="system"`` marks the worktree as daemon-owned (hidden from the
    Picker, exempt from routine cleanup); ``owner``/``name`` label it for the
    System-menu browse view.

    ``no_pair`` opts THIS creation out of the paired-knowledge carve
    regardless of ``origin`` -- for a registrar/pool declaration that has no
    bound knowledge repo to give every worker (e.g. a portable pool). It is
    a narrower, per-call sibling of the blunt whole-host
    ``AGENT_WORKTREES_NO_PAIR`` env var; either one skips the carve.

    Raises ``RuntimeError`` on failure.
    """
    repo = config.default_repo
    if kind != "system" and getattr(repo, "knowledge_only", False):
        raise RuntimeError(
            f"'{config.repo_name}' is a knowledge-only companion repo "
            "(knowledge_only: true) -- it cannot be driven directly. "
            "It is carved automatically as a paired '-k' worktree when the "
            "stateless harness bound to it (see its knowledge_repo config) "
            "creates its own worktree; work there instead."
        )
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

    if owner_ref:
        parsed_owner = tracking.parse_claim_ref(owner_ref)
        if parsed_owner is None or not parsed_owner.is_qualified:
            raise RuntimeError(
                f"--owner-ref must be qualified as machine/project/worktree_id (got {owner_ref!r})"
            )
        if parsed_owner.machine != config.machine:
            raise RuntimeError(
                f"cross-machine owner {owner_ref} cannot synchronously accept "
                "this worktree obligation; use a dispatch/lease flow that "
                "persists remote ownership before creation"
            )
        readiness = _coordination_readiness_for_owner_ref(owner_ref, config)
        if not readiness.ready:
            raise CoordinationReadinessFailure(readiness)

    _validate_profile_assignment_config(config)
    launches_copilot = kind != "system"
    fake_args = None
    if launches_copilot:
        fake_args = argparse.Namespace(
            copilot_args=[],
            recovery=recovery,
            no_mux=no_mux,
            no_resume=False,
            profile=(profile.name if profile is not None and profile_is_explicit else None),
        )
        launch_preflight = launch_preflight or _preflight_launch(
            config,
            fake_args,
            worktree_path,
        )
        if launch_preflight.error:
            raise LaunchPreflightError(launch_preflight.error)

    # Round-6 review finding: preflight the PAIRED-KNOWLEDGE allocation
    # policy here, BEFORE any side effect below (owner claim, harness git
    # worktree/branch, tracking record) -- the ONLY preflight this policy
    # previously had lived inside `_carve_paired_knowledge`, which
    # `_create_worktree_core` calls AFTER the harness worktree/branch/
    # record already exist, so a violation there still left those harness
    # side effects behind (and without the orphan-path context the later
    # revalidation adds). This mirrors the harness's own preflight-then-
    # revalidate-under-lock shape immediately below; `_carve_paired_
    # knowledge`'s own preflight/revalidation stay in place as defense in
    # depth for the narrower TOCTOU window between here and its own carve.
    #
    # Pairing applies to every `kind == "session"` worktree regardless of
    # `origin` (#catch-22 follow-up to #3207): a delegate/system-origin
    # dispatch worker needs its own paired knowledge sibling exactly as much
    # as an interactive operator does -- reciprocal disposal (see
    # `terminal_conclusion.conclude_disposable_worktree`) is what lets a
    # dispatch attempt's conclusion release both halves together, so origin
    # is no longer a reason to skip the carve. `no_pair` is the intentional,
    # explicit per-call opt-out for a registrar/pool with no bound knowledge
    # repo to hand its workers.
    if kind == "session" and not no_pair:
        _paired_knowledge_allocation_preflight(config)

    # Ensure root exists
    Path(repo.worktree_root).mkdir(parents=True, exist_ok=True)

    prepared = _prepare_worktree_source(
        repo.anchor,
        remote=repo.remote,
        default_branch=repo.default_branch,
        fast_forward_anchor=config.auto_fast_forward,
        label="repository",
    )

    # pr-attribution-codenames Phase 2 (#2838): assign the codename FIRST --
    # before the owner claim is journaled and before the git worktree/branch
    # is created. An allocation failure (an exhausted finite configured
    # wordlist) must never leave the owner ledger with a live obligation
    # pointing at a worktree that was never created, nor an orphaned
    # checkout with no tracking record. The whole sequence below (allocate
    # -> acquire the owner record lock -> journal owner claim -> create the
    # git worktree -> write the record) is held under one cross-process
    # allocation lock so two concurrent `create` calls can never observe the
    # same existing codename set and choose the same candidate.
    #
    # Lock order is always this module's `allocation_lock` (outer) then any
    # per-record lock (inner) -- here, the owner's -- never the reverse.
    # `codename_tracking.ensure_codename` (the resume/status backfill path)
    # acquires them in this same order; reversing it here (owner lock, then
    # allocation lock, as an earlier revision did) is a real cross-process
    # deadlock hazard against a concurrent backfill of that same owner
    # worktree's own codename.
    #
    # codename-attribution-by-default (round-21 finding): preflight the
    # allocation-time policy BEFORE any create side effect below (owner
    # claim journal, git worktree/branch, tracking record) -- a distinct,
    # more severe gap than the paired-knowledge path since this is the
    # ordinary `create` path every worktree goes through.
    new_codename_source = codename_tracking.classify_codename_source(
        getattr(repo, "codename", None)
    )
    codename_tracking.check_allocation_policy(
        pr_enabled=bool(getattr(repo.pr, "enabled", False)),
        codename_source=new_codename_source,
        source_attribution_configured=bool(
            getattr(repo.pr, "source_attribution_configured", False)
        ),
    )
    tracking_path = cfg.tracking_dir()
    tracking_path.mkdir(parents=True, exist_ok=True)
    with codename_tracking.allocation_lock(tracking_path):
        # Second revalidation (round-13/14 finding, sharpened by a PR
        # #3037 review finding) immediately before the first side effect
        # below -- RECOMPUTES from a freshly-loaded config, never reuses
        # the pre-lock `repo`/`new_codename_source` snapshot above: a
        # config change landing in the window between the preflight and
        # this point must be observed here, or re-checking the SAME
        # stale values (which can never disagree with themselves) makes
        # the claimed TOCTOU-window shrink hollow.
        try:
            fresh_config = cfg.load_config(project=config.repo_name)
            fresh_repo = fresh_config.default_repo
            fresh_codename_source = codename_tracking.classify_codename_source(
                getattr(fresh_repo, "codename", None)
            )
            fresh_pr_enabled = bool(getattr(fresh_repo.pr, "enabled", False))
            fresh_sac = bool(
                getattr(fresh_repo.pr, "source_attribution_configured", False)
            )
            fresh_wordlist_config = fresh_config
        except Exception:
            # PR #3037 review finding: never fall back to the pre-lock
            # snapshot on a reload failure -- degrade to the
            # conservative, ALWAYS-non-authorizing state instead (see the
            # matching fix in _carve_paired_knowledge for the full
            # rationale).
            fresh_codename_source = "custom"
            fresh_pr_enabled = True
            fresh_sac = False
            fresh_wordlist_config = config
        codename_tracking.check_allocation_policy(
            pr_enabled=fresh_pr_enabled,
            codename_source=fresh_codename_source,
            source_attribution_configured=fresh_sac,
        )
        new_codename = codename_tracking.assign_new_codename(
            tracking_path, codename_tracking.wordlist_for_repo(fresh_wordlist_config)
        )

        # Creator ownership is established before the resource exists, and
        # its ledger lock stays held until the child tracking record is
        # durable. Thus finalize cannot hand off/clean a not-yet-created
        # child reference.
        owner_guard = None
        if owner_ref:
            owner_path, _owner_id, owner_err = _resolve_owner_ref_record_path(owner_ref, config)
            if owner_err or owner_path is None or not owner_path.exists():
                raise RuntimeError(owner_err or f"owner ledger is missing: {owner_ref}")
            owner_guard = tracking._RecordLock(owner_path, require_sidecar=True)
            owner_guard.__enter__()

        try:
            if owner_guard is not None and not _journal_owner_reciprocal_claim(
                config, worktree_id, owner_ref, owner_locked=True
            ):
                raise RuntimeError(f"owner {owner_ref} cannot accept a new worktree obligation")

            print(f"Creating worktree on branch {branch}...", file=sys.stderr)
            git_ops.create_worktree(
                repo.anchor,
                worktree_path,
                branch,
                prepared.start_point,
            )

            # Write tracking YAML
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
                codename=new_codename,
                codename_source=fresh_codename_source,
                dispatch_attempt=(
                    tracking.DispatchAttempt(
                        task_id=str(dispatch_attempt["task_id"]),
                        reservation_key=str(dispatch_attempt["reservation_key"]),
                        attempt=int(dispatch_attempt["attempt"]),
                        driver=str(dispatch_attempt["driver"]),
                        supervisor=str(dispatch_attempt["supervisor"]),
                        creator_machine=config.machine,
                    )
                    if dispatch_attempt is not None
                    else None
                ),
                # #1029: link the new worktree back to the session that spawned it, so a
                # later resume (esp. a PR/feedback worktree with no sessions of its own)
                # restores context instead of cold-starting.
                parent_session=_creation_parent_session(
                    parent_session,
                    inherit_ambient=inherit_parent_session,
                ),
                # #2178: for a bridge spawn, record the caller worktree so the Picker can
                # jump back to it.
                caller_worktree=caller_worktree or None,
                # resource-claims: the qualified backward owner link, for a worktree
                # spun up as another worktree's outbound resource (stamped by `run` /
                # an explicit --owner-ref). Absent = unclaimed.
                owner_ref=owner_ref or None,
                bound_agent=bound_agent or None,
            )
        finally:
            if owner_guard is not None:
                owner_guard.__exit__(None, None, None)

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

    # citadel paired -harness/-knowledge worktree lifecycle (#957): when this is
    # a stateless harness bound to a knowledge repo, carve/stamp the knowledge
    # pair together with this worktree and cross-stamp the linkage. Only for
    # plain session worktrees (never system/bridge), and fully fail-safe -- a
    # pairing failure never breaks the harness carve. Applies regardless of
    # `origin` (see the preflight comment above for why); `no_pair` is the
    # explicit per-call opt-out.
    if kind == "session" and not no_pair:
        try:
            pair_stamp = _carve_paired_knowledge(
                config,
                harness_id=worktree_id,
                timestamp=timestamp,
                suffix=suffix,
                plat=plat,
                plat_short=plat_short,
            )
        except codename_tracking.CodenameAttributionPolicyError:
            # codename-attribution-by-default (round-10/11 finding): this is
            # NOT an ordinary pairing glitch -- it is the paired-knowledge
            # allocation policy correctly refusing to leak a custom
            # vocabulary term. Re-raise (abort `create` outright) rather
            # than degrading to a logged warning and a silent `pair_stamp =
            # None`, which would defeat the policy entirely.
            raise
        except Exception as exc:  # pragma: no cover - defensive
            print(f"paired-knowledge carve failed (non-fatal): {exc}", file=sys.stderr)
            pair_stamp = None
        _stamp_and_compose_paired_knowledge(config, record, worktree_path, pair_stamp)

    # Copilot discovers repository settings before sessionStart. A committed
    # relative-path directory marketplace source (this repo's own) resolves
    # live against whichever checkout is active, so no local override seeding
    # is needed here (#marketplace-override-retirement).

    result = {"worktree": _worktree_to_dict(record)}
    if not launches_copilot:
        return result

    # Build launch command (for caller to use).
    assert fake_args is not None
    assert launch_preflight is not None
    selection = _launch_profile_selection(
        config,
        fake_args,
        record,
        lane="new",
        generation_key=f"new:{worktree_id}",
        ordinary_profile=profile,
        explicit_profile=profile if profile_is_explicit else None,
    )
    _reflect_assignment(record, selection)
    launch_cmd = _build_launch_cmd(
        config,
        fake_args,
        worktree_path,
        profile=selection.profile,
        preflight=launch_preflight,
    )
    env = _apply_assignment_env(
        _build_env(
            selection.profile,
            _repo_session_env(config, worktree_path),
            work_dir=worktree_path,
        ),
        selection,
    )
    result["worktree"] = _worktree_to_dict(record)
    result["launch"] = {
        "action": "exec",
        "work_dir": worktree_path,
        "cmd": launch_cmd,
        "env": env,
        "worktree_id": worktree_id,
        "post_exit": True,
        "no_mux": no_mux,
        # Authoritative for downstream out-of-process calls (e.g. the
        # launcher's `execution-leg get`), which must scope to the
        # project that actually owns this worktree -- not whatever ambient
        # project the launcher itself started with, nor the mutable
        # process-global `cfg.active_project()` (#2338).
        "project": config.repo_name,
    }
    if selection.assignment is not None:
        result["launch"]["profile_assignment"] = profile_assignment.metadata(selection.assignment)
    return result


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


@dataclasses.dataclass(frozen=True)
class LaunchPreflight:
    """Read-only validation required before constructing a launch plan."""

    config_root: state_root_mod.ConfigRoot | None = None

    @property
    def error(self) -> str | None:
        if self.config_root is not None and not self.config_root.path:
            return (
                self.config_root.error or "could not resolve the machine-local configuration root"
            )
        return None

    @property
    def config_root_path(self) -> str:
        return (self.config_root.path or "") if self.config_root else ""


class LaunchPreflightError(RuntimeError):
    """A launch plan cannot be built without mutating unsafe state."""


def _preflight_launch(
    config: cfg.Config,
    args: argparse.Namespace,
    work_dir: str,
) -> LaunchPreflight:
    """Validate normalized setup state before any launch-side mutation."""
    recovery = getattr(args, "recovery", False)
    repo = config.default_repo
    plat_key = config.platform if config.platform != "wsl" else "linux"
    launch_map = repo.launch_recovery if recovery else repo.launch
    config_root = None
    if not recovery and plat_key not in launch_map and repo.setup_hook.get(plat_key):
        config_root = state_root_mod.resolve_config_root(
            config,
            cwd=work_dir,
            project=cfg.active_project(),
        )
    return LaunchPreflight(config_root=config_root)


def _launch_preflight_error(
    preflight: LaunchPreflight,
    *,
    json_output: bool = False,
) -> int:
    """Emit a controlled launch error in the caller's established format."""
    message = preflight.error or "launch preflight failed"
    if json_output:
        return _json_error(message, exit_code=3)
    print(f"  \u2717 {message}", file=sys.stderr)
    _emit_plan({"action": "error", "error": message, "exit_code": 3})
    return 3


def _build_launch_cmd(
    config: cfg.Config,
    args: argparse.Namespace,
    work_dir: str,
    profile: cfg.CopilotProfile | None = None,
    *,
    preflight: LaunchPreflight | None = None,
    fallback_copilot_path: str | None = None,
) -> list[str]:
    """Build the launch command from config or fallback convention.

    If the repo config has ``launch`` / ``launch_recovery`` entries for
    the current platform, those are used with variable substitution.
    Otherwise, in precedence order: a repo ``setup_hook`` or ``copilot_path``
    selects the
    **normalized** launch (the default-setup launcher runs the repo hook, then
    execs Copilot); else a legacy ``tools/setup/setup.{ps1,sh}`` is run as the
    session command; else the plugin's ``default-setup.{ps1,sh}``. A verified
    predecessor executable may be supplied as a final Copilot-path fallback for
    handoff launches only; explicit launch templates, configured
    ``copilot_path``, and legacy setup scripts remain authoritative.
    """
    recovery = getattr(args, "recovery", False)
    repo = config.default_repo
    plat = config.platform  # "windows", "wsl", or "linux"
    plat_key = plat if plat != "wsl" else "linux"
    is_windows = platform.system() == "Windows"
    preflight = preflight or _preflight_launch(config, args, work_dir)
    if preflight.error:
        raise LaunchPreflightError(preflight.error)

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
            "home": os.path.expanduser("~"),
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
            "home": os.path.expanduser("~"),
        }
        session_dirs = [d.format(**variables) for d in repo.session_path.get(plat_key, [])]
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
        copilot_path = repo.copilot_path.get(plat_key)
        configured_copilot_path = copilot_path.format(**variables) if copilot_path else ""
        resolved_copilot_path = configured_copilot_path or fallback_copilot_path or ""
        if hook_path:
            # (1) Normalized launch via the default-setup launcher + repo hook.
            resolved_hook = hook_path.format(**variables)
            if not os.path.isabs(resolved_hook):
                resolved_hook = str(Path(anchor) / resolved_hook)
            config_root_path = preflight.config_root_path
            if is_windows:
                launcher = str(inst.install_dir() / "scripts" / "default-setup.ps1")
                cmd = [
                    "pwsh.exe",
                    "-NoProfile",
                    "-NoLogo",
                    "-File",
                    launcher,
                    "-Machine",
                    config.machine,
                    "-SetupHook",
                    resolved_hook,
                ]
                if config_root_path:
                    cmd += ["-ConfigRoot", config_root_path, "-RuntimePython", sys.executable]
                if session_path_arg:
                    cmd += ["-SessionPath", session_path_arg]
                if resolved_env_script:
                    cmd += ["-EnvScript", resolved_env_script]
                if resolved_copilot_path:
                    cmd += ["-CopilotPath", resolved_copilot_path]
                if recovery:
                    cmd.append("-Recovery")
            else:
                launcher = str(inst.install_dir() / "scripts" / "default-setup.sh")
                cmd = [
                    "bash",
                    launcher,
                    "--machine",
                    config.machine,
                    "--setup-hook",
                    resolved_hook,
                ]
                if config_root_path:
                    cmd += ["--config-root", config_root_path, "--runtime-python", sys.executable]
                if session_path_arg:
                    cmd += ["--session-path", session_path_arg]
                if resolved_env_script:
                    cmd += ["--env-script", resolved_env_script]
                if resolved_copilot_path:
                    cmd += ["--copilot-path", resolved_copilot_path]
                if recovery:
                    cmd.append("--recovery")
        elif is_windows:
            setup_path = str(Path(anchor) / "tools" / "setup" / "setup.ps1")
            legacy = (
                Path(setup_path).is_file()
                and not resolved_env_script
                and not configured_copilot_path
            )
            if not legacy:
                setup_path = str(inst.install_dir() / "scripts" / "default-setup.ps1")
            cmd = [
                "pwsh.exe",
                "-NoProfile",
                "-NoLogo",
                "-File",
                setup_path,
                "-Machine",
                config.machine,
            ]
            # session_path is only understood by the default-setup launcher;
            # never pass it to a legacy setup.ps1 (unknown params would leak
            # through to Copilot as bogus args).
            if session_path_arg and not legacy:
                cmd += ["-SessionPath", session_path_arg]
            if resolved_env_script:
                cmd += ["-EnvScript", resolved_env_script]
            if resolved_copilot_path and not legacy:
                cmd += ["-CopilotPath", resolved_copilot_path]
            if recovery:
                cmd.append("-Recovery")
        else:
            setup_path = str(Path(anchor) / "tools" / "setup" / "setup.sh")
            legacy = (
                Path(setup_path).is_file()
                and not resolved_env_script
                and not configured_copilot_path
            )
            if not legacy:
                setup_path = str(inst.install_dir() / "scripts" / "default-setup.sh")
            cmd = ["bash", setup_path, "--machine", config.machine]
            if session_path_arg and not legacy:
                cmd += ["--session-path", session_path_arg]
            if resolved_env_script:
                cmd += ["--env-script", resolved_env_script]
            if resolved_copilot_path and not legacy:
                cmd += ["--copilot-path", resolved_copilot_path]
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
    is_acp = "--acp" in passthrough
    if not is_acp and not any(
        a == flag for a in passthrough for flag in ("--allow-all-tools", "--allow-all", "--yolo")
    ):
        cmd.append("--allow-all")

    # Every resolved session command passes through one installed wrapper.
    # This gives optional sibling integrations a single pre-exec seam even for
    # explicit and legacy launch templates, while recovery remains available.
    if is_windows:
        wrapper = str(inst.install_dir() / "scripts" / "launch-command.ps1")
        prefix = ["pwsh.exe", "-NoProfile", "-NoLogo", "-File", wrapper]
    else:
        wrapper = str(inst.install_dir() / "scripts" / "launch-command.sh")
        prefix = ["bash", wrapper]
    if recovery:
        prefix.append("--recovery")
    prefix.append("--")
    return prefix + cmd


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


def _wait_for_handoff_candidate(
    record_path: Path,
    token: str,
    pane_id: str | None,
    *,
    timeout: float = 30.0,
    mux_session: str | None = None,
    predecessor_session_id: str | None = None,
) -> tuple[str | None, str]:
    """Wait until a successor is confirmed for the exact handoff token.

    See ``sessions_pane_retire.wait_for_handoff_candidate`` (moved there to
    keep this grandfathered module's shrink-only size baseline).
    """
    return sessions_pane_retire.wait_for_handoff_candidate(
        record_path, token, pane_id, timeout=timeout, mux_session=mux_session,
        predecessor_session_id=predecessor_session_id,
    )


def _resolve_handoff_cutover_target(
    raw_id: str | None,
    session_id: str | None,
) -> tuple[int, dict[str, object]]:
    """Resolve the target worktree (or adopted anchor) for handoff cutover."""
    config = None
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
            try:
                config = cfg.load_config()
            except Exception as exc:
                return 2, {
                    "ok": False,
                    "error": (
                        "could not resolve a worktree or adopted anchor from "
                        "cwd; pass --worktree-id (or --session-id for a "
                        "bare-resumed worktree session). Project resolution "
                        f"failed: {exc}"
                    ),
                }
            if _cwd_is_inside_project(Path(config.default_repo.anchor)):
                wt_id = tracking.ANCHOR_ID
            else:
                return 2, {
                    "ok": False,
                    "error": (
                        "could not resolve a worktree or adopted anchor from "
                        "cwd; run from the intended checkout, pass "
                        "--worktree-id, or pass --session-id for a "
                        "bare-resumed worktree session"
                    ),
                }
    return 0, {
        "ok": True,
        "worktree_id": wt_id,
        "anchor_mode": wt_id == tracking.ANCHOR_ID,
        "config": config,
    }


def _handoff_cutover_spawn_result(
    args: argparse.Namespace,
) -> tuple[int, dict[str, object]]:
    """Return the spawn-mode ``handoff-cutover`` result without printing JSON."""
    seed = getattr(args, "seed", None)
    if not seed:
        return 1, {
            "ok": False, "error": "handoff-cutover requires --seed (or --retire-pane)",
        }
    raw_id = getattr(args, "worktree_id", None)
    session_id = getattr(args, "session_id", None)
    headless = bool(getattr(args, "headless", False))
    rc, resolved = _resolve_handoff_cutover_target(raw_id, session_id)
    if rc != 0:
        return rc, resolved
    wt_id = str(resolved.get("worktree_id") or "")
    config = resolved.get("config")

    # A live cutover needs a mux session to cut into. Without one, the caller
    # (extension) must fall back to the store-task-and-reply flow -- unless
    # --headless was requested, which explicitly opts out of mux entirely
    # (context-handoff-overhaul Phase 3 §4.3's non-mux launch primitive).
    anchor_mode = bool(resolved.get("anchor_mode"))
    if headless and anchor_mode:
        return 2, {
            "ok": False,
            "error": "handoff-cutover --headless does not support anchor-mode cutover",
        }

    mux_session = None
    record = None
    record_path = None
    if anchor_mode:
        if config is None:
            try:
                config = cfg.load_config()
            except Exception as exc:
                return 1, {"ok": False, "error": str(exc)}
        mux_session = sessions.current_mux_session(getattr(args, "old_pane", None))
        if not mux_session or not sessions.has_mux_session_named(mux_session):
            return 3, {
                "ok": False,
                "error": (
                    "adopted anchor is not inside a live mux session; the "
                    "stored handoff remains available for paste/resume"
                ),
            }
        work_dir = config.default_repo.anchor
    else:
        if not headless and not sessions.has_mux_session(wt_id):
            return 3, {
                "ok": False,
                "error": f"no mux session wt-{wt_id}; not under mux",
            }
        if config is None:
            try:
                config = cfg.load_config()
            except Exception as exc:
                return 1, {"ok": False, "error": str(exc)}
        record_path = cfg.tracking_dir() / f"{wt_id}.yaml"
        if not record_path.exists():
            return 1, {"ok": False, "error": f"Worktree not found: {wt_id}"}
        record = tracking.load_record(record_path)
        work_dir = record.worktree_path

    backend_error = _unsupported_hosted_launch(
        record,
        "handoff-cutover",
    )
    if backend_error:
        return 3, {"ok": False, "error": backend_error}

    launch_preflight = _preflight_launch(config, args, work_dir)
    if launch_preflight.error:
        return 3, {"ok": False, "error": launch_preflight.error}
    try:
        selection = _launch_profile_selection(
            config,
            args,
            record,
            lane="handoff-cutover",
            generation_key=f"handoff:{session_id or wt_id}",
            predecessor_session=session_id,
            allocate_new=not getattr(args, "dry_run", False),
        )
    except profile_assignment.ProfileAssignmentError as exc:
        return 3, {"ok": False, "error": str(exc)}
    if record is not None:
        _reflect_assignment(record, selection)
    predecessor_binding = None
    if session_id:
        predecessor_binding = (
            sessions.mux_binding_for_session(session_id, expected_session_name=mux_session,)
            if anchor_mode and mux_session
            else sessions.mux_binding_for_session(session_id)
        )
    expected_mux_session = mux_session if anchor_mode else sessions.mux_session_name(wt_id)
    expected_copilot_pid = getattr(args, "expected_copilot_pid", None)
    expected_copilot_start = getattr(args, "expected_copilot_start_time", None)
    if (
        predecessor_binding
        and (
            predecessor_binding.get("session_name") != expected_mux_session
            or (
                expected_copilot_pid is not None
                and predecessor_binding.get("copilot_pid") != expected_copilot_pid
            )
            or (
                expected_copilot_start is not None
                and str(predecessor_binding.get("copilot_start_time"))
                != str(expected_copilot_start)
            )
        )
    ):
        predecessor_binding = None
    predecessor_copilot_path = None
    predecessor_pid = None
    predecessor_start = None
    if predecessor_binding:
        predecessor_pid = predecessor_binding.get("copilot_pid")
        predecessor_start = predecessor_binding.get("copilot_start_time")
        if predecessor_pid and predecessor_start:
            predecessor_copilot_path = procs.copilot_relaunch_path(
                procs.process_executable_path(predecessor_pid)
            )
            if not predecessor_copilot_path or locks.process_start_time(predecessor_pid) != str(
                predecessor_start
            ):
                predecessor_copilot_path = None
    if predecessor_pid is None:
        predecessor_pid = expected_copilot_pid
    if predecessor_start is None:
        predecessor_start = expected_copilot_start
    launch_cmd = _build_launch_cmd(
        config,
        args,
        work_dir,
        profile=selection.profile,
        preflight=launch_preflight,
        fallback_copilot_path=predecessor_copilot_path,
    )
    env = _apply_assignment_env(
        _build_env(
            selection.profile,
            _repo_session_env(config, work_dir),
            work_dir=work_dir,
        ),
        selection,
    )
    handoff_token = getattr(args, "handoff_token", None)
    if handoff_token:
        env[_SESSION_HANDOFF_TOKEN] = handoff_token

    if headless:
        # No pane, no mux session, no seed-typing choreography: the seed is
        # passed as a native ``-i <seed>`` argument directly by
        # headless_new_session itself (headless has no pane-wrapper argv
        # mangling to route around).
        if getattr(args, "dry_run", False):
            dry_result: dict[str, object] = {
                "ok": True,
                "dry_run": True,
                "headless": True,
                "work_dir": work_dir,
                "cmd": [*launch_cmd, "-i", "<seed>"],
                "seed_len": len(seed),
            }
            if selection.assignment is not None:
                dry_result["profile_assignment"] = profile_assignment.metadata(
                    selection.assignment)
            return 0, dry_result

        spawn_event_ctx = {
            "worktree_id": wt_id, "session_id": session_id, "source": "python",
            "handoff_token": handoff_token, "old_pane": None,
            "method": "headless_new_session",
        }
        activity.log_event("handoff_successor_spawn_started", **spawn_event_ctx)
        result = sessions.headless_new_session(wt_id, work_dir, launch_cmd, env, seed=seed)
        if not result.get("ok"):
            failure = dict(result, ok=False)
            failure["error"] = f"failed to spawn headless successor: {result.get('error')}"
            activity.log_event(
                "handoff_successor_spawn_failed", error=result.get("error"), **spawn_event_ctx)
            return 4, failure
        pid = result.get("pid")
        activity.log_event(
            "handoff_cutover_spawn", new_pane=None, pid=pid,
            seeded=True, seed_ready=True,
            candidate_session=None, candidate_status="awaiting-session-association",
            predecessor_copilot_pid=predecessor_pid,
            predecessor_copilot_start_time=predecessor_start,
            **spawn_event_ctx,
        )
        candidate_session = None
        if handoff_token and record_path is not None:
            candidate_session, candidate_status = _wait_for_handoff_candidate(
                record_path, handoff_token, None,
            )
            if not candidate_session:
                return 4, {
                    "ok": False,
                    "headless": True,
                    "pid": pid,
                    "candidate_status": candidate_status,
                    "error": (
                        "successor did not create a token-associated Copilot "
                        f"session (status: {candidate_status})"
                    ),
                }
        response: dict[str, object] = {
            "ok": True,
            "headless": True,
            "pid": pid,
            "seed_len": len(seed),
            "seeded": True,
            "seed_ready": True,
            "seed_method": "interactive-argv",
        }
        if candidate_session:
            response["candidate_session"] = candidate_session
        if selection.assignment is not None:
            response["profile_assignment"] = profile_assignment.metadata(selection.assignment)
        return 0, response

    # Keep the multi-word seed OUT of the pane command argv: psmux space-joins
    # that argv before CreateProcess and irreversibly splits it. mux_new_window
    # sends base64 control tokens to the platform wrapper instead; the wrapper
    # decodes and appends native ``-i <seed>`` immediately before
    # launching the setup/Copilot command, after psmux has finished reconstructing
    # argv. A receipt handshake proves the wrapper performed that reconstruction
    # before this command reports success.

    # Capture the pane to retire (the operator's current Copilot) BEFORE opening
    # the new window, which becomes the active pane. ``--old-pane`` lets the
    # extension pin its own $TMUX_PANE explicitly.
    old_pane = (
        getattr(args, "old_pane", None)
        or (
            predecessor_binding.get("pane_id")
            if predecessor_binding
            else (
                sessions.mux_active_pane_named(mux_session)
                if anchor_mode and mux_session
                else sessions.mux_copilot_pane(wt_id, session_id)
            )
        )
        or (None if anchor_mode else sessions.mux_active_pane(wt_id))
    )

    if getattr(args, "dry_run", False):
        dry_result: dict[str, object] = {
            "ok": True,
            "dry_run": True,
            "session": mux_session or sessions.mux_session_name(wt_id),
            "old_pane": old_pane,
            "work_dir": work_dir,
            "cmd": list(launch_cmd),
            "seed_len": len(seed),
        }
        if selection.assignment is not None:
            dry_result["profile_assignment"] = profile_assignment.metadata(selection.assignment)
        return 0, dry_result

    spawn_event_ctx = {
        "worktree_id": wt_id, "session_id": session_id, "source": "python",
        "handoff_token": handoff_token, "old_pane": old_pane,
        "expected_mux_session": expected_mux_session,
        "method": "mux_new_window_interactive_argv",
    }
    def _spawn_failed(error: object) -> None:
        activity.log_event(
            "handoff_successor_spawn_failed", error=error, **spawn_event_ctx)
    activity.log_event("handoff_successor_spawn_started", **spawn_event_ctx)
    try:
        result = pane_lifecycle.pane_create(
            wt_id, work_dir, launch_cmd, env,
            initial_prompt=seed, session_name=mux_session)
    except Exception as exc:
        # mux_new_window guards only a known subset; an uncaught exception
        # must not leave the spawn stuck at "started".
        _spawn_failed(str(exc))
        raise
    if not result.get("ok"):
        failure = dict(result, ok=False)
        failure["error"] = f"failed to open successor window: {result.get('error')}"
        _spawn_failed(result.get("error"))
        return 4, failure
    new_pane = result.get("new_pane")
    activity.log_event(
        "handoff_cutover_spawn", new_pane=new_pane,
        seeded=bool(result.get("prompt_received")),
        seed_ready=bool(result.get("prompt_received")),
        candidate_session=None, candidate_status="awaiting-session-association",
        predecessor_copilot_pid=predecessor_pid,
        predecessor_copilot_start_time=predecessor_start,
        **spawn_event_ctx,
    )
    candidate_session = None
    if handoff_token and record_path is not None:
        candidate_session, candidate_status = _wait_for_handoff_candidate(
            record_path,
            handoff_token,
            new_pane,
            mux_session=expected_mux_session,
            predecessor_session_id=session_id,
        )
        if not candidate_session:
            failure = dict(result)
            failure.update(
                {
                    "ok": False,
                    "candidate_status": candidate_status,
                    "error": (
                        "successor did not create a token-associated Copilot "
                        f"session (status: {candidate_status})"
                    ),
                }
            )
            return 4, failure

    response: dict[str, object] = dict(result)
    response.update(
        {
            "ok": True,
            "session": mux_session or sessions.mux_session_name(wt_id),
            "old_pane": old_pane,
            "new_pane": new_pane,
            "seed_len": len(seed),
            "seeded": bool(result.get("prompt_received")),
            "seed_ready": bool(result.get("prompt_received")),
            "seed_method": "interactive-argv",
        }
    )
    if candidate_session:
        response["candidate_session"] = candidate_session
    if selection.assignment is not None:
        response["profile_assignment"] = profile_assignment.metadata(selection.assignment)
    return 0, response


def _latest_handoff_cutover_retry_handoff(
    record: tracking.WorktreeRecord,
) -> tracking.SessionHandoff | None:
    """Return the most recent non-cancelled handoff on ``record``."""
    handoffs = [
        handoff
        for handoff in getattr(record, "handoffs", []) or []
        if getattr(handoff, "state", None) != "cancelled"
    ]
    if not handoffs:
        return None
    return max(handoffs, key=lambda handoff: getattr(handoff, "ordinal", 0))


def _handoff_cutover_retry_result(
    args: argparse.Namespace,
) -> tuple[int, dict[str, object]]:
    """Retry a stuck cutover without duplicating a live successor."""
    session_id = getattr(args, "session_id", None)
    rc, resolved = _resolve_handoff_cutover_target(
        getattr(args, "worktree_id", None),
        session_id,
    )
    if rc != 0:
        return rc, resolved
    wt_id = str(resolved.get("worktree_id") or "")
    if wt_id == tracking.ANCHOR_ID:
        return 3, {
            "ok": False,
            "error": "handoff-cutover --retry only supports tracked worktrees",
        }
    record_path = cfg.tracking_dir() / f"{wt_id}.yaml"
    if not record_path.exists():
        return 1, {"ok": False, "error": f"Worktree not found: {wt_id}"}
    record = tracking.load_record(record_path)
    handoff = _latest_handoff_cutover_retry_handoff(record)
    if handoff is None:
        return 1, {
            "ok": False,
            "error": f"worktree {wt_id} has no recorded handoff to retry",
        }
    predecessor_session = str(getattr(handoff, "predecessor", "") or "").strip() or None
    if session_id and predecessor_session and session_id != predecessor_session:
        return 2, {
            "ok": False,
            "error": (
                "handoff-cutover --retry must be invoked from the most recent "
                f"handoff predecessor ({predecessor_session}), not {session_id}"
            ),
        }
    handoff_token = str(getattr(handoff, "token", "") or "").strip() or None
    explicit_token = str(getattr(args, "handoff_token", "") or "").strip() or None
    if explicit_token and explicit_token != handoff_token:
        return 2, {
            "ok": False,
            "error": (
                f"handoff-cutover --retry targets the most recent handoff "
                f"token {handoff_token}, not {explicit_token}"
            ),
        }
    head_session = record.resolved_head_session
    recorded_successor = str(getattr(handoff, "successor", "") or "").strip() or None
    if recorded_successor and head_session not in {None, recorded_successor}:
        return 2, {
            "ok": False,
            "error": (
                "the most recent handoff's recorded successor disagrees with "
                f"the resolved worktree head ({head_session}); retry refused"
            ),
        }
    live_session = recorded_successor or (
        str(getattr(handoff, "candidate", "") or "").strip() or None
    )
    expected_mux_session = sessions.mux_session_name(wt_id)
    mux_bin = sessions._mux_bin()
    if live_session:
        try:
            binding = sessions.mux_binding_for_session(
                live_session,
                expected_session_name=expected_mux_session,
            )
        except Exception:
            binding = None
        pane_id = str((binding or {}).get("pane_id") or "").strip() or None
        if (
            binding
            and str(binding.get("session_name") or "").strip() == expected_mux_session
            and pane_id
            and sessions._mux_pane_alive(pane_id, mux_bin)
        ):
            response = {
                "ok": True,
                "outcome": "refocused",
                "worktree_id": wt_id,
                "handoff_token": handoff_token,
                "handoff_state": (
                    "linked" if recorded_successor else getattr(handoff, "state", None)
                ),
                "session": expected_mux_session,
                "successor_session": live_session,
                "successor_pane": pane_id,
            }
            if getattr(args, "dry_run", False):
                response["dry_run"] = True
                return 0, response
            if sessions.mux_focus_pane(expected_mux_session, pane_id):
                return 0, response
            response.update(
                {
                    "ok": False,
                    "error": (
                        "a live successor pane already exists, but its mux "
                        "window could not be focused"
                    ),
                }
            )
            return 5, response
    request = _monitor_read_session_state_handoff(
        _monitor_session_state_handoff_path(predecessor_session)
    )
    seed = str(getattr(args, "seed", "") or "").strip() or None
    request_token = str((request or {}).get("handoffId") or "").strip() or None
    if request_token in {None, handoff_token}:
        if recorded_successor:
            prompt_text = str((request or {}).get("promptText") or "").strip()
            if prompt_text:
                seed = prompt_text
        if not seed:
            request_seed = str((request or {}).get("seed") or "").strip()
            if request_seed:
                seed = request_seed
    spawn_args = argparse.Namespace(**vars(args))
    spawn_args.worktree_id = wt_id
    spawn_args.session_id = predecessor_session or session_id
    spawn_args.handoff_token = None if recorded_successor else handoff_token
    spawn_args.seed = seed
    rc, response = _handoff_cutover_spawn_result(spawn_args)
    response.setdefault("worktree_id", wt_id)
    response.setdefault("handoff_token", handoff_token)
    if rc == 0 and response.get("ok"):
        response["outcome"] = "spawned"
    return rc, response


# A losing Stage-13 claimant's brief retry window (see _maybe_emit_stage_13):
# short enough to add negligible latency to a hook/monitor call, long enough
# for the winner's own log_event() + failure-detection + rollback to settle.
_STAGE13_CLAIM_RETRIES = 3
_STAGE13_CLAIM_RETRY_DELAY_S = 0.05


def _maybe_emit_stage_13(
    wt_id: str | None, handoff_token: str | None, *, launch_id: str | None = None,
) -> None:
    """Emit Stage 13 (handoff_complete) once the predecessor pane is gone AND
    the successor is head (linked, not just candidate) -- called from both
    the retire and claim sides since either can complete first. Dedup is an
    atomic exclusive-create claim file keyed by a collision-resistant digest
    of the exact (worktree, token) pair (NOT the lossy character-sanitized
    ``_monitor_handoff_claim_segment()``, which can map distinct tokens like
    "task:1" and "task_1" onto the same on-disk name): the successor's
    sessionStart process and the resident monitor's retire process run
    concurrently, so a plain read_events() check-then-act could let both
    sides pass before either logs, double-recording Stage 13. If the log
    write is detected to have failed (via
    ``activity.log_event_failure_count()``), the winner rolls its claim back
    so the failure stays retryable -- but that rollback can race a losing
    caller's own FileExistsError check, so a loser doesn't just give up: it
    waits briefly, and if the claim vanished (a rollback), retries the claim
    itself instead of silently losing Stage 13 forever. A hard process crash
    between the claim and the log write is a narrower residual gap left to
    Phase 3's durable trace store.
    """
    if not wt_id or not handoff_token:
        return
    retired = {
        str(e.get("handoff_token") or "").strip()
        for e in activity.read_events(worktree_id=wt_id, event="handoff_predecessor_retire")
        if e.get("outcome") == "gone"
    }
    if handoff_token not in retired:
        return
    try:
        record = tracking.load_record(cfg.tracking_dir() / f"{wt_id}.yaml")
    except Exception:
        return
    handoff = next((h for h in record.handoffs if h.token == handoff_token), None)
    if handoff is None or handoff.state != "linked":
        return
    digest = hashlib.sha256(f"{wt_id}\x00{handoff_token}".encode()).hexdigest()
    claim_path = _monitor_handoff_claim_root() / "stage13" / f"{digest}.json"
    try:
        claim_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return  # unwritable/malformed claim root -- fail open like the claim itself
    claimed = False
    for _attempt in range(_STAGE13_CLAIM_RETRIES):
        try:
            with open(claim_path, "x", encoding="utf-8"):
                pass
        except FileExistsError:
            # A losing caller must not just give up: if the current holder's
            # log write fails and rolls back the claim (below), that rollback
            # can happen between our check and its own return, and no other
            # caller may ever retry -- silently losing Stage 13 forever. Give
            # the holder a brief window to either finish or roll back, then
            # retry the claim ourselves if it did.
            time.sleep(_STAGE13_CLAIM_RETRY_DELAY_S)
            if claim_path.exists():
                return  # still held -- its owner is genuinely in flight
            continue
        except OSError:
            return  # unwritable claim dir -- no-op
        claimed = True
        break
    if not claimed:
        return
    failures_before = activity.log_event_failure_count()
    activity.log_event(
        "handoff_complete", worktree_id=wt_id, session_id=handoff.predecessor,
        successor_session_id=handoff.successor, handoff_token=handoff_token,
        launch_id=launch_id)
    if activity.log_event_failure_count() > failures_before:
        with contextlib.suppress(OSError):
            claim_path.unlink()


def _settle_predecessor_session_claim(wt_id: str | None, session_id: str) -> None:
    """Settle a confirmed-retired predecessor's ``session`` claim to at-rest.

    Runs for *every* confirmed retire (bare or token-bearing), unlike
    :func:`_conclude_retired_predecessor` which only concludes the
    ``SessionEntry`` on a bare retire -- a token-bearing retire's own
    ``SessionEntry`` transition is ``link_handoff``'s job, but nothing else
    settles the predecessor's Phase 8 ``session`` resource claim once its
    pane and Copilot process are positively confirmed gone. Settles (not
    releases) since ``settle_resource_claim`` never resurrects an
    already-``released`` claim (mirrors ``finalize.py``'s
    ``_settle_current_session_claim`` guard) -- a ``deregister_session`` that
    raced ahead and released the claim first is left alone. Best-effort:
    unknown worktree/session or a missing claim is a silent no-op, never a
    hard failure of the retire itself.
    """
    if not wt_id or not session_id:
        return
    with contextlib.suppress(Exception):
        yaml_path = tracking._owning_tracking_dir(wt_id) / f"{wt_id}.yaml"
        if not yaml_path.exists():
            return
        with tracking._RecordLock(yaml_path):
            record = tracking.load_record(yaml_path)
            predecessor_ref = tracking.format_claim_ref(
                record.machine, record.repo, record.worktree_id, session=session_id,
            )
            existing_claim = next(
                (c for c in record.resources if c.ref == predecessor_ref), None,
            )
            if existing_claim is None or existing_claim.state == "released":
                return
            tracking.settle_resource_claim(
                record, predecessor_ref, disposition=obligations.AT_REST, save=False,
            )
            tracking.save_record(record, yaml_path)


def _conclude_retired_predecessor(wt_id: str | None, session_id: str) -> None:
    """Mark a retire-confirmed predecessor's ``SessionEntry`` concluded.

    A confirmed ``--retire-pane`` (pane gone AND its Copilot process
    positively verified dead by pid) is a deliberate, verified act -- not
    liveness inference -- so it is safe to assert conclusion here, same as
    ``conclude-session``'s own contract. Without this, a session whose
    process died via a bare self-retire (no handoff-token successor link)
    leaves its ``SessionEntry.state`` stuck at ``"active"`` forever, and
    ``register_session``'s creation-guard then refuses to ever promote a
    later successor to head -- a zombie head pointer.

    Deliberately does NOT attempt to promote any other session found on the
    record: ``conclude_session`` (and this repair, which shares its
    contract) never infers a successor from list membership -- that's the
    same invariant a plain ``conclude-session`` upholds ("another active
    session is never promoted by list or timestamp order; a successor or
    adopter must assert the next transition explicitly"). A session that
    registered while this predecessor was still active is not necessarily
    its successor -- it could be an unrelated parallel session -- so
    guessing from "exactly one other active entry" would misattribute
    succession exactly as easily as it would complete a deferred one. A
    subsequent registration (any future hook/bind call) still correctly
    claims the now-cleared head via ``register_session``'s own ordinary
    path; closing the narrower race window where an already-registered
    session is never re-triggered needs an explicit adoption/relationship
    marker, which is a separate, larger change than this repair's scope.
    Best-effort: unknown worktree/session or an already-concluded entry is a
    silent no-op, never a hard failure of the retire itself.
    """
    if not wt_id or not session_id:
        return
    yaml_path = tracking._owning_tracking_dir(wt_id) / f"{wt_id}.yaml"
    if not yaml_path.exists():
        return
    with contextlib.suppress(Exception), tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        entry = record.session_entry(session_id)
        if entry is None or entry.state != "active":
            return
        tracking.conclude_session(record, session_id, state="concluded", save=False)
        tracking.save_record(record, yaml_path)


def _resolve_retire_pane_mux_session(
    retire_pane: str | None,
    expected_mux: str | None,
) -> str | None:
    """Return the mux session that actually owns ``retire_pane``, safely.

    ``sessions.current_mux_session()``/``mux_session_for_pane()`` runs a
    *bare* ``display-message -t <pane_id>`` -- unqualified by session. Live
    validation of a real ``handoff-cutover --retire-pane`` invocation from
    outside the target pane's own session (e.g. an orchestrator, not a
    predecessor self-retiring) showed psmux resolving that bare target to
    the *caller's own current/default session* rather than the session that
    genuinely contains ``retire_pane``, even with no real pane-id collision
    -- producing a false ``identity-mismatch-skip`` that silently no-ops a
    legitimate retire. Mirror the already-safe pattern used by
    ``pane_terminate``/``mux_focus_pane`` (#2890/#2892/#2896): check
    membership in the expected session first via a session-scoped
    ``list-panes -a`` filter, and only fall back to an unscoped lookup (also
    ``list-panes -a``-based, never a bare ``display-message``) when the pane
    isn't there.
    """
    if not expected_mux or not retire_pane:
        return None
    mux_bin = sessions_pane_retire._mux_bin()
    if sessions_pane_retire._list_matching_pane_targets(
        retire_pane, mux_bin, session_name=expected_mux,
    ):
        return expected_mux
    try:
        return sessions_pane_retire._resolve_unambiguous_pane_session(retire_pane, mux_bin)
    except sessions_pane_retire.MuxPaneTargetAmbiguityError:
        return None


def _handoff_cutover_retire_result(
    args: argparse.Namespace,
) -> tuple[int, dict[str, object]]:
    """Return the retire-mode ``handoff-cutover`` result without printing JSON."""
    retire_pane = getattr(args, "retire_pane", None)
    raw_id = getattr(args, "worktree_id", None)
    wt_id = _resolve_worktree_id(raw_id) if raw_id else None
    session_id = getattr(args, "session_id", None)
    expected_mux = getattr(args, "mux_session", None)
    require_mux_identity = bool(getattr(args, "require_mux_identity", False))
    expected_copilot_pid = getattr(args, "expected_copilot_pid", None)
    expected_copilot_start = getattr(args, "expected_copilot_start_time", None)
    strict_process_identity = (
        expected_copilot_pid is not None or expected_copilot_start is not None
    )
    current_mux = _resolve_retire_pane_mux_session(retire_pane, expected_mux)
    pane_not_in_expected_mux = bool(expected_mux and current_mux != expected_mux)
    binding = (
        sessions.mux_binding_for_session(session_id)
        if (strict_process_identity and session_id and not pane_not_in_expected_mux)
        else None
    )
    process_identity_ok = True
    process_identity_reason = None
    if strict_process_identity and not pane_not_in_expected_mux:
        if expected_copilot_pid is None or not expected_copilot_start or binding is None:
            process_identity_ok = False
            process_identity_reason = "process-identity-unavailable"
        elif (
            binding.get("pane_id") != retire_pane
            or binding.get("copilot_pid") != expected_copilot_pid
            or str(binding.get("copilot_start_time")) != str(expected_copilot_start)
        ):
            process_identity_ok = False
            process_identity_reason = "process-identity-mismatch"
    if not process_identity_ok:
        result = {
            "ok": False,
            "pane": retire_pane,
            "gone": False,
            "method": process_identity_reason,
            "expected_copilot_pid": expected_copilot_pid,
            "expected_copilot_start_time": expected_copilot_start,
        }
    elif require_mux_identity and not expected_mux:
        result = {
            "ok": bool(session_id),
            "pane": retire_pane,
            "gone": True,
            "method": "identity-unavailable-skip",
            "expected_mux_session": None,
            "current_mux_session": None,
        }
    elif expected_mux and current_mux != expected_mux:
        result = {
            "ok": bool(session_id),
            "pane": retire_pane,
            "gone": True,
            "method": (
                "identity-mismatch-skip" if current_mux else "identity-unresolved-skip"
            ),
            "expected_mux_session": expected_mux,
            "current_mux_session": current_mux,
        }
    else:
        result = pane_lifecycle.pane_terminate(
            retire_pane,
            mux_session=expected_mux,
        )
    reap = {"checked": False}
    identity_skip = result.get("method") in {
        "process-identity-unavailable", "process-identity-mismatch",
    }
    if session_id and result.get("method") != "last-window-skip" and not identity_skip:
        if strict_process_identity:
            reap = reclaim.ensure_session_copilot_reaped(
                session_id,
                expected_pid=expected_copilot_pid,
                expected_start_time=expected_copilot_start,
            )
        else:
            reap = reclaim.ensure_session_copilot_reaped(session_id)
        result["copilot"] = reap
    proc_ok = ((not reap.get("checked")) or reap.get("survivors", 0) == 0) and reap.get(
        "identity_verified", True
    )
    skipped_identity = result.get("method") == "identity-mismatch-skip"
    overall_ok = bool(result.get("ok")) and proc_ok
    result["ok"] = overall_ok
    # A pane is only genuinely, positively confirmed retired when the real
    # mux retire ran and reported it gone -- never for a synthetic
    # "-skip" result (last-window guard, unresolved/mismatched mux identity,
    # unverifiable process identity), each of which reports success without
    # ever having signaled the pane. Concluding the predecessor on one of
    # those would mark a still-running session as "concluded".
    pane_confirmed_retired = bool(result.get("gone")) and result.get("method") not in {
        "process-identity-unavailable", "process-identity-mismatch",
        "identity-unavailable-skip", "identity-mismatch-skip",
        "identity-unresolved-skip", "last-window-skip",
    }
    # Only repair a BARE retire (no handoff token at all) here. A token-bearing
    # retire belongs to the ordinary handoff flow, where the successor's own
    # `register_session(..., handoff_token=...)` -> `link_handoff` concludes
    # the predecessor once it actually claims the token -- including the
    # legitimate case where a candidate has been associated
    # (`associate_handoff_candidate`) but not yet acknowledged: the
    # predecessor is still `active` and its resident monitor may still retire
    # it before that late acknowledgement lands. Concluding it here would
    # make that acknowledgement fail (`link_handoff` refuses an explicitly
    # concluded predecessor).
    bare_retire = not getattr(args, "handoff_token", None)
    if overall_ok and pane_confirmed_retired and session_id:
        _settle_predecessor_session_claim(wt_id, session_id)
    if overall_ok and pane_confirmed_retired and bare_retire and session_id:
        _conclude_retired_predecessor(wt_id, session_id)
    activity.log_event(
        "handoff_predecessor_retire",
        worktree_id=wt_id,
        session_id=session_id,
        source="python",
        handoff_token=getattr(args, "handoff_token", None),
        successor_session_id=getattr(args, "successor_session_id", None),
        old_pane=retire_pane,
        successor_verified=bool(getattr(args, "successor_verified", False)),
        reason=getattr(args, "retire_reason", None),
        method=result.get("method"),
        outcome=(
            "identity-mismatch"
            if skipped_identity
            else "gone"
            if (result.get("gone") and proc_ok)
            else "left-running"
        ),
        copilot_found=reap.get("found", 0),
        copilot_reaped=reap.get("reaped", 0),
        copilot_survivors=reap.get("survivors", 0),
    )
    if getattr(args, "handoff_token", None):
        from . import handoff_diagnostics

        handoff_diagnostics.stamp_session_state_handoff(
            session_id,
            handoff_token=getattr(args, "handoff_token", None),
            successor_session_id=getattr(args, "successor_session_id", None),
        )
    _maybe_emit_stage_13(
        wt_id, getattr(args, "handoff_token", None),
        launch_id=getattr(args, "launch_id", None) or os.environ.get("WORKTREE_LAUNCH_ID"))
    return (0 if overall_ok else 1), result


def cmd_copilot(args: argparse.Namespace) -> int:
    """Deliver a TTY Copilot session to the user in THIS terminal.

    The canonical "___ copilot" verb: ensure a durable, mux-wrapped Copilot
    session exists for the target worktree (identical create-or-resume
    semantics to `embody`), then hand this process's own controlling
    terminal over to it -- this process becomes the mux client, replacing
    itself via exec so there is no wrapper left holding the TTY. Unlike
    `embody` (always detached, JSON out, for a programmatic caller), this is
    the human/TTY-facing counterpart and refuses without one.

    `agent-codespaces`/`agent-containers` implement the same verb
    (`copilot <name>`) for a remote venue by SSH `-t`'ing in and running
    this exact command there -- one name, one meaning, everywhere: deliver a
    TTY Copilot session in the current terminal. The amount of setup needed
    before that's possible (none locally; venue prep + reverse-forwards
    remotely) is the only thing that differs.
    """
    if not sys.stdin.isatty():
        output.err(
            "`copilot` needs a controlling terminal to attach to -- for a "
            "programmatic/detached launch use `embody` instead."
        )
        return 2

    # Reuse embody's full target-resolution/preflight/create-or-resume logic
    # in-process rather than duplicating it -- only its JSON result is wanted
    # here, not its stdout (which is about to become the mux client's TTY).
    # `embody`'s JSON goes through `_json_output`, which deliberately writes
    # to `sys.__stdout__` (not `sys.stdout`) so it still reaches the real
    # terminal from inside `output.stdout_to_stderr` -- a plain
    # `contextlib.redirect_stdout` (which only swaps `sys.stdout`) never
    # captures it, so `buf.getvalue()` came back empty every single call
    # (confirmed live, agent-bridge-cli-mode-sessions Phase 4 validation:
    # 100% reproducible both locally and over a remote venue SSH session --
    # `copilot` never actually attached to the mux session it had just
    # created). `output.capture_json_output()` swaps `sys.__stdout__` itself,
    # the level `_json_output` actually writes to.
    with output.capture_json_output() as buf:
        rc = cmd_embody(args)
    if rc != 0:
        # embody already wrote its JSON error to buf; surface it for a human
        # on stderr and exit the same way embody would have.
        sys.stderr.write(buf.getvalue())
        return rc
    try:
        result = json.loads(buf.getvalue())
    except (ValueError, TypeError):
        output.err("copilot: could not parse the embodiment result")
        return 1

    session_name = result.get("session")
    if not session_name:
        output.err("copilot: embodiment result had no session name")
        return 1

    mux_bin = sessions._mux_bin(getattr(args, "mux", None))
    argv = [mux_bin, "attach-session", "-t", session_name]
    try:
        os.execvp(mux_bin, argv)  # never returns on success
    except OSError as exc:
        output.err(f"copilot: could not attach to {session_name!r}: {exc}")
        return 1
    return 0  # pragma: no cover -- unreachable after a successful execvp


def cmd_resolve(args: argparse.Namespace) -> int:
    """Resolve a launch plan and emit it as JSON."""
    return resolve_cli.cmd_resolve(args)


# resolve launch helpers are componentized into resolve_cli.py,
# resolve_launch_cli.py, resolve_machine_cli.py, resolve_picker_cli.py,
# and resolve_system_cli.py.

def _infer_worktree_id(
    explicit: str | None,
    config: cfg.Config | None = None,
) -> str | None:
    """Return the worktree ID from an explicit arg or the current directory.

    Resolution order:
      1. Explicit value passed on the CLI
      2. The current working directory under the configured ``worktree_root``

    Identity is resolved **purely from the directory**, the way git resolves
    its repo. The ambient ``$WORKTREE_ID`` is **not**
    consulted -- it was the source of cross-session/cross-repo contamination.
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


# Worktree-identity resolution (git-dir-based ID inference, short-suffix
# resolution, external-worktree adoption) moved to worktree_identity.py --
# no dependency on this entry-point module, so sibling CLI modules (pr_cli.py
# etc.) can import it directly instead of reverse-importing __main__.
from .worktree_identity import (  # noqa: E402 -- re-export position matches original definition site
    _adopt_linked_worktree,  # noqa: F401 -- session_binding_cli accesses via __main__
    _infer_worktree_id_from_cwd,
    _infer_worktree_id_from_worktree_root,  # noqa: F401 -- re-exported for unit tests
    _resolve_worktree_id,
    _worktree_id_from_git,
    _worktree_path_for_id,  # noqa: F401 -- re-exported for context_cli
    resolve_worktree_id_by_codename,  # noqa: F401 -- re-exported for session_metadata_cli
)

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
            output.ok(f"Reaped {len(reaped)} idle orphan mux session(s): {', '.join(reaped)}")
    except Exception:
        pass
    _sweep_managed_on_exit()
    _sweep_launcher_shells_on_exit()
    _sweep_finished_sessions_on_cadence()


def _sweep_finished_sessions_on_cadence() -> None:
    """Best-effort auto-clean of FINISHED session worktrees at a lifecycle
    boundary (the no-daemon prune-on-next-start cadence).

    Extends the same picker-launch + session-end cadence as the mux/managed/
    launcher sweeps to the ordinary (non-managed) worktrees the manual
    ``cleanup`` would remove -- ``finalized`` / merged / git-COMPLETED ones idle
    past the grace window -- so finished user worktrees self-heal without a
    background service (the ~hundreds-of-stale-worktrees accumulation). Reuses
    :func:`sweep_finished_session_worktrees`, which mirrors the manual cleanup's
    conservative safety exactly. Gated by the :func:`auto_clean_enabled`
    kill-switch and wrapped best-effort; never raises.
    """
    if not auto_clean_enabled():
        return
    try:
        report = sweep_finished_session_worktrees()
        removed = report.get("removed") or []
        if removed:
            output.ok(
                f"Auto-cleaned {len(removed)} finished worktree(s): "
                + ", ".join(x["id"] for x in removed)
            )
    except Exception:
        pass


def _write_session_lifecycle_receipt(payload: dict, state: str) -> None:
    version, _environment = _session_lifecycle_metadata(payload)
    launch_key = _session_lifecycle_launch_key(payload, version)
    if not launch_key:
        return
    root = _aw_runtime_home() / ".session-context"
    target = root / f"lifecycle-{launch_key}.json"
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        root.mkdir(parents=True, exist_ok=True)
        prune_before = time.time() - 60 * 60
        for candidate in root.glob("lifecycle-*.json"):
            try:
                if candidate.stat().st_mtime < prune_before:
                    candidate.unlink()
            except OSError:
                pass
        temporary.write_text(
            json.dumps(
                {"launchKey": launch_key, "state": state},
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass


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


# ═══════════════════════════════════════════════════════════════════════════
# status
# ═══════════════════════════════════════════════════════════════════════════


def _cmd_status_write(
    args: argparse.Namespace,
    *,
    summary: str | None,
    title: str | None = None,
    follow_up: bool | None = None,
) -> int:
    """Write mode of `status`: annotate THIS worktree's agent-asserted
    disposition (summary / title / follow-up). Resolves the worktree from CWD (or
    --worktree-id). Orthogonal to git/session state; see the worktree-status-core
    effort and the agent-fabric vision (disposition-is-asserted-pulse-is-derived).
    """
    config = cfg.load_config()
    worktree_id = _infer_worktree_id(getattr(args, "worktree_id", None), config)
    if not worktree_id:
        output.err(
            "Could not determine worktree ID. Run from inside a worktree or pass --worktree-id."
        )
        return 1
    worktree_id = _resolve_worktree_id(worktree_id)
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        output.err(f"Tracking file not found at {yaml_path}. Cannot annotate an unknown worktree.")
        return 1
    # pr-attribution-codenames Phase 2 (#2838): an explicit status touch is
    # exactly the "first touch" the backfill plan describes -- lazily assign
    # a pre-Phase-2 record's missing codename. Done as its OWN independent
    # locked step, strictly before the disposition RMW below, so the
    # project-wide allocation lock and the per-record lock are never held
    # nested/simultaneously in either order (a lock-ordering/deadlock hazard
    # flagged in review: `create` acquires them allocation-lock-then-record-
    # lock via `create_new_record`'s own `save_record`).
    peek = tracking.load_record(yaml_path)
    if not peek.codename:
        try:
            codename_tracking.ensure_codename(
                peek, cfg.tracking_dir(), codename_tracking.wordlist_for_repo(config),
                **codename_tracking.allocation_policy_kwargs_for_repo(config),
            )
        except codename_tracking.CodenameAttributionPolicyError as e:
            # Round-5 review finding: this backfill can raise a policy
            # rejection -- report it cleanly instead of a raw traceback
            # (this command has no --json envelope of its own to
            # preserve, but a plain traceback is still a defect).
            output.err(str(e))
            return 1
    # Foreground verb (#4547): the whole load -> set_disposition -> save is a
    # critical RMW held under the blocking record lock, so a concurrent Picker
    # best-effort sweep skips rather than clobbering the disposition overlay.
    with tracking._RecordLock(yaml_path):
        record = tracking.load_record(yaml_path)
        if record.kind in tracking.MANAGED_KINDS and record.status in {
            "complete", "completed", "finalized",
        }:
            output.err(
                f"Worktree {worktree_id} is terminal and managed; refusing disposition changes."
            )
            return 1
        if follow_up is False and record.active_effort is not None:
            output.err(
                "Cannot resolve this worktree while an effort remains bound. "
                "Complete, transfer, or replace it with 'effort-focus'."
            )
            return 1
        if follow_up is True and record.status == "finalized":
            tracking.update_status(record, "active", save=False)
        session_id = os.environ.get("COPILOT_AGENT_SESSION_ID") or None
        tracking.set_disposition(
            record,
            summary=summary,
            title=title,
            follow_up=follow_up,
            session_id=session_id,
            save=False,
        )
        tracking.save_record(record)
        # Stage 5 (status_reported): once per session_id (held under the
        # same RecordLock as the write above so two concurrent writers can't
        # both observe "no prior event" and double-emit). No `limit` here --
        # the log is already retention-pruned, and a `limit` would silently
        # drop an older matching event once a worktree accumulates enough
        # newer ones, defeating the once-per-session guarantee.
        if session_id and not any(
            e.get("session_id") == session_id
            for e in activity.read_events(worktree_id=worktree_id, event="status_reported")
        ):
            activity.log_event("status_reported", worktree_id=worktree_id, session_id=session_id)
    flag = "follow-ups pending" if record.follow_up else "resolved"
    msg = f"[OK] Worktree {worktree_id[-4:]} disposition: {flag}"
    if title is not None and record.title:
        msg += f" -- title: {record.title}"
    if record.summary:
        msg += f" -- {record.summary}"
    print(msg)
    return 0


def _cmd_status_history(args: argparse.Namespace) -> int:
    """Read mode of `status --history`: print THIS worktree's durable disposition
    trajectory (summary / title / follow-up over time), newest last. Resolves the
    worktree from CWD (or --worktree-id). Honors --json / --limit."""
    config = cfg.load_config()
    worktree_id = _infer_worktree_id(getattr(args, "worktree_id", None), config)
    if not worktree_id:
        output.err(
            "Could not determine worktree ID. Run from inside a worktree or pass --worktree-id."
        )
        return 1
    worktree_id = _resolve_worktree_id(worktree_id)
    limit = getattr(args, "limit", None)
    entries = disposition_history.read(worktree_id, limit=limit)
    if getattr(args, "json", False):
        _json_output({"worktree_id": worktree_id, "history": entries})
        return 0
    if not entries:
        print(f"No disposition history for {worktree_id[-4:]}.")
        return 0
    print(
        f"Disposition history for {worktree_id[-4:]} "
        f"({len(entries)} entr{'y' if len(entries) == 1 else 'ies'}):"
    )
    for e in entries:
        at = e.get("at") or "?"
        changed = ",".join(e.get("changed") or []) or "-"
        flag = "!" if e.get("follow_up") else " "
        kind = e.get("kind") or "status"
        sess = e.get("session")
        sess_tag = f" {sess[-6:]}" if isinstance(sess, str) and sess else ""
        title = e.get("title")
        summary = e.get("summary") or ""
        head = f"  {at} [{flag}] {kind}{sess_tag} ({changed})"
        if title:
            head += f" title: {title}"
        print(head)
        if summary:
            print(f"        {summary}")
    return 0


# session/history/effort metadata surfaces are componentized into session_metadata_cli.py.


# status-family subcommand surfaces are componentized into status_cli.py,
# status_bar_cli.py, status_updater_cli.py, and status_monitor_runtime.py.

# status-updater / status-monitor runtime helpers moved to status_updater_cli.py
# and status_monitor_runtime.py.

_RETIRE_TERMINAL_FAILURE_METHODS = frozenset(
    {
        "process-identity-unavailable",
        "process-identity-mismatch",
        "identity-mismatch-skip",
        "identity-unresolved-skip",
        "last-window-skip",
    }
)
_RETIRE_ABANDON_GRACE_S = 600

def _pending_handoff_retire_requests(
    record: tracking.WorktreeRecord,
) -> list[dict[str, object]]:
    """Every consumed/associated handoff whose predecessor still needs
    retirement, oldest first -- the shared core both
    :func:`_monitor_pending_handoff_predecessor_retire` (the daemon's
    one-at-a-time sweep) and ``handoffs-check`` (which retires every stale
    predecessor on a worktree in one pass, not just the first) build on.

    Considers every non-``cancelled`` handoff on the record (``pending`` --
    a candidate is associated but not yet linked -- and ``linked``, i.e.
    fully confirmed), not just ``record.pending_handoffs``. A handoff
    reaches ``linked`` well before its predecessor is actually retired (that
    is a separate, later step), so restricting this to the still-``pending``
    subset made a predecessor whose retire failed hours ago -- the daemon's
    own sweep had already long since moved past that handoff to newer ones
    -- permanently invisible to both the automatic sweep and this on-demand
    check, even though the mux pane was still sitting there alive.

    A token whose retire attempts are ALL unrecoverable-class failures
    (`_RETIRE_TERMINAL_FAILURE_METHODS`) for at least
    `_RETIRE_ABANDON_GRACE_S` is concluded ("abandoned") rather than
    surfaced again -- see the abandonment pass below. Before this fix, no
    terminal condition existed at all: a predecessor whose own process had
    already exited (so identity could never be proven again) was retried
    every sweep forever, confirmed in production as thousands of no-op
    retries spanning days across several worktrees.
    """
    worktree_id = getattr(record, "worktree_id", None)
    if not worktree_id:
        return []
    # The rolling activity.jsonl log is bounded (age + a 64-event read cap);
    # a worktree with more than 64 later cutovers -- or one revisited well
    # past the log's retention window -- can silently drop an older
    # handoff's spawn/retire evidence right out of view. Both
    # "handoff_cutover_spawn" and "handoff_predecessor_retire" are
    # stage-mapped (HANDOFF_STAGE_MAP stages 8 and 11), so they also land in
    # the durable, unrotated per-project trace store (handoff_trace.py,
    # Phase 3) -- merge it in as the completeness backstop the bounded log
    # can't be.
    trace_events: list[dict[str, object]] = []
    project = cfg.active_project()
    if project:
        try:
            trace_events = handoff_trace.read_trace(project, worktree_id)
        except Exception:
            trace_events = []
    spawn_events = [e for e in trace_events if e.get("event") == "handoff_cutover_spawn"]
    spawn_events += activity.read_events(
        worktree_id=worktree_id, event="handoff_cutover_spawn", limit=64,
    )
    spawned = {
        str(event.get("handoff_token") or "").strip(): event
        for event in spawn_events
        if str(event.get("handoff_token") or "").strip()
    }
    retire_events = [e for e in trace_events if e.get("event") == "handoff_predecessor_retire"]
    retire_events += activity.read_events(
        worktree_id=worktree_id, event="handoff_predecessor_retire", limit=64,
    )
    retired = {
        str(event.get("handoff_token") or "").strip()
        for event in retire_events
        # A genuinely successful retirement ("gone") or a confirmed-
        # unrecoverable one ("abandoned", see below) both count as
        # "handled" -- matching Stage 13's own success gate (see
        # _maybe_emit_stage_13). A merely-failed attempt (e.g.
        # "left-running" on a stale/incorrect identity hint) must stay
        # retryable, not be permanently abandoned on its own.
        if event.get("outcome") in ("gone", "abandoned")
        and str(event.get("handoff_token") or "").strip()
    }
    # A predecessor whose retire attempts have ALL failed the same
    # unrecoverable way for a sustained period is never coming back --
    # most commonly because the predecessor's own Copilot process already
    # exited on its own days ago, so mux_binding_for_session can never find
    # a live lock to prove identity against again. Retrying that forever
    # (previously: no terminal condition existed at all) wastes every sweep
    # indefinitely and leaves a stale request permanently contesting a pane
    # a much later, unrelated session has since legitimately reused --
    # confirmed in production as thousands of no-op retries across several
    # worktrees, one running 3+ days straight. `_RETIRE_TERMINAL_FAILURE_
    # METHODS` are the outcomes that can never self-resolve by "the
    # predecessor eventually starts responding" (unlike a fresh
    # process-identity check racing a just-spawned successor); a token
    # failing only with those, for at least `_RETIRE_ABANDON_GRACE_S`,
    # is concluded rather than retried again. The conclusion is logged
    # exactly once (that log event is itself what the `retired` set above
    # picks up on the next sweep, self-limiting without extra state).
    by_token: dict[str, list[dict[str, object]]] = {}
    for event in retire_events:
        token = str(event.get("handoff_token") or "").strip()
        if token:
            by_token.setdefault(token, []).append(event)
    now_ts = time.time()
    for token, events in by_token.items():
        if token in retired:
            continue
        if not events or not all(
            e.get("method") in _RETIRE_TERMINAL_FAILURE_METHODS for e in events
        ):
            continue
        failure_times = []
        for event in events:
            try:
                failure_times.append(
                    datetime.fromisoformat(
                        str(event.get("ts")).replace("Z", "+00:00")
                    ).timestamp()
                )
            except (TypeError, ValueError):
                continue
        if not failure_times or (now_ts - min(failure_times)) < _RETIRE_ABANDON_GRACE_S:
            continue
        last = events[-1]
        activity.log_event(
            "handoff_predecessor_retire",
            worktree_id=worktree_id,
            session_id=last.get("session_id"),
            source="python",
            handoff_token=token,
            old_pane=last.get("old_pane"),
            successor_verified=True,
            reason="monitor-abandoned",
            method=last.get("method"),
            outcome="abandoned",
            copilot_found=0,
            copilot_reaped=0,
            copilot_survivors=0,
        )
        retired.add(token)
    requests: list[dict[str, object]] = []
    candidates = [h for h in record.handoffs if getattr(h, "state", None) != "cancelled"]
    for handoff in reversed(sorted(candidates, key=lambda h: getattr(h, "ordinal", 0))):
        token = str(getattr(handoff, "token", "") or "").strip()
        if not token or token in retired:
            continue
        successor_session = str(
            getattr(handoff, "successor", None) or getattr(handoff, "candidate", None) or ""
        ).strip()
        if not successor_session:
            continue
        spawn = spawned.get(token)
        if not spawn:
            continue
        predecessor_session = str(
            spawn.get("session_id") or getattr(handoff, "predecessor", "") or ""
        ).strip()
        old_pane = str(spawn.get("old_pane") or "").strip() or None
        if not predecessor_session or not old_pane:
            continue
        predecessor_pid = spawn.get("predecessor_copilot_pid")
        try:
            predecessor_pid = (
                int(str(predecessor_pid).strip()) if predecessor_pid not in (None, "") else None
            )
        except (TypeError, ValueError):
            predecessor_pid = None
        predecessor_start = str(spawn.get("predecessor_copilot_start_time") or "").strip() or None
        # The recorded predecessor_copilot_pid/start_time can itself be
        # permanently wrong for a handoff whose spawn event predates the
        # process-identity fix (context-handoff previously logged its own
        # Node extension-host pid, not the actual copilot process) -- no
        # future fix corrects data already written. A fresh, session-scoped
        # mux_binding_for_session() lookup is the same stronger identity
        # proof the spawn-time fix now trusts; prefer it here too so a
        # historically-poisoned old_pane/pid pairing can still be resolved
        # and retired instead of permanently failing its identity check.
        try:
            live_binding = sessions.mux_binding_for_session(
                predecessor_session,
                expected_session_name=sessions.mux_session_name(worktree_id),
            )
        except Exception:
            live_binding = None
        if live_binding:
            old_pane = live_binding.get("pane_id") or old_pane
            predecessor_pid = live_binding.get("copilot_pid")
            predecessor_start = str(live_binding.get("copilot_start_time") or "").strip() or None
        yield_reason = "linked" if getattr(handoff, "successor", None) else "candidate"
        requests.append({
            "handoff_token": token,
            "worktree_id": worktree_id,
            "predecessor_session_id": predecessor_session,
            "predecessor_pid": predecessor_pid,
            "predecessor_start_time": predecessor_start,
            "retire_pane": old_pane,
            "mux_session": str(spawn.get("expected_mux_session") or "").strip() or None,
            "successor_session_id": successor_session,
            "retire_reason": f"monitor-successor-{yield_reason}",
        })
    return requests


def _monitor_pending_handoff_predecessor_retire(
    record: tracking.WorktreeRecord,
    *,
    require_monitor_enabled: bool = True,
) -> dict[str, object] | None:
    """Return one consumed/associated handoff whose predecessor still needs retirement.

    ``require_monitor_enabled`` gates this to the resident daemon's own
    automatic sweep (default). An explicit, on-demand caller (``handoffs-check``)
    passes ``False`` -- a manual diagnostic must work even when the background
    monitor is disabled (``AGENT_WORKTREES_STATUS_MONITOR=0``); that toggle
    only opts a machine out of *automatic* sweeps, not out of an agent's
    ability to explicitly ask "is this worktree's cutover actually finished?".
    """
    if require_monitor_enabled and not _status_monitor_enabled():
        return None
    requests = _pending_handoff_retire_requests(record)
    return requests[0] if requests else None


def _monitor_retire_handoff_predecessor(
    request: dict[str, object],
) -> tuple[int, dict[str, object]]:
    """Invoke the existing handoff-cutover retire choreography in-process."""
    binding = None
    predecessor_session = str(request.get("predecessor_session_id") or "").strip() or None
    if predecessor_session:
        worktree_id = str(request.get("worktree_id") or "").strip() or None
        expected_session_name = sessions.mux_session_name(worktree_id) if worktree_id else None
        try:
            binding = sessions.mux_binding_for_session(
                predecessor_session, expected_session_name=expected_session_name
            )
        except TypeError:
            try:
                binding = sessions.mux_binding_for_session(predecessor_session)
            except Exception:
                binding = None
        except Exception:
            binding = None
    predecessor_pid = request.get("predecessor_pid")
    predecessor_start = request.get("predecessor_start_time")
    if binding:
        current_pid = binding.get("copilot_pid")
        current_start = str(binding.get("copilot_start_time") or "").strip() or None
        if (
            predecessor_pid is not None
            and current_pid != predecessor_pid
            or predecessor_start not in (None, "")
            and current_start != str(predecessor_start)
        ):
            activity.log_event(
                "handoff_predecessor_retire",
                worktree_id=request.get("worktree_id"),
                session_id=predecessor_session,
                source="python",
                handoff_token=request.get("handoff_token"),
                successor_session_id=request.get("successor_session_id"),
                old_pane=request.get("retire_pane"),
                successor_verified=True,
                reason=request.get("retire_reason"),
                method="process-identity-mismatch",
                outcome="identity-mismatch",
                copilot_found=0,
                copilot_reaped=0,
                copilot_survivors=0,
            )
            return 1, {
                "ok": False,
                "pane": request.get("retire_pane"),
                "gone": False,
                "method": "process-identity-mismatch",
                "expected_copilot_pid": predecessor_pid,
                "expected_copilot_start_time": predecessor_start,
            }
    return _handoff_cutover_retire_result(
        argparse.Namespace(
            worktree_id=request.get("worktree_id"),
            session_id=predecessor_session,
            handoff_token=request.get("handoff_token"),
            mux_session=request.get("mux_session"),
            require_mux_identity=bool(request.get("mux_session")),
            retire_pane=request.get("retire_pane"),
            successor_verified=True,
            retire_reason=request.get("retire_reason"),
            successor_session_id=request.get("successor_session_id"),
            expected_copilot_pid=predecessor_pid,
            expected_copilot_start_time=predecessor_start,
            seed=None,
            dry_run=False,
            json=True,
        )
    )



def _monitor_trigger_handoff_cutover(
    request: dict[str, object],
) -> tuple[int, dict[str, object]]:
    """Invoke the existing handoff-cutover spawn choreography in-process."""
    predecessor_session = str(request.get("predecessor_session_id") or "").strip() or None
    predecessor_pid = request.get("predecessor_pid")
    predecessor_start = request.get("predecessor_start_time")
    old_pane = None
    mux_session = None
    if predecessor_session:
        worktree_id = str(request.get("worktree_id") or "").strip() or None
        expected_session_name = sessions.mux_session_name(worktree_id) if worktree_id else None
        try:
            binding = sessions.mux_binding_for_session(
                predecessor_session, expected_session_name=expected_session_name,
            )
        except Exception:
            binding = None
        if binding:
            # Trust a freshly resolved, session-scoped binding unconditionally --
            # it is keyed off the predecessor's own registered ``inuse.<pid>.lock``
            # (a strong per-session identity proof), whereas ``predecessor_pid``
            # here is only ever a caller-supplied *hint* (e.g. context-handoff's
            # own `handoff_requested` activity field, which historically recorded
            # its own Node extension-host pid rather than the actual `copilot`
            # process -- #handoff-cutover-lifecycle-journal). Requiring agreement
            # with an untrustworthy hint before accepting a good binding is what
            # let every subsequent handoff on a worktree silently fail to retire
            # its predecessor (permanently, since a failed attempt was still
            # recorded as "handled") -- the hint is no longer load-bearing here.
            old_pane = binding.get("pane_id")
            mux_session = binding.get("session_name")
            predecessor_pid = binding.get("copilot_pid")
            predecessor_start = str(binding.get("copilot_start_time") or "").strip() or None
    rc, response = _handoff_cutover_spawn_result(
        argparse.Namespace(
            seed=request.get("seed"),
            worktree_id=request.get("worktree_id"),
            session_id=predecessor_session,
            handoff_token=request.get("token"),
            mux_session=None,
            require_mux_identity=False,
            old_pane=old_pane,
            retire_pane=None,
            successor_verified=False,
            retire_reason=None,
            expected_copilot_pid=predecessor_pid,
            expected_copilot_start_time=predecessor_start,
            dry_run=False,
            json=True,
        )
    )
    candidate_session = str(response.get("candidate_session") or "").strip() if rc == 0 else ""
    if candidate_session and response.get("old_pane"):
        _monitor_retire_handoff_predecessor(
            {
                "handoff_token": request.get("token"),
                "worktree_id": request.get("worktree_id"),
                "predecessor_session_id": predecessor_session,
                "predecessor_pid": predecessor_pid,
                "predecessor_start_time": predecessor_start,
                "retire_pane": response.get("old_pane"),
                "mux_session": mux_session or response.get("session"),
                "successor_session_id": candidate_session,
                "retire_reason": "monitor-successor-candidate",
            }
        )
    return rc, response


def _monitor_maybe_trigger_handoff_cutover(path: str, *, governance=None) -> None:
    """Pick up one actionable pending handoff for ``path`` if the monitor owns it."""
    record = _find_record_for_path(path)
    if record is None:
        return
    _monitor_maybe_process_handoff_record(record, governance=governance)


def _monitor_maybe_process_handoff_record(
    record: tracking.WorktreeRecord,
    *,
    governance=None,
) -> None:
    """Run the monitor's handoff retire/spawn checks for one tracking record."""
    retire_request = _monitor_pending_handoff_predecessor_retire(record)
    if retire_request is not None:
        _status_monitor_recheck(governance, "pre-mutation:handoff-predecessor-retire")
        _monitor_retire_handoff_predecessor(retire_request)
    request = _monitor_pending_handoff_request(record)
    if request is None:
        return
    _status_monitor_recheck(governance, "pre-mutation:handoff-cutover")
    _monitor_trigger_handoff_cutover(request)


def _monitor_sweep(
    mux_bin: str | None,
    token: str,
    prefix: str,
    ctx_done: set[str],
    interval: float = 15,
    picker_projects: set[str] | None = None,
    catalog_observer=None,
    pane_observer=None,
    segment_cache=None,
    published: dict[tuple[str, str], str] | None = None,
    incarnations: dict[str, str] | None = None,
    session_projects: dict[str, str] | None = None,
    project_lock=None,
    lifecycle_priority=None,
    governance=None,
) -> int:
    """One coalescing pass over all live, registered ``wt-*`` sessions.

    Returns the number of sessions served, or ``-1`` on a *transient* mux
    enumeration failure (so the caller neither prunes nor idle-exits on a
    hiccup).  For each served session it wins the ``@aw_updater`` election (so a
    lingering per-session updater stands down), renders ``@aw_ctx`` once and
    ``@aw_seg`` every pass -- the same renders the per-session updater did,
    coalesced into one process.  Registry entries whose session is definitively
    gone are pruned.
    """
    reg_dir = _monitor_registry_dir()
    registry = _read_monitor_registry(reg_dir)
    served: list[tuple[str, str]] = []
    if mux_bin:
        live = _monitor_list_sessions(mux_bin)
        if live is None:
            return -1
        if catalog_observer is not None:
            catalog_observer(set(live))
        live_wt = {n for n in live if n.startswith("wt-")}
        stale_sessions = [s for s in registry if s not in live_wt]
        if stale_sessions:
            _status_monitor_recheck(governance, "pre-mutation:prune-registry")
        for sess in stale_sessions:
            _remove_monitor_entry(reg_dir, sess)
            registry.pop(sess, None)
            ctx_done.discard(sess)
            if incarnations is not None:
                incarnations.pop(sess, None)
            if published is not None:
                for key in [key for key in published if key[0] == sess]:
                    published.pop(key, None)
        if incarnations is not None:
            for sess in live_wt:
                value = live.get(sess)
                incarnation = str(value[1]) if isinstance(value, tuple) and len(value) > 1 else ""
                prior = incarnations.get(sess)
                if prior is not None and prior != incarnation:
                    ctx_done.discard(sess)
                    if published is not None:
                        for key in [key for key in published if key[0] == sess]:
                            published.pop(key, None)
                incarnations[sess] = incarnation
        served = [(s, p) for s, p in registry.items() if s in live_wt and p]
    if session_projects is not None:
        registered_paths: set[str] = set()
        for path in registry.values():
            if not path:
                continue
            try:
                registered_paths.add(os.path.normcase(os.path.realpath(path)))
            except (OSError, ValueError):
                continue
        for key in list(session_projects):
            if key not in registered_paths:
                session_projects.pop(key, None)
    warm_projects: dict[str, str | None] = {
        project: None for project in (picker_projects or set())
    }
    served_path_keys: set[str] = set()
    for sess, path in served:
        if pane_observer is not None:
            pane_observer(sess, path)
        context_value = None
        segment_value = ""
        _wait_for_lifecycle_priority(lifecycle_priority)
        with project_lock if project_lock is not None else contextlib.nullcontext():
            try:
                path_key = os.path.normcase(os.path.realpath(path))
                project = session_projects.get(path_key) if session_projects is not None else None
                if project:
                    cfg.set_active_project(project)
                else:
                    _activate_project_for_path(path, force=True)
                    project = cfg.project_name()
                    if session_projects is not None:
                        session_projects[path_key] = project
                served_path_keys.add(path_key)
                warm_projects.setdefault(project, path)
            except Exception:
                pass
            if sess not in ctx_done:
                try:
                    context_value = _render_status_context(path, plain=False)
                except Exception:
                    pass
            try:
                if segment_cache is not None:
                    segment_value = segment_cache.get(path)
                else:
                    _status_monitor_recheck(governance, "pre-mutation:render-status")
                    segment_value = _render_status_segment(
                        path, fetch=False, plain=False, no_title=False, persist_title=True
                    )
            except _StatusMonitorGovernanceDeferred:
                raise
            except Exception:
                pass
            if mux_bin:
                try:
                    _monitor_maybe_trigger_handoff_cutover(path, governance=governance)
                except _StatusMonitorGovernanceDeferred:
                    raise
                except Exception:
                    pass

        # Win the single-instance election so any per-session updater retires,
        # publishing our current-runtime prefix so it defers to us (not to a
        # superseded owner) -- see cmd_status_updater's debounce.
        def _publish(option: str, value: str, session: str = sess) -> bool:
            key = (session, option)
            if published is not None and published.get(key) == value:
                return True
            success = _monitor_mux_set(mux_bin, session, option, value)
            if published is not None and success:
                published[key] = value
            return success

        _status_monitor_recheck(governance, "pre-mutation:publish-status")
        _publish("@aw_updater", token)
        _publish("@aw_updater_prefix", prefix)
        if context_value is not None and _publish("@aw_ctx", context_value):
            ctx_done.add(sess)
        _publish("@aw_seg", segment_value)
    # A pending-handoff worktree that this tick's served-pane pass never
    # observed (dormant since the last sweep, or missed by a stale project
    # scope) can still need its cutover check run -- that is the whole point
    # of this pass (#handoff-cutover-head-misalignment). But it must never
    # attempt live cutover choreography (subprocess mux queries, a spawn, a
    # retire) against a worktree that is not *actually* sitting in a live mux
    # session right now: mirror the served-pane pass's own ``if mux_bin:``
    # gate, then positively confirm liveness with ``has_mux_session`` per
    # worktree rather than only inferring dormancy from "not in the served
    # set" (served can miss a genuinely live session for reasons unrelated to
    # whether it is safe to act -- e.g. a stale project scope on this tick).
    if mux_bin:
        scan_projects = list(warm_projects)
        active_project = cfg.active_project()
        if active_project and active_project not in scan_projects:
            scan_projects.append(active_project)
        for project in scan_projects:
            _wait_for_lifecycle_priority(lifecycle_priority)
            with project_lock if project_lock is not None else contextlib.nullcontext():
                try:
                    cfg.set_active_project(project)
                    for record in tracking.list_records(cfg.tracking_dir()):
                        worktree_path = getattr(record, "worktree_path", None)
                        if worktree_path:
                            try:
                                if (
                                    os.path.normcase(os.path.realpath(worktree_path))
                                    in served_path_keys
                                ):
                                    continue
                            except (OSError, ValueError):
                                pass
                        if not record.pending_handoffs:
                            retire_request = _monitor_pending_handoff_predecessor_retire(record)
                            if retire_request is None:
                                continue
                        try:
                            if not sessions.has_mux_session(record.worktree_id):
                                continue
                        except Exception:
                            continue
                        _monitor_maybe_process_handoff_record(record, governance=governance)
                except _StatusMonitorGovernanceDeferred:
                    raise
                except Exception:
                    pass
    for project in warm_projects:
        _wait_for_lifecycle_priority(lifecycle_priority)
        with project_lock if project_lock is not None else contextlib.nullcontext():
            try:
                _status_monitor_recheck(governance, "pre-mutation:warm-list-cache")
                cfg.set_active_project(project)
                _warm_list_cache_for_active_project(interval=interval)
            except _StatusMonitorGovernanceDeferred:
                raise
            except Exception:
                pass
    return len(served)


class _StatusMonitorGovernanceDeferred(RuntimeError):
    """Raised when loop governance blocks a status-monitor mutation."""

    def __init__(self, result: dict):
        super().__init__(str(result.get("reason") or "governance blocked"))
        self.result = result


def _status_monitor_recheck(governance, checkpoint: str) -> dict | None:
    """Return the recheck result or raise when mutation is no longer allowed."""
    if governance is None:
        return None
    result = governance.recheck(checkpoint)
    if result is not None and result.get("status") != "ready":
        raise _StatusMonitorGovernanceDeferred(result)
    return result


class _StatusSegmentCache:
    """Share throttled Git classification across monitor and IPC consumers.

    Callers establish the target's active project before ``get`` so record
    resolution and rendering share one activation.
    """

    def __init__(self, ttl: float = 60.0):
        self.ttl = max(15.0, float(ttl))
        self._entries: dict[str, tuple[float, str]] = {}
        self._aliases: dict[str, str] = {}
        self._lock = threading.RLock()

    @staticmethod
    def _key(path: str) -> str:
        return os.path.normcase(os.path.realpath(path))

    def get(self, path: str) -> str:
        input_key = self._key(path)
        with self._lock:
            now = time.monotonic()
            key = self._aliases.get(input_key, input_key)
            cached = self._entries.get(key)
            if cached and now - cached[0] < self.ttl:
                return cached[1]

        record = _find_record_for_path(path)
        target = record.worktree_path if record and record.worktree_path else path
        key = self._key(target)
        with self._lock:
            now = time.monotonic()
            cached = self._entries.get(key)
            if cached and now - cached[0] < self.ttl:
                self._aliases[input_key] = key
                return cached[1]
        value = _render_status_segment(
            target, fetch=False, plain=False, no_title=False, persist_title=True
        )
        with self._lock:
            self._aliases[input_key] = key
            self._entries[key] = (time.monotonic(), value)
        return value

    def invalidate(self, cwd: str | None) -> None:
        if not cwd:
            return
        target = self._key(cwd)
        with self._lock:
            removed: set[str] = set()
            for key in list(self._entries):
                try:
                    common = os.path.commonpath((target, key))
                except (OSError, ValueError):
                    continue
                if common == key or common == target:
                    self._entries.pop(key, None)
                    removed.add(key)
            for alias, key in list(self._aliases.items()):
                try:
                    common = os.path.commonpath((target, alias))
                except (OSError, ValueError):
                    common = ""
                if key in removed or common == alias or common == target:
                    self._aliases.pop(alias, None)

    def invalidate_all(self) -> None:
        with self._lock:
            self._entries.clear()
            self._aliases.clear()


_HOOK_WRITE_TOOLS = frozenset(
    {
        "create",
        "edit",
        "str_replace",
        "str_replace_editor",
        "str_replace_based_edit_tool",
        "write",
        "write_file",
        "insert",
        "apply_patch",
        "new_file",
        "multi_edit",
    }
)
_HOOK_SHELL_TOOLS = frozenset(
    {
        "bash",
        "sh",
        "shell",
        "powershell",
        "pwsh",
        "cmd",
        "run",
        "run_command",
        "execute",
        "exec",
        "terminal",
    }
)


def _hook_payload_cwd(payload: dict) -> str:
    return str(payload.get("workingDirectory") or payload.get("cwd") or os.getcwd())


def _session_lifecycle_metadata(payload: dict) -> tuple[str, dict[str, str]]:
    metadata = payload.get("_agentWorktrees")
    if not isinstance(metadata, dict):
        metadata = {}
    version = metadata.get("pluginVersion")
    environment = metadata.get("environment")
    normalized_environment = (
        {
            str(key): str(value)
            for key, value in environment.items()
            if isinstance(key, str) and isinstance(value, str)
        }
        if isinstance(environment, dict)
        else {}
    )
    return version if isinstance(version, str) else "", normalized_environment


def _session_lifecycle_launch_key(payload: dict, version: str) -> str:
    import hashlib
    import math
    import struct

    session_id = payload.get("sessionId")
    cwd = payload.get("cwd")
    source = payload.get("source", "")
    timestamp = payload.get("timestamp")
    if (
        not isinstance(session_id, str)
        or not session_id
        or not isinstance(cwd, str)
        or not os.path.isabs(cwd)
        or not isinstance(source, str)
        or not version
        or isinstance(timestamp, bool)
        or not isinstance(timestamp, (int, float))
        or not math.isfinite(timestamp)
    ):
        return ""
    try:
        canonical_cwd = os.path.realpath(cwd)
    except OSError:
        return ""
    timestamp_text = (
        str(timestamp)
        if isinstance(timestamp, int)
        else f"f64:{struct.pack('>d', timestamp).hex()}"
    )
    identity = json.dumps(
        [session_id, canonical_cwd, source, version, timestamp_text],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _write_session_lifecycle_snapshot(
    name: str,
    payload: dict,
    output_text: str,
) -> None:
    version, _environment = _session_lifecycle_metadata(payload)
    if not version:
        try:
            version = (_aw_runtime_home() / "current-version").read_text(encoding="utf-8").strip()
        except (OSError, UnicodeError):
            version = ""
    launch_key = _session_lifecycle_launch_key(payload, version)
    if not launch_key:
        return
    root = _aw_runtime_home() / ".session-context"
    target = root / f"{name}-{launch_key}.json"
    temporary = target.with_name(f"{target.name}.{os.getpid()}.tmp")
    try:
        root.mkdir(parents=True, exist_ok=True)
        temporary.write_text(
            json.dumps(
                {"launchKey": launch_key, "output": output_text},
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        os.replace(temporary, target)
    except OSError:
        try:
            temporary.unlink()
        except OSError:
            pass


def _registration_nudge_context(cwd: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return ""
        top = Path(result.stdout.strip()).resolve()
        anchor = git_ops.resolve_to_anchor(top)
        name = anchor.name
        if not name:
            return ""
        projects = inst.read_projects_registry().get("projects", {})
        if isinstance(projects, dict) and name in projects:
            return ""
        import hashlib

        marker_dir = _aw_runtime_home() / ".register-nudged"
        marker = marker_dir / hashlib.sha1(str(top).encode("utf-8")).hexdigest()
        if marker.exists():
            return ""
        marker_dir.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
        return (
            f"This repo ({name}) is not a registered agent-worktrees project. "
            "To enable isolated, concurrent worktree sessions (create/finalize "
            "+ the PR flow), register it once from the repo root: "
            f"agent-worktrees register {name} . This is an onboarding nudge "
            "only -- nothing has been registered, and agent-worktrees never "
            "auto-adopts a repo."
        )
    except Exception:
        return ""


def _start_project_session_hook(
    cwd: str,
    environment: dict[str, str],
) -> subprocess.Popen | None:
    try:
        _activate_project_for_path(cwd, force=True)
        project = cfg.project_name()
    except Exception:
        return None
    script = (
        cfg.project_dir(project)
        / "hooks"
        / ("session-start.ps1" if os.name == "nt" else "session-start.sh")
    )
    if not script.is_file():
        return None
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell.exe")
        argv = [shell, "-NoLogo", "-NoProfile", "-File", str(script)] if shell else []
    else:
        shell = shutil.which("bash")
        argv = [shell, str(script)] if shell else []
    if not argv:
        return None
    child_environment = dict(environment)
    group_kwargs = (
        {"start_new_session": True}
        if os.name == "posix"
        else {
            "creationflags": getattr(
                subprocess,
                "CREATE_NEW_PROCESS_GROUP",  # headless-guard: allow bounded hook child in its own process group while stdout/stderr stay piped
                0,  # headless-guard: allow: bounded hook process group plus the resident headless-child guard
            )
        }
    )
    try:
        return subprocess.Popen(
            argv,
            cwd=cwd,
            env=child_environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            **group_kwargs,
        )
    except OSError:
        return None


def _finish_project_session_hook(
    process: subprocess.Popen | None,
    deadline: float | None,
) -> tuple[dict, str]:
    if process is None:
        return {}, ""
    remaining = max(0.1, deadline - time.time() - 0.25) if deadline is not None else 10.0
    try:
        stdout, stderr = process.communicate(timeout=remaining)
    except subprocess.TimeoutExpired:
        try:
            if os.name == "posix":
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            else:
                process.send_signal(signal.CTRL_BREAK_EVENT)
        except (OSError, ProcessLookupError):
            process.kill()
        try:
            process.communicate(timeout=0.25)
        except subprocess.TimeoutExpired:
            process.kill()
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
        return {}, "[agent-worktrees] Project session-start hook timed out.\n"
    hook_result: dict = {}
    try:
        value = json.loads(stdout.strip() or "{}")
        if isinstance(value, dict):
            hook_result = value
    except ValueError:
        stderr += stdout
    diagnostic = stderr
    if process.returncode != 0:
        diagnostic += (
            f"[agent-worktrees] Project session-start hook exited {process.returncode}.\n"
        )
    return hook_result, diagnostic


def _anchor_hygiene_diagnostic(cwd: str) -> str:
    try:
        from . import anchor_hygiene

        report = anchor_hygiene.check_anchor(cwd)
    except Exception:
        return ""
    messages = []
    if report.is_behind:
        messages.append(
            "[agent-worktrees] Anchor repo is "
            f"{report.behind_count} commit(s) behind "
            f"{report.tracking or 'upstream'}: {report.anchor_path}"
        )
    if report.dirty_files:
        messages.append(
            f"[agent-worktrees] Anchor repo has uncommitted work: {report.anchor_path}"
        )
    if report.stash_entries:
        messages.append(f"[agent-worktrees] Anchor repo has stash entries: {report.anchor_path}")
    return "".join(f"{message}\n" for message in messages)


def _migrate_legacy_marketplace_overrides(payload: dict, cwd: str) -> None:
    """One-time cleanup of a marker left by the retired marketplace_overrides
    mechanism.

    A repo's ``.github/copilot/settings.local.json`` may still carry the
    now-defunct ``_agentWorktreesMarketplaceOverrides`` marker and its
    absolute ``directory``-source entries from a prior version of this
    plugin. Left in place, those entries would keep shadowing the repo's own
    committed marketplace declaration with a stale, anchor-pinned path
    forever, since nothing produces or refreshes that marker anymore. This
    retires exactly the marker-owned values that are still unmodified and
    removes the marker; any operator edit to a managed key is preserved
    untouched, matching ``_retire_invalid_pair_overlay_locked``'s own
    retirement contract. Idempotent and a silent no-op once migrated.
    """
    output_text = "{}"
    try:
        from . import knowledge_plugins as kp

        output_path = Path(cwd).resolve() / ".github" / "copilot" / "settings.local.json"
        if output_path.is_file():
            with kp._overlay_transaction(output_path):
                existing = kp._load_json_object(output_path)
                marker = existing.get("_agentWorktreesMarketplaceOverrides")
                previous = marker.get("marketplaces") if isinstance(marker, dict) else None
                if isinstance(marker, dict) and marker.get("version") == 1 and isinstance(previous, dict):
                    marketplaces = kp._dict_setting(existing, "extraKnownMarketplaces", output_path)
                    kp._retire_previous(marketplaces, previous)
                    result = dict(existing)
                    result.pop("_agentWorktreesMarketplaceOverrides", None)
                    if marketplaces:
                        result["extraKnownMarketplaces"] = marketplaces
                    else:
                        result.pop("extraKnownMarketplaces", None)
                    if kp._write_overlay(output_path, result):
                        output_text = json.dumps(
                            {
                                "additionalContext": (
                                    "Agent Worktrees retired a legacy local "
                                    f"marketplace source override in {output_path}. "
                                    "Restart Copilot CLI for the committed "
                                    "marketplace source to take effect."
                                )
                            }
                        )
    except Exception:
        pass
    _write_session_lifecycle_snapshot("marketplace-overrides-migration", payload, output_text)


def _reconcile_knowledge_plugin_overlay(payload: dict, cwd: str) -> None:
    """Best-effort sessionStart refresh of the knowledge-repo plugin overlay.

    ``compose_from_pair`` otherwise only runs at worktree create time (or via
    the manual ``knowledge compose-plugins`` CLI subcommand), so a plugin
    enabled in the knowledge repo's own settings *after* the harness worktree
    was created would never propagate into ``settings.local.json`` -- not
    even across a restart. Re-running it here, silently no-op'ing when the
    checkout isn't a valid/paired knowledge pair, closes that gap.
    """
    output_text = "{}"
    try:
        from . import knowledge_plugins

        _activate_project_for_path(cwd)
        summary = knowledge_plugins.compose_from_pair(cwd=cwd)
        if summary.get("changed"):
            path = summary.get("settings_local", "settings.local.json")
            output_text = json.dumps(
                {
                    "additionalContext": (
                        "Agent Worktrees updated the knowledge-repo plugin "
                        f"enable overlay in {path}. Restart Copilot CLI for "
                        "the newly enabled plugin(s) to take effect."
                    )
                }
            )
    except Exception:
        pass
    _write_session_lifecycle_snapshot("knowledge-plugin-overlay", payload, output_text)


def _provisioning_status_diagnostic(cwd: str) -> str:
    status = _aw_runtime_home() / "logs" / "provision-status.json"
    try:
        previous = json.loads(status.read_text(encoding="utf-8"))
        if (
            isinstance(previous, dict)
            and previous.get("ok") is False
            and os.path.normcase(os.path.realpath(str(previous.get("repo") or "")))
            == os.path.normcase(os.path.realpath(cwd))
        ):
            failed = ", ".join(
                str(item.get("service"))
                for item in previous.get("failed", [])
                if isinstance(item, dict) and item.get("service")
            ) or str(previous.get("reason") or "unknown failure")
            return (
                "[agent-worktrees] Previous background provisioning failed: "
                f"{failed}. Inspect {_aw_runtime_home() / 'logs'} and rerun "
                "reconcile-plugins.\n"
            )
    except (OSError, UnicodeError, ValueError, TypeError):
        pass
    return ""


def _start_provisioning_if_needed(
    cwd: str,
    session_environment: dict[str, str] | None = None,
    *,
    include_status_diagnostic: bool = True,
    process_holder: list[subprocess.Popen] | None = None,
) -> str:
    session_environment = session_environment or {}
    if (
        session_environment.get("WORKTREE_NO_RECONCILE") == "1"
        or session_environment.get("WORKTREE_NO_PROVISION") == "1"
    ):
        return ""
    status = _aw_runtime_home() / "logs" / "provision-status.json"
    diagnostic = _provisioning_status_diagnostic(cwd) if include_status_diagnostic else ""
    try:
        from . import reconcile

        plan = reconcile.build_plan(Path(cwd), save=False)
    except Exception as exc:
        return diagnostic + (f"[agent-worktrees] Runtime provisioning preview failed: {exc}\n")
    if plan.get("action") != "reconcile":
        return diagnostic
    services = ", ".join(
        dict.fromkeys(
            str(item.get("service"))
            for item in plan.get("updates", [])
            if isinstance(item, dict) and item.get("service")
        )
    )
    status.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    log = status.parent / f"provision-{stamp}.log"
    argv = [
        sys.executable,
        "-m",
        "agent_worktrees",
        "reconcile-plugins",
        "--repo",
        cwd,
        "--status",
        str(status),
        "--apply",
    ]
    argv[0] = _windowless_python()
    env = dict(os.environ)
    env.update(windowless_python_env(sys.executable))
    kwargs: dict = {
        "env": env,
        "stdin": subprocess.DEVNULL,
        "cwd": os.path.expanduser("~"),
    }
    kwargs.update(detached_kwargs(breakaway=True))
    try:
        stdout = log.open("w", encoding="utf-8")
        stderr = log.with_suffix(".log.err").open("w", encoding="utf-8")
        try:
            process = subprocess.Popen(argv, stdout=stdout, stderr=stderr, **kwargs)
            if process_holder is not None:
                process_holder.append(process)
        finally:
            stdout.close()
            stderr.close()
        spawned = True
    except Exception:
        spawned = False
    if not spawned:
        diagnostic += "[agent-worktrees] Could not start background provisioning.\n"
    elif services:
        diagnostic += f"[agent-worktrees] Provisioning runtime(s) in background: {services}\n"
    return diagnostic


_PROVISIONING_WORKERS_LOCK = threading.Lock()
_PROVISIONING_WORKERS: set[str] = set()


def _schedule_provisioning_if_needed(
    cwd: str,
    session_environment: dict[str, str] | None = None,
    *,
    start_event: threading.Event | None = None,
) -> str:
    session_environment = dict(session_environment or {})
    if (
        session_environment.get("WORKTREE_NO_RECONCILE") == "1"
        or session_environment.get("WORKTREE_NO_PROVISION") == "1"
    ):
        return ""
    diagnostic = _provisioning_status_diagnostic(cwd)
    key = os.path.normcase(os.path.realpath(cwd))
    with _PROVISIONING_WORKERS_LOCK:
        if key in _PROVISIONING_WORKERS:
            return diagnostic
        _PROVISIONING_WORKERS.add(key)

    def _worker() -> None:
        try:
            if start_event is not None:
                start_event.wait()
            process_holder: list[subprocess.Popen] = []
            worker_diagnostic = _start_provisioning_if_needed(
                cwd,
                session_environment,
                include_status_diagnostic=False,
                process_holder=process_holder,
            )
            if worker_diagnostic:
                log = _aw_runtime_home() / "logs" / "provision-preview.log"
                try:
                    log.parent.mkdir(parents=True, exist_ok=True)
                    with log.open("a", encoding="utf-8") as stream:
                        stream.write(
                            f"{datetime.now(timezone.utc).isoformat()} {cwd}\n{worker_diagnostic}"
                        )
                except OSError:
                    pass
            if process_holder:
                process_holder[-1].wait()
        finally:
            with _PROVISIONING_WORKERS_LOCK:
                _PROVISIONING_WORKERS.discard(key)

    try:
        threading.Thread(
            target=_worker,
            name="agent-worktrees-provision-preview",
            daemon=True,
        ).start()
    except (OSError, RuntimeError) as exc:
        with _PROVISIONING_WORKERS_LOCK:
            _PROVISIONING_WORKERS.discard(key)
        diagnostic += (
            f"[agent-worktrees] Could not schedule background provisioning preview: {exc}\n"
        )
    return diagnostic


def _run_session_lifecycle(
    payload: dict,
    *,
    deadline: float | None = None,
    provisioning_start_event: threading.Event | None = None,
    plugin_related_anchors: list[str] | None = None,
) -> dict:
    cwd = _hook_payload_cwd(payload)
    _version, session_environment = _session_lifecycle_metadata(payload)
    prior_project = cfg.active_project()
    diagnostics = ""
    result: dict = {}
    project_process = None
    _write_session_lifecycle_receipt(payload, "started")
    try:
        project_process = _start_project_session_hook(cwd, session_environment)
        nudge = _registration_nudge_context(cwd)
        _write_session_lifecycle_snapshot(
            "register-nudge",
            payload,
            json.dumps({"additionalContext": nudge}) if nudge else "{}",
        )

        _migrate_legacy_marketplace_overrides(payload, cwd)
        _reconcile_knowledge_plugin_overlay(payload, cwd)

        registration_args = argparse.Namespace(
            worktree_id=None,
            session_id=payload.get("sessionId"),
            cwd=cwd,
            stdin=False,
            pid=None,
            pane=(session_environment.get("TMUX_PANE") or session_environment.get("PSMUX_PANE")),
            launch_id=session_environment.get("WORKTREE_LAUNCH_ID"),
            assignment_token=session_environment.get(profile_assignment.ASSIGNMENT_TOKEN_ENV),
            emit_context=True,
            handoff_token=None,
            handoff_candidate_token=session_environment.get(_SESSION_HANDOFF_TOKEN),
            result_holder=[],
            resident_environment=True,
            hook_payload=payload,
            plugin_related_anchors=plugin_related_anchors,
        )
        try:
            # Prefer an already-bound cmd_register_session global (real or
            # test-monkeypatched, see test_hook_ipc.py); globals().get()
            # avoids __getattr__'s eager full load. Fall back to a direct
            # import if unbound (why session_inspection_cli isn't in
            # _CLUSTER_FREE_MODULES).
            _register_session_fn = globals().get("cmd_register_session")
            if _register_session_fn is None:
                from . import session_binding_cli as _session_binding_cli

                _register_session_fn = _session_binding_cli.cmd_register_session
            _register_session_fn(registration_args)
        except Exception as exc:
            diagnostics += f"[agent-worktrees] Session registration failed: {exc}\n"
        registration_output = (
            registration_args.result_holder[-1] if registration_args.result_holder else "{}"
        )
        _write_session_lifecycle_snapshot("register-session", payload, registration_output or "{}")

        if deadline is None or time.time() < deadline - 1.0:
            diagnostics += _anchor_hygiene_diagnostic(cwd)
        if deadline is None or time.time() < deadline - 1.0:
            if provisioning_start_event is None:
                diagnostics += _start_provisioning_if_needed(cwd, session_environment)
            else:
                diagnostics += _schedule_provisioning_if_needed(
                    cwd,
                    session_environment,
                    start_event=provisioning_start_event,
                )
    except Exception as exc:
        diagnostics += f"[agent-worktrees] Session lifecycle failed: {exc}\n"
    finally:
        project_result, project_diagnostic = _finish_project_session_hook(
            project_process, deadline
        )
        result.update(project_result)
        diagnostics += project_diagnostic
        _write_session_lifecycle_receipt(payload, "completed")
        cfg.set_active_project(prior_project)
    if diagnostics:
        result["_stderr"] = diagnostics
    return result


def _load_hook_client_module():
    import importlib.util

    candidates = []
    try:
        candidates.append(cfg.install_dir() / "bin" / "hook_client.py")
    except Exception:
        pass
    candidates.append(Path(__file__).resolve().parents[2] / "scripts" / "hook_client.py")
    for path in candidates:
        if not path.is_file():
            continue
        spec = importlib.util.spec_from_file_location(
            "_agent_worktrees_resident_hook_client", path
        )
        if spec is None or spec.loader is None:
            continue
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    return None


class _ResidentHookPolicy:
    """Warm hook policy inputs that previously required per-event discovery."""

    def __init__(self, hook_client, ttl: float = 300.0):
        self.hook_client = hook_client
        self.ttl = ttl
        self._anchors: tuple[float, tuple[int, int] | None, list[dict]] = (0.0, None, [])
        self._guarded: dict[str, tuple[float, list[dict]]] = {}
        self._plugin_related_anchors: list[str] | None = None

    def _module(self, name: str):
        if self.hook_client is None:
            return None
        return self.hook_client._load_sibling(name)

    def ready(self) -> bool:
        return self.hook_client is not None and all(
            self._module(name) is not None
            for name in (
                "statelessness_guard.py",
                "cross_repo_guard.py",
                "anchor_write_guard.py",
                "pr_supersede_guard.py",
                "nudge_status.py",
            )
        )

    def plugin_related_anchors(self) -> list[str]:
        if self._plugin_related_anchors is None:
            from . import related

            self._plugin_related_anchors = related.installed_plugin_related_anchors()
        return self._plugin_related_anchors

    def anchors(self) -> list[dict]:
        from . import registry_paths

        now = time.monotonic()
        module = self._module("anchor_write_guard.py")
        registry_root = registry_paths.registry_root()
        source = module._repos_yaml(registry_root) if module else None
        try:
            stat = source.stat() if source else None
            identity = (stat.st_mtime_ns, stat.st_size) if stat else None
        except OSError:
            identity = None
        if now - self._anchors[0] < self.ttl and identity == self._anchors[1]:
            return self._anchors[2]
        value = module.load_worktree_anchors(registry_root) if module else []
        self._anchors = (now, identity, value)
        return value

    def guarded_roots(self, cwd: str) -> list[dict]:
        from . import related, repos

        module = self._module("cross_repo_guard.py")
        root = module.find_repo_root(cwd) if module else None
        if root is None:
            return []
        key = os.path.normcase(os.path.realpath(root))
        now = time.monotonic()
        cached = self._guarded.get(key)
        if cached and now - cached[0] < self.ttl:
            return cached[1]
        try:
            anchors = _related_config_source_anchors(
                str(root),
                installed_anchors=self.plugin_related_anchors(),
            )
            entries = related.list_related_grafted(anchors)
            value = []
            for entry in entries:
                if not entry.delegate or entry.delegate == "none":
                    continue
                path = repos.resolve_path(entry.name)
                if path:
                    value.append(
                        {
                            "name": entry.name,
                            "delegate": entry.delegate,
                            "path": path,
                            "locus": {
                                "preferred": entry.locus.preferred,
                                "machines": list(entry.locus.machines),
                            },
                        }
                    )
        except Exception:
            value = []
        self._guarded[key] = (now, value)
        return value

    def pre(self, payload: dict) -> dict:
        tool = str(payload.get("toolName") or payload.get("tool_name") or "").lower()
        may_write = tool in _HOOK_WRITE_TOOLS or tool in _HOOK_SHELL_TOOLS
        combined: dict = {}
        for name in ("statelessness_guard.py", "cross_repo_guard.py", "anchor_write_guard.py", "pr_supersede_guard.py"):
            module = self._module(name)
            if module is None:
                continue
            kwargs = {}
            if name == "cross_repo_guard.py":
                kwargs["guarded_roots"] = (
                    self.guarded_roots(_hook_payload_cwd(payload)) if may_write else []
                )
            elif name == "anchor_write_guard.py":
                kwargs["anchors"] = self.anchors() if may_write else []
            try:
                decision = module.decide(payload, home=Path.home(), **kwargs)
            except Exception:
                continue
            if isinstance(decision, dict) and decision:
                combined = self.hook_client._merge_pre_decisions(combined, decision)
                if combined.get("permissionDecision") == "deny":
                    break
        return combined

    def post(self, payload: dict, deadline: float | None = None) -> dict:
        module = self._module("nudge_status.py")
        if module is None:
            return {}
        cwd = _hook_payload_cwd(payload)
        found = None
        try:
            _activate_project_for_path(cwd, force=True)
            wt_id = tracking.find_worktree_id_by_cwd(cwd)
            path = cfg.tracking_dir() / f"{wt_id}.yaml" if wt_id else None
            if wt_id and path and path.is_file():
                found = (wt_id, path)
        except Exception:
            pass
        try:
            text = module.decide(
                payload, home=Path.home(), tracking_record=found, deadline=deadline
            )
        except Exception:
            return {}
        return {"additionalContext": text} if text else {}

    def mutation_targets(self, payload: dict) -> list[str] | None:
        tool = str(payload.get("toolName") or payload.get("tool_name") or "").lower()
        if tool in _HOOK_WRITE_TOOLS:
            args = payload.get("toolArgs") or payload.get("tool_input") or {}
            cwd = _hook_payload_cwd(payload)
            if isinstance(args, dict):
                for key in (
                    "path",
                    "file_path",
                    "filePath",
                    "filename",
                    "fileName",
                    "target_file",
                    "targetFile",
                ):
                    value = args.get(key)
                    if value:
                        path = str(value)
                        if not os.path.isabs(path):
                            path = os.path.join(cwd, path)
                        return [path]
            return [cwd]
        if tool not in _HOOK_SHELL_TOOLS:
            return []
        args = payload.get("toolArgs") or payload.get("tool_input") or {}
        if not isinstance(args, dict):
            return []
        command = next(
            (
                str(args.get(key) or "")
                for key in ("command", "cmd", "script", "commandLine", "commandline", "input")
                if args.get(key)
            ),
            "",
        )
        module = self._module("statelessness_guard.py")
        return None if module and module._WRITE_VERBS.search(command) else []


def _resident_hook_decision(
    kind: str,
    payload: dict,
    *,
    segment_cache: _StatusSegmentCache,
    policy: _ResidentHookPolicy,
    deadline: float | None = None,
    provisioning_start_event: threading.Event | None = None,
) -> dict:
    cwd = _hook_payload_cwd(payload)
    previous_project = cfg.active_project()
    _activate_project_for_path(cwd, force=True)
    try:
        if kind == "preToolUse":
            return policy.pre(payload)
        if kind == "postToolUse":
            targets = policy.mutation_targets(payload)
            if targets is None:
                segment_cache.invalidate_all()
            else:
                for target in targets:
                    segment_cache.invalidate(target)
            advisory = policy.post(payload, deadline)
            binding = _bind_nudge_decision(cwd, deadline=deadline)
            if advisory and binding:
                a = str(advisory.get("additionalContext") or "")
                b = str(binding.get("additionalContext") or "")
                return {"additionalContext": "\n\n".join(x for x in (a, b) if x)}
            return advisory or binding
        if kind == "snapshot":
            return {"segment": segment_cache.get(cwd)}
        if kind == "sessionStart":
            return _run_session_lifecycle(
                payload,
                deadline=deadline,
                provisioning_start_event=provisioning_start_event,
                plugin_related_anchors=policy.plugin_related_anchors(),
            )
        return {}
    finally:
        cfg.set_active_project(previous_project)


_RESIDENT_LIFECYCLE_RUNWAY_S = 1.0


def _resident_hook_lock_timeout(kind: str, remaining: float) -> float:
    if kind == "sessionStart":
        return max(0.0, remaining - _RESIDENT_LIFECYCLE_RUNWAY_S)
    return min(0.05, remaining)


def _resident_hook_should_yield(kind: str, priority_event) -> bool:
    return kind != "sessionStart" and priority_event.is_set()


def _wait_for_lifecycle_priority(priority_event) -> None:
    while priority_event is not None and priority_event.is_set():
        time.sleep(0.01)


def _claim_resident_lifecycle(
    payload: dict,
    claims: dict[str, float],
    *,
    now: float | None = None,
) -> tuple[str, bool]:
    current = time.monotonic() if now is None else now
    for expired in [key for key, deadline in claims.items() if deadline <= current]:
        claims.pop(expired, None)
    version, _environment = _session_lifecycle_metadata(payload)
    launch_key = _session_lifecycle_launch_key(payload, version)
    if launch_key and launch_key in claims:
        return launch_key, False
    if launch_key:
        claims[launch_key] = float("inf")
    return launch_key, True


_RESIDENT_LIFECYCLE_DEDUPE_S = 60.0


def _release_resident_lifecycle(
    launch_key: str,
    claims: dict[str, float],
    *,
    completed: bool,
    now: float | None = None,
) -> None:
    if not launch_key:
        return
    if completed:
        current = time.monotonic() if now is None else now
        claims[launch_key] = current + _RESIDENT_LIFECYCLE_DEDUPE_S
    else:
        claims.pop(launch_key, None)


# resident monitor surface is componentized into status_monitor_runtime.py.

# list/read surfaces are componentized into list_cli.py.


# claim/follow-up surfaces are componentized into claims_cli.py and follow_ups_cli.py.


# create/run/sync worktree operations are componentized into worktree_ops_cli.py.


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

    if not rec.checkout_managed:
        if not tracking.retire_record(rec, tracking_path):
            # pr-attribution-codenames Phase 2 follow-up: a contended
            # cross-process lock (e.g. a concurrent codename backfill)
            # defers retirement rather than proceeding unsafely -- this
            # external record is retried on a later reap pass.
            warnings.append(
                f"Tracking record for {rec.worktree_id} is busy (contended "
                "lock) -- will retire on a later pass."
            )
            return 0, warnings
        disposition_history.remove(rec.worktree_id)
        handoff_trace.remove_trace(cfg.active_project(), rec.worktree_id)
        activity.log_event(
            "external_worktree_tracking_retired",
            worktree_id=rec.worktree_id,
            path=rec.worktree_path,
        )
        return 0, warnings

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
            names = ", ".join(f"{k['name'] or '?'}({k['pid']})" for k in killed if k["killed"])
            if names:
                warnings.append(f"Terminated lingering process(es): {names}")
            activity.log_event(
                "worktree_procs_terminated",
                worktree_id=rec.worktree_id,
                count=sum(1 for k in killed if k["killed"]),
            )

        if not git_ops.remove_worktree(repo.anchor, rec.worktree_path):
            warnings.append("Could not remove worktree via git -- forcing directory removal.")
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

    # Remove tracking YAML (or tombstone it, when paired -- #957/#220). The
    # worktree checkout/branch are already gone by this point regardless of
    # whether this succeeds -- a contended lock (pr-attribution-codenames
    # Phase 2 follow-up) defers retirement to a later reap pass rather than
    # deleting without real cross-process exclusivity.
    if tracking.retire_record(rec, tracking_path):
        disposition_history.remove(rec.worktree_id)
        handoff_trace.remove_trace(cfg.active_project(), rec.worktree_id)
    else:
        warnings.append(
            f"Tracking record for {rec.worktree_id} is busy (contended lock) "
            "-- worktree/branch removed; record will retire on a later pass."
        )

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
        return _result(
            {
                "ok": False,
                "removed": False,
                "skipped": False,
                "reason": f"worktree not found: {wt_id}",
            }
        )
    rec = tracking.load_record(yaml_path)
    if rec.kind in tracking.MANAGED_KINDS:
        return _result(
            {
                "ok": False,
                "removed": False,
                "skipped": True,
                "reason": f"agent-owned {rec.kind} worktree (use the System menu)",
            }
        )

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
            rec.worktree_path,
            rec.branch,
            fetch=False,
            remote=repo.remote,
            default_branch=repo.default_branch,
            active_paths=active_paths,
        )
        info = _apply_tracking_override(rec, info)
    elif rec.status == "finalized":
        info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.COMPLETED)
    else:
        info = git_ops.WorktreeStateInfo(state=git_ops.WorktreeState.GONE)

    # An active session is never reaped, even with force.
    if info.state == git_ops.WorktreeState.ACTIVE:
        return _result(
            {
                "ok": False,
                "removed": False,
                "skipped": True,
                "reason": "active Copilot session in use",
                "bucket": "active",
            }
        )
    if not force:
        if info.state == git_ops.WorktreeState.GONE:
            if rec.branch and not git_ops.is_branch_merged(
                rec.branch,
                upstream,
                cwd=repo.anchor,
            ):
                return _result(
                    {
                        "ok": False,
                        "removed": False,
                        "skipped": True,
                        "reason": "branch has unmerged commits (worktree dir missing)",
                    }
                )
        else:
            disp = prune.cleanup_disposition(
                rec,
                info,
                turn_count=turns,
                include_unused=include_unused,
                include_conversations=include_conversations,
                claimant_alive=claimant_mod.resolve_claimant_alive,
                paired_sibling_final=prune.default_paired_sibling_final,
            )
            if not disp.cleanable:
                return _result(
                    {
                        "ok": False,
                        "removed": False,
                        "skipped": True,
                        "reason": disp.reason,
                        "bucket": disp.bucket,
                    }
                )

    lock = fin.FinalizeLock(Path(repo.worktree_root) / ".finalize.lock")
    try:
        lock.acquire()
    except TimeoutError:
        return _result(
            {
                "ok": False,
                "removed": False,
                "skipped": False,
                "reason": "timed out waiting for finalization lock",
            }
        )
    try:
        result = _revalidate_cleanup_safety(
            wt_id,
            repo=repo,
            tracking_path=tracking_path,
            force=force,
            include_unused=include_unused,
            include_conversations=include_conversations,
            reap=lambda latest, fresh_info: _reap_worktree(
                latest, fresh_info, repo, tracking_path),
        )
        if not result.cleanable:
            return _result(
                {
                    "ok": False,
                    "removed": False,
                    "skipped": True,
                    "reason": result.reason,
                    "bucket": result.bucket,
                }
            )
        failures = result.failures
        warnings = result.warnings
        info = result.info
        git_ops.prune_worktrees(cwd=repo.anchor)
    finally:
        lock.release()

    return _result(
        {
            "ok": failures == 0,
            "removed": True,
            "skipped": False,
            "state": info.state.value,
            "warnings": warnings,
        }
    )


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


def reap_orphan_mux_sessions(
    *,
    dry_run: bool = False,
    only_id: str | None = None,
    idle_grace_secs: float = REAP_IDLE_GRACE_SECS,
    now: float | None = None,
) -> dict:
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

    # One reverse map, not a scan per session: the sweep is O(sessions x
    # records) otherwise.
    by_session = sessions.mux_session_index(by_id)

    reaped: list[str] = []
    skipped: list[dict] = []
    errors: list[dict] = []
    for name, attached in all_sessions.items():
        if not name.startswith("wt-"):
            continue
        # Resolve against the tracked ids: `mux_session_name` maps `.` -> `_`,
        # so stripping the prefix would miss a dotted id's record and read as
        # "untracked" below -- which reaps a live, tracked session.
        wt_id = sessions.worktree_id_from_mux_session(name, index=by_session)
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
                activity.log_event("mux_session_reaped", worktree_id=wt_id, reason=reason)
            except Exception:
                pass
        else:
            errors.append({"id": wt_id, "reason": f"kill failed ({reason})"})

    return {"available": True, "reaped": reaped, "skipped": skipped, "errors": errors}


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
_LAUNCHER_SHELL_NAMES = frozenset(
    {
        "pwsh.exe",
        "powershell.exe",
        "python.exe",
        "pwsh",
        "powershell",
        "python",
        "python3",
    }
)
# Command-line substrings that POSITIVELY identify an agent-worktrees launcher
# shell (lowercased match). Nothing is EVER reaped without one of these.
_LAUNCHER_SIGNATURES = ("launch-session", "-m agent_worktrees", "agent_worktrees.__main__")
# Command-line substrings that VETO a reap even when a launcher signature is
# present -- services/daemons, ACP/stdio sessions, and the reaper's own verbs.
_LAUNCHER_REAP_VETOES = (
    "serve-service",
    "agent_dispatch",
    "agent-dispatch",
    "telemetry",
    "status-updater",
    "status-monitor",
    "vault",
    "--acp",
    "--stdio",
    "reap-shells",
    "reap_shells",
    "reap-sessions",
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
_LIVE_DESCENDANT_IMAGES = frozenset(
    {
        "copilot.exe",
        "node.exe",
        "tmux.exe",
        "psmux.exe",
        "copilot",
        "node",
        "tmux",
        "psmux",
    }
)


def _ancestor_chain_intact(
    ppid: int,
    by_pid: dict[int, dict],
    pid_alive: Callable[[int], bool] | None,
) -> bool:
    """Whether ``ppid`` and every ancestor above it, as far as verifiable, is
    still alive -- i.e. whether the process whose parent is ``ppid`` is
    genuinely parented rather than an orphan whose immediate parent happens to
    still be a live (but itself orphaned/stuck) intermediate node.

    Walking past the immediate parent matters for exactly the launcher-shell
    chains this reaper targets: ``agent-worktrees.ps1 -> python -> python ->
    pwsh launch-session.ps1`` stacks several of *this reaper's own* process
    names on top of each other, so an intermediate hop's parent can be dead
    even while the immediate parent (one level down) is still alive.

    Without a real ``pid_alive`` probe (pure/test mode), this degrades to the
    original single-hop snapshot-membership check: a filtered snapshot cannot
    distinguish "not enumerated" from "dead" for anything beyond one hop, so
    walking further would misclassify a live-but-unenumerated terminal as
    dead. With a real probe, walking continues past the immediate parent as
    long as each hop it can still see in ``by_pid`` is confirmed alive,
    stopping (and assuming intact) the moment it runs off the edge of what
    was enumerated -- never the moment it merely can't verify further.
    """
    if pid_alive is None:
        return ppid in by_pid
    cur = ppid
    guard = 0
    while cur > 0 and guard < 128:
        if not bool(pid_alive(cur)):
            return False
        node = by_pid.get(cur)
        if node is None:
            return True  # edge of the snapshot; alive so far, can't see further
        next_ppid = int(node.get("ppid", -1) or -1)
        if next_ppid <= 0 or next_ppid == cur:
            return True  # reached the top of a fully-verified chain
        cur = next_ppid
        guard += 1
    return True


def select_orphan_launcher_shells(
    procs: list[dict],
    *,
    now: float,
    idle_grace_secs: float,
    self_pid: int,
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
        parent_alive = _ancestor_chain_intact(ppid, by_pid, pid_alive)
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
    # No -Filter: candidate selection (name + positive command-line signature)
    # happens in select_orphan_launcher_shells, but the parent-alive check
    # needs to walk the FULL ancestor chain up to the real console host
    # (Windows Terminal/conhost/explorer/etc.) -- a filtered snapshot that
    # only ever contains launcher/witness images can't see past them, so a
    # stacked chain's true root (a dead terminal) would look unverifiable
    # rather than confirmed-dead. See _ancestor_chain_intact.
    ps = (
        "Get-CimInstance Win32_Process | "
        "Select-Object ProcessId,ParentProcessId,Name,CommandLine,SessionId,"
        "@{n='Create';e={try{([DateTimeOffset]$_.CreationDate)"
        ".ToUnixTimeSeconds()}catch{$null}}} | ConvertTo-Json -Compress -Depth 3"
    )
    try:
        out = subprocess.run(
            ["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=30,
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
            procs.append(
                {
                    "pid": int(d.get("ProcessId")),
                    "ppid": int(d.get("ParentProcessId") or -1),
                    "name": (d.get("Name") or "").lower(),
                    "cmdline": d.get("CommandLine") or "",
                    "create_epoch": (float(d["Create"]) if d.get("Create") is not None else None),
                    "session_id": int(d.get("SessionId") or -1),
                }
            )
        except (TypeError, ValueError):
            continue
    return procs


# sync/finalize picker helpers are componentized into worktree_ops_cli.py.


# ═══════════════════════════════════════════════════════════════════════════
# profiles (terminal-profile selection -- the Picker's Profiles grid column)
# ═══════════════════════════════════════════════════════════════════════════


def _resolve_copilot() -> str | None:
    """Resolve a runnable Copilot CLI executable, or ``None``.

    Thin delegate to the shared resolver in ``reconcile`` (used by both this
    update flow and the session-start provision path). Never installs Copilot --
    see ``reconcile.resolve_copilot`` (dotfiles#990).
    """
    from . import reconcile as _rc

    return _rc.resolve_copilot()


def _find_repo_dir() -> Path | None:
    """Find the repo root for the current project.

    Priority order (most specific → least specific):
      1. Running script location (navigate up to git root)
      2. The (assumed) CWD git root (via git rev-parse)
      3. Config anchor (last resort -- may be stale)

    Resolution is from the directory, not ambient env: the former
    ``WORKTREE_REPO`` env fallback has been removed (it was
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
            capture_output=True,
            text=True,
            timeout=5,
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
    machine: str,
    plat: str,
    srcroot: Path | str,
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
    path: Path,
    repo_dir: Path,
    machine: str,
    plat: str,
    project: str,
    default_branch: str = "master",
    *,
    headless: bool = False,
    no_terminal_profile: bool = False,
) -> None:
    """Write the machine-local per-project config YAML.

    Machine-wide fields (srcroot/machine/platform/copilot_profiles) live in the
    global ~/.agent-worktrees/config.yaml; repo settings may live in-repo at
    <anchor>/.agent-worktrees/config.yaml. This file keeps only the project
    marker and machine paths (anchor / worktree_root). It does **not** stamp
    repo-invariant settings such as ``default_branch``: that is owned by the
    repo's in-repo ``.agent-worktrees/config.yaml`` and backfilled from
    ``repos.yaml`` by ``load_config``. Stamping ``default_branch`` here (from the
    ambient system git default, e.g. ``master``, when detection fell back)
    shadowed the correct in-repo value and broke ``create-pr`` against a
    non-existent ``origin/master`` (dotfiles #1090). The ``default_branch``
    parameter is retained for call-site compatibility but is no longer persisted.

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
        if no_terminal_profile
        else ""
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
    remote: origin
"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    output.changed(f"Written config: {path}")


def build_parser() -> argparse.ArgumentParser:
    _load_full_command_surface()
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

    from . import pr_cli, resolve_cli, session_binding_cli, session_inspection_cli, session_tracking_cli

    resolve_cli.add_parsers(sub)

    p = sub.add_parser(
        "execution-leg",
        help="Inspect or mutate the provider-neutral execution leg for a worktree",
    )
    p.add_argument(
        "action",
        choices=["get", "set", "clear", "reserve", "renew", "release"],
    )
    p.add_argument("--worktree-id", required=True)
    p.add_argument("--provider")
    p.add_argument(
        "--state",
        choices=["active", "disposed", "unknown"],
        default="active",
    )
    p.add_argument("--binding-revision", type=int)
    p.add_argument("--blob-file")
    p.add_argument("--if-match-revision", type=int)
    p.add_argument("--operation", choices=["ensure", "dispose"])
    p.add_argument("--reservation-token")
    p.add_argument("--reservation-owner")
    p.add_argument("--reservation-owner-pid", type=int)
    p.add_argument("--reservation-owner-start-time")
    p.add_argument(
        "--lease-seconds",
        type=int,
        default=_EXECUTION_LEG_RESERVATION_DEFAULT_SECONDS,
    )
    p.add_argument("--json", action="store_true")

    finalize_cli.add_parsers(sub)
    pr_state_cli.add_parsers(sub)

    status_cli.add_parsers(sub)

    status_bar_cli.add_parsers(sub)
    status_updater_cli.add_parsers(sub)
    status_monitor_cli.add_parsers(sub)
    status_monitor_runtime.add_parsers(sub)
    pane_lifecycle.register_cli(sub)
    handoff_cli.add_parsers(sub)

    handoff_cli.add_copilot_parser(sub)

    list_cli.add_parsers(sub)
    claims_cli.add_parsers(sub)
    follow_ups_cli.add_parsers(sub)
    session_metadata_cli.add_parsers(sub)

    cleanup_gc_cli.add_parsers(sub)
    reap_cli.add_parsers(sub)
    reclaim_cli.add_parsers(sub)
    worktree_ops_cli.add_parsers(sub)

    picker_profiles_cli.add_parsers(sub)
    maintenance_cli.add_parsers(sub)

    installation_cli.add_parsers(sub)
    update_cli.add_parsers(sub)

    context_cli.add_parsers(sub)
    services_cli.add_parsers(sub)
    repos_cli.add_parsers(sub)
    copilot_identity_cli.add_parsers(sub)
    related_cli.add_parsers(sub)
    git_cli.add_parsers(sub)

    pr_cli.add_parsers(sub)

    # stage-update (background marketplace download; #1430 stage-then-join)
    sp = sub.add_parser(
        "stage-update", help="Background-stage the plugin marketplace update (JSON status)"
    )
    sp.add_argument(
        "--status",
        default=None,
        help="Status file path (defaults to ~/.agent-worktrees/updater-status.json)",
    )
    sp.add_argument("--json", action="store_true", help="Echo the status dict to stdout")

    # reconcile-marketplaces -- retired (#2722); kept as a no-op compatibility
    # shim so a caller still running pre-upgrade script content (an in-flight
    # launch, or a stale deployed marketplace-overrides.ps1/.sh) doesn't
    # hard-fail with "Unknown subcommand" during the upgrade window.
    sp = sub.add_parser(
        "reconcile-marketplaces",
        help="Deprecated no-op (local marketplace source overrides were retired)",
    )
    sp.add_argument("--cwd", default=None, help=argparse.SUPPRESS)
    sp.add_argument("--stdin", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--session-start", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--ensure-ignored", action="store_true", help=argparse.SUPPRESS)
    sp.add_argument("--json", action="store_true", help=argparse.SUPPRESS)

    session_binding_cli.add_parsers(sub)
    session_inspection_cli.add_parsers(sub)

    session_tracking_cli.add_parsers(sub)
    worktree_status_audit.add_parsers(sub)

    # activity -- view the high-level worktree lifecycle log
    sp = sub.add_parser(
        "activity",
        help="View the worktree/session lifecycle activity log",
    )
    sp.add_argument(
        "--since",
        default=None,
        help="Only show events newer than this (e.g. 2d, 12h, "
        "30m, or an ISO date). Default: all retained.",
    )
    sp.add_argument("--worktree-id", default=None, help="Filter to a single worktree id")
    sp.add_argument(
        "--launch-id",
        dest="launch_id",
        default=None,
        help="Filter to a single launch flow (correlation id)",
    )
    sp.add_argument("--event", default=None, help="Filter to a single event type")
    sp.add_argument("--lines", type=int, default=None, help="Show only the most recent N events")
    sp.add_argument(
        "--json", action="store_true", help="Emit one JSON object per line instead of a table"
    )

    # activity-log -- append a single event (launcher/hook hook-invoked)
    sp = sub.add_parser(
        "activity-log",
        help="Append one lifecycle event to the activity log (internal)",
    )
    sp.add_argument("event", help="Event name")
    sp.add_argument("--worktree-id", default=None)
    sp.add_argument("--session-id", default=None)
    sp.add_argument(
        "--launch-id", dest="launch_id", default=None, help="Launch-flow correlation id"
    )
    sp.add_argument("--source", default="launcher")
    sp.add_argument(
        "--field", action="append", default=[], help="Extra context as key=value (repeatable)"
    )

    return parser


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


def _hook_event_timestamp(payload: dict | None) -> str | None:
    """Normalize a hook payload timestamp without using it for ordering."""
    if not payload:
        return None
    value = payload.get("timestamp")
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        seconds = float(value)
        if seconds > 10_000_000_000:
            seconds /= 1000
        try:
            return (
                datetime.fromtimestamp(seconds, tz=timezone.utc)
                .replace(tzinfo=None)
                .strftime("%Y-%m-%dT%H:%M:%S")
            )
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
            return parsed.strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            return None
    return None


def _emit_register_session_result(args: argparse.Namespace, result: dict) -> None:
    text = json.dumps(result, separators=(",", ":"))
    holder = getattr(args, "result_holder", None)
    if isinstance(holder, list):
        holder.append(text)
    else:
        print(text)


# maintenance / doctor surfaces are componentized into maintenance_cli.py.


def _current_session_ids() -> set[str]:
    """Session ids that must never be GC'd: the running agent's own session."""
    sid = os.environ.get("COPILOT_AGENT_SESSION_ID")
    return {sid} if sid else set()


# maintenance / doctor surfaces are componentized into maintenance_cli.py.




# ── Lazy dispatch (agent-cli-lazy-dispatch effort, Phase 1) ────────────────
# {command: (module_name, handler_attr_name)} for every subcommand whose own
# module owns BOTH its argparse subparser (via that module's `add_parsers`,
# or `register_cli` for pane_lifecycle) AND its COMMAND_MAP handler, with no
# naming conflict against any other module. Regenerate by importing
# agent_worktrees.__main__ once (pays the full cost, offline), then for each
# add_parsers-owning module, building a throwaway
# `argparse.ArgumentParser().add_subparsers()` and calling that module's own
# `add_parsers(sub)` to see which command names land in `sub.choices`;
# cross-check module/attr via `COMMAND_MAP[name].__module__`/`__name__`.
# Deliberately excludes: commands with no module-owned parser (parser built
# inline in build_parser() -- activity, activity-log, stage-update,
# reconcile-marketplaces, execution-leg, copilot), commands whose handler is
# native to __main__ itself (resolve, handoff-trace), and commands that never
# reach COMMAND_MAP at all (their own hand-rolled manual-dispatch branches in
# main() -- services, repos, accounts, related, state-root,
# coordination-readiness, config-root, knowledge, git, pr*, worktree, lease,
# fleet, reconcile, delegates, hook -- each of those is fixed to do its own
# lazy `from . import <module>` at its own call site instead).
_LAZY_DISPATCH_TABLE: dict[str, tuple[str, str]] = {
    'anchor-check': ('maintenance_cli', 'cmd_anchor_check'),
    'attribution-audit': ('finalize_cli', 'cmd_attribution_audit'),
    'backfill-sessions': ('maintenance_cli', 'cmd_backfill_sessions'),
    'bind-nudge': ('session_binding_cli', 'cmd_bind_nudge'),
    'bind-session': ('session_binding_cli', 'cmd_bind_session'),
    'claimant-liveness': ('session_metadata_cli', 'cmd_claimant_liveness'),
    'claims': ('claims_cli', 'cmd_claims'),
    'cleanup': ('cleanup_gc_cli', 'cmd_cleanup'),
    'codename-lookup': ('session_metadata_cli', 'cmd_codename_lookup'),
    'conclude-disposable': ('session_tracking_cli', 'cmd_conclude_disposable'),
    'conclude-session': ('session_tracking_cli', 'cmd_conclude_session'),
    'config-migrate': ('maintenance_cli', 'cmd_config_migrate'),
    'create': ('worktree_ops_cli', 'cmd_create'),
    'create-pr': ('finalize_cli', 'cmd_create_pr'),
    'deploy-instructions': ('context_cli', 'cmd_deploy_instructions'),
    'deregister-session': ('session_binding_cli', 'cmd_deregister_session'),
    'dev': ('maintenance_cli', 'cmd_dev'),
    'doctor': ('maintenance_cli', 'cmd_doctor'),
    'effort-focus': ('session_metadata_cli', 'cmd_effort_focus'),
    'embody': ('handoff_cli', 'cmd_embody'),
    'finalize': ('finalize_cli', 'cmd_finalize'),
    'follow-ups': ('follow_ups_cli', 'cmd_follow_ups'),
    'gc': ('cleanup_gc_cli', 'cmd_gc'),
    'get': ('context_cli', 'cmd_get'),
    'handoff-cutover': ('handoff_cli', 'cmd_handoff_cutover'),
    'handoffs-check': ('handoff_cli', 'cmd_handoffs_check'),
    'head-session': ('session_tracking_cli', 'cmd_head_session'),
    'history-digest': ('session_metadata_cli', 'cmd_history_digest'),
    'hygiene': ('maintenance_cli', 'cmd_hygiene'),
    'install': ('installation_cli', 'cmd_install'),
    'install-status': ('context_cli', 'cmd_install_status'),
    'installer-readiness': ('context_cli', 'cmd_installer_readiness'),
    'link-succession': ('session_tracking_cli', 'cmd_link_succession'),
    'list': ('list_cli', 'cmd_list'),
    'list-sessions': ('session_tracking_cli', 'cmd_list_sessions'),
    'machine-context': ('context_cli', 'cmd_machine_context'),
    'mark-complete': ('finalize_cli', 'cmd_mark_complete'),
    'note-handoff': ('session_binding_cli', 'cmd_note_handoff'),
    'pane-create': ('pane_lifecycle', 'cmd_pane_create'),
    'pane-terminate': ('pane_lifecycle', 'cmd_pane_terminate'),
    'picker': ('picker_profiles_cli', 'cmd_picker'),
    'post-exit': ('finalize_cli', 'cmd_post_exit'),
    'pr-complete': ('pr_state_cli', 'cmd_pr_complete'),
    'pr-create': ('finalize_cli', 'cmd_create_pr'),
    'pr-ready': ('pr_state_cli', 'cmd_pr_ready'),
    'pr-status': ('pr_state_cli', 'cmd_pr_status'),
    'pre-launch': ('update_cli', 'cmd_pre_launch'),
    'profiles': ('picker_profiles_cli', 'cmd_profiles'),
    'push-changes': ('finalize_cli', 'cmd_push_changes'),
    'reap-sessions': ('reap_cli', 'cmd_reap_sessions'),
    'reap-shells': ('reap_cli', 'cmd_reap_shells'),
    'recent-messages': ('session_tracking_cli', 'cmd_recent_messages'),
    'reclaim': ('reclaim_cli', 'cmd_reclaim'),
    'reconcile-binstubs': ('maintenance_cli', 'cmd_reconcile_binstubs'),
    'reconcile-plugins': ('update_cli', 'cmd_reconcile_plugins'),
    'reconcile-sessions': ('status_monitor_runtime', 'cmd_reconcile_sessions'),
    'register': ('installation_cli', 'cmd_register'),
    'register-project-entry': ('maintenance_cli', 'cmd_register_project_entry'),
    'register-session': ('session_binding_cli', 'cmd_register_session'),
    'remove-system': ('worktree_ops_cli', 'cmd_remove_system'),
    'remux': ('reclaim_cli', 'cmd_remux'),
    'repair': ('picker_profiles_cli', 'cmd_repair'),
    'restart': ('reclaim_cli', 'cmd_restart'),
    'run': ('worktree_ops_cli', 'cmd_run'),
    'session-binding': ('session_inspection_cli', 'cmd_session_binding'),
    'session-lifecycle': ('session_inspection_cli', 'cmd_session_lifecycle'),
    'session-lineage': ('session_inspection_cli', 'cmd_session_lineage'),
    'session-lock': ('session_metadata_cli', 'cmd_session_lock'),
    'session-recovery': ('session_inspection_cli', 'cmd_session_recovery'),
    'session-role': ('session_metadata_cli', 'cmd_session_role'),
    'session-tail': ('session_tracking_cli', 'cmd_session_tail'),
    'session-transcript': ('session_tracking_cli', 'cmd_session_transcript'),
    'set-pr': ('pr_state_cli', 'cmd_set_pr'),
    'status': ('status_cli', 'cmd_status'),
    'status-context': ('status_bar_cli', 'cmd_status_context'),
    'status-monitor': ('status_monitor_cli', 'cmd_status_monitor'),
    'status-monitor-restart': ('status_monitor_runtime', 'cmd_status_monitor_restart'),
    'status-segment': ('status_bar_cli', 'cmd_status_segment'),
    'status-updater': ('status_updater_cli', 'cmd_status_updater'),
    'sync': ('worktree_ops_cli', 'cmd_sync'),
    'terminal-fragment': ('picker_profiles_cli', 'cmd_terminal_fragment'),
    'uninstall': ('installation_cli', 'cmd_uninstall'),
    'uninstall-plugins': ('update_cli', 'cmd_uninstall_plugins'),
    'update': ('update_cli', 'cmd_update'),
    'validate': ('picker_profiles_cli', 'cmd_validate'),
    'worktree-lineage': ('session_tracking_cli', 'cmd_worktree_lineage'),
    'worktree-status-audit': ('worktree_status_audit', 'cmd_worktree_status_audit'),
    'worktree-status-bundle': ('session_tracking_cli', 'cmd_worktree_status_bundle'),
}

# The complete set of top-level agent-worktrees verb literals -- exactly
# every real argparse subparser choice (build_parser()'s subs[0].choices),
# verified by regenerating _LAZY_DISPATCH_TABLE (see its own comment) and
# diffing against the real parser. This set's only job is
# `front_door_cli._worktrees_verbs()`'s "never treat a real worktrees verb as
# a routable sibling-plugin slug" exclusion check, done BEFORE any
# subcommand-specific dispatch -- it must stay cheap (no imports) or it would
# defeat lazy dispatch for every invocation, not just fast-tracked ones.
# Deliberately excludes manual-dispatch-only verbs that never register an
# argparse subparser choice at all (delegates, fleet, hook, lease, reconcile,
# unregister, worktree) -- several of those (worktree, in particular) rely on
# being ABSENT here so `_canonical_slug()` can still fold the singular
# "worktree" back to this binstub's own "worktrees" alias; adding them here
# would silently break that fold-back (found the hard way, via
# test_router_worktree_singular_folds_back).
_ALL_KNOWN_VERBS: frozenset[str] = frozenset(_LAZY_DISPATCH_TABLE.keys()) | frozenset({
    "services", "repos", "accounts", "copilot-identity", "related", "state-root",
    "coordination-readiness", "config-root", "knowledge", "git",
    "pr-watch", "pr-merge", "pr-research", "pr",
    "activity", "activity-log", "stage-update", "reconcile-marketplaces",
    "execution-leg", "copilot", "resolve", "handoff-trace",
})


# A module is "cluster-free" when every `_core()`-reached name (direct, via
# a var, or via `_core_helper()`) is bound at module level BEFORE the
# deferred `_load_full_command_surface()` block, AND no function it reaches
# transitively (in __main__.py or a sibling module) touches a name bound
# only inside that block. Skips `_ensure_cluster_loaded()` in
# `_dispatch_lazy()` for these modules only. Regenerate/diff via
# `tests/_core_cluster_scan.py`'s `compute_cluster_free_modules()` -- never
# hand-edit, never trust static analysis alone (a regex scan shipped
# `create-pr`'s regression; this transitive class needs runtime exercise
# too -- see the effort's Journal). Adding a module requires running its
# real commands end-to-end, not just `--help`.
_CLUSTER_FREE_MODULES: frozenset[str] = frozenset({
    "claims_cli",
    "context_cli",
    "follow_ups_cli",
    "installation_cli",
    "maintenance_cli",
    "pane_lifecycle",
    "picker_profiles_cli",
    "pr_state_cli",
    "reclaim_cli",
    "session_metadata_cli",
    "session_tracking_cli",
    "status_bar_cli",
    "status_cli",
    "status_monitor_runtime",
    "status_updater_cli",
    "update_cli",
    "worktree_status_audit",
})


def _dispatch_lazy(command: str, args_list: list[str]) -> int:
    """Fast-path dispatch for a module-delegated subcommand.

    Imports and registers only the ONE module that owns `command`'s parser
    and handler (per _LAZY_DISPATCH_TABLE), instead of build_parser()'s full
    eager surface. The resulting parser/help text for this one subcommand is
    identical to what build_parser() would have produced for it.
    """
    module_name, handler_attr = _LAZY_DISPATCH_TABLE[command]
    if module_name not in _CLUSTER_FREE_MODULES:
        _ensure_cluster_loaded()
    module = importlib.import_module(f"{__package__}.{module_name}")
    parser = argparse.ArgumentParser(
        prog="agent-worktrees",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)
    if module_name == "pane_lifecycle":
        module.register_cli(sub)
    else:
        module.add_parsers(sub)
    args = parser.parse_args(args_list)
    # Prefer an already-populated COMMAND_MAP's entry when one exists (real
    # runtime never builds COMMAND_MAP for a fast-tracked command, so this is
    # normally a no-op fallthrough to the module attribute below -- but a
    # caller that already loaded the full surface, or a test that
    # monkeypatches COMMAND_MAP[command] to intercept dispatch, gets the
    # override it expects instead of silently bypassing it).
    command_map = globals().get("COMMAND_MAP")
    handler = command_map.get(command) if command_map else None
    if handler is None:
        handler = getattr(module, handler_attr)
    return handler(args)


# A cluster of CLI submodules cross-reference each other's helpers through
# `_core()` (this __main__ module) rather than importing one another
# directly -- a pre-existing pattern discovered while implementing lazy
# dispatch (see the agent-cli-lazy-dispatch effort's Journal). An initial,
# narrower version of this function tried to enumerate exactly the at-risk
# names via static analysis; that missed a second calling shape
# (`core = _core(); ... core.attr`, not just the direct `_core().attr`
# chain) and shipped a live `AttributeError` in `create-pr` before being
# caught. Given the residual risk that yet another calling shape exists
# somewhere in this ~35-module surface, this now delegates straight to
# `_load_full_command_surface()` -- fully safe by construction (identical to
# every command's pre-lazy-dispatch behavior) -- rather than re-attempting a
# narrower, harder-to-fully-verify subset. This means the fast path below
# still avoids constructing the full ~110-entry argparse tree via
# `build_parser()`, but no longer avoids this module cluster's own import
# cost for `_LAZY_DISPATCH_TABLE` commands. Re-narrowing this safely (Phase
# 1b) needs runtime-exercised verification of every fast-tracked command,
# not static analysis alone.
_CLUSTER_LOADED = False


def _ensure_cluster_loaded() -> None:
    global _CLUSTER_LOADED
    if _CLUSTER_LOADED:
        return
    _load_full_command_surface()
    _CLUSTER_LOADED = True


_FULL_SURFACE_LOADED = False


def _load_full_command_surface() -> None:
    """Import every remaining CLI submodule and build COMMAND_MAP.

    Deferred (previously unconditional module-level code executed on
    every invocation regardless of which single subcommand was
    requested). Called lazily from the COMMAND_MAP-based dispatch site
    and from build_parser() -- a fast-pathed subcommand (see
    _LAZY_DISPATCH_TABLE) never pays this cost. Idempotent.
    """
    global _FULL_SURFACE_LOADED
    if _FULL_SURFACE_LOADED:
        return
    global COMMAND_MAP, CoordinationReadinessFailure, RevalidationResult, _CONCLUDED_STATES, _DESCRIPTOR_STYLE_BG, _ENV_BG, _GET_KEYS, _INSTRUCTION_MARKER, discover_plugin_dir
    global _PR_NAMESPACE, _PluginActivation, _RegisteredPluginTarget, _SEGMENT_STYLE, _WORKTREE_VERBS, _activate_project_for_path, _activate_project_for_worktree_id, _activate_session_binding
    global _all_tracking_dirs, _append_update_if_stale, _apply_assignment_env, _auto_clean_grace_secs, _aw_runtime_home, _background_environment, _bind_nudge_decision, _bind_nudge_should_fire
    global _browse_marketplace_plugins, _build_installer_argv, _build_list_json_payload, _capture_session_title, _claim_from_run_output, _claim_handoff_actor, _claims_add, _claims_cleanup
    global _claims_handoff, _claims_mirror_status, _claims_orphans, _claims_release, _claims_settle, _claims_show, _claims_sweep, _clarify_registration_account
    global _classify_pr_operands, _cleanup_one, _cleanup_per_item_skip_reason, _cleanup_stale_instructions, _cmd_list_stream, _cmd_update_in_plugin, _coordination_readiness_for_owner_ref, _deploy_copilot_instructions
    global _detect_upstream_branch, _effort_focus_output, _effort_orientation, _effort_storage_root, _emit_coordination_rejection, _emit_pr_reminder, _emit_remote_plan_for_env, _ensure_ado_pr_cli
    global _ensure_anchor_ledger, _ensure_status_monitor, _enumerate_launcher_shells_posix, _fast_forward_project_anchors, _filter_list_worktree, _find_installed_plugin_dir, _find_record_for_path, _find_tracking_file
    global _find_tracking_file_by_session, _find_tracking_file_exact, _follow_up_to_json, _follow_ups_add, _follow_ups_dismiss, _follow_ups_record_path, _follow_ups_resolve, _follow_ups_show
    global _gh_env_for_repo, _git_positional, _git_resolve_target, _git_usage, _heal_stale_anchor_if_self_missing, _in_ssh_session, _dispatch_assigned_tasks, _infer_active_github_slug
    global _infer_active_repo_slug, _invocation_update_context, _is_copilot_plugin_name, _journal_run_claim, _launch_profile_selection, _list_error, _list_records_for_args, _load_all_machine_keys
    global _load_remote_machines, _machine_key_for_display, _mirror_terminal_profiles, _module_names, _monitor_claim_handoff_cutover, _monitor_handoff_claim_created_at, _monitor_handoff_claim_path, _monitor_handoff_claim_root
    global _monitor_handoff_claim_segment, _monitor_handoff_claim_staleness, _monitor_list_sessions, _monitor_lock_path, _monitor_mux_set, _monitor_pending_handoff_request, _monitor_read_session_state_handoff, _monitor_registry_dir
    global _monitor_session_state_handoff_path, _new_picker_blocked_by_ssh, _parse_follow_up_refs, _pending_handoff_predecessor_safe, _perform_remux, _picker_profile_choice, _platform_short, _plugin_managed_notice
    global _post_exit_gate, _pr_flow_profile, _pr_merge_now, _pr_merge_print_human, _pr_merge_usage, _pr_parse_repo, _pr_reminder_for, _pr_usage
    global _pr_watch_review_blocking, _pr_watch_usage, _prepare_namespaced_project_state, _print_gc_managed, _print_gc_orphans, _print_gc_shells, _proc_boot_time, _profiles_host
    global _project_for_tracking_file, _project_update_context, _prune_stale_pivots_after_update, _read_monitor_registry, _reconcile_one_runtime, _reconcile_registered_runtimes, _reflect_assignment, _refresh_list_record, _refresh_marketplace
    global _refresh_terminal_profiles, _register_session_for_monitor, _registered_plugin_targets, _related_anchor, _related_conduct, _related_config_source_anchors, _related_current_machine, _related_doctor
    global _related_lookup_anchors, _related_opt, _related_usage, _relocate_active_project_for_worktree, _remove_managed_file, _remove_managed_instruction, _remove_managed_worktree, _remove_monitor_entry
    global _render_doctor_report, _render_dropin_registry_report, _render_related_findings, _render_status_context, _render_status_segment, _repo_for_record, _require_coordination_readiness, _resolve_anchor_owner_ref
    global _resolve_base_repo, _resolve_codename_anywhere, _resolve_environment, _resolve_lease_origin, _resolve_machine_alias, _resolve_mux_worktree_id, _resolve_new, _resolve_owner_ref
    global _resolve_owner_ref_record_path, _resolve_profile, _resolve_remote_default_branch, _resolve_repo_remote, _resolve_resume, _resolve_ssh_alias, _resolve_terminal_install_script, _resolve_worktree_for_read
    global _restart_status_monitor, _restore_before_resume, _revalidate_cleanup_safety, _run_backfill, _run_machine_menu, _run_new_picker, _run_picker_housekeeping, _run_reciprocal_backfill
    global _run_system_menu, _runtime_superseded, _self_entry_present, _session_role, _slot_superseded, _slugify, _spawn_detached, _spawn_status_updater
    global _start_picker_monitor_root, _status_monitor_enabled, _status_segment_json, _succession_header, _sweep_orphans_on_exit, _sync_one_record, _system_cleanup, _system_pause
    global _system_status, _system_update, _system_worktrees_browse, _terminal_fragment_doctor, _tracked_pr_head_evidence, _try_machine_handoff, _uninstall_one_plugin_payload, _update_flags
    global _update_modules, _update_one_plugin_payload, _update_registered_plugins, _valid_monitor_session, _validate_machine_registry, _validate_profile_assignment_config, _warm_list_cache_for_active_project, _windowless_python
    global auto_clean_enabled, claims_cli, cleanup_gc_cli, cmd_accounts_dispatch, cmd_anchor_check, cmd_attribution_audit, cmd_backfill_sessions, cmd_bind_nudge
    global cmd_bind_session, cmd_claimant_liveness, cmd_claims, cmd_cleanup, cmd_codename_lookup, cmd_conclude_disposable, cmd_conclude_session, cmd_config_migrate
    global cmd_config_root_dispatch, cmd_coordination_readiness_dispatch, cmd_copilot_identity_dispatch, cmd_create, cmd_create_pr, cmd_deploy_instructions, cmd_deregister_session, cmd_dev, cmd_doctor
    global cmd_effort_focus, cmd_embody, cmd_finalize, cmd_follow_ups, cmd_gc, cmd_get, cmd_git_dispatch, cmd_git_feature_branch
    global cmd_git_merge_to_feature, cmd_git_sync, cmd_handoff_cutover, cmd_handoff_trace, cmd_handoffs_check, cmd_head_session, cmd_history_digest, cmd_hygiene
    global cmd_install, cmd_install_status, cmd_installer_readiness, cmd_knowledge_dispatch, cmd_link_succession, cmd_list, cmd_list_sessions, cmd_machine_context
    global cmd_mark_complete, cmd_note_handoff, cmd_picker, cmd_post_exit, cmd_pr_complete, cmd_pr_dispatch, cmd_pr_merge_dispatch, cmd_pr_ready
    global cmd_pr_research_dispatch, cmd_pr_status, cmd_pr_watch_dispatch, cmd_pre_launch, cmd_profiles, cmd_push_changes, cmd_reap_sessions, cmd_reap_shells
    global cmd_recent_messages, cmd_reclaim, cmd_reconcile_binstubs, cmd_reconcile_marketplaces, cmd_reconcile_plugins, cmd_reconcile_sessions, cmd_register, cmd_register_project_entry
    global cmd_register_session, cmd_related_dispatch, cmd_remove_system, cmd_remux, cmd_repair, cmd_repos_dispatch, cmd_restart, cmd_run
    global cmd_services_dispatch, cmd_session_binding, cmd_session_lifecycle, cmd_session_lineage, cmd_session_lock, cmd_session_recovery, cmd_session_role
    global cmd_session_tail
    global cmd_session_transcript, cmd_set_pr, cmd_state_root_dispatch, cmd_status, cmd_status_context, cmd_status_monitor, cmd_status_monitor_restart, cmd_status_segment
    global cmd_status_updater, cmd_sync, cmd_terminal_fragment, cmd_uninstall, cmd_uninstall_plugins, cmd_update, cmd_validate, cmd_worktree_dispatch
    global cmd_worktree_lineage, cmd_worktree_status_bundle, context_cli, copilot_identity_cli, finalize_cli, finalize_one, follow_ups_cli, front_door_cli, git_cli
    global handoff_cli, handoff_diagnostics, installation_cli, list_cli, maintenance_cli, picker_profiles_cli, plan_pre_launch, pr_cli
    global pr_state_cli, reap_cli, reap_orphan_launcher_shells, reclaim_cli, reclaim_one, related_cli, repos_cli, resolve_cli
    global resolve_launch_cli, resolve_machine_cli, resolve_picker_cli, resolve_system_cli, services_cli, session_binding_cli, session_inspection_cli, session_metadata_cli
    global session_tracking_cli, status_bar_cli, status_cli, status_monitor_cli, status_monitor_runtime, status_updater_cli, sweep_finished_session_worktrees
    global sweep_managed_worktrees
    global sync_one, terminal_conclusion, update_cli, worktree_ops_cli
    from . import (
        claims_cli,
        cleanup_gc_cli,
        context_cli,
        copilot_identity_cli,
        finalize_cli,
        follow_ups_cli,
        front_door_cli,
        git_cli,
        handoff_cli,
        handoff_diagnostics,
        installation_cli,
        list_cli,
        maintenance_cli,
        picker_profiles_cli,
        pr_cli,
        pr_state_cli,
        reap_cli,
        reclaim_cli,
        related_cli,
        resolve_cli,
        resolve_launch_cli,
        resolve_machine_cli,
        resolve_picker_cli,
        resolve_system_cli,
        repos_cli,
        session_binding_cli,
        session_inspection_cli,
        services_cli,
        session_metadata_cli,
        session_tracking_cli,
        status_bar_cli,
        status_cli,
        status_monitor_cli,
        status_monitor_runtime,
        status_updater_cli,
        update_cli,
        worktree_ops_cli,
    )

    _infer_active_repo_slug = pr_cli._infer_active_repo_slug
    _infer_active_github_slug = pr_cli._infer_active_github_slug
    _pr_watch_usage = pr_cli._pr_watch_usage
    _pr_parse_repo = pr_cli._pr_parse_repo
    _tracked_pr_head_evidence = pr_cli._tracked_pr_head_evidence
    _classify_pr_operands = pr_cli._classify_pr_operands
    _pr_watch_review_blocking = pr_cli._pr_watch_review_blocking
    cmd_pr_watch_dispatch = pr_cli.cmd_pr_watch_dispatch
    _PR_NAMESPACE = pr_cli._PR_NAMESPACE
    _pr_merge_usage = pr_cli._pr_merge_usage
    _pr_merge_print_human = pr_cli._pr_merge_print_human
    _pr_merge_now = pr_cli._pr_merge_now
    cmd_pr_merge_dispatch = pr_cli.cmd_pr_merge_dispatch
    _pr_usage = pr_cli._pr_usage
    cmd_pr_research_dispatch = pr_cli.cmd_pr_research_dispatch
    cmd_pr_dispatch = pr_cli.cmd_pr_dispatch
    _GET_KEYS = context_cli._GET_KEYS
    _resolve_lease_origin = context_cli._resolve_lease_origin
    _pr_reminder_for = context_cli._pr_reminder_for
    _emit_pr_reminder = context_cli._emit_pr_reminder
    cmd_deploy_instructions = context_cli.cmd_deploy_instructions
    cmd_machine_context = context_cli.cmd_machine_context
    cmd_get = context_cli.cmd_get
    cmd_install_status = context_cli.cmd_install_status
    cmd_installer_readiness = context_cli.cmd_installer_readiness
    cmd_reconcile_marketplaces = context_cli.cmd_reconcile_marketplaces
    cmd_state_root_dispatch = context_cli.cmd_state_root_dispatch
    cmd_coordination_readiness_dispatch = context_cli.cmd_coordination_readiness_dispatch
    cmd_config_root_dispatch = context_cli.cmd_config_root_dispatch
    cmd_knowledge_dispatch = context_cli.cmd_knowledge_dispatch
    _resolve_environment = services_cli._resolve_environment
    _WORKTREE_VERBS = services_cli._WORKTREE_VERBS
    _is_copilot_plugin_name = services_cli._is_copilot_plugin_name
    _plugin_managed_notice = services_cli._plugin_managed_notice
    cmd_worktree_dispatch = services_cli.cmd_worktree_dispatch
    cmd_services_dispatch = services_cli.cmd_services_dispatch
    _repo_for_record = tracking._repo_for_record
    _clarify_registration_account = repos_cli._clarify_registration_account
    cmd_repos_dispatch = repos_cli.cmd_repos_dispatch
    cmd_accounts_dispatch = repos_cli.cmd_accounts_dispatch
    cmd_copilot_identity_dispatch = copilot_identity_cli.cmd_copilot_identity_dispatch
    _related_usage = related_cli._related_usage
    _related_opt = related_cli._related_opt
    _related_anchor = related_cli._related_anchor
    _related_current_machine = related_cli._related_current_machine
    _related_config_source_anchors = related_cli._related_config_source_anchors
    _related_lookup_anchors = related_cli._related_lookup_anchors
    _related_doctor = related_cli._related_doctor
    _render_related_findings = related_cli._render_related_findings
    _related_conduct = related_cli._related_conduct
    cmd_related_dispatch = related_cli.cmd_related_dispatch
    _all_tracking_dirs = session_tracking_cli._all_tracking_dirs
    _find_tracking_file = session_tracking_cli._find_tracking_file
    _find_tracking_file_exact = session_tracking_cli._find_tracking_file_exact
    _find_tracking_file_by_session = session_tracking_cli._find_tracking_file_by_session
    _project_for_tracking_file = session_tracking_cli._project_for_tracking_file
    _relocate_active_project_for_worktree = session_tracking_cli._relocate_active_project_for_worktree
    cmd_list_sessions = session_tracking_cli.cmd_list_sessions
    cmd_head_session = session_tracking_cli.cmd_head_session
    cmd_worktree_lineage = session_tracking_cli.cmd_worktree_lineage
    cmd_worktree_status_bundle = session_tracking_cli.cmd_worktree_status_bundle
    cmd_conclude_session = session_tracking_cli.cmd_conclude_session
    cmd_conclude_disposable = session_tracking_cli.cmd_conclude_disposable
    cmd_link_succession = session_tracking_cli.cmd_link_succession
    cmd_session_transcript = session_tracking_cli.cmd_session_transcript
    cmd_session_tail = session_tracking_cli.cmd_session_tail
    cmd_recent_messages = session_tracking_cli.cmd_recent_messages
    terminal_conclusion = session_tracking_cli.terminal_conclusion
    _resolve_worktree_for_read = session_metadata_cli._resolve_worktree_for_read
    _session_role = session_metadata_cli._session_role
    _CONCLUDED_STATES = session_metadata_cli._CONCLUDED_STATES
    _pending_handoff_predecessor_safe = session_metadata_cli._pending_handoff_predecessor_safe
    _succession_header = session_metadata_cli._succession_header
    _effort_orientation = session_metadata_cli._effort_orientation
    cmd_session_role = session_metadata_cli.cmd_session_role
    cmd_history_digest = session_metadata_cli.cmd_history_digest
    _effort_focus_output = session_metadata_cli._effort_focus_output
    _effort_storage_root = session_metadata_cli._effort_storage_root
    cmd_effort_focus = session_metadata_cli.cmd_effort_focus
    cmd_claimant_liveness = session_metadata_cli.cmd_claimant_liveness
    cmd_codename_lookup = session_metadata_cli.cmd_codename_lookup
    cmd_session_lock = session_metadata_cli.cmd_session_lock
    _activate_session_binding = session_binding_cli._activate_session_binding
    _bind_nudge_should_fire = session_binding_cli._bind_nudge_should_fire
    _bind_nudge_decision = session_binding_cli._bind_nudge_decision
    _capture_session_title = session_binding_cli._capture_session_title
    cmd_register_session = session_binding_cli.cmd_register_session
    cmd_deregister_session = session_binding_cli.cmd_deregister_session
    cmd_bind_session = session_binding_cli.cmd_bind_session
    cmd_bind_nudge = session_binding_cli.cmd_bind_nudge
    cmd_note_handoff = session_binding_cli.cmd_note_handoff
    cmd_session_lifecycle = session_inspection_cli.cmd_session_lifecycle
    cmd_session_binding = session_inspection_cli.cmd_session_binding
    cmd_session_recovery = session_inspection_cli.cmd_session_recovery
    cmd_session_lineage = session_inspection_cli.cmd_session_lineage
    _resolve_repo_remote = pr_config._resolve_repo_remote
    _pr_flow_profile = pr_config._pr_flow_profile
    _sweep_orphans_on_exit = finalize_cli._sweep_orphans_on_exit
    _post_exit_gate = finalize_cli._post_exit_gate
    cmd_post_exit = finalize_cli.cmd_post_exit
    cmd_finalize = finalize_cli.cmd_finalize
    cmd_push_changes = finalize_cli.cmd_push_changes
    cmd_create_pr = finalize_cli.cmd_create_pr
    cmd_attribution_audit = finalize_cli.cmd_attribution_audit
    cmd_mark_complete = finalize_cli.cmd_mark_complete
    cmd_set_pr = pr_state_cli.cmd_set_pr
    cmd_pr_ready = pr_state_cli.cmd_pr_ready
    cmd_pr_status = pr_state_cli.cmd_pr_status
    cmd_pr_complete = pr_state_cli.cmd_pr_complete
    cmd_status = status_cli.cmd_status
    cmd_status_monitor = status_monitor_cli.cmd_status_monitor
    _SEGMENT_STYLE = status_bar_cli._SEGMENT_STYLE
    _DESCRIPTOR_STYLE_BG = status_bar_cli._DESCRIPTOR_STYLE_BG
    _find_record_for_path = status_bar_cli._find_record_for_path
    _resolve_remote_default_branch = status_bar_cli._resolve_remote_default_branch
    _detect_upstream_branch = status_bar_cli._detect_upstream_branch
    _render_status_segment = status_bar_cli._render_status_segment
    _status_segment_json = status_bar_cli._status_segment_json
    cmd_status_segment = status_bar_cli.cmd_status_segment
    _platform_short = status_bar_cli._platform_short
    _ENV_BG = status_bar_cli._ENV_BG
    _resolve_machine_alias = status_bar_cli._resolve_machine_alias
    _render_status_context = status_bar_cli._render_status_context
    cmd_status_context = status_bar_cli.cmd_status_context
    _activate_project_for_path = status_updater_cli._activate_project_for_path
    _resolve_mux_worktree_id = status_updater_cli._resolve_mux_worktree_id
    _activate_project_for_worktree_id = status_updater_cli._activate_project_for_worktree_id
    _slot_superseded = status_updater_cli._slot_superseded
    _runtime_superseded = status_updater_cli._runtime_superseded
    _background_environment = status_updater_cli._background_environment
    _spawn_status_updater = status_updater_cli._spawn_status_updater
    cmd_status_updater = status_updater_cli.cmd_status_updater
    _status_monitor_enabled = status_monitor_runtime._status_monitor_enabled
    _aw_runtime_home = status_monitor_runtime._aw_runtime_home
    _monitor_lock_path = status_monitor_runtime._monitor_lock_path
    _monitor_registry_dir = status_monitor_runtime._monitor_registry_dir
    _monitor_handoff_claim_root = status_monitor_runtime._monitor_handoff_claim_root
    _monitor_handoff_claim_stale_seconds = (
        status_monitor_runtime._monitor_handoff_claim_stale_seconds
    )
    _monitor_handoff_claim_segment = status_monitor_runtime._monitor_handoff_claim_segment
    _monitor_handoff_claim_path = status_monitor_runtime._monitor_handoff_claim_path
    _monitor_handoff_claim_created_at = status_monitor_runtime._monitor_handoff_claim_created_at
    _monitor_handoff_claim_staleness = status_monitor_runtime._monitor_handoff_claim_staleness
    _monitor_publish_handoff_cutover_claim = (
        status_monitor_runtime._monitor_publish_handoff_cutover_claim
    )
    _monitor_reclaim_stale_handoff_cutover_claim = (
        status_monitor_runtime._monitor_reclaim_stale_handoff_cutover_claim
    )
    _monitor_claim_handoff_cutover = status_monitor_runtime._monitor_claim_handoff_cutover
    _valid_monitor_session = status_monitor_runtime._valid_monitor_session
    _register_session_for_monitor = status_monitor_runtime._register_session_for_monitor
    _read_monitor_registry = status_monitor_runtime._read_monitor_registry
    _remove_monitor_entry = status_monitor_runtime._remove_monitor_entry
    _windowless_python = status_monitor_runtime._windowless_python
    _spawn_detached = status_monitor_runtime._spawn_detached
    _ensure_status_monitor = status_monitor_runtime._ensure_status_monitor
    _restart_status_monitor = status_monitor_runtime._restart_status_monitor
    cmd_status_monitor_restart = status_monitor_runtime.cmd_status_monitor_restart
    cmd_reconcile_sessions = status_monitor_runtime.cmd_reconcile_sessions
    _monitor_mux_set = status_monitor_runtime._monitor_mux_set
    _monitor_list_sessions = status_monitor_runtime._monitor_list_sessions
    _monitor_session_state_handoff_path = status_monitor_runtime._monitor_session_state_handoff_path
    _monitor_read_session_state_handoff = status_monitor_runtime._monitor_read_session_state_handoff
    _monitor_pending_handoff_request = status_monitor_runtime._monitor_pending_handoff_request
    _load_remote_machines = resolve_machine_cli._load_remote_machines
    _try_machine_handoff = resolve_machine_cli._try_machine_handoff
    _load_all_machine_keys = resolve_machine_cli._load_all_machine_keys
    _new_picker_blocked_by_ssh = resolve_machine_cli._new_picker_blocked_by_ssh
    _in_ssh_session = resolve_machine_cli._in_ssh_session
    _emit_remote_plan_for_env = resolve_machine_cli._emit_remote_plan_for_env
    _resolve_ssh_alias = resolve_machine_cli._resolve_ssh_alias
    _machine_key_for_display = resolve_machine_cli._machine_key_for_display
    _resolve_profile = resolve_launch_cli._resolve_profile
    _picker_profile_choice = resolve_launch_cli._picker_profile_choice
    _validate_profile_assignment_config = resolve_launch_cli._validate_profile_assignment_config
    _launch_profile_selection = resolve_launch_cli._launch_profile_selection
    _apply_assignment_env = resolve_launch_cli._apply_assignment_env
    _reflect_assignment = resolve_launch_cli._reflect_assignment
    _resolve_base_repo = resolve_launch_cli._resolve_base_repo
    _resolve_resume = resolve_launch_cli._resolve_resume
    _resolve_new = resolve_launch_cli._resolve_new
    _run_picker_housekeeping = resolve_picker_cli._run_picker_housekeeping
    _run_new_picker = resolve_picker_cli._run_new_picker
    _start_picker_monitor_root = resolve_picker_cli._start_picker_monitor_root
    _run_machine_menu = resolve_picker_cli._run_machine_menu
    _run_system_menu = resolve_system_cli._run_system_menu
    _system_cleanup = resolve_system_cli._system_cleanup
    _system_update = resolve_system_cli._system_update
    _system_status = resolve_system_cli._system_status
    _system_pause = resolve_system_cli._system_pause
    _system_worktrees_browse = resolve_system_cli._system_worktrees_browse
    _cmd_list_stream = list_cli._cmd_list_stream
    _list_records_for_args = list_cli._list_records_for_args
    _filter_list_worktree = list_cli._filter_list_worktree
    _refresh_list_record = list_cli._refresh_list_record
    _list_error = list_cli._list_error
    _build_list_json_payload = list_cli._build_list_json_payload
    _warm_list_cache_for_active_project = list_cli._warm_list_cache_for_active_project
    cmd_list = list_cli.cmd_list
    _dispatch_assigned_tasks = claims_cli._dispatch_assigned_tasks
    _claim_handoff_actor = claims_cli._claim_handoff_actor
    _require_coordination_readiness = claims_cli._require_coordination_readiness
    _emit_coordination_rejection = claims_cli._emit_coordination_rejection
    CoordinationReadinessFailure = claims_cli.CoordinationReadinessFailure
    _coordination_readiness_for_owner_ref = claims_cli._coordination_readiness_for_owner_ref
    _claims_handoff = claims_cli._claims_handoff
    _resolve_owner_ref_record_path = claims_cli._resolve_owner_ref_record_path
    _claims_add = claims_cli._claims_add
    _claims_mirror_status = claims_cli._claims_mirror_status
    _claims_release = claims_cli._claims_release
    _claims_settle = claims_cli._claims_settle
    _claims_sweep = claims_cli._claims_sweep
    _claims_cleanup = claims_cli._claims_cleanup
    _claims_orphans = claims_cli._claims_orphans
    _claims_show = claims_cli._claims_show
    cmd_claims = claims_cli.cmd_claims
    _parse_follow_up_refs = follow_ups_cli._parse_follow_up_refs
    _follow_up_to_json = follow_ups_cli._follow_up_to_json
    _follow_ups_record_path = follow_ups_cli._follow_ups_record_path
    _follow_ups_show = follow_ups_cli._follow_ups_show
    _follow_ups_add = follow_ups_cli._follow_ups_add
    _follow_ups_resolve = follow_ups_cli._follow_ups_resolve
    _follow_ups_dismiss = follow_ups_cli._follow_ups_dismiss
    cmd_follow_ups = follow_ups_cli.cmd_follow_ups
    discover_plugin_dir = _discover_plugin_dir  # noqa: F841 -- compatibility re-export
    cmd_handoff_cutover = handoff_cli.cmd_handoff_cutover
    _restore_before_resume = handoff_cli._restore_before_resume
    _resolve_codename_anywhere = handoff_cli._resolve_codename_anywhere
    cmd_embody = handoff_cli.cmd_embody
    cmd_handoffs_check = handoff_cli.cmd_handoffs_check
    _enumerate_launcher_shells_posix = reap_cli._enumerate_launcher_shells_posix
    _proc_boot_time = reap_cli._proc_boot_time
    reap_orphan_launcher_shells = reap_cli.reap_orphan_launcher_shells
    cmd_reap_shells = reap_cli.cmd_reap_shells
    _remove_managed_worktree = reap_cli._remove_managed_worktree
    sweep_managed_worktrees = reap_cli.sweep_managed_worktrees
    auto_clean_enabled = reap_cli.auto_clean_enabled
    _auto_clean_grace_secs = reap_cli._auto_clean_grace_secs
    sweep_finished_session_worktrees = reap_cli.sweep_finished_session_worktrees
    cmd_reap_sessions = reap_cli.cmd_reap_sessions
    _slugify = worktree_ops_cli._slugify
    cmd_remove_system = worktree_ops_cli.cmd_remove_system
    cmd_create = worktree_ops_cli.cmd_create
    _resolve_owner_ref = worktree_ops_cli._resolve_owner_ref
    _resolve_anchor_owner_ref = worktree_ops_cli._resolve_anchor_owner_ref
    _claim_from_run_output = worktree_ops_cli._claim_from_run_output
    _ensure_anchor_ledger = worktree_ops_cli._ensure_anchor_ledger
    _journal_run_claim = worktree_ops_cli._journal_run_claim
    cmd_run = worktree_ops_cli.cmd_run
    _sync_one_record = worktree_ops_cli._sync_one_record
    sync_one = worktree_ops_cli.sync_one
    finalize_one = worktree_ops_cli.finalize_one
    cmd_sync = worktree_ops_cli.cmd_sync
    cmd_reclaim = reclaim_cli.cmd_reclaim
    _perform_remux = reclaim_cli._perform_remux
    cmd_remux = reclaim_cli.cmd_remux
    reclaim_one = reclaim_cli.reclaim_one
    cmd_restart = reclaim_cli.cmd_restart
    _cleanup_one = cleanup_gc_cli._cleanup_one
    _cleanup_per_item_skip_reason = cleanup_gc_cli._cleanup_per_item_skip_reason
    RevalidationResult = cleanup_gc_cli.RevalidationResult
    _revalidate_cleanup_safety = cleanup_gc_cli._revalidate_cleanup_safety
    cmd_cleanup = cleanup_gc_cli.cmd_cleanup
    _print_gc_orphans = cleanup_gc_cli._print_gc_orphans
    _print_gc_managed = cleanup_gc_cli._print_gc_managed
    _print_gc_shells = cleanup_gc_cli._print_gc_shells
    cmd_gc = cleanup_gc_cli.cmd_gc
    _profiles_host = picker_profiles_cli._profiles_host
    cmd_profiles = picker_profiles_cli.cmd_profiles
    _mirror_terminal_profiles = picker_profiles_cli._mirror_terminal_profiles
    cmd_terminal_fragment = picker_profiles_cli.cmd_terminal_fragment
    _terminal_fragment_doctor = picker_profiles_cli._terminal_fragment_doctor
    cmd_picker = picker_profiles_cli.cmd_picker
    cmd_validate = picker_profiles_cli.cmd_validate
    _resolve_terminal_install_script = picker_profiles_cli._resolve_terminal_install_script
    _refresh_terminal_profiles = picker_profiles_cli._refresh_terminal_profiles
    cmd_repair = picker_profiles_cli.cmd_repair
    cmd_hygiene = maintenance_cli.cmd_hygiene
    cmd_dev = maintenance_cli.cmd_dev
    _run_reciprocal_backfill = maintenance_cli._run_reciprocal_backfill
    _run_backfill = maintenance_cli._run_backfill
    cmd_backfill_sessions = maintenance_cli.cmd_backfill_sessions
    cmd_doctor = maintenance_cli.cmd_doctor
    _render_doctor_report = maintenance_cli._render_doctor_report
    _render_dropin_registry_report = maintenance_cli._render_dropin_registry_report
    cmd_reconcile_binstubs = maintenance_cli.cmd_reconcile_binstubs
    cmd_register_project_entry = maintenance_cli.cmd_register_project_entry
    cmd_anchor_check = maintenance_cli.cmd_anchor_check
    cmd_config_migrate = maintenance_cli.cmd_config_migrate
    _validate_machine_registry = installation_cli._validate_machine_registry
    _INSTRUCTION_MARKER = installation_cli._INSTRUCTION_MARKER
    _remove_managed_file = installation_cli._remove_managed_file
    _remove_managed_instruction = installation_cli._remove_managed_instruction
    _gh_env_for_repo = installation_cli._gh_env_for_repo
    _deploy_copilot_instructions = installation_cli._deploy_copilot_instructions
    _cleanup_stale_instructions = installation_cli._cleanup_stale_instructions
    _prepare_namespaced_project_state = installation_cli._prepare_namespaced_project_state
    _ensure_ado_pr_cli = installation_cli._ensure_ado_pr_cli
    cmd_install = installation_cli.cmd_install
    cmd_register = installation_cli.cmd_register
    cmd_uninstall = installation_cli.cmd_uninstall
    cmd_update = update_cli.cmd_update
    _update_flags = update_cli._update_flags
    _cmd_update_in_plugin = update_cli._cmd_update_in_plugin
    _project_update_context = update_cli._project_update_context
    _invocation_update_context = update_cli._invocation_update_context
    _refresh_marketplace = update_cli._refresh_marketplace
    _browse_marketplace_plugins = update_cli._browse_marketplace_plugins
    _uninstall_one_plugin_payload = update_cli._uninstall_one_plugin_payload
    _update_one_plugin_payload = update_cli._update_one_plugin_payload
    _PluginActivation = update_cli._PluginActivation
    _RegisteredPluginTarget = update_cli._RegisteredPluginTarget
    _update_registered_plugins = update_cli._update_registered_plugins
    _registered_plugin_targets = update_cli._registered_plugin_targets
    _module_names = update_cli._module_names
    _reconcile_registered_runtimes = update_cli._reconcile_registered_runtimes
    _reconcile_one_runtime = update_cli._reconcile_one_runtime
    _fast_forward_project_anchors = update_cli._fast_forward_project_anchors
    _prune_stale_pivots_after_update = update_cli._prune_stale_pivots_after_update
    _self_entry_present = update_cli._self_entry_present
    _heal_stale_anchor_if_self_missing = update_cli._heal_stale_anchor_if_self_missing
    _update_modules = update_cli._update_modules
    _find_installed_plugin_dir = update_cli._find_installed_plugin_dir
    plan_pre_launch = update_cli.plan_pre_launch
    cmd_pre_launch = update_cli.cmd_pre_launch
    _build_installer_argv = update_cli._build_installer_argv
    _append_update_if_stale = update_cli._append_update_if_stale
    cmd_reconcile_plugins = update_cli.cmd_reconcile_plugins
    cmd_uninstall_plugins = update_cli.cmd_uninstall_plugins
    _git_usage = git_cli._git_usage
    _git_resolve_target = git_cli._git_resolve_target
    _git_positional = git_cli._git_positional
    cmd_git_sync = git_cli.cmd_git_sync
    cmd_git_feature_branch = git_cli.cmd_git_feature_branch
    cmd_git_merge_to_feature = git_cli.cmd_git_merge_to_feature
    cmd_git_dispatch = git_cli.cmd_git_dispatch
    socket = _socket  # noqa: F841 -- compatibility re-export
    svc = _svc  # noqa: F841 -- compatibility re-export

    def cmd_handoff_trace(args):
        return handoff_diagnostics.cmd_handoff_trace(
            args, json_output=_json_output, json_error=_json_error
        )
    COMMAND_MAP = {
        "resolve": cmd_resolve,
        "execution-leg": cmd_execution_leg,
        "post-exit": cmd_post_exit,
        "session-lock": cmd_session_lock,
        "finalize": cmd_finalize,
        "push-changes": cmd_push_changes,
        "create-pr": cmd_create_pr,
        "pr-create": cmd_create_pr,  # pr-* family alias (also rewritten pre-argparse)
        "attribution-audit": cmd_attribution_audit,
        "set-pr": cmd_set_pr,
        "pr-ready": cmd_pr_ready,
        "pr-status": cmd_pr_status,
        "pr-complete": cmd_pr_complete,
        "mark-complete": cmd_mark_complete,
        "status": cmd_status,
        "effort-focus": cmd_effort_focus,
        "status-segment": cmd_status_segment,
        "status-context": cmd_status_context,
        "status-updater": cmd_status_updater,
        "status-monitor": cmd_status_monitor,
        "reconcile-sessions": cmd_reconcile_sessions,
        "status-monitor-restart": cmd_status_monitor_restart,
        "pane-create": pane_lifecycle.cmd_pane_create,
        "pane-terminate": pane_lifecycle.cmd_pane_terminate,
        "handoff-cutover": cmd_handoff_cutover,
        "handoff-trace": cmd_handoff_trace,
        "handoffs-check": cmd_handoffs_check,
        "embody": cmd_embody,
        "copilot": cmd_copilot,
        "list": cmd_list,
        "claims": cmd_claims,
        "follow-ups": cmd_follow_ups,
        "claimant-liveness": cmd_claimant_liveness,
        "codename-lookup": cmd_codename_lookup,
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
        "installer-readiness": cmd_installer_readiness,
        "deploy-instructions": cmd_deploy_instructions,
        "machine-context": cmd_machine_context,
        "get": cmd_get,
        "pre-launch": cmd_pre_launch,
        "stage-update": cmd_stage_update,
        "reconcile-marketplaces": cmd_reconcile_marketplaces,
        "reconcile-plugins": cmd_reconcile_plugins,
        "uninstall-plugins": cmd_uninstall_plugins,
        "reconcile-binstubs": cmd_reconcile_binstubs,
        "register-project-entry": cmd_register_project_entry,
        "dev": cmd_dev,
        "register-session": cmd_register_session,
        "session-lifecycle": cmd_session_lifecycle,
        "deregister-session": cmd_deregister_session,
        "session-binding": cmd_session_binding,
        "session-recovery": cmd_session_recovery,
        "session-lineage": cmd_session_lineage,
        "bind-session": cmd_bind_session,
        "bind-nudge": cmd_bind_nudge,
        "history-digest": cmd_history_digest,
        "note-handoff": cmd_note_handoff,
        "session-role": cmd_session_role,
        "backfill-sessions": cmd_backfill_sessions,
        "doctor": cmd_doctor,
        "hygiene": cmd_hygiene,
        "list-sessions": cmd_list_sessions,
        "head-session": cmd_head_session,
        "worktree-lineage": cmd_worktree_lineage,
        "worktree-status-bundle": cmd_worktree_status_bundle,
        "worktree-status-audit": cmd_worktree_status_audit,
        "conclude-session": cmd_conclude_session,
        "conclude-disposable": cmd_conclude_disposable,
        "link-succession": cmd_link_succession,
        "session-transcript": cmd_session_transcript,
        "session-tail": cmd_session_tail,
        "recent-messages": cmd_recent_messages,
        "anchor-check": cmd_anchor_check,
        "activity": activity.cmd_activity,
        "activity-log": activity.cmd_activity_log,
    }
    _FULL_SURFACE_LOADED = True


def _print_boot_provenance() -> None:
    """Print extended boot provenance checks for migration verification."""
    home = Path.home()
    install = cfg.install_dir()
    checks: list[tuple[str, bool, str]] = []

    # 1. Runtime package identity
    pkg_dir = install / "lib" / "agent_worktrees"
    has_new = pkg_dir.is_dir()
    checks.append(
        (
            "runtime",
            has_new,
            f"agent_worktrees at {pkg_dir}" if has_new else "agent_worktrees package NOT FOUND",
        )
    )

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
    checks.append(
        (
            "no-legacy-pkg",
            not has_old,
            "no worktree_manager remnants"
            if not has_old
            else f"OLD package found: {old_pkg if old_pkg.is_dir() else old_venv_pkg}",
        )
    )

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
    checks.append(
        (
            "session-hook",
            hook_found,
            "bootstrap-check wired in sessionStart"
            if hook_found
            else "sessionStart hook NOT FOUND",
        )
    )

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


# front-door invocation routing is componentized into front_door_cli.py.

# ═══════════════════════════════════════════════════════════════════════════
# git -- collaboration primitives (sync / feature-branch / merge-to-feature)
# ═══════════════════════════════════════════════════════════════════════════


# git collaboration surfaces are componentized into git_cli.py.

def main(argv: list[str] | None = None) -> int:
    global _INVOCATION_CWD
    try:
        _INVOCATION_CWD = Path.cwd()
    except OSError:
        _INVOCATION_CWD = None

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
        _canon = None if args_list[0] in _worktrees_verbs() else _canonical_slug(args_list[0])
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
    _needs_project = not _is_no_project_invocation(args_list)
    _optional_project = (
        bool(args_list) and args_list[0] == "config-root" and _is_no_project_invocation(args_list)
    )

    if _proj:
        _project, _assumed = _resolve_active_project(_proj)
    elif _needs_project or _optional_project:
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

    # No args → the binstub seam (Phase 6 / DQ7 / DQ8). A bare, no-args
    # invocation is the human-facing path. The Manager now owns the transplanted
    # production UX, so prefer it when healthy. The bundled Picker remains the
    # rollback/fallback until the compatibility boundary is removed.
    # Headless projects are never interactive, so they keep their CLI-only
    # summary. Any args route programmatically to the CLI (below), never through
    # this seam. A non-interactive invocation (no attached terminal) is checked
    # ahead of both branches: opening the blocking Manager/Picker UI without a
    # terminal to drive it risks a resident process nothing will ever close
    # (copilot-extensions#2670) -- this takes priority over headless/Manager
    # preference since it is a correctness guard, not a UX preference.
    if not args_list:
        return dispatch_bare_invocation(has_project)

    # A project-requiring subcommand without any project context → balk
    # helpfully instead of raising a bare RuntimeError deep in load_config.
    if not has_project and not _is_no_project_invocation(args_list):
        return cmd_help_unrouted(requested=args_list[0])

    # --version / -V → print version + build info + boot provenance
    if args_list[0] in ("--version", "-V"):
        try:
            from ._build_info import BUILD_INFO
        except ImportError:
            BUILD_INFO = {"version": "?.?.?", "commit": "unknown", "build_timestamp": "unknown"}
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

    # Fast path (checked before manual-dispatch verbs + the blanket
    # _ensure_cluster_loaded() call below, Phase 1b): import only the
    # module owning this subcommand instead of the full eager surface, so a
    # _CLUSTER_FREE_MODULES command (see _dispatch_lazy()) skips that cost.
    if args_list[0] in _LAZY_DISPATCH_TABLE:
        try:
            return _dispatch_lazy(args_list[0], args_list)
        except (
            FileNotFoundError, ValueError, inst.BinstubOwnershipError,
            codename_tracking.CodenameAttributionPolicyError,
        ) as e:
            output.err(str(e))
            return 1
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # Manual-dispatch verbs + the COMMAND_MAP fallback may reach the
    # cross-referencing CLI-submodule cluster -- resolve it once.
    _ensure_cluster_loaded()

    # Services uses manual dispatch for passthrough support --
    # argparse can't handle "unknown subcommand = service name".
    if args_list[0] == "services":
        from . import services_cli

        try:
            return services_cli.cmd_services_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # Repos uses manual dispatch for subcommand flexibility.
    if args_list[0] == "repos":
        from . import repos_cli

        try:
            return repos_cli.cmd_repos_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # Accounts (gh identity catalog) -- manual dispatch.
    if args_list[0] == "accounts":
        from . import repos_cli

        try:
            return repos_cli.cmd_accounts_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # Copilot identity (Copilot CLI's own login, distinct from gh) -- manual
    # dispatch, mirroring 'repos'/'accounts'.
    if args_list[0] == "copilot-identity":
        from . import copilot_identity_cli

        try:
            return copilot_identity_cli.cmd_copilot_identity_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # Related (per-project related repos) -- manual dispatch.
    if args_list[0] == "related":
        from . import related_cli

        try:
            return related_cli.cmd_related_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # state-root (resolve where efforts/visions/logs are written) -- manual
    # dispatch (needs project context; see cmd_state_root_dispatch).
    if args_list[0] == "state-root":
        _ensure_cluster_loaded()
        from . import context_cli

        try:
            return context_cli.cmd_state_root_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # coordination-readiness -- JSON preflight for claim-producing peers.
    if args_list[0] == "coordination-readiness":
        _ensure_cluster_loaded()
        from . import context_cli

        try:
            return context_cli.cmd_coordination_readiness_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # config-root (guarded machine-local setup destination) -- manual dispatch.
    if args_list[0] == "config-root":
        _ensure_cluster_loaded()
        from . import context_cli

        try:
            return context_cli.cmd_config_root_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # knowledge (paired private-repo management) -- manual dispatch.
    if args_list[0] == "knowledge":
        _ensure_cluster_loaded()
        from . import context_cli

        try:
            return context_cli.cmd_knowledge_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # git -- collaboration primitives (manual dispatch).
    if args_list[0] == "git":
        from . import git_cli

        try:
            return git_cli.cmd_git_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # pr-watch -- provider-generic PR review-callback watcher (manual dispatch:
    # sub-subcommands wait/cursor + owner/name + pr).
    if args_list[0] == "pr-watch":
        from . import pr_cli

        try:
            return pr_cli.cmd_pr_watch_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # pr-merge -- signal merge consent (apply the consent label). Manual
    # dispatch (owner/name + pr | --all).
    if args_list[0] == "pr-merge":
        from . import pr_cli

        try:
            return pr_cli.cmd_pr_merge_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # pr-research -- read the repo's live provider settings -> policy matrix
    # (#225). Read-only manual dispatch (owner/name).
    if args_list[0] == "pr-research":
        from . import pr_cli

        try:
            return pr_cli.cmd_pr_research_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # pr <verb> namespace -- sugar over the flat pr-* command family.
    if args_list[0] == "pr":
        from . import pr_cli

        try:
            return pr_cli.cmd_pr_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # Worktree namespace -- groups the non-launching lifecycle verbs as a
    # discoverable alias over the existing top-level commands.
    if args_list[0] == "worktree":
        from . import services_cli

        try:
            return services_cli.cmd_worktree_dispatch(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # lease/fleet/reconcile/delegates -- self-contained argparse verbs manually
    # dispatched here (each owns its own module) rather than growing this
    # already-at-ceiling tree. fleet/reconcile/delegates are Phase 1/2/3 of
    # #2740 (aggregate list, out-of-band PR reconcile, delegate graph/sweep).
    _manual_verb_modules = {
        "lease": ("lease_cli", "run_lease"),
        "fleet": ("list_views_cli", "run_fleet"),
        "reconcile": ("reconcile_cli", "run_reconcile"),
        "delegates": ("delegate_cli", "run_delegates"),
    }
    if args_list[0] in _manual_verb_modules:
        mod_name, func_name = _manual_verb_modules[args_list[0]]
        mod = __import__(f"{__package__}.{mod_name}", fromlist=[mod_name])
        try:
            return getattr(mod, func_name)(args_list[1:])
        except KeyboardInterrupt:
            print("\nCancelled.")
            return 130

    # Hook guardrails (manual dispatch: hook name + git passthrough args).
    if args_list[0] == "hook":
        from . import hooks as _hooks

        name = args_list[1] if len(args_list) > 1 else ""
        return _hooks.run_hook(name, args_list[2:])

    # (_LAZY_DISPATCH_TABLE fast path is checked earlier, before the
    # blanket _ensure_cluster_loaded() call above.)

    # First arg is a known subcommand → parse normally
    _load_full_command_surface()
    if args_list[0] in COMMAND_MAP:
        parser = build_parser()
        args = parser.parse_args(args_list)
        handler = COMMAND_MAP.get(args.command)
        if not handler:
            parser.print_help()
            return 1
        try:
            return handler(args)
        except (
            FileNotFoundError, ValueError, inst.BinstubOwnershipError,
            codename_tracking.CodenameAttributionPolicyError,
        ) as e:
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


def console_entry() -> None:
    """Entry point for both the ``python -m agent_worktrees`` guard below and
    the installed ``agent-worktrees`` console script (`pyproject.toml`'s
    ``[project.scripts]``) -- the generated script wrapper calls this
    directly, bypassing the ``__main__`` guard, so routing both through here
    is required for the shutdown-crash workaround to cover the installed
    command too.
    """
    from ._shutdown_exit import run_and_exit

    run_and_exit(main)


def __getattr__(name: str):
    """PEP 562 module fallback: lazily-loaded attributes for external importers.

    `_load_full_command_surface()` populates ~35 submodules' worth of
    cross-referenced names only when this CLI's own dispatch runs
    (`build_parser()` or the `COMMAND_MAP` fallback) or when
    `_ensure_cluster_loaded()`/`_dispatch_lazy()` runs it explicitly for a
    fast-tracked command. A consumer that imports this module directly as a
    library (`importlib.import_module("agent_worktrees.__main__")`) and
    touches one of those names *before* any of those code paths have run
    never triggers the load -- this happened in production for
    `worktree-manager`'s Picker (ThomasMichon/copilot-extensions#3319),
    which was fixed at its own call site, but this is a defensive backstop
    for any other/future external consumer that reaches this module the same
    way. Triggers the full load on the FIRST failed attribute lookup only
    (cheap in the common case: a genuine typo/AttributeError still pays this
    once, then raises as normal).
    """
    if not _FULL_SURFACE_LOADED:
        _load_full_command_surface()
        try:
            return globals()[name]
        except KeyError:
            pass
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


if __name__ == "__main__":
    console_entry()
