"""Declarative transition table for the spawn-reservation/allocation-fencing
layer, coupled to the bridge machine.

This module is the Phase 9 (``review-automation-reliability`` effort,
``efforts/active/review-automation-reliability/phase-9-state-machine-architecture.md``
"Assignment: the reservation/allocation-fencing layer" section) made real.
``agent_dispatch.queue.SpawnReservation``/``SpawnState`` already implement
this lifecycle and its guarantees in real, working code
(``reserve_spawn``, ``fail_spawn``, ``defer_spawn``, ``settle_spawn``,
``retire_spawn``, ``rearm_spawn``); this module does not change any of
that runtime behavior. It declares the same shape
:mod:`agent_dispatch.task_state_machine` and
:mod:`agent_dispatch.bridge_state_machine` already used -- every legal
transition named once, as data, tagged with a recovery mode -- **plus**
two properties this layer needs that a bare transition table cannot
express on its own:

1. **The single-assignment invariant.** No second reservation may be
   minted while one is already :data:`agent_dispatch.queue.SpawnState.ACTIVE`
   for the same task, or -- when an ``exclusive_key`` is set -- for the
   same exclusive-key group. ``reserve_spawn`` already atomically enforces
   this under one write lock; :func:`violating_assignment_groups` makes it
   a checkable structural property over a snapshot of reservation rows,
   rather than "true because the SQL transaction happens to be written
   correctly."
2. **The reservation<->bridge consistency relation.** A reservation's
   ``SpawnState`` is a claim about its bridge; the bridge's own
   :class:`~agent_dispatch.bridge_state_machine.BridgeState` is the fresh,
   live-probed fact. :func:`classify_consistency` names, for every
   ``(SpawnState, BridgeState)`` pairing this module is aware of, whether
   it is not-applicable, consistent, or an anomaly -- **any pairing not
   explicitly whitelisted as consistent is an anomaly**, matching Phase
   9's general rule of never assuming the safer or more convenient state
   without positive evidence.

This module also declares the closed "letting go" vocabulary
(:class:`LetGoReason`): every named way a reservation exits ``ACTIVE``
already goes through one of ``queue.py``'s ``fail_spawn`` / ``defer_spawn``
/ ``settle_spawn`` / ``retire_spawn`` methods, each requiring a
caller-supplied reason -- never a silent drop. :data:`LET_GO_REASON_TRANSITION`
and :data:`LET_GO_REASON_RECOVERY_MODE` name which declared transition and
recovery mode each reason maps to.

Deliberately out of scope (per the sub-doc): the launch-to-claim
grace/recovery monitor -- a distinct component with its own internal
state, consuming this module's contract from the outside. This module
declares the contract; it does not declare that component.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .bridge_state_machine import BridgeState
from .queue import SpawnState
from .task_state_machine import RecoveryMode

#: This machine's states, sourced directly from
#: :class:`agent_dispatch.queue.SpawnState` rather than duplicated.
ALL_SPAWN_STATES: frozenset[str] = frozenset(
    {
        SpawnState.RESERVING,
        SpawnState.SPAWNED,
        SpawnState.COLD,
        SpawnState.RELEASING,
        SpawnState.SETTLED,
        SpawnState.FAILED,
        SpawnState.REARMED,
        SpawnState.DEFERRED,
    }
)
INITIAL_SPAWN_STATE = SpawnState.RESERVING

#: States with zero declared outgoing transition below. Note this is
#: **not** the same set as ``SpawnState.RELEASABLE`` -- ``FAILED`` is
#: releasable (a fresh attempt may be reserved) but is not machine-terminal
#: here, because ``rearm`` is a real declared exit from it.
SPAWN_TERMINAL_STATES: frozenset[str] = frozenset(
    {SpawnState.SETTLED, SpawnState.DEFERRED, SpawnState.REARMED}
)


@dataclass(frozen=True)
class SpawnTransition:
    """One legal reservation-lifecycle move, as declared data."""

    name: str
    from_states: frozenset[str]
    to_state: str
    recovery_mode: RecoveryMode
    #: Where this transition is implemented, for traceability.
    implemented_by: str


SPAWN_TRANSITIONS: tuple[SpawnTransition, ...] = (
    SpawnTransition(
        name="confirm_spawn",
        from_states=frozenset({SpawnState.RESERVING, SpawnState.SPAWNED, SpawnState.COLD}),
        to_state=SpawnState.SPAWNED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.record_spawn (embody launched, handle recorded)",
    ),
    SpawnTransition(
        name="go_cold",
        from_states=frozenset({SpawnState.SPAWNED, SpawnState.COLD}),
        to_state=SpawnState.COLD,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.record_cold (intentional stop while task suspended)",
    ),
    SpawnTransition(
        name="request_release",
        from_states=frozenset({SpawnState.RESERVING, SpawnState.SPAWNED, SpawnState.COLD}),
        to_state=SpawnState.RELEASING,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.request_spawn_release (fences until exact absence proof)",
    ),
    SpawnTransition(
        name="fail",
        from_states=(SpawnState.ACTIVE - frozenset({SpawnState.RELEASING}))
        | frozenset({SpawnState.FAILED}),
        to_state=SpawnState.FAILED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.fail_spawn (idempotent on an already-failed row)",
    ),
    SpawnTransition(
        name="defer",
        from_states=SpawnState.ACTIVE - frozenset({SpawnState.RELEASING}),
        to_state=SpawnState.DEFERRED,
        recovery_mode=RecoveryMode.SELF_RECOVERING,
        implemented_by="TaskQueue.defer_spawn (carried session confirmed live/busy)",
    ),
    SpawnTransition(
        name="settle_direct",
        from_states=SpawnState.ACTIVE - frozenset({SpawnState.RELEASING}),
        to_state=SpawnState.SETTLED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.settle_spawn (task reached a terminal outcome)",
    ),
    SpawnTransition(
        name="retire_to_settled",
        from_states=frozenset({SpawnState.RELEASING}),
        to_state=SpawnState.SETTLED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.retire_spawn (exact absence proof, settled disposition)",
    ),
    SpawnTransition(
        name="retire_to_failed",
        from_states=frozenset({SpawnState.RELEASING}),
        to_state=SpawnState.FAILED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.retire_spawn (exact absence proof, failed disposition)",
    ),
    SpawnTransition(
        name="rearm",
        from_states=frozenset({SpawnState.FAILED}),
        to_state=SpawnState.REARMED,
        recovery_mode=RecoveryMode.SELF_REPAIR,
        implemented_by="TaskQueue.rearm_spawn (explicit, permission-gated operator override)",
    ),
)


def reachable_states(start: str = INITIAL_SPAWN_STATE) -> frozenset[str]:
    """Every state reachable from ``start`` by declared transitions."""
    seen = {start}
    frontier = {start}
    while frontier:
        nxt: set[str] = set()
        for transition in SPAWN_TRANSITIONS:
            if transition.from_states & frontier:
                nxt.add(transition.to_state)
        nxt -= seen
        seen |= nxt
        frontier = nxt
    return frozenset(seen)


def states_without_exit() -> frozenset[str]:
    """Non-terminal states with zero outgoing declared transition."""
    states_with_exit = {s for t in SPAWN_TRANSITIONS for s in t.from_states}
    return frozenset(ALL_SPAWN_STATES - SPAWN_TERMINAL_STATES - states_with_exit)


def terminal_states_with_exit() -> frozenset[str]:
    """Terminal states that (incorrectly) still have a declared exit."""
    states_with_exit = {s for t in SPAWN_TRANSITIONS for s in t.from_states}
    return frozenset(SPAWN_TERMINAL_STATES & states_with_exit)


class LetGoReason(Enum):
    """The closed, reasoned vocabulary for every way a reservation exits
    ``ACTIVE``. Every named reason maps to a real declared transition
    above -- there is no "none of the above" member."""

    FAILED = "failed"
    DEFERRED = "deferred"
    SETTLED = "settled"
    RETIRED_REARM = "retired_rearm"
    #: A different claim won the task while this reservation was still
    #: RESERVING/unclaimed-SPAWNED. Distinct from FAILED: the reservation
    #: agent-dispatch itself assigned is gracefully terminated and
    #: released with a preempted conclusion -- never the winning
    #: claimant's own reservation.
    PREEMPTED = "preempted"


#: Which declared transition each reason maps to. Preemption resolves
#: through the same release path as an ordinary graceful settle, tagged
#: with its own reason for the audit trail rather than a distinct
#: transition.
LET_GO_REASON_TRANSITION: dict[LetGoReason, str] = {
    LetGoReason.FAILED: "fail",
    LetGoReason.DEFERRED: "defer",
    LetGoReason.SETTLED: "settle_direct",
    LetGoReason.RETIRED_REARM: "rearm",
    LetGoReason.PREEMPTED: "request_release",
}

#: Recovery mode per reason, exactly as the sub-doc classifies each.
LET_GO_REASON_RECOVERY_MODE: dict[LetGoReason, RecoveryMode] = {
    LetGoReason.FAILED: RecoveryMode.SAFE_RETRY,
    LetGoReason.DEFERRED: RecoveryMode.SELF_RECOVERING,
    LetGoReason.SETTLED: RecoveryMode.SAFE_RETRY,
    LetGoReason.RETIRED_REARM: RecoveryMode.SELF_REPAIR,
    LetGoReason.PREEMPTED: RecoveryMode.SELF_RECOVERING,
}


@dataclass(frozen=True)
class ReservationRecord:
    """A minimal snapshot of one reservation row, for the single-assignment
    check below. Deliberately narrow -- only the fields that check needs."""

    key: str
    task_id: str
    state: str
    exclusive_key: str | None = None


def violating_assignment_groups(
    reservations: tuple[ReservationRecord, ...],
) -> frozenset[str]:
    """The single-assignment invariant, made checkable: return every
    grouping key (a task id, or ``exclusive:<key>`` for an exclusive-key
    group) that has **more than one** simultaneously
    :data:`agent_dispatch.queue.SpawnState.ACTIVE` reservation. An empty
    result means the invariant holds."""
    by_task: dict[str, int] = {}
    by_exclusive: dict[str, int] = {}
    for record in reservations:
        if record.state not in SpawnState.ACTIVE:
            continue
        by_task[record.task_id] = by_task.get(record.task_id, 0) + 1
        if record.exclusive_key is not None:
            by_exclusive[record.exclusive_key] = by_exclusive.get(record.exclusive_key, 0) + 1
    violations = {task_id for task_id, count in by_task.items() if count > 1}
    violations |= {f"exclusive:{key}" for key, count in by_exclusive.items() if count > 1}
    return frozenset(violations)


class ConsistencyTier(Enum):
    """The three-tier reservation<->bridge consistency relation."""

    #: No reservation exists for the task, or the live body isn't bound to
    #: any specific reservation right now. Never an anomaly -- the check
    #: is scoped per-reservation and simply does not apply.
    NOT_APPLICABLE = "not_applicable"
    #: A reservation names a specific live process and the fresh bridge
    #: read agrees.
    CONSISTENT = "consistent"
    #: A reservation claims a specific live process and the fresh read
    #: contradicts it.
    ANOMALY = "anomaly"


#: The explicit whitelist of consistent (SpawnState, BridgeState) pairings.
#: Everything not listed here is an anomaly by default -- this module never
#: assumes consistency without positive evidence. ``(RESERVING, RUNNING)``
#: is handled separately below since its consistency additionally depends
#: on ``worktree_ownership``.
_CONSISTENT_PAIRS: frozenset[tuple[str, BridgeState]] = frozenset(
    {
        (SpawnState.RESERVING, BridgeState.ABSENT),
        (SpawnState.SPAWNED, BridgeState.HYDRATING),
        (SpawnState.SPAWNED, BridgeState.RUNNING),
        (SpawnState.COLD, BridgeState.SUSPENDED),
        (SpawnState.COLD, BridgeState.ABSENT),
        (SpawnState.RELEASING, BridgeState.SUSPENDED),
        (SpawnState.RELEASING, BridgeState.RUNNING),
        (SpawnState.RELEASING, BridgeState.ABSENT),
        (SpawnState.RELEASING, BridgeState.ENDED),
        (SpawnState.SETTLED, BridgeState.ABSENT),
        (SpawnState.SETTLED, BridgeState.ENDED),
        (SpawnState.FAILED, BridgeState.ABSENT),
        (SpawnState.FAILED, BridgeState.ENDED),
        (SpawnState.REARMED, BridgeState.ABSENT),
        (SpawnState.REARMED, BridgeState.ENDED),
        (SpawnState.DEFERRED, BridgeState.RUNNING),
        (SpawnState.DEFERRED, BridgeState.HYDRATING),
    }
)

#: Ownership values that make ``(RESERVING, RUNNING)`` consistent: the
#: reservation is deliberately carrying forward a still-live session
#: rather than about to spawn a fresh one.
_RESERVING_RUNNING_CONSISTENT_OWNERSHIP: frozenset[str] = frozenset({"reused"})


def classify_consistency(
    spawn_state: str | None,
    bridge_state: BridgeState | None,
    *,
    worktree_ownership: str | None = None,
) -> ConsistencyTier:
    """Classify one reservation's consistency against a fresh bridge read.

    ``spawn_state`` or ``bridge_state`` being ``None`` means the check
    does not apply (no reservation exists, or the live body isn't bound to
    one) -- :attr:`ConsistencyTier.NOT_APPLICABLE`, never an anomaly.
    Every other pairing is :attr:`ConsistencyTier.ANOMALY` unless it is
    explicitly whitelisted as :attr:`ConsistencyTier.CONSISTENT` --
    deliberately fail-anomalous rather than fail-consistent.
    """
    if spawn_state is None or bridge_state is None:
        return ConsistencyTier.NOT_APPLICABLE
    if spawn_state == SpawnState.RESERVING and bridge_state is BridgeState.RUNNING:
        if worktree_ownership in _RESERVING_RUNNING_CONSISTENT_OWNERSHIP:
            return ConsistencyTier.CONSISTENT
        return ConsistencyTier.ANOMALY
    if (spawn_state, bridge_state) in _CONSISTENT_PAIRS:
        return ConsistencyTier.CONSISTENT
    return ConsistencyTier.ANOMALY


#: Maps :mod:`agent_dispatch.tracking`'s coarse liveness verdicts
#: (``live``/``gone``/``unknown``) to a :class:`BridgeState` for
#: :func:`classify_consistency`. This is deliberately coarse: the local
#: supervisor's tracking module only distinguishes a live process from a
#: gone one, not the finer HYDRATING/SUSPENDED/ENDED distinctions
#: ``bridge_state_machine.py`` declares -- ``unknown`` maps to ``None``
#: (not-applicable) rather than guessing, per Phase 9's "never assume the
#: safer state without evidence" rule.
_VERDICT_TO_BRIDGE_STATE: dict[str, BridgeState | None] = {
    "live": BridgeState.RUNNING,
    "gone": BridgeState.ABSENT,
    "unknown": None,
}


def verdict_to_bridge_state(verdict: str) -> BridgeState | None:
    """Translate a tracking-module liveness verdict to a coarse
    :class:`BridgeState`, or ``None`` when the verdict carries no
    positive evidence either way (``unknown``, or any value this module
    does not recognize)."""
    return _VERDICT_TO_BRIDGE_STATE.get(verdict)
