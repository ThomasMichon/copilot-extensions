"""Structural tests for the declared spawn-reservation/allocation-fencing
tables.

Phase 9 (``review-automation-reliability`` effort) fixtures: these assert
properties of the *declared* tables in
``agent_dispatch.spawn_reservation_machine``, not a live queue/supervisor.
No process, no SQLite -- purely a check that the transition table, the
single-assignment invariant, the closed "let go" vocabulary, and the
reservation<->bridge consistency relation are internally consistent.
"""

from __future__ import annotations

import pytest

from agent_dispatch.bridge_state_machine import BridgeState
from agent_dispatch.queue import SpawnState
from agent_dispatch.spawn_reservation_machine import (
    ALL_SPAWN_STATES,
    INITIAL_SPAWN_STATE,
    LET_GO_REASON_RECOVERY_MODE,
    LET_GO_REASON_TRANSITION,
    SPAWN_TERMINAL_STATES,
    SPAWN_TRANSITIONS,
    ConsistencyTier,
    LetGoReason,
    RecoveryMode,
    ReservationRecord,
    classify_consistency,
    reachable_states,
    states_without_exit,
    terminal_states_with_exit,
    verdict_to_bridge_state,
    violating_assignment_groups,
)

SPAWN_TRANSITION_NAMES = frozenset(t.name for t in SPAWN_TRANSITIONS)


# --- Lifecycle table ---------------------------------------------------------


def test_every_state_reachable_from_reserving():
    reachable = reachable_states(INITIAL_SPAWN_STATE)
    unreachable = ALL_SPAWN_STATES - reachable
    assert not unreachable, f"unreachable from RESERVING: {unreachable}"


def test_no_nonterminal_state_lacks_an_exit():
    dead_ends = states_without_exit()
    assert not dead_ends, f"non-terminal states with no declared exit: {dead_ends}"


def test_no_terminal_state_has_a_declared_exit():
    leaky = terminal_states_with_exit()
    assert not leaky, f"terminal states with a declared exit: {leaky}"


def test_failed_is_releasable_but_not_machine_terminal():
    """FAILED has a real declared exit (rearm) even though it is part of
    SpawnState.RELEASABLE -- releasable and machine-terminal are different
    concepts here."""
    assert SpawnState.FAILED in SpawnState.RELEASABLE
    assert SpawnState.FAILED not in SPAWN_TERMINAL_STATES
    assert SpawnState.FAILED not in states_without_exit()


def test_every_transition_names_only_declared_states():
    for transition in SPAWN_TRANSITIONS:
        assert transition.from_states <= ALL_SPAWN_STATES, transition.name
        assert transition.to_state in ALL_SPAWN_STATES, transition.name


def test_every_transition_has_exactly_one_recovery_mode():
    for transition in SPAWN_TRANSITIONS:
        assert isinstance(transition.recovery_mode, RecoveryMode), transition.name


def test_no_transition_originates_from_a_terminal_state():
    for transition in SPAWN_TRANSITIONS:
        overlap = transition.from_states & SPAWN_TERMINAL_STATES
        assert not overlap, f"{transition.name} sources from terminal {overlap}"


def test_transition_names_are_unique():
    names = [t.name for t in SPAWN_TRANSITIONS]
    assert len(names) == len(set(names)), names


def test_recovery_modes_are_not_all_the_same():
    modes = {t.recovery_mode for t in SPAWN_TRANSITIONS}
    assert len(modes) > 1


# --- Closed "let go" vocabulary ----------------------------------------------


def test_every_let_go_reason_names_a_real_declared_transition():
    for reason in LetGoReason:
        assert LET_GO_REASON_TRANSITION[reason] in SPAWN_TRANSITION_NAMES, reason


def test_every_let_go_reason_carries_exactly_one_recovery_mode():
    for reason in LetGoReason:
        assert isinstance(LET_GO_REASON_RECOVERY_MODE[reason], RecoveryMode), reason


def test_let_go_reason_recovery_modes_match_the_subdoc_classification():
    """Locks in the sub-doc's explicit per-reason classification, not just
    "some RecoveryMode or other"."""
    assert LET_GO_REASON_RECOVERY_MODE[LetGoReason.FAILED] is RecoveryMode.SAFE_RETRY
    assert LET_GO_REASON_RECOVERY_MODE[LetGoReason.DEFERRED] is RecoveryMode.SELF_RECOVERING
    assert LET_GO_REASON_RECOVERY_MODE[LetGoReason.SETTLED] is RecoveryMode.SAFE_RETRY
    assert LET_GO_REASON_RECOVERY_MODE[LetGoReason.RETIRED_REARM] is RecoveryMode.SELF_REPAIR
    assert LET_GO_REASON_RECOVERY_MODE[LetGoReason.PREEMPTED] is RecoveryMode.SELF_RECOVERING


def test_preemption_is_distinct_from_failure():
    """Preemption must not collapse onto the same transition as an
    ordinary failure -- it is its own reason, resolving through the
    graceful release path instead."""
    assert (
        LET_GO_REASON_TRANSITION[LetGoReason.PREEMPTED]
        != LET_GO_REASON_TRANSITION[LetGoReason.FAILED]
    )


# --- Single-assignment invariant ---------------------------------------------


def test_no_violation_with_a_single_active_reservation_per_task():
    reservations = (
        ReservationRecord(key="k1", task_id="t1", state=SpawnState.SPAWNED),
        ReservationRecord(key="k2", task_id="t2", state=SpawnState.RESERVING),
    )
    assert violating_assignment_groups(reservations) == frozenset()


def test_violation_when_two_reservations_are_active_for_the_same_task():
    reservations = (
        ReservationRecord(key="k1", task_id="t1", state=SpawnState.SPAWNED),
        ReservationRecord(key="k2", task_id="t1", state=SpawnState.RESERVING),
    )
    assert violating_assignment_groups(reservations) == frozenset({"t1"})


def test_no_violation_when_the_prior_reservation_is_releasable():
    """A FAILED/SETTLED/REARMED/DEFERRED reservation is not ACTIVE, so a
    fresh reservation for the same task is not a violation."""
    reservations = (
        ReservationRecord(key="k1", task_id="t1", state=SpawnState.FAILED),
        ReservationRecord(key="k2", task_id="t1", state=SpawnState.RESERVING),
    )
    assert violating_assignment_groups(reservations) == frozenset()


def test_violation_when_two_reservations_share_an_active_exclusive_key():
    reservations = (
        ReservationRecord(key="k1", task_id="t1", state=SpawnState.SPAWNED, exclusive_key="x"),
        ReservationRecord(key="k2", task_id="t2", state=SpawnState.RESERVING, exclusive_key="x"),
    )
    violations = violating_assignment_groups(reservations)
    assert "exclusive:x" in violations


def test_no_violation_for_distinct_exclusive_keys():
    reservations = (
        ReservationRecord(key="k1", task_id="t1", state=SpawnState.SPAWNED, exclusive_key="x"),
        ReservationRecord(key="k2", task_id="t2", state=SpawnState.RESERVING, exclusive_key="y"),
    )
    assert violating_assignment_groups(reservations) == frozenset()


# --- Reservation<->bridge consistency ---------------------------------------


def test_not_applicable_when_either_side_is_absent_from_the_check():
    assert classify_consistency(None, BridgeState.RUNNING) is ConsistencyTier.NOT_APPLICABLE
    assert classify_consistency(SpawnState.SPAWNED, None) is ConsistencyTier.NOT_APPLICABLE
    assert classify_consistency(None, None) is ConsistencyTier.NOT_APPLICABLE


@pytest.mark.parametrize(
    "spawn_state,bridge_state",
    [
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
    ],
)
def test_whitelisted_pairings_are_consistent(spawn_state, bridge_state):
    assert classify_consistency(spawn_state, bridge_state) is ConsistencyTier.CONSISTENT


@pytest.mark.parametrize(
    "spawn_state,bridge_state",
    [
        (SpawnState.SPAWNED, BridgeState.ABSENT),
        (SpawnState.SPAWNED, BridgeState.ENDED),
        (SpawnState.COLD, BridgeState.RUNNING),
        (SpawnState.COLD, BridgeState.ENDED),
        (SpawnState.SETTLED, BridgeState.RUNNING),
        (SpawnState.FAILED, BridgeState.RUNNING),
        (SpawnState.REARMED, BridgeState.RUNNING),
        (SpawnState.DEFERRED, BridgeState.ABSENT),
        (SpawnState.DEFERRED, BridgeState.ENDED),
    ],
)
def test_documented_anomalies_are_classified_as_anomaly(spawn_state, bridge_state):
    assert classify_consistency(spawn_state, bridge_state) is ConsistencyTier.ANOMALY


def test_reserving_running_is_consistent_only_when_reused():
    assert (
        classify_consistency(
            SpawnState.RESERVING, BridgeState.RUNNING, worktree_ownership="reused"
        )
        is ConsistencyTier.CONSISTENT
    )


@pytest.mark.parametrize("ownership", [None, "created", "targeted", "unknown"])
def test_reserving_running_is_anomaly_without_reused_ownership(ownership):
    assert (
        classify_consistency(
            SpawnState.RESERVING, BridgeState.RUNNING, worktree_ownership=ownership
        )
        is ConsistencyTier.ANOMALY
    )


def test_undeclared_pairings_default_to_anomaly_not_consistency():
    """A pairing the sub-doc never explicitly addresses (e.g. RELEASING +
    HYDRATING) must default to ANOMALY, not be silently treated as fine --
    this module never assumes consistency without positive evidence."""
    assert (
        classify_consistency(SpawnState.RELEASING, BridgeState.HYDRATING)
        is ConsistencyTier.ANOMALY
    )
    assert (
        classify_consistency(SpawnState.SETTLED, BridgeState.SUSPENDED) is ConsistencyTier.ANOMALY
    )


# --- verdict_to_bridge_state: tracking-verdict -> BridgeState translation ---


def test_live_verdict_maps_to_running():
    assert verdict_to_bridge_state("live") is BridgeState.RUNNING


def test_gone_verdict_maps_to_absent():
    assert verdict_to_bridge_state("gone") is BridgeState.ABSENT


def test_unknown_verdict_maps_to_none_not_a_guess():
    assert verdict_to_bridge_state("unknown") is None


def test_an_unrecognized_verdict_maps_to_none_not_a_guess():
    assert verdict_to_bridge_state("something-new") is None
