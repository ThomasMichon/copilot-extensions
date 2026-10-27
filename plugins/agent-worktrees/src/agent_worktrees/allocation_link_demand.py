"""Use the existing resident point-read demand after durable origin reservation."""

from __future__ import annotations

import logging

from . import owned_worktree as allocation, status_monitor_runtime, worktrees_client
from .worktrees_projection_index import ProjectionError, ProjectionUnavailable

_LOG = logging.getLogger(__name__)


def request(owner_ref: str) -> None:
    if not status_monitor_runtime._status_monitor_enabled():
        return
    _, project, worktree_id = allocation.qualified_ref(owner_ref)
    try:
        result = worktrees_client.read_current({
            "contract_version": 1, "project": project, "worktree_id": worktree_id,
            "include_missing": True,
        })
        if result.get("availability", {}).get("status") == "advisory":
            _LOG.warning(
                "Allocation responsibility is durable; resident discovery wake is unconfirmed",
            )
    except (OSError, ValueError, RuntimeError, ProjectionError, ProjectionUnavailable) as exc:
        _LOG.warning(
            "Allocation responsibility is durable; resident discovery wake is unconfirmed (%s)",
            type(exc).__name__,
        )
