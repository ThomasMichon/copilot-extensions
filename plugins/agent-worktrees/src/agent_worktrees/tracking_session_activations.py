"""Canonical session activation metadata, independent of provider configuration."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .tracking import SessionEntry


MAX_PRUNED_SOURCES = 256


def activation_token(value: object, *, field: str, limit: int = 128) -> str | None:
    """Validate optional opaque lifecycle metadata without inventing legacy proof."""
    if value is None:
        return None
    if (
        not isinstance(value, str) or not value or len(value) > limit
        or re.fullmatch(r"[A-Za-z0-9._:-]+", value) is None
    ):
        raise ValueError(f"Invalid activation {field}")
    return value


@dataclass
class SessionActivation:
    """One observed interval in which a session was associated with a worktree.

    Hook delivery is at-least-once and a Copilot session may be resumed many
    times. Keeping each interval append-only preserves that history instead of
    overwriting the session's original ``started_at`` on every resume.
    """

    ordinal: int
    started_at: str
    start_recorded_at: str
    start_source: str = "hook"
    ended_at: str | None = None
    end_recorded_at: str | None = None
    end_source: str | None = None
    source_identity: str | None = None
    end_reason: str | None = None
    source_ambiguous: bool = False

    def __post_init__(self) -> None:
        activation_token(self.source_identity, field="source_identity")
        activation_token(self.end_reason, field="end_reason", limit=64)
        if type(self.source_ambiguous) is not bool:
            raise ValueError("Invalid activation source ambiguity")


def parse_activations(raw: object) -> list[SessionActivation]:
    """Read the legacy-compatible activation ledger."""
    activations = []
    if not isinstance(raw, list):
        return activations
    for item in raw:
        if not isinstance(item, dict):
            continue
        started = item.get("started_at", "")
        if started in (None, "", "null"):
            continue

        def timestamp(value: object) -> str | None:
            if hasattr(value, "isoformat"):
                return value.isoformat()
            return str(value) if value not in (None, "", "null") else None

        try:
            ordinal = int(item.get("ordinal", 0))
        except (TypeError, ValueError):
            ordinal = 0
        activations.append(SessionActivation(
            ordinal=ordinal if ordinal > 0 else len(activations) + 1,
            started_at=timestamp(started),
            start_recorded_at=timestamp(item.get("start_recorded_at")) or timestamp(started),
            start_source=str(item.get("start_source") or "hook"),
            ended_at=timestamp(item.get("ended_at")),
            end_recorded_at=timestamp(item.get("end_recorded_at")),
            end_source=str(item["end_source"]) if item.get("end_source") else None,
            source_identity=item.get("source_identity"),
            end_reason=item.get("end_reason"),
            source_ambiguous=item.get("source_ambiguous", False),
        ))
    return activations


def parse_pruning_fence(raw: dict) -> tuple[list[str], bool]:
    """Reject malformed safety metadata rather than forgetting discarded sources."""
    sources = raw.get("pruned_sources", [])
    saturated = raw.get("source_history_saturated", False)
    if (
        not isinstance(sources, list) or len(sources) > MAX_PRUNED_SOURCES
        or type(saturated) is not bool
        or any(activation_token(value, field="pruned source") is None for value in sources)
        or len(set(sources)) != len(sources)
    ):
        raise ValueError("Invalid activation pruning fence")
    return list(sources), saturated


def merge_pruning_fence(entry: SessionEntry, current: SessionEntry) -> None:
    """Safety metadata is monotonic, even across equal-revision stale saves."""
    retained = {(item.ordinal, item.source_identity) for item in entry.activations}
    sources = sorted(set(entry.pruned_sources) | set(current.pruned_sources) | {
        item.source_identity for item in current.activations
        if item.source_identity is not None
        and (item.ordinal, item.source_identity) not in retained
    })
    entry.source_history_saturated = (
        entry.source_history_saturated or current.source_history_saturated
        or len(sources) > MAX_PRUNED_SOURCES
    )
    entry.pruned_sources = sources[:MAX_PRUNED_SOURCES]


def prune_activations(entry: SessionEntry, limit: int) -> None:
    """Remember source identities before discarding bounded interval history."""
    discarded = entry.activations[:-limit]
    sources = sorted(set(entry.pruned_sources) | {
        item.source_identity for item in discarded if item.source_identity is not None
    })
    entry.source_history_saturated |= len(sources) > MAX_PRUNED_SOURCES
    entry.pruned_sources = sources[:MAX_PRUNED_SOURCES]
    entry.activations = entry.activations[-limit:]


def source_end_authoritative(entry: SessionEntry, source: str) -> bool:
    """A discarded interval must never regain exact-source End authority."""
    return not entry.source_history_saturated and source not in entry.pruned_sources


def activation_fields(entry: SessionEntry) -> dict:
    """Serialize activation history and its independent bounded pruning fence."""
    fields = {}
    if entry.pruned_sources:
        fields["pruned_sources"] = entry.pruned_sources
    if entry.source_history_saturated:
        fields["source_history_saturated"] = True
    if entry.activations:
        fields["activations"] = [
            {
                "ordinal": item.ordinal,
                "started_at": item.started_at,
                "start_recorded_at": item.start_recorded_at,
                "start_source": item.start_source,
                **{
                    name: getattr(item, name)
                    for name in (
                        "source_identity", "source_ambiguous", "ended_at",
                        "end_recorded_at", "end_source", "end_reason",
                    ) if getattr(item, name)
                },
            }
            for item in entry.activations
        ]
    return fields
