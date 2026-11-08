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
