"""Recover a ``RELEASING`` spawn reservation that never carried a
session_handle and has sat past a bounded age with no automatic recovery.

Extracted as its own coherent responsibility, mirroring
:mod:`agent_dispatch.spawn_cold_recovery`'s ``COLD`` sweep for the same
module-size-discipline reason (``AGENTS.md``). ``supervisor`` below is a
:class:`agent_dispatch.supervisor.Supervisor` instance; this module has no
dependency on that class beyond the attributes it reads.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from .client import DispatchError
from .queue import SpawnState

log = logging.getLogger("agent-dispatch.supervisor")

#: How long a handle-less RELEASING reservation may sit before this sweep
#: treats it as stranded. Mirrors :data:`agent_dispatch.supervisor
#: ._MIN_RESERVING_TIMEOUT_SECONDS` -- the same conservative bound already
#: trusted for the sibling "stuck mid-launch" gap (#3354), not a new
#: judgment call.
STRANDED_RELEASING_TIMEOUT_SECONDS = 600.0


def recover_stranded_releasing_reservations(
    supervisor: Any, *, now: float | None = None
) -> int:
    """Force-fail a ``RELEASING`` reservation that never recorded a
    session_handle and has sat past :data:`STRANDED_RELEASING_TIMEOUT_SECONDS`.

    ``reserve_spawn`` treats any ``RELEASING`` reservation as still active --
    by design, since a release/cleanup call in flight might yet succeed --
    and keys that check on ``exclusive_key``, not ``task_id``, so a stuck one
    blocks every future task sharing that key, not just its own retry
    (copilot-extensions#3179). The CLI's own ``reservations fail --force``
    already carries the correct judgment for exactly this shape, spelled out
    in its own help text: a RELEASING reservation with **no recorded
    session_handle** has nothing an automatic exact-absence proof could ever
    confirm either way (there's no handle to check liveness against), so
    without this sweep it would otherwise sit ``releasing`` forever, the
    *same* way a handle-carrying one gets genuinely liveness-checked by
    :meth:`Supervisor.reconcile_reserving` and friends -- it just never had
    that path cover it, because none of the existing gone/idle verdict
    checks have anything to probe. Confirmed live: one such reservation
    (from a spawn that crashed before ever recording a handle) sat
    ``releasing`` for 8+ hours, permanently blocking a PR review's
    ``exclusive_key`` across three separate fresh task recreations despite
    the lane having free concurrency the entire time (aperture-labs#7890
    section 1) -- `reserve_spawn` doctor/diagnosis tooling reported it
    "healthy" the whole time, since it was never designed to look here.

    Deliberately narrower than the manual ``--force`` escape hatch: this
    sweep only ever acts when ``session_handle`` is genuinely absent (never
    ``--confirmed-absent``'s stronger claim about a *recorded* handle) and
    only past the bounded age above, so a release call that's merely still
    in flight is never cut off early.
    """
    now = time.time() if now is None else now
    recovered = 0
    for res in supervisor._pool_reservations(state=SpawnState.RELEASING):
        key = str(res.get("key") or "")
        if not key:
            continue
        if res.get("session_handle"):
            continue  # a real handle exists -- the liveness-checked path owns this
        reserved_at = res.get("reserved_at")
        updated_at = res.get("updated_at")
        anchor = updated_at if isinstance(updated_at, (int, float)) else reserved_at
        if not isinstance(anchor, (int, float)):
            continue  # no timestamp to bound against -- never guess
        if (now - anchor) < STRANDED_RELEASING_TIMEOUT_SECONDS:
            continue  # still within the ordinary release window
        try:
            task = supervisor.client.get(res["task_id"])
        except DispatchError:
            continue  # task vanished; leave the reservation for a human
        if not supervisor._matches_pool(task):
            continue
        try:
            supervisor.client.fail_spawn(
                key,
                force=True,
                detail=(
                    "auto-recovered by recover_stranded_releasing_reservations: "
                    f"RELEASING with no session_handle for "
                    f"{now - anchor:.0f}s (>= "
                    f"{STRANDED_RELEASING_TIMEOUT_SECONDS:.0f}s bound) -- "
                    "nothing an exact-absence proof could ever confirm, so "
                    "this would otherwise block its exclusive_key forever "
                    "(copilot-extensions#3179)"
                ),
            )
        except DispatchError:
            log.exception(
                "failed to auto-recover stranded releasing reservation %s", key
            )
            continue
        recovered += 1
        log.warning(
            "auto-recovered stranded releasing reservation %s for task %s "
            "(handle-less, stuck %.0fs -- copilot-extensions#3179)",
            key,
            task.get("id"),
            now - anchor,
        )
    return recovered
