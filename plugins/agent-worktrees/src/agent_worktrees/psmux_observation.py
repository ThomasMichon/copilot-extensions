"""Worktrees consumer adapters for the canonical Driver mux observations."""

from agent_remote_driver import mux_inventory
from agent_remote_driver.mux_observation_types import ObservationUnavailable

from .mux_list_result import MuxObservationUnavailable


def observe(*, timeout: float = 5) -> dict[str, int]:
    try:
        return mux_inventory.observe(timeout=timeout)
    except ObservationUnavailable as exc:
        raise MuxObservationUnavailable(str(exc)) from exc


def display_status(worktree_ids: list[str]) -> dict:
    from agent_remote_driver.mux_observation import observe_psmux_many

    from .sessions import MuxInfo, mux_session_name

    observations = observe_psmux_many([mux_session_name(wt_id) for wt_id in worktree_ids])
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


def session_status(worktree_ids: list[str], *, strict: bool = False) -> dict:
    """Keep unknown rows out of authoritative liveness and recovery callers."""
    from .sessions import MuxInfo, mux_session_name

    if strict:
        inventory = observe()
        return {
            wt_id: MuxInfo(
                exists=mux_session_name(wt_id) in inventory,
                clients=inventory.get(mux_session_name(wt_id), 0),
            ) for wt_id in worktree_ids
        }
    result = display_status(worktree_ids)
    if any(not info.observed for info in result.values()):
        raise MuxObservationUnavailable("Native mux presence observation is unavailable")
    return result


def list_session_counts() -> dict[str, int] | None:
    from agent_remote_driver.mux_observation import list_psmux

    observed = list_psmux()
    if observed is None or any(o.status not in {"present", "absent"} for o in observed.values()):
        return None
    return {name: o.clients for name, o in observed.items() if o.status == "present"}


def active_pane(session_name: str) -> str | None:
    from agent_remote_driver.mux_observation import observe_psmux

    observed = observe_psmux(session_name)
    active = [p.pane_id for p in observed.panes if p.active and not p.dead]
    return active[0] if observed.status == "present" and len(active) == 1 else None
