"""Immutable observer interfaces are independent of actor acceptance or rendering."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
import json

import pytest

from worktree_manager.companion_timeline_contract import (
    ActorKind, Availability, Completeness, EventRef, EvidenceKind, HistoryPage,
    LiveDelta, MAX_FRAME_BYTES, Provider, Qualification, SourceIdentity,
    SourcePosition, SourceToken, TimelineActor, TimelineContractError,
    TimelineEvent, TimelineKind, TimelineSnapshot, canonical_payload,
    check_wire_body, decode_wire_frame, literal_display,
)


def source() -> SourceIdentity:
    return SourceIdentity(Provider.WORKTREES, "owner-observations", "example", "selected", "head", "generation")


def event(number: int = 1) -> TimelineEvent:
    identity = source()
    return TimelineEvent(
        identity, SourcePosition(identity.source_key, identity.generation, str(number)),
        f"event-{number}", TimelineActor("head", ActorKind.SESSION, Qualification.AUTHORITATIVE, "**Main**"),
        TimelineKind.PURPOSE_CHANGED, EvidenceKind.PERSISTED_MUTATION,
        datetime(2026, 11, 2, tzinfo=timezone.utc), "**Inspect** [bold]literal[/bold]",
    )


def test_immutable_source_identity_is_separate_from_request_epoch() -> None:
    first, second = SourceToken(source(), 1), SourceToken(source(), 2)
    assert first != second and first.identity == second.identity
    with pytest.raises(FrozenInstanceError):
        first.request_epoch = 3
    with pytest.raises(TimelineContractError):
        SourceToken(source(), True)
    with pytest.raises(TimelineContractError):
        replace(source(), provider="worktrees")
    with pytest.raises(TimelineContractError):
        replace(source(), project="example\nother")


def test_literal_prose_and_control_escaping_do_not_rewrite_records() -> None:
    observed = replace(event(), details="**literal** [bold]data[/bold]\x1b[2J\nnext")
    assert observed.actor.label == "**Main**"
    assert literal_display(observed.details) == "**literal** [bold]data[/bold]\\x1b[2J\nnext"
    assert "\x1b" in observed.details
    assert observed.encoded_size > 0 and observed.ref == EventRef("owner-observations", "generation", "event-1")
    with pytest.raises(TimelineContractError):
        replace(observed, observed_at=datetime(2026, 11, 2))
    with pytest.raises(TimelineContractError):
        replace(observed, kind="purpose changed")
    with pytest.raises(TimelineContractError):
        replace(observed, source=replace(source(), generation="other"))


def test_json_payload_is_finite_canonical_immutable_text() -> None:
    observed = replace(event(), data_json='{"z":null,"a":[],"control":"\\u0000"}')
    assert observed.data_json == '{"a":[],"control":"\\u0000","z":null}'
    for value in ('{"same":1,"same":2}', '{"number":NaN}', '{"number":1e999}', "not-json"):
        with pytest.raises(TimelineContractError):
            canonical_payload(value)
    with pytest.raises(TimelineContractError):
        canonical_payload('{"text":"\ud800"}')


def test_page_source_duplicate_and_cursor_bounds() -> None:
    token = SourceToken(source(), 0)
    position = SourcePosition("owner-observations", "generation", "2")
    snapshot = TimelineSnapshot(token, position, Availability.OBSERVED, Completeness.PARTIAL, (event(),))
    assert snapshot.position == position
    page = HistoryPage(token, position, None, (event(),), Completeness.COMPLETE)
    assert page.next_before is None
    with pytest.raises(TimelineContractError):
        replace(snapshot, events=(event(), event()))
    with pytest.raises(TimelineContractError):
        replace(snapshot, availability=Availability.UNKNOWN)
    with pytest.raises(TimelineContractError):
        replace(snapshot, position=None)
    with pytest.raises(TimelineContractError):
        replace(page, next_before=position)
    with pytest.raises(TimelineContractError):
        LiveDelta(token, position, position, ())
    with pytest.raises(TimelineContractError):
        replace(page, events=tuple(event(number) for number in range(65)))


def test_wire_budgets_apply_before_enrichment() -> None:
    assert decode_wire_frame(b'{"type":"snapshot","version":1}') == {"type": "snapshot", "version": 1}
    check_wire_body({"data": [1, None, True]})
    for raw in (b'{"same":1,"same":2}', b'{"number":NaN}', b"[]", b"x" * (MAX_FRAME_BYTES + 1)):
        with pytest.raises(TimelineContractError):
            decode_wire_frame(raw)
    with pytest.raises(TimelineContractError):
        check_wire_body({"parts": ["x" * 4096] * 8})
    for value in ({"text": "\ud800"}, {"\ud800": "value"}):
        with pytest.raises(TimelineContractError, match="invalid UTF-8"):
            check_wire_body(value)
    with pytest.raises(TimelineContractError, match="invalid UTF-8"):
        decode_wire_frame(b'{"text":"\\ud800"}')
    assert json.loads(canonical_payload('{"data":true}')) == {"data": True}
