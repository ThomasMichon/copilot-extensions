"""Tests for the Phase 10 item 3 revision-history evaluator."""

from __future__ import annotations

import pytest

from agent_dispatch.github_provider_adapter import PRObservation
from agent_dispatch.pr_revision_evaluator import evaluate_observation
from agent_dispatch.provider_state_machine import (
    ApprovalStatus,
    HoldReason,
    Mergeability,
    Revision,
)


def _observation(
    number: int = 1,
    approval_status: ApprovalStatus = ApprovalStatus.APPROVED,
    mergeability: Mergeability = Mergeability.CLEAN,
    holds: frozenset[HoldReason] = frozenset(),
    diff_hash: str = "diff-1",
    base_sha: str = "base-1",
) -> PRObservation:
    return PRObservation(
        number=number,
        approval_status=approval_status,
        mergeability=mergeability,
        holds=holds,
        revision=Revision(diff_hash=diff_hash, base_sha=base_sha),
    )


def test_first_observation_ever_is_returned_unchanged():
    current = _observation(approval_status=ApprovalStatus.APPROVED)
    result = evaluate_observation(None, current)
    assert result is current


def test_unchanged_revision_leaves_approved_status_untouched():
    previous = _observation(approval_status=ApprovalStatus.APPROVED)
    current = _observation(approval_status=ApprovalStatus.APPROVED)
    result = evaluate_observation(previous, current)
    assert result.approval_status == ApprovalStatus.APPROVED


def test_base_only_movement_leaves_approved_status_untouched():
    previous = _observation(
        approval_status=ApprovalStatus.APPROVED, diff_hash="diff-1", base_sha="base-1"
    )
    current = _observation(
        approval_status=ApprovalStatus.APPROVED, diff_hash="diff-1", base_sha="base-2"
    )
    result = evaluate_observation(previous, current)
    assert result.approval_status == ApprovalStatus.APPROVED


def test_substantive_revision_change_invalidates_an_approved_status_to_stale():
    previous = _observation(
        approval_status=ApprovalStatus.APPROVED, diff_hash="diff-1", base_sha="base-1"
    )
    # The provider is queried after the new commit landed, but (as is
    # typical without a "dismiss stale reviews" branch-protection rule)
    # GitHub itself still reports the review decision as APPROVED.
    current = _observation(
        approval_status=ApprovalStatus.APPROVED, diff_hash="diff-2", base_sha="base-1"
    )
    result = evaluate_observation(previous, current)
    assert result.approval_status == ApprovalStatus.STALE


def test_substantive_revision_change_leaves_a_non_approved_status_untouched():
    previous = _observation(
        approval_status=ApprovalStatus.APPROVED, diff_hash="diff-1", base_sha="base-1"
    )
    # The provider itself already reports this isn't approved any more
    # (e.g. someone requested changes on the new commit) -- nothing left
    # to invalidate.
    current = _observation(
        approval_status=ApprovalStatus.CHANGES_REQUESTED,
        diff_hash="diff-2",
        base_sha="base-1",
    )
    result = evaluate_observation(previous, current)
    assert result.approval_status == ApprovalStatus.CHANGES_REQUESTED


@pytest.mark.parametrize(
    "status", [ApprovalStatus.NONE, ApprovalStatus.PENDING, ApprovalStatus.STALE]
)
def test_substantive_revision_change_with_non_approved_current_status_is_a_noop(
    status,
):
    previous = _observation(
        approval_status=ApprovalStatus.APPROVED, diff_hash="diff-1", base_sha="base-1"
    )
    current = _observation(approval_status=status, diff_hash="diff-2", base_sha="base-1")
    result = evaluate_observation(previous, current)
    assert result.approval_status == status


def test_evaluation_preserves_every_other_field():
    previous = _observation(diff_hash="diff-1", base_sha="base-1")
    current = _observation(
        number=42,
        approval_status=ApprovalStatus.APPROVED,
        mergeability=Mergeability.CHECKS_PENDING,
        holds=frozenset({HoldReason.DRAFT}),
        diff_hash="diff-2",
        base_sha="base-1",
    )
    result = evaluate_observation(previous, current)
    assert result.number == 42
    assert result.mergeability == Mergeability.CHECKS_PENDING
    assert result.holds == frozenset({HoldReason.DRAFT})
    assert result.revision == current.revision
    assert result.approval_status == ApprovalStatus.STALE
