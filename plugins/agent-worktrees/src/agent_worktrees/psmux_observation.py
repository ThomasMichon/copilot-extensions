"""Worktrees consumer adapters for the canonical Driver mux observations."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from agent_remote_driver import mux_inventory
from agent_remote_driver.mux_observation_types import ObservationUnavailable

from .mux_list_result import MuxObservationUnavailable

if TYPE_CHECKING:
    from .sessions import MuxInfo


def observe(*, timeout: float = 5) -> dict[str, int]:
    from agent_remote_driver.mux_observation import _data_dir, _namespace

    try:
        namespace = _namespace()
        prefix = namespace + "__" if namespace else ""
        memberships = mux_inventory.observe_memberships(timeout=timeout, data_dir=_data_dir())
        return {
            name: clients
            for base, (name, clients, _incarnation) in memberships.items()
            if base == prefix + name
        }
    except (ObservationUnavailable, ValueError) as exc:
        raise MuxObservationUnavailable(str(exc)) from exc


def display_status(worktree_ids: list[str], *, timeout: float = 1) -> dict[str, MuxInfo]:
    from agent_remote_driver.mux_observation import observe_psmux_many

    from .sessions import MuxInfo, mux_session_name

    observations = observe_psmux_many([mux_session_name(wt_id) for wt_id in worktree_ids], timeout=timeout)
    result = {}
    for wt_id in worktree_ids:
        observed = observations[mux_session_name(wt_id)]
        if observed.status not in {"present", "absent"}:
            result[wt_id] = MuxInfo(observed=False)
        else:
            result[wt_id] = MuxInfo(
                exists=observed.status == "present",
                clients=observed.clients if observed.status == "present" else 0,
            )
    return result


def session_status(
    worktree_ids: list[str], *, strict: bool = False, timeout: float = 5,
) -> dict[str, MuxInfo]:
    """Keep unknown rows out of authoritative liveness and recovery callers."""
    from .sessions import MuxInfo, mux_session_name

    deadline = time.monotonic() + min(timeout, 5)
    result = display_status(worktree_ids, timeout=min(timeout, 1))
    if strict and any(not info.observed for info in result.values()):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise MuxObservationUnavailable("Native mux presence observation timed out")
        inventory = observe(timeout=remaining)
        for wt_id, info in result.items():
            if not info.observed:
                name = mux_session_name(wt_id)
                result[wt_id] = MuxInfo(exists=name in inventory, clients=inventory.get(name, 0))
    if any(not info.observed for info in result.values()):
        raise MuxObservationUnavailable("Native mux presence observation is unavailable")
    return result


def list_session_counts() -> dict[str, int] | None:
    from agent_remote_driver.mux_observation import list_psmux

    observed = list_psmux()
    if observed is None or any(o.status not in {"present", "absent"} for o in observed.values()):
        return None
    result: dict[str, int] = {}
    for name, observation in observed.items():
        if observation.status == "present":
            if observation.clients is None:
                return None
            result[name] = observation.clients
    return result


def active_pane(session_name: str) -> str | None:
    from agent_remote_driver.mux_observation import observe_psmux

    observed = observe_psmux(session_name)
    active = [p.pane_id for p in observed.panes if p.active and not p.dead]
    return active[0] if observed.status == "present" and len(active) == 1 else None
