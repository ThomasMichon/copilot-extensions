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
            pr_ops._reconcile_active_pr(rec, config, best_effort=True)
        except Exception:
            continue
        new_active = rec.active_pr()
        if new_active is not None and tracking._pr_is_terminal(new_active):
            changed += 1
    return changed


def reconcile_bound_live() -> int:
    """Off-hot-path: reconcile each local worktree's cached ``bound_live`` AND
    ``mux_live`` signals against the authoritative machine-wide scans (#4057 /
    #1416 / dotfiles#1205).

    Despite the name (kept for its call sites), this sweep now reconciles BOTH
    liveness hints in one records pass: the bound-Copilot signal (below) and the
    ``mux_live`` signal. ``mux_live`` is otherwise only stamped at discrete
    lifecycle events, so a ``wt-<id>`` mux that appears AFTER its last event-time
    stamp (e.g. a psmux startup-restore landing minutes after resume) would
    persist a stale ``false`` for a genuinely live mux; recomputing it here --
    mirroring bound liveness -- keeps it honest (dotfiles#1205).

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
    # Batch the wt-<id> mux presence check for the SAME record set so we can
    # reconcile the cached ``mux_live`` hint here too. ``mux_live`` is otherwise
    # only stamped at discrete lifecycle events (launch/Enter verify, Stop,
    # confirmed teardown), so a mux that appears AFTER its last event-time stamp
    # -- e.g. a psmux startup-restore that lands seconds/minutes after resume --
    # is never re-observed and persists a stale ``false`` for a genuinely live
    # mux (dotfiles#1205). Reconciling it on this off-hot-path sweep, mirroring
    # bound liveness, closes that gap. Mux presence is a definitive local check
    # (the session exists or it does not), so -- unlike the bound scan -- there
    # is no Unknown state to guard against.
    try:
        mux_map = sessions.mux_status_many([r.worktree_id for r in records])
    except Exception:
        mux_map = {}
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
        # mux liveness (mirror of the bound transition logic): observe a mux
        # that appeared or vanished since the last event-time stamp. A live mux
        # renews its freshness (throttled); a false->true or true->false is a
        # real ACTIVE-visibility transition, so it counts toward ``changed`` and
        # reloads the picker. A mux that is absent and was already false is left
        # untouched -- no YAML churn on the fleet's idle records.
        info = mux_map.get(rec.worktree_id)
        mux_present = bool(info and getattr(info, "exists", False))
        if mux_present:
            if rec.mux_live is not True:
                changed += 1
            tracking.stamp_mux_live(
                rec.worktree_id, True, refresh=True, sync=True)
        elif rec.mux_live is True:
            tracking.stamp_mux_live(rec.worktree_id, False, sync=True)
            changed += 1
    return changed


def _overlay_cached_state(raw: dict, rec) -> None:
    """Overlay a worktree's cached session-render state onto its cache-only row.

    picker-cache-first-paint (dotfiles#948): the first-paint pass reads no
    EXPENSIVE live data (no events.jsonl, no process-table scan, no git
    classify), so turns/state/summary come from the record's session-render
    cache (stamped by a prior populate / Refresh). Two exceptions are resolved
    live because they are CHEAP and must be correct immediately:

    * **ACTIVE** -- a live ``inuse.<pid>.lock`` in one of this worktree's
      registered session folders (``sessions.worktree_has_live_session`` -- a
      targeted glob + pid-check, not a machine scan) means a running Copilot.
      ACTIVE wins over ANY cached/terminal/unknown state, because git's own
      ``active`` derivation needs exactly this lock scan, so a running worktree
      would otherwise render ``?``/stale until the Pass 2 classify.
    * The cached ``bound_live`` hint (``session_bound_live`` -- the
      cwd-independent #1416 bare-resume signal, read cache-only by
      ``_worktree_to_dict``) is the second live signal.

    A worktree with neither live signal NOR any cached state (both
    ``session_turns`` and ``git_state`` absent) renders **Unknown** -- a Refresh
    or the follow-up populate fills it.
    """
    try:
        live = sessions.worktree_has_live_session(rec)
    except Exception:
        live = False
    live = live or (raw.get("session_bound_live") is True)

    if rec.session_turns is not None:
        raw["turn_count"] = rec.session_turns
    if rec.git_state:
        raw["state"] = rec.git_state
    if rec.session_summary and not (raw.get("title") and raw["title"] != "null"):
        raw["title"] = rec.session_summary

    if live:
        # A live bound Copilot -> ACTIVE, authoritative in the fast pass (wins
        # over a cached terminal/unknown state). Also surface the lock signal so
        # the Sess column + Reclaim/Stop gating read it.
        raw["session_lock_live"] = True
        raw["state"] = "active"
    elif rec.session_turns is None and not rec.git_state:
        raw["state"] = "unknown"


def load(machine: str | None = None, env: str | None = None,
         *, classify: bool = True):
    """Normalized records for this machine's worktrees (tracking + classify).

    *machine*/*env* default to this host's identity (``LOCAL``). The SSH source
    overrides them so the local machine's rows carry its ``machines.yaml``
    display name and env label, matching the multi-machine tab descriptors.

    ``classify`` gates the expensive per-worktree git classification
    (``_classify_records`` -- ~5 git spawns each) **and** the live session/
    process gather. When ``False`` this returns a **cache-only** provisional
    listing (picker-cache-first-paint, dotfiles#948): it reads ONLY the
    per-worktree state files -- turns/state/summary from the session-render cache
    (``session_turns``/``git_state``/``session_summary``, stamped by a prior
    populate) plus the cheap cached bound-Copilot hint -- with NO ``events.jsonl``
    read, NO machine-wide process/lock scan, and NO mux probe, so the picker
    paints immediately. A worktree the cache has never populated renders
    **Unknown**; the loader then fills authoritative live+git state in with a
    second ``classify=True`` pass (which also writes the cache back). When
    ``True`` (default) rows carry the canonical git-derived ``state`` and the
    full live session/mux gather.
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
    machine = machine if machine is not None else LOCAL[0]
    env = env if env is not None else LOCAL[1]

    if not classify:
        # PASS 1 -- cache-only first paint (dotfiles#948). Read ONLY the state
        # files: no scan_sessions_fast (events.jsonl), no bare_orphan/
        # resolve_bound (process table), no mux probe, no git classify. Rows
        # render from the session-render cache; never-populated -> Unknown.
        out = []
        for rec in records:
            raw = _worktree_to_dict(rec)
            _overlay_cached_state(raw, rec)
            out.append(derive.norm(raw, machine, env))
        return out

    session_ctx = sessions.scan_sessions_fast(records)
    # #93: one machine-wide pass to find worktrees hosting a bare (un-muxed)
    # bound Copilot, so each row can be marked as an orphan. Best-effort: a
    # process-enumeration hiccup must never break the picker render.
    try:
        bare_orphan_wts = reclaim.bare_orphan_worktree_ids()
    except Exception:
        bare_orphan_wts = set()
    # #4272 bridge-lock: worktrees with a live bridge-owned Copilot, read
    # file-first from bridge.lock (the cheap, cwd-independent bare-session
    # signal). Best-effort: a scan hiccup must never break the render.
    try:
        bridge_live_wts = reclaim.live_bridge_worktrees()
    except Exception:
        bridge_live_wts = set()
    mux_map = sessions.mux_status_many([r.worktree_id for r in records])
    state_map = _classify_records(records, session_ctx) if classify else {}
    out = []
    for rec in records:
        raw = _worktree_to_dict(
            rec, mux_info=mux_map.get(rec.worktree_id),
            session_ctx=session_ctx, state_info=state_map.get(rec.worktree_id),
            bare_orphan_wts=bare_orphan_wts,
            bridge_live_wts=bridge_live_wts,
        )
        # picker-cache-first-paint (dotfiles#948) write-back: on the
        # authoritative (classify=True) populate pass, stamp the session-render
        # cache back onto the record so the NEXT cache-only first paint reads
        # turns/state/summary without touching events.jsonl or scanning
        # processes. Best-effort: a stamp hiccup must never break the render.
        if classify:
            _stamp_from_raw(rec, raw, session_ctx)
        out.append(derive.norm(raw, machine, env))
    return out


def _stamp_from_raw(rec, raw: dict, session_ctx) -> None:
    """Write the session-render cache back from a freshly gathered ``raw`` row.

    picker-cache-first-paint (dotfiles#948): shared by the classify populate and
    the per-row :func:`refresh_one` -- persists ``session_turns``/``git_state``/
    ``session_summary`` so the next cache-only first paint reads them without any
    ``events.jsonl`` or process scan. Best-effort: never raises.
    """
    try:
        _norm = sessions._normalize_path(rec.worktree_path)
        tracking.stamp_session_state(
            rec.worktree_id,
            turns=int(raw.get("turn_count", 0)),
            summary=session_ctx.latest_summary.get(_norm),
            git_state=raw.get("state"),
        )
    except Exception:
        pass


def refresh_one(worktree_id: str, machine: str | None = None,
                env: str | None = None):
    """Live-gather + write-back for ONE worktree -- the picker's per-row Refresh.

    picker-cache-first-paint (dotfiles#948): runs the same authoritative gather
    the classify populate does (session scan + git classify + mux + bound/bridge
    liveness) but scoped to a single worktree, stamps the session-render cache,
    and returns the freshly normalized row -- so an **Unknown** or stale row can
    be populated on demand without a full-fleet reload. Returns ``None`` when the
    worktree's record or checkout is gone. Never raises for a missing record;
    local machine only (a remote row refreshes on its owning machine).
    """
    from ..__main__ import (
        _build_active_paths, _classify_one_record, _worktree_to_dict)

    derive.NOW = _dt.datetime.now()
    yaml_path = cfg.tracking_dir() / f"{worktree_id}.yaml"
    if not yaml_path.exists():
        return None
    rec = tracking.load_record(yaml_path)
    if not (rec and rec.worktree_path and Path(rec.worktree_path).exists()
            and (Path(rec.worktree_path) / ".git").exists()):
        return None
    machine = machine if machine is not None else LOCAL[0]
    env = env if env is not None else LOCAL[1]
    session_ctx = sessions.scan_sessions_fast([rec])
    try:
        bare_orphan_wts = reclaim.bare_orphan_worktree_ids()
    except Exception:
        bare_orphan_wts = set()
    try:
        bridge_live_wts = reclaim.live_bridge_worktrees()
    except Exception:
        bridge_live_wts = set()
    mux_map = sessions.mux_status_many([rec.worktree_id])
    config = cfg.load_config()
    active_paths = _build_active_paths([rec], session_ctx)
    info = _classify_one_record(
        rec, repo=config.default_repo, active_paths=active_paths,
        session_ctx=session_ctx)
    raw = _worktree_to_dict(
        rec, mux_info=mux_map.get(rec.worktree_id), session_ctx=session_ctx,
        state_info=info, bare_orphan_wts=bare_orphan_wts,
        bridge_live_wts=bridge_live_wts,
    )
    _stamp_from_raw(rec, raw, session_ctx)
    return derive.norm(raw, machine, env)

