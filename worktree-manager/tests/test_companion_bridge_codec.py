"""Membership evidence remains partial, source-qualified and non-authorizing."""

from __future__ import annotations

import base64
from copy import deepcopy
import json

import pytest

from worktree_manager.companion_bridge_codec import MembershipCodec, selected_owner
from worktree_manager.companion_timeline_contract import TimelineContractError
from worktree_manager.companion_timeline_model import TimelineWindow
from worktree_manager.companion_worktree_codec import OwnerReadError

OWNER = "fixture-host/example/selected"
SOURCE = {
    "provider": "bridge", "owner_ref": OWNER, "project": "example", "worktree_id": "selected",
    "head_session_id": None, "hosting_generation": "fixture-generation",
    "journal_incarnation": "fixture-generation", "hosting_authority": "fixture-authority",
}
AT = "2026-11-03T08:00:00+00:00"


def cursor(number: int, source: dict | None = None) -> str:
    raw = json.dumps({
        "version": 1, "source": source or SOURCE, "event_id": number,
    }, ensure_ascii=True, separators=(",", ":")).encode()
    return "abso1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")


def member(state: str = "starting") -> dict:
    return {
        "member_id": "owned:child", "owner_ref": OWNER, "registry": "owned",
        "bridge_session_id": "child", "durable_session_id": None, "project": "example",
        "worktree_id": "child-worktree", "head_session_id": None, "current_answerability": "unknown",
        "liveness": "unknown", "freshness": "unknown", "link_qualification": "reported",
        "retirement": None, "state": state, "transport": "local",
        "incarnation": {"created_at": 1.0}, "admission_phase": "unknown",
    }


def snapshot(number: int = 0) -> dict:
    return {
        "schema": "bridge-owner-membership-v1", "version": 1, "kind": "snapshot", "source": SOURCE,
        "availability": "observed", "completeness": "partial", "durability": "unknown",
        "head_session_id": None, "observed_at": AT,
        "retained": {"floor": 1, "head": number, "complete": False},
        "base_position": None, "position": cursor(number), "members": [member()], "changes": [],
        "has_more": False, "current_answerability": "unknown",
        "limitations": ["partial-bootstrap", "canonical-head-unknown"],
    }


def delta(number: int = 1, *, before: int = 0, state: str = "idle") -> dict:
    frame = snapshot(number)
    frame.update(kind="delta", base_position=cursor(before), members=[], changes=[{
        "kind": "member-upsert", "operation": "upsert", "member": member(state),
        "evidence_kind": "observed-change", "grants_claim_release": False,
        "observed_at": AT, "id": cursor(number),
    }])
    return frame


def raw(value: dict) -> bytes:
    return json.dumps(value, separators=(",", ":")).encode()


def admitted() -> tuple[MembershipCodec, object, TimelineWindow]:
    codec = MembershipCodec("example", "selected", OWNER)
    frame = codec.decode(raw(snapshot()))
    token = codec.token(frame, 1)
    window = TimelineWindow()
    assert window.bind(token).applied
    assert window.apply_snapshot(codec.apply(frame, token)).applied
    return codec, token, window


def test_snapshot_is_current_partial_membership_not_a_fabricated_history_event() -> None:
    codec, token, window = admitted()
    assert not window.events and token.identity.head_session_id is None
    state = window.sources[token.identity.source_key]
    assert json.loads(state.snapshot.facts_json)["members"] == [member()]
    frame = codec.decode(raw(delta()))
    assert window.apply_delta(codec.delta(frame, token)).applied
    assert window.apply_snapshot(codec.apply(frame, token)).applied
    event = window.events[0]
    assert event.kind.value == "bridge-member-changed"
    assert event.actor.qualification.value == "reported" and event.actor.reported_state == "idle"
    assert event.evidence_kind.value == "observed-change"
    assert event.relations[0].qualification.value == "reported"
    assert "no claim release" in event.details


@pytest.mark.parametrize("kind", [
    "record-disappeared", "registration-disappeared", "session-retired",
    "confirmed-relationship-retired", "registration-expired", "local-admission-retired",
])
def test_tombstones_retain_exact_native_reason_without_releasing_claims(kind: str) -> None:
    codec, token, window = admitted()
    frame = delta()
    frame["changes"][0].update(kind=kind, operation="tombstone")
    frame = codec.decode(raw(frame))
    assert window.apply_delta(codec.delta(frame, token)).applied
    assert window.apply_snapshot(codec.apply(frame, token)).applied
    assert not json.loads(window.sources[token.identity.source_key].snapshot.facts_json)["members"]
    assert json.loads(window.events[0].data_json)["kind"] == kind
    assert json.loads(window.events[0].data_json)["grants_claim_release"] is False


@pytest.mark.parametrize("mutation", [
    lambda frame: frame.update(version=True),
    lambda frame: frame.update(completeness="complete"),
    lambda frame: frame.update(durability="committed"),
    lambda frame: frame.update(head_session_id="head-a"),
    lambda frame: frame.update(current_answerability="pending"),
    lambda frame: frame["members"][0].update(liveness="alive"),
    lambda frame: frame["members"][0].update(link_qualification="host-confirmed"),
    lambda frame: frame["members"][0]["incarnation"].update(private_token="not-publishable"),
    lambda frame: frame["members"].append(deepcopy(frame["members"][0])),
    lambda frame: frame.update(position=cursor(0, {**SOURCE, "hosting_authority": "other"})),
    lambda frame: frame["source"].update(owner_ref="fixture-host/example/other"),
])
def test_invalid_or_strengthened_evidence_is_rejected_before_publication(mutation) -> None:
    frame = deepcopy(snapshot())
    mutation(frame)
    with pytest.raises(TimelineContractError):
        MembershipCodec("example", "selected", OWNER).decode(raw(frame))


@pytest.mark.parametrize("field", [
    "registry", "link_qualification", "retirement", "relationship_qualification",
])
@pytest.mark.parametrize("value", [[], {}, True])
def test_malformed_member_qualifications_are_contract_errors(field: str, value: object) -> None:
    frame = snapshot()
    row = frame["members"][0]
    if field == "relationship_qualification":
        row.update(
            registry="relationship", member_id="relationship:relation",
            relationship_id="relation", uncertain_remote_outcome=False,
            incarnation={"relationship_id": "relation"},
        )
    row[field] = value
    with pytest.raises(TimelineContractError):
        MembershipCodec("example", "selected", OWNER).decode(raw(frame))


@pytest.mark.parametrize("field", ["kind", "operation"])
def test_unhashable_frame_and_change_kinds_are_contract_errors(field: str) -> None:
    frame = delta()
    (frame if field == "kind" else frame["changes"][0])[field] = []
    with pytest.raises(TimelineContractError):
        MembershipCodec("example", "selected", OWNER).decode(raw(frame))


def test_null_relationship_identity_is_a_contract_error() -> None:
    frame = snapshot()
    frame["members"][0].update(
        registry="relationship", member_id="relationship:relation", relationship_id=None,
        uncertain_remote_outcome=False, relationship_qualification="unknown",
        incarnation={"relationship_id": None},
    )
    with pytest.raises(TimelineContractError, match="relationship_id"):
        MembershipCodec("example", "selected", OWNER).decode(raw(frame))


def test_conflicting_native_identity_is_not_hidden_by_a_new_display_epoch() -> None:
    codec, token, window = admitted()
    frame = codec.decode(raw(delta()))
    assert window.apply_delta(codec.delta(frame, token)).applied
    changed = delta(state="running")
    changed = codec.decode(raw(changed))
    assert not window.apply_snapshot(codec.apply(changed, token)).applied or (
        not window.apply_delta(codec.delta(changed, token)).applied
    )
    assert window.events[0].actor.reported_state == "idle"


def test_foreign_selection_and_replacement_are_not_silently_retargeted() -> None:
    assert selected_owner(OWNER + "#head-a", "example", "selected") == OWNER
    for owner in (None, "fixture-host/example/other", "fixture-host/other/selected"):
        with pytest.raises((OwnerReadError, TimelineContractError)):
            selected_owner(owner, "example", "selected")
    codec, _token, _window = admitted()
    frame = deepcopy(snapshot())
    frame["source"].update(hosting_generation="replacement", journal_incarnation="replacement")
    frame["position"] = cursor(0, frame["source"])
    with pytest.raises(OwnerReadError, match="source-replaced"):
        codec.decode(raw(frame))


def named_member(name: str) -> dict:
    return {
        **member("s"), "member_id": "owned:" + name, "bridge_session_id": name,
        "project": None, "worktree_id": None, "transport": "x",
    }


def replay_page(names: list[str], before: int, *, tombstones: bool, more: bool) -> dict:
    frame = delta(before + len(names), before=before)
    frame.update(has_more=more, changes=[{
        **frame["changes"][0], "member": named_member(name), "id": cursor(before + index + 1),
        "operation": "tombstone" if tombstones else "upsert",
    } for index, name in enumerate(names)])
    return frame


def test_paginated_full_replacement_keeps_transient_members_out_of_current_facts() -> None:
    codec = MembershipCodec("example", "selected", OWNER)
    initial = snapshot()
    initial["members"] = [named_member(f"z{i}") for i in range(64)]
    initial = codec.decode(raw(initial))
    token = codec.token(initial, 1)
    codec.apply(initial, token)
    window = TimelineWindow()
    window.bind(token)
    window.apply_snapshot(codec.apply(initial, token))
    before = 0
    for tombstones in (False, True):
        names = [f"{'z' if tombstones else 'a'}{i}" for i in range(64)]
        for start in range(0, 64, 16):
            more = not tombstones or start != 48
            frame = codec.decode(raw(replay_page(names[start:start + 16], before, tombstones=tombstones, more=more)))
            baseline = codec.apply(frame, token)
            assert window.apply_delta(codec.delta(frame, token)).applied
            assert window.apply_snapshot(baseline).applied
            facts = json.loads(baseline.facts_json)
            if more:
                assert not facts["members"] and "membership-replay-incomplete" in facts["limitations"]
            before += 16
    assert len(window.events) == 128
    assert set(codec.members) == {f"owned:a{i}" for i in range(64)}
    assert len(json.loads(baseline.facts_json)["members"]) == 64
    assert not codec.replaying


def test_completed_replay_still_enforces_current_bound_atomically() -> None:
    codec = MembershipCodec("example", "selected", OWNER)
    initial = snapshot()
    initial["members"] = [named_member(f"z{i}") for i in range(64)]
    token = codec.token(initial, 1)
    codec.apply(initial, token)
    with pytest.raises(TimelineContractError, match="admitted bound"):
        codec.apply(replay_page(["a"], 0, tombstones=False, more=False), token)
    assert len(codec.members) == 64 and "owned:a" not in codec.members
