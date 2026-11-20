"""Use the existing resident point-read demand after durable origin reservation."""

from __future__ import annotations

import logging
import time

from . import owned_worktree as allocation, status_monitor_runtime, worktrees_client
from .worktrees_projection_index import ProjectionError, ProjectionUnavailable

_LOG = logging.getLogger(__name__)

# A freshly provisioned or just-replaced resident needs a few seconds to publish
# its endpoint and first roster; remote creation waits for it instead of failing.
CAPABILITY_WAIT = 60.0
_RECOVERY_INTERVAL = 5.0
_RESIDENT_STARTING = (ConnectionError, FileNotFoundError, TimeoutError, ProjectionUnavailable)


def _resident_health(request: dict[str, object]) -> object:
    deadline = time.monotonic() + CAPABILITY_WAIT
    next_recovery = 0.0
    delay = 0.1
    while True:
        try:
            worktrees_client.read_current(request)
            return worktrees_client.read_health()
        except _RESIDENT_STARTING:
            now = time.monotonic()
            if now >= deadline:
                raise
            if now >= next_recovery:
                next_recovery = now + _RECOVERY_INTERVAL
                try:
                    status_monitor_runtime._ensure_status_monitor()
                except Exception as exc:  # the wait itself still decides the outcome
                    _LOG.debug("resident wake during capability wait failed (%s)", type(exc).__name__)
        time.sleep(min(delay, max(0.0, deadline - time.monotonic())))
        delay = min(delay * 2, 1.0)


def require_capability(owner_ref: str) -> None:
    """Refuse new remote effects unless the actual reconciliation owner is admitting."""
    from .allocation_link_runtime import CAPABILITY

    if not status_monitor_runtime._status_monitor_enabled():
        raise ValueError("remote creation requires the existing resident allocation linker")
    _, project, worktree_id = allocation.qualified_ref(owner_ref)
    health = _resident_health({
        "contract_version": 1, "project": project, "worktree_id": worktree_id,
        "include_missing": True,
    })
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
