"""Structural tests for the declared bridge/session state-machine tables.

Phase 9 (``review-automation-reliability`` effort) fixtures: these assert
properties of the *declared* tables in
``agent_dispatch.bridge_state_machine``, not a live bridge daemon. No
process, no session host -- purely a check that the lifecycle table and
the liveness-to-resume coupling are internally consistent before any
scenario fixture or live adapter is built on top of them.
"""

from __future__ import annotations

import pytest

from agent_dispatch.bridge_state_machine import (
    ALL_BRIDGE_STATES,
    BRIDGE_TRANSITIONS,
    INITIAL_BRIDGE_STATE,
    RESUME_ACTION_TRANSITIONS,
    TERMINAL_BRIDGE_STATES,
    BridgeState,
    Liveness,
    RecoveryMode,
    RegistryClass,
    ResumeAction,
    create_fresh_allowed,
    reachable_states,
    resolve_liveness,
    resolve_resume,
    states_without_exit,
    terminal_states_with_exit,
)

# --- Lifecycle table ---------------------------------------------------------


def test_every_state_reachable_from_absent():
    reachable = reachable_states(INITIAL_BRIDGE_STATE)
    unreachable = ALL_BRIDGE_STATES - reachable
    assert not unreachable, f"unreachable bridge states: {unreachable}"


def test_no_nonterminal_state_lacks_an_exit():
    dead_ends = states_without_exit()
    assert not dead_ends, f"non-terminal states with no declared exit: {dead_ends}"


def test_no_terminal_state_has_a_declared_exit():
    leaky = terminal_states_with_exit()
    assert not leaky, f"terminal states with a declared exit: {leaky}"


def test_ended_is_the_only_terminal_state():
    assert TERMINAL_BRIDGE_STATES == frozenset({BridgeState.ENDED})


def test_every_transition_names_only_declared_states():
    for transition in BRIDGE_TRANSITIONS:
        assert transition.from_states <= ALL_BRIDGE_STATES, transition.name
        assert transition.to_state in ALL_BRIDGE_STATES, transition.name


def test_every_transition_has_exactly_one_recovery_mode():
    for transition in BRIDGE_TRANSITIONS:
        assert isinstance(transition.recovery_mode, RecoveryMode), transition.name


def test_no_transition_originates_from_a_terminal_state():
    for transition in BRIDGE_TRANSITIONS:
        overlap = transition.from_states & TERMINAL_BRIDGE_STATES
        assert not overlap, f"{transition.name} sources from terminal {overlap}"


def test_transition_names_are_unique():
    names = [t.name for t in BRIDGE_TRANSITIONS]
    assert len(names) == len(set(names)), names


def test_recovery_modes_are_not_all_the_same():
    modes = {t.recovery_mode for t in BRIDGE_TRANSITIONS}
    assert len(modes) > 1


def test_hydration_failed_makes_absent_recoverable_not_a_dead_end():
    """A failed spawn must revert to ABSENT, not leave HYDRATING stuck --
    this is what keeps HYDRATING from being a dead end without a terminal
    designation."""
    (transition,) = [t for t in BRIDGE_TRANSITIONS if t.name == "hydration_failed"]
    assert transition.from_states == frozenset({BridgeState.HYDRATING})
    assert transition.to_state is BridgeState.ABSENT
    assert transition.recovery_mode is RecoveryMode.SELF_REPAIR


# --- Liveness: always live, never cache-gated -------------------------------


def test_resolve_liveness_always_calls_the_live_probe():
    calls = []

    def probe():
        calls.append(1)
        return Liveness.WARM

    result = resolve_liveness(Liveness.COLD, probe)
    assert result is Liveness.WARM
    assert calls == [1], "the live probe must be called even when a cache hint is present"


def test_resolve_liveness_ignores_a_stale_cache_hint():
    """A COLD cache hint must not suppress a HOT live result -- the cache is
    never authoritative, per Phase 9's corrected liveness model."""
    result = resolve_liveness(Liveness.COLD, lambda: Liveness.HOT)
    assert result is Liveness.HOT


def test_resolve_liveness_with_no_cache_hint_still_calls_probe():
    calls = []

    def probe():
        calls.append(1)
        return Liveness.COLD

    result = resolve_liveness(None, probe)
    assert result is Liveness.COLD
    assert calls == [1]


# --- Resume outcome, coupled to the lifecycle table -------------------------


def test_hot_without_force_refuses():
    assert resolve_resume(Liveness.HOT) is ResumeAction.REFUSE


def test_hot_with_force_takes_over():
    assert resolve_resume(Liveness.HOT, force_takeover=True) is ResumeAction.FORCE_TAKEOVER


def test_warm_reattaches():
    assert resolve_resume(Liveness.WARM) is ResumeAction.REATTACH


def test_cold_always_spawns_fresh_bound():
    assert resolve_resume(Liveness.COLD) is ResumeAction.SPAWN_FRESH_BOUND
    # Force-takeover is meaningless when nothing is hot; cold always succeeds
    # the same way regardless of the flag.
    assert resolve_resume(Liveness.COLD, force_takeover=True) is ResumeAction.SPAWN_FRESH_BOUND


@pytest.mark.parametrize("action", [ResumeAction.REATTACH, ResumeAction.SPAWN_FRESH_BOUND])
def test_every_non_refusing_non_composite_action_names_a_real_transition(action):
    """REATTACH and SPAWN_FRESH_BOUND must each map to a transition that
    actually exists in BRIDGE_TRANSITIONS -- this is what keeps the
    liveness-outcome table and the lifecycle table coupled instead of being
    two independent tables that happen to share a module."""
    transition_name = RESUME_ACTION_TRANSITIONS[action]
    names = {t.name for t in BRIDGE_TRANSITIONS}
    assert transition_name in names


def test_refuse_and_force_takeover_have_no_direct_transition_entry():
    """REFUSE performs no transition at all; FORCE_TAKEOVER is a composite
    (stop, then resume) with no single atomic entry of its own."""
    assert ResumeAction.REFUSE not in RESUME_ACTION_TRANSITIONS
    assert ResumeAction.FORCE_TAKEOVER not in RESUME_ACTION_TRANSITIONS


def test_reattach_transition_is_resume_from_suspended():
    (transition,) = [t for t in BRIDGE_TRANSITIONS if t.name == "resume"]
    assert transition.from_states == frozenset({BridgeState.SUSPENDED})
    assert transition.to_state is BridgeState.RUNNING


def test_spawn_fresh_bound_transition_is_from_absent():
    (transition,) = [t for t in BRIDGE_TRANSITIONS if t.name == "spawn_fresh_bound"]
    assert transition.from_states == frozenset({BridgeState.ABSENT})


# --- Registry class: single-head vs multi-head ------------------------------


def test_single_head_registry_disallows_create_fresh():
    assert create_fresh_allowed(RegistryClass.SINGLE_HEAD) is False


def test_multi_head_registry_allows_create_fresh():
    assert create_fresh_allowed(RegistryClass.MULTI_HEAD) is True
