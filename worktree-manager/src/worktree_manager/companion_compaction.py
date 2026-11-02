"""Observer-only compaction classification over qualified pre-context evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from fractions import Fraction
import math

from .companion_timeline_contract import (
    EventRef, SourceIdentity, TimelineContractError, aware_time, identifier,
    optional_text,
)


class CapacityBasis(StrEnum):
    ACTIVE_PRE_WINDOW = "active-pre-window"
    TARGET_WINDOW = "target-window"
    UNKNOWN = "unknown"


class MeasurementKind(StrEnum):
    SDK_DIRECT = "sdk-direct"
    ESTIMATED = "estimated"
    UNKNOWN = "unknown"


class PreRelation(StrEnum):
    SAME_EVENT = "same-event"
    PRODUCER_LINKED_PRE_EVENT = "producer-linked-pre-event"
    UNCORRELATED = "uncorrelated"


class Freshness(StrEnum):
    CURRENT = "current"
    STALE = "stale"
    UNKNOWN = "unknown"


class CompactionClassification(StrEnum):
    PREMATURE = "premature"
    NOT_PREMATURE_BY_THRESHOLD = "not-premature-by-threshold"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CompactionPolicy:
    threshold_percent: Fraction
    policy_ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.threshold_percent, Fraction) or not 0 <= self.threshold_percent <= 100:
            raise TimelineContractError("compaction threshold must be a finite percentage from 0 to 100")
        identifier(self.policy_ref, "compaction policy reference")

    @classmethod
    def from_number(cls, value: object, policy_ref: str) -> CompactionPolicy:
        if type(value) not in (int, float) or (isinstance(value, float) and not math.isfinite(value)):
            raise TimelineContractError("compaction threshold must be a finite number, not prose or boolean")
        if isinstance(value, (int, float)) and not 0 <= value <= 100:
            raise TimelineContractError("compaction threshold must be a percentage from 0 to 100")
        return cls(Fraction(str(value)), policy_ref)


@dataclass(frozen=True, slots=True)
class CompactionEvidence:
    source: SourceIdentity
    session_id: str
    context_epoch: str | None
    compaction_ref: EventRef
    used_tokens: int | None
    capacity_tokens: int | None
    capacity_basis: CapacityBasis
    measurement_kind: MeasurementKind
    relation: PreRelation
    freshness: Freshness
    measurement_ref: EventRef | None = None
    measurement_session_id: str | None = None
    measurement_context_epoch: str | None = None
    measured_at: datetime | None = None
    target_capacity_tokens: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceIdentity) or not isinstance(self.compaction_ref, EventRef):
            raise TimelineContractError("compaction evidence requires typed source identities")
        identifier(self.session_id, "compaction session")
        optional_text(self.context_epoch, "compaction context epoch", identity=True)
        optional_text(self.measurement_session_id, "measurement session", identity=True)
        optional_text(self.measurement_context_epoch, "measurement context epoch", identity=True)
        if self.measurement_ref is not None and not isinstance(self.measurement_ref, EventRef):
            raise TimelineContractError("measurement reference must be an EventRef")
        for value, kind in (
            (self.capacity_basis, CapacityBasis), (self.measurement_kind, MeasurementKind),
            (self.relation, PreRelation), (self.freshness, Freshness),
        ):
            if not isinstance(value, kind):
                raise TimelineContractError("compaction evidence selectors must be exact enum values")
        aware_time(self.measured_at, "measurement time", optional=True)


@dataclass(frozen=True, slots=True)
class CompactionResult:
    classification: CompactionClassification
    reason_code: str
    explanation: str
    compaction_ref: EventRef
    measurement_ref: EventRef | None
    threshold_percent: Fraction | None
    policy_ref: str | None
    used_tokens: int | None
    capacity_tokens: int | None


def classify_compaction(
    evidence: CompactionEvidence,
    policy: CompactionPolicy | None,
    *,
    expected_source: SourceIdentity,
    expected_session_id: str,
    expected_context_epoch: str | None = None,
) -> CompactionResult:
    """A target limit, latest usage timestamp or heartbeat cannot qualify pre-state."""
    if not isinstance(evidence, CompactionEvidence) or not isinstance(expected_source, SourceIdentity):
        raise TimelineContractError("classification requires typed evidence and selected source")
    if policy is not None and not isinstance(policy, CompactionPolicy):
        raise TimelineContractError("classification policy must be a CompactionPolicy")
    identifier(expected_session_id, "selected compaction session")
    optional_text(expected_context_epoch, "selected context epoch", identity=True)

    def result(classification: CompactionClassification, code: str, explanation: str) -> CompactionResult:
        return CompactionResult(
            classification, code, explanation, evidence.compaction_ref,
            evidence.measurement_ref, policy.threshold_percent if policy else None,
            policy.policy_ref if policy else None,
            evidence.used_tokens if type(evidence.used_tokens) is int else None,
            evidence.capacity_tokens if type(evidence.capacity_tokens) is int else None,
        )

    unknown = CompactionClassification.UNKNOWN
    if policy is None:
        return result(unknown, "policy-unconfigured", "Compaction observed; the viewer threshold is not configured.")
    if evidence.source != expected_source:
        return result(unknown, "source-mismatch", "Compaction evidence belongs to a different selected source.")
    same_event = evidence.relation is PreRelation.SAME_EVENT and evidence.measurement_ref == evidence.compaction_ref
    measurement_session = evidence.measurement_session_id
    if measurement_session is None and same_event:
        measurement_session = evidence.session_id
    if evidence.session_id != expected_session_id or measurement_session != expected_session_id:
        return result(unknown, "session-mismatch", "The compaction and pre-measurement are not linked to the selected session.")
    epoch = expected_context_epoch if expected_context_epoch is not None else evidence.context_epoch
    measurement_epoch = evidence.measurement_context_epoch
    if measurement_epoch is None and same_event:
        measurement_epoch = evidence.context_epoch
    if (epoch is None and (not same_event or measurement_epoch is not None)) or (
        epoch is not None and (evidence.context_epoch != epoch or measurement_epoch != epoch)
    ):
        return result(unknown, "context-epoch-unqualified", "Compaction observed; matching pre-context epoch evidence is unavailable.")
    expected_pair = (expected_source.source_key, expected_source.generation)
    if (evidence.compaction_ref.source_key, evidence.compaction_ref.generation) != expected_pair:
        return result(unknown, "compaction-reference-mismatch", "The occurrence reference does not match the source generation.")
    if evidence.measurement_ref is None or (evidence.measurement_ref.source_key, evidence.measurement_ref.generation) != expected_pair:
        return result(unknown, "measurement-reference-unqualified", "A same-source pre-measurement reference is unavailable.")
    if evidence.relation is PreRelation.UNCORRELATED or (
        evidence.relation is PreRelation.SAME_EVENT and evidence.measurement_ref != evidence.compaction_ref
    ):
        return result(unknown, "pre-relationship-unqualified", "Cursor adjacency or cached usage does not prove pre-compaction coverage.")
    if evidence.measurement_kind is not MeasurementKind.SDK_DIRECT or evidence.freshness is not Freshness.CURRENT:
        return result(unknown, "measurement-unqualified", "The pre-measurement is missing, stale or estimated.")
    if evidence.capacity_basis is not CapacityBasis.ACTIVE_PRE_WINDOW:
        return result(unknown, "active-capacity-unqualified", "A targeted model window is not established as active pre-context capacity.")
    if type(evidence.used_tokens) is not int or evidence.used_tokens < 0:
        return result(unknown, "usage-unqualified", "Authoritative nonnegative pre-context token usage is unavailable.")
    if type(evidence.capacity_tokens) is not int or evidence.capacity_tokens <= 0:
        return result(unknown, "capacity-unqualified", "Authoritative positive pre-context capacity is unavailable.")
    premature = Fraction(evidence.used_tokens * 100, evidence.capacity_tokens) < policy.threshold_percent
    return result(
        CompactionClassification.PREMATURE if premature else CompactionClassification.NOT_PREMATURE_BY_THRESHOLD,
        "below-threshold" if premature else "at-or-above-threshold",
        "Qualified pre-context utilization is below the configured threshold." if premature
        else "Qualified pre-context utilization is not below the configured threshold; no broader normality claim is made.",
    )
