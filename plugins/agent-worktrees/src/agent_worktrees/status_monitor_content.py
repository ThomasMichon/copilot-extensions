"""Refresh independent content snapshots only in the scheduled producer."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from typing import Any, Protocol

from . import config as cfg, config_cache, push_timeout
from .monitor_projection_cache import SegmentOwner


class ContentOwner(SegmentOwner, Protocol):
    def get(self, path: str) -> str: ...


class LifecyclePriority(Protocol):
    def is_set(self) -> bool: ...


def refresh_content_segments(
    project: str, paths: set[str], owner: ContentOwner, *,
    governance_deferred: type[Exception], on_progress: Callable[[], None] | None = None,
    admitted: Callable[[], bool] | None = None,
    before_observation: Callable[[], None] | None = None,
    on_completed: Callable[[str], None] | None = None,
) -> None:
    """Refresh observed content through its original computation and cleanup owner."""
    for path in sorted(paths):
        if on_progress is not None:
            on_progress()
        if admitted is not None and not admitted():
            return
        if before_observation is not None:
            before_observation()
        try:
            owner.get(path)
            if on_completed is not None:
                on_completed(path)
        except governance_deferred:
            raise
        except push_timeout.ObservationCleanupError as error:
            owner.retain_unclaimed_cleanup(project, path, error)
        except push_timeout.ObservationCleanupPending:
            continue
        except Exception:
            continue


def refresh_registered_content(
    paths: dict[str, str | None], owner: ContentOwner, *,
    governance_deferred: type[Exception],
    project_lock: AbstractContextManager[Any] | None = None,
    admitted: Callable[[], bool] | None = None,
    before_observation: Callable[[], None] | None = None,
    config_cache_for_project: Callable[[str], config_cache.ConfigCacheSession] | None = None,
    on_completed: Callable[[str], None] | None = None,
) -> None:
    """Compute served standalone content after the read-only projection pass."""
    for path, project in paths.items():
        if admitted is not None and not admitted():
            return
        with project_lock if project_lock is not None else nullcontext():
            if admitted is not None and not admitted():
                return
            previous_project = cfg.active_project()
            try:
                cfg.set_active_project(project)
                with config_cache.project_scope(config_cache_for_project, project):
                    refresh_content_segments(
                        project or "", {path}, owner, governance_deferred=governance_deferred,
                        admitted=admitted, before_observation=before_observation,
                        on_completed=on_completed,
                    )
            finally:
                cfg.set_active_project(previous_project)


def refresh_monitor_content(
    paths: dict[str, str | None], owner: ContentOwner, *,
    recheck: Callable[[object, str], None], governance_deferred: type[Exception],
    project_lock: AbstractContextManager[Any] | None = None,
    lifecycle_priority: LifecyclePriority | None = None, governance: object = None,
    config_cache_for_project: Callable[[str], config_cache.ConfigCacheSession] | None = None,
    on_completed: Callable[[str], None] | None = None,
) -> None:
    """Bind the elected monitor's governance and lifecycle policy to content work."""
    refresh_registered_content(
        paths, owner, governance_deferred=governance_deferred,
        project_lock=project_lock,
        admitted=lambda: lifecycle_priority is None or not lifecycle_priority.is_set(),
        before_observation=lambda: recheck(governance, "pre-mutation:render-status"),
        config_cache_for_project=config_cache_for_project,
        on_completed=on_completed,
    )
