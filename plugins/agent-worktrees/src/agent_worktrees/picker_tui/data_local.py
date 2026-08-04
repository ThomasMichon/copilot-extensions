#!/usr/bin/env python3
"""Real local data source for the Worktree Picker TUI.

Exposes the same surface the engine's prototype sources did
(``LOCAL`` / ``LOCAL_LABEL`` / ``machines()`` / ``load()`` / ``bucket`` /
``for_machine``), but backed by the real tracking store + git classification
on *this* machine. Slice 1 of the port covers the local machine only; remote
machines arrive via an SSH source + async loader in a later slice.
"""
from __future__ import annotations

import datetime as _dt
import socket
from pathlib import Path

from .. import config as cfg
from .. import reclaim, sessions, tracking
from . import derive, roster

bucket = derive.bucket
for_machine = derive.for_machine
host_cols = roster.host_cols
target_envs = roster.target_envs

_ENV_LABEL = {"windows": "Win", "wsl": "WSL", "linux": "Linux"}


def _local_identity() -> tuple[str, str]:
    host = socket.gethostname().split(".")[0]
    plat = cfg.detect_platform()
    return host, _ENV_LABEL.get(plat, plat.title())


LOCAL = _local_identity()
LOCAL_LABEL = f"{LOCAL[0]} · {LOCAL[1].lower()}"


def _project_repo() -> tuple[str, str]:
    """``(repo name, default branch)`` for the active project's default repo.

    Data-backs the picker top bar's repo/branch segments (formerly hardcoded to
    ``aperture-labs`` / ``master``). Returns empty strings when config can't be
    resolved, so the engine simply drops the segment rather than showing a
    fabricated value.
    """
    try:
        config = cfg.load_config()
        return config.repo_name, config.default_repo.default_branch
    except Exception:
        return "", ""


REPO, BRANCH = _project_repo()


def machines():
    """Machine-tab descriptors. Slice 1: the local machine only."""
    m, e = LOCAL
    return [(f"{m} {e}", m, e, True)]


def load_profile_column(machine, env):
    """Read a host's terminal-profile column (local in-process / remote SSH)."""
    from . import profiles_io
    return profiles_io.load_column(machine, env)


def apply_profile_column(machine, env, sels, *, mirror=True):
    """Persist a host's terminal-profile column. Returns ``(ok, detail)``."""
    from . import profiles_io
    return profiles_io.apply_column(machine, env, sels, mirror=mirror)


def reconcile_prs() -> int:
    """Best-effort: reconcile this machine's worktrees' active PR state against
    the provider, writing merged/closed back into the tracking YAML (#1423).

    A PR merged externally (the ``auto-merge`` label / provider API, bypassing
    ``finalize``/``pr-status``) leaves the local record stale at ``open``, so the
    Picker shows already-merged worktrees as having open PRs. This reconciles
    every local worktree whose active PR is still non-terminal and persists the
    resolved state, so the next render is honest.

    Returns the count of records whose active PR moved to a terminal state.
    Never raises: an unconfigured/unreachable provider leaves state untouched.
    Local machine only -- remote worktrees reconcile on their owning machine.
    """
    from .. import pr_ops

    try:
        config = cfg.load_config()
        tracking_path = cfg.tracking_dir()
        plat = cfg.detect_platform()
        records = tracking.list_records(tracking_path, platform_filter=plat)
    except Exception:
        return 0
    changed = 0
    for rec in records:
        active = rec.active_pr()
        if active is None or active.number is None:
            continue
        if tracking._pr_is_terminal(active):
            continue
        try:
            pr_ops._reconcile_active_pr(rec, config)
        except Exception:
            continue
        new_active = rec.active_pr()
        if new_active is not None and tracking._pr_is_terminal(new_active):
            changed += 1
    return changed


def reconcile_bound_live() -> int:
    """Off-hot-path: reconcile each local worktree's cached ``bound_live`` signal
    against the authoritative machine-wide bound-Copilot scan (#4057 / #1416).

    A bare-resumed Copilot (cwd=home) is invisible to BOTH the registered-session
    lock scan (its session was never registered under the worktree) and the mux
    batch (a bare Copilot has no mux), so its worktree wrongly renders non-ACTIVE
    (#1416). This reconciler resolves every live bound Copilot on the machine via
    :func:`reclaim.resolve_bound_copilots` -- cwd-independent, and NOT
    self-excluding (unlike ``bare_orphan_worktree_ids``, so it also counts *this*
    session's own worktree and every mux-homed one) -- and stamps each affected
    worktree's cached ``bound_live`` so a follow-up populate can surface a
    bare-resumed session in the Active section from cache alone.

    TRI-STATE, minimal-churn: a live worktree is stamped ``True`` (refresh, so a
    steadily-live session's hint never ages out); a worktree that was ``True`` but
    is no longer bound is stamped ``False`` (the session-ended transition). A
    never-bound worktree is left untouched (``None`` = Unknown, NEVER persisted),
    so the fleet's idle YAMLs are not rewritten. When the scan itself fails
    (Unknown), nothing is stamped. Never raises; local machine only -- remote
    worktrees reconcile on their owning machine. Returns the count of records
    whose CONSUMER-VISIBLE liveness (fresh ``bound_live=True`` vs not) flipped, so
    a nonzero result reloads the picker -- including a still-live worktree whose
    hint had aged past the populate TTL (renewing it must re-surface it ACTIVE,
    not silently leave it non-active until the next poll).

    A bound Copilot the scan could not attribute to a worktree (``worktree_id``
    None -- a transient attribution failure, not proof of death) suppresses ALL
    negative transitions this pass: a genuinely-gone positive still expires via
    the freshness TTL, but a momentarily-unattributable live session is never
    wrongly cleared to ``False``.
    """
    try:
        tracking_path = cfg.tracking_dir()
        plat = cfg.detect_platform()
        records = tracking.list_records(tracking_path, platform_filter=plat)
    except Exception:
        return 0
    records = [
        r for r in records if r.worktree_path and Path(r.worktree_path).exists()
    ]
    if not records:
        return 0
    try:
        bound = reclaim.resolve_bound_copilots()
    except Exception:
        return 0  # Unknown -- never persist
    # Lazy import (avoids a picker_tui <-> __main__ cycle): the SAME fresh-hint
    # test the populate path uses, so "changed" tracks true consumer visibility.
    from ..__main__ import _fresh_bound_live_hint

    live_ids = {b.get("worktree_id") for b in bound if b.get("worktree_id")}
    # An unattributable live binding -> some worktree's liveness is Unknown this
    # pass; hold off on clearing positives so a transient miss can't flap a live
    # session to non-ACTIVE.
    had_unresolved = any(b.get("worktree_id") is None for b in bound)
    changed = 0
    for rec in records:
        was_visible = _fresh_bound_live_hint(rec) is True
        if rec.worktree_id in live_ids:
            tracking.stamp_bound_live(rec.worktree_id, True, refresh=True)
            if not was_visible:
                changed += 1  # became (or re-freshened into) ACTIVE-visible
        elif rec.bound_live is True and not had_unresolved:
            # True -> False: the bound session ended. Clear it (a real
            # transition; idle worktrees that were never bound stay untouched).
            tracking.stamp_bound_live(rec.worktree_id, False)
            if was_visible:
                changed += 1
    return changed


def load(machine: str | None = None, env: str | None = None,
         *, classify: bool = True):
    """Normalized records for this machine's worktrees (tracking + classify).

    *machine*/*env* default to this host's identity (``LOCAL``). The SSH source
    overrides them so the local machine's rows carry its ``machines.yaml``
    display name and env label, matching the multi-machine tab descriptors.

    ``classify`` gates the expensive per-worktree git classification
    (``_classify_records`` -- ~5 git spawns each). When ``False`` this returns a
    **fast** provisional listing (tracking + sessions + mux only, no git), whose
    rows the picker renders immediately via ``derive``'s classification-absent
    heuristic (status + turns + PR); the loader then fills authoritative git
    states in with a second ``classify=True`` pass. When ``True`` (default) rows
    carry the canonical git-derived ``state``.
    """
    # Lazy import to avoid a picker_tui <-> __main__ import cycle.
    from ..__main__ import _classify_records, _worktree_to_dict

    derive.NOW = _dt.datetime.now()
    tracking_path = cfg.tracking_dir()
    plat = cfg.detect_platform()
    records = tracking.list_records(tracking_path, platform_filter=plat)
    records = [
        r for r in records
        if r.worktree_path
        and Path(r.worktree_path).exists()
        and (Path(r.worktree_path) / ".git").exists()
    ]
    if not records:
        return []
    session_ctx = sessions.scan_sessions_fast(records)
    # #93: one machine-wide pass to find worktrees hosting a bare (un-muxed)
    # bound Copilot, so each row can be marked as an orphan. Best-effort: a
    # process-enumeration hiccup must never break the picker render.
    try:
        bare_orphan_wts = reclaim.bare_orphan_worktree_ids()
    except Exception:
        bare_orphan_wts = set()
    mux_map = sessions.mux_status_many([r.worktree_id for r in records])
    state_map = _classify_records(records, session_ctx) if classify else {}
    machine = machine if machine is not None else LOCAL[0]
    env = env if env is not None else LOCAL[1]
    out = []
    for rec in records:
        raw = _worktree_to_dict(
            rec, mux_info=mux_map.get(rec.worktree_id),
            session_ctx=session_ctx, state_info=state_map.get(rec.worktree_id),
            bare_orphan_wts=bare_orphan_wts,
        )
        out.append(derive.norm(raw, machine, env))
    return out
