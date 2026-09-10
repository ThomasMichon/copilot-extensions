"""Deterministic simulation driver for the Phase 9 coupled machines.

The three declared machines
(:mod:`agent_dispatch.task_state_machine`,
:mod:`agent_dispatch.provider_state_machine`,
:mod:`agent_dispatch.bridge_state_machine`) and their coupling rules
(:mod:`agent_dispatch.machine_coupling`) are pure data and pure functions --
none of them carry a notion of "the current world." This module is the
Phase 9 sub-doc's "Simulation and test track" driver: a small, generic
:class:`World` that pairs a task :class:`~agent_dispatch.machine_coupling.VersionedRecord`,
a bridge one, and an optional provider-approval one, plus ``step_*``
helpers that resolve a named transition from the owning table and apply
it through :func:`agent_dispatch.machine_coupling.apply_transition`.

This module declares no new machine behavior. It only drives the already-
declared tables so a scenario fixture can assert, mechanically:

- the correct terminal state is reached regardless of the order two
  independent events are delivered in,
- replaying the same event is idempotent (a second application never
  double-applies an effect), and
- the recovery mode recorded for each step matches the taxonomy already
  attached to that transition.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from .bridge_state_machine import BRIDGE_TRANSITIONS
from .machine_coupling import CASOutcome, VersionedRecord, apply_transition
from .provider_state_machine import APPROVAL_TRANSITIONS
from .task_state_machine import TRANSITIONS as TASK_TRANSITIONS
from .task_state_machine import RecoveryMode

TASK_TRANSITIONS_BY_NAME = {t.name: t for t in TASK_TRANSITIONS}
BRIDGE_TRANSITIONS_BY_NAME = {t.name: t for t in BRIDGE_TRANSITIONS}
APPROVAL_TRANSITIONS_BY_NAME = {t.name: t for t in APPROVAL_TRANSITIONS}


@dataclass(frozen=True)
class World:
    """Coupled versioned records: the task's own state (as ``queue.py``
    would persist it), the bridge/session's own state (as a session-host
    store would persist it), and -- when a scenario needs it -- the
    provider's approval-dimension state (as a provider adapter would
    persist it). Each evolves independently and is reconciled only through
    :mod:`agent_dispatch.machine_coupling`'s declared rules or
    :mod:`agent_dispatch.provider_state_machine`'s revision-classification
    functions -- never by one directly writing another."""

    task: VersionedRecord
    bridge: VersionedRecord
    provider: VersionedRecord | None = None


@dataclass(frozen=True)
class StepResult:
    """The outcome of one attempted step against a :class:`World`."""

    world: World
    outcome: CASOutcome
    #: ``None`` when the step was not a legal move from the current state
    #: and did not already reflect the target (a genuinely out-of-order
    #: delivery that must simply be retried once its precondition holds --
    #: not a CAS race and not a replay).
    recovery_mode: RecoveryMode | None


def _attempt(record: VersionedRecord, from_states, to_state: str, recovery_mode: RecoveryMode):
    """Attempt one named transition against ``record``, tolerating
    out-of-order delivery: a step whose precondition the record does not
    currently satisfy is left untouched (the caller retries later) rather
    than raising, since providers and bridges may deliver events in any
    order."""
    if record.state not in from_states:
        if record.state == to_state:
            return record, CASOutcome.ALREADY_ADVANCED, recovery_mode
        return record, None, None
    outcome, updated = apply_transition(record, record.generation, to_state)
    return updated, outcome, recovery_mode


def step_task(world: World, transition_name: str) -> StepResult:
    """Attempt the named task transition against ``world.task``."""
    transition = TASK_TRANSITIONS_BY_NAME[transition_name]
    updated, outcome, mode = _attempt(
        world.task, transition.from_states, transition.to_state, transition.recovery_mode
    )
    return StepResult(replace(world, task=updated), outcome, mode)


def step_bridge(world: World, transition_name: str) -> StepResult:
    """Attempt the named bridge transition against ``world.bridge``."""
    transition = BRIDGE_TRANSITIONS_BY_NAME[transition_name]
    from_values = frozenset(s.value for s in transition.from_states)
    updated, outcome, mode = _attempt(
        world.bridge, from_values, transition.to_state.value, transition.recovery_mode
    )
    return StepResult(replace(world, bridge=updated), outcome, mode)


def step_provider_approval(world: World, transition_name: str) -> StepResult:
    """Attempt the named approval-dimension transition against
    ``world.provider``. Raises if the world has no provider record --
    callers must seed one, since not every scenario involves the provider
    machine."""
    if world.provider is None:
        raise ValueError("world has no provider record to step")
    transition = APPROVAL_TRANSITIONS_BY_NAME[transition_name]
    from_values = frozenset(s.value for s in transition.from_states)
    updated, outcome, mode = _attempt(
        world.provider, from_values, transition.to_state.value, transition.recovery_mode
    )
    return StepResult(replace(world, provider=updated), outcome, mode)


def apply_sequence(world: World, steps: tuple[tuple[str, str], ...]) -> World:
    """Apply an ordered sequence of ``(machine, transition_name)`` steps,
    where ``machine`` is ``"task"``, ``"bridge"``, or ``"provider"``. Steps
    whose precondition is not yet satisfied are silently skipped
    (out-of-order delivery) -- callers that need to assert every step
    actually applied should inspect individual :func:`step_task` /
    :func:`step_bridge` / :func:`step_provider_approval` results instead."""
    for machine, name in steps:
        if machine == "task":
            world = step_task(world, name).world
        elif machine == "bridge":
            world = step_bridge(world, name).world
        elif machine == "provider":
            world = step_provider_approval(world, name).world
        else:
            raise ValueError(f"unknown machine {machine!r}")
    return world
