"""Explicit control-flow coupling between the three Phase 9 machines.

The task, provider, and bridge/session machines
(:mod:`agent_dispatch.task_state_machine`,
:mod:`agent_dispatch.provider_state_machine`,
:mod:`agent_dispatch.bridge_state_machine`) are each declared
independently. This module is the phase-9 sub-doc's **coupling rules**
made real -- the piece that keeps a task-state transition from assuming a
bridge effect happened just because it was requested, and keeps a
bridge-side event from directly mutating task state.

Two invariants, declared as data so they are checkable rather than
convention:

1. **Task-requests-bridge-action, waits for confirmation.**
   :data:`TASK_TRANSITION_BRIDGE_CONFIRMATION` names, for each task
   transition that requires a bridge action, exactly which bridge
   transition(s) count as that action's confirmation. A task transition
   is never considered to have taken effect on the bridge side until one
   of its named bridge transitions actually occurs.
2. **Bridge-event-as-evidence-only.** :data:`BRIDGE_EVENT_EVIDENCE` names,
   for each bridge-side event, only the task transitions that event is
   *evidence for* -- never a transition it applies. The only accessor,
   :func:`consider_task_transitions`, returns names; it has no way to
   mutate a task record, because it is never given one. Applying (or not)
   is entirely the task machine's own evaluator's decision.

This module also declares the **CAS/generation mechanism** the sub-doc's
"Mechanism, concretely" section describes: every transition across all
three machines is a compare-and-set against a monotonic generation
number, and a stale CAS is resolved by re-reading rather than assumed to
be an error. :class:`VersionedRecord` and :func:`apply_transition` make
this a single, generic, machine-agnostic primitive rather than three
separately hand-rolled CAS loops.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .bridge_state_machine import BRIDGE_TRANSITIONS
from .task_state_machine import TRANSITIONS as TASK_TRANSITIONS

TASK_TRANSITION_NAMES: frozenset[str] = frozenset(t.name for t in TASK_TRANSITIONS)
BRIDGE_TRANSITION_NAMES: frozenset[str] = frozenset(t.name for t in BRIDGE_TRANSITIONS)


class BridgeEvent(Enum):
    """Bridge-side events observed by the task machine's evaluator.

    Every member is *evidence only* -- see :data:`BRIDGE_EVENT_EVIDENCE`.
    """

    SESSION_ENDED = "session_ended"
    HOST_DETACHED = "host_detached"
    PORT_CHANGED = "port_changed"
    LIVENESS_DOWNGRADED = "liveness_downgraded"


#: For each bridge event, the task-transition names it is evidence *for*.
#: An empty set means the event is purely informational (e.g. a port
#: change alone implies no task-state re-evaluation by itself). This is
#: intentionally the *only* thing this module lets a bridge event name --
#: there is no companion "apply" table, and :func:`consider_task_transitions`
#: is given no task record to mutate even if it wanted to.
BRIDGE_EVENT_EVIDENCE: dict[BridgeEvent, frozenset[str]] = {
    BridgeEvent.SESSION_ENDED: frozenset({"complete", "abandon"}),
    BridgeEvent.HOST_DETACHED: frozenset({"suspend"}),
    BridgeEvent.PORT_CHANGED: frozenset(),
    BridgeEvent.LIVENESS_DOWNGRADED: frozenset({"suspend"}),
}


def consider_task_transitions(event: BridgeEvent) -> frozenset[str]:
    """The task-transition names ``event`` is evidence for.

    Returns names only. This function applies nothing to any task
    record -- it is not given one -- so a bridge event can never directly
    mutate task state through this call; the task machine's own evaluator
    is the only thing that decides whether and how to act on the
    evidence.
    """
    return BRIDGE_EVENT_EVIDENCE[event]


#: task-requests-bridge-action: for each task transition that requires a
#: bridge action to actually take effect, the bridge transition name(s)
#: that count as confirmation of that action. A task transition is never
#: treated as having produced its bridge effect until one of these bridge
#: transitions is observed.
TASK_TRANSITION_BRIDGE_CONFIRMATION: dict[str, frozenset[str]] = {
    "start": frozenset({"ready"}),
    "suspend": frozenset({"suspend"}),
    #: Whichever the resolved liveness outcome turns out to be (Phase 9's
    #: one-universal-resume model) -- both are legal confirmations.
    "resume": frozenset({"resume", "spawn_fresh_bound"}),
    "complete": frozenset({"end"}),
    "abandon": frozenset({"end", "end_suspended"}),
}


class CASOutcome(Enum):
    """The result of one compare-and-set transition attempt."""

    #: The expected generation matched; the transition was applied.
    APPLIED = "applied"
    #: The expected generation was stale and the record does not already
    #: reflect the target state: someone else moved it first. The caller
    #: must re-read current state and re-evaluate, not retry blindly.
    LOST_CAS = "lost_cas"
    #: The expected generation was stale, but the record already reflects
    #: the target state -- a replayed request that already succeeded.
    #: This is Phase 9's idempotent-replay requirement: a no-op, not an
    #: error and not a duplicate effect.
    ALREADY_ADVANCED = "already_advanced"


@dataclass(frozen=True)
class VersionedRecord:
    """A minimal generation+state pair. Generic across all three machines
    this CAS mechanism serves -- it does not know or care which machine's
    state string it carries."""

    generation: int
    state: str


def apply_transition(
    record: VersionedRecord, expected_generation: int, to_state: str
) -> tuple[CASOutcome, VersionedRecord]:
    """Attempt one compare-and-set transition against ``record``.

    Never mutates ``record`` (it is frozen); always returns the record to
    use going forward alongside the outcome.
    """
    if record.generation == expected_generation:
        return CASOutcome.APPLIED, VersionedRecord(
            generation=record.generation + 1, state=to_state
        )
    if record.state == to_state:
        return CASOutcome.ALREADY_ADVANCED, record
    return CASOutcome.LOST_CAS, record
