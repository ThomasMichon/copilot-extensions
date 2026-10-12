"""Phase 9 simulation/test-track fixtures, second slice: the two scenarios
that needed the provider machine's revision/head-tracking extension
(:data:`agent_dispatch.provider_state_machine.Revision` and friends).

Each scenario corresponds to one of the two remaining bullets in the
"Simulation and test track" section of
``efforts/active/review-automation-reliability/phase-9-state-machine-architecture.md``
that were deferred out of the first simulation slice for lacking a
revision concept: base-only vs. substantive provider revision movement,
and an out-of-order verdict arriving after a substantive revision has
already superseded it.
"""

from __future__ import annotations

import itertools

import pytest

from agent_dispatch.machine_coupling import CASOutcome, VersionedRecord
from agent_dispatch.provider_state_machine import (
    REVISION_CHANGE_APPROVAL_TRANSITION,
    ApprovalStatus,
    Revision,
    RevisionChangeKind,
    classify_revision_change,
    verdict_applies_to_current_revision,
)
from agent_dispatch.simulation import World, step_provider_approval

# --- Scenario: base-only vs. substantive provider revision movement --------


def _approved_world() -> World:
    return World(
        task=VersionedRecord(generation=0, state="started"),
        bridge=VersionedRecord(generation=0, state="running"),
        provider=VersionedRecord(generation=0, state=ApprovalStatus.APPROVED.value),
    )


def test_base_only_revision_movement_leaves_an_existing_approval_intact():
    """Unrelated commits landing on the base branch, with the submitter's
    diff unchanged, must not invalidate an existing approval -- the
    approval still covers the current diff."""
    previous = Revision(diff_hash="d1", base_sha="b1")
    current = Revision(diff_hash="d1", base_sha="b2")
    kind = classify_revision_change(previous, current)
    assert kind is RevisionChangeKind.BASE_ONLY
    assert REVISION_CHANGE_APPROVAL_TRANSITION[kind] is None

    world = _approved_world()
    # No approval-dimension transition is implied, so the evaluator applies
    # none -- replaying "nothing" any number of times is trivially
    # idempotent and order-independent.
    assert world.provider.state == ApprovalStatus.APPROVED.value


def test_substantive_revision_movement_invalidates_the_existing_approval():
    """A real change to the submitter's own diff -- whether or not the base
    also moved -- must invalidate any existing approval."""
    for previous, current in [
        (Revision(diff_hash="d1", base_sha="b1"), Revision(diff_hash="d2", base_sha="b1")),
        (Revision(diff_hash="d1", base_sha="b1"), Revision(diff_hash="d2", base_sha="b2")),
    ]:
        kind = classify_revision_change(previous, current)
        assert kind is RevisionChangeKind.SUBSTANTIVE
        transition_name = REVISION_CHANGE_APPROVAL_TRANSITION[kind]
        assert transition_name == "revision_invalidates_approval"

        world = _approved_world()
        result = step_provider_approval(world, transition_name)
        assert result.world.provider.state == ApprovalStatus.STALE.value


def test_no_revision_movement_implies_no_transition():
    previous = Revision(diff_hash="d1", base_sha="b1")
    current = Revision(diff_hash="d1", base_sha="b1")
    kind = classify_revision_change(previous, current)
    assert kind is RevisionChangeKind.NONE
    assert REVISION_CHANGE_APPROVAL_TRANSITION[kind] is None


@pytest.mark.parametrize("order", list(itertools.permutations(("base_move", "unrelated_noise"))))
def test_base_only_classification_is_independent_of_observation_order(order):
    """Classifying a base-only move must not depend on whether an
    unrelated, no-op re-observation of the same current revision happens
    before or after the real comparison."""
    previous = Revision(diff_hash="d1", base_sha="b1")
    current = Revision(diff_hash="d1", base_sha="b2")
    kinds = []
    for step in order:
        if step == "base_move":
            kinds.append(classify_revision_change(previous, current))
        else:
            kinds.append(classify_revision_change(current, current))
    # Whichever order the two independent observations are made in, the
    # real base-only move is always classified the same way.
    assert RevisionChangeKind.BASE_ONLY in kinds
    assert RevisionChangeKind.NONE in kinds


# --- Scenario: an out-of-order verdict superseded by a newer revision -------


def test_verdict_for_a_superseded_revision_is_rejected_by_the_guard():
    """A verdict/approval computed against an old revision, delivered after
    a substantive revision change has already landed, must fail the
    current-revision guard -- the evaluator must not apply it as if it
    covered the current head."""
    stale_verdict_revision = Revision(diff_hash="d1", base_sha="b1")
    current_revision = Revision(diff_hash="d2", base_sha="b1")
    assert not verdict_applies_to_current_revision(stale_verdict_revision, current_revision)


def test_verdict_for_the_current_revision_passes_the_guard():
    revision = Revision(diff_hash="d1", base_sha="b1")
    assert verdict_applies_to_current_revision(revision, revision)


def test_out_of_order_verdict_delivery_converges_to_stale_regardless_of_order():
    """Whether the substantive-revision event or the (now-stale) verdict
    arrives first, the terminal approval state must be STALE, never
    APPROVED-for-a-head-it-no-longer-covers."""

    def apply_revision_change(world: World) -> World:
        result = step_provider_approval(world, "revision_invalidates_approval")
        return result.world

    def apply_late_verdict_guarded(
        world: World, verdict_revision: Revision, current: Revision
    ) -> World:
        """The evaluator's own guard: a verdict is applied only if it still
        covers the current revision. Once the revision has moved on, this
        is a no-op -- the verdict is dropped, not misapplied."""
        if not verdict_applies_to_current_revision(verdict_revision, current):
            return world
        # covers current revision: nothing to do here since it was already
        # APPROVED for this exact revision.
        return world

    verdict_revision = Revision(diff_hash="d1", base_sha="b1")
    current_revision = Revision(diff_hash="d2", base_sha="b1")

    # Order A: revision change lands first, then the stale verdict arrives.
    world_a = _approved_world()
    world_a = apply_revision_change(world_a)
    world_a = apply_late_verdict_guarded(world_a, verdict_revision, current_revision)

    # Order B: the stale verdict is (re-)delivered before the revision
    # change is processed, then the revision change lands.
    world_b = _approved_world()
    world_b = apply_late_verdict_guarded(world_b, verdict_revision, current_revision)
    world_b = apply_revision_change(world_b)

    assert world_a.provider.state == ApprovalStatus.STALE.value
    assert world_b.provider.state == ApprovalStatus.STALE.value


def test_stale_approval_transition_is_idempotent_under_replay():
    world = _approved_world()
    once = step_provider_approval(world, "revision_invalidates_approval")
    twice = step_provider_approval(once.world, "revision_invalidates_approval")
    assert once.outcome is CASOutcome.APPLIED
    assert twice.outcome is CASOutcome.ALREADY_ADVANCED
    assert twice.world.provider.state == once.world.provider.state == ApprovalStatus.STALE.value
