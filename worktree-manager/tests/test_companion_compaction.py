"""Prematurity is strict pre-context policy, not a rounded pressure indicator."""

from __future__ import annotations

from dataclasses import replace
from fractions import Fraction

import pytest

from worktree_manager.companion_compaction import (
    CapacityBasis, CompactionClassification, CompactionEvidence, CompactionPolicy,
    Freshness, MeasurementKind, PreRelation, classify_compaction,
)
from worktree_manager.companion_timeline_contract import (
    EventRef, Provider, SourceIdentity, TimelineContractError,
)


def evidence(used: int = 35_000, capacity: int = 100_000) -> CompactionEvidence:
    source = SourceIdentity(Provider.BRIDGE, "session-events", "example", "selected", "head", "generation")
    occurrence = EventRef(source.source_key, source.generation, "compaction-1")
    return CompactionEvidence(
        source, "native-session", "context-1", occurrence, used, capacity,
        CapacityBasis.ACTIVE_PRE_WINDOW, MeasurementKind.SDK_DIRECT, PreRelation.SAME_EVENT,
        Freshness.CURRENT, occurrence, "native-session", "context-1",
    )


def classify(value: CompactionEvidence, policy: CompactionPolicy | None = None) -> CompactionClassification:
    result = classify_compaction(
        value, policy or CompactionPolicy.from_number(65, "operator-policy"),
        expected_source=evidence().source, expected_session_id="native-session",
        expected_context_epoch="context-1",
    )
    assert result.reason_code and result.explanation and result.compaction_ref == value.compaction_ref
    return result.classification


@pytest.mark.parametrize(("used", "expected"), [
    (0, True), (28_000, True), (35_000, True), (64_990, True), (64_999, True), (65_000, False), (75_000, False),
])
def test_threshold_uses_unrounded_pre_ratio(used: int, expected: bool) -> None:
    result = classify(evidence(used))
    assert result is (
        CompactionClassification.PREMATURE if expected else CompactionClassification.NOT_PREMATURE_BY_THRESHOLD
    )


@pytest.mark.parametrize("patch", [
    {"capacity_basis": CapacityBasis.TARGET_WINDOW},
    {"capacity_basis": CapacityBasis.UNKNOWN},
    {"freshness": Freshness.STALE},
    {"freshness": Freshness.UNKNOWN},
    {"measurement_kind": MeasurementKind.ESTIMATED},
    {"relation": PreRelation.UNCORRELATED},
    {"used_tokens": None}, {"used_tokens": True}, {"used_tokens": -1},
    {"capacity_tokens": None}, {"capacity_tokens": True}, {"capacity_tokens": 0},
    {"session_id": "other"}, {"measurement_session_id": "other"},
    {"context_epoch": None}, {"measurement_context_epoch": "other"},
    {"measurement_ref": None},
    {"measurement_ref": EventRef("other", "generation", "usage")},
    {"measurement_ref": EventRef("session-events", "generation", "other-event")},
])
def test_unqualified_data_is_unknown_not_normal(patch: dict[str, object]) -> None:
    assert classify(replace(evidence(), **patch)) is CompactionClassification.UNKNOWN


def test_exact_configured_policy_and_source_replacement() -> None:
    policy = CompactionPolicy.from_number(64.99, "selected-policy")
    assert policy.threshold_percent == Fraction(6499, 100)
    assert classify(evidence(64_990), policy) is CompactionClassification.NOT_PREMATURE_BY_THRESHOLD
    assert classify(replace(evidence(), source=replace(evidence().source, generation="replaced"))) is CompactionClassification.UNKNOWN
    for value in (True, "65%", float("nan"), float("inf"), -1, 101, 1 << 20_000):
        with pytest.raises(TimelineContractError):
            CompactionPolicy.from_number(value, "policy")


def test_same_occurrence_scope_does_not_invent_a_context_epoch() -> None:
    value = replace(evidence(), context_epoch=None, measurement_context_epoch=None, measurement_session_id=None)
    result = classify_compaction(
        value, CompactionPolicy.from_number(65, "policy"),
        expected_source=value.source, expected_session_id=value.session_id,
    )
    assert result.classification is CompactionClassification.PREMATURE
    linked = replace(value, relation=PreRelation.PRODUCER_LINKED_PRE_EVENT)
    assert classify_compaction(
        linked, CompactionPolicy.from_number(65, "policy"),
        expected_source=value.source, expected_session_id=value.session_id,
    ).classification is CompactionClassification.UNKNOWN


def test_unconfigured_policy_does_not_set_a_global_65_default() -> None:
    value = evidence()
    assert classify_compaction(
        value, None, expected_source=value.source, expected_session_id=value.session_id,
        expected_context_epoch=value.context_epoch,
    ).classification is CompactionClassification.UNKNOWN
