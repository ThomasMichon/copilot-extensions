"""Owner-liveness tether for the coordinator's own generation: self-retire when
superseded.

Thin adapter over the shared ``single_instance_lease`` supersession decision
(process-slot-ownership Phase 4, aperture-labs #5091 -- the same lib agent-bridge
already adopted via copilot-extensions #737). Detachment (a zero-downtime
redeploy stands the new coordinator up beside the old one and flips the shared
routing table so clients follow it) can leave a *demoted* coordinator running
with nothing to shut it down -- if the deploy orchestrator never sends the old
coordinator its retire signal (it crashed, or the cutover was abandoned), the
demoted generation lingers as a stranded ``serve --passive`` process. The fix: a
coordinator that observes it has been demoted -- a newer generation flipped the
routing table and now serves clients -- drains to its safe cutover point (no
in-flight claim) and exits on its own instead of lingering.

This module keeps agent-dispatch's ``is_superseded(config_dir, ...)`` shape -- it
reads the routing table via ``zdd`` and delegates the pure, fail-safe decision to
the library, which returns ``True`` only when the ``active`` entry is a
*different* pid, at a *strictly higher* generation, that is *actually listening*
(a live successor). Every ambiguous state -- no table, no/parse-broken ``active``
entry, our own pid still active, a not-higher generation, or a successor that is
not (yet) accepting connections -- returns ``False`` (stay alive). The
genuinely-active coordinator always reads its own pid as ``active`` and
therefore can never self-retire; only a demoted generation with a confirmed live
successor ever can.

The coordinator wires this into a guarded background loop (see
``coordinator.py`` lifespan); the loop is opt-in and additionally gates on the
coordinator being at its safe cutover point (the ``DrainGate`` reports no
in-flight claim) before it exits, so a claim mid-flight is never dropped.
"""

from __future__ import annotations

from single_instance_lease import is_listening as _is_listening
from single_instance_lease import is_superseded as _lib_is_superseded
from zdd import routing

__all__ = ["_is_listening", "is_superseded"]


def is_superseded(
    config_dir,
    my_pid: int,
    my_generation: int,
    *,
    read_table=routing.read_table,
    is_listening=_is_listening,
) -> bool:
    """Has a live, strictly-newer coordinator generation superseded us?

    Reads the routing table via ``read_table(config_dir)`` (``zdd.routing`` by
    default) and delegates the fail-safe decision to the shared
    ``single_instance_lease`` primitive. ``read_table`` and ``is_listening`` are
    injected for testing; any error surfaced by ``read_table`` bubbles up to the
    caller (the coordinator's guarded loop treats a raised check as "stay
    alive").
    """
    table = read_table(config_dir)
    return _lib_is_superseded(
        table, my_pid, my_generation, is_listening=is_listening
    )

