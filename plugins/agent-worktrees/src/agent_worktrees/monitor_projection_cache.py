"""Read-only segment view retaining the monitor's original cleanup owner."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol

from . import push_timeout


class SegmentOwner(Protocol):
    def get_cached(self, path: str) -> str | None: ...

    def observation_blocked(self, project: str, path: str | None = None) -> bool: ...

    def pending_segment_for_path(self, path: str) -> str: ...

    def retain_unclaimed_cleanup(
        self, project: str, path: str | None, error: push_timeout.ObservationCleanupError,
    ) -> None: ...


class ProjectedSegments:
    """Only ``get`` changes; all observation custody stays with ``owner``."""

    def __init__(
        self, owner: SegmentOwner | None, read: Callable[[str], str],
    ) -> None:
        self.owner = owner
        self.read = read

    def get(self, path: str) -> str:
        return self.read(path)

    def __getattr__(self, name: str) -> Any:
        return getattr(self.owner, name)
