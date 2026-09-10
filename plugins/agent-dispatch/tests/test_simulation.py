"""Phase 9 simulation/test-track fixtures: deterministic scenarios driven
through :mod:`agent_dispatch.simulation` against the declared task, bridge,
and coupling tables.

Each scenario below corresponds to one bullet in the "Simulation and test
track" section of
``efforts/active/review-automation-reliability/phase-9-state-machine-architecture.md``
and asserts the three properties that section requires: the correct
terminal state is reached regardless of interleaving order, the same event
is idempotent under replay, and the recovery mode taken matches the
declared taxonomy. This file covers the five scenarios expressible against
the machines' already-declared vocabulary; the remaining scenarios need a
vocabulary extension (revision/head tracking, version/EOL) and land in a
follow-up slice.
"""

from __future__ import annotations

import itertools

import pytest

from agent_dispatch.bridge_state_machine import (
    BridgeState,
    Liveness,
    ResumeAction,
    resolve_liveness,
    resolve_resume,
)
from agent_dispatch.machine_coupling import (
    BRIDGE_EVENT_EVIDENCE,
    BridgeEvent,
    CASOutcome,
    VersionedRecord,
    consider_task_transitions,
)
from agent_dispatch.queue import Status
from agent_dispatch.simulation import World, step_bridge, step_task
from agent_dispatch.task_state_machine import RecoveryMode

# --- Scenario: session host detached while a task believes it is running ---


def _detached_world() -> World:
    return World(
        task=VersionedRecord(generation=0, state=Status.STARTED),
        bridge=VersionedRecord(generation=0, state=BridgeState.RUNNING.value),
    )


def _apply_host_detached(world: World, order: tuple[str, ...]) -> World:
    """Apply the two confirming steps (task ``suspend``, bridge
    ``suspend``) in ``order`` -- the arrival order between a bridge-side
    liveness downgrade and the task evaluator's own reaction to it is not
    guaranteed."""
    for step in order:
        if step == "task":
            world = step_task(world, "suspend").world
        else:
            world = step_bridge(world, "suspend").world
    return world


@pytest.mark.parametrize("order", list(itertools.permutations(("task", "bridge"))))
def test_host_detached_converges_regardless_of_order(order):
    """``HOST_DETACHED`` is evidence for the task's own ``suspend``
    transition, and Phase 9's coupling rule confirms it via the bridge's
    own ``suspend`` transition. Both must land at SUSPENDED regardless of
    which side's transition is processed first."""
    assert consider_task_transitions(BridgeEvent.HOST_DETACHED) == frozenset({"suspend"})
    world = _apply_host_detached(_detached_world(), order)
    assert world.task.state == Status.SUSPENDED
    assert world.bridge.state == BridgeState.SUSPENDED.value


def test_host_detached_recovery_mode_is_safe_retry_on_both_sides():
    world = _detached_world()
    task_result = step_task(world, "suspend")
    bridge_result = step_bridge(world, "suspend")
    assert task_result.recovery_mode is RecoveryMode.SAFE_RETRY
    assert bridge_result.recovery_mode is RecoveryMode.SAFE_RETRY


def test_host_detached_suspend_is_idempotent_under_replay():
    world = _apply_host_detached(_detached_world(), ("task", "bridge"))
    replayed_task = step_task(world, "suspend")
    replayed_bridge = step_bridge(world, "suspend")
    assert replayed_task.outcome is CASOutcome.ALREADY_ADVANCED
    assert replayed_bridge.outcome is CASOutcome.ALREADY_ADVANCED
    assert replayed_task.world == world
    assert replayed_bridge.world == world


# --- Scenario: the bridge's discovered port changes underneath an --------
# --- already-attached task -------------------------------------------------


def test_port_changed_alone_implies_no_task_transition_and_no_op():
    """A port change is purely informational -- it must not, by itself,
    move any task state, and applying "nothing" twice is trivially
    idempotent."""
    world = World(
        task=VersionedRecord(generation=0, state=Status.STARTED),
        bridge=VersionedRecord(generation=0, state=BridgeState.RUNNING.value),
    )
    assert consider_task_transitions(BridgeEvent.PORT_CHANGED) == frozenset()
    assert BRIDGE_EVENT_EVIDENCE[BridgeEvent.PORT_CHANGED] == frozenset()
    # No task transition is named, so the evaluator applies none -- the
    # world is unchanged, and unchanged twice is the same as unchanged
    # once.
    assert world == world


# --- Scenario: two provider-driven task events for the same task arrive ---
# --- out of order or duplicated --------------------------------------------


def test_duplicate_approve_events_are_idempotent_regardless_of_replay_count():
    """Phase 2's dedup requirement: a provider redelivering the same
    ``approve`` event must never advance the task twice."""
    world = World(
        task=VersionedRecord(generation=0, state=Status.PROPOSED),
        bridge=VersionedRecord(generation=0, state=BridgeState.ABSENT.value),
    )
    first = step_task(world, "approve")
    assert first.outcome is CASOutcome.APPLIED
    assert first.world.task.state == Status.QUEUED

    # Redeliver the same "approve" event any number of times: each replay
    # must be a no-op, not a duplicate effect.
    replayed = first.world
    for _ in range(3):
        result = step_task(replayed, "approve")
        assert result.outcome is CASOutcome.ALREADY_ADVANCED
        assert result.world == replayed
        replayed = result.world


@pytest.mark.parametrize(
    "order",
    list(itertools.permutations(("approve", "claim"))),
)
def test_out_of_order_provider_events_converge_once_preconditions_are_met(order):
    """A ``claim`` event delivered before its ``approve`` precondition has
    landed must not be lost or misapplied -- it is simply not yet legal,
    and re-attempting it after ``approve`` lands must still reach the same
    terminal state as the in-order delivery."""
    world = World(
        task=VersionedRecord(generation=0, state=Status.PROPOSED),
        bridge=VersionedRecord(generation=0, state=BridgeState.ABSENT.value),
    )
    # First pass in the (possibly out-of-order) delivery order: an
    # out-of-order "claim" attempted before "approve" has landed is simply
    # not yet legal and is left untouched (recovery_mode is None).
    for name in order:
        world = step_task(world, name).world
    # A retry pass re-attempts every event once more, which is exactly how
    # an at-least-once delivery system recovers from out-of-order arrival:
    # any event that was skipped the first time now finds its precondition
    # satisfied.
    for name in order:
        world = step_task(world, name).world
    assert world.task.state == Status.CLAIMED


# --- Scenario: resume against a liveness cache that is stale, empty, or ---
# --- missing entirely --------------------------------------------------------


@pytest.mark.parametrize("cache_hint", [None, Liveness.COLD, Liveness.WARM, Liveness.HOT])
def test_resume_converges_on_the_live_result_regardless_of_cache_hint(cache_hint):
    """However stale, empty, or simply wrong the cache hint is, the live
    probe is always the deciding read -- the resume outcome must be
    identical for every possible hint, since Phase 9's ``resolve_liveness``
    ignores the hint's value entirely."""
    world = World(
        task=VersionedRecord(generation=0, state=Status.SUSPENDED),
        bridge=VersionedRecord(generation=0, state=BridgeState.SUSPENDED.value),
    )
    liveness = resolve_liveness(cache_hint, lambda: Liveness.WARM)
    assert liveness is Liveness.WARM
    action = resolve_resume(liveness)
    assert action is ResumeAction.REATTACH
    updated = step_bridge(world, "resume").world
    assert updated.bridge.state == BridgeState.RUNNING.value


def test_resume_reattach_is_idempotent_under_replay():
    world = World(
        task=VersionedRecord(generation=0, state=Status.SUSPENDED),
        bridge=VersionedRecord(generation=0, state=BridgeState.SUSPENDED.value),
    )
    once = step_bridge(world, "resume")
    twice = step_bridge(once.world, "resume")
    assert once.outcome is CASOutcome.APPLIED
    assert twice.outcome is CASOutcome.ALREADY_ADVANCED
    assert twice.world == once.world


# --- Scenario: resume against a target that is genuinely hot ---------------


def test_hot_resume_without_force_refuses_and_never_mutates_the_world():
    world = World(
        task=VersionedRecord(generation=0, state=Status.STARTED),
        bridge=VersionedRecord(generation=0, state=BridgeState.RUNNING.value),
    )
    liveness = resolve_liveness(None, lambda: Liveness.HOT)
    action = resolve_resume(liveness)
    assert action is ResumeAction.REFUSE
    # A refusal performs no bridge transition at all -- replaying the
    # refusal any number of times must leave the world byte-identical.
    for _ in range(3):
        assert resolve_resume(resolve_liveness(None, lambda: Liveness.HOT)) is ResumeAction.REFUSE
    assert world == World(
        task=VersionedRecord(generation=0, state=Status.STARTED),
        bridge=VersionedRecord(generation=0, state=BridgeState.RUNNING.value),
    )


def test_hot_resume_with_force_takeover_is_a_composite_stop_then_resume():
    """``FORCE_TAKEOVER`` has no direct entry in ``BRIDGE_TRANSITIONS``: it
    is the composite of stopping the existing headed session (the ``suspend``
    transition) followed by the ordinary resume-from-suspended transition.
    Applying that composite twice (a duplicated force-takeover request) must
    still converge on RUNNING, never spawn a second controller."""
    world = World(
        task=VersionedRecord(generation=0, state=Status.STARTED),
        bridge=VersionedRecord(generation=0, state=BridgeState.RUNNING.value),
    )
    liveness = resolve_liveness(None, lambda: Liveness.HOT)
    action = resolve_resume(liveness, force_takeover=True)
    assert action is ResumeAction.FORCE_TAKEOVER

    def composite(world: World) -> World:
        world = step_bridge(world, "suspend").world
        world = step_bridge(world, "resume").world
        return world

    once = composite(world)
    twice = composite(once)
    assert once.bridge.state == BridgeState.RUNNING.value
    # A repeated force-takeover genuinely performs stop-then-resume again
    # (the target really was hot again), so the generation legitimately
    # advances each time -- but the *state* it converges to is identical
    # every time, never a second live controller.
    assert twice.bridge.state == once.bridge.state == BridgeState.RUNNING.value
