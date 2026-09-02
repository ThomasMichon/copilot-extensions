"""Bounded delegated-result snapshot tests."""

from __future__ import annotations

import argparse
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agent_bridge import __main__ as cli
from agent_bridge.app import create_app
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.events import EventLog
from agent_bridge.models import ServiceConfig, SessionStatus
from agent_bridge.protocol import (
    REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION,
    RESULT_SNAPSHOT_PROTOCOL_VERSION,
)
from agent_bridge.result_snapshot import _event_ref, _turn_ref
from agent_bridge.routes import sessions as session_routes
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget
from agent_bridge.worktree_head import HeadInfo


@pytest.fixture(autouse=True)
def _isolate_local_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv(
        "AGENT_WORKTREES_PROJECTS_YAML",
        str(tmp_path / "nonexistent-projects.yaml"),
    )


@pytest.fixture
def app(tmp_path):
    cfg = ServiceConfig(port=0, bind="127.0.0.1", db_path=str(tmp_path / "test.db"))
    return create_app(config=cfg, token="test-token")


@pytest.fixture
def client(app):
    with TestClient(app) as value:
        value.headers["Authorization"] = "Bearer test-token"
        yield value


def _seed_session(app, sid: str = "sess-1") -> tuple[SessionManager, Session]:
    mgr: SessionManager = app.state.session_manager
    target = SpawnTarget(type="local", cwd="/wt", worktree_id="wt-1")
    session = Session(sid, "calm-lake", target, "test-agent")
    session.status = SessionStatus.IDLE
    session.event_log = EventLog(db=mgr.db, session_id=sid, worktree_id="wt-1")
    mgr._sessions[sid] = session
    mgr.db.create_session(
        sid, "calm-lake", "test-agent", "/wt", "local", "idle", time.time()
    )
    return mgr, session


def _complete_turn(
    mgr: SessionManager,
    session: Session,
    *,
    response: str,
    stop_reason: str = "end_turn",
) -> None:
    now = time.time()
    mgr.db.create_turn(session.session_id, 0, "prompt", now)
    mgr.db.update_turn(
        session.session_id,
        0,
        response_text=response,
        stop_reason=stop_reason,
        completed_at=now + 1,
    )
    session.turn_count = 1


def _register_live(client, session_id: str = "live-1", worktree_id: str = "wt-live"):
    response = client.post(
        "/api/v1/live-sessions",
        json={
            "session_id": session_id,
            "machine": "host",
            "cwd": "/wt",
            "worktree_id": worktree_id,
            "repo": "example/repo",
            "branch": "main",
            "pid": 123,
        },
    )
    assert response.status_code == 200


def _ingest_live(client, *events, session_id: str = "live-1"):
    response = client.post(
        f"/api/v1/live-sessions/{session_id}/events",
        json={"events": list(events)},
    )
    assert response.status_code == 200


def test_owned_snapshot_recovers_latest_result_and_position(client, app) -> None:
    mgr, session = _seed_session(app)
    _complete_turn(mgr, session, response="finished work")
    session.event_log.append("agent_message", {"text": "finished work"})
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})

    response = client.get("/api/v1/sessions/sess-1/result")

    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["logical_delegate_id"] == "wt-1"
    assert body["latest_result"]["availability"] == "available"
    assert body["latest_result"]["value"]["text"] == "finished work"
    assert body["incremental"]["position"].startswith("abr1.")
    assert [item["kind"] for item in body["incremental"]["items"]] == [
        "assistant_message",
        "turn_complete",
    ]


def test_owned_snapshot_recovers_latest_result_after_log_reload(client, app) -> None:
    mgr, session = _seed_session(app)
    _complete_turn(mgr, session, response="persisted result")
    session.event_log.append("agent_message", {"text": "persisted result"})
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})
    mgr.db.flush()
    session.event_log = EventLog.from_db(
        mgr.db, session.session_id, worktree_id="wt-1"
    )
    session.client = None

    body = client.get("/api/v1/sessions/sess-1/result").json()

    assert body["latest_result"]["availability"] == "available"
    assert body["latest_result"]["value"]["text"] == "persisted result"
    assert body["incremental"]["position"]


def test_snapshot_before_first_event_has_no_position(client, app) -> None:
    _seed_session(app)

    body = client.get("/api/v1/sessions/sess-1/result").json()

    assert body["incremental"]["availability"] == "not_yet_observed"
    assert body["incremental"]["position"] is None


def test_positioned_snapshot_is_cursor_neutral(client, app) -> None:
    mgr, session = _seed_session(app)
    session.event_log.append("agent_message", {"text": "one"})
    first = client.get("/api/v1/sessions/sess-1/result").json()
    position = first["incremental"]["position"]
    mgr.db.set_cursor("caller-a", "sess-1", 1, time.time())
    session.event_log.append("agent_message", {"text": "two"})

    body = client.get(
        "/api/v1/sessions/sess-1/result",
        params={"position": position},
    ).json()

    assert [item["summary"] for item in body["incremental"]["items"]] == ["two"]
    assert mgr.db.get_cursor("caller-a", "sess-1") == 1
    assert mgr.db.get_cursor("caller-b", "sess-1") == 0


def test_positioned_snapshot_always_advances_with_small_text_budget(
    client, app
) -> None:
    mgr, session = _seed_session(app)
    _complete_turn(mgr, session, response="r" * 1000)
    session.event_log.append("agent_message", {"text": "first"})
    first = client.get("/api/v1/sessions/sess-1/result").json()
    old_position = first["incremental"]["position"]
    session.event_log.append("agent_message", {"text": "x" * 1000})

    body = client.get(
        "/api/v1/sessions/sess-1/result",
        params={"position": old_position, "max_text_chars": 256},
    ).json()

    assert body["incremental"]["position"] != old_position
    assert body["incremental"]["items"][0]["truncated"] is True


def test_default_snapshot_keeps_latest_items(client, app) -> None:
    _, session = _seed_session(app)
    for number in range(5):
        session.event_log.append("agent_message", {"text": f"message-{number}"})

    body = client.get(
        "/api/v1/sessions/sess-1/result",
        params={"max_items": 2},
    ).json()

    assert [item["summary"] for item in body["incremental"]["items"]] == [
        "message-3",
        "message-4",
    ]
    assert body["incremental"]["truncated_before"] is True
    assert body["incremental"]["has_more"] is False


def test_worktree_handle_resolves_authoritative_owned_session(
    client, app, monkeypatch
) -> None:
    mgr, session = _seed_session(app)
    session.event_log.append("agent_message", {"text": "owned"})
    assert mgr.db.reserve_worktree_ownership(
        "wt-1", "sess-1", now=time.time()
    )
    probe = MagicMock()
    monkeypatch.setattr(session_routes, "resolve_head", probe)

    response = client.get("/api/v1/sessions/wt-1/result")

    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["requested_ref"] == "wt-1"
    assert body["identity"]["snapshot_session_id"] == "sess-1"
    probe.assert_not_called()


def test_worktree_handle_falls_back_to_ground_layer_head(
    client, app, monkeypatch
) -> None:
    mgr, session = _seed_session(app)
    session.acp_session_id = "acp-head"
    mgr.db.update_session_acp_id("sess-1", "acp-head")
    monkeypatch.setattr(
        session_routes,
        "resolve_head",
        lambda _worktree_id: HeadInfo(
            active=False,
            occupied=True,
            head_session="acp-head",
            state="stopped",
            tracked=True,
        ),
    )

    response = client.get("/api/v1/sessions/wt-1/result")

    assert response.status_code == 200
    assert response.json()["identity"]["snapshot_session_id"] == "sess-1"


def test_active_owned_reservation_precedes_ground_layer_probe(
    client, app, monkeypatch
) -> None:
    mgr, predecessor = _seed_session(app)
    successor = Session(
        "sess-2",
        "bright-river",
        SpawnTarget(type="local", cwd="/wt", worktree_id="wt-1"),
        "test-agent",
    )
    successor.status = SessionStatus.IDLE
    successor.acp_session_id = "acp-head"
    successor.event_log = EventLog(
        db=mgr.db, session_id="sess-2", worktree_id="wt-1"
    )
    mgr._sessions["sess-2"] = successor
    mgr.db.create_session(
        "sess-2", "bright-river", "test-agent", "/wt", "local", "idle", time.time()
    )
    mgr.db.update_session_acp_id("sess-2", "acp-head")
    assert mgr.db.reserve_worktree_ownership(
        "wt-1", predecessor.session_id, now=time.time()
    )
    probe = MagicMock()
    monkeypatch.setattr(session_routes, "resolve_head", probe)

    response = client.get("/api/v1/sessions/wt-1/result")

    assert response.status_code == 200
    assert response.json()["identity"]["snapshot_session_id"] == "sess-1"
    probe.assert_not_called()


def test_ambiguous_worktree_without_ground_head_fails_explicitly(
    client, app, monkeypatch
) -> None:
    mgr, _predecessor = _seed_session(app)
    successor = Session(
        "sess-2",
        "bright-river",
        SpawnTarget(type="local", cwd="/wt", worktree_id="wt-1"),
        "test-agent",
    )
    successor.status = SessionStatus.IDLE
    successor.event_log = EventLog(
        db=mgr.db, session_id="sess-2", worktree_id="wt-1"
    )
    mgr._sessions["sess-2"] = successor
    mgr.db.create_session(
        "sess-2", "bright-river", "test-agent", "/wt", "local", "idle", time.time()
    )
    monkeypatch.setattr(
        session_routes,
        "resolve_head",
        lambda _worktree_id: HeadInfo(
            active=False,
            occupied=False,
            head_session=None,
            state=None,
            tracked=False,
        ),
    )

    response = client.get("/api/v1/sessions/wt-1/result")

    assert response.status_code == 409
    assert "without guessing" in response.json()["detail"]


def test_worktree_handle_does_not_fall_back_past_unknown_authoritative_head(
    client, app, monkeypatch
) -> None:
    mgr, predecessor = _seed_session(app)
    predecessor.status = SessionStatus.STOPPED
    mgr.db.update_session_status(
        predecessor.session_id, SessionStatus.STOPPED.value, time.time()
    )
    assert mgr.db.register_live_session(
        "represented-head",
        machine="host",
        cwd="/wt",
        worktree_id="wt-1",
        repo="example/repo",
        branch="main",
        pid=123,
        role=None,
        now=time.time(),
    ) == "live"
    probe = MagicMock(
        return_value=HeadInfo(
            active=True,
            occupied=True,
            head_session="represented-head",
            state="active",
            tracked=True,
        )
    )
    monkeypatch.setattr(session_routes, "resolve_head", probe)

    response = client.get("/api/v1/sessions/wt-1/result")

    assert response.status_code == 409
    assert "represented session" in response.json()["detail"]
    probe.assert_not_called()


def test_represented_only_worktree_reports_unavailable(client, app) -> None:
    mgr: SessionManager = app.state.session_manager
    assert mgr.db.register_live_session(
        "represented-head",
        machine="host",
        cwd="/wt",
        worktree_id="wt-represented",
        repo="example/repo",
        branch="main",
        pid=123,
        role=None,
        now=time.time(),
    ) == "live"

    response = client.get("/api/v1/sessions/wt-represented/result")

    assert response.status_code == 409
    assert "represented session" in response.json()["detail"]


def test_predecessor_snapshot_names_successor(client, app) -> None:
    mgr, _session = _seed_session(app)
    successor = Session(
        "sess-2",
        "bright-river",
        SpawnTarget(type="local", cwd="/wt", worktree_id="wt-1"),
        "test-agent",
    )
    successor.status = SessionStatus.IDLE
    successor.event_log = EventLog(
        db=mgr.db, session_id="sess-2", worktree_id="wt-1"
    )
    mgr._sessions["sess-2"] = successor
    mgr.db.create_session(
        "sess-2", "bright-river", "test-agent", "/wt", "local", "idle", time.time()
    )
    mgr.db.link_succession("sess-1", "sess-2", time.time())

    body = client.get("/api/v1/sessions/sess-1/result").json()

    assert body["identity"]["snapshot_session_id"] == "sess-1"
    assert body["identity"]["current_session_id"] == "sess-2"
    assert body["identity"]["successor_id"] == "sess-2"


def test_old_position_reports_discontinuity_after_rebuild(client, app) -> None:
    _, session = _seed_session(app)
    session.event_log.append("agent_message", {"text": "before"})
    old = client.get("/api/v1/sessions/sess-1/result").json()

    session.event_log.rebuild([("agent_message", {"text": "after"})])
    body = client.get(
        "/api/v1/sessions/sess-1/result",
        params={"position": old["incremental"]["position"]},
    ).json()

    assert body["incremental"]["availability"] == "discontinuous"
    assert body["incremental"]["items"] == []
    assert "rebuilt" in body["incremental"]["reason"]
    assert body["incremental"]["position"] != old["incremental"]["position"]


def test_interrupted_empty_turn_is_partial_not_success(client, app) -> None:
    mgr, session = _seed_session(app)
    _complete_turn(mgr, session, response="partial answer", stop_reason="interrupted")

    body = client.get("/api/v1/sessions/sess-1/result").json()

    assert body["latest_result"]["availability"] == "partial"
    assert body["latest_result"]["value"]["text"] == "partial answer"
    assert body["state"]["attention"]["availability"] == "unknown_after_restart"


@pytest.mark.parametrize("stop_reason", ["max_tokens", "max_turn_requests"])
def test_limit_stop_reasons_are_partial(client, app, stop_reason) -> None:
    mgr, session = _seed_session(app)
    _complete_turn(mgr, session, response="partial answer", stop_reason=stop_reason)

    body = client.get("/api/v1/sessions/sess-1/result").json()

    assert body["latest_result"]["availability"] == "partial"


def test_all_projected_content_respects_text_budget(client, app) -> None:
    mgr, session = _seed_session(app)
    _complete_turn(
        mgr,
        session,
        response="r" * 1000,
        stop_reason="error:" + ("e" * 1000),
    )
    fake = MagicMock()
    fake.pending_ask_user.return_value = [
        {
            "tool_call_id": "t" * 1000,
            "message": "m" * 1000,
            "requested_schema": {
                "type": "object",
                "properties": {("f" * 1000): {"type": "string"}},
            },
        }
    ]
    session.client = fake
    session.event_log.append("turn_complete", {"stop_reason": "s" * 1000})

    body = client.get(
        "/api/v1/sessions/sess-1/result",
        params={"max_text_chars": 2048},
    ).json()

    assert body["limits"]["used_text_chars"] <= 2048
    pending = body["state"]["pending_input"]["value"][0]
    assert len(pending["tool_call_id"]) <= 160
    assert len(pending["fields"][0]) <= 80
    assert body["latest_result"]["value"]["stop_reason_truncated"] is True
    assert body["incremental"]["items"][0]["truncated"] is True


def test_pending_input_is_unknown_without_live_client(client, app) -> None:
    mgr, session = _seed_session(app)
    _complete_turn(mgr, session, response="partial")
    session.client = None

    body = client.get("/api/v1/sessions/sess-1/result").json()

    assert body["state"]["pending_input"]["availability"] == "unknown_after_restart"
    assert body["state"]["pending_input"]["value"] is None


def test_pending_input_is_bounded_when_live(client, app) -> None:
    _, session = _seed_session(app)
    fake = MagicMock()
    fake.pending_ask_user.return_value = [
        {
            "tool_call_id": "tc-1",
            "message": "x" * 2000,
            "requested_schema": {
                "type": "object",
                "properties": {"choice": {"type": "string"}},
            },
        }
    ]
    session.client = fake

    body = client.get(
        "/api/v1/sessions/sess-1/result",
        params={"max_text_chars": 512},
    ).json()

    pending = body["state"]["pending_input"]
    assert pending["availability"] == "available"
    assert pending["value"][0]["message_truncated"] is True
    assert body["limits"]["used_text_chars"] <= 512
    assert body["state"]["attention"]["value"] == "input_required"


def test_detail_reference_expands_turn_and_event(client, app) -> None:
    mgr, session = _seed_session(app)
    _complete_turn(mgr, session, response="done")
    session.event_log.append("agent_message", {"text": "done"})
    snapshot = client.get("/api/v1/sessions/sess-1/result").json()

    turn = client.get(
        "/api/v1/sessions/sess-1/result/detail",
        params={"ref": snapshot["latest_result"]["detail_ref"]},
    )
    event = client.get(
        "/api/v1/sessions/sess-1/result/detail",
        params={"ref": snapshot["incremental"]["items"][0]["detail_ref"]},
    )

    assert turn.status_code == 200
    assert turn.json()["turn"]["response_text"] == "done"
    assert "thought_text" not in turn.json()["turn"]
    assert event.status_code == 200
    assert event.json()["event"]["event"] == "agent_message"


def test_detail_reference_does_not_expand_unprojected_thought(client, app) -> None:
    _, session = _seed_session(app)
    event = session.event_log.append("agent_thought", {"text": "private reasoning"})
    ref = _event_ref(
        "owned",
        session.session_id,
        session.event_log.continuity_id or "",
        event.id,
    )

    response = client.get(
        "/api/v1/sessions/sess-1/result/detail",
        params={"ref": ref},
    )

    assert response.status_code == 404


def test_result_position_is_flushed_to_durable_storage(client, app) -> None:
    mgr, session = _seed_session(app)
    session.event_log.append("agent_message", {"text": "durable"})

    body = client.get("/api/v1/sessions/sess-1/result").json()

    assert body["incremental"]["position"]
    assert mgr.db.get_max_event_id("sess-1") == 1


def test_detail_reference_rejects_rebuilt_history(client, app) -> None:
    _, session = _seed_session(app)
    session.event_log.append("agent_message", {"text": "before"})
    snapshot = client.get("/api/v1/sessions/sess-1/result").json()
    ref = snapshot["incremental"]["items"][0]["detail_ref"]
    session.event_log.rebuild([("agent_message", {"text": "after"})])

    response = client.get(
        "/api/v1/sessions/sess-1/result/detail",
        params={"ref": ref},
    )

    assert response.status_code == 409


def test_missing_detail_404_is_not_quoted(client, app) -> None:
    _seed_session(app)

    response = client.get(
        "/api/v1/sessions/sess-1/result/detail",
        params={"ref": _turn_ref("sess-1", 999)},
    )

    assert response.status_code == 404
    assert not response.json()["detail"].startswith("'")


@pytest.mark.parametrize("ref", ["not-a-result-token", "abr1.%%%%"])
def test_detail_reference_rejects_malformed_input_as_bad_request(
    client, app, ref
) -> None:
    _seed_session(app)

    response = client.get(
        "/api/v1/sessions/sess-1/result/detail",
        params={"ref": ref},
    )

    assert response.status_code == 400


def test_unknown_session_ref_does_not_probe_ground_layer(
    client, app, monkeypatch
) -> None:
    probe = MagicMock()
    monkeypatch.setattr(session_routes, "resolve_head", probe)

    response = client.get("/api/v1/sessions/typo/result")

    assert response.status_code == 404
    probe.assert_not_called()


def test_client_gates_snapshot_against_older_daemon(monkeypatch) -> None:
    client = BridgeClient("http://127.0.0.1:1", token="x")
    monkeypatch.setattr(
        client,
        "daemon_protocol",
        lambda **_kwargs: (RESULT_SNAPSHOT_PROTOCOL_VERSION - 1, 1),
    )

    with pytest.raises(BridgeClientError) as exc:
        client.get_result_snapshot("sess-1")

    assert exc.value.status == 426
    assert "require agent-bridge HTTP protocol" in exc.value.detail


def test_represented_snapshot_reports_reduced_fidelity(client, app) -> None:
    _register_live(client)
    _ingest_live(
        client,
        {"id": "1", "type": "user.message", "data": {"content": "prompt"}},
        {"id": "2", "type": "assistant.reasoning", "data": {"content": "hidden"}},
        {"id": "3", "type": "assistant.message", "data": {"content": "visible"}},
        {"id": "4", "type": "assistant.turn_end", "data": {}},
        {
            "id": "5",
            "type": "tool.execution_start",
            "data": {
                "toolCallId": "nested-tool",
                "toolName": "Nested work",
                "agentId": "sub-1",
            },
        },
    )

    body = client.get("/api/v1/live-sessions/wt-live/result").json()

    assert body["fidelity"]["level"] == "reduced"
    assert body["fidelity"]["event_retention"] == "process_lifetime"
    assert body["latest_result"]["availability"] == "available"
    assert body["latest_result"]["value"]["text"] == "visible"
    assert body["state"]["at_rest"] is True
    assert body["state"]["liveness"] is None
    assert body["incremental"]["position"].startswith("abr1.")
    assert "restart_stable_position" in body["fidelity"]["unavailable"]

    detail = client.get(
        "/api/v1/live-sessions/wt-live/result/detail",
        params={"ref": body["latest_result"]["detail_ref"]},
    ).json()
    assert [event["event"] for event in detail["events"]] == [
        "agent_message",
        "turn_complete",
    ]


def test_represented_position_reports_discontinuity_after_store_reset(
    client, app
) -> None:
    _register_live(client)
    _ingest_live(
        client,
        {"id": "1", "type": "assistant.message", "data": {"content": "before"}},
    )
    first = client.get("/api/v1/live-sessions/live-1/result").json()
    app.state.live_event_store.drop("live-1")
    _ingest_live(
        client,
        {"id": "2", "type": "assistant.message", "data": {"content": "after"}},
    )

    body = client.get(
        "/api/v1/live-sessions/live-1/result",
        params={"position": first["incremental"]["position"]},
    ).json()

    assert body["incremental"]["availability"] == "discontinuous"
    assert "rebuilt or replaced" in body["incremental"]["reason"]


def test_represented_pending_input_is_read_only(client, app) -> None:
    _register_live(client)
    _ingest_live(
        client,
        {
            "id": "1",
            "type": "tool.execution_start",
            "data": {
                "toolCallId": "ask-1",
                "toolName": "ask_user",
                "arguments": {"message": "Choose"},
            },
        },
    )

    body = client.get("/api/v1/live-sessions/live-1/result").json()

    pending = body["state"]["pending_input"]
    assert pending["availability"] == "partial"
    assert pending["value"][0]["read_only"] is True
    assert body["state"]["attention"]["value"] == "input_required"


def test_represented_latest_result_respects_user_turn_boundary(client, app) -> None:
    _register_live(client)
    _ingest_live(
        client,
        {"id": "1", "type": "assistant.message", "data": {"content": "old"}},
        {"id": "2", "type": "user.message", "data": {"content": "new prompt"}},
        {"id": "3", "type": "assistant.message", "data": {"content": "new"}},
        {"id": "4", "type": "assistant.turn_end", "data": {}},
    )

    body = client.get("/api/v1/live-sessions/live-1/result").json()

    assert body["latest_result"]["availability"] == "available"
    assert body["latest_result"]["value"]["text"] == "new"


def test_represented_latest_result_excludes_nested_agent_output(client, app) -> None:
    _register_live(client)
    _ingest_live(
        client,
        {"id": "1", "type": "user.message", "data": {"content": "prompt"}},
        {
            "id": "2",
            "type": "assistant.message",
            "data": {"content": "nested", "agentId": "sub-1"},
        },
        {"id": "3", "type": "assistant.message", "data": {"content": "parent"}},
        {"id": "4", "type": "assistant.turn_end", "data": {}},
    )

    body = client.get("/api/v1/live-sessions/live-1/result").json()

    assert body["latest_result"]["value"]["text"] == "parent"


def test_nested_completion_does_not_set_parent_boundary(client, app) -> None:
    _register_live(client)
    _ingest_live(
        client,
        {"id": "1", "type": "user.message", "data": {"content": "prompt"}},
        {
            "id": "2",
            "type": "assistant.message",
            "data": {"content": "nested", "agentId": "sub-1"},
        },
        {
            "id": "3",
            "type": "assistant.turn_end",
            "data": {"agentId": "sub-1"},
        },
        {"id": "4", "type": "assistant.message", "data": {"content": "parent"}},
    )

    body = client.get("/api/v1/live-sessions/live-1/result").json()

    assert body["latest_result"]["availability"] == "partial"
    assert body["latest_result"]["value"]["text"] == "parent"
    assert body["state"]["attention"]["value"] is None
    detail = client.get(
        "/api/v1/live-sessions/live-1/result/detail",
        params={"ref": body["latest_result"]["detail_ref"]},
    ).json()
    assert [event["event"] for event in detail["events"]] == ["agent_message"]
    assert detail["events"][0]["data"]["text"] == "parent"
    assert [item["summary"] for item in body["incremental"]["items"]] == [
        "parent"
    ]


def test_represented_detail_rejects_forged_nested_event_ref(client, app) -> None:
    _register_live(client)
    _ingest_live(
        client,
        {
            "id": "1",
            "type": "assistant.message",
            "data": {"content": "nested", "agentId": "sub-1"},
        },
    )
    log = app.state.live_event_store.get("live-1")
    assert log is not None
    ref = _event_ref(
        "represented",
        "live-1",
        log.continuity_id or "",
        1,
    )

    response = client.get(
        "/api/v1/live-sessions/live-1/result/detail",
        params={"ref": ref},
    )

    assert response.status_code == 404


def test_represented_resolved_input_is_not_pending(client, app) -> None:
    _register_live(client)
    _ingest_live(
        client,
        {
            "id": "1",
            "type": "tool.execution_start",
            "data": {
                "toolCallId": "ask-1",
                "toolName": "ask_user",
                "arguments": {"message": "Choose"},
            },
        },
        {
            "id": "2",
            "type": "tool.execution_complete",
            "data": {"toolCallId": "ask-1", "success": True},
        },
    )

    body = client.get("/api/v1/live-sessions/live-1/result").json()

    assert body["state"]["pending_input"]["value"] == []
    assert body["state"]["attention"]["value"] is None


def test_nested_tool_is_not_parent_active_work(client, app) -> None:
    _register_live(client)
    _ingest_live(
        client,
        {
            "id": "1",
            "type": "tool.execution_start",
            "data": {
                "toolCallId": "nested-tool",
                "toolName": "Nested work",
                "agentId": "sub-1",
            },
        },
    )

    body = client.get("/api/v1/live-sessions/live-1/result").json()

    assert body["state"]["active_work"]["value"] is None


def test_represented_pending_tool_id_is_bounded(client, app) -> None:
    _register_live(client)
    _ingest_live(
        client,
        {
            "id": "1",
            "type": "tool.execution_start",
            "data": {
                "toolCallId": "x" * 5000,
                "toolName": "ask_user",
                "arguments": {"message": "Choose"},
            },
        },
    )

    body = client.get(
        "/api/v1/live-sessions/live-1/result",
        params={"max_text_chars": 256},
    ).json()

    pending = body["state"]["pending_input"]["value"][0]
    assert len(pending["tool_call_id"]) <= 160
    assert pending["tool_call_id_truncated"] is True
    assert body["limits"]["used_text_chars"] <= 256


def test_represented_snapshot_without_events_is_explicit(client, app) -> None:
    _register_live(client)

    body = client.get("/api/v1/live-sessions/live-1/result").json()

    assert body["incremental"]["availability"] == "not_yet_observed"
    assert body["incremental"]["position"] is None
    assert body["latest_result"]["availability"] == "not_yet_observed"


def test_process_replacement_requires_a_new_session_id(
    client, app
) -> None:
    _register_live(client)
    _ingest_live(
        client,
        {"id": "1", "type": "assistant.message", "data": {"content": "before"}},
    )
    first = client.get("/api/v1/live-sessions/live-1/result").json()
    app.state.db.execute_write(
        "UPDATE live_sessions SET status='expired' WHERE session_id=?",
        ("live-1",),
    )
    response = client.post(
        "/api/v1/live-sessions",
        json={
            "session_id": "live-1",
            "machine": "host",
            "cwd": "/wt",
            "worktree_id": "wt-live",
            "repo": "example/repo",
            "branch": "main",
            "pid": 456,
        },
    )
    assert response.status_code == 409
    assert response.json()["detail"]["reason"] == "incarnation_mismatch"
    _register_live(client, session_id="live-2", worktree_id="wt-live")
    _ingest_live(
        client,
        {"id": "1", "type": "assistant.message", "data": {"content": "after"}},
        session_id="live-2",
    )

    current = client.get("/api/v1/live-sessions/wt-live/result").json()
    stale = client.get(
        "/api/v1/live-sessions/live-2/result",
        params={"position": first["incremental"]["position"]},
    )

    assert current["identity"]["snapshot_session_id"] == "live-2"
    assert stale.status_code == 400


def test_new_process_incarnation_refreshes_worktree_ordering(client, app) -> None:
    db = app.state.db
    assert db.register_live_session(
        "reused",
        machine="host",
        cwd="/wt",
        worktree_id="wt-order",
        repo="example/repo",
        branch="main",
        pid=100,
        role=None,
        now=100.0,
    ) == "live"
    db.execute_write(
        "UPDATE live_sessions SET status='expired' WHERE session_id=?",
        ("reused",),
    )
    assert db.register_live_session(
        "other",
        machine="host",
        cwd="/wt",
        worktree_id="wt-order",
        repo="example/repo",
        branch="main",
        pid=200,
        role=None,
        now=200.0,
    ) == "live"
    db.execute_write(
        "UPDATE live_sessions SET status='wedged' WHERE session_id=?",
        ("other",),
    )
    assert db.register_live_session(
        "replacement",
        machine="host",
        cwd="/wt",
        worktree_id="wt-order",
        repo="example/repo",
        branch="main",
        pid=300,
        role=None,
        now=300.0,
    ) == "live"

    assert db.current_represented_session_for_worktree(
        "wt-order", now=300.0
    ) == "replacement"


def test_wedged_same_process_preserves_represented_history(client, app) -> None:
    _register_live(client)
    _ingest_live(
        client,
        {"id": "1", "type": "assistant.message", "data": {"content": "before"}},
    )
    first = client.get("/api/v1/live-sessions/live-1/result").json()
    app.state.db.execute_write(
        "UPDATE live_sessions SET status='wedged' WHERE session_id=?",
        ("live-1",),
    )
    _register_live(client)
    _ingest_live(
        client,
        {"id": "2", "type": "assistant.message", "data": {"content": "after"}},
    )

    body = client.get(
        "/api/v1/live-sessions/wt-live/result",
        params={"position": first["incremental"]["position"]},
    ).json()

    assert body["incremental"]["availability"] == "available"
    assert [item["summary"] for item in body["incremental"]["items"]] == ["after"]


def test_wedged_worktree_resolves_for_result_inspection(client, app) -> None:
    _register_live(client)
    app.state.db.execute_write(
        "UPDATE live_sessions SET status='wedged' WHERE session_id=?",
        ("live-1",),
    )

    response = client.get("/api/v1/live-sessions/wt-live/result")

    assert response.status_code == 200
    assert response.json()["state"]["session_status"] == "wedged"


def test_owned_reservation_wins_over_wedged_worktree_result(client, app) -> None:
    _register_live(client)
    app.state.db.execute_write(
        "UPDATE live_sessions SET status='wedged' WHERE session_id=?",
        ("live-1",),
    )
    mgr: SessionManager = app.state.session_manager
    owned = Session(
        "owned-1",
        "owned",
        SpawnTarget(type="local", cwd="/wt", worktree_id="wt-live"),
        "test-agent",
    )
    owned.status = SessionStatus.IDLE
    owned.event_log = EventLog(
        db=mgr.db, session_id="owned-1", worktree_id="wt-live"
    )
    mgr._sessions["owned-1"] = owned
    mgr.db.create_session(
        "owned-1", "owned", "test-agent", "/wt", "local", "idle", time.time()
    )
    assert mgr.db.reserve_worktree_ownership(
        "wt-live", "owned-1", now=time.time()
    )

    represented = client.get(
        "/api/v1/live-sessions/result-target",
        params={"handle": "wt-live"},
    )
    owned_result = client.get("/api/v1/sessions/wt-live/result")

    assert represented.status_code == 404
    assert owned_result.status_code == 200
    assert owned_result.json()["identity"]["snapshot_session_id"] == "owned-1"


def test_failed_owned_reservation_yields_to_represented_result(client, app) -> None:
    mgr, owned = _seed_session(app)
    assert mgr.db.reserve_worktree_ownership(
        "wt-1", owned.session_id, now=time.time()
    )
    owned.status = SessionStatus.FAILED
    mgr.db.update_session_status(
        owned.session_id, SessionStatus.FAILED.value, time.time()
    )
    _register_live(client, worktree_id="wt-1")

    represented = client.get("/api/v1/live-sessions/wt-1/result")
    owned_result = client.get("/api/v1/sessions/wt-1/result")

    assert represented.status_code == 200
    assert represented.json()["identity"]["snapshot_session_id"] == "live-1"
    assert owned_result.status_code == 409
    assert "represented session" in owned_result.json()["detail"]


def test_wedged_representation_outranks_stopped_owned_history(client, app) -> None:
    mgr, owned = _seed_session(app)
    owned.status = SessionStatus.STOPPED
    mgr.db.update_session_status(
        owned.session_id, SessionStatus.STOPPED.value, time.time()
    )
    _register_live(client, worktree_id="wt-1")
    app.state.db.execute_write(
        "UPDATE live_sessions SET status='wedged' WHERE session_id=?",
        ("live-1",),
    )

    represented = client.get("/api/v1/live-sessions/wt-1/result")
    owned_result = client.get("/api/v1/sessions/wt-1/result")

    assert represented.status_code == 200
    assert represented.json()["identity"]["snapshot_session_id"] == "live-1"
    assert owned_result.status_code == 409
    assert "represented session" in owned_result.json()["detail"]


def test_client_gates_represented_snapshot_against_protocol_six(
    monkeypatch,
) -> None:
    client = BridgeClient("http://127.0.0.1:1", token="x")
    monkeypatch.setattr(
        client,
        "daemon_protocol",
        lambda **_kwargs: (RESULT_SNAPSHOT_PROTOCOL_VERSION, 1),
    )

    with pytest.raises(BridgeClientError) as exc:
        client.get_live_result_snapshot("live-1")

    assert exc.value.status == 426
    assert (
        f"protocol v{REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION}"
        in exc.value.detail
    )


def test_cli_result_renders_snapshot(monkeypatch, capsys) -> None:
    class _FakeClient:
        def daemon_supports(self, _version):
            return False

        def resolve_live_result_target(self, _session_ref):
            raise AssertionError("v6 owned result must not probe represented target")

        def get_result_snapshot(self, *_args, **_kwargs):
            return {
                "identity": {
                    "logical_delegate_id": "wt-1",
                    "snapshot_session_id": "sess-1",
                    "current_session_id": "sess-1",
                },
                "fidelity": {"level": "full"},
                "state": {
                    "session_status": "idle",
                    "attention": {"availability": "available", "value": "turn_complete"},
                    "active_work": {"availability": "available", "value": None},
                    "pending_input": {"availability": "available", "value": []},
                },
                "latest_result": {
                    "availability": "available",
                    "value": {
                        "turn_index": 0,
                        "text": "done",
                        "stop_reason": "end_turn",
                    },
                    "detail_ref": "abr1.turn",
                },
                "incremental": {
                    "availability": "available",
                    "items": [
                        {
                            "event_id": 2,
                            "kind": "turn_complete",
                            "summary": "end_turn",
                            "status": "end_turn",
                        }
                    ],
                    "position": "abr1.position",
                },
            }

    monkeypatch.setattr(cli, "_get_client", lambda **_kwargs: _FakeClient())
    args = argparse.Namespace(
        session_ref="sess-1",
        position=None,
        max_items=None,
        max_text_chars=None,
        expand=None,
        json=False,
    )

    cli._cmd_result(args)

    output = capsys.readouterr().out
    assert "wt-1  [idle]  fidelity=full" in output
    assert "Latest:    available" in output
    assert "Position:  abr1.position" in output


def test_cli_result_uses_represented_surface(monkeypatch, capsys) -> None:
    class _FakeClient:
        def daemon_supports(self, _version):
            return True

        def resolve_live_result_target(self, _session_ref):
            return {"session_id": "live-1"}

        def get_live_result_snapshot(self, *_args, **_kwargs):
            return {
                "identity": {
                    "logical_delegate_id": "wt-live",
                    "snapshot_session_id": "live-1",
                    "current_session_id": "live-1",
                },
                "fidelity": {"level": "reduced"},
                "state": {
                    "session_status": "live",
                    "attention": {"availability": "partial", "value": None},
                    "active_work": {"availability": "available", "value": None},
                    "pending_input": {
                        "availability": "partial",
                        "value": [],
                        "reason": "process tail only",
                    },
                },
                "latest_result": {"availability": "not_yet_observed"},
                "incremental": {
                    "availability": "not_yet_observed",
                    "items": [],
                    "position": None,
                },
            }

    monkeypatch.setattr(cli, "_get_client", lambda **_kwargs: _FakeClient())
    args = argparse.Namespace(
        session_ref="wt-live",
        position=None,
        max_items=None,
        max_text_chars=None,
        expand=None,
        json=False,
    )

    cli._cmd_result(args)

    output = capsys.readouterr().out
    assert "wt-live  [live]  fidelity=reduced" in output
