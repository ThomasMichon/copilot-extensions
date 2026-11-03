"""Source-owner wire interoperability without importing a sibling into Manager."""

from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import replace
from hashlib import sha256
import importlib
import json
from pathlib import Path

import pytest

from worktree_manager.companion_agenda_codec import AgendaCodec, CAPABILITY
from worktree_manager.companion_timeline_contract import (
    ActorKind, Completeness, Durability, EvidenceKind, Provider, Qualification,
    TimelineContractError, TimelineGap, canonical_payload,
)
from worktree_manager.companion_worktree_codec import OwnerReadError, encoded

WORKTREE = "selected-worktree"
SESSION = "selected-current-head"
HISTORICAL = "historical-operation-session"


def raw(value: dict) -> bytes:
    return encoded(value).encode() + b"\n"


@pytest.fixture
def owner(tmp_path, monkeypatch, request):
    scripts = Path(__file__).resolve().parents[2] / "plugins/efforts/skills/driving-effort-agendas/scripts"
    monkeypatch.syspath_prepend(str(scripts))
    history = importlib.import_module("agenda_history")
    wire = importlib.import_module("agenda_history_contract")
    store = importlib.import_module("agenda_store")
    compact = hasattr(request, "param")
    selected_worktree = "w" if compact else WORKTREE
    operation_session = None if compact else HISTORICAL
    board = {
        "version": 2, "source": {"kind": "task", "ref": "sample-task"}, "revision": 1,
        "items": {"one": {
            "source_ref": "sample/one", "scope_ref": "sample-scope",
            "action": "a" if compact else "Inspect evidence",
            "exit": ["Evidence reported"], "on": {"done": "advance", "blocked": "wait"},
            "relevant_subjects": ["sample"],
        }}, "order": ["one"],
    }
    init_request = {
        "schema": "efforts.agenda-request", "version": 2, "mode": "context-qualified",
        "expected_revision": 0, "operation_id": "i" if compact else "initial-operation", "change": {
            "operation": "init", "worktree": selected_worktree, "board": board,
            "baseline_context": {"facts": [{
                "kind": "input", "subject": "sample", "value": getattr(request, "param", "initial"),
                "evidence_ref": "e" if compact else "sample-input",
            }]},
            "resource_caps": {
                "max_depth": 4, "max_pending_items": 32, "max_deferred_frames": 4,
                "max_no_parent_progress": 3,
            },
        },
    }
    store.mutate(tmp_path, init_request, session=operation_session)

    def wait(detail="Await externally reported evidence"):
        state = store.read(tmp_path)
        cursor = state["documents"]["cursor"]
        frame_id = cursor["frames"][-1]
        item_id = cursor["frame_state"][frame_id]["pending"][0]
        revision = state["revision"]
        return store.mutate(tmp_path, {
            **init_request, "expected_revision": revision, "operation_id": f"accepted-{revision}",
            "change": {
                "operation": "wait", "frame_id": frame_id, "item_id": item_id,
                "reason_kind": "external", "reason_ref": "sample-event", "next_action": detail,
                "evidence": [{
                    "criterion": "Waiting for evidence", "kind": "measured",
                    "ref": "sample-proof", "inputs": "sample revision",
                }],
            },
        }, session=operation_session)[0]

    selected = history.reader_info(tmp_path, selected_worktree)
    codec = AgendaCodec("sample-project", selected_worktree, SESSION, selected["source"]["store_id"])

    def page(**kwargs):
        return history.history_page(tmp_path, selected_worktree, codec.store_id, **kwargs)

    return codec, selected, page, wait, wire, history, tmp_path


def test_actual_reader_snapshot_retains_accepted_identity_not_current_session(owner):
    codec, selected, page, wait, *_ = owner
    accepted = wait()
    assert codec.info(raw(selected))["capabilities"] == [CAPABILITY]
    frame = codec.decode(raw(page()))
    token = codec.token(7)
    snapshot = codec.snapshot(frame, token)
    assert token.identity.provider is Provider.AGENDA
    assert token.identity.generation == codec.store_id
    assert token.identity.head_session_id == SESSION
    assert codec.revision(snapshot.position.cursor) == 2 and snapshot.position.cursor == frame["cursor"]
    assert snapshot.before is None
    assert snapshot.completeness is Completeness.PARTIAL
    event = snapshot.events[-1]
    assert event.source.head_session_id == SESSION and event.actor.actor_id == HISTORICAL
    assert event.actor.kind is ActorKind.SESSION and event.actor.qualification is Qualification.REPORTED
    assert event.durability is Durability.COMMITTED and event.evidence_kind is EvidenceKind.RETAINED_EVENT
    assert event.event_id == frame["entries"][-1]["event_id"]
    assert codec.revision(event.position.cursor) == accepted["revision"]
    assert event.occurred_at.isoformat() == accepted["committed_at"].replace("Z", "+00:00")
    assert event.observed_at == event.occurred_at
    assert snapshot.facts_json is None and snapshot.creation_context is None
    detail = json.loads(event.data_json)
    assert detail["result"] == accepted and detail["actor"]["qualification"] == "caller-asserted"


def test_real_owner_paging_preserves_revision_boundaries_and_initialization_floor(owner):
    codec, _, page, wait, *_ = owner
    for _ in range(4):
        wait()
    token = codec.token(0)
    latest = codec.decode(raw(page(limit=2)))
    snapshot = codec.snapshot(latest, token)
    assert [codec.revision(event.position.cursor) for event in snapshot.events] == [4, 5]
    assert codec.revision(snapshot.before.cursor) == 4
    middle = codec.page(codec.decode(raw(page(before_cursor=snapshot.before.cursor, limit=2))),
                        token, snapshot.before)
    assert [codec.revision(event.position.cursor) for event in middle.events] == [2, 3]
    assert codec.revision(middle.next_before.cursor) == 2
    oldest = codec.page(codec.decode(raw(page(before_cursor=middle.next_before.cursor))),
                        token, middle.next_before)
    assert [codec.revision(event.position.cursor) for event in oldest.events] == [1]
    assert oldest.next_before is None
    empty_before = codec.position(oldest.events[0].position.cursor)
    empty = codec.page(codec.decode(raw(page(before_cursor=empty_before.cursor))), token, empty_before)
    assert empty.events == () and empty.next_before is None
    floor = codec.position(latest["history_floor"])
    gap = codec.page(codec.decode(raw(page(before_cursor=floor.cursor))), token, floor)
    assert isinstance(gap, TimelineGap) and codec.revision(gap.floor.cursor) == 0
    assert gap.before == floor and gap.completeness is Completeness.PARTIAL
    assert json.loads(gap.source_ref_json)["owner_reconcile"] == "reader-info-and-history"


def test_same_immutable_operation_does_not_change_with_later_page_head_time(owner):
    codec, _, page, wait, *_ = owner
    token = codec.token(0)
    original = codec.events(codec.decode(raw(page())), token)[0]
    wait()
    later = codec.events(codec.decode(raw(page())), token)[0]
    assert later.content() == original.content()
    assert later.observed_at == original.occurred_at


def test_detail_over_display_bound_has_truthful_manager_omission(owner):
    codec, _, page, wait, *_ = owner
    accepted = wait("漢" * 2500)
    frame = codec.decode(raw(page(limit=1)))
    assert frame["entries"][0]["result"] == accepted
    snapshot = codec.snapshot(frame, codec.token(0))
    event = snapshot.events[0]
    detail = json.loads(event.data_json)
    assert detail["schema"] == "worktree-manager.agenda-detail-omission"
    native = encoded(frame["entries"][0]).encode()
    assert detail["encoded_bytes"] == len(native) and detail["sha256"] == sha256(native).hexdigest()
    assert detail["owner_omission"] is None
    assert len(event.data_json.encode()) <= 4096
    assert "omitted from display" in event.details


def nested_value(depth):
    value = {"leaf": [0, 1.25, True, False, None, "literal\n\ttext"]}
    for _ in range(depth):
        value = {"child": value}
    return value


@pytest.mark.parametrize(("owner", "display_limit"), [
    pytest.param([0] * 128, None, id="bounded-array"),
    pytest.param([0] * 129, "array bound", id="oversized-array"),
    pytest.param(nested_value(2), None, id="bounded-nesting-and-json-values"),
    pytest.param(nested_value(17), "nesting bound", id="deep-owner-value"),
], indirect=["owner"])
def test_actual_owner_structural_display_limits_preserve_valid_source_and_stable_evidence(
    owner, display_limit,
):
    codec, _, page, wait, *_ = owner
    token = codec.token(0)
    frame = codec.decode(raw(page()))
    item = frame["entries"][0]
    assert item["result"] is not None and item["omission"] is None
    candidate = encoded(item)
    assert len(candidate.encode()) <= 4096
    event = codec.events(frame, token)[0]
    assert canonical_payload(event.data_json) == event.data_json
    if display_limit is None:
        assert json.loads(event.data_json) == item
        assert "Detail omitted" not in event.details
    else:
        with pytest.raises(TimelineContractError, match=display_limit):
            canonical_payload(candidate)
        detail = json.loads(event.data_json)
        assert detail["schema"] == "worktree-manager.agenda-detail-omission"
        assert display_limit in detail["display_limit"]
        assert detail["sha256"] == sha256(candidate.encode()).hexdigest()
        assert detail["encoded_bytes"] == len(candidate.encode())
        assert detail["event_id"] == item["event_id"] and detail["cursor"] == item["cursor"]
        assert detail["owner_omission"] is None
        assert "Timeline payload limits" in event.details
    wait()
    later = codec.events(codec.decode(raw(page())), token)[0]
    assert later.content() == event.content()
    malformed = deepcopy(frame)
    malformed["entries"][0]["result"]["persisted"] = False
    with pytest.raises(TimelineContractError, match="persisted operation identity"):
        codec.events(malformed, token)


@pytest.mark.parametrize(("owner", "expected"), [
    pytest.param(2**80, 2**80, id="large-positive-domain-integer"),
    pytest.param(-(2**80), -(2**80), id="large-negative-domain-integer"),
], indirect=["owner"])
def test_actual_owner_domain_integers_remain_exact_in_display(owner, expected):
    codec, _, page, *_ = owner
    frame = codec.decode(raw(page()))
    event = codec.snapshot(frame, codec.token(0)).events[0]
    detail = json.loads(event.data_json)
    assert detail == frame["entries"][0]
    contexts = [record["body"] for record in detail["result"]["records"] if record["kind"] == "context"]
    values = [fact["value"] for context in contexts for fact in context["facts"]]
    assert values == [expected] and type(values[0]) is int
    assert "Detail omitted" not in event.details


@pytest.mark.parametrize("invalid", [True, False, -1, 1 << 63, 2**80, -(2**80)])
def test_domain_integer_support_does_not_relax_protocol_revision_bounds(owner, invalid):
    codec, selected, page, *_ = owner
    info = deepcopy(selected)
    info["revision"] = invalid
    with pytest.raises(TimelineContractError, match="invalid agenda integer"):
        codec.info(raw(info))
    frame = page()
    cursor = frame["cursor"]
    value = json.loads(base64.urlsafe_b64decode(cursor[4:] + "=" * (-len(cursor[4:]) % 4)))
    value["revision"] = invalid
    bad_cursor = "ag1." + base64.urlsafe_b64encode(encoded(value).encode()).decode().rstrip("=")
    with pytest.raises(TimelineContractError, match="invalid agenda integer"):
        codec.position(bad_cursor)
    frame["cursor"] = bad_cursor
    with pytest.raises(TimelineContractError, match="invalid agenda integer"):
        codec.decode(raw(frame))


def test_actual_owner_omission_retains_original_payload_integrity_and_reference(owner):
    codec, _, page, wait, *_ = owner
    accepted = wait("漢" * 10000)
    frame = codec.decode(raw(page(limit=1)))
    item = frame["entries"][0]
    assert item["result"] is None
    event = codec.snapshot(frame, codec.token(0)).events[0]
    detail = json.loads(event.data_json)
    assert detail["omission"] == item["omission"]
    assert detail["omission"]["sha256"] == sha256(encoded(accepted).encode()).hexdigest()
    assert detail["omission"]["payload_ref"]["operation_id"] == accepted["operation_id"]
    assert "Owner omitted" in event.details


def test_legacy_reader_support_is_visible_but_never_admitted_as_rich(owner):
    codec, selected, page, _, wire, *_ = owner
    legacy = deepcopy(selected)
    legacy["source"]["store_version"] = 1
    source = wire.Source(WORKTREE, codec.store_id, 1)
    legacy.update(cursor=wire.position(source, 1), capabilities=[], availability="observed-only",
                  reason="legacy store has no context-qualified rich records")
    assert codec.info(raw(legacy))["availability"] == "observed-only"
    frame = page()
    frame["source"] = legacy["source"]
    with pytest.raises(OwnerReadError, match="unsupported"):
        codec.decode(raw(frame))


@pytest.mark.parametrize("fault", [
    "version-bool", "source", "store", "store-version-bool", "scope", "completeness",
    "floor", "event-floor", "event-identity", "operation", "actor-qualification", "actor-control",
    "result-version-bool", "result-revision-bool", "result-persisted", "nested-operation",
    "result-source", "result-actor", "records-type", "record-body", "record-id", "record-metadata",
    "tail", "discontinuity", "duplicate-event", "has-more", "has-more-bool", "next-before", "head-time",
])
def test_malformed_owner_contract_is_rejected_before_projection(owner, fault):
    codec, _, page, wait, *_ = owner
    wait()
    value = page()
    item = value["entries"][-1]
    result = item["result"]
    if fault == "version-bool":
        value["contract_version"] = True
    elif fault == "source":
        value["source"]["worktree"] = "other-worktree"
    elif fault == "store":
        value["source"]["store_id"] = "a" * 32
    elif fault == "store-version-bool":
        value["source"]["store_version"] = True
    elif fault == "scope":
        value["history_scope"] = "all-execution"
    elif fault == "completeness":
        value["completeness"] = "complete"
    elif fault == "floor":
        value["history_floor"] = value["cursor"]
    elif fault == "event-floor":
        item["cursor"] = value["history_floor"]
    elif fault == "event-identity":
        item["event_id"] = "line-2"
    elif fault == "operation":
        item["operation"] = "execute"
    elif fault == "actor-qualification":
        item["actor"]["qualification"] = "authoritative"
    elif fault == "actor-control":
        item["actor"]["session"] = "head\ninjected"
    elif fault == "result-version-bool":
        result["version"] = True
    elif fault == "result-revision-bool":
        result["revision"] = True
    elif fault == "result-persisted":
        result["persisted"] = 1
    elif fault == "nested-operation":
        result["operation"] = {"operation": "wait"}
    elif fault == "result-source":
        result["source"]["session"] = SESSION
    elif fault == "result-actor":
        result["actor"]["actor"] = "invented"
    elif fault == "records-type":
        result["records"] = {}
    elif fault == "record-body":
        result["records"][0]["body"] = []
    elif fault == "record-id":
        result["record_ids"][0] = "different-record"
    elif fault == "record-metadata":
        result["records"][0]["created_at"] = "2020-01-01T00:00:00Z"
    elif fault == "tail":
        value["entries"].pop()
    elif fault == "discontinuity":
        value["entries"].reverse()
    elif fault == "duplicate-event":
        value["entries"][0] = deepcopy(item)
    elif fault == "has-more":
        value["has_more"] = True
    elif fault == "has-more-bool":
        value["has_more"] = 0
    elif fault == "next-before":
        value["next_before"] = value["cursor"]
    else:
        value["committed_at"] = "2020-01-01T00:00:00Z"
    with pytest.raises(TimelineContractError):
        codec.decode(raw(value))


@pytest.mark.parametrize("fault", ["bytes-bool", "digest", "reference", "revision-bool", "both", "neither"])
def test_omitted_payload_evidence_is_not_fabricated_or_silently_discarded(owner, fault):
    codec, _, page, wait, *_ = owner
    wait("漢" * 10000)
    value = page(limit=1)
    item = value["entries"][0]
    if fault == "bytes-bool":
        item["omission"]["utf8_bytes"] = True
    elif fault == "digest":
        item["omission"]["sha256"] = "not-a-digest"
    elif fault == "reference":
        item["omission"]["payload_ref"]["operation_id"] = "different-operation"
    elif fault == "revision-bool":
        item["omission"]["payload_ref"]["revision"] = True
    elif fault == "both":
        item["result"] = {}
    else:
        item["omission"] = None
    with pytest.raises(TimelineContractError):
        codec.decode(raw(value))


@pytest.mark.parametrize("fault", ["padding", "foreign", "bool", "extra", "namespace", "noncanonical"])
def test_position_requires_the_canonical_selected_owner_cursor(owner, fault):
    codec, _, page, _, wire, *_ = owner
    cursor = page()["cursor"]
    if fault == "padding":
        cursor += "="
    elif fault == "foreign":
        cursor = wire.position(wire.Source("another-worktree", codec.store_id, 2), 1)
    else:
        value = json.loads(base64.urlsafe_b64decode(cursor[4:] + "=" * (-len(cursor[4:]) % 4)))
        if fault == "bool":
            value["revision"] = True
        elif fault == "extra":
            value["extra"] = "no"
        elif fault == "namespace":
            value["namespace"] = "0" * 64
        body = json.dumps(value).encode() if fault == "noncanonical" else encoded(value).encode()
        cursor = "ag1." + base64.urlsafe_b64encode(body).decode().rstrip("=")
    with pytest.raises(TimelineContractError):
        codec.position(cursor)


def test_wrong_boundary_token_and_historical_snapshot_cannot_be_promoted(owner):
    codec, _, page, wait, *_ = owner
    wait()
    wait()
    token = codec.token(0)
    current = codec.decode(raw(page(limit=2)))
    before = codec.position(current["next_before"])
    assert before.cursor != current["cursor"]
    old = codec.decode(raw(page(before_cursor=before.cursor)))
    assert codec.page(old, token, before).events == codec.events(old, token)
    with pytest.raises(TimelineContractError):
        codec.snapshot(old, token)
    with pytest.raises(TimelineContractError):
        codec.page(old, token, codec.position(current["cursor"]))
    with pytest.raises(TimelineContractError):
        codec.page(old, token, replace(before, source_key="foreign-agenda"))
    with pytest.raises(TimelineContractError):
        codec.page(old, token, replace(before, generation="foreign-store"))
    with pytest.raises(TimelineContractError):
        codec.page(old, token, None)
    wrong = replace(token, identity=replace(token.identity, head_session_id=HISTORICAL))
    with pytest.raises(TimelineContractError):
        codec.snapshot(current, wrong)
    other = AgendaCodec("another-project", WORKTREE, SESSION, codec.store_id)
    assert other.token(0).identity.source_key != token.identity.source_key


@pytest.mark.parametrize("fault", ["duplicate", "nul", "control", "nonfinite", "overflow-float", "oversize", "body"])
def test_strict_json_and_owner_byte_bounds(owner, fault):
    codec, _, page, *_ = owner
    value = page()
    if fault == "duplicate":
        data = raw(value).replace(b'"type":"history-page"', b'"type":"gap","type":"history-page"')
    elif fault in {"nul", "control"}:
        value["entries"][0]["result"]["transition"]["display"] = "\x00" if fault == "nul" else "\x1b"
        data = raw(value)
    elif fault in {"nonfinite", "overflow-float"}:
        data = raw(value).replace(b'"revision":1', b'"revision":NaN' if fault == "nonfinite" else b'"revision":1e999')
    elif fault == "oversize":
        data = b" " * (35 * 1024 + 1)
    else:
        value["entries"][0]["result"]["transition"]["display"] = ""
        body = {key: item for key, item in value.items()
                if key not in {"schema", "contract_version", "type", "source", "cursor"}}
        filler = 32769 - (len(encoded(body).encode()) + 1)
        value["entries"][0]["result"]["transition"]["display"] = "x" * filler
        data = raw(value)
        assert len(data) < 35 * 1024
    with pytest.raises(TimelineContractError):
        codec.decode(data)


def test_exact_owner_error_is_visible_and_version_checked(owner):
    codec, *_ = owner
    value = {
        "schema": "efforts.agenda-history", "contract_version": 1, "type": "error",
        "error": {"code": "source-replaced", "message": "Selected store was replaced"},
    }
    with pytest.raises(OwnerReadError) as failure:
        codec.decode(raw(value))
    assert failure.value.code == "source-replaced"
    value["contract_version"] = True
    with pytest.raises(TimelineContractError):
        codec.info(raw(value))
