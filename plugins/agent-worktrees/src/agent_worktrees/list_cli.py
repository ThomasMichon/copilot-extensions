"""List/read CLI surfaces extracted from ``__main__``."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import config as cfg
from . import git_ops, list_cache, output, profile_assignment, reclaim, sessions, tracking
from . import status_monitor_runtime


def _core():
    from . import __main__ as core

    return core


def _core_helper(name: str, local):
    candidate = vars(_core()).get(name)
    if callable(candidate) and candidate is not local:
        return candidate
    return local


def _apply_tracking_override(*args, **kwargs):
    return _core()._apply_tracking_override(*args, **kwargs)


def _build_active_paths(*args, **kwargs):
    return _core()._build_active_paths(*args, **kwargs)


def _classify_one_record(*args, **kwargs):
    return _core()._classify_one_record(*args, **kwargs)


def _classify_records(*args, **kwargs):
    return _core()._classify_records(*args, **kwargs)


def _json_error(*args, **kwargs):
    return _core()._json_error(*args, **kwargs)


def _json_output(*args, **kwargs):
    return _core()._json_output(*args, **kwargs)


def _normalize_path(*args, **kwargs):
    return _core()._normalize_path(*args, **kwargs)


def _status_monitor_enabled(*args, **kwargs):
    return status_monitor_runtime._status_monitor_enabled(*args, **kwargs)


def _ensure_status_monitor(*args, **kwargs):
    return status_monitor_runtime._ensure_status_monitor(*args, **kwargs)


def _worktree_to_dict(*args, **kwargs):
    return _core()._worktree_to_dict(*args, **kwargs)


def resolve_worktree_id_by_codename(*args, **kwargs):
    return _core().resolve_worktree_id_by_codename(*args, **kwargs)


def add_parsers(sub) -> None:
    p = sub.add_parser("list", help="List worktrees from tracking records")
    p.add_argument("--json", action="store_true", help="JSON output mode (stdout is JSON only)")
    p.add_argument(
        "--mux-details",
        action="store_true",
        help="Include mux session attached/detached status (JSON only)",
    )
    p.add_argument(
        "--tracking-status",
        default="all",
        choices=["active", "complete", "finalized", "orphaned", "archived", "all"],
        help="Filter by tracking status (default: all). 'archived' also needs --all.",
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Include worktrees whose directories no longer exist on disk",
    )
    p.add_argument(
        "--include-other-platforms",
        action="store_true",
        help="Include worktrees from other platforms (e.g. Windows when on Linux)",
    )
    p.add_argument(
        "--classify",
        action="store_true",
        help="Include git state classification (state/ahead/behind/"
        "dirty; JSON only). Slower: ~5 git calls per worktree.",
    )
    p.add_argument(
        "--cache-only",
        action="store_true",
        help="Cache-only fast paint (picker-cache-first-paint, "
        "dotfiles#948): build JSON rows from ONLY the cached "
        "session-render fields in each tracking record -- no "
        "events.jsonl scan, no process/mux scan, no git "
        "classify. Never-populated worktrees render Unknown. "
        "Used by the Picker's SSH fast phase; a --classify "
        "populate later fills + writes the cache back.",
    )
    p.add_argument(
        "--profile-assignment-history",
        action="store_true",
        help="Include bounded profile_assignments history and the latest "
        "assignment in each JSON worktree row. Ordinary and cache-polled "
        "rows include only current_profile_assignment.",
    )
    p.add_argument(
        "--stream",
        action="store_true",
        help="Emit newline-delimited JSON (one worktree per line, "
        "flushed) for the Picker's streaming SSH consumer: a "
        "begin frame, fast (unclassified) rows, then classified "
        "rows (with --classify), then a done frame. Implies "
        "--json.",
    )
    p.add_argument(
        "--fresh",
        action="store_true",
        help="Bypass the coalescing result cache (list-coalescing, "
        "cx#918) and force a live scan, refreshing the cache. "
        "Use when exactness matters; normal polling reads a "
        "short-TTL cache so concurrent/repeated calls coalesce "
        "onto one scan (tune via AGENT_WORKTREES_LIST_CACHE_TTL, "
        "0 disables).",
    )
    p.add_argument("--worktree-id", help="Restrict the listing to one exact or unique-suffix ID")
    p.add_argument(
        "--codename",
        default=None,
        help="Restrict the listing to the worktree with this codename "
        "(alternative to --worktree-id)",
    )
    p.add_argument(
        "--refresh",
        action="store_true",
        help="Before listing one --worktree-id, repair its missing "
        "session registry and refresh bound/mux liveness",
    )
    p.add_argument(
        "--glance",
        action="store_true",
        help="Compact, agent-digestible 'at a glance' digest of "
        "ACTIVE worktrees: one line each (id, disposition age, "
        "title -- summary), ranked by recency, with "
        "no-disposition worktrees named rather than hidden. For "
        "situational awareness / sub-agent consumption -- far "
        "cheaper to read than --json.",
    )


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
    bare_orphan_wts: set[str] | None = None

    def to_dict(rec, state_info):
        wt = _worktree_to_dict(
            rec,
            mux_info=mux_map.get(rec.worktree_id),
            session_ctx=session_ctx,
            state_info=state_info,
            bare_orphan_wts=bare_orphan_wts,
            include_profile_assignment_history=getattr(args, "profile_assignment_history", False),
        )
        title = wt.get("title")
        if not title or title == "null":
            wt["title"] = session_ctx.latest_summary.get(_normalize_path(rec.worktree_path))
        return wt

    emit({"type": "begin", "version": getattr(_core(), "_JSON_SCHEMA_VERSION"), "count": len(records)})
    if getattr(args, "mux_details", False):
        try:
            bare_orphan_wts = reclaim.bare_orphan_worktree_ids()
        except Exception:
            bare_orphan_wts = None
    from . import delegate_cli

    delegate_overlay = delegate_cli.delegate_graph_overlays([
        {
            "id": rec.worktree_id,
            "machine": rec.machine,
            "platform": rec.platform,
            "repo": rec.repo,
            "path": rec.worktree_path,
            "status": rec.status,
            **(
                {"caller_worktree": rec.caller_worktree}
                if rec.caller_worktree
                else {}
            ),
        }
        for rec in records
    ])
    for rec in records:
        wt = to_dict(rec, None)
        wt.update(delegate_overlay.get(rec.worktree_id, {}))
        emit({"type": "worktree", "phase": "fast", "wt": wt})
    if getattr(args, "classify", False):
        config = cfg.load_config()
        repo = config.default_repo
        active_paths = _build_active_paths(records, session_ctx)
        from .picker_support.data_local import _stamp_from_raw

        for rec in records:
            info = _classify_one_record(
                rec, repo=repo, active_paths=active_paths, session_ctx=session_ctx
            )
            wt = to_dict(rec, info)
            _stamp_from_raw(rec, wt, session_ctx)
            wt.update(delegate_overlay.get(rec.worktree_id, {}))
            emit({"type": "worktree", "phase": "classified", "wt": wt})
    emit({"type": "done", "count": len(records)})
    return 0


def _list_records_for_args(args: argparse.Namespace):
    """Load the filtered record set shared by ``list`` and daemon cache warm."""
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

    if not getattr(args, "all", False):
        records = [
            r
            for r in records
            if r.worktree_path
            and Path(r.worktree_path).exists()
            and (Path(r.worktree_path) / ".git").exists()
        ]
    return records


def _filter_list_worktree(records, raw_id: str):
    """Restrict list records to one exact or unique-suffix worktree id."""
    exact = [rec for rec in records if rec.worktree_id == raw_id]
    if exact:
        return exact
    matches = [rec for rec in records if rec.worktree_id.endswith(raw_id)]
    if len(matches) > 1:
        ids = ", ".join(sorted(rec.worktree_id[-12:] for rec in matches))
        raise ValueError(f"Ambiguous short ID '{raw_id}' matches {len(matches)} worktrees: {ids}")
    return matches


def _refresh_list_record(rec: tracking.WorktreeRecord) -> None:
    """Repair and refresh one record before an explicit Picker row read."""
    if getattr(rec, "sessions", None) is None:
        try:
            discovered = sessions.backfill_sessions([rec])
            rec.sessions = [
                tracking.SessionEntry(session_id=session_id, started_at="")
                for session_id in discovered.get(rec.worktree_id, [])
            ]
            tracking.save_record(rec)
        except Exception:
            pass

    try:
        bridge_live = rec.worktree_id in reclaim.live_bridge_worktrees()
        bound_live = (
            bool(reclaim.resolve_bound_copilots(worktree_id=rec.worktree_id)) or bridge_live
        )
        mux_info = sessions.mux_status_many([rec.worktree_id]).get(rec.worktree_id)
        mux_live = bool(mux_info and mux_info.exists)
        now = datetime.now().isoformat(timespec="seconds")
        rec.bound_live = bound_live
        rec.bound_live_at = now
        rec.mux_live = mux_live
        rec.mux_live_at = now
        tracking.stamp_bound_live(rec.worktree_id, bound_live)
        tracking.stamp_mux_live(rec.worktree_id, mux_live, sync=True)
    except Exception:
        pass


def _list_error(args: argparse.Namespace, message: str) -> int:
    """Render list argument errors in the caller's selected output mode."""
    if getattr(args, "json", False) or getattr(args, "stream", False):
        return _json_error(message)
    output.err(message)
    return 1


def _build_list_json_payload(
    args: argparse.Namespace,
    records,
    *,
    stamp_session_state: bool = True,
) -> dict:
    """Build the enriched JSON listing without reading or writing its cache."""
    mux_map: dict[str, sessions.MuxInfo] = {}
    if getattr(args, "mux_details", False):
        wt_ids = [rec.worktree_id for rec in records]
        mux_map = sessions.mux_status_many(wt_ids)
    session_ctx = sessions.scan_sessions_fast(records)
    state_map: dict[str, git_ops.WorktreeStateInfo] = {}
    if getattr(args, "classify", False):
        daemon_filters = {
            "status_filter": (None if args.tracking_status == "all" else args.tracking_status),
            "platform_filter": (
                None if getattr(args, "include_other_platforms", False) else cfg.detect_platform()
            ),
            "all": bool(getattr(args, "all", False)),
        }
        state_map = _classify_records(records, session_ctx, daemon_filters=daemon_filters)
    bare_orphan_wts: set[str] | None = None
    bridge_live_wts: set[str] | None = None
    if getattr(args, "mux_details", False):
        try:
            bare_orphan_wts = reclaim.bare_orphan_worktree_ids()
        except Exception:
            bare_orphan_wts = None
        try:
            bridge_live_wts = reclaim.live_bridge_worktrees()
        except Exception:
            bridge_live_wts = None
    worktrees = [
        _worktree_to_dict(
            rec,
            mux_info=mux_map.get(rec.worktree_id),
            session_ctx=session_ctx,
            state_info=state_map.get(rec.worktree_id),
            bare_orphan_wts=bare_orphan_wts,
            bridge_live_wts=bridge_live_wts,
            include_profile_assignment_history=getattr(args, "profile_assignment_history", False),
        )
        for rec in records
    ]
    for wt_dict, rec in zip(worktrees, records, strict=True):
        title = wt_dict.get("title")
        if not title or title == "null":
            norm = _normalize_path(rec.worktree_path)
            title = session_ctx.latest_summary.get(norm)
        wt_dict["title"] = title
    from . import delegate_cli

    delegate_cli.annotate_delegate_graph(worktrees)
    if getattr(args, "classify", False) and stamp_session_state:
        from .picker_support.data_local import _stamp_from_raw

        for wt_dict, rec in zip(worktrees, records, strict=True):
            _stamp_from_raw(rec, wt_dict, session_ctx)
    return {"worktrees": worktrees}


def _warm_list_cache_for_active_project(*, interval: float = 15) -> int:
    """Refresh exact, recently requested list shapes for the active project."""
    if list_cache.ttl_seconds() <= 0:
        return 0
    project = cfg.project_name()
    warmed = 0
    for demand in list_cache.recent_demands(project):
        shape = demand["args"]
        args = argparse.Namespace(
            json=True,
            stream=False,
            cache_only=False,
            glance=False,
            fresh=True,
            tracking_status=demand.get("tracking_status", "all"),
            **shape,
        )
        records = _core_helper("_list_records_for_args", _list_records_for_args)(args)
        payload = _core_helper("_build_list_json_payload", _build_list_json_payload)(
            args, records, stamp_session_state=False
        )
        list_cache.write(
            demand["key"],
            payload,
            fresh_for=list_cache.resident_fresh_for(interval),
        )
        warmed += 1
    return warmed


def cmd_list(args: argparse.Namespace) -> int:
    """List worktrees from tracking records."""
    records = _core_helper("_list_records_for_args", _list_records_for_args)(args)
    worktree_id = getattr(args, "worktree_id", None)
    codename_arg = getattr(args, "codename", None)
    if codename_arg and not worktree_id:
        resolved_id = resolve_worktree_id_by_codename(codename_arg)
        if resolved_id is None:
            records = []
        else:
            worktree_id = resolved_id
    if worktree_id:
        try:
            records = _core_helper("_filter_list_worktree", _filter_list_worktree)(
                records, worktree_id
            )
        except ValueError as error:
            return _core_helper("_list_error", _list_error)(args, str(error))
    if getattr(args, "refresh", False):
        if not worktree_id:
            return _core_helper("_list_error", _list_error)(args, "--refresh requires --worktree-id")
        if records:
            _core_helper("_refresh_list_record", _refresh_list_record)(records[0])

    if getattr(args, "glance", False):
        profile_assignment.maintain()
        from . import list_views_cli

        return list_views_cli.cmd_list_glance(records)

    if getattr(args, "stream", False):
        profile_assignment.maintain()
        return _core_helper("_cmd_list_stream", _cmd_list_stream)(args, records)

    if args.json:
        if getattr(args, "cache_only", False):
            from .picker_support.data_local import _overlay_cached_state
            from . import delegate_cli

            worktrees = []
            for rec in records:
                raw = _worktree_to_dict(
                    rec,
                    include_profile_assignment_history=getattr(
                        args, "profile_assignment_history", False
                    ),
                )
                if rec.session_summary and not (raw.get("title") and raw["title"] != "null"):
                    raw["title"] = rec.session_summary
                _overlay_cached_state(raw, rec)
                worktrees.append(raw)
            delegate_cli.annotate_delegate_graph(worktrees)
            _json_output({"worktrees": worktrees})
            return 0
        _lc_key = None
        _project = None
        if not worktree_id:
            try:
                _project = cfg.project_name()
                _lc_key = list_cache.cache_key(
                    args,
                    project=_project,
                    tracking_status=getattr(args, "tracking_status", "all"),
                )
            except Exception:
                _lc_key = None
                _project = None
        if _lc_key and _project:
            list_cache.note_demand(
                _lc_key,
                args,
                project=_project,
                tracking_status=getattr(args, "tracking_status", "all"),
            )
            if _status_monitor_enabled():
                _ensure_status_monitor()
        if _lc_key and not getattr(args, "fresh", False):
            _cached = list_cache.read_fresh(_lc_key)
            if isinstance(_cached, dict) and "worktrees" in _cached:
                _json_output(_cached)
                return 0
        profile_assignment.maintain()
        _payload = _core_helper("_build_list_json_payload", _build_list_json_payload)(args, records)
        if _lc_key:
            list_cache.write(_lc_key, _payload)
        _json_output(_payload)
        return 0

    if not records:
        profile_assignment.maintain()
        print("No tracked worktrees.")
        return 0

    profile_assignment.maintain()
    session_ctx = sessions.scan_sessions_fast(records)

    print()
    print(f"{'ID':<42} {'Status':<12} {'Platform':<8} Title")
    print(f"{'─' * 41:<42} {'─' * 11:<12} {'─' * 7:<8} {'─' * 30}")
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
