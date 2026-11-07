"""Owner-only enrollment of tracking, git, mux and session roster facts."""

from __future__ import annotations

import time

from . import config as cfg
from . import reclaim, sessions, tracking, push_timeout, git_ops
from .worktrees_current_rows import materialize
from .mux_list_result import MuxObservationUnavailable


def publish_active_project(
    runtime, *, observe_records=None, cleanup_owner=None, managed_mux_cache=None,
) -> int:
    project = cfg.project_name()
    if cleanup_owner is not None and git_ops._windows_observation():
        try:
            if cleanup_owner.observation_blocked(project):
                raise push_timeout.ObservationCleanupPending("project cleanup pending")
            repo = cfg.load_config(
                cfg.project_dir(project) / "config.yaml", project=project,
            ).default_repo
            return cleanup_owner.run_observation(
                project, repo.anchor, None,
                lambda: publish_active_project(
                    runtime, observe_records=observe_records, managed_mux_cache=managed_mux_cache,
                ),
            )
        except push_timeout.ObservationCleanupPending as error:
            runtime.publication_failed(project, error)
            raise
    try:
        return _publish_active_project(
            runtime, project, observe_records=observe_records, managed_mux_cache=managed_mux_cache,
        )
    except Exception as exc:
        runtime.publication_failed(project, exc)
        raise


def _publish_active_project(
    runtime, project: str, *, observe_records=None, managed_mux_cache=None,
) -> int:
    from . import __main__ as core

    records = tracking.list_records(cfg.tracking_dir(project))
    if observe_records is not None:
        observe_records(project, records)
    session_ctx = sessions.scan_sessions_fast(records)
    try:
        mux_map = sessions.mux_status_many([rec.worktree_id for rec in records])
        mux_observed = True
    except MuxObservationUnavailable:
        mux_map = {}
        mux_observed = False
    if managed_mux_cache is not None and mux_observed:
        from .mux_link import MAPPING_STALE_AFTER_SECONDS

        now = time.time()
        for record in records:
            entry = managed_mux_cache.get(project, record.worktree_id)
            received_at = entry.get("received_at") if entry else None
            if (
                not entry or not entry.get("live")
                or isinstance(received_at, bool) or not isinstance(received_at, (int, float))
                or now - received_at > MAPPING_STALE_AFTER_SECONDS
            ):
                continue
            mux_map[record.worktree_id] = sessions.MuxInfo(
                exists=True, clients=entry["attached_clients"],
            )
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
        runtime.publish(
            project, rows, observed_at=observed_at,
            record_heads={
                record.worktree_id: {
                    "creation_nonce": record.creation_nonce,
                    "workspace": record.worktree_path,
                    "head_session": record.resolved_head_session,
                }
                for record in records
            },
        )
    return len(rows)
