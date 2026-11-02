"""Immutable synthetic DTOs, never installed providers or active worktrees."""

from datetime import datetime, timedelta, timezone

from worktree_manager.companion_timeline_contract import (
    ActorKind, Availability, Completeness, Durability, EvidenceKind, Provider,
    Qualification, SourceIdentity, SourcePosition, SourceToken, TimelineActor,
    TimelineEvent, TimelineKind, TimelineSnapshot,
)


def token(epoch: int = 0, *, head: str = "head-a", generation: str = "generation-a",
          key: str = "worktree-source") -> SourceToken:
    return SourceToken(SourceIdentity(
        Provider.WORKTREES, key, "example", "selected", head, generation,
    ), epoch)


def position(source: SourceToken, cursor: str) -> SourcePosition:
    return SourcePosition(source.identity.source_key, source.identity.generation, cursor)


def event(source: SourceToken, number: int, *, actor: str | None = "actor-a",
          summary: str | None = None, details: str = "") -> TimelineEvent:
    return TimelineEvent(
        source.identity, position(source, f"event-position-{number}"), f"event-{number}",
        TimelineActor(actor, ActorKind.SESSION, Qualification.REPORTED, actor or "unknown actor"),
        TimelineKind.SOURCE_EVENT,
        EvidenceKind.RETAINED_EVENT,
        datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=number),
        summary or f"Recorded event {number}", details=details, durability=Durability.COMMITTED,
    )


def snapshot(source: SourceToken, events: tuple[TimelineEvent, ...], *,
             cursor: str = "live-0", before: str | None = "older-0") -> TimelineSnapshot:
    return TimelineSnapshot(
        source, position(source, cursor), Availability.OBSERVED, Completeness.PARTIAL, events,
        purpose="Complete the accepted assignment.", effort_ref="efforts/active/example/README.md",
        creation_context="Original source and scope are recorded; timing may be unknown.",
        before=position(source, before) if before is not None else None,
    )
