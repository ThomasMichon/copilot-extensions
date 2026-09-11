"""Declarative transition table for the dispatch task state machine.

This module is the Phase 9 (``review-automation-reliability`` effort,
``efforts/active/review-automation-reliability/phase-9-state-machine-architecture.md``)
task-machine declaration made real: rather than let each transition's legal
callers and recovery behavior live only as scattered ``_transition(...)``
call sites in :mod:`agent_dispatch.queue`, this module names every legal
transition once, as data, tagged with the recovery mode it is classified
under. It reconciles Phase 1's original reviewer-flavored state list
(requested / claimed / analyzing / awaiting-steer / ready / submitted /
failed / abandoned) with the actual, already-implemented generic task
states in :class:`agent_dispatch.queue.Status`: the reviewer-flavored names
were a specific consumer's projection of this same eight-state machine.

The three recovery modes are exactly Phase 9's taxonomy:

- ``SELF_RECOVERING``: the system's own next evaluation reaches the correct
  state without external action.
- ``SAFE_RETRY``: idempotent replay of the same transition is the correct
  remedy (every transition below is CAS-guarded by ``queue.py``'s
  generation/lease fencing, so a duplicate request is a no-op, not a
  duplicate effect).
- ``SELF_REPAIR``: the system must actively reconcile an inconsistent
  intermediate state before proceeding (e.g. a carried spawn reservation
  whose bridge/session status disagrees with the task's assumed state --
  the ``reconcile_reserving`` class of gap this effort's Phase 5 fixed one
  instance of).

This module declares transitions; it does not change ``queue.py``'s
runtime behavior. Its purpose is to make the machine's shape checkable
(every state reachable, no non-terminal state without an exit, every
transition carrying exactly one recovery mode) independent of the code
that executes it, and to give the Phase 9 simulation/test track a
declared table to drive against.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .queue import Status


class RecoveryMode(Enum):
    """Phase 9's recovery taxonomy, applied to a single transition."""

    SELF_RECOVERING = "self_recovering"
    SAFE_RETRY = "safe_retry"
    SELF_REPAIR = "self_repair"


@dataclass(frozen=True)
class Transition:
    """One legal transition: a named move from a set of source states."""

    name: str
    from_states: frozenset[str]
    to_state: str
    recovery_mode: RecoveryMode
    #: Where this transition is implemented, for traceability back to the
    #: executing code (``queue.py`` line references drift; method names do
    #: not).
    implemented_by: str


#: All states this machine declares. Sourced directly from
#: :class:`agent_dispatch.queue.Status` rather than duplicated, so this
#: table cannot silently drift from the real state set.
ALL_STATES: frozenset[str] = frozenset(
    {
        Status.PROPOSED,
        Status.QUEUED,
        Status.CLAIMED,
        Status.STARTED,
        Status.SUSPENDED,
        Status.COMPLETED,
        Status.ABANDONED,
        Status.DEAD_LETTER,
    }
)

#: Terminal states carry no outgoing transition -- sourced from
#: ``Status.TERMINAL`` for the same reason.
TERMINAL_STATES: frozenset[str] = Status.TERMINAL

#: The initial state a freshly created task is admitted in.
INITIAL_STATE = Status.PROPOSED

#: The declared transition table. Every legal move ``queue.py`` implements
#: today is named here exactly once.
TRANSITIONS: tuple[Transition, ...] = (
    Transition(
        name="approve",
        from_states=frozenset({Status.PROPOSED}),
        to_state=Status.QUEUED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.approve",
    ),
    Transition(
        name="claim",
        from_states=frozenset({Status.QUEUED}),
        to_state=Status.CLAIMED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.claim (atomic conditional UPDATE)",
    ),
    Transition(
        name="start",
        from_states=frozenset({Status.CLAIMED}),
        to_state=Status.STARTED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.start",
    ),
    Transition(
        name="suspend",
        from_states=frozenset({Status.STARTED}),
        to_state=Status.SUSPENDED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.suspend",
    ),
    Transition(
        name="resume",
        from_states=frozenset({Status.SUSPENDED}),
        to_state=Status.STARTED,
        recovery_mode=RecoveryMode.SELF_REPAIR,
        implemented_by="TaskQueue.resume",
    ),
    Transition(
        name="release_suspended",
        from_states=frozenset({Status.SUSPENDED}),
        to_state=Status.QUEUED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.release_suspended",
    ),
    Transition(
        name="requeue_held",
        from_states=Status.HELD,
        to_state=Status.QUEUED,
        recovery_mode=RecoveryMode.SELF_REPAIR,
        implemented_by="TaskQueue.release / liveness GC (owner-gone reconciliation)",
    ),
    Transition(
        name="dead_letter_held",
        from_states=Status.HELD,
        to_state=Status.DEAD_LETTER,
        recovery_mode=RecoveryMode.SELF_REPAIR,
        implemented_by="TaskQueue liveness GC (attempt cap exceeded)",
    ),
    Transition(
        name="complete",
        #: Phase 10's live-wiring pass found the real
        #: ``TaskQueue.complete_with_outcome`` allows completing a
        #: ``suspended`` task directly (a suspended task may resolve while
        #: no worker process is running -- e.g. an awaited external
        #: condition became true -- and forcing a fake resume/active turn
        #: solely to reach the terminal state would be worse). The
        #: original declaration only named ``started``; corrected here to
        #: match the real, already-working behavior rather than the other
        #: way around.
        from_states=frozenset({Status.STARTED, Status.SUSPENDED}),
        to_state=Status.COMPLETED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.complete_with_outcome",
    ),
    Transition(
        name="abandon",
        from_states=Status.ABANDONABLE,
        to_state=Status.ABANDONED,
        recovery_mode=RecoveryMode.SAFE_RETRY,
        implemented_by="TaskQueue.abandon (requires permitted=True)",
    ),
)

#: Lookup by name, for callers (Phase 10's live-wiring: ``queue.py``'s own
#: transition call sites) that want to source a transition's declared
#: ``from_states``/``to_state`` rather than duplicating them inline.
TRANSITIONS_BY_NAME: dict[str, Transition] = {t.name: t for t in TRANSITIONS}


def reachable_states(start: str = INITIAL_STATE) -> frozenset[str]:
    """Every state reachable from ``start`` by declared transitions."""
    seen = {start}
    frontier = {start}
    while frontier:
        nxt: set[str] = set()
        for transition in TRANSITIONS:
            if transition.from_states & frontier:
                nxt.add(transition.to_state)
        nxt -= seen
        seen |= nxt
        frontier = nxt
    return frozenset(seen)


def states_without_exit() -> frozenset[str]:
    """Non-terminal states with zero outgoing declared transition."""
    states_with_exit = {state for transition in TRANSITIONS for state in transition.from_states}
    return frozenset(ALL_STATES - TERMINAL_STATES - states_with_exit)


def terminal_states_with_exit() -> frozenset[str]:
    """Terminal states that (incorrectly) still have a declared exit."""
    states_with_exit = {state for transition in TRANSITIONS for state in transition.from_states}
    return frozenset(TERMINAL_STATES & states_with_exit)


class SteerOutcome(Enum):
    """The steer-vs-transition race scenario's dual-outcome resolution.

    ``TaskQueue.submit_steer`` (``queue.py``) already implements exactly
    this dual outcome for a submitted steer against a suspended task: an
    interactive owner is woken directly, a headless one is released for
    re-embodiment. Declaring it here as checkable data -- rather than the
    ``VersionedRecord``/``SuspendReason`` scaffolding an earlier design
    pass proposed -- is what this module adds; the mechanism itself is
    real and unchanged.
    """

    #: A suspended task with an interactive owner is atomically resumed to
    #: STARTED, preserving its owner -- the same dual-outcome shape
    #: :data:`agent_dispatch.machine_coupling.TASK_TRANSITION_BRIDGE_CONFIRMATION`
    #: already uses for ``"resume"``.
    RESUME_WITH_WAKE = "resume_with_wake"
    #: A suspended headless task has no interactive inbox to wake, so it
    #: is instead released to QUEUED for safe re-embodiment.
    RELEASE_TO_QUEUED = "release_to_queued"


#: Which declared ``TRANSITIONS`` name a given steer outcome maps to. Both
#: names must be real transitions already declared above -- this is what
#: keeps the steer-outcome table coupled to the lifecycle table instead of
#: an independently declared parallel fact.
STEER_OUTCOME_TRANSITION: dict[SteerOutcome, str] = {
    SteerOutcome.RESUME_WITH_WAKE: "resume",
    SteerOutcome.RELEASE_TO_QUEUED: "release_suspended",
}


def resolve_steer_outcome(*, is_headless_reservation: bool) -> SteerOutcome:
    """The observed steer-submission outcome for a suspended task, keyed
    only by whether its current reservation is headless (no interactive
    inbox) -- exactly the ``cold_headless`` branch
    ``TaskQueue.submit_steer`` already evaluates."""
    if is_headless_reservation:
        return SteerOutcome.RELEASE_TO_QUEUED
    return SteerOutcome.RESUME_WITH_WAKE


def suspend_blocked_by_pending_steer(*, has_untaken_steer: bool) -> bool:
    """The refusal-while-untaken-steer invariant: a task with an
    unconsumed steer answer already in its inbox must never be suspended
    out from under that answer -- exactly ``TaskQueue.suspend``'s
    ``reject_pending_steer=True`` guard, declared here as a checkable
    predicate rather than left implicit in the guard's call site."""
    return has_untaken_steer
