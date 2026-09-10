"""Structural tests for the Phase 9 machine-coupling declarations.

These assert the two coupling invariants from
``efforts/active/review-automation-reliability/phase-9-state-machine-architecture.md``
against the declared tables in ``agent_dispatch.machine_coupling``: a
bridge event names task transitions only as evidence, never applies one,
and every task transition that requires bridge confirmation names a real
bridge transition. Also covers the generic CAS/generation primitive. No
process, no live queue/bridge -- purely declared-table and pure-function
checks.
"""

from __future__ import annotations

import pytest

from agent_dispatch.machine_coupling import (
    BRIDGE_EVENT_EVIDENCE,
    BRIDGE_TRANSITION_NAMES,
    TASK_TRANSITION_BRIDGE_CONFIRMATION,
    TASK_TRANSITION_NAMES,
    BridgeEvent,
    CASOutcome,
    VersionedRecord,
    apply_transition,
    consider_task_transitions,
)

# --- Bridge-event-as-evidence-only -------------------------------------------


def test_every_bridge_event_is_declared():
    assert set(BRIDGE_EVENT_EVIDENCE) == set(BridgeEvent)


@pytest.mark.parametrize("event", list(BridgeEvent))
def test_bridge_event_evidence_names_only_real_task_transitions(event):
    for name in BRIDGE_EVENT_EVIDENCE[event]:
        assert name in TASK_TRANSITION_NAMES, (event, name)


def test_consider_task_transitions_returns_only_names_never_applies():
    """The accessor is never given a task record, so it structurally
    cannot mutate one -- calling it twice for the same event must be a
    pure, side-effect-free lookup returning the same names both times."""
    for event in BridgeEvent:
        first = consider_task_transitions(event)
        second = consider_task_transitions(event)
        assert first == second
        assert isinstance(first, frozenset)


def test_port_changed_alone_implies_no_task_transition():
    """A port change is informational only -- it must not, by itself, be
    declared as evidence for any task transition."""
    assert consider_task_transitions(BridgeEvent.PORT_CHANGED) == frozenset()


# --- Task-requests-bridge-action, waits for confirmation --------------------


def test_every_confirmed_task_transition_is_a_real_task_transition():
    for name in TASK_TRANSITION_BRIDGE_CONFIRMATION:
        assert name in TASK_TRANSITION_NAMES, name


@pytest.mark.parametrize("task_transition_name", list(TASK_TRANSITION_BRIDGE_CONFIRMATION))
def test_every_confirming_bridge_transition_is_real(task_transition_name):
    confirmations = TASK_TRANSITION_BRIDGE_CONFIRMATION[task_transition_name]
    assert confirmations, f"{task_transition_name} declares no confirmation"
    for bridge_name in confirmations:
        assert bridge_name in BRIDGE_TRANSITION_NAMES, (
            task_transition_name,
            bridge_name,
        )


def test_resume_accepts_either_resolved_liveness_outcome():
    """Phase 9's one-universal-resume model: the task machine's `resume`
    transition is confirmed by whichever bridge outcome liveness actually
    resolved to (reattach or spawn-fresh-bound), not a single hardcoded
    path."""
    assert TASK_TRANSITION_BRIDGE_CONFIRMATION["resume"] == frozenset(
        {"resume", "spawn_fresh_bound"}
    )


# --- CAS / generation mechanism ----------------------------------------------


def test_apply_transition_applied_when_generation_matches():
    record = VersionedRecord(generation=3, state="started")
    outcome, updated = apply_transition(record, expected_generation=3, to_state="suspended")
    assert outcome is CASOutcome.APPLIED
    assert updated == VersionedRecord(generation=4, state="suspended")


def test_apply_transition_already_advanced_is_a_noop_on_replay():
    """A stale expectation whose target state the record already reflects
    is a replayed request that already succeeded -- a no-op, not an
    error."""
    record = VersionedRecord(generation=4, state="suspended")
    outcome, updated = apply_transition(record, expected_generation=3, to_state="suspended")
    assert outcome is CASOutcome.ALREADY_ADVANCED
    assert updated == record


def test_apply_transition_lost_cas_when_someone_else_moved_first():
    record = VersionedRecord(generation=4, state="abandoned")
    outcome, updated = apply_transition(record, expected_generation=3, to_state="suspended")
    assert outcome is CASOutcome.LOST_CAS
    assert updated == record


def test_apply_transition_never_mutates_the_input_record():
    record = VersionedRecord(generation=3, state="started")
    before = VersionedRecord(record.generation, record.state)
    apply_transition(record, expected_generation=3, to_state="suspended")
    assert record == before
