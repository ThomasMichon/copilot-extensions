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


def test_the_cli_passes_a_boundary_through(monkeypatch, capsys):
    from agent_bridge.protocol import ATTENTION_BOUNDARY_PROTOCOL_VERSION

    fake = _fake_client(ATTENTION_BOUNDARY_PROTOCOL_VERSION, {
        "a": {"requested_ref": "a", "registry": "bridge", "session_id": "a", "reason": "turn_complete",
              "availability": "available", "fidelity": "full",
              "since": "2026-01-01T00:00:00+00:00", "position": "opaque-position"}})
    monkeypatch.setattr(cli, "_get_client", lambda ensure=True: fake)
    cli.main(["--json", "attention", "a"])
    entry = json.loads(capsys.readouterr().out)["sessions"][0]
    assert (entry["since"], entry["position"]) == ("2026-01-01T00:00:00+00:00", "opaque-position")


# -- the boundary (since, position) ------------------------------------------------


def _token(position: str) -> dict:
    from agent_bridge.attention_wait import _decode_token

    return _decode_token(position)


def _epoch(since: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(since).timestamp()


_QUESTION = {"id": "1", "type": "tool.execution_start", "data": {
    "toolCallId": "ask-1", "toolName": "ask_user", "arguments": {"message": "Choose"}}}


def test_the_boundary_capability_is_advertised():
    from agent_bridge.protocol import ATTENTION_BOUNDARY_PROTOCOL_VERSION

    assert HTTP_PROTOCOL_VERSION >= ATTENTION_BOUNDARY_PROTOCOL_VERSION > CURRENT_ATTENTION_PROTOCOL_VERSION


def test_an_owned_request_names_the_position_wait_settles_on(client, app):
    session = _owned(app, pending={"ask-1"})
    session.event_log.append("user_message", {"content": "go"})
    asked = session.event_log.append("ask_user_request", {"tool_call_id": "ask-1", "message": "which?"})
    body = _current(client)
    assert _epoch(body["since"]) == pytest.approx(asked.timestamp)
    token = _token(body["position"])
    assert (token["kind"], token["logical_delegate_kind"], token["logical_delegate_id"],
            token["observed_session_id"], token["event_id"]) == ("position", "worktree", "wt-1", "sess-1", asked.id)
    waited = client.get("/api/v1/sessions/sess-1/attention",
                        params={"reason": "input_required", "timeout_seconds": 0}).json()
    assert (waited["settled"], waited["position"]) == (True, body["position"])
    after = client.get("/api/v1/sessions/sess-1/attention", params={
        "reason": "input_required", "position": body["position"], "timeout_seconds": 0})
    assert after.status_code == 200, after.text
    assert after.json()["settled"] is False


def test_the_same_boundary_keeps_its_position_and_the_next_one_moves_it(client, app):
    session = _owned(app, pending={"ask-1", "ask-2"})
    session.event_log.append("ask_user_request", {"tool_call_id": "ask-1", "message": "which?"})
    first = _current(client)
    assert _current(client, "wt-1")["position"] == first["position"]
    session.event_log.append("ask_user_resolved", {"tool_call_id": "ask-1"})
    session.event_log.append("ask_user_request", {"tool_call_id": "ask-2", "message": "and then?"})
    second = _current(client)
    assert second["reason"] == first["reason"] == "input_required"
    assert _token(second["position"])["event_id"] > _token(first["position"])["event_id"]


def test_an_idle_session_names_its_turn_completion(client, app):
    session = _owned(app, status=SessionStatus.IDLE)
    session.event_log.append("user_message", {"content": "go"})
    done = session.event_log.append("turn_complete", {"stop_reason": "end_turn"})
    body = _current(client)
    assert (body["reason"], _token(body["position"])["event_id"]) == ("turn_complete", done.id)
    assert _epoch(body["since"]) == pytest.approx(done.timestamp)


def test_a_terminal_status_names_its_recorded_transition(client, app):
    session = _owned(app, status=SessionStatus.FAILED)
    session.event_log.append("user_message", {"content": "go"})
    failed = session.event_log.append("session_state_changed", {"status": "failed"})
    body = _current(client)
    assert (body["reason"], _token(body["position"])["event_id"]) == ("failed", failed.id)


def test_a_terminal_status_without_a_recorded_transition_has_no_boundary(client, app):
    session = _owned(app, status=SessionStatus.FAILED)
    session.event_log.append("user_message", {"content": "go"})
    body = _current(client)
    assert (body["reason"], body["since"], body["position"]) == ("failed", None, None)


def test_nothing_open_reports_an_empty_boundary_not_a_missing_one(client, app):
    session = _owned(app)
    session.event_log.append("turn_complete", {"stop_reason": "end_turn"})
    session.event_log.append("user_message", {"content": "next"})
    body = _current(client)
    assert {"since", "position"} <= body.keys()
    assert (body["reason"], body["since"], body["position"]) == (None, None, None)


def test_a_request_from_before_a_restart_keeps_its_boundary(client, app):
    session = _owned(app)  # the client no longer knows the request
    asked = session.event_log.append("ask_user_request", {"tool_call_id": "ask-1", "message": "which?"})
    body = _current(client)
    assert (body["availability"], _token(body["position"])["event_id"]) == ("unknown_after_restart", asked.id)


def test_a_represented_question_names_a_stable_boundary(client):
    _register_live(client)
    _ingest_live(client, _QUESTION)
    first = _current(client, "live-1")
    assert first["since"] is not None
    assert _current(client, "wt-live")["position"] == first["position"]
    token = _token(first["position"])
    assert (token["logical_delegate_kind"], token["logical_delegate_id"], token["observed_session_id"]) == (
        "worktree", "wt-live", "live-1")
    _ingest_live(client, {"id": "2", "type": "tool.execution_complete", "data": {
        "toolCallId": "ask-1", "success": True}}, {"id": "3", "type": "tool.execution_start", "data": {
            "toolCallId": "ask-2", "toolName": "ask_user", "arguments": {"message": "Choose again"}}})
    second = _current(client, "live-1")
    assert (second["reason"], second["detail"]) == ("input_required", "Choose again")
    assert _token(second["position"])["event_id"] > token["event_id"]


def test_a_represented_turn_completion_names_its_boundary(client):
    _register_live(client)
    _ingest_live(client, {"id": "1", "type": "user.message", "data": {"content": "go"}},
                 {"id": "2", "type": "assistant.turn_end", "data": {}})
    first = _current(client, "live-1")
    assert first["reason"] == "turn_complete" and first["since"] is not None
    _ingest_live(client, {"id": "3", "type": "user.message", "data": {"content": "more"}},
                 {"id": "4", "type": "assistant.turn_end", "data": {}})
    second = _current(client, "live-1")
    assert second["reason"] == "turn_complete"
    assert _token(second["position"])["event_id"] > _token(first["position"])["event_id"]


def test_a_represented_session_after_a_restart_has_no_boundary(client, app):
    _register_live(client)
    _ingest_live(client, _QUESTION)
    app.state.live_event_store = type(app.state.live_event_store)()  # a new generation's empty store
    body = _current(client, "live-1")
    assert (body["availability"], body["since"], body["position"]) == ("unknown_after_restart", None, None)


def test_a_represented_boundary_from_before_this_generation_has_no_position(client):
    """A history replay renumbers what an earlier generation already reported:
    the boundary keeps its time but gets no position to compare."""
    _register_live(client)
    asked_at = time.time() - 7 * 86400
    _ingest_live(client, {**_QUESTION, "timestamp": asked_at})
    body = _current(client, "live-1")
    assert (body["reason"], body["position"]) == ("input_required", None)
    assert _epoch(body["since"]) == pytest.approx(asked_at)


def _land_after_the_first_tail_read(monkeypatch, app, *events, session_id: str = "live-1") -> list[int]:
    """Ingest *events* into the represented history right after a reader's first
    tail read, as live ingestion racing that read would: a later read sees a
    newer tail. Returns how many log events landed (once)."""
    store = app.state.live_event_store
    log = store.get(session_id)
    read = log.snapshot_window
    landed: list[int] = []

    def racing(**window):
        found = read(**window)
        if not landed and window.get("after") is None:
            landed.append(store.ingest(session_id, list(events)))
        return found

    monkeypatch.setattr(log, "snapshot_window", racing)
    return landed


#: A request, what resolves it and the next request of the same reason; the
#: field their detail comes from and the two details.
_REPLACED = {
    "question": ("input_required", "message", ("Choose", "Choose again"), _QUESTION,
                 {"id": "2", "type": "tool.execution_complete", "data": {"toolCallId": "ask-1", "success": True}},
                 {"id": "3", "type": "tool.execution_start", "data": {
                     "toolCallId": "ask-2", "toolName": "ask_user", "arguments": {"message": "Choose again"}}}),
    "permission": ("permission_required", "intention", ("run it", "run it again"),
                   {"id": "1", "type": "permission.requested", "data": {"permissionRequest": {
                       "kind": "shell", "intention": "run it", "toolCallId": "tc-1"}}},
                   {"id": "2", "type": "tool.execution_start", "data": {"toolCallId": "tc-1", "toolName": "shell"}},
                   {"id": "3", "type": "permission.requested", "data": {"permissionRequest": {
                       "kind": "shell", "intention": "run it again", "toolCallId": "tc-2"}}}),
}


@pytest.mark.parametrize("kind", sorted(_REPLACED))
def test_a_represented_row_never_pairs_one_request_with_the_next_ones_position(client, app, monkeypatch, kind):
    """Request A resolves and B, of the same reason, arrives while a row is read:
    the row's detail and its boundary still name the same request. A's detail
    with B's position would let a consumer mark B delivered without seeing it."""
    reason, field, details, first, resolved, second = _REPLACED[kind]
    _register_live(client)
    _ingest_live(client, first)
    landed = _land_after_the_first_tail_read(monkeypatch, app, resolved, second)
    row = _current(client, "live-1")
    assert landed == [2]  # A resolved and B arrived mid-read
    following = _current(client, "live-1")
    events = {event.id: event for event in app.state.live_event_store.get("live-1").get_events()}
    named = [events[_token(body["position"])["event_id"]] for body in (row, following)]
    assert [(body["reason"], body["detail"]) for body in (row, following)] == [
        (reason, details[0]), (reason, details[1])]
    assert [event.data[field] for event in named] == list(details)  # each position names its own request
    assert named[1].id > named[0].id


def test_a_represented_turn_completion_row_names_the_completion_it_read(client, app, monkeypatch):
    """The same holds for a turn completion: one landing mid-read is the next
    read's boundary, not this row's."""
    _register_live(client)
    _ingest_live(client, {"id": "1", "type": "user.message", "data": {"content": "go"}},
                 {"id": "2", "type": "assistant.turn_end", "data": {}})
    landed = _land_after_the_first_tail_read(
        monkeypatch, app, {"id": "3", "type": "user.message", "data": {"content": "more"}},
        {"id": "4", "type": "assistant.turn_end", "data": {}})
    row = _current(client, "live-1")
    assert landed == [2]
    completions = [event.id for event in app.state.live_event_store.get("live-1").get_events()
                   if event.event == "turn_complete"]
    assert (row["reason"], _token(row["position"])["event_id"]) == ("turn_complete", completions[0])
    assert _token(_current(client, "live-1")["position"])["event_id"] == completions[-1]
