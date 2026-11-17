"""Structured scan and entry verdicts shared by drop-in registry consumers."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Generic, TypeVar

T = TypeVar("T")


class ScanAuthority(str, Enum):
    """Whether a registry scan is authoritative for desired-set reconciliation."""

    COMPLETE = "complete"
    ABSENT = "absent"
    INDETERMINATE = "indeterminate"


class EntryStatus(str, Enum):
    """Activation verdict for one registry entry."""

    ACTIVE = "active"
    ACTIVE_WITH_ADVISORY = "active-with-advisory"
    INACTIVE = "inactive"
    INDETERMINATE = "indeterminate"


@dataclass(frozen=True)
class Finding:
    """One stable diagnostic emitted by a registry classifier."""

    registry: str
    entry: str
    status: str
    reason: str
    target: str | None = None
    owner: str | None = None
    remedy: str | None = None
    detail: str | None = None

    def to_dict(self) -> dict[str, str]:
        """Return the compact JSON-friendly representation used by doctor commands."""
        out = {
            "registry": self.registry,
            "entry": self.entry,
            "status": self.status,
            "reason": self.reason,
        }
        for key in ("target", "owner", "remedy", "detail"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out

    def fingerprint(self) -> str:
        """Stable operational identity for warning deduplication.

        Rendering metadata is deliberately excluded: detail/remedy/owner text may
        change while the underlying entry/reason/target failure is unchanged.
        """
        payload = json.dumps(
            {
                "registry": self.registry,
                "entry": self.entry,
                "reason": self.reason,
                "target": self.target,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True)
class EntryDecision(Generic[T]):
    """Classified state and optional active value for one entry."""

    status: EntryStatus
    value: T | None = None
    findings: tuple[Finding, ...] = ()

    def __post_init__(self) -> None:
        active = self.status in (EntryStatus.ACTIVE, EntryStatus.ACTIVE_WITH_ADVISORY)
        if active and self.value is None:
            raise ValueError(f"{self.status.value} decisions require a value")
        if not active and self.value is not None:
            raise ValueError(f"{self.status.value} decisions cannot carry a value")
        if self.status is EntryStatus.ACTIVE and self.findings:
            raise ValueError("active decisions cannot carry findings; use active-with-advisory")
        if self.status is not EntryStatus.ACTIVE and not self.findings:
            raise ValueError(f"{self.status.value} decisions require at least one finding")

    @classmethod
    def active(cls, value: T) -> EntryDecision[T]:
        return cls(status=EntryStatus.ACTIVE, value=value)

    @classmethod
    def advisory(cls, value: T, *findings: Finding) -> EntryDecision[T]:
        return cls(
            status=EntryStatus.ACTIVE_WITH_ADVISORY,
            value=value,
            findings=tuple(findings),
        )

    @classmethod
    def inactive(cls, *findings: Finding) -> EntryDecision[T]:
        return cls(status=EntryStatus.INACTIVE, findings=tuple(findings))

    @classmethod
    def indeterminate(cls, *findings: Finding) -> EntryDecision[T]:
        return cls(status=EntryStatus.INDETERMINATE, findings=tuple(findings))


@dataclass(frozen=True)
class ScanSnapshot(Generic[T]):
    """One registry scan, including non-active entry verdicts."""

    registry: str
    authority: ScanAuthority
    decisions: Mapping[str, EntryDecision[T]] = field(default_factory=dict)
    findings: tuple[Finding, ...] = ()

    def reconcile(self, previous: Mapping[str, T] | None = None) -> dict[str, T]:
        """Derive desired state without turning uncertainty into removal.

        A registry-level indeterminate scan preserves the complete previous set.
        A complete scan rebuilds the set, while retaining a prior value for an
        entry whose own current read is indeterminate. Confirmed absence is an
        authoritative empty set.
        """
        prior = dict(previous or {})
        if self.authority is ScanAuthority.INDETERMINATE:
            return prior
        if self.authority is ScanAuthority.ABSENT:
            return {}

        desired: dict[str, T] = {}
        for key, decision in self.decisions.items():
            if decision.status in (
                EntryStatus.ACTIVE,
                EntryStatus.ACTIVE_WITH_ADVISORY,
            ):
                if decision.value is not None:
                    desired[key] = decision.value
            elif decision.status is EntryStatus.INDETERMINATE and key in prior:
                desired[key] = prior[key]
        return desired
