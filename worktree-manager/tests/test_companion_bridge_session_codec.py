"""Owning semantic frames, immutable replay and honest missing-time/authority projection."""

from __future__ import annotations

from copy import deepcopy
import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest

from worktree_manager.companion_bridge_session_codec import SessionCodec
from worktree_manager.companion_timeline_contract import LiveDelta, TimelineContractError, TimelineKind, ViewportAnchor
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

    def frame(*, history=False, position=None, limit=64, tool_statistics=False):
        request = wire.ReadRequest(
            wire.Selection("child", native_source["project"], "child-worktree", None),
            history, False, position, limit, tool_statistics=tool_statistics,
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
    frame = codec.decode(semantic.frame())
    assert frame["source"]["project"] is None
    event, = codec.events(frame, codec.token(frame, 1))
    assert event.actor.reported_state is None
    assert "Current execution and concurrency unknown" in event.details
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


def test_actual_reported_agents_have_distinct_stable_lanes_without_inferred_parentage(semantic) -> None:
    from agent_bridge.acp_subagents import SubagentAttribution

    attribution = SubagentAttribution(
        semantic.log.append, lambda: True, lambda: "native-session",
    )
    semantic.log.append("tool_call_start", {"tool_call_id": "root-call", "title": "Plan work"})
    for agent in ("child", "helper-b"):
        attribution.handle_raw_event({
            "sessionId": "native-session", "type": "subagent.started", "agentId": agent,
            "data": {"agentDisplayName": "Compare options", "toolCallId": "launch-" + agent},
        })
    attribution.handle_raw_event({
        "sessionId": "native-session", "type": "tool.execution_start", "agentId": "child",
        "data": {"toolCallId": "child-call"},
    })
    semantic.log.append("tool_call_start", {
        "tool_call_id": "child-call", "title": "Inspect changes",
        **attribution.tool_call_fields(SimpleNamespace(tool_call_id="child-call", field_meta=None)),
    })
    codec, window = semantic.codec(), TimelineWindow()
    frame = codec.decode(semantic.frame())
    token = codec.token(frame, 1)
    snapshot = codec.snapshot(frame, token)
    root, first, second, tool = snapshot.events
    assert root.actor.actor_id == "bridge-session:child"
    assert first.actor.actor_id == "subagent:child"
    assert second.actor.actor_id == "subagent:helper-b"
    assert first.actor.label == second.actor.label == "Compare options"
    assert all(event.actor.kind.value == "subagent" for event in (first, second, tool))
    assert all(event.actor.qualification.value == "reported" for event in snapshot.events)
    assert all(event.actor.reported_state is None for event in snapshot.events)
    assert first.relations == second.relations == tool.relations == ()
    assert json.loads(first.data_json)["details"]["parent_tool_call_id"] == "launch-child"
    assert window.bind(token).applied and window.apply_snapshot(snapshot).applied
    assert len({window.lane(event) for event in (root, first, second)}) == 3
    assert window.lane(tool) == window.lane(first)
    layout = TimelineLayout()
    layout.rebuild(window, 120)
    rows = [layout.row(block.start, window) for block in layout.blocks[:3]]
    assert all(row is not None for row in rows)
    assert len({layout.prefix(row, window) for row in rows}) == 3
    replay = semantic.codec()
    same = replay.decode(semantic.frame())
    assert replay.events(same, token) == snapshot.events
    lanes = {event.ref: window.lane(event) for event in snapshot.events}
    attribution.handle_raw_event({
        "sessionId": "native-session", "type": "subagent.completed", "agentId": "child", "data": {},
    })
    live = codec.decode(semantic.frame(position=frame["position"]))
    completed, = codec.events(live, token)
    assert completed.actor.actor_id == first.actor.actor_id
    assert completed.actor.reported_state is None and not completed.relations
    assert window.apply_delta(LiveDelta(
        token, codec.position(token, live["base_position"]),
        codec.position(token, live["position"]), (completed,),
    )).applied
    assert window.lane(completed) == window.lane(first)
    assert {event.ref: window.lane(event) for event in snapshot.events} == lanes


def test_actual_acp_activity_reports_do_not_become_current_concurrency(semantic) -> None:
    from acp.schema import ToolCallProgress, ToolCallStart
    from agent_bridge.acp_client import AcpClient

    client = AcpClient(on_event=semantic.log.append)
    client._acp_session_id = "native-session"
    for agent in ("helper-a", "helper-b"):
        client._subagent_attr.handle_raw_event({
            "sessionId": "native-session", "type": "subagent.started", "agentId": agent,
            "data": {"agentDisplayName": "Inspect source", "toolCallId": "launch-" + agent},
        })
        client._handle_session_update(ToolCallStart(
            session_update="tool_call", tool_call_id="tool-" + agent, title="Read source",
            field_meta={"github.com/copilot": {"agentId": agent}},
        ))
    client._handle_session_update(ToolCallProgress(
        session_update="tool_call_update", tool_call_id="tool-helper-a", status="completed",
    ))
    client._subagent_attr.handle_raw_event({
        "sessionId": "native-session", "type": "subagent.completed", "agentId": "helper-a",
        "data": {},
    })
    assert semantic.log.active_tool_call()["tool_call_id"] == "tool-helper-b"

    codec, window = semantic.codec(), TimelineWindow()
    frame = codec.decode(semantic.frame(tool_statistics=True), tool_statistics=True)
    token = codec.token(frame, 1)
    snapshot = codec.snapshot(frame, token)
    assert frame["tool_statistics"]["start_occurrences"] == 2
    assert frame["coverage"] == "partial" and frame["retained"]["complete"] is False
    assert [event.kind.value for event in snapshot.events].count("contribution-reported") == 3
    assert all(event.actor.reported_state is None for event in snapshot.events)
    assert all(not event.relations for event in snapshot.events)
    assert window.bind(token).applied and window.apply_snapshot(snapshot).applied
    layout = TimelineLayout()
    for event in snapshot.events:
        assert window.toggle_expansion(event.ref).applied
    layout.rebuild(window, 100)
    assert all(
        "Current execution and concurrency unknown" in block.detail_text
        for block in layout.blocks
    )
    assert snapshot.events[-1].summary == "Reported agent completion"
    assert len({window.lane(event) for event in snapshot.events}) == 2


@pytest.mark.parametrize("kind,details", [
    ("subagent_started", {"agent_display_name": "A name without identity"}),
    ("subagent_completed", {"agent_id": ""}),
    ("subagent_failed", {"agent_id": "bad\x00identity"}),
    ("tool_call_start", {"agent_id": "bad\nidentity"}),
    ("tool_call_update", {"agent_id": "\u754c" * 512}),
    ("unknown_kind", {"agent_id": "helper", "agent_display_name": "Do not infer actor type"}),
])
def test_absent_invalid_or_unqualified_actor_metadata_stays_with_reporter(
    semantic, kind: str, details: dict,
) -> None:
    semantic.log.append(kind, details)
    codec = semantic.codec()
    frame = codec.decode(semantic.frame())
    event = codec.events(frame, codec.token(frame, 1))[0]
    assert event.actor.kind.value == "bridge-session"
    assert event.actor.actor_id == "bridge-session:child"
    assert event.actor.label == "child" and event.actor.reported_state is None
    assert frame["coverage"] == "partial" and frame["current_requests"] == "unknown"
    assert "Current execution and concurrency unknown" in event.details


@pytest.mark.parametrize("size", [(100, 32), (48, 24)])
def test_actual_output_burst_widget_history_is_sparse_bounded_and_inspectable(semantic, size) -> None:
    from worktree_manager.companion_timeline_widget import SubwayTimeline
    from test_companion_timeline_widget import TimelineApp

    semantic.log.append("tool_call_start", {"tool_call_id": "work", "title": "Inspect source"})
    for n in range(40):
        semantic.log.append("agent_message" if n % 2 else "agent_thought", {"text": "omitted output"})
    semantic.log.append("turn_complete", {})
    codec, window = semantic.codec(), TimelineWindow()
    frame = codec.decode(semantic.frame())
    source = codec.token(frame, 1)
    observed = codec.snapshot(frame, source)
    assert len(observed.events) < 42 and observed.before is not None
    assert window.bind(source).applied and window.apply_snapshot(observed).applied
    original = tuple(row.content() for row in observed.events)

    async def check() -> None:
        app = TimelineApp(window)
        async with app.run_test(size=size) as pilot:
            timeline = app.query_one(SubwayTimeline)
            timeline.focus()
            await pilot.pause()
            assert len(timeline.layout_index.blocks) == 2
            group, complete = timeline.layout_index.blocks
            assert len(group.members) == len(observed.events) - 1
            assert complete.event.summary == "Turn completed"
            assert timeline.selected == complete.event.ref
            assert not app.requests and not timeline.allow_vertical_scroll
            await pilot.press("up")
            await pilot.pause()
            assert len(app.requests) == 1 and not window.follow_live
            anchor = timeline.capture_anchor()
            assert anchor is not None and timeline._selected_group
            history = codec.decode(semantic.frame(history=True, position=frame["next_before"]), history=True)
            page = codec.page(history, source, observed.before)
            timeline.set_demand_visible(False)
            assert not timeline.prepend_page(page).applied
            timeline.set_demand_visible(True)
            await pilot.pause()
            assert len(app.requests) == 1
            await pilot.press("up")
            await pilot.pause()
            assert len(app.requests) == 2
            anchor = timeline.capture_anchor()
            assert anchor is not None
            assert timeline.prepend_page(page).applied
            await pilot.pause()
            assert len(window.events) == 42 and len(timeline.layout_index.blocks) == 3
            assert window.events[0].summary == "Tool started: Inspect source"
            assert len(timeline.layout_index.blocks[1].members) == 40
            assert tuple(window.event(row.ref).content() for row in observed.events) == original
            assert window.sources[source.identity.source_key].position == observed.position
            row = timeline.layout_index.anchor_row(anchor)
            assert row is not None
            assert row + timeline._padding - int(timeline.scroll_offset.y) == anchor.screen_row
            assert timeline.selected == window.events[1].ref and timeline._selected_group
            await pilot.press("enter")
            await pilot.pause()
            assert len(timeline.layout_index.blocks) == 43
            assert not window.expanded
            member = window.events[4]
            start = timeline.layout_index.anchor_row(ViewportAnchor(member.ref, 0, 0))
            assert start is not None
            timeline.scroll_to(y=start + timeline._padding, animate=False, immediate=True)
            await pilot.pause()
            assert timeline.selected == member.ref and not timeline._selected_group
            await pilot.press("enter")
            await pilot.pause()
            assert member.ref in window.expanded
            assert member.position.cursor in timeline.layout_index._refs[member.ref].detail_text
            assert "content_sha256" in timeline.layout_index._refs[member.ref].detail_text
            await pilot.press("home")
            await pilot.pause()
            group = timeline.layout_index._groups[member.ref]
            screen_row = group.start + timeline._padding - int(timeline.scroll_offset.y)
            assert await pilot.click(timeline, offset=(1, screen_row))
            await pilot.pause()
            assert len(timeline.layout_index.blocks) == 3
            assert timeline._selected_group and timeline.selected == window.events[1].ref
            assert timeline._padding < max(0, timeline.size.height - timeline.layout_index.height)
            await pilot.press("end")
            await pilot.pause()
            assert window.follow_live and not window.unread
            assert timeline._padding == max(0, timeline.size.height - timeline.layout_index.height)
            last = timeline.layout_index.row(timeline.layout_index.height - 1, window)
            assert last is not None
            assert timeline.layout_index.height - 1 + timeline._padding - int(timeline.scroll_offset.y) == timeline.size.height - 1
            assert timeline.selected == last.event
            group = timeline.layout_index._groups[member.ref]
            screen_row = group.start + timeline._padding - int(timeline.scroll_offset.y)
            assert await pilot.click(timeline, offset=(timeline.layout_index.prefix_width + 1, screen_row))
            await pilot.pause()
            assert timeline._selected_group and not window.follow_live
            await pilot.resize_terminal(60, 26)
            assert timeline.has_focus and timeline._selected_group
            semantic.log.append("agent_message", {"text": "another omitted output"})
            live = codec.decode(semantic.frame(position=frame["position"]))
            assert timeline.apply_delta(LiveDelta(
                source, codec.position(source, live["base_position"]),
                codec.position(source, live["position"]), codec.events(live, source),
            )).applied
            await pilot.pause()
            assert len(timeline.layout_index.blocks) == 4 and len(app.requests) == 2
            assert len(window.events) == 43 and len(window._proofs) == 43
            assert timeline.has_focus and timeline._selected_group and not window.follow_live
            assert not timeline.source_error(source, "source disconnected").applied
            assert len(timeline.layout_index.blocks) == 4 and len(app.requests) == 2
            assert frame["coverage"] == "partial" and frame["current_requests"] == "unknown"

    asyncio.run(check())


def test_actual_output_groups_respect_actor_work_and_unknown_diagnostic_boundaries(semantic, monkeypatch) -> None:
    for kind, details in (
        ("agent_message", {"agent_id": "a", "agent_display_name": "Shared name"}),
        ("agent_thought", {"agent_id": "a", "agent_display_name": "Shared name"}),
        ("agent_message", {"agent_id": "b", "agent_display_name": "Shared name"}),
        ("tool_call_start", {"agent_id": "b", "title": "agent_message"}),
        ("agent_message", {"agent_id": "b", "agent_display_name": "Shared name"}),
        ("future_output", {"title": "agent_message", "agent_id": "b"}),
        ("agent_message", {}),
        ("ask_user_request", {"request_id": "input"}),
        ("permission_request", {"request_id": "permission"}),
        ("compaction_start", {}),
        ("agent_thought", {}),
    ):
        semantic.log.append(kind, details)
    codec, window = semantic.codec(), TimelineWindow()
    frame = codec.decode(semantic.frame())
    source = codec.token(frame, 1)
    snapshot = codec.snapshot(frame, source)
    assert window.bind(source).applied and window.apply_snapshot(snapshot).applied
    raw = tuple(row.content() for row in window.events)
    proofs = dict(window._proofs)
    layout = TimelineLayout()

    def forbidden(*args, **kwargs):
        raise AssertionError("presentation attempted JSON parsing or source I/O")

    with monkeypatch.context() as patch:
        patch.setattr("json.loads", forbidden)
        patch.setattr("builtins.open", forbidden)
        patch.setattr("os.scandir", forbidden)
        layout.rebuild(window, 80)
        for n in range(layout.height):
            assert layout.row(n, window) is not None
    groups = [block for block in layout.blocks if block.members]
    assert [len(block.members) for block in groups] == [2, 1, 1, 1, 1]
    assert groups[0].event.actor.actor_id == "subagent:a"
    assert groups[1].event.actor.actor_id == groups[2].event.actor.actor_id == "subagent:b"
    assert groups[0].event.actor.label == groups[1].event.actor.label == "Shared name"
    assert window.lane(groups[0].event) != window.lane(groups[1].event)
    separate = [block.event for block in layout.blocks if not block.members]
    assert [row.kind for row in separate] == [
        TimelineKind.SOURCE_EVENT, TimelineKind.SOURCE_EVENT, TimelineKind.INPUT_REQUEST,
        TimelineKind.PERMISSION_REQUEST, TimelineKind.COMPACTION,
    ]
    assert separate[1].summary == "Bridge session: future_output"
    assert tuple(row.content() for row in window.events) == raw and window._proofs == proofs
    assert all(row.actor.reported_state is None for row in window.events)
