"""Refresh independent content snapshots only in the scheduled producer."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from . import push_timeout
from .monitor_projection_cache import SegmentOwner


class ContentOwner(SegmentOwner, Protocol):
    def get(self, path: str) -> str: ...


def refresh_content_segments(
    project: str, paths: set[str], owner: ContentOwner, *,
    governance_deferred: type[Exception], on_progress: Callable[[], None] | None = None,
) -> None:
    for path in sorted(paths):
        if on_progress is not None:
            on_progress()
        try:
            owner.get(path)
        except governance_deferred:
            raise
        except push_timeout.ObservationCleanupError as error:
            owner.retain_unclaimed_cleanup(project, path, error)
        except push_timeout.ObservationCleanupPending:
            continue
        except Exception:
            continue
