"""Actual owner wire construction through the independent Manager projection."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest

from worktree_manager.companion_timeline_contract import (
    Availability, Completeness, EvidenceKind, TimelineContractError, TimelineGap, TimelineKind,
)
from worktree_manager.companion_timeline_model import TimelineWindow
from worktree_manager.companion_worktree_codec import OwnerReadError, WorktreeCodec
from _worktree_timeline_fixtures import (
    JOURNAL, RESIDENT, cursor, delta, gap, page, raw, snapshot,
)


def codec() -> WorktreeCodec:
    return WorktreeCodec("example", "selected", "head-a", RESIDENT)


def test_actual_owner_snapshot_projects_current_facts_without_created_event() -> None:
    reader = codec()
    value = reader.decode(raw(snapshot()))
    token = reader.token(value, 3)
    observed = reader.snapshot(value, token)
    assert token.identity.generation == JOURNAL and token.identity.owner_ref == RESIDENT
    assert observed.purpose == "Current accepted purpose."
    assert observed.effort_ref == "efforts/active/example/README.md"
    assert observed.creation_context is None and observed.events == ()
    assert observed.completeness is Completeness.PARTIAL
    assert observed.position is not None and observed.position.cursor == cursor(40)


def test_actual_owner_history_events_retain_source_evidence_and_literal_text() -> None:
    reader = codec()
    selected = reader.token(reader.decode(raw(snapshot())), 4)
    before = reader.position(selected, cursor(21))
    result = reader.page(reader.decode(raw(page(before=21))), selected, before)
    assert not isinstance(result, TimelineGap)
    assert result.before == before and result.next_before is None
    assert len(result.events) == 20
    assert all(item.kind is TimelineKind.OBSERVED_CHANGE for item in result.events)
    assert all(item.evidence_kind is EvidenceKind.OBSERVED_CHANGE for item in result.events)
    assert result.events[0].occurred_at is None and result.events[0].data_json is not None


@pytest.mark.parametrize("change", [
    {"contract_version": True}, {"extra": True}, {"worktree_id": "wrong"},
    {"project": "wrong"}, {"head_session_id": "head-b"}, {"source_generation": "opaque-invalid"},
    {"resident_generation": "c" * 32}, {"replay_barrier": "wrong"},
    {"history_scope": "lifetime"}, {"replay_complete": 1},
    {"facts": None}, {"entries": [None]}, {"availability": "Observed"},
])
def test_malformed_or_wrong_selection_is_not_an_empty_snapshot(change: dict) -> None:
    reader = codec()
    value = snapshot()
    value.update(change)
    with pytest.raises((TimelineContractError, OwnerReadError, ValueError)):
        decoded = reader.decode(raw(value))
        reader.snapshot(decoded, reader.token(decoded, 1))


@pytest.mark.parametrize("member", ["creation_nonce", "last_session_id", "id", "repo"])
def test_unknown_incarnation_and_wrong_current_fact_identity_are_rejected(member: str) -> None:
    value = snapshot()
    value["facts"]["row"][member] = None
    with pytest.raises(TimelineContractError):
        codec().decode(raw(value))


def test_source_replacement_and_page_boundary_fences_remain_exact() -> None:
    reader = codec()
    token = reader.token(reader.decode(raw(snapshot())), 1)
    value = page(before=21)
    value["source"] = "current-roster:replacement-record"
    with pytest.raises(OwnerReadError, match="source-replaced"):
        reader.page(reader.decode(raw(value)), token, reader.position(token, cursor(21)))
    with pytest.raises(TimelineContractError, match="boundary"):
        reader.page(reader.decode(raw(page(before=21))), token, reader.position(token, cursor(20)))


def test_record_incarnation_cannot_change_under_an_existing_source_header() -> None:
    reader = codec()
    reader.decode(raw(snapshot()))
    value = delta()
    value["facts"]["row"]["creation_nonce"] = "replacement-record"
    with pytest.raises(OwnerReadError, match="incarnation"):
        reader.decode(raw(value))


def test_history_gap_preserves_floor_reason_and_live_current_position() -> None:
    reader = codec()
    token = reader.token(reader.decode(raw(snapshot())), 7)
    model = TimelineWindow()
    model.bind(token)
    assert model.apply_snapshot(reader.snapshot(reader.decode(raw(snapshot(after=21))), token)).applied
    before = reader.position(token, cursor(22))
    model.request_history(token, before)
    value = gap(22)
    result = reader.page(reader.decode(raw(value)), token, before)
    assert isinstance(result, TimelineGap)
    state = model.sources[token.identity.source_key]
    rows = model.events
    assert not model.apply_gap(result).applied
    assert result.reason == "retention" and result.floor.cursor == cursor(22)
    assert result.reconcile == "snapshot-and-history" and result.before == before
    current = model.sources[token.identity.source_key]
    assert current.position == state.position and current.availability is Availability.OBSERVED
    assert current.requested_before is None and model.events == rows
    assert model.context()[0] == "Current accepted purpose."
    renewed = replace(token, request_epoch=8)
    assert model.bind(renewed).applied
    assert not model.apply_gap(result).applied


def test_same_barrier_replay_slices_do_not_conflict_but_changed_event_content_does() -> None:
    reader = codec()
    value = reader.decode(raw(snapshot(after=21)))
    token = reader.token(value, 1)
    model = TimelineWindow()
    model.bind(token)
    assert model.apply_snapshot(reader.snapshot(value, token)).applied
    assert model.apply_snapshot(reader.snapshot(reader.decode(raw(snapshot(after=40))), token)).applied
    changed = deepcopy(value)
    changed["entries"][0]["summary"] = "Conflicting immutable event"
    result = model.apply_snapshot(reader.snapshot(reader.decode(raw(changed)), token))
    assert not result.applied and result.code.value == "content-conflict"


def test_wire_limits_duplicate_members_boolean_types_and_sampled_event_qualification() -> None:
    reader = codec()
    for data in (b'{"type":"snapshot","type":"delta"}', b"\xff", b" " * (35 * 1024 + 1)):
        with pytest.raises(TimelineContractError):
            reader.decode(data)
    value = delta()
    value["entries"][0]["kind"] = "claim-added"
    parsed = reader.decode(raw(value))
    with pytest.raises(TimelineContractError, match="sampled"):
        reader.events(parsed, reader.token(parsed, 1))
    value = snapshot()
    value["facts"]["row"]["summary"] = "x" * 4097
    with pytest.raises(TimelineContractError):
        reader.decode(raw(value))
    for bad in (2**63, "text\x00"):
        value = snapshot()
        value["facts"]["row"]["untrusted"] = bad
        with pytest.raises(TimelineContractError):
            reader.decode(raw(value))
