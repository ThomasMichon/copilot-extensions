"""Include managed mux projects in the existing scheduled computation owner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from . import status_monitor_runtime

if TYPE_CHECKING:
    from .mux_link import ManagedMuxCache


def managed_roster_projects(cache: ManagedMuxCache | None) -> set[str]:
    entries, _, _ = status_monitor_runtime._monitor_managed_session_union(cache, {})
    return {
        entry["project"] for entry in entries.values()
        if isinstance(entry.get("project"), str) and entry["project"]
    }
