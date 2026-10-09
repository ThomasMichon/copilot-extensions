"""``GET /api/v1/sessions/{ref}/attention/current`` and ``agent-bridge attention``:
a session's current attention reason, read once, for owned and represented
sessions alike."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from agent_bridge import attention_cli
from agent_bridge import __main__ as cli
from agent_bridge.app import create_app
from agent_bridge.client import BridgeClientError
from agent_bridge.events import EventLog
from agent_bridge.models import ServiceConfig, SessionStatus
from agent_bridge.protocol import CURRENT_ATTENTION_PROTOCOL_VERSION, HTTP_PROTOCOL_VERSION
from agent_bridge.session_manager import Session, SessionManager
from agent_bridge.transport import SpawnTarget


@pytest.fixture(autouse=True)
def _isolate_local_discovery(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_WORKTREES_PROJECTS_YAML", str(tmp_path / "nonexistent-projects.yaml"))


@pytest.fixture
def app(tmp_path):
    cfg = ServiceConfig(port=0, bind="127.0.0.1", db_path=str(tmp_path / "test.db"))
    return create_app(config=cfg, token="test-token")


@pytest.fixture
def client(app):
    with TestClient(app) as value:
        value.headers["Authorization"] = "Bearer test-token"
        yield value


def _owned(app, sid: str = "sess-1", status: SessionStatus = SessionStatus.RUNNING,
           pending: set[str] | None = None) -> Session:
    mgr: SessionManager = app.state.session_manager
    session = Session(sid, "calm-lake", SpawnTarget(type="local", cwd="/wt", worktree_id="wt-1"), "test-agent")
    session.status = status
    session.event_log = EventLog(db=mgr.db, session_id=sid, worktree_id="wt-1")
    live = pending or set()
    session.client = SimpleNamespace(is_running=False,
                                     has_pending_elicitation=lambda cid: cid in live,
                                     has_pending_permission=lambda rid: rid in live)
    mgr._sessions[sid] = session
    mgr.db.create_session(sid, "calm-lake", "test-agent", "/wt", "local", status.value, time.time())
    return session


def _current(client, ref: str = "sess-1") -> dict:
    response = client.get(f"/api/v1/sessions/{ref}/attention/current")
    assert response.status_code == 200, response.text
    return response.json()


def test_the_capability_is_advertised():
    assert HTTP_PROTOCOL_VERSION >= CURRENT_ATTENTION_PROTOCOL_VERSION


def test_an_owned_session_parked_on_a_permission_request(client, app):
    session = _owned(app, pending={"perm-1"})
    session.event_log.append("user_message", {"content": "go"})
    session.event_log.append("permission_request", {"request_id": "perm-1", "intention": "run the build"})
    body = _current(client)
    assert (body["reason"], body["availability"], body["registry"], body["fidelity"]) == (
        "permission_required", "available", "bridge", "full")
    assert (body["session_id"], body["worktree_id"], body["detail"]) == ("sess-1", "wt-1", "run the build")


def test_an_owned_session_parked_on_a_policy_decision(client, app):
    session = _owned(app)
    session.event_log.append("policy_required", {"action_id": "act-1", "message": "launch paused"})
    assert _current(client)["reason"] == "policy_required"


def test_a_resolved_request_is_no_longer_current(client, app):
    session = _owned(app, status=SessionStatus.IDLE)
    session.event_log.append("ask_user_request", {"tool_call_id": "ask-1", "message": "which?"})
    session.event_log.append("ask_user_resolved", {"tool_call_id": "ask-1"})
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})
    body = _current(client)
    assert (body["reason"], body["detail"]) == ("turn_complete", None)


def test_the_newest_open_request_wins(client, app):
    session = _owned(app, pending={"ask-1", "perm-1"})
    session.event_log.append("ask_user_request", {"tool_call_id": "ask-1", "message": "which?"})
    session.event_log.append("permission_request", {"request_id": "perm-1"})
    assert _current(client)["reason"] == "permission_required"


def test_a_request_from_before_a_restart_is_flagged(client, app):
    session = _owned(app)  # the client no longer knows the request
    session.event_log.append("ask_user_request", {"tool_call_id": "ask-1", "message": "which?"})
    body = _current(client)
    assert (body["reason"], body["availability"]) == ("input_required", "unknown_after_restart")


def test_a_running_session_with_nothing_open_has_no_reason(client, app):
    session = _owned(app)
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})
    session.event_log.append("user_message", {"content": "next"})
    body = _current(client)
    assert (body["reason"], body["availability"]) == (None, None)


def test_a_failed_session_reports_failed(client, app):
    _owned(app, status=SessionStatus.FAILED)
    assert _current(client)["reason"] == "failed"


def test_a_worktree_handle_resolves_its_owned_session(client, app):
    session = _owned(app, pending={"ask-1"})
    session.event_log.append("ask_user_request", {"tool_call_id": "ask-1", "message": "which?"})
    body = _current(client, "wt-1")
    assert (body["reason"], body["session_id"]) == ("input_required", "sess-1")


def _register_live(client, session_id: str = "live-1", worktree_id: str = "wt-live"):
    response = client.post("/api/v1/live-sessions", json={
        "session_id": session_id, "machine": "host", "cwd": "/wt", "worktree_id": worktree_id,
        "repo": "example/repo", "branch": "main", "pid": 123})
    assert response.status_code == 200


def _ingest_live(client, *events, session_id: str = "live-1"):
    response = client.post(f"/api/v1/live-sessions/{session_id}/events", json={"events": list(events)})
    assert response.status_code == 200


def test_a_registered_interactive_session_parked_on_a_question(client):
    _register_live(client)
    _ingest_live(client, {"id": "1", "type": "tool.execution_start", "data": {
        "toolCallId": "ask-1", "toolName": "ask_user", "arguments": {"message": "Choose"}}})
    body = _current(client, "live-1")
    assert (body["reason"], body["registry"], body["fidelity"], body["session_id"], body["worktree_id"]) == (
        "input_required", "live", "reduced", "live-1", "wt-live")


def test_a_registered_interactive_session_parked_on_a_permission(client):
    _register_live(client)
    _ingest_live(client, {"id": "1", "type": "permission.requested", "data": {
        "permissionRequest": {"kind": "shell", "intention": "run it", "toolCallId": "tc-1"}}})
    assert _current(client, "wt-live")["reason"] == "permission_required"


def test_an_unknown_session_is_not_found(client):
    assert client.get("/api/v1/sessions/nope/attention/current").status_code == 404


def test_a_live_registration_without_this_generations_history_is_unknown_not_clear(client, app):
    """After a bridge restart the durable registration survives but the
    represented history doesn't: that is no evidence of no open question."""
    _register_live(client)
    _ingest_live(client, {"id": "1", "type": "tool.execution_start", "data": {
        "toolCallId": "ask-1", "toolName": "ask_user", "arguments": {"message": "Choose"}}})
    app.state.live_event_store = type(app.state.live_event_store)()  # a new generation's empty store
    body = _current(client, "live-1")
    assert (body["reason"], body["availability"], body["session_id"]) == (None, "unknown_after_restart", "live-1")


# -- the CLI -----------------------------------------------------------------------


def _fake_client(version: int, answers: dict) -> MagicMock:
    fake = MagicMock()
    fake.daemon_supports.side_effect = lambda minimum: version >= minimum
    fake.daemon_protocol.return_value = (version, 1)

    def current(method, path):
        ref = path.split("/")[4]
        assert (method, path) == ("GET", f"/api/v1/sessions/{ref}/attention/current")
        answer = answers[ref]
        if isinstance(answer, Exception):
            raise answer
        return dict(answer)

    fake._request.side_effect = current
    return fake


def test_the_cli_reports_each_session_in_order(monkeypatch, capsys):
    fake = _fake_client(CURRENT_ATTENTION_PROTOCOL_VERSION, {
        "a": {"requested_ref": "a", "registry": "bridge", "session_id": "a", "reason": "policy_required",
              "availability": "available", "fidelity": "full"},
        "b": BridgeClientError(404, "Session or worktree b not found"),
        "c": BridgeClientError(500, "boom"),
    })
    monkeypatch.setattr(cli, "_get_client", lambda ensure=True: fake)
    cli.main(["--json", "attention", "a", "b", "c"])
    out = json.loads(capsys.readouterr().out)
    assert out["schema"] == 1
    assert [(e["ref"], e["status"]) for e in out["sessions"]] == [("a", "ok"), ("b", "not_found"), ("c", "error")]
    assert out["sessions"][0]["reason"] == "policy_required" and "requested_ref" not in out["sessions"][0]


def test_the_cli_reports_unsupported_against_an_older_daemon_without_asking_it():
    fake = _fake_client(CURRENT_ATTENTION_PROTOCOL_VERSION - 1, {})
    entries = attention_cli.read_current_attention(fake, ["a", "b"])
    assert [e["status"] for e in entries] == ["unsupported", "unsupported"]
    assert f"v{CURRENT_ATTENTION_PROTOCOL_VERSION}" in entries[0]["error"]
    fake.current_attention.assert_not_called()
