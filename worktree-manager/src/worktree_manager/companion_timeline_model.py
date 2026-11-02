"""Bounded timeline display state; source evidence never grants execution authority."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections import deque
from collections.abc import Iterable
from enum import StrEnum
from hashlib import sha256
import json

from .companion_timeline_contract import (
    Availability, Completeness, EventRef, HistoryPage, LiveDelta, MAX_EXPANDED_GROUPS,
    MAX_LANES, MAX_WINDOW_BYTES, MAX_WINDOW_EVENTS, Provider, RelationKind,
    SourcePosition, SourceToken, TimelineEvent, TimelineSnapshot,
)

MAX_EVENT_PROOFS = MAX_WINDOW_EVENTS * 4
MAX_SOURCE_SCOPES = MAX_LANES


class ResultCode(StrEnum):
    APPLIED = "applied"
    STALE_TOKEN = "stale-token"
    SOURCE_ERROR = "source-error"
    CURSOR_MISMATCH = "cursor-mismatch"
    CONTENT_CONFLICT = "content-conflict"
    LIMIT = "limit"
    UNKNOWN_EVENT = "unknown-event"


@dataclass(frozen=True, slots=True)
class WindowResult:
    applied: bool
    code: ResultCode
    reason: str
    evicted: int = 0


@dataclass(frozen=True, slots=True)
class SourceState:
    token: SourceToken
    position: SourcePosition | None = None
    before: SourcePosition | None = None
    requested_before: SourcePosition | None = None
    availability: Availability = Availability.UNKNOWN
    completeness: Completeness = Completeness.UNKNOWN
    snapshot: TimelineSnapshot | None = None
    snapshot_position: SourcePosition | None = None
    snapshot_digest: bytes | None = None


class TimelineWindow:
    """One observer window, with explicit binding and immutable event fingerprints."""

    def __init__(
        self, *, max_events: int = MAX_WINDOW_EVENTS, max_bytes: int = MAX_WINDOW_BYTES,
        max_proofs: int = MAX_EVENT_PROOFS,
    ) -> None:
        if type(max_events) is not int or type(max_bytes) is not int or not 1 <= max_events <= MAX_WINDOW_EVENTS or not 1 <= max_bytes <= MAX_WINDOW_BYTES:
            raise ValueError("timeline window limits exceed their declared bounds")
        if type(max_proofs) is not int or not 1 <= max_proofs <= MAX_EVENT_PROOFS:
            raise ValueError("timeline integrity proof limit exceeds its bounded budget")
        self.max_events, self.max_bytes, self.max_proofs = max_events, max_bytes, max_proofs
        self.sources: dict[str, SourceState] = {}
        self.events: tuple[TimelineEvent, ...] = ()
        self.expanded: frozenset[EventRef] = frozenset()
        self.unread: frozenset[EventRef] = frozenset()
        self.follow_live = True
        self.revision = 0
        self.evicted = 0
        self.last_result = WindowResult(True, ResultCode.APPLIED, "No source bound.")
        self._proofs: dict[EventRef, bytes] = {}
        self._cursors: dict[str, set[SourcePosition]] = {}
        self._lanes: dict[tuple[str, str | None], int] = {}
        self._by_ref: dict[EventRef, TimelineEvent] = {}
        self._scope: tuple[str, str] | None = None
        self._head: str | None = None
        self._anchor: EventRef | None = None
        self.reconciliation_required: frozenset[str] = frozenset()

    def _result(
        self, applied: bool, code: ResultCode, reason: str, *, evicted: int = 0,
    ) -> WindowResult:
        self.last_result = WindowResult(applied, code, reason, evicted)
        return self.last_result

    def _integrity_gap(self, key: str, reason: str) -> WindowResult:
        self.reconciliation_required |= {key}
        state = self.sources[key]
        self.sources[key] = replace(
            state, availability=Availability.UNAVAILABLE, requested_before=None,
        )
        self.revision += 1
        return self._result(
            False, ResultCode.LIMIT,
            reason + " Last observed rows retained. Reconcile from a fresh owner-qualified "
            "snapshot in a new observer window; renewing this epoch cannot erase evidence.",
        )

    def bind(self, token: SourceToken) -> WindowResult:
        key = token.identity.source_key
        old = self.sources.get(key)
        scope = token.identity.project, token.identity.worktree_id
        if self._scope is not None and scope != self._scope:
            return self._result(False, ResultCode.STALE_TOKEN, "Source belongs to a different selected worktree.")
        if old is not None and token.request_epoch < old.token.request_epoch:
            return self._result(False, ResultCode.STALE_TOKEN, "Source request epoch regressed.")
        if old is not None and key in self.reconciliation_required and old.token.identity == token.identity:
            return self._result(False, ResultCode.LIMIT, "Fresh observation baseline required; same-source epoch renewal preserves exhausted evidence.")
        head = token.identity.head_session_id
        if self._head is not None and head is not None and head != self._head:
            if old is None:
                return self._result(False, ResultCode.STALE_TOKEN, "Additional source belongs to a different head.")
            self.sources.clear()
            self.events = ()
            self.expanded = self.unread = frozenset()
            self._proofs.clear()
            self._cursors.clear()
            self._lanes.clear()
            self.reconciliation_required = frozenset()
            old = None
        self._scope = scope
        if head is not None:
            self._head = head
        if old is None and len(self.sources) >= MAX_SOURCE_SCOPES:
            return self._result(False, ResultCode.LIMIT, "Source scope budget exhausted.")
        if old is not None and old.token.identity != token.identity:
            refs = {event.ref for event in self.events if event.source.source_key == key}
            self.events = tuple(event for event in self.events if event.ref not in refs)
            self.expanded -= refs
            self.unread -= refs
            self._proofs = {ref: value for ref, value in self._proofs.items() if ref.source_key != key}
            self._cursors.pop(key, None)
            self._lanes = {actor: lane for actor, lane in self._lanes.items() if actor[0] != key}
            self.reconciliation_required -= {key}
            old = None
        self.sources[key] = replace(old, token=token, requested_before=None) if old else SourceState(token)
        self._by_ref = {event.ref: event for event in self.events}
        self.revision += 1
        return self._result(True, ResultCode.APPLIED, "Source request bound; prior epochs are fenced.")

    def _source(self, token: SourceToken) -> SourceState | None:
        state = self.sources.get(token.identity.source_key)
        return state if state is not None and state.token == token else None

    @staticmethod
    def _digest(event: TimelineEvent) -> bytes:
        return sha256(json.dumps(
            event.content(), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).digest()

    def _check(self, events: tuple[TimelineEvent, ...], key: str) -> WindowResult | None:
        additions = 0
        for event in events:
            old = self._proofs.get(event.ref)
            if old is not None and old != self._digest(event):
                return self._result(False, ResultCode.CONTENT_CONFLICT, "An immutable event changed content.")
            additions += old is None
        if len(self._proofs) + additions > self.max_proofs:
            return self._integrity_gap(key, "Integrity proof budget exhausted; evidence was not evicted.")
        return None

    def _integrity_size(self) -> int:
        return sum(
            64 + sum(len(value.encode("utf-8")) for value in (ref.source_key, ref.generation, ref.event_id))
            for ref in self._proofs
        ) + sum(
            64 + sum(len(value.encode("utf-8")) for value in (position.source_key, position.generation, position.cursor))
            for values in self._cursors.values() for position in values
        )

    def _commit(
        self, events: tuple[TimelineEvent, ...], state: SourceState, *, history: bool = False,
        replace_source: bool = False,
    ) -> WindowResult:
        key = state.token.identity.source_key
        if key in self.reconciliation_required:
            return self._result(False, ResultCode.LIMIT, "Source is awaiting explicit observation reconciliation.")
        rejected = self._check(events, key)
        if rejected is not None:
            return rejected
        if any(event.encoded_size > self.max_bytes for event in events):
            return self._result(False, ResultCode.LIMIT, "An event exceeds the display-window byte budget.")
        integrity_added = sum(
            64 + sum(len(value.encode("utf-8")) for value in (
                event.ref.source_key, event.ref.generation, event.ref.event_id,
            )) for event in events if event.ref not in self._proofs
        )
        if state.position is not None and state.position not in self._cursors.get(state.token.identity.source_key, set()):
            if sum(len(values) for values in self._cursors.values()) >= MAX_EVENT_PROOFS:
                return self._integrity_gap(key, "Live cursor integrity budget exhausted.")
            integrity_added += 64 + sum(len(value.encode("utf-8")) for value in (
                state.position.source_key, state.position.generation, state.position.cursor,
            ))
        if self._integrity_size() + integrity_added > MAX_WINDOW_BYTES:
            return self._integrity_gap(key, "Integrity evidence byte budget exhausted.")
        existing = {event.ref: event for event in self.events}
        fresh = {event.ref for event in events if event.ref not in self._proofs}
        for event in events:
            existing[event.ref] = event
        # Source tuples preserve provider order. Across sources, timestamps are
        # a display merge hint, never causal or ownership evidence.
        source_rows: dict[str, deque[TimelineEvent]] = {}
        for event in existing.values():
            source_rows.setdefault(event.source.source_key, deque()).append(event)
        old_rows = [event for event in self.events if event.source.source_key == key]
        loaded = {event.ref for event in old_rows}
        additions = [
            event for event in events if event.ref not in loaded
            and (history or replace_source or event.ref not in self._proofs)
        ]
        source_rows[key] = deque(additions + old_rows if history else old_rows + additions)
        rows: list[TimelineEvent] = []
        while any(source_rows.values()):
            candidates = [(items[0].occurred_at or items[0].observed_at, source_key)
                          for source_key, items in source_rows.items() if items]
            _, source_key = min(candidates)
            rows.append(source_rows[source_key].popleft())
        removed = 0
        size = sum(event.encoded_size for event in rows)
        while len(rows) > self.max_events or size > self.max_bytes:
            protected = {self._anchor}
            if history and self.events:
                protected.add(self.events[-1].ref)
            choices = [
                index for index, event in enumerate(rows) if event.ref not in protected
            ]
            if not choices:
                return self._result(False, ResultCode.LIMIT, "Window cannot retain its visible anchor within budget.")
            index = choices[-1] if history else choices[0]
            evicted = rows.pop(index)
            size -= evicted.encoded_size
            removed += 1
        for event in events:
            self._proofs[event.ref] = self._digest(event)
            actor_key = (event.source.source_key, event.actor.actor_id)
            if actor_key not in self._lanes and len(self._lanes) < MAX_LANES - 1:
                occupied = set(self._lanes.values())
                self._lanes[actor_key] = next(
                    lane for lane in range(MAX_LANES - 1) if lane not in occupied
                )
        self.sources[key] = state
        if state.position is not None:
            self._cursors.setdefault(key, set()).add(state.position)
        self.events = tuple(rows)
        self._by_ref = {event.ref: event for event in rows}
        visible = frozenset(self._by_ref)
        self.expanded &= visible
        self.unread &= visible
        if not self.follow_live and not history:
            self.unread |= fresh & visible
        self.evicted += removed
        self.revision += 1
        return self._result(True, ResultCode.APPLIED, "History window changed." if history else "Publication applied.", evicted=removed)

    def apply_snapshot(self, snapshot: TimelineSnapshot) -> WindowResult:
        state = self._source(snapshot.token)
        if state is None:
            return self._result(False, ResultCode.STALE_TOKEN, "Snapshot has a stale epoch, head or source identity.")
        if snapshot.availability is not Availability.OBSERVED:
            if snapshot.availability is Availability.HEAD_CHANGED:
                self.events = ()
                self.sources = {
                    key: replace(value, availability=Availability.HEAD_CHANGED, requested_before=None)
                    for key, value in self.sources.items()
                }
                state = self.sources[snapshot.token.identity.source_key]
            else:
                self.events = tuple(event for event in self.events if event.source != snapshot.token.identity)
            self._by_ref = {event.ref: event for event in self.events}
            self.expanded &= frozenset(self._by_ref)
            self.unread &= frozenset(self._by_ref)
            self.sources[snapshot.token.identity.source_key] = replace(
                state, availability=snapshot.availability, completeness=snapshot.completeness, requested_before=None,
            )
            self.revision += 1
            return self._result(True, ResultCode.APPLIED, f"Source availability: {snapshot.availability.value}.")
        if snapshot.position != state.position and snapshot.position in self._cursors.get(snapshot.token.identity.source_key, set()):
            return self._result(False, ResultCode.CURSOR_MISMATCH, "Snapshot returned an older known live position.")
        digest = sha256(json.dumps({
            "events": [event.content() for event in snapshot.events],
            "purpose": snapshot.purpose, "effort": snapshot.effort_ref,
            "creation": snapshot.creation_context, "facts": snapshot.facts_json,
        }, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).digest()
        if snapshot.position == state.snapshot_position and state.snapshot_digest is not None:
            if digest != state.snapshot_digest:
                return self._result(False, ResultCode.CONTENT_CONFLICT, "Snapshot content changed at the same position.")
        return self._commit(snapshot.events, replace(
            state, position=snapshot.position,
            before=state.before if state.snapshot is not None and any(
                event.source.source_key == snapshot.token.identity.source_key for event in self.events
            ) else snapshot.before, requested_before=None,
            availability=snapshot.availability, completeness=snapshot.completeness,
            snapshot=replace(snapshot, events=()), snapshot_position=snapshot.position, snapshot_digest=digest,
        ), replace_source=True)

    def apply_delta(self, delta: LiveDelta) -> WindowResult:
        state = self._source(delta.token)
        if state is None:
            return self._result(False, ResultCode.STALE_TOKEN, "Delta belongs to an old request/source/head.")
        if delta.token.identity.source_key in self.reconciliation_required:
            return self._result(False, ResultCode.LIMIT, "Live source paused pending a fresh observation baseline.")
        if state.availability is not Availability.OBSERVED or state.position != delta.base:
            return self._result(False, ResultCode.CURSOR_MISMATCH, "Delta does not continue the current observed position.")
        if delta.position in self._cursors.get(delta.token.identity.source_key, set()):
            return self._result(False, ResultCode.CURSOR_MISMATCH, "Delta returned a previously consumed cursor.")
        return self._commit(delta.events, replace(state, position=delta.position))

    def request_history(self, token: SourceToken, before: SourcePosition) -> WindowResult:
        state = self._source(token)
        if token.identity.source_key in self.reconciliation_required:
            return self._result(False, ResultCode.LIMIT, "History paused until explicit observation reconciliation.")
        if state is None or state.before != before:
            return self._result(False, ResultCode.STALE_TOKEN, "History demand does not match the loaded boundary.")
        if state.requested_before is not None:
            return self._result(False, ResultCode.CURSOR_MISMATCH, "A history page is already pending.")
        self.sources[token.identity.source_key] = replace(state, requested_before=before)
        return self._result(True, ResultCode.APPLIED, "One bounded history page requested.")

    def prepend_page(self, page: HistoryPage) -> WindowResult:
        state = self._source(page.token)
        if state is None or state.requested_before != page.before or state.before != page.before:
            return self._result(False, ResultCode.STALE_TOKEN, "History page belongs to an old request or boundary.")
        if page.availability is not Availability.OBSERVED:
            self.sources[page.token.identity.source_key] = replace(state, requested_before=None)
            return self._result(False, ResultCode.SOURCE_ERROR, f"History is {page.availability.value}; existing rows retained.")
        return self._commit(page.events, replace(
            state, before=page.next_before, requested_before=None, completeness=page.completeness,
        ), history=True)

    def lane(self, event: TimelineEvent) -> int:
        return self._lanes.get((event.source.source_key, event.actor.actor_id), MAX_LANES - 1)

    @property
    def lane_count(self) -> int:
        return max((self.lane(event) + 1 for event in self.events), default=1)

    @property
    def encoded_size(self) -> int:
        return sum(event.encoded_size for event in self.events)

    def event(self, ref: EventRef) -> TimelineEvent | None:
        return self._by_ref.get(ref)

    def children(self, ref: EventRef) -> tuple[TimelineEvent, ...]:
        return tuple(event for event in self.events if any(
            relation.kind is RelationKind.PARENT and relation.target == ref for relation in event.relations
        ))

    def toggle_expansion(self, ref: EventRef) -> WindowResult:
        if ref not in self._by_ref:
            return self._result(False, ResultCode.UNKNOWN_EVENT, "Expansion target is outside the loaded window.")
        if ref in self.expanded:
            self.expanded -= {ref}
        elif len(self.expanded) >= MAX_EXPANDED_GROUPS:
            return self._result(False, ResultCode.LIMIT, "Expanded detail-group budget exhausted.")
        else:
            self.expanded |= {ref}
        self.revision += 1
        return self._result(True, ResultCode.APPLIED, "Detail group toggled; relationships remain qualified.")

    def set_follow_live(self, value: bool) -> None:
        self.follow_live = value
        if value:
            self.unread = frozenset()

    def pin_viewport(self, ref: EventRef | None) -> None:
        self._anchor = ref

    def context(self) -> tuple[str | None, str | None, str | None]:
        states = sorted(self.sources.values(), key=lambda state: state.token.identity.provider is not Provider.WORKTREES)
        snapshots = [
            state.snapshot for state in states
            if state.availability is Availability.OBSERVED and state.snapshot is not None
        ]
        def first(values: Iterable[str | None]) -> str | None:
            return next((value for value in values if value), None)

        return (
            first(snapshot.purpose for snapshot in snapshots),
            first(snapshot.effort_ref for snapshot in snapshots),
            first(snapshot.creation_context for snapshot in snapshots),
        )
