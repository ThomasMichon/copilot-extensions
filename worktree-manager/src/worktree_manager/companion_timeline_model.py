"""Bounded timeline display state; source evidence never grants execution authority."""

from __future__ import annotations

from dataclasses import dataclass, replace
from collections import deque
from collections.abc import Iterable
from copy import deepcopy
from enum import StrEnum
from hashlib import sha256
import json

from .companion_timeline_contract import (
    Availability, Completeness, EventRef, HistoryPage, LiveDelta, MAX_EXPANDED_GROUPS,
    MAX_LANES, MAX_WINDOW_BYTES, MAX_WINDOW_EVENTS, Provider, RelationKind, TimelineContractError,
    SourcePosition, SourceToken, TimelineEvent, TimelineGap, TimelineSnapshot,
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
        self.source_gaps: dict[str, TimelineGap] = {}

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
            self.source_gaps.clear()
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
            self.source_gaps.pop(key, None)
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
        keys = self._provider_keys(key)
        if sum(ref.source_key in keys for ref in self._proofs) + additions > min(
            self.max_proofs, MAX_EVENT_PROOFS // len(Provider),
        ):
            return self._integrity_gap(key, "Integrity proof budget exhausted; evidence was not evicted.")
        return None

    def _provider_keys(self, key: str) -> set[str]:
        provider = self.sources[key].token.identity.provider
        return {key for key, state in self.sources.items() if state.token.identity.provider is provider}

    def _integrity_size(self, keys: set[str] | None = None) -> int:
        return sum(
            64 + sum(len(value.encode("utf-8")) for value in (ref.source_key, ref.generation, ref.event_id))
            for ref in self._proofs if keys is None or ref.source_key in keys
        ) + sum(
            64 + sum(len(value.encode("utf-8")) for value in (position.source_key, position.generation, position.cursor))
            for key, values in self._cursors.items() for position in values if keys is None or key in keys
        )

    def _merge_ordered(
        self, loaded: list[TimelineEvent], incoming: tuple[TimelineEvent, ...], *, history: bool,
    ) -> list[TimelineEvent] | WindowResult:
        common = {event.ref for event in loaded} & {event.ref for event in incoming}
        anchors = [event.ref for event in loaded if event.ref in common]
        if anchors != [event.ref for event in incoming if event.ref in common]:
            return self._result(
                False, ResultCode.CONTENT_CONFLICT,
                "Provider order contradicts the loaded order of shared source events.",
            )
        rows: list[TimelineEvent] = []
        old_index = new_index = 0
        # Shared identities anchor both ordered tuples. Unshared loaded rows
        # remain history; IDs and timestamps never establish source order.
        for anchor in anchors:
            old_end, new_end = old_index, new_index
            while loaded[old_end].ref != anchor:
                old_end += 1
            while incoming[new_end].ref != anchor:
                new_end += 1
            old_gap, new_gap = loaded[old_index:old_end], incoming[new_index:new_end]
            rows.extend(new_gap if history else old_gap)
            rows.extend(old_gap if history else new_gap)
            rows.append(incoming[new_end])
            old_index, new_index = old_end + 1, new_end + 1
        rows.extend(incoming[new_index:] if history else loaded[old_index:])
        rows.extend(loaded[old_index:] if history else incoming[new_index:])
        return rows

    def _commit(
        self, events: tuple[TimelineEvent, ...], state: SourceState, *, history: bool = False,
        replace_source: bool = False,
    ) -> WindowResult:
        key = state.token.identity.source_key
        if key in self.reconciliation_required:
            return self._result(False, ResultCode.LIMIT, "Source is awaiting explicit observation reconciliation.")
        old_rows = [event for event in self.events if event.source.source_key == key]
        if history or replace_source:
            ordered = self._merge_ordered(old_rows, events, history=history)
            if isinstance(ordered, WindowResult):
                return ordered
        else:
            loaded = {event.ref for event in old_rows}
            ordered = old_rows + [
                event for event in events if event.ref not in loaded and event.ref not in self._proofs
            ]
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
        # Fixed owner partitions keep the total bound while preventing an
        # exhausted optional owner from consuming another owner's admission.
        keys = self._provider_keys(key)
        if state.position is not None and state.position not in self._cursors.get(state.token.identity.source_key, set()):
            if sum(len(values) for source, values in self._cursors.items() if source in keys) >= MAX_EVENT_PROOFS // len(Provider):
                return self._integrity_gap(key, "Live cursor integrity budget exhausted.")
            integrity_added += 64 + sum(len(value.encode("utf-8")) for value in (
                state.position.source_key, state.position.generation, state.position.cursor,
            ))
        if self._integrity_size(keys) + integrity_added > MAX_WINDOW_BYTES // len(Provider):
            return self._integrity_gap(key, "Integrity evidence byte budget exhausted.")
        fresh = {event.ref for event in events if event.ref not in self._proofs}
        # Source tuples preserve provider order. Across sources, timestamps are
        # a display merge hint, never causal or ownership evidence.
        source_rows: dict[str, deque[TimelineEvent]] = {}
        for event in self.events:
            if event.source.source_key == key:
                continue
            source_rows.setdefault(event.source.source_key, deque()).append(event)
        source_rows[key] = deque(ordered)
        rows: list[TimelineEvent] = []
        while any(source_rows.values()):
            candidates = [((items[0].occurred_at or items[0].observed_at) is None,
                           items[0].occurred_at or items[0].observed_at, source_key)
                          for source_key, items in source_rows.items() if items]
            _, _, source_key = min(candidates)
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
        digest = self._snapshot_digest(snapshot)
        if snapshot.position == state.snapshot_position and state.snapshot_digest is not None:
            if digest != state.snapshot_digest:
                return self._result(False, ResultCode.CONTENT_CONFLICT, "Snapshot content changed at the same position.")
        oldest = next((
            event for event in self.events if event.source.source_key == snapshot.token.identity.source_key
        ), None)
        snapshot_refs = {event.ref for event in snapshot.events}
        before = state.before if oldest is not None and oldest.ref not in snapshot_refs else snapshot.before
        pending = state.requested_before if (
            state.availability is Availability.OBSERVED and state.requested_before == before
        ) else None
        return self._commit(snapshot.events, replace(
            state, position=snapshot.position, before=before, requested_before=pending,
            availability=snapshot.availability, completeness=snapshot.completeness,
            snapshot=replace(snapshot, events=()), snapshot_position=snapshot.position, snapshot_digest=digest,
        ), replace_source=True)

    @staticmethod
    def _snapshot_digest(snapshot: TimelineSnapshot) -> bytes:
        return sha256(json.dumps({
            "purpose": snapshot.purpose, "effort": snapshot.effort_ref,
            "creation": snapshot.creation_context, "facts": snapshot.facts_json,
        }, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).digest()

    def reconciliation_window(self, snapshot: TimelineSnapshot) -> TimelineWindow:
        """Validate an explicit fresh baseline without changing this window or its evidence."""
        key = snapshot.token.identity.source_key
        state = self.sources.get(key)
        if (
            state is None or key not in self.reconciliation_required
            or snapshot.token.identity != state.token.identity
            or snapshot.token.request_epoch <= state.token.request_epoch
        ):
            raise TimelineContractError("reconciliation requires a new request for this exhausted source")
        if (
            snapshot.availability is not Availability.OBSERVED or snapshot.position is None
            or snapshot.as_of is None or snapshot.facts_json is None
            or snapshot.token.identity.owner_ref is None
        ):
            raise TimelineContractError("reconciliation requires fresh owner-qualified observed facts")
        if snapshot.position != state.position and snapshot.position in self._cursors.get(key, set()):
            raise TimelineContractError("reconciliation snapshot returned an older known live position")
        if (
            snapshot.position == state.snapshot_position and state.snapshot_digest is not None
            and self._snapshot_digest(snapshot) != state.snapshot_digest
        ):
            raise TimelineContractError("reconciliation changed facts at the same live position")
        if any(
            event.ref in self._proofs and self._proofs[event.ref] != self._digest(event)
            for event in snapshot.events
        ):
            raise TimelineContractError("reconciliation changed immutable event evidence")
        # Only the exhausted provider moves to a fresh baseline. Independent
        # readers still publish against their exact accepted bindings/cursors.
        candidate = deepcopy(self)
        candidate.sources.pop(key)
        candidate.events = tuple(event for event in self.events if event.source.source_key != key)
        candidate._by_ref = {event.ref: event for event in candidate.events}
        candidate._proofs = {ref: digest for ref, digest in self._proofs.items() if ref.source_key != key}
        candidate._cursors.pop(key, None)
        candidate._lanes = {actor: lane for actor, lane in self._lanes.items() if actor[0] != key}
        candidate.reconciliation_required -= {key}
        candidate.source_gaps.pop(key, None)
        bound = candidate.bind(snapshot.token)
        if not bound.applied:
            raise TimelineContractError("reconciliation source was not admitted: " + bound.reason)
        accepted = candidate.apply_snapshot(snapshot)
        if not accepted.applied:
            raise TimelineContractError("reconciliation baseline was not admitted: " + accepted.reason)
        visible = frozenset(event.ref for event in candidate.events)
        candidate.follow_live = self.follow_live
        candidate.expanded = self.expanded & visible
        candidate.unread = (
            (self.unread | visible.difference(self._proofs)) & visible
            if not self.follow_live else frozenset()
        )
        return candidate

    def apply_gap(self, gap: TimelineGap) -> WindowResult:
        state = self._source(gap.token)
        if state is None or gap.before is not None and gap.before != state.requested_before:
            return self._result(False, ResultCode.STALE_TOKEN, "Gap belongs to an old source or page request.")
        if gap.availability is Availability.HEAD_CHANGED:
            self.apply_snapshot(TimelineSnapshot(
                gap.token, gap.position, Availability.HEAD_CHANGED, gap.completeness,
            ))
            state = self.sources[gap.token.identity.source_key]
        self.source_gaps[gap.token.identity.source_key] = gap
        self.sources[gap.token.identity.source_key] = replace(
            state, completeness=gap.completeness,
            requested_before=None if gap.before is not None else state.requested_before,
        )
        self.revision += 1
        return self._result(False, ResultCode.SOURCE_ERROR, f"History gap: {gap.reason}: {gap.details}")

    def source_error(self, token: SourceToken, message: str) -> WindowResult:
        state = self._source(token)
        if state is None:
            return self._result(False, ResultCode.STALE_TOKEN, "Error belongs to a retired source.")
        self.sources[token.identity.source_key] = replace(
            state, availability=(
                Availability.HEAD_CHANGED if state.availability is Availability.HEAD_CHANGED
                else Availability.UNAVAILABLE
            ), requested_before=None,
        )
        self.revision += 1
        return self._result(False, ResultCode.SOURCE_ERROR, message)

    def pause_source(self, token: SourceToken) -> WindowResult:
        """Retain history without claiming an inactive reader failed or is current."""
        state = self._source(token)
        if state is None:
            return self._result(False, ResultCode.STALE_TOKEN, "Pause belongs to a retired source.")
        self.sources[token.identity.source_key] = replace(
            state, availability=(
                Availability.UNKNOWN if state.availability is Availability.OBSERVED
                else state.availability
            ), requested_before=None,
        )
        self.revision += 1
        return WindowResult(True, ResultCode.APPLIED, "Reader paused; retained evidence is not current.")

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

    def recover_snapshot(
        self, snapshot: TimelineSnapshot, replay: tuple[LiveDelta, ...],
    ) -> WindowResult:
        """Atomically restore an inactive source through a bounded exact replay."""
        state = self._source(snapshot.token)
        if (
            state is None or state.availability not in {Availability.UNAVAILABLE, Availability.UNKNOWN}
            or state.position is None or snapshot.availability is not Availability.OBSERVED
            or not isinstance(replay, tuple) or not 1 <= len(replay) <= 4
            or any(not isinstance(delta, LiveDelta) or delta.token != snapshot.token for delta in replay)
            or replay[0].base != state.position or replay[-1].position != snapshot.position
        ):
            return self._result(False, ResultCode.CURSOR_MISMATCH, "Recovery requires a fresh snapshot and its exact retained-boundary replay.")
        candidate = deepcopy(self)
        key = snapshot.token.identity.source_key
        candidate.sources[key] = replace(state, availability=Availability.OBSERVED)
        for delta in replay:
            result = candidate.apply_delta(delta)
            if not result.applied:
                break
        else:
            result = candidate.apply_snapshot(snapshot)
        if not result.applied:
            if key in candidate.reconciliation_required:
                return self._integrity_gap(key, result.reason)
            return self._result(False, result.code, result.reason)
        self.__dict__.update(candidate.__dict__)
        return result

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
