"""Owning semantic frames, immutable replay and honest missing-time/authority projection."""

from __future__ import annotations

from copy import deepcopy
import importlib
import json
from types import SimpleNamespace

import pytest

from worktree_manager.companion_bridge_session_codec import SessionCodec
from worktree_manager.companion_timeline_contract import LiveDelta, TimelineContractError
from worktree_manager.companion_timeline_layout import TimelineLayout
from worktree_manager.companion_timeline_model import TimelineWindow
from worktree_manager.companion_worktree_codec import OwnerReadError
from test_agenda_end_to_end import ROOT, SOURCE_PATHS
from test_companion_bridge_codec import OWNER, member


@pytest.fixture
def semantic(monkeypatch):
    for path in reversed([ROOT / "plugins/agent-bridge/src", *SOURCE_PATHS]):
        monkeypatch.syspath_prepend(str(path))
    owner = importlib.import_module("agent_bridge.session_observations")
    wire = importlib.import_module("agent_bridge.session_observation_contract")
    events = importlib.import_module("agent_bridge.events")
    log = events.EventLog()
    native_source = {
        "provider": "bridge", "project": "example", "worktree_id": "child-worktree",
        "head_session_id": None, "session_id": "child", "durable_session_id": None,
        "hosting_generation": "fixture-generation", "registry": "owned", "transport": "local",
    }

    def frame(*, history=False, position=None, limit=64):
        request = wire.ReadRequest(
            wire.Selection("child", native_source["project"], "child-worktree", None),
            history, False, position, limit,
        )
        return owner.frame(owner.KnownSource(log, native_source, native_source["registry"]), request)

    return SimpleNamespace(
        log=log, frame=frame, source=native_source, events=events, owner=owner, wire=wire,
        codec=lambda: SessionCodec("example", "selected", OWNER, member(), "fixture-generation"),
    )


def test_real_semantic_history_and_live_have_independent_positions_and_stable_events(semantic) -> None:
    for index in range(5):
        semantic.log.append("ask_user_request", {"request_id": str(index)})
    codec = semantic.codec()
    first = codec.decode(semantic.frame(limit=2))
    token = codec.token(first, 7)
    window = TimelineWindow()
    assert window.bind(token).applied
    initial = codec.snapshot(first, token)
    assert window.apply_snapshot(initial).applied
    assert [row.event_id for row in window.events] == ["event:4", "event:5"]
    before = initial.before
    assert before is not None
    page = codec.decode(semantic.frame(history=True, position=first["next_before"]), history=True)
    assert window.request_history(token, before).applied
    assert window.prepend_page(codec.page(page, token, before)).applied
    assert window.sources[token.identity.source_key].position == initial.position
    assert [row.event_id for row in window.events] == [f"event:{index}" for index in range(1, 6)]
    # A new viewer/epoch must not give an existing retained event a new observation time.
    replay = semantic.codec()
    same = replay.decode(semantic.frame())
    assert codec.events(first, token) == replay.events(same, token)[-2:]
    event = window.events[-1]
    assert event.actor.qualification.value == "reported"
    assert event.kind.value == "input-request"
    assert "answerability unknown" in event.details
    assert json.loads(event.data_json)["details"]["request_id"] == "4"
    assert token.identity.head_session_id is None and token.identity.worktree_id == "selected"
    assert json.loads(initial.facts_json)["session_source"]["worktree_id"] == "child-worktree"


def test_unknown_native_kind_and_missing_time_are_not_invented_causal_or_input_evidence(semantic) -> None:
    semantic.log.append("unfamiliar_awaiting_operator", {"text": "permission requested"})
    with semantic.log._lock:
        original = semantic.log._events[0]
        semantic.log._events[0] = semantic.events.SseEvent(
            original.id, original.event, original.data, timestamp=float("nan"),
        )
    codec = semantic.codec()
    body = codec.decode(semantic.frame())
    token = codec.token(body, 1)
    event = codec.events(body, token)[0]
    assert event.kind.value == "source-event" and event.observed_at is None and event.occurred_at is None
    window = TimelineWindow()
    assert window.bind(token).applied and window.apply_snapshot(codec.snapshot(body, token)).applied
    layout = TimelineLayout()
    layout.rebuild(window, 80)
    assert layout.blocks[0].heading_text.startswith("--:--  ")
    assert window.toggle_expansion(event.ref).applied
    layout.rebuild(window, 80)
    assert "Time: unknown" in layout.blocks[0].detail_text
    # Unknown time has a deterministic display merge, not a fabricated timestamp.
    assert window.apply_snapshot(codec.snapshot(body, token)).applied


@pytest.mark.parametrize("mutate", [
    lambda body: body.update(schema="bridge-owner-membership-v1"),
    lambda body: body.update(version=True),
    lambda body: body.update(kind="history-page"),
    lambda body: body.update(current_requests="pending"),
    lambda body: body["retained"].update(complete=True),
    lambda body: body["source"].update(head_session_id="head-a"),
    lambda body: body["source"].update(hosting_generation="replacement"),
    lambda body: body["source"].update(worktree_id="foreign"),
    lambda body: body["source"].update(registry="represented"),
    lambda body: body["entries"][0].update(content_sha256="0" * 64),
    lambda body: body["entries"][0].update(answerability="pending"),
    lambda body: body["entries"][0].update(native_event_id=True),
    lambda body: body["entries"].append(deepcopy(body["entries"][0])),
])
def test_invalid_action_binding_hash_or_strengthened_evidence_refuses(semantic, mutate) -> None:
    semantic.log.append("permission_request", {"request_id": "request-a"})
    body = semantic.frame()
    mutate(body)
    with pytest.raises(TimelineContractError):
        semantic.codec().decode(body)


def test_actual_compaction_preserves_unqualified_start_evidence_without_false_warning(semantic) -> None:
    semantic.log.append("compaction_start", {"current_tokens": 28_000, "token_limit": 100_000})
    codec = semantic.codec()
    frame = codec.decode(semantic.frame())
    event = codec.events(frame, codec.token(frame, 1))[0]
    assert event.kind.value == "compaction"
    assert "classification unknown" in event.details
    evidence = json.loads(event.data_json)["details"]["compaction_evidence"]
    assert evidence["pre_usage"]["active_capacity_tokens"] is None
    assert evidence["pre_usage"]["qualification"] == "unknown"


@pytest.mark.parametrize("empty", [False, True])
def test_delta_position_cannot_skip_hashed_or_undelivered_native_events(semantic, empty: bool) -> None:
    for index in range(3):
        semantic.log.append("turn_complete", {"title": str(index)})
    current = semantic.frame()
    before = semantic.wire.position(current["source"], 1)
    body = semantic.frame(position=before, limit=1)
    assert body["entries"][0]["native_event_id"] == 2 and body["has_more"]
    body.update(position=current["position"], has_more=False)
    if empty:
        body["entries"] = []
        body["next_before"] = None
    with pytest.raises(TimelineContractError, match="delivered evidence|delta lost its base"):
        semantic.codec().decode(body)


@pytest.mark.parametrize("kind,empty", [
    ("history", False), ("history", True), ("latest-history", False),
    ("snapshot", False), ("snapshot", True),
])
def test_snapshot_and_history_cannot_omit_retained_tail_or_report_false_exhaustion(
    semantic, kind: str, empty: bool,
) -> None:
    for index in range(5):
        semantic.log.append("turn_complete", {"title": str(index)})
    current = semantic.frame()
    before = semantic.wire.position(current["source"], 4) if kind == "history" else None
    history = kind != "snapshot"
    body = semantic.frame(history=history, position=before)
    if empty:
        body["entries"] = []
    else:
        body["entries"] = body["entries"][:2]
        body["position"] = body["entries"][-1]["id"]
    body.update(next_before=None, has_more=False)
    with pytest.raises(TimelineContractError, match="omitted events before its retained boundary"):
        semantic.codec().decode(body, history=history)


def test_empty_history_at_the_actual_retained_floor_is_valid_partial_evidence(semantic) -> None:
    semantic.log.append("turn_complete", {})
    current = semantic.frame()
    floor = semantic.wire.position(current["source"], current["retained"]["floor"])
    body = semantic.codec().decode(semantic.frame(history=True, position=floor), history=True)
    assert not body["entries"] and body["next_before"] is None and body["coverage"] == "partial"


def test_empty_log_acquires_only_real_first_continuity_then_rebuild_refuses(semantic) -> None:
    codec = semantic.codec()
    empty = codec.decode(semantic.frame())
    assert empty["position"] is None
    with pytest.raises(OwnerReadError, match="no real event continuity"):
        codec.token(empty, 1)
    semantic.log.append("turn_complete", {})
    first = codec.decode(semantic.frame())
    token = codec.token(first, 1)
    assert codec.snapshot(first, token).events[0].event_id == "event:1"
    semantic.log.rebuild([("turn_complete", {})])
    with pytest.raises(OwnerReadError, match="incarnation changed"):
        codec.decode(semantic.frame())


def test_represented_project_is_not_borrowed_and_federated_relationship_is_not_acquired(semantic) -> None:
    selected = member()
    selected.update(registry="represented", member_id="represented:child", project=None,
                    incarnation={"pid": 42, "process_started_at": None, "registered_at": 1.0})
    semantic.source.update(registry="represented", project=None, transport="represented-sdk")
    semantic.log.append("subagent_started", {"agent_id": "helper"})
    codec = SessionCodec("example", "selected", OWNER, selected, "fixture-generation")
    assert codec.decode(semantic.frame())["source"]["project"] is None
    selected.update(registry="relationship", member_id="relationship:relation",
                    relationship_id="relation", uncertain_remote_outcome=True,
                    relationship_qualification="unknown", incarnation={"relationship_id": "relation"})
    with pytest.raises(OwnerReadError, match="locally admitted"):
        SessionCodec("example", "selected", OWNER, selected, "fixture-generation")


@pytest.mark.parametrize("lifecycle,label", [
    ("started", "Reported agent start"),
    ("completed", "Reported agent completion"),
    ("failed", "Reported agent failure"),
])
def test_actual_acp_contribution_labels_reach_readable_literal_rows(
    semantic, lifecycle: str, label: str,
) -> None:
    from agent_bridge.acp_subagents import SubagentAttribution

    attribution = SubagentAttribution(
        semantic.log.append, lambda: True, lambda: "native-session",
    )
    attribution.handle_raw_event({
        "sessionId": "native-session", "type": "subagent." + lifecycle,
        "agentId": "helper", "data": {
            "agentName": "search", "agentDisplayName": "[bold]Compare options[/bold]",
            "agentDescription": "private task description must not be projected",
            "toolCallId": "launch-call",
        },
    })
    codec = semantic.codec()
    frame = codec.decode(semantic.frame())
    event = codec.events(frame, codec.token(frame, 1))[0]
    assert event.summary == label + ": [bold]Compare options[/bold]"
    assert event.kind.value == "contribution-reported"
    assert event.actor.qualification.value == "reported"
    assert "Reported linkage is not custody" in event.details
    raw = json.loads(event.data_json)
    assert raw["native_kind"] == "subagent_" + lifecycle
    assert raw["details"]["agent_name"] == "search"
    assert raw["details"]["parent_tool_call_id"] == "launch-call"
    assert "private task description" not in event.data_json
    assert raw["answerability"] == "unknown" and frame["coverage"] == "partial"
    window = TimelineWindow()
    token = codec.token(frame, 1)
    assert window.bind(token).applied and window.apply_snapshot(codec.snapshot(frame, token)).applied
    layout = TimelineLayout()
    layout.rebuild(window, 120)
    assert event.summary in layout.blocks[0].heading_text
    assert raw["native_kind"] not in layout.blocks[0].heading_text


@pytest.mark.parametrize("kind,details,expected", [
    ("tool_call_start", {"title": "Inspect [literal] changes"}, "Tool started: Inspect [literal] changes"),
    ("tool_call_update", {"status": "completed"}, "Tool completed"),
    ("tool_call_update", {"status": "unfamiliar"}, "Tool update"),
    ("subagent_started", {}, "Reported agent start"),
    ("subagent_started", {"agent_name": "search", "agent_display_name": "  "},
     "Reported agent start: search"),
    ("subagent_started", {"agent_display_name": ["invalid"]}, "Reported agent start"),
    ("unfamiliar_awaiting_operator", {"title": "Permission requested"},
     "Bridge session: unfamiliar_awaiting_operator"),
])
def test_missing_old_or_malformed_labels_do_not_infer_work_or_current_state(
    semantic, kind: str, details: dict, expected: str,
) -> None:
    semantic.log.append(kind, details)
    codec = semantic.codec()
    frame = codec.decode(semantic.frame())
    event = codec.events(frame, codec.token(frame, 1))[0]
    assert event.summary == expected
    assert frame["current_requests"] == "unknown" and frame["coverage"] == "partial"
    assert json.loads(event.data_json)["native_kind"] == kind


def test_bounded_work_label_preserves_full_reported_title_in_evidence(semantic) -> None:
    title = "Inspect " + "\u754c" * 300
    semantic.log.append("tool_call_start", {"title": title})
    codec = semantic.codec()
    frame = codec.decode(semantic.frame())
    event = codec.events(frame, codec.token(frame, 1))[0]
    assert event.summary == "Tool started: " + title[:160] + "..."
    assert json.loads(event.data_json)["details"]["title"] == title


@pytest.mark.parametrize("live", [False, True])
@pytest.mark.parametrize("kind,details,expected", [
    ("tool_call_start", {"title": "Inspect\x00changes"}, "Tool started"),
    ("subagent_started", {"agent_display_name": "Compare\x00options", "agent_name": "search"},
     "Reported agent start: search"),
    ("subagent_started", {"agent_name": "search\x00"}, "Reported agent start"),
])
def test_invalid_prose_labels_fall_back_in_snapshot_and_live_without_losing_evidence(
    semantic, live: bool, kind: str, details: dict, expected: str,
) -> None:
    codec, window = semantic.codec(), TimelineWindow()
    initial = None
    if live:
        semantic.log.append("turn_complete", {})
        initial = codec.decode(semantic.frame())
        token = codec.token(initial, 1)
        assert window.bind(token).applied
        assert window.apply_snapshot(codec.snapshot(initial, token)).applied
    semantic.log.append(kind, details)
    frame = codec.decode(semantic.frame(position=initial["position"] if live else None))
    token = codec.token(frame, 1)
    if live:
        assert frame["kind"] == "delta"
        result = window.apply_delta(LiveDelta(
            token, codec.position(token, frame["base_position"]),
            codec.position(token, frame["position"]), codec.events(frame, token),
        ))
    else:
        assert window.bind(token).applied
        result = window.apply_snapshot(codec.snapshot(frame, token))
    assert result.applied
    event = window.events[-1]
    assert event.summary == expected
    assert json.loads(event.data_json)["details"] == details


def test_invalid_utf8_reported_name_remains_an_explicit_wire_source_error(semantic) -> None:
    semantic.log.append("subagent_started", {"agent_display_name": "Compare\ud800options"})
    with pytest.raises(TimelineContractError, match="invalid UTF-8 text"):
        semantic.codec().decode(semantic.frame())
