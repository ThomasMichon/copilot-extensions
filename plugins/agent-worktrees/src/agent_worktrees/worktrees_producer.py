"""Owner-only enrollment of tracking, git, mux and session roster facts."""

from __future__ import annotations

import time

from . import config as cfg
from . import reclaim, sessions, tracking
from .worktrees_current_rows import materialize


def publish_active_project(runtime, *, observe_records=None) -> int:
    project = cfg.project_name()
    try:
        return _publish_active_project(runtime, project, observe_records=observe_records)
    except Exception as exc:
        runtime.publication_failed(project, exc)
        raise


def _publish_active_project(runtime, project: str, *, observe_records=None) -> int:
    from . import __main__ as core

    records = tracking.list_records(cfg.tracking_dir(project))
    if observe_records is not None:
        observe_records(project, records)
    session_ctx = sessions.scan_sessions_fast(records)
    mux_map = sessions.mux_status_many([rec.worktree_id for rec in records])
    repo = cfg.load_config(cfg.project_dir(project) / "config.yaml", project=project).default_repo
    state_map = core._classify_records_live(records, session_ctx, repo=repo)
    bare_orphan_wts = reclaim.bare_orphan_worktree_ids()
    bridge_live_wts = reclaim.live_bridge_worktrees()
    observed_at = time.time()
    with cfg.cached_load_config_scope():
        rows = materialize(
            records,
            serialize=core._worktree_to_dict,
            observed_at=observed_at,
            state_map=state_map,
            mux_map=mux_map,
            session_ctx=session_ctx,
            bare_orphan_wts=bare_orphan_wts,
            bridge_live_wts=bridge_live_wts,
        )
        for row, record in zip(rows, records, strict=True):
            if not row.get("title") or row["title"] == "null":
                row["title"] = session_ctx.latest_summary.get(
                    sessions._normalize_path(record.worktree_path),
                )
        from . import delegate_cli

        delegate_cli.annotate_delegate_graph(rows, current_only=True)
        runtime.publish(project, rows, observed_at=observed_at)
    return len(rows)
