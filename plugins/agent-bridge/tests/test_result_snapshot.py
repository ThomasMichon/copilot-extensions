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
from agent_bridge.protocol import RESULT_SNAPSHOT_PROTOCOL_VERSION
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
    _mgr, session = _seed_session(app)
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


def test_worktree_handle_resolves_authoritative_owned_session(client, app) -> None:
    mgr, session = _seed_session(app)
    session.event_log.append("agent_message", {"text": "owned"})
    assert mgr.db.reserve_worktree_ownership(
        "wt-1", "sess-1", now=time.time()
    )

    response = client.get("/api/v1/sessions/wt-1/result")

    assert response.status_code == 200
    body = response.json()
    assert body["identity"]["requested_ref"] == "wt-1"
    assert body["identity"]["snapshot_session_id"] == "sess-1"


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


def test_worktree_handle_prefers_ground_layer_head_over_reservation(
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
    monkeypatch.setattr(
        session_routes,
        "resolve_head",
        lambda _worktree_id: HeadInfo(
            active=True,
            occupied=True,
            head_session="acp-head",
            state="active",
            tracked=True,
        ),
    )

    response = client.get("/api/v1/sessions/wt-1/result")

    assert response.status_code == 200
    assert response.json()["identity"]["snapshot_session_id"] == "sess-2"


def test_worktree_handle_does_not_fall_back_past_unknown_authoritative_head(
    client, app, monkeypatch
) -> None:
    mgr, predecessor = _seed_session(app)
    assert mgr.db.reserve_worktree_ownership(
        "wt-1", predecessor.session_id, now=time.time()
    )
    monkeypatch.setattr(
        session_routes,
        "resolve_head",
        lambda _worktree_id: HeadInfo(
            active=True,
            occupied=True,
            head_session="represented-head",
            state="active",
            tracked=True,
        ),
    )

    response = client.get("/api/v1/sessions/wt-1/result")

    assert response.status_code == 409
    assert "not a bridge-owned session" in response.json()["detail"]


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
    _mgr, session = _seed_session(app)
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
    _mgr, session = _seed_session(app)
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
    assert event.status_code == 200
    assert event.json()["event"]["event"] == "agent_message"


def test_result_position_is_flushed_to_durable_storage(client, app) -> None:
    mgr, session = _seed_session(app)
    session.event_log.append("agent_message", {"text": "durable"})

    body = client.get("/api/v1/sessions/sess-1/result").json()

    assert body["incremental"]["position"]
    assert mgr.db.get_max_event_id("sess-1") == 1


def test_detail_reference_rejects_rebuilt_history(client, app) -> None:
    _mgr, session = _seed_session(app)
    session.event_log.append("agent_message", {"text": "before"})
    snapshot = client.get("/api/v1/sessions/sess-1/result").json()
    ref = snapshot["incremental"]["items"][0]["detail_ref"]
    session.event_log.rebuild([("agent_message", {"text": "after"})])

    response = client.get(
        "/api/v1/sessions/sess-1/result/detail",
        params={"ref": ref},
    )

    assert response.status_code == 409


def test_detail_reference_rejects_malformed_input_as_bad_request(client, app) -> None:
    _seed_session(app)

    response = client.get(
        "/api/v1/sessions/sess-1/result/detail",
        params={"ref": "not-a-result-token"},
    )

    assert response.status_code == 400


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


def test_cli_result_renders_snapshot(monkeypatch, capsys) -> None:
    class _FakeClient:
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
