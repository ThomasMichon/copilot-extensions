"""Owner-liveness tether for the daemon's own generation: self-retire when
superseded.

Thin adapter over the shared ``single_instance_lease`` supersession decision
(extracted from this module -- copilot-extensions #737). Detachment (a
zero-downtime redeploy stands the new daemon up beside the old and flips a
routing table so clients follow it) can leave a *demoted* daemon running with
nothing to shut it down; a daemon that observes it has been superseded by a live,
strictly-newer generation drains and exits on its own instead of lingering.

This module keeps agent-bridge's ``is_superseded(config_dir, ...)`` shape -- it
reads the routing table via ``zdd`` and delegates the pure, fail-safe decision to
the library, which returns ``True`` only when the ``active`` entry is a
*different* pid, at a *strictly higher* generation, that is *actually listening*.
Every ambiguous state returns ``False`` (stay alive). The genuinely-active daemon
always reads its own pid as ``active`` and therefore can never self-retire.
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
    """Has a live, strictly-newer daemon generation superseded us?

    Reads the routing table via ``read_table(config_dir)`` (``zdd.routing`` by
    default) and delegates the fail-safe decision to the shared
    ``single_instance_lease`` primitive. ``read_table`` and ``is_listening`` are
    injected for testing; any error surfaced by ``read_table`` bubbles up to the
    caller (the daemon's guarded loop treats a raised check as "stay alive").
    """
    table = read_table(config_dir)
    return _lib_is_superseded(
        table, my_pid, my_generation, is_listening=is_listening
    )
