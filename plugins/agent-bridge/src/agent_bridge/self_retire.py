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

import os

from single_instance_lease import is_listening as _is_listening
from single_instance_lease import is_superseded as _lib_is_superseded
from zdd import routing

__all__ = ["_is_listening", "is_superseded", "slot_descriptor"]


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


_DEFAULT_SELF_RETIRE_STATUS = {
    "enabled": False,
    "armed": False,
    "generation": None,
    "superseded": False,
    "confirms": 0,
}


def slot_descriptor(
    config_dir,
    *,
    read_table=routing.read_table,
    self_retire_status: dict | None = None,
) -> dict:
    """Render this daemon's own slot-ownership view for ``/health``.

    process-slot-ownership Phase 5 (aperture-labs): "doctor"/"activity"/cockpit
    render ``process -> slot -> owner -> alive?`` -- the routing table's
    ``active``/``previous`` entries are the *slot*, this process's own pid is
    the candidate *owner*, and the self-retire loop's own live status dict is
    the *alive?* liveness-monitoring answer. Read-only and best-effort: any
    failure to read the routing table degrades to an empty ``active``/
    ``previous`` rather than failing the whole ``/health`` response.

    Deliberately mirrors agent-dispatch's ``_slot_descriptor`` shape (same
    ``pid``/``role``/``active``/``previous``/``self_retire`` keys) with one
    intentional omission: agent-dispatch also publishes a continuous
    ``abandoned_passive_reap`` loop status, because its coordinator runs that
    reap as an in-process background task. agent-bridge's equivalent
    abandoned-passive reap (#5195) runs once, in the separate short-lived
    ``deploy`` CLI process, *before* this daemon's own process even starts --
    there is no in-process loop here to report a live status for, so adding
    that key would fabricate a liveness signal that does not exist. A
    cross-process persisted "last reap outcome" is a distinct, larger design
    (a new file + writer + reader) left as a possible follow-up, not bundled
    into this observability parity slice.
    """
    my_pid = os.getpid()
    try:
        table = read_table(config_dir) or {}
    except Exception:
        table = {}
    active = table.get("active") if isinstance(table, dict) else None
    previous = table.get("previous") if isinstance(table, dict) else None
    active_pid = active.get("pid") if isinstance(active, dict) else None
    if (
        not isinstance(active_pid, int)
        or isinstance(active_pid, bool)  # bool is an int subclass -- exclude it
        or active_pid <= 0
    ):
        # No usable pid to compare against (missing/null/malformed entry) --
        # cannot answer the owner question either way.
        role = "unknown"
    elif active_pid == my_pid:
        role = "active"
    else:
        role = "passive"
    return {
        "pid": my_pid,
        "role": role,
        "active": active if isinstance(active, dict) else None,
        "previous": previous if isinstance(previous, dict) else None,
        "self_retire": dict(self_retire_status)
        if self_retire_status
        else dict(_DEFAULT_SELF_RETIRE_STATUS),
    }
