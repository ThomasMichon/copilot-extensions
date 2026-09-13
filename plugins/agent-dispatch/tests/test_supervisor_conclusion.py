"""Direct unit tests for the pure spawn-conclusion / cleanup-retry decision
helpers extracted to :mod:`agent_dispatch.supervisor_conclusion`.

Also exercised indirectly through ``Supervisor.reconcile`` /
``release_requested_bodies`` integration tests in ``test_supervisor.py``;
this file pins down each pure decision in isolation (matching the
``spawn_attempt_projection.py`` precedent) so a future change to it fails
fast and locally rather than only via a much larger integration assertion --
the operator's standing "state-adjacent pure logic must be directly
unit-tested" bar.
"""

from __future__ import annotations

from agent_dispatch.supervisor import Supervisor
from agent_dispatch.supervisor_conclusion import (
    _append_conclusion_detail,
    _bounded_cleanup_failure,
    _bounded_component_failure,
    _cleanup_envelope_state,
    _component_retry_meta,
    _conclusion_retry_meta,
    _conclusion_retry_payload,
    _conclusion_state,
    _hold_pending_cleanup,
)


def test_bounded_cleanup_failure_stays_pending_below_the_attempt_cap():
    state, updated = _bounded_cleanup_failure({}, attempts=0, now=100.0)
    assert state == "pending"
    assert updated["attempts"] == 1
    assert updated["next_attempt_at"] == 130.0


def test_bounded_cleanup_failure_holds_at_the_attempt_cap():
    state, updated = _bounded_cleanup_failure({}, attempts=11, now=100.0)
    assert state == "held"
    assert updated["attempts"] == 12
    assert updated["next_attempt_at"] == 0
    assert updated["action"] == "preserved"
    assert updated["reason"] == "cleanup-retry-exhausted"


def test_component_retry_meta_defaults_for_a_non_dict_component():
    assert _component_retry_meta(None) == (0, 0.0)
    assert _component_retry_meta("not-a-dict") == (0, 0.0)


def test_component_retry_meta_reads_attempts_and_next_attempt_at():
    assert _component_retry_meta({"attempts": 3, "next_attempt_at": 42.5}) == (3, 42.5)


def test_bounded_component_failure_stays_pending_below_the_attempt_cap():
    state, updated = _bounded_component_failure({}, now=100.0)
    assert state == "pending"
    assert updated["state"] == "pending"
    assert updated["attempts"] == 1


def test_bounded_component_failure_holds_at_the_attempt_cap():
    state, updated = _bounded_component_failure({"attempts": 11}, now=100.0)
    assert state == "held"
    assert updated["state"] == "held"
    assert updated["next_attempt_at"] == 0


def test_hold_pending_cleanup_converts_pending_components_to_held():
    payload = {
        "session_end": {"state": "pending"},
        "worktree_cleanup": {"state": "complete"},
    }
    updated = _hold_pending_cleanup(payload)
    assert updated["action"] == "preserved"
    assert updated["reason"] == "cleanup-retry-exhausted"
    assert updated["next_attempt_at"] == 0
    assert updated["session_end"]["state"] == "held"
    assert updated["worktree_cleanup"]["state"] == "complete"


def test_cleanup_envelope_state_prefers_pending_over_held_over_complete():
    assert (
        _cleanup_envelope_state(
            {"session_end": {"state": "pending"}, "worktree_cleanup": {"state": "held"}}
        )
        == "pending"
    )
    assert (
        _cleanup_envelope_state(
            {"session_end": {"state": "complete"}, "worktree_cleanup": {"state": "held"}}
        )
        == "held"
    )
    assert (
        _cleanup_envelope_state(
            {"session_end": {"state": "complete"}, "worktree_cleanup": {"state": "complete"}}
        )
        == "complete"
    )


def test_cleanup_envelope_state_defaults_to_complete_with_no_components():
    assert _cleanup_envelope_state({}) == "complete"


def test_conclusion_state_primed_and_removed_actions_are_complete():
    assert _conclusion_state({"action": "primed"}) == "complete"
    assert _conclusion_state({"action": "already-primed"}) == "complete"
    assert _conclusion_state({"action": "removed"}) == "complete"
    assert _conclusion_state({"action": "already-removed"}) == "complete"


def test_conclusion_state_transient_reasons_are_pending():
    assert _conclusion_state({"action": "failed"}) == "pending"
    assert _conclusion_state({"reason": "live-mux"}) == "pending"
    assert _conclusion_state({"reason": "live-session"}) == "pending"
    assert _conclusion_state({"reason": "session-identity-unavailable"}) == "pending"


def test_conclusion_state_defaults_to_held():
    assert _conclusion_state({"action": "unknown", "reason": "some-error"}) == "held"


def test_append_conclusion_detail_appends_action_and_reason():
    detail = _append_conclusion_detail("base detail", {"action": "failed", "reason": "gone"})
    assert detail == "base detail; terminal conclusion failed (gone)"


def test_append_conclusion_detail_passes_through_a_none_outcome():
    assert _append_conclusion_detail("base detail", None) == "base detail"


def test_conclusion_retry_payload_parses_stored_json_and_normalizes_session():
    reservation = {"conclusion_detail": '{"session": "s1", "attempts": 2}'}
    payload = _conclusion_retry_payload(reservation)
    assert payload["acp_session_id"] == "s1"
    assert payload["attempts"] == 2


def test_conclusion_retry_payload_defaults_for_malformed_json():
    assert _conclusion_retry_payload({"conclusion_detail": "not json"}) == {}
    assert _conclusion_retry_payload({}) == {}


def test_conclusion_retry_meta_reads_attempts_and_next_attempt_at():
    reservation = {"conclusion_detail": '{"attempts": 4, "next_attempt_at": 55}'}
    assert _conclusion_retry_meta(reservation) == (4, 55.0)


def test_conclusion_retry_meta_defaults_for_an_empty_reservation():
    assert _conclusion_retry_meta({}) == (0, 0.0)


def test_supervisor_re_exposes_every_helper_as_a_static_method():
    """``Supervisor`` aliases each helper as a class-level ``staticmethod`` so
    existing ``self._foo(...)``/``Supervisor._foo(...)`` call sites keep
    working unchanged after the extraction."""
    assert Supervisor._conclusion_state({"action": "removed"}) == "complete"
    assert Supervisor._bounded_cleanup_failure({}, attempts=0, now=0.0)[0] == "pending"
    assert Supervisor._bounded_component_failure({}, now=0.0)[0] == "pending"
    assert Supervisor._conclusion_retry_meta({}) == (0, 0.0)
