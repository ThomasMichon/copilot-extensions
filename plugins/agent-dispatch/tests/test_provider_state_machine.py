"""Structural tests for the declared provider/PR-target state-machine tables.

Phase 9 (``review-automation-reliability`` effort) fixtures: these assert
properties of the *declared* tables in
``agent_dispatch.provider_state_machine``, not a live provider adapter. No
network, no provider SDK -- purely a check that the tables themselves are
internally consistent, and that the capability table resolves per Phase 9's
declared defaults, before any scenario fixture or live adapter is built on
top of them.
"""

from __future__ import annotations

import pytest

from agent_dispatch.provider_state_machine import (
    ALL_APPROVAL_STATES,
    ALL_MERGEABILITY_STATES,
    APPROVAL_TRANSITIONS,
    INITIAL_APPROVAL_STATE,
    INITIAL_MERGEABILITY_STATE,
    MERGEABILITY_TRANSITIONS,
    PROVIDER_CAPABILITIES,
    REPOSITORY_OVERRIDES,
    ApprovalStatus,
    ConflictPolicy,
    HoldReason,
    Mergeability,
    NotificationFidelity,
    ProviderEventType,
    RecoveryMode,
    capability_for,
    merge_blocked_by_hold,
    reachable_states,
    states_without_exit,
)

# --- Approval dimension -----------------------------------------------------


def test_every_approval_state_reachable():
    reachable = reachable_states(APPROVAL_TRANSITIONS, INITIAL_APPROVAL_STATE)
    unreachable = ALL_APPROVAL_STATES - reachable
    assert not unreachable, f"unreachable approval states: {unreachable}"


def test_no_approval_state_lacks_an_exit():
    dead_ends = states_without_exit(ALL_APPROVAL_STATES, APPROVAL_TRANSITIONS)
    assert not dead_ends, f"approval states with no declared exit: {dead_ends}"


def test_every_approval_transition_names_only_declared_states():
    for transition in APPROVAL_TRANSITIONS:
        assert transition.from_states <= ALL_APPROVAL_STATES, transition.name
        assert transition.to_state in ALL_APPROVAL_STATES, transition.name


def test_every_approval_transition_has_exactly_one_recovery_mode():
    for transition in APPROVAL_TRANSITIONS:
        assert isinstance(transition.recovery_mode, RecoveryMode), transition.name


def test_approval_transition_names_are_unique():
    names = [t.name for t in APPROVAL_TRANSITIONS]
    assert len(names) == len(set(names)), names


def test_stale_is_only_reached_from_approved():
    """A revision push must invalidate an existing approval -- STALE is not
    reachable any other way, or the "approval no longer covers head" model
    would be meaningless."""
    (transition,) = [t for t in APPROVAL_TRANSITIONS if t.to_state is ApprovalStatus.STALE]
    assert transition.from_states == frozenset({ApprovalStatus.APPROVED})


# --- Mergeability dimension --------------------------------------------------


def test_every_mergeability_state_reachable():
    reachable = reachable_states(MERGEABILITY_TRANSITIONS, INITIAL_MERGEABILITY_STATE)
    unreachable = ALL_MERGEABILITY_STATES - reachable
    assert not unreachable, f"unreachable mergeability states: {unreachable}"


def test_no_mergeability_state_lacks_an_exit():
    dead_ends = states_without_exit(ALL_MERGEABILITY_STATES, MERGEABILITY_TRANSITIONS)
    assert not dead_ends, f"mergeability states with no declared exit: {dead_ends}"


def test_every_mergeability_transition_names_only_declared_states():
    for transition in MERGEABILITY_TRANSITIONS:
        assert transition.from_states <= ALL_MERGEABILITY_STATES, transition.name
        assert transition.to_state in ALL_MERGEABILITY_STATES, transition.name


def test_every_mergeability_transition_has_exactly_one_recovery_mode():
    for transition in MERGEABILITY_TRANSITIONS:
        assert isinstance(transition.recovery_mode, RecoveryMode), transition.name


def test_mergeability_transition_names_are_unique():
    names = [t.name for t in MERGEABILITY_TRANSITIONS]
    assert len(names) == len(set(names)), names


def test_conflicted_requires_reevaluation_before_clean():
    """Resolving a conflict must re-run checks (CHECKS_PENDING), never jump
    straight back to CLEAN -- a resolved conflict is unverified, not proven
    clean, until checks confirm it."""
    (transition,) = [t for t in MERGEABILITY_TRANSITIONS if t.name == "conflict_resolved"]
    assert transition.to_state is Mergeability.CHECKS_PENDING


def test_recovery_modes_are_not_all_the_same_across_both_dimensions():
    modes = {t.recovery_mode for t in APPROVAL_TRANSITIONS} | {
        t.recovery_mode for t in MERGEABILITY_TRANSITIONS
    }
    assert len(modes) > 1


# --- Hold (flag set, not a state dimension) ---------------------------------


def test_empty_hold_never_blocks_merge():
    assert merge_blocked_by_hold(frozenset()) is False


@pytest.mark.parametrize("reason", list(HoldReason))
def test_any_single_hold_reason_blocks_merge(reason):
    assert merge_blocked_by_hold(frozenset({reason})) is True


def test_multiple_hold_reasons_still_block_merge():
    assert merge_blocked_by_hold(frozenset(HoldReason)) is True


# --- Provider capability table -----------------------------------------------


@pytest.mark.parametrize("provider", list(PROVIDER_CAPABILITIES))
def test_every_provider_declares_fidelity_for_every_event_type(provider):
    capability = PROVIDER_CAPABILITIES[provider]
    declared = set(capability.notification_fidelity)
    assert declared == set(ProviderEventType), (
        f"{provider} is missing fidelity for {set(ProviderEventType) - declared}"
    )


@pytest.mark.parametrize("provider", list(PROVIDER_CAPABILITIES))
def test_every_provider_fidelity_value_is_declared_enum(provider):
    capability = PROVIDER_CAPABILITIES[provider]
    for event_type, fidelity in capability.notification_fidelity.items():
        assert isinstance(event_type, ProviderEventType)
        assert isinstance(fidelity, NotificationFidelity)


@pytest.mark.parametrize("provider", list(PROVIDER_CAPABILITIES))
def test_every_provider_defaults_to_hand_back_conflict_policy(provider):
    """Phase 9's resolution: hand-back is the default; branch-mutating must
    be an explicit repository opt-in, never a provider default."""
    assert PROVIDER_CAPABILITIES[provider].conflict_policy is ConflictPolicy.HAND_BACK


def test_capability_for_unknown_provider_raises():
    with pytest.raises(KeyError):
        capability_for("no_such_provider")


def test_capability_for_known_provider_without_override_returns_default():
    resolved = capability_for("github")
    assert resolved == PROVIDER_CAPABILITIES["github"]


def test_capability_for_applies_repository_override():
    REPOSITORY_OVERRIDES[("github", "test/opted-in-repo")] = {
        "conflict_policy": ConflictPolicy.BRANCH_MUTATING
    }
    try:
        resolved = capability_for("github", "test/opted-in-repo")
        assert resolved.conflict_policy is ConflictPolicy.BRANCH_MUTATING
        # Everything not overridden still matches the provider default.
        assert (
            resolved.automated_identity_eligible_approver
            == PROVIDER_CAPABILITIES["github"].automated_identity_eligible_approver
        )
    finally:
        del REPOSITORY_OVERRIDES[("github", "test/opted-in-repo")]


def test_capability_for_unrelated_repo_is_unaffected_by_other_overrides():
    REPOSITORY_OVERRIDES[("github", "test/opted-in-repo")] = {
        "conflict_policy": ConflictPolicy.BRANCH_MUTATING
    }
    try:
        resolved = capability_for("github", "test/unrelated-repo")
        assert resolved.conflict_policy is ConflictPolicy.HAND_BACK
    finally:
        del REPOSITORY_OVERRIDES[("github", "test/opted-in-repo")]
