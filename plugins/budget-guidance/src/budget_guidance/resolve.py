"""Deterministic field-level resolution for partial budget readings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .models import BudgetConfig, READING_FIELDS, StaticAdapter, instant_text


@dataclass(frozen=True)
class FieldCandidate:
    """One source's candidate value for a resolved field."""

    field: str
    value: Any
    adapter_id: str
    source: str
    authority: int
    captured_at: datetime
    freshness_seconds: int
    freshness: str


@dataclass(frozen=True)
class ResolvedField:
    """A selected field plus all visible contradictory candidates."""

    selected: FieldCandidate
    contradictions: tuple[FieldCandidate, ...]


@dataclass(frozen=True)
class Resolution:
    """Resolved fields and adapter states."""

    fields: dict[str, ResolvedField]
    adapters: tuple[dict[str, Any], ...]


def _freshness(adapter: StaticAdapter, at: datetime) -> str:
    age = at - adapter.reading.captured_at
    if age.days < 0:
        return "fresh"
    age_microseconds = (
        (age.days * 86400 + age.seconds) * 1_000_000
        + age.microseconds
    )
    freshness_microseconds = adapter.reading.freshness_seconds * 1_000_000
    return "fresh" if age_microseconds <= freshness_microseconds else "stale"


def _ordered_candidates(candidates: list[FieldCandidate]) -> list[FieldCandidate]:
    ordered = sorted(candidates, key=lambda item: (item.source, item.adapter_id))
    ordered.sort(key=lambda item: item.captured_at, reverse=True)
    ordered.sort(key=lambda item: item.authority)
    return ordered


def resolve(config: BudgetConfig, at: datetime) -> Resolution:
    """Resolve each field independently without authority inversion."""
    candidates: dict[str, list[FieldCandidate]] = {field: [] for field in READING_FIELDS}
    adapter_states: list[dict[str, Any]] = []
    for adapter in config.adapters:
        reading = adapter.reading
        freshness = _freshness(adapter, at)
        adapter_states.append(
            {
                "id": adapter.adapter_id,
                "source": reading.source,
                "authority": adapter.authority,
                "availability": reading.availability,
                "error": reading.error,
                "captured_at": instant_text(reading.captured_at),
                "freshness": freshness,
                "freshness_seconds": reading.freshness_seconds,
            }
        )
        if reading.availability != "available":
            continue
        for field in READING_FIELDS:
            value = getattr(reading, field)
            if value is None:
                continue
            candidates[field].append(
                FieldCandidate(
                    field=field,
                    value=value,
                    adapter_id=adapter.adapter_id,
                    source=reading.source,
                    authority=adapter.authority,
                    captured_at=reading.captured_at,
                    freshness_seconds=reading.freshness_seconds,
                    freshness=freshness,
                )
            )

    fields: dict[str, ResolvedField] = {}
    for field, field_candidates in candidates.items():
        if not field_candidates:
            continue
        ordered = _ordered_candidates(field_candidates)
        selected = ordered[0]
        contradictions = tuple(
            candidate
            for candidate in ordered[1:]
            if candidate.value != selected.value
        )
        fields[field] = ResolvedField(
            selected=selected,
            contradictions=contradictions,
        )
    return Resolution(fields=fields, adapters=tuple(adapter_states))


def candidate_dict(candidate: FieldCandidate, value: Any) -> dict[str, Any]:
    """Render common candidate attribution."""
    return {
        "value": value,
        "adapter": candidate.adapter_id,
        "source": candidate.source,
        "authority": candidate.authority,
        "captured_at": instant_text(candidate.captured_at),
        "freshness": candidate.freshness,
        "freshness_seconds": candidate.freshness_seconds,
    }
