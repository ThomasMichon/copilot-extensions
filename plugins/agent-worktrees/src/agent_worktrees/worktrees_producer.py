"""Owner-only enrollment of tracking, git, mux and session roster facts."""

from __future__ import annotations

import contextlib
import sys
import time

from . import config as cfg
from . import reclaim, sessions, tracking, push_timeout, git_ops
from .worktrees_current_rows import materialize, selection_row
from .mux_list_result import MuxObservationUnavailable
from .worktrees_projection_index import ProjectionError, ProjectionUnavailable, SelectionIndex


def publish_project_rosters(
    projects, *, interval, project_lock, lifecycle_priority, governance,
    segment_cache, config_cache_for_project, roster_publisher, warm_list_cache,
) -> None:
    from . import __main__ as core, config_cache

    for project in sorted(projects):
        core._wait_for_lifecycle_priority(lifecycle_priority)
        with project_lock if project_lock is not None else contextlib.nullcontext():
            try:
                core._status_monitor_recheck(governance, "pre-mutation:warm-list-cache")
                cfg.set_active_project(project)
                with config_cache.project_scope(config_cache_for_project, project):
                    if roster_publisher is not None:
                        roster_publisher()
                    else:
                        warm_list_cache(
                            interval=interval,
                            **({"cleanup_owner": segment_cache}
                               if segment_cache is not None and git_ops._windows_observation() else {}),
                        )
            except core._StatusMonitorGovernanceDeferred:
                raise
            except git_ops.push_timeout.ObservationCleanupError as exc:
                if segment_cache is None:
                    raise
                segment_cache.retain_unclaimed_cleanup(project, None, exc)
                if roster_publisher is not None:
                    print(f"current-roster publication failed for {project}: {exc}", file=sys.stderr)
            except Exception as exc:
                if roster_publisher is not None:
                    print(f"current-roster publication failed for {project}: {exc}", file=sys.stderr)


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

    # The roster is only as fresh as the first fact read for it.
    collected_from = runtime.clock()
    records = tracking.list_records(cfg.tracking_dir(project))
    if observe_records is not None:
        observe_records(project, records)
    refresh_requests = runtime.refreshes.pending(project)
    refreshed = set()
    if refresh_requests:
        from .list_cli import _refresh_list_record

        positions = {record.worktree_id: index for index, record in enumerate(records)}
        selection_index = SelectionIndex(selection_row(record) for record in records)
        accepted_requests = []
        for request_id, selection in refresh_requests:
            try:
                selected_ids = selection_index.select(selection)
            except ProjectionError as error:
                runtime.refreshes.reject(request_id, str(error))
                continue
            except ProjectionUnavailable as error:
                runtime.refreshes.reject(request_id, str(error), code="refresh-unavailable")
                continue
            if len(selected_ids) != 1:
                runtime.refreshes.reject(
                    request_id, "Point refresh requires one readable selected record",
                    code="refresh-unavailable",
                )
                continue
            record = records[positions[selected_ids[0]]]
            try:
                if record.worktree_id not in refreshed:
                    previous_mux = (
                        managed_mux_cache.get(project, record.worktree_id)
                        if managed_mux_cache is not None else None
                    )
                    record = _refresh_list_record(record, strict=True)
                    records[positions[record.worktree_id]] = record
                    if record.mux_live is False:
                        runtime.refreshes.supersede_mux(project, record.worktree_id, previous_mux)
                    refreshed.add(record.worktree_id)
            except (OSError, ValueError, MuxObservationUnavailable, push_timeout.ObservationUnavailable) as error:
                runtime.refreshes.reject(request_id, str(error), code="refresh-unavailable")
                continue
            accepted_requests.append((request_id, {
                "worktree_id": record.worktree_id, "creation_nonce": record.creation_nonce,
                "path": record.worktree_path,
            }))
        refresh_requests = tuple(accepted_requests)
    session_ctx = sessions.scan_sessions_fast(records)
    try:
        mux_map = sessions.mux_status_many([rec.worktree_id for rec in records])
        mux_observed = True
    except MuxObservationUnavailable:
        mux_map = {}
        mux_observed = False
    if refresh_requests and not mux_observed:
        for request_id, _selector in refresh_requests:
            runtime.refreshes.reject(
                request_id, "Point-refresh publication lacks a current mux observation",
                code="refresh-unavailable",
            )
        refresh_requests = ()
    # Fresh receipt alone cannot qualify cached counts during observation loss.
    if managed_mux_cache is not None and mux_observed:
        from .mux_link import MAPPING_STALE_AFTER_SECONDS

        now = time.time()
        for record in records:
            if record.worktree_id in refreshed:
                continue
            entry = managed_mux_cache.get(project, record.worktree_id)
            if entry is not None and not runtime.refreshes.mux_observation_current(
                project, record.worktree_id, entry,
            ):
                continue
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
            project, rows, observed_at=observed_at, collected_from=collected_from,
            refresh_requests=refresh_requests,
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
