"""Group C local reconcile-and-stamp CLI surface for the production Picker."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from . import config as cfg, pr_ops, reclaim, sessions, tracking
from . import output


def add_parsers(sub) -> None:
    parser = sub.add_parser(
        "picker-reconcile-local",
        help="Run the Group C local reconcile-and-stamp sweep as one JSON batch",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit the versioned reconcile payload as JSON",
    )
    parser.add_argument(
        "--worktree-id",
        action="append",
        default=None,
        help="Restrict the batch to one tracked worktree id (repeatable)",
    )


def _fresh_bound_live_hint(rec) -> bool | None:
    live = getattr(rec, "bound_live", None)
    if live is None:
        return None
    stamped = getattr(rec, "bound_live_at", None)
    if not stamped:
        return None
    try:
        dt = datetime.fromisoformat(str(stamped))
    except (TypeError, ValueError):
        return None
    now = datetime.now(dt.tzinfo) if dt.tzinfo is not None else datetime.now()
    if (now - dt).total_seconds() > 600:
        return None
    return bool(live)


def _picker_reconcile_local_row(
    rec,
    *,
    lock_live: bool,
    stale_pids: list[int],
    bound_visible: bool,
    mux_info,
) -> dict:
    row: dict[str, object] = {"id": rec.worktree_id}
    if rec.prs:
        active = rec.active_pr()
        row["pr"] = pr_ops._pr_to_dict(active) if active is not None else None
        row["prs"] = [pr_ops._pr_to_dict(pr) for pr in rec.prs]
        row["pr_count"] = len(rec.prs)
    if bound_visible:
        row["session_bound_live"] = True
    if lock_live:
        row["session_lock_live"] = True
    elif stale_pids:
        row["session_lock_stale"] = True
        row["stale_lock_pids"] = list(stale_pids)
    if mux_info is not None:
        row["mux_session"] = bool(getattr(mux_info, "exists", False))
        row["mux_clients"] = int(getattr(mux_info, "clients", 0))
        row["mux_attached"] = bool(getattr(mux_info, "attached", False))
    return row


def build_payload(*, worktree_ids: list[str] | None = None) -> dict:
    requested_ids = [str(wid).strip() for wid in (worktree_ids or []) if str(wid).strip()]
    requested_set = set(requested_ids)
    tracking_path = cfg.tracking_dir()
    platform_name = cfg.detect_platform()
    records = tracking.list_records(tracking_path, platform_filter=platform_name)
    if requested_set:
        records = [rec for rec in records if rec.worktree_id in requested_set]

    # Only pay for ``cfg.load_config()`` -- which pulls in a full, unbounded
    # tracking-records re-scan (``_control_plane_related_pr_map``) plus
    # plugin-activation resolution, together ~10s+ on a machine with a large
    # tracking history -- when a record in scope actually HAS a PR left to
    # reconcile. Profiling a single-worktree call (the Picker's per-row
    # Actions-dialog refine, #3307 2026-09-30 render-perf follow-up) found
    # this was the dominant cost, paid in full even though the overwhelming
    # common case (no PR, or an already-terminal one) never uses ``config``
    # at all.
    reconcilable = []
    for rec in records:
        active = rec.active_pr()
        if active is None or active.number is None or tracking._pr_is_terminal(active):
            continue
        reconcilable.append(rec)

    config = None
    if reconcilable:
        try:
            config = cfg.load_config()
        except Exception:
            config = None

    pr_terminal_count = 0
    if config is not None:
        for rec in reconcilable:
            try:
                pr_ops._reconcile_active_pr(rec, config, best_effort=True)
            except Exception:
                continue
            active = rec.active_pr()
            if active is not None and tracking._pr_is_terminal(active):
                pr_terminal_count += 1

    pathful_records = [
        rec for rec in records if rec.worktree_path and Path(rec.worktree_path).exists()
    ]
    try:
        bound = reclaim.resolve_bound_copilots()
        bound_scan_ok = True
    except Exception:
        bound = []
        bound_scan_ok = False
    live_ids = {entry.get("worktree_id") for entry in bound if entry.get("worktree_id")}
    had_unresolved_bound = any(entry.get("worktree_id") is None for entry in bound)
    try:
        mux_map = sessions.mux_status_many([rec.worktree_id for rec in pathful_records])
        mux_scan_ok = True
    except Exception:
        mux_map = {}
        mux_scan_ok = False

    bound_visible_change_count = 0
    for rec in pathful_records:
        was_visible = _fresh_bound_live_hint(rec) is True
        if rec.worktree_id in live_ids:
            tracking.stamp_bound_live(rec.worktree_id, True, refresh=True)
            if not was_visible:
                bound_visible_change_count += 1
        elif rec.bound_live is True and bound_scan_ok and not had_unresolved_bound:
            tracking.stamp_bound_live(rec.worktree_id, False)
            if was_visible:
                bound_visible_change_count += 1
        if mux_scan_ok:
            info = mux_map.get(rec.worktree_id)
            mux_present = bool(info and getattr(info, "exists", False))
            if mux_present:
                tracking.stamp_mux_live(rec.worktree_id, True, refresh=True, sync=True)
            elif rec.mux_live is True:
                tracking.stamp_mux_live(rec.worktree_id, False, sync=True)

    rows: list[dict] = []
    for rec in records:
        try:
            lock_live, stale_pids = sessions.worktree_session_lock_state(rec)
        except Exception:
            lock_live, stale_pids = False, []
        bound_visible = rec.worktree_id in live_ids
        if not bound_visible and (not bound_scan_ok or had_unresolved_bound):
            bound_visible = _fresh_bound_live_hint(rec) is True
        rows.append(
            _picker_reconcile_local_row(
                rec,
                lock_live=lock_live,
                stale_pids=stale_pids,
                bound_visible=bound_visible,
                mux_info=mux_map.get(rec.worktree_id) if mux_scan_ok else None,
            )
        )

    return {
        "version": 1,
        "rows": rows,
        "summary": {
            "platform": platform_name,
            "requested_worktree_ids": requested_ids,
            "record_count": len(records),
            "pr_terminal_count": pr_terminal_count,
            "bound_visible_change_count": bound_visible_change_count,
            "had_unresolved_bound": had_unresolved_bound,
            "mux_scan_ok": mux_scan_ok,
        },
    }


def cmd_picker_reconcile_local(args: argparse.Namespace) -> int:
    """Emit the Group C local reconcile-and-stamp sweep as one JSON batch."""
    try:
        payload = build_payload(worktree_ids=getattr(args, "worktree_id", None))
    except Exception as exc:
        return output._json_error(str(exc))
    if getattr(args, "json", False):
        output._json_output(payload)
    else:
        print(payload["summary"]["record_count"])
    return 0
