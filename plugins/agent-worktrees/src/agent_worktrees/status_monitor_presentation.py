"""Publish managed native presentation without entering legacy mux effects."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
import re
from typing import Any

from agent_remote_driver.mux_observation import observe_psmux
from agent_remote_driver.mux_creation import is_psmux

from . import config as cfg, config_cache, mux_status_link, push_timeout
from . import status_bar_cli, status_monitor_runtime, tracking


def _native_entry(
    entry: dict[str, Any], mux_bin: str, cache: Any,
) -> dict[str, Any] | None:
    if not all(
        isinstance(entry.get(field), str) and entry[field]
        for field in ("project", "worktree_id", "worktree_path", "mux_session")
    ):
        return None
    if re.fullmatch(r"\$[0-9]+:[0-9]+", str(entry.get("session_incarnation", ""))):
        return entry
    observed = observe_psmux(entry["mux_session"], timeout=1.0)
    if observed.status != "present" or not observed.clients:
        return None
    result = mux_status_link.push_discovery_via_daemon({
        "project": entry["project"], "worktree_id": entry["worktree_id"],
        "worktree_path": entry["worktree_path"], "mux_session": entry["mux_session"],
        "mux_bin": mux_bin, "session_incarnation": observed.incarnation,
        "attached_clients": observed.clients,
    })
    if not result or not result.get("applied"):
        return None
    cache.apply_observation(result["entry"])
    current = cache.get(entry["project"], entry["worktree_id"])
    return current if (
        isinstance(current, dict) and current.get("live")
        and current.get("mux_session") == entry["mux_session"]
        and current.get("session_incarnation") == observed.incarnation
    ) else None


def run_monitor_sweep(
    sweep: Callable[..., int], mux_bin: str | None, token: str, prefix: str,
    ctx_done: set[str], **options: Any,
) -> int:
    """Keep the original observation/roster pass and add managed presentation."""
    from . import __main__ as core

    if not mux_bin or not is_psmux(mux_bin):
        return sweep(mux_bin, token, prefix, ctx_done, **options)
    observed_names: set[str] | None = None
    original_observer = options.get("catalog_observer")

    def observe(names: set[str]) -> None:
        nonlocal observed_names
        observed_names = set(names)
        if original_observer is not None:
            original_observer(names)

    forwarded = dict(options, catalog_observer=observe)
    served = sweep(mux_bin, token, prefix, ctx_done, **forwarded)
    cache = options.get("managed_mux_cache")
    segments = options.get("segment_cache")
    if served < 0 or observed_names is None or cache is None or segments is None:
        return served
    names = observed_names
    entries, _, _ = status_monitor_runtime._monitor_managed_session_union(cache, {})
    published = options.get("published")
    incarnations = options.get("incarnations")
    lock = options.get("project_lock")
    wait: Callable[[Any], None] = core._wait_for_lifecycle_priority
    recheck: Callable[[Any, str], object] = core._status_monitor_recheck
    governance_deferred: type[Exception] = core._StatusMonitorGovernanceDeferred
    settings_scope: Callable[..., Any] = config_cache.project_scope

    def publish_session(session: str, original: dict[str, Any]) -> None:
        if session not in names:
            return
        recheck(options.get("governance"), "pre-mutation:discover-connected-mux")
        entry = _native_entry(original, mux_bin, cache)
        if entry is None:
            return
        project, worktree_id, path = (
            entry.get("project"), entry.get("worktree_id"), entry.get("worktree_path"),
        )
        if (
            not isinstance(project, str) or not project
            or not isinstance(worktree_id, str) or not worktree_id
            or not isinstance(path, str) or not path
        ):
            return
        if incarnations is not None:
            status_monitor_runtime._monitor_update_session_incarnations(
                incarnations, {}, set(), {session: entry}, ctx_done, published,
            )
        wait(options.get("lifecycle_priority"))
        with lock if lock is not None else nullcontext():
            cfg.set_active_project(project)
            with settings_scope(options.get("config_cache_for_project"), project):
                context = None
                try:
                    if session not in ctx_done and not segments.observation_blocked(project, path):
                        context = status_bar_cli._render_status_context(path, plain=False)
                    segment = segments.get(path)
                except push_timeout.ObservationCleanupPending:
                    segment = segments.pending_segment_for_path(path)
        if not isinstance(segment, str):
            return
        values = {"@aw_updater": token, "@aw_updater_prefix": prefix, "@aw_seg": segment}
        if context is not None:
            values["@aw_ctx"] = context
        result = mux_status_link.publish_managed_session_status(
            managed_mux_cache=cache, project=project, path=path, session_name=session,
            worktree_id=worktree_id, values=values, published=published, prefix=prefix, token=token,
            resolve_worktree_id=tracking.find_worktree_id_by_cwd,
            before_publish=lambda: recheck(options.get("governance"), "pre-mutation:publish-status"),
        )
        if result is not None and result.get("context_published"):
            ctx_done.add(session)

    for session, original in entries.items():
        try:
            publish_session(session, original)
        except governance_deferred:
            raise
        except push_timeout.ObservationCleanupError as error:
            project, path = original.get("project"), original.get("worktree_path")
            if not isinstance(project, str) or not isinstance(path, str):
                raise
            segments.retain_unclaimed_cleanup(project, path, error)
        except Exception:
            continue
    return served
