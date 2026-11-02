"""Immutable observer data; identities and evidence are not execution authority."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
import json
import math
from typing import cast

MAX_FRAME_BYTES = 35 * 1024
MAX_BODY_BYTES = 32 * 1024
MAX_PAGE_EVENTS = 64
MAX_WINDOW_EVENTS = 5_000
MAX_WINDOW_BYTES = 8 * 1024 * 1024
MAX_STRIP_CACHE_BYTES = 4 * 1024 * 1024
MAX_LANES = 32
MAX_EXPANDED_GROUPS = 16
MAX_DETAIL_LINES = 64
OVERSCAN_LINES = 8


class TimelineContractError(ValueError):
    """A source or consumer supplied invalid observer data."""


class Provider(StrEnum):
    WORKTREES = "worktrees"
    AGENDA = "agenda"
    BRIDGE = "bridge"


class Availability(StrEnum):
    OBSERVED = "observed"
    UNSUPPORTED = "unsupported"
    UNAVAILABLE = "unavailable"
    HEAD_CHANGED = "head-changed"
    UNKNOWN = "unknown"


class Completeness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    UNKNOWN = "unknown"


class Durability(StrEnum):
    COMMITTED = "committed"
    PENDING = "pending"
    UNKNOWN = "unknown"


class EvidenceKind(StrEnum):
    PERSISTED_MUTATION = "persisted-mutation"
    RETAINED_EVENT = "retained-event"
    OBSERVED_CHANGE = "observed-change"
    REPORTED_LIFECYCLE = "reported-lifecycle"


class ActorKind(StrEnum):
    WORKTREE = "worktree"
    SESSION = "session"
    SUBAGENT = "subagent"
    BRIDGE_SESSION = "bridge-session"
    RESOURCE = "resource"
    UNKNOWN = "unknown"


class Qualification(StrEnum):
    AUTHORITATIVE = "authoritative"
    REPORTED = "reported"
    UNKNOWN = "unknown"


class RelationKind(StrEnum):
    PARENT = "parent"
    RETURN = "return"
    RESOURCE = "resource"
    SUCCESSION = "succession"
    UNLINKED = "unlinked"


class TimelineKind(StrEnum):
    WORKTREE_CREATED = "worktree-created"
    PURPOSE_CHANGED = "purpose-changed"
    EFFORT_BOUND = "effort-bound"
    EFFORT_RELEASED = "effort-released"
    CLAIM_ADDED = "claim-added"
    CLAIM_RELEASED = "claim-released"
    CLAIM_SETTLED = "claim-settled"
    AGENDA_TRANSITION = "agenda-transition"
    CONTRIBUTION_REPORTED = "contribution-reported"
    BRIDGE_MEMBER_CHANGED = "bridge-member-changed"
    INPUT_REQUEST = "input-request"
    PERMISSION_REQUEST = "permission-request"
    COMPACTION = "compaction"
    CONTEXT_PRESSURE = "context-pressure"
    SOURCE_GAP = "source-gap"
    OBSERVED_CHANGE = "observed-change"
    SOURCE_EVENT = "source-event"


def text(value: str, label: str, *, empty: bool = False, limit: int = 4096) -> None:
    if not isinstance(value, str) or "\x00" in value or (not empty and not value.strip()):
        raise TimelineContractError(f"{label} must be literal text without NUL")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeError as exc:
        raise TimelineContractError(f"{label} is not valid UTF-8 text") from exc
    if size > limit:
        raise TimelineContractError(f"{label} exceeds its text bound")


def identifier(value: str, label: str) -> None:
    text(value, label, limit=1024)
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise TimelineContractError(f"{label} contains an identifier control character")


def optional_text(value: str | None, label: str, *, identity: bool = False) -> None:
    if value is not None:
        identifier(value, label) if identity else text(value, label)


def aware_time(value: datetime | None, label: str, *, optional: bool = False) -> None:
    if optional and value is None:
        return
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise TimelineContractError(f"{label} must be a timezone-aware timestamp")


def literal_display(value: str) -> str:
    """Escape terminal controls at presentation, without rewriting source values."""
    return "".join(
        f"\\x{ord(character):02x}" if (ord(character) < 32 and character not in "\n\t") or ord(character) == 127
        else character for character in value
    )


def _finite_json(value: object, depth: int = 0) -> None:
    if depth > 16:
        raise TimelineContractError("observer JSON exceeds its nesting bound")
    if isinstance(value, dict):
        if len(value) > 128:
            raise TimelineContractError("observer JSON exceeds its member bound")
        for key, item in value.items():
            if not isinstance(key, str):
                raise TimelineContractError("observer JSON keys must be text")
            _finite_json(key, depth + 1)
            _finite_json(item, depth + 1)
    elif isinstance(value, list):
        if len(value) > 128:
            raise TimelineContractError("observer JSON exceeds its array bound")
        for item in value:
            _finite_json(item, depth + 1)
    elif isinstance(value, str):
        try:
            size = len(value.encode("utf-8"))
        except UnicodeError as exc:
            raise TimelineContractError("observer JSON contains invalid UTF-8 text") from exc
        if size > 4096:
            raise TimelineContractError("observer JSON exceeds its string bound")
    elif type(value) is float:
        if not math.isfinite(value):
            raise TimelineContractError("observer JSON contains a nonfinite number")
    elif value is not None and type(value) not in (bool, int):
        raise TimelineContractError("observer payload is not finite JSON")


def _pairs(values: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in values:
        if key in result:
            raise TimelineContractError("observer JSON contains a duplicate key")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise TimelineContractError(f"observer JSON contains {value}")


def canonical_payload(value: str | None, *, limit: int = 4096) -> str | None:
    if value is None:
        return None
    text(value, "observer JSON", limit=limit)
    try:
        decoded = json.loads(value, object_pairs_hook=_pairs, parse_constant=_constant)
        _finite_json(decoded)
        encoded = json.dumps(decoded, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise TimelineContractError(f"invalid observer JSON: {exc}") from exc
    if len(encoded.encode("utf-8")) > limit:
        raise TimelineContractError("observer JSON exceeds its encoded bound")
    return encoded


def _encoded(value: object) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise TimelineContractError(f"observer data cannot be encoded: {exc}") from exc


def decode_wire_frame(raw: bytes) -> dict[str, object]:
    """Apply wire limits before enrichment into internal immutable observer DTOs."""
    if not isinstance(raw, bytes) or len(raw) > MAX_FRAME_BYTES:
        raise TimelineContractError("observer wire frame exceeds its bound")
    try:
        value: object = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        _finite_json(value)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise TimelineContractError(f"invalid observer wire frame: {exc}") from exc
    if not isinstance(value, dict):
        raise TimelineContractError("observer wire frame must be an object")
    return cast(dict[str, object], value)


def check_wire_body(value: object) -> None:
    _finite_json(value)
    if len(_encoded(value)) + 1 > MAX_BODY_BYTES:
        raise TimelineContractError("observer wire body exceeds its bound")


@dataclass(frozen=True, slots=True)
class SourceIdentity:
    provider: Provider
    source_key: str
    project: str
    worktree_id: str
    head_session_id: str | None
    generation: str
    owner_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.provider, Provider):
            raise TimelineContractError("provider must be an exact Provider value")
        for label in ("source_key", "project", "worktree_id", "generation"):
            identifier(getattr(self, label), label)
        optional_text(self.head_session_id, "head_session_id", identity=True)
        optional_text(self.owner_ref, "owner_ref", identity=True)


@dataclass(frozen=True, slots=True)
class SourceToken:
    identity: SourceIdentity
    request_epoch: int

    def __post_init__(self) -> None:
        if not isinstance(self.identity, SourceIdentity) or type(self.request_epoch) is not int or self.request_epoch < 0:
            raise TimelineContractError("source request token has an invalid identity or epoch")


@dataclass(frozen=True, slots=True)
class SourcePosition:
    source_key: str
    generation: str
    cursor: str

    def __post_init__(self) -> None:
        for label in ("source_key", "generation", "cursor"):
            identifier(getattr(self, label), label)

    def matches(self, source: SourceIdentity) -> bool:
        return (self.source_key, self.generation) == (source.source_key, source.generation)


@dataclass(frozen=True, slots=True)
class EventRef:
    source_key: str
    generation: str
    event_id: str

    def __post_init__(self) -> None:
        for label in ("source_key", "generation", "event_id"):
            identifier(getattr(self, label), label)


@dataclass(frozen=True, slots=True)
class TimelineActor:
    actor_id: str | None
    kind: ActorKind
    qualification: Qualification
    label: str
    reported_state: str | None = None

    def __post_init__(self) -> None:
        optional_text(self.actor_id, "actor_id", identity=True)
        text(self.label, "actor label")
        optional_text(self.reported_state, "reported actor state")
        if not isinstance(self.kind, ActorKind) or not isinstance(self.qualification, Qualification):
            raise TimelineContractError("actor kind/qualification must be exact enums")
        if self.qualification is Qualification.AUTHORITATIVE and self.actor_id is None:
            raise TimelineContractError("an authoritative actor requires an identity")


@dataclass(frozen=True, slots=True)
class TimelineRelation:
    kind: RelationKind
    qualification: Qualification
    target: EventRef | None = None
    reference: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, RelationKind) or not isinstance(self.qualification, Qualification):
            raise TimelineContractError("relation kind/qualification must be exact enums")
        if self.target is not None and not isinstance(self.target, EventRef):
            raise TimelineContractError("relation target must be an EventRef")
        optional_text(self.reference, "relation reference", identity=True)
        if self.qualification is Qualification.AUTHORITATIVE and self.target is None and self.reference is None:
            raise TimelineContractError("an authoritative relation requires a source reference")


@dataclass(frozen=True, slots=True)
class TimelineEvent:
    source: SourceIdentity
    position: SourcePosition
    event_id: str
    actor: TimelineActor
    kind: TimelineKind
    evidence_kind: EvidenceKind
    observed_at: datetime
    summary: str
    occurred_at: datetime | None = None
    details: str = ""
    relations: tuple[TimelineRelation, ...] = ()
    payload_ref: str | None = None
    reported_phase: str | None = None
    durability: Durability = Durability.UNKNOWN
    data_json: str | None = None
    _encoded_size: int = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceIdentity) or not isinstance(self.position, SourcePosition) or not self.position.matches(self.source):
            raise TimelineContractError("event position does not match its source identity")
        identifier(self.event_id, "event_id")
        if not isinstance(self.actor, TimelineActor) or not isinstance(self.kind, TimelineKind):
            raise TimelineContractError("event actor/kind must be typed observer values")
        if not isinstance(self.evidence_kind, EvidenceKind) or not isinstance(self.durability, Durability):
            raise TimelineContractError("event evidence/durability must be exact enums")
        aware_time(self.observed_at, "observed_at")
        aware_time(self.occurred_at, "occurred_at", optional=True)
        text(self.summary, "event summary")
        text(self.details, "event details", empty=True)
        optional_text(self.payload_ref, "payload_ref", identity=True)
        optional_text(self.reported_phase, "reported_phase")
        if not isinstance(self.relations, tuple) or len(self.relations) > 32 or any(not isinstance(item, TimelineRelation) for item in self.relations):
            raise TimelineContractError("event relations exceed their typed bound")
        object.__setattr__(self, "data_json", canonical_payload(self.data_json))
        size = len(_encoded(self.content()))
        if size > MAX_WINDOW_BYTES:
            raise TimelineContractError("event exceeds its display-window bound")
        object.__setattr__(self, "_encoded_size", size)

    @property
    def ref(self) -> EventRef:
        return EventRef(self.source.source_key, self.source.generation, self.event_id)

    @property
    def encoded_size(self) -> int:
        return self._encoded_size

    def content(self) -> dict[str, object]:
        return {
            "source": asdict(self.source), "position": asdict(self.position), "event_id": self.event_id,
            "actor": asdict(self.actor), "kind": self.kind.value, "evidence_kind": self.evidence_kind.value,
            "observed_at": self.observed_at.isoformat(), "occurred_at": self.occurred_at.isoformat() if self.occurred_at else None,
            "summary": self.summary, "details": self.details, "relations": [asdict(item) for item in self.relations],
            "payload_ref": self.payload_ref, "reported_phase": self.reported_phase,
            "durability": self.durability.value, "data_json": self.data_json,
        }


def _events(events: tuple[TimelineEvent, ...], source: SourceIdentity) -> None:
    if not isinstance(events, tuple) or len(events) > MAX_PAGE_EVENTS:
        raise TimelineContractError("observer page exceeds its event bound")
    seen: set[EventRef] = set()
    size = 0
    for event in events:
        if not isinstance(event, TimelineEvent) or event.source != source or event.ref in seen:
            raise TimelineContractError("observer page has a duplicate or wrong-source event")
        seen.add(event.ref)
        size += event.encoded_size
    # Repeated source identities enlarge DTOs; wire limits apply before normalization.
    if size > MAX_WINDOW_BYTES:
        raise TimelineContractError("observer page exceeds its display-window bound")


@dataclass(frozen=True, slots=True)
class TimelineSnapshot:
    token: SourceToken
    position: SourcePosition | None
    availability: Availability
    completeness: Completeness
    events: tuple[TimelineEvent, ...] = ()
    as_of: datetime | None = None
    purpose: str | None = None
    effort_ref: str | None = None
    creation_context: str | None = None
    before: SourcePosition | None = None
    facts_json: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.token, SourceToken) or not isinstance(self.availability, Availability) or not isinstance(self.completeness, Completeness):
            raise TimelineContractError("snapshot requires typed source and availability")
        for position in (self.position, self.before):
            if position is not None and (not isinstance(position, SourcePosition) or not position.matches(self.token.identity)):
                raise TimelineContractError("snapshot cursor does not match its source")
        aware_time(self.as_of, "snapshot as_of", optional=True)
        optional_text(self.purpose, "purpose")
        optional_text(self.effort_ref, "effort_ref", identity=True)
        optional_text(self.creation_context, "creation_context")
        _events(self.events, self.token.identity)
        object.__setattr__(self, "facts_json", canonical_payload(self.facts_json, limit=MAX_BODY_BYTES))
        if self.availability is Availability.OBSERVED and self.position is None:
            raise TimelineContractError("observed snapshot requires an atomic source position")
        if self.availability is not Availability.OBSERVED and self.events:
            raise TimelineContractError("unavailable snapshot cannot retain active events")


@dataclass(frozen=True, slots=True)
class HistoryPage:
    token: SourceToken
    before: SourcePosition
    next_before: SourcePosition | None
    events: tuple[TimelineEvent, ...]
    completeness: Completeness
    availability: Availability = Availability.OBSERVED

    def __post_init__(self) -> None:
        if not isinstance(self.token, SourceToken) or not isinstance(self.completeness, Completeness) or not isinstance(self.availability, Availability):
            raise TimelineContractError("history page requires typed source and qualification")
        for position in (self.before, self.next_before):
            if position is not None and (not isinstance(position, SourcePosition) or not position.matches(self.token.identity)):
                raise TimelineContractError("history cursor does not match its source")
        if not isinstance(self.before, SourcePosition) or self.next_before == self.before:
            raise TimelineContractError("history page requires an advancing before cursor")
        _events(self.events, self.token.identity)
        if self.availability is not Availability.OBSERVED and self.events:
            raise TimelineContractError("unavailable history cannot retain active events")


@dataclass(frozen=True, slots=True)
class LiveDelta:
    token: SourceToken
    base: SourcePosition
    position: SourcePosition
    events: tuple[TimelineEvent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.token, SourceToken):
            raise TimelineContractError("delta requires a typed source token")
        for position in (self.base, self.position):
            if not isinstance(position, SourcePosition) or not position.matches(self.token.identity):
                raise TimelineContractError("delta cursor does not match its source")
        if self.base == self.position:
            raise TimelineContractError("delta must advance its source cursor")
        _events(self.events, self.token.identity)


@dataclass(frozen=True, slots=True)
class ViewportAnchor:
    event: EventRef
    logical_line: int
    screen_row: int

    def __post_init__(self) -> None:
        if not isinstance(self.event, EventRef) or any(type(value) is not int or value < 0 for value in (self.logical_line, self.screen_row)):
            raise TimelineContractError("viewport anchor requires an event and nonnegative line positions")
