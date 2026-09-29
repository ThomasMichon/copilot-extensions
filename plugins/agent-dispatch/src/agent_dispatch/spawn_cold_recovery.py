"""Recover a ``COLD`` spawn reservation whose task ended up queued/unowned,
or reached ``DEAD_LETTER``, without going through the sanctioned resume or
settlement path.

Extracted out of :mod:`agent_dispatch.supervisor` (module-size discipline --
see ``AGENTS.md``'s note on new additions going into a sibling module rather
than growing an already-near-ceiling file further) as its own coherent
responsibility, mirroring how :mod:`agent_dispatch.idle_confirm` holds
``nudge_idle_headless_tasks`` for the same reason. ``supervisor`` below is a
:class:`agent_dispatch.supervisor.Supervisor` instance; this module has no
dependency on that class beyond the attributes it reads.
"""

from __future__ import annotations

import logging
from typing import Any

from .client import DispatchError
from .queue import SpawnState, Status
from .spawn_factories import _parse_fleet_body_handle, _parse_local_body_handle

log = logging.getLogger("agent-dispatch.supervisor")

#: Settled by reconcile()/dead-lettering elsewhere -- never this sweep's job.
#: Kept identical to :data:`agent_dispatch.supervisor._TERMINAL` (includes
#: COMPLETED as of 2026-09-28, rubber-duck review -- see that constant's own
#: comment for why DEAD_LETTER is deliberately excluded from this set even
#: though it's also settled elsewhere, by recover_dead_lettered_cold_reservations
#: below).
_TERMINAL = frozenset({Status.SUBMITTED, Status.COMPLETED, Status.ABANDONED})


def recover_stranded_cold_reservations(supervisor: Any) -> int:
    """Release a ``COLD`` reservation whose task ended up queued/unowned
    instead of the ``SUSPENDED``-with-owner shape every other cold-path
    sweep expects -- a permanent-orphan gap with no automatic recovery.

    A reservation goes ``COLD`` when its task is intentionally dormant (see
    :meth:`Supervisor.cool_dormant_bodies`), resumed later by
    :meth:`Supervisor.release_resumed_cold_tasks` -- but that resume path
    only fires for a reservation whose ``resume_requested`` flag is set (by
    ``submit_steer()``, itself gated on the task still being ``SUSPENDED``
    at the moment the steer lands) **and** whose task is still ``SUSPENDED``
    with a live owner when the sweep runs. Neither condition holds once the
    task ends up ``QUEUED``/unowned *before* a resume-triggering steer
    arrives -- at that point ``submit_steer()``'s resume branch is a no-op
    (status is no longer ``SUSPENDED``), so ``resume_requested`` never gets
    set, and :meth:`Supervisor.release_resumed_cold_tasks` would ALSO skip
    it even if it somehow did (it requires ``status == SUSPENDED`` and an
    owner). Nor does :meth:`Supervisor.recover_gone` cover it
    (``SPAWNED``-reservations only, explicitly skips ``SUSPENDED``), nor
    :meth:`Supervisor.reconcile` (terminal tasks only). The result: a
    ``COLD`` reservation whose task already went queued/unowned is a dead
    zone no automatic path ever revisits again -- confirmed live: a PR
    review sat unrecoverable for 5+ hours, surviving a full supervisor
    restart, until an operator manually failed the reservation by hand
    (aperture-labs PR #7759's stall).

    The exact trigger that reclaims a dormant ``SUSPENDED`` task to
    ``QUEUED``/unowned ahead of its resume is, as of this writing, **not
    confirmed against the live call graph** -- an earlier revision of this
    docstring attributed it to "the coordinator's own lease-expiry GC," but
    no in-repo path was found that matches: ``reconcile_liveness`` only
    scans ``CLAIMED``/``STARTED`` tasks, never ``SUSPENDED``, and a
    ``SUSPENDED`` task carries no lease at all (``lease_expires_at`` is
    cleared on entry). Treat the trigger as unknown pending further tracing
    (rubber-duck review, 2026-09-28) -- this sweep repairs the resulting
    invariant violation (``QUEUED``/unowned task, confirmed-gone ``COLD``
    reservation) regardless of how a future case gets there.

    This closes that gap: any ``COLD`` reservation whose task is genuinely
    available (``QUEUED``, no owner, non-terminal) with a **confirmed**
    -gone/idle body is deferred here (not failed -- see the ``defer_spawn``
    call below), freeing its ``exclusive_key`` so the
    next :meth:`Supervisor.poll_once` reserves and embodies it fresh (the
    replacement resumes from the task's own ``progress_log``, same as every
    other confirmed-gone recovery in that class). Never acts on an
    unconfirmed/unknown liveness verdict, and never touches a reservation
    whose task is still genuinely ``SUSPENDED`` (that dormancy is
    intentional and stays :meth:`Supervisor.release_resumed_cold_tasks`'s
    job) or still actively owned.
    """
    from . import tracking

    recovered = 0
    for res in supervisor._pool_reservations(state=SpawnState.COLD):
        key = str(res.get("key") or "")
        if not key:
            continue
        try:
            task = supervisor.client.get(res["task_id"])
        except DispatchError:
            continue  # task vanished; leave the reservation for a human
        if not supervisor._matches_pool(task):
            continue
        status = task.get("status")
        if status in _TERMINAL or status == Status.DEAD_LETTER:
            continue  # settled by reconcile()/dead-lettering, not here
        if status != Status.QUEUED or task.get("owner"):
            continue  # still genuinely dormant or owned -- not this gap
        fleet = _parse_fleet_body_handle(res.get("session_handle"))
        local_sid = _parse_local_body_handle(res.get("session_handle"))
        confirmed_gone = False
        try:
            if fleet is not None:
                confirmed_gone = (
                    supervisor.fleet_verdict_fn(*fleet) == tracking.GONE
                    or supervisor.fleet_cold_fn(*fleet)
                )
            elif local_sid is not None:
                confirmed_gone = (
                    supervisor.local_body_verdict_fn(local_sid) == tracking.GONE
                    or supervisor.local_cold_fn(local_sid)
                )
            else:
                continue  # no recognizable handle -- never guess
        except Exception:
            continue  # liveness is best-effort -- never fatal, never act
        if not confirmed_gone:
            continue
        try:
            # `defer_spawn`, not `fail_spawn`: this reservation didn't fail --
            # its task/reservation bookkeeping fell out of sync while the body
            # was intentionally stopped. `fail_spawn` counts toward
            # `Supervisor._failed_spawn_counts`'s dead-letter budget
            # (rubber-duck review, 2026-09-28); repeatedly repairing this
            # same benign cross-FSM inconsistency for a task that never
            # actually failed a spawn attempt would eventually dead-letter it
            # on repair debt alone. `defer_spawn` releases the same capacity
            # for a fresh attempt without ever counting toward that cap --
            # exactly the "declined, not failed" semantics it already exists
            # for (see its own docstring).
            supervisor.client.defer_spawn(
                key,
                detail=(
                    "stranded cold reservation: task reclaimed to "
                    "queued/unowned before a resume-triggering steer "
                    "arrived; confirmed body absence, releasing for a "
                    "fresh attempt"
                ),
            )
        except DispatchError:
            log.exception(
                "failed to release stranded cold reservation %s", key
            )
            continue
        recovered += 1
        log.warning(
            "released stranded cold reservation %s for queued task %s "
            "(no automatic path had covered this queued+cold "
            "combination)",
            key,
            task.get("id"),
        )
    return recovered


def recover_dead_lettered_cold_reservations(supervisor: Any) -> int:
    """Settle a ``COLD`` reservation whose task reached ``DEAD_LETTER``.

    :meth:`Supervisor.recover_gone` already settles this exact combination
    (task ``DEAD_LETTER``, "held, not re-spawned") -- but only for a
    ``SPAWNED`` reservation, since it iterates
    ``self._pool_reservations(state=SpawnState.SPAWNED)`` (rubber-duck
    review, 2026-09-28). A reservation that was ``COLD`` (its body
    intentionally stopped while the task was dormant) when its task's owner
    later went gone and exhausted ``max_attempts`` into ``DEAD_LETTER``
    never enters that loop at all, so its reservation is never settled --
    fencing its ``exclusive_key`` forever, the same permanent-orphan shape
    :func:`recover_stranded_cold_reservations` above closes for the
    ``QUEUED``/unowned case.

    Deliberately its own narrow sweep rather than widening
    :meth:`Supervisor.recover_gone`'s own ``SPAWNED``-scoped loop to also
    accept ``COLD``: that loop's body assumes an actively-embodied session
    (fleet/local liveness probing, on-behalf ``yield_task``) that isn't the
    right shape for an intentionally-stopped ``COLD`` body, and widening it
    would have this sweep and :func:`recover_stranded_cold_reservations`
    both racing to act on the same ``COLD`` reservation for a task that
    isn't ``DEAD_LETTER`` (e.g. ``QUEUED``/unowned) -- disjoint scopes (this
    function only ever matches ``DEAD_LETTER``) avoid that entirely. Uses
    :meth:`~agent_dispatch.client.Client.settle_spawn`, matching
    :meth:`Supervisor.recover_gone`'s own ``DEAD_LETTER`` handling exactly
    (a held, terminal-failure task is settled outright, never re-spawned --
    unlike the ``QUEUED`` case above, there is no "fresh attempt" to defer
    into).
    """
    settled = 0
    for res in supervisor._pool_reservations(state=SpawnState.COLD):
        key = str(res.get("key") or "")
        if not key:
            continue
        try:
            task = supervisor.client.get(res["task_id"])
        except DispatchError:
            continue  # task vanished; leave the reservation for a human
        if not supervisor._matches_pool(task):
            continue
        if task.get("status") != Status.DEAD_LETTER:
            continue  # not this gap -- every other status has its own sweep
        try:
            supervisor.client.settle_spawn(key, detail="task dead_lettered")
        except DispatchError:
            log.exception(
                "failed to settle dead-lettered cold reservation %s", key
            )
            continue
        settled += 1
        log.warning(
            "settled cold reservation %s for dead-lettered task %s "
            "(recover_gone() only covers this for a spawned reservation)",
            key,
            task.get("id"),
        )
    return settled
