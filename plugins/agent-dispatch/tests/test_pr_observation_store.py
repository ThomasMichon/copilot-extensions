"""Tests for the Phase 10 item 3 PR-observation persistence store."""

from __future__ import annotations

import time
from pathlib import Path

from agent_dispatch.github_provider_adapter import PRObservation
from agent_dispatch.pr_observation_store import PRObservationStore, record_observation
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


def test_get_on_an_unknown_pr_returns_none(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    assert store.get("example/project", 1) is None
    assert store.last_observed_at("example/project", 1) is None


def test_put_then_get_round_trips_every_field(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    observation = _observation(
        number=7,
        approval_status=ApprovalStatus.CHANGES_REQUESTED,
        mergeability=Mergeability.CHECKS_FAILED,
        holds=frozenset({HoldReason.DRAFT, HoldReason.WIP}),
        diff_hash="abc",
        base_sha="def",
    )

    store.put("example/project", 7, observation, observed_at=1000.0)
    result = store.get("example/project", 7)

    assert result == observation
    assert store.last_observed_at("example/project", 7) == 1000.0


def test_put_overwrites_the_prior_observation_for_the_same_key(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    store.put(
        "example/project",
        1,
        _observation(approval_status=ApprovalStatus.PENDING),
        observed_at=1000.0,
    )
    store.put(
        "example/project",
        1,
        _observation(approval_status=ApprovalStatus.APPROVED),
        observed_at=2000.0,
    )

    result = store.get("example/project", 1)

    assert result.approval_status == ApprovalStatus.APPROVED
    assert store.last_observed_at("example/project", 1) == 2000.0


def test_different_repos_are_independent(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    store.put("example/one", 1, _observation(number=1), observed_at=1000.0)

    assert store.get("example/two", 1) is None
    assert store.get("example/one", 1) is not None


def test_different_pr_numbers_in_the_same_repo_are_independent(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    store.put("example/project", 1, _observation(number=1), observed_at=1000.0)

    assert store.get("example/project", 2) is None
    assert store.get("example/project", 1) is not None


def test_store_persists_across_instances_against_the_same_db_path(tmp_path: Path):
    db_path = tmp_path / "pr.db"
    PRObservationStore(db_path).put("example/project", 1, _observation(), observed_at=1000.0)

    reopened = PRObservationStore(db_path)
    assert reopened.get("example/project", 1) is not None


# --- record_observation ------------------------------------------------------


def test_record_observation_persists_the_first_observation_unchanged(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")
    current = _observation(approval_status=ApprovalStatus.APPROVED)

    result = record_observation(store, "example/project", 1, current, now=1000.0)

    assert result.approval_status == ApprovalStatus.APPROVED
    assert store.get("example/project", 1) == current
    assert store.last_observed_at("example/project", 1) == 1000.0


def test_record_observation_applies_staleness_against_the_stored_previous(
    tmp_path: Path,
):
    store = PRObservationStore(tmp_path / "pr.db")
    record_observation(
        store,
        "example/project",
        1,
        _observation(approval_status=ApprovalStatus.APPROVED, diff_hash="diff-1"),
        now=1000.0,
    )

    result = record_observation(
        store,
        "example/project",
        1,
        _observation(approval_status=ApprovalStatus.APPROVED, diff_hash="diff-2"),
        now=2000.0,
    )

    assert result.approval_status == ApprovalStatus.STALE
    assert store.get("example/project", 1).approval_status == ApprovalStatus.STALE
    assert store.last_observed_at("example/project", 1) == 2000.0


def test_record_observation_defaults_now_to_the_current_wall_clock(tmp_path: Path):
    store = PRObservationStore(tmp_path / "pr.db")

    before = time.time()
    record_observation(store, "example/project", 1, _observation())
    after = time.time()

    observed_at = store.last_observed_at("example/project", 1)
    assert before <= observed_at <= after
