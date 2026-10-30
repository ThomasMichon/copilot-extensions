"""Logical related/config acquisition coverage, never reusable input evidence."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Generic, Literal, TypeVar

from dropin_registry import EntryStatus, Finding, ScanAuthority

T = TypeVar("T")
PathSemantics = Literal["not-resolved", "registered-local-path"]


@dataclass(frozen=True)
class SourceObservation:
    """An ordered acquisition outcome; owner verdicts retain their original status.

    Targets and ownership are metadata only. No configuration bodies or freshness
    witnesses are copied into observations. Retained values are available data,
    not current confirmed contributions.
    """

    source: str
    stage: str
    authority: ScanAuthority
    findings: tuple[Finding, ...] = ()
    status: EntryStatus | None = None
    owner: str | None = None
    target: str | None = None
    retained: bool = False


@dataclass(frozen=True)
class AcquisitionReport(Generic[T]):
    """Coverage for exactly `scope`, independently of stable reuse qualification."""

    scope: str
    value: T
    observations: tuple[SourceObservation, ...]
    path_semantics: PathSemantics = "not-resolved"
    missing_coverage: tuple[str, ...] = field(default=(
        "full-load-config-closure", "inrepo-config-closure", "stable-owner-input-frontier",
    ), init=False)
    reusable: Literal[False] = field(default=False, init=False)
    reuse_blockers: tuple[str, ...] = field(default=(
        "config-owner-closure-unavailable", "stable-owner-frontier-unavailable",
    ), init=False)

    @property
    def authority(self) -> ScanAuthority:
        """Logical coverage only; a complete scan may have uncertain entries."""
        if any(o.authority is ScanAuthority.INDETERMINATE for o in self.observations):
            return ScanAuthority.INDETERMINATE
        if any(o.authority is ScanAuthority.COMPLETE for o in self.observations):
            return ScanAuthority.COMPLETE
        return ScanAuthority.ABSENT

    @property
    def findings(self) -> tuple[Finding, ...]:
        """Keep owner diagnostics, without repeating a shared entry finding."""
        return tuple(dict.fromkeys(f for o in self.observations for f in o.findings))


def observe(
    observations: list[SourceObservation] | None,
    source: str,
    stage: str,
    authority: ScanAuthority,
    *,
    findings: tuple[Finding, ...] = (),
    status: EntryStatus | None = None,
    owner: str | None = None,
    target: str | None = None,
    retained: bool = False,
) -> None:
    """Record already-acquired metadata only when the caller requested a report."""
    if observations is not None:
        observations.append(SourceObservation(source, stage, authority, findings, status, owner, target, retained))


def failed(
    observations: list[SourceObservation] | None,
    source: str,
    stage: str,
    reason: str,
    detail: str,
) -> None:
    """Record an acquisition failure without fabricating an empty success."""
    observe(observations, source, stage, ScanAuthority.INDETERMINATE, findings=(Finding(
        registry=stage, entry=source, status="indeterminate", reason=reason, detail=detail,
    ),))


def owner_findings(findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
    """Project owner reason/status/provenance, excluding unstructured body text."""
    return tuple(replace(finding, detail=None) if finding.detail is not None else finding for finding in findings)
