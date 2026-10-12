"""Structural tests for the declared task-status/spawn-reservation
compatibility table.

These assert properties of the *declared* table in
``agent_dispatch.task_reservation_consistency``, not a live queue/
supervisor. No process, no SQLite -- purely a check that the table is
internally consistent and matches the analysis in its own module
docstring (which pairing is covered by which method, and which pairing
is a genuinely known, uncovered gap).
"""

from __future__ import annotations

from agent_dispatch.queue import SpawnState, Status
from agent_dispatch.task_reservation_consistency import (
    ACTIVE_SPAWN_STATES,
    ALL_TASK_STATUSES,
    TaskReservationTier,
    classify_task_reservation,
    covering_sweep,
    is_known_uncovered,
)

CONCLUDED_SPAWN_STATES = frozenset(
    {SpawnState.SETTLED, SpawnState.FAILED, SpawnState.REARMED, SpawnState.DEFERRED}
)


def test_active_spawn_states_matches_queue_records():
    """This table's scope is deliberately exactly
    ``SpawnState.ACTIVE`` -- if that set ever changes upstream, this
    table needs a deliberate, reviewed follow-up, not a silent drift."""
    assert ACTIVE_SPAWN_STATES == SpawnState.ACTIVE
    assert ACTIVE_SPAWN_STATES == {
        SpawnState.RESERVING,
        SpawnState.SPAWNED,
        SpawnState.COLD,
        SpawnState.RELEASING,
    }


def test_all_task_statuses_is_exhaustive():
    assert ALL_TASK_STATUSES == {
        Status.PROPOSED,
        Status.QUEUED,
        Status.CLAIMED,
        Status.STARTED,
        Status.SUSPENDED,
        Status.SUBMITTED,
        Status.COMPLETED,
        Status.ABANDONED,
    }


def test_a_concluded_spawn_state_is_always_consistent():
    """A concluded reservation is just a historical audit record -- it may
    legitimately coexist with any task status (see the module docstring)."""
    for spawn_state in CONCLUDED_SPAWN_STATES:
        for status in ALL_TASK_STATUSES:
            assert (
                classify_task_reservation(status, spawn_state)
                is TaskReservationTier.CONSISTENT
            )


def test_reserving_and_releasing_are_consistent_with_every_status():
    """reconcile_reserving() and release_requested_bodies() are each
    status-agnostic, traced directly from their own source (see the
    module's _CONSISTENT_PAIRS comment) -- every status is covered."""
    for status in ALL_TASK_STATUSES:
        assert (
            classify_task_reservation(status, SpawnState.RESERVING)
            is TaskReservationTier.CONSISTENT
        )
        assert (
            classify_task_reservation(status, SpawnState.RELEASING)
            is TaskReservationTier.CONSISTENT
        )


def test_spawned_is_consistent_with_every_status():
    """recover_gone() (SPAWNED-scoped) plus reconcile()'s widened
    _TERMINAL (#4542) together cover every status for SPAWNED -- no
    known gap."""
    for status in ALL_TASK_STATUSES:
        assert (
            classify_task_reservation(status, SpawnState.SPAWNED)
            is TaskReservationTier.CONSISTENT
        )


def test_cold_suspended_is_the_steady_state_and_is_consistent():
    assert (
        classify_task_reservation(Status.SUSPENDED, SpawnState.COLD)
        is TaskReservationTier.CONSISTENT
    )


def test_cold_is_consistent_only_for_statuses_with_a_known_covering_sweep():
    """QUEUED (#4512) and the three _TERMINAL
    statuses (reconcile()'s own RESERVING/SPAWNED/COLD pool) each have a
    real covering sweep for a COLD reservation."""
    covered = {
        Status.SUSPENDED,
        Status.QUEUED,
        Status.SUBMITTED,
        Status.COMPLETED,
        Status.ABANDONED,
    }
    for status in covered:
        assert (
            classify_task_reservation(status, SpawnState.COLD)
            is TaskReservationTier.CONSISTENT
        )


def test_cold_is_a_known_uncovered_anomaly_for_proposed_claimed_started():
    """record_cold only ever fires for a SUSPENDED task (cool_dormant_
    bodies's own guard), so reaching one of these requires a second,
    independent bug elsewhere first -- but no sweep would ever clear it
    if it did. This is exactly the gap class this table exists to name
    rather than silently assume can't happen."""
    for status in (Status.PROPOSED, Status.CLAIMED, Status.STARTED):
        assert (
            classify_task_reservation(status, SpawnState.COLD)
            is TaskReservationTier.ANOMALY
        )
        assert is_known_uncovered(status, SpawnState.COLD)
        assert covering_sweep(status, SpawnState.COLD) is None


def test_covering_sweep_is_none_for_a_consistent_pairing():
    assert covering_sweep(Status.SUSPENDED, SpawnState.COLD) is None


def test_every_active_state_status_pair_is_classified_one_way_or_the_other():
    """No pairing may be silently unhandled -- every one of the 9x4
    combinations this table models resolves to exactly one tier. As of
    this writing every ANOMALY pairing is also a known-uncovered one (see
    the module's _COVERING_SWEEP note): a pairing folded into consistency
    once a repair sweep is traced for it, so "anomalous AND covered" is
    not currently a reachable third case -- but a future addition to
    _COVERING_SWEEP without a matching removal from _CONSISTENT_PAIRS
    would legitimately introduce one, so this only asserts today's actual
    shape rather than assuming it can never change."""
    for status in ALL_TASK_STATUSES:
        for spawn_state in ACTIVE_SPAWN_STATES:
            tier = classify_task_reservation(status, spawn_state)
            assert tier in (
                TaskReservationTier.CONSISTENT,
                TaskReservationTier.ANOMALY,
            )
            if tier is TaskReservationTier.ANOMALY:
                assert is_known_uncovered(status, spawn_state)
                assert covering_sweep(status, spawn_state) is None
