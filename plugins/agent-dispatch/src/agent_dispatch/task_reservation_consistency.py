"""Declarative compatibility table for task ``Status`` vs. active
``SpawnState`` pairings.

This module is the direct answer to a structural-review finding (rubber-duck
review of agent-dispatch's task-status/spawn-reservation state management,
2026-09-28, following the incident-driven fixes in PR #4512 and its
follow-up #4542): three separate cross-FSM orphan gaps were each found only
*after* a task got stuck for hours in production (aperture-labs PR #7759's
COLD+QUEUED stall; two more found by inspection: reconcile()'s ``_TERMINAL``
missing ``CONFIRMED``, and ``recover_gone()``'s ``SPAWNED``-only reservation
loop missing ``DEAD_LETTER``+``COLD``). Each fix closed one specific pairing
after the fact. This module instead declares -- as data, once -- **every**
``(Status, SpawnState)`` pairing this codebase's sweeps are aware of, so the
*next* gap is caught by a periodic, read-only, additive consistency sweep
(mirroring :mod:`agent_dispatch.spawn_reservation_machine`'s own
reservation<->bridge consistency relation and its "fail-anomalous" rule:
any pairing not explicitly whitelisted as consistent is an anomaly) instead
of waiting for the next multi-hour production stall to surface it.

Scope is deliberately narrow, matching
:meth:`agent_dispatch.supervisor.Supervisor.sweep_spawn_consistency`'s own
precedent: only the four **active** ``SpawnState`` values
(``RESERVING``, ``SPAWNED``, ``COLD``, ``RELEASING`` --
:data:`agent_dispatch.queue.SpawnState.ACTIVE`) are modeled. A concluded
reservation (``SETTLED``/``FAILED``/``REARMED``/``DEFERRED``) is just a
historical audit record and may legitimately coexist with *any* task
status -- it carries no live claim on capacity or an exclusive_key, so
there is nothing to reconcile against.

This module is deliberately **detection-only**, not an auto-fixer (per the
rubber-duck review's own explicit caution): each declared repair mechanism
already has its own liveness evidence and cleanup disposition requirements
that a blind "normalize this pairing" mutator could not safely reproduce.
Where analysis below found a pairing with **no** existing repair path
(``COLD`` while ``PROPOSED``, ``CLAIMED``, or ``STARTED`` -- see
``_UNCOVERED_ANOMALIES``), this module only logs it; it does not invent an
unreproduced repair for a combination never yet observed live, matching how
every real fix in this area so far started from a live reproduction, not a
guess.
"""

from __future__ import annotations

import logging
from enum import Enum

from .queue import SpawnState, Status

log = logging.getLogger("agent-dispatch.supervisor")


#: The four "still active" reservation states this table covers -- see the
#: module docstring for why concluded states are out of scope.
ACTIVE_SPAWN_STATES: frozenset[str] = SpawnState.ACTIVE

#: Every task status, for completeness in the table below.
ALL_TASK_STATUSES: frozenset[str] = frozenset(
    {
        Status.PROPOSED,
        Status.QUEUED,
        Status.CLAIMED,
        Status.STARTED,
        Status.SUSPENDED,
        Status.COMPLETED,
        Status.CONFIRMED,
        Status.ABANDONED,
        Status.DEAD_LETTER,
    }
)


class TaskReservationTier(Enum):
    """The two-tier task-status/reservation-state consistency relation --
    deliberately binary (unlike
    :class:`spawn_reservation_machine.ConsistencyTier`'s three tiers): every
    pairing modeled here has BOTH a status and a reservation state by
    construction (the caller only classifies a reservation it already
    found), so there is no ``NOT_APPLICABLE`` case to carry."""

    #: A structurally expected pairing -- steady-state, a normal bounded
    #: transient, or a pairing at least one sweep is already known to
    #: revisit and correct.
    CONSISTENT = "consistent"
    #: A pairing no legal transition should produce and stay in -- flagged
    #: regardless of whether a repair sweep currently covers it (see
    #: :func:`covering_sweep` for that distinction).
    ANOMALY = "anomaly"





#: The explicit whitelist of consistent ``(Status, SpawnState)`` pairings,
#: restricted to :data:`ACTIVE_SPAWN_STATES`. Everything else is an anomaly
#: by default -- this module never assumes consistency without a traced
#: covering mechanism (see the per-state notes below and
#: ``_COVERING_SWEEP``).
#:
#: RESERVING and RELEASING are consistent with **every** status:
#: ``Supervisor.reconcile_reserving()`` and
#: ``Supervisor.release_requested_bodies()`` each revisit their whole
#: reservation-state pool unconditionally on the task's own status (gated
#: only on ``_matches_pool``/``release_requested`` respectively) -- neither
#: has ever needed a status carve-out, traced directly from their source.
_CONSISTENT_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {(status, SpawnState.RESERVING) for status in ALL_TASK_STATUSES}
    | {(status, SpawnState.RELEASING) for status in ALL_TASK_STATUSES}
    | {
        # SPAWNED: the normal embodied-worker shape for every status that
        # can hold one, plus every terminal/near-terminal status --
        # Supervisor.recover_gone() (SPAWNED-scoped) explicitly excludes
        # only _TERMINAL and SUSPENDED from its own gone/live liveness
        # loop, explicitly handles DEAD_LETTER, and everything else
        # (PROPOSED/QUEUED/CLAIMED/STARTED) falls through that same loop
        # -- so recover_gone() alone covers every status here except the
        # three now-widened _TERMINAL ones, which reconcile() covers
        # instead (its own RESERVING/SPAWNED/COLD pool, gated on
        # Supervisor._TERMINAL -- fixed in #4542 to include CONFIRMED).
        (Status.PROPOSED, SpawnState.SPAWNED),
        (Status.QUEUED, SpawnState.SPAWNED),
        (Status.CLAIMED, SpawnState.SPAWNED),
        (Status.STARTED, SpawnState.SPAWNED),
        (Status.SUSPENDED, SpawnState.SPAWNED),
        (Status.COMPLETED, SpawnState.SPAWNED),
        (Status.CONFIRMED, SpawnState.SPAWNED),
        (Status.ABANDONED, SpawnState.SPAWNED),
        (Status.DEAD_LETTER, SpawnState.SPAWNED),
        # COLD: only ever legitimately set while a task is SUSPENDED (see
        # Supervisor.cool_dormant_bodies's own status guard) -- the
        # steady-state dormant shape. QUEUED/DEAD_LETTER/COMPLETED/
        # CONFIRMED/ABANDONED are each covered by their own dedicated
        # sweep (recover_stranded_cold_reservations #4512,
        # recover_dead_lettered_cold_reservations #4542, and reconcile()'s
        # RESERVING/SPAWNED/COLD pool for the three _TERMINAL statuses).
        # PROPOSED/CLAIMED/STARTED have NO known covering sweep today --
        # see _UNCOVERED_ANOMALIES; they are deliberately left classified
        # as ANOMALY (the default) below, not added here.
        (Status.SUSPENDED, SpawnState.COLD),
        (Status.QUEUED, SpawnState.COLD),
        (Status.COMPLETED, SpawnState.COLD),
        (Status.CONFIRMED, SpawnState.COLD),
        (Status.ABANDONED, SpawnState.COLD),
        (Status.DEAD_LETTER, SpawnState.COLD),
    }
)

#: Anomalous pairings this module is aware of that have **no** known
#: covering sweep as of this writing (2026-09-28) -- traced directly from
#: every sweep's own source, not merely absence-of-evidence. Structurally
#: implausible in normal operation (``record_cold`` only ever fires for a
#: ``SUSPENDED`` task -- see ``Supervisor.cool_dormant_bodies``'s own guard
#: -- so reaching one of these requires a *second*, independent bug
#: elsewhere first), but a table exists precisely to say so out loud
#: rather than silently assume it can't happen. Never auto-repaired here
#: (see the module docstring); :func:`covering_sweep` reports these as
#: ``None`` so a caller can log them distinctly from a covered anomaly.
_UNCOVERED_ANOMALIES: frozenset[tuple[str, str]] = frozenset(
    {
        (Status.PROPOSED, SpawnState.COLD),
        (Status.CLAIMED, SpawnState.COLD),
        (Status.STARTED, SpawnState.COLD),
    }
)

#: Which method is known to revisit and correct each *covered* anomalous
#: pairing (i.e. every ``(status, SpawnState.COLD)`` pairing not in
#: ``_CONSISTENT_PAIRS`` and not in ``_UNCOVERED_ANOMALIES`` -- there are
#: currently none, since every COLD anomaly this module knows of is either
#: consistent or uncovered; kept as an explicit empty mapping so a future
#: addition to either set is a one-line, reviewable diff here too).
_COVERING_SWEEP: dict[tuple[str, str], str] = {}


def classify_task_reservation(
    task_status: str, spawn_state: str
) -> TaskReservationTier:
    """Classify one ``(task_status, spawn_state)`` pairing.

    Only meaningful for ``spawn_state in SpawnState.ACTIVE``
    (:data:`ACTIVE_SPAWN_STATES`) -- a concluded reservation state is
    always :attr:`TaskReservationTier.CONSISTENT` with every status (see
    the module docstring), since this table only models live capacity/
    exclusive_key claims.
    """
    if spawn_state not in ACTIVE_SPAWN_STATES:
        return TaskReservationTier.CONSISTENT
    if (task_status, spawn_state) in _CONSISTENT_PAIRS:
        return TaskReservationTier.CONSISTENT
    return TaskReservationTier.ANOMALY


def covering_sweep(task_status: str, spawn_state: str) -> str | None:
    """The method known to revisit and correct this anomalous pairing, or
    ``None`` when classification is :attr:`TaskReservationTier.CONSISTENT`
    (nothing to cover) **or** the pairing is a known-uncovered anomaly
    (:data:`_UNCOVERED_ANOMALIES`) -- distinguish the two via
    :func:`classify_task_reservation` first."""
    return _COVERING_SWEEP.get((task_status, spawn_state))


def is_known_uncovered(task_status: str, spawn_state: str) -> bool:
    """Whether this anomalous pairing has no known covering sweep today."""
    return (task_status, spawn_state) in _UNCOVERED_ANOMALIES


def sweep(supervisor) -> dict[str, int]:
    """Live wiring of this module's compatibility table against the pool's
    real, current reservation rows -- the task-status counterpart to
    :meth:`Supervisor.sweep_spawn_consistency`.

    Read-only and additive, exactly like that sibling sweep: this never
    mutates a reservation or a task, and changes no other reconciliation
    method's behavior. It only classifies today's real
    ``(task_status, spawn_state)`` pairings and logs any anomaly found --
    a ``covered`` one at ``warning`` (a repair sweep should clear it on
    its own next cycle; seeing one here at all is worth watching, since it
    means a repair is either mid-flight or not actually firing) and an
    ``uncovered`` one at ``error`` (no sweep will ever clear this one --
    see :data:`_UNCOVERED_ANOMALIES`; this is the exact structural alarm
    this module exists to raise before it becomes another multi-hour
    stall). ``supervisor`` is a :class:`agent_dispatch.supervisor.Supervisor`
    instance; this module has no dependency on that class beyond the
    attributes it reads.

    Returns a summary dict: ``checked``, ``anomalies``, ``covered``
    (an anomaly with a known repair sweep), ``uncovered`` (one without).
    """
    from .client import DispatchError

    checked = 0
    anomalies = 0
    covered = 0
    uncovered = 0
    active_state_filter = ",".join(sorted(ACTIVE_SPAWN_STATES))
    for res in supervisor._pool_reservations(state=active_state_filter):
        key = str(res.get("key") or "")
        spawn_state = str(res.get("state") or "")
        if not key or spawn_state not in ACTIVE_SPAWN_STATES:
            continue
        try:
            task = supervisor.client.get(res["task_id"])
        except DispatchError:
            continue  # task vanished; nothing live to classify against
        if not supervisor._matches_pool(task):
            continue
        task_status = str(task.get("status") or "")
        checked += 1
        tier = classify_task_reservation(task_status, spawn_state)
        if tier is not TaskReservationTier.ANOMALY:
            continue
        anomalies += 1
        if is_known_uncovered(task_status, spawn_state):
            uncovered += 1
            log.error(
                "task-reservation consistency: %s is %r with NO known "
                "covering sweep for task %s (status %r) -- this pairing "
                "will never self-heal; needs a dedicated repair once "
                "reproduced live (see task_reservation_consistency.py's "
                "_UNCOVERED_ANOMALIES)",
                key,
                spawn_state,
                task.get("id"),
                task_status,
            )
        else:
            covered += 1
            sweep_name = covering_sweep(task_status, spawn_state)
            log.warning(
                "task-reservation consistency: %s is %r while task %s is "
                "%r -- an anomalous pairing, expected to clear via %s on "
                "an upcoming cycle",
                key,
                spawn_state,
                task.get("id"),
                task_status,
                sweep_name or "an as-yet-unnamed covering sweep",
            )
    return {
        "checked": checked,
        "anomalies": anomalies,
        "covered": covered,
        "uncovered": uncovered,
    }

