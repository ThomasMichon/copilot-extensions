"""Compatibility boundary for Driver's complete action-time mux inventory."""

from agent_remote_driver import mux_inventory
from agent_remote_driver.mux_observation_types import ObservationUnavailable

from .mux_list_result import MuxObservationUnavailable


def observe(*, timeout: float = 5) -> dict[str, int]:
    try:
        return mux_inventory.observe(timeout=timeout)
    except ObservationUnavailable as exc:
        raise MuxObservationUnavailable(str(exc)) from exc
