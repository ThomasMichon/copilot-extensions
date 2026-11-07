"""Notify the resident about committed mux facts, not heartbeat timestamps."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .mux_link import ManagedMuxCache


def _facts(entry: dict | None) -> dict | None:
    if entry is None:
        return None
    return {key: value for key, value in entry.items() if key not in {"observed_at", "received_at"}}


def build_compute(
    cache: ManagedMuxCache, *, kind: str, stale_after: float,
    on_change: Callable[[dict], None] | None = None,
) -> Callable[[str, dict], dict]:
    """Keep mapping acceptance independent of its best-effort consumer wake."""
    def compute(request_kind: str, payload: dict) -> dict:
        if request_kind != kind:
            raise ValueError(f"mux_link daemon does not serve kind={request_kind!r}")
        previous = None
        if (
            on_change is not None and isinstance(payload, dict)
            and isinstance(payload.get("project"), str)
            and isinstance(payload.get("worktree_id"), str)
        ):
            previous = cache.get(payload["project"], payload["worktree_id"])
        result = cache.apply_observation(payload)
        if on_change is not None and result.get("applied"):
            current = cache.get(payload["project"], payload["worktree_id"])
            restored = bool(
                previous and previous["live"]
                and time.time() - previous["received_at"] > stale_after
            )
            if current is not None and (_facts(previous) != _facts(current) or restored):
                try:
                    on_change(current)
                except Exception:
                    # The cache mutation already committed; notification is not a replay boundary.
                    pass
        return result

    return compute
