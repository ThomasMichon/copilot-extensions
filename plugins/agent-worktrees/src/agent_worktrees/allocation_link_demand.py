"""Use the existing resident point-read demand after durable origin reservation."""

from __future__ import annotations

import logging

from . import owned_worktree as allocation, status_monitor_runtime, worktrees_client
from .worktrees_projection_index import ProjectionError, ProjectionUnavailable

_LOG = logging.getLogger(__name__)


def require_capability(owner_ref: str) -> None:
    """Refuse new remote effects unless the actual reconciliation owner is admitting."""
    from .allocation_link_runtime import CAPABILITY

    if not status_monitor_runtime._status_monitor_enabled():
        raise ValueError("remote creation requires the existing resident allocation linker")
    _, project, worktree_id = allocation.qualified_ref(owner_ref)
    worktrees_client.read_current({
        "contract_version": 1, "project": project, "worktree_id": worktree_id,
        "include_missing": True,
    })
    health = worktrees_client.read_health()
    if not isinstance(health, dict):
        raise ValueError("actual resident returned invalid allocation-linker capability evidence")
    linker = health.get("allocationLinker")
    if (
        health.get("status") != "ready" or health.get("published") is not True
        or not isinstance(health.get("capabilities"), list)
        or CAPABILITY not in health["capabilities"]
        or not isinstance(linker, dict) or linker.get("admitting") is not True
    ):
        raise ValueError("actual resident lacks an admitting allocation linker; update or recover its owner")


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
