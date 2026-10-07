"""Tests for the pure PR-transition contract (``watch_contract``)."""

from __future__ import annotations

from agent_pull_requests.watch_contract import (
    CHECKS_CHANGED,
    CLOSED,
    MERGEABLE_CHANGED,
    MERGED,
    REVIEW_CHANGED,
    Baseline,
    PRSnapshot,
    advance_baseline,
    compute_transitions,
)


def test_merged_fires_from_open_baseline():
    baseline = Baseline()
    snap = PRSnapshot(pr_state="closed", merged=True)
    assert compute_transitions(baseline, snap, (MERGED, CLOSED)) == (MERGED,)


def test_closed_unmerged_fires_distinctly_from_merged():
    baseline = Baseline()
    snap = PRSnapshot(pr_state="closed", merged=False)
    assert compute_transitions(baseline, snap, (MERGED, CLOSED)) == (CLOSED,)


def test_already_merged_baseline_does_not_refire():
    baseline = Baseline(merged=True)
    snap = PRSnapshot(pr_state="closed", merged=True)
    assert compute_transitions(baseline, snap, (MERGED, CLOSED)) == ()


def test_review_decision_unknown_baseline_does_not_fire_on_first_concrete_value():
    baseline = Baseline(review_decision=None)
    snap = PRSnapshot(review_decision="APPROVED")
    assert compute_transitions(baseline, snap, (REVIEW_CHANGED,)) == ()


def test_review_decision_change_fires_once_baseline_is_concrete():
    baseline = Baseline(review_decision="CHANGES_REQUESTED")
    snap = PRSnapshot(review_decision="APPROVED")
    assert compute_transitions(baseline, snap, (REVIEW_CHANGED,)) == (REVIEW_CHANGED,)


def test_review_decision_same_value_does_not_fire():
    baseline = Baseline(review_decision="APPROVED")
    snap = PRSnapshot(review_decision="APPROVED")
    assert compute_transitions(baseline, snap, (REVIEW_CHANGED,)) == ()


def test_mergeable_change_fires_only_when_requested():
    baseline = Baseline(mergeable="CONFLICTING")
    snap = PRSnapshot(mergeable="MERGEABLE")
    assert compute_transitions(baseline, snap, (MERGEABLE_CHANGED,)) == (MERGEABLE_CHANGED,)
    assert compute_transitions(baseline, snap, (MERGED, CLOSED)) == ()


def test_checks_change_fires():
    baseline = Baseline(checks_state="pending")
    snap = PRSnapshot(checks_state="success")
    assert compute_transitions(baseline, snap, (CHECKS_CHANGED,)) == (CHECKS_CHANGED,)


def test_multiple_transitions_can_fire_together():
    baseline = Baseline(review_decision="REVIEW_REQUIRED", checks_state="pending")
    snap = PRSnapshot(review_decision="APPROVED", checks_state="success")
    fired = compute_transitions(baseline, snap, (REVIEW_CHANGED, CHECKS_CHANGED))
    assert set(fired) == {REVIEW_CHANGED, CHECKS_CHANGED}


def test_untracked_transition_never_fires_even_if_true():
    baseline = Baseline()
    snap = PRSnapshot(pr_state="closed", merged=True)
    # Caller only cares about review changes -- a real merge must not fire.
    assert compute_transitions(baseline, snap, (REVIEW_CHANGED,)) == ()


def test_advance_baseline_fills_unknown_fields_without_firing():
    baseline = Baseline()
    snap = PRSnapshot(
        review_decision="REVIEW_REQUIRED", mergeable="MERGEABLE", checks_state="pending"
    )
    advanced = advance_baseline(baseline, snap)
    assert advanced.review_decision == "REVIEW_REQUIRED"
    assert advanced.mergeable == "MERGEABLE"
    assert advanced.checks_state == "pending"
    # And the advanced baseline does NOT retroactively fire against the
    # same snapshot it was adopted from.
    all_transitions = (REVIEW_CHANGED, MERGEABLE_CHANGED, CHECKS_CHANGED)
    assert compute_transitions(advanced, snap, all_transitions) == ()


def test_advance_baseline_never_unmerges_or_uncloses():
    baseline = Baseline(merged=True, closed=True)
    snap = PRSnapshot(pr_state="open", merged=False)
    advanced = advance_baseline(baseline, snap)
    assert advanced.merged is True
    assert advanced.closed is True
