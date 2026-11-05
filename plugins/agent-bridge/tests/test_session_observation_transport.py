"""Owning parser/HTTP boundary, with authentication and no SDK process."""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge import __main__ as cli
from agent_bridge.auth import BearerAuthMiddleware
from agent_bridge.events import EventLog
from agent_bridge.live_representation import LiveEventStore
from agent_bridge.routes.session_observations import router
from agent_bridge.session_observation_cli import ObservationStream, read
from agent_bridge.session_observation_contract import (
    HISTORY,
    CURRENT_REQUESTS,
    OBSERVATIONS,
    ObservationError,
    ReadRequest,
    decode,
)


@pytest.fixture
def endpoint(tmp_db, session_manager):
    tmp_db.create_session("session-1", "fixture", None, "/fixture", "local", "idle", 1.0)
    log = EventLog(db=tmp_db, session_id="session-1", worktree_id="worktree-1")
    log.append("permission_request", {"request_id": "permission-1", "intention": "build"})
    session_manager._sessions["session-1"] = SimpleNamespace(
        session_id="session-1",
        acp_session_id="durable-session",
        target=SimpleNamespace(project="demo", worktree_id="worktree-1", type="local"),
        event_log=log,
        subscriber_count=0,
    )
    session_manager._generation_id = "private-test-generation"
    app = FastAPI()
    app.state.session_manager = session_manager
    app.state.db = tmp_db
    app.state.live_event_store = LiveEventStore()
    app.add_middleware(BearerAuthMiddleware, token="fixture-token")
    app.include_router(router)
    with TestClient(app) as client:
        scheme = "Bear" + "er "
        client.headers["Authorization"] = scheme + "fixture-token"
        yield client


def _read(endpoint, *, history=False, **query):
    path = "session-observation-history" if history else "session-observations"
    return endpoint.get(
        f"/api/v1/{path}/session-1",
        params={"contract_version": "1", "worktree_id": "worktree-1", "project": "demo", **query},
    )


def _body(response):
    assert response.status_code == 200, response.text
    return decode(response.content.split(b"data: ", 1)[1].strip())


def test_actual_snapshot_and_history_routes_are_distinct_finite_reads(endpoint):
    snapshot = _body(_read(endpoint))
    assert snapshot["schema"] == OBSERVATIONS and snapshot["kind"] == "snapshot"
    page_response = _read(endpoint, history=True)
    page = _body(page_response)
    assert page["schema"] == HISTORY and page["kind"] == "history-page"
    assert page_response.content.count(b"data: ") == 1
    assert page["entries"][0]["kind"] == "permission-request"


def test_optional_wire_is_explicit_and_never_added_to_old_or_history_reads(endpoint):
    baseline = _body(_read(endpoint))
    assert "current_request_state" not in baseline
    assert _body(_read(endpoint, current_requests="false")) == baseline
    opted = _body(_read(endpoint, current_requests="true"))
    assert opted["current_requests"] == "unknown"
    assert opted["entries"] == baseline["entries"] and opted["position"] == baseline["position"]
    assert opted["current_request_state"]["availability"] == "unknown"
    assert opted["current_request_state"]["client_incarnation"] is None
    for history in (False, True):
        assert _read(endpoint, history=history, current_requests="True").status_code == 422
    assert _read(endpoint, history=True, current_requests="true").status_code == 422
    assert _read(endpoint, history=True, current_requests="false").status_code == 422


def test_actual_local_callback_future_reaches_http_without_history_inference(endpoint):
    from test_acp_current_requests import ask, client

    async def start_waiter():
        owner = client()
        session = endpoint.app.state.session_manager.get_session("session-1")
        session.client, session.acp_session_id = owner, owner.acp_session_id
        session.status = "idle"
        return owner, await ask(owner)

    owner, task = endpoint.portal.call(start_waiter)
    try:
        pending = _body(_read(endpoint, current_requests="true"))
        current = pending["current_request_state"]
        assert current["availability"] == "observed" and current["completeness"] == "complete"
        assert current["requests"][0]["kind"] == "input"
        assert pending["entries"][0]["kind"] == "permission-request"
        assert "Private question" not in str(current) and "tool-1" not in str(current)
        endpoint.portal.call(lambda: owner.resolve_elicitation("tool-1", {}, action="cancel"))
        cleared = _body(_read(endpoint, current_requests="true"))
        assert cleared["position"] == pending["position"]
        assert cleared["entries"] == pending["entries"]
        assert cleared["current_request_state"]["requests"] == []
        assert cleared["current_request_state"]["revision"] > current["revision"]
    finally:
        async def settle():
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        endpoint.portal.call(settle)


@pytest.mark.parametrize("support", [None, True, 2, 1])
def test_optional_cli_negotiates_actual_provider_before_sending_new_query(support):
    client = MagicMock()
    capabilities = {OBSERVATIONS: 1}
    if support is not None:
        capabilities[CURRENT_REQUESTS] = support
    client.health.return_value = {"capabilities": capabilities}
    query = [("contract_version", "1"), ("worktree_id", "worktree-1")]
    selected = ReadRequest.parse("session-1", [*query, ("current_requests", "true")], history=False)
    if type(support) is int and support == 1:
        read(client, selected)
        assert "current_requests=true" in client._stream_sse.call_args.args[0]
    else:
        with pytest.raises(ObservationError, match=CURRENT_REQUESTS):
            read(client, selected)
        client._stream_sse.assert_not_called()
        # A supported rollback still permits the original baseline.
        read(client, ReadRequest.parse("session-1", query, history=False))
        assert "current_requests" not in client._stream_sse.call_args.args[0]


def test_actual_cli_optional_parser_declaration_and_http_frame_join(endpoint, monkeypatch, capsys):
    from agent_bridge import session_observation_cli

    client = MagicMock()
    client.health.return_value = {"capabilities": {OBSERVATIONS: 1, CURRENT_REQUESTS: 1}}
    payload = _read(endpoint, current_requests="true").content
    client._stream_sse.side_effect = lambda *args, **kwargs: kwargs["stream_factory"](
        io.BytesIO(payload)
    )
    monkeypatch.setattr(cli, "_get_client", lambda **kwargs: client)
    parser = cli.build_parser()
    base = ["observe-session", "session-1", "--contract-version", "1",
            "--worktree-id", "worktree-1", "--json"]
    args = parser.parse_args(["--project", "demo", *base, "--current-requests"])
    session_observation_cli._cmd(args)
    body = decode(capsys.readouterr().out.encode())
    assert body["current_request_state"]["reason"] == "no-current-client"
    assert "current_requests=true" in client._stream_sse.call_args.args[0]
    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--current-request"])
    with pytest.raises(SystemExit):
        parser.parse_args(["session-observation-history", *base[1:], "--current-requests"])
    declaration = parser.parse_args(["version", "--contracts-json"])
    declaration.func(declaration)
    contracts = decode(capsys.readouterr().out.encode())
    assert contracts["contracts"][CURRENT_REQUESTS]["version"] == 1
    assert contracts["contracts"]["agent-bridge.resume"]["version"] == 1


def test_authentication_cannot_be_replaced_by_a_caller_label(endpoint):
    del endpoint.headers["Authorization"]
    response = _read(endpoint, caller_id="trusted-observer")
    assert response.status_code == 401


@pytest.mark.parametrize(
    "query",
    [
        {"contract_version": "2"},
        {"contract_version": "true"},
        {"contract_version": "1.0"},
        {"action": "history"},
        {"caller_id": "owner"},
        {"limit": "0"},
        {"limit": "65"},
        {"limit": "1.0"},
        {"limit": "01"},
        {"limit": "9" * 8000},
        {"follow": "True"},
    ],
)
def test_actual_http_parser_rejects_foreign_or_malformed_fields(endpoint, query):
    assert _read(endpoint, **query).status_code == 422


def test_history_rejects_follow_and_after_instead_of_opening_raw_stream(endpoint):
    assert _read(endpoint, history=True, follow="true").status_code == 422
    assert _read(endpoint, history=True, after="anything").status_code == 422


def test_duplicate_query_fields_rejected(endpoint):
    response = endpoint.get(
        "/api/v1/session-observations/session-1",
        params=[
            ("contract_version", "1"),
            ("contract_version", "1"),
            ("worktree_id", "worktree-1"),
        ],
    )
    assert response.status_code == 422


def test_wrong_head_and_project_are_visible_failures(endpoint):
    assert _body(_read(endpoint, head_session_id="unproven"))["code"] == "head-unavailable"
    assert _body(_read(endpoint, project="foreign"))["code"] == "selection-mismatch"


@pytest.mark.parametrize("history", [False, True])
def test_unknown_source_never_falls_through_to_an_actor_or_raw_stream(endpoint, history):
    path = "session-observation-history" if history else "session-observations"
    response = endpoint.get(
        f"/api/v1/{path}/missing",
        params={"contract_version": "1", "worktree_id": "worktree-1"},
    )
    assert _body(response)["code"] == "source-unavailable"
    assert response.content.count(b"data: ") == 1


@pytest.mark.parametrize("command", ["observe-session", "session-observation-history"])
def test_actual_cli_parser_requires_explicit_version_and_json(command):
    parser = cli.build_parser()
    base = [command, "session-1", "--worktree-id", "worktree-1"]
    with pytest.raises(SystemExit):
        parser.parse_args(base)
    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--contract-version", "1"])
    args = parser.parse_args(["--project", "demo", *base, "--contract-version", "1", "--json"])
    assert args.command == command and args.project == "demo"
    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--contract-version", "1", "--json", "--unknown"])


@pytest.mark.parametrize("capabilities", [None, {}, {OBSERVATIONS: True}, {OBSERVATIONS: 2}])
def test_old_peers_are_unsupported_without_any_event_request(capabilities):
    client = MagicMock()
    client.health.return_value = {"capabilities": capabilities}
    request = ReadRequest.parse(
        "session-1",
        [
            ("contract_version", "1"),
            ("worktree_id", "worktree-1"),
        ],
        history=False,
    )
    with pytest.raises(ObservationError, match="does not support"):
        read(client, request)
    client._stream_sse.assert_not_called()


def test_selected_cli_uses_only_the_negotiated_bounded_parser():
    client = MagicMock()
    client.health.return_value = {"capabilities": {HISTORY: 1}}
    request = ReadRequest.parse(
        "session-1",
        [
            ("contract_version", "1"),
            ("worktree_id", "worktree-1"),
        ],
        history=True,
    )
    read(client, request)
    args, kwargs = client._stream_sse.call_args
    assert args[0].startswith("/api/v1/session-observation-history/session-1?")
    assert kwargs["stream_factory"] is ObservationStream


def test_history_capability_does_not_enable_the_observation_operation():
    client = MagicMock()
    client.health.return_value = {"capabilities": {HISTORY: 1}}
    request = ReadRequest.parse(
        "session-1",
        [
            ("contract_version", "1"),
            ("worktree_id", "worktree-1"),
        ],
        history=False,
    )
    with pytest.raises(ObservationError):
        read(client, request)
    client._stream_sse.assert_not_called()


def test_owning_cli_reaches_the_real_frame_parser(endpoint, monkeypatch, capsys):
    from agent_bridge import session_observation_cli

    client = MagicMock()
    client.health.return_value = {"capabilities": {HISTORY: 1}}
    payload = _read(endpoint, history=True).content
    client._stream_sse.side_effect = lambda *args, **kwargs: kwargs["stream_factory"](
        io.BytesIO(payload)
    )
    monkeypatch.setattr(cli, "_get_client", lambda **kwargs: client)
    args = cli.build_parser().parse_args(
        [
            "--project",
            "demo",
            "session-observation-history",
            "session-1",
            "--worktree-id",
            "worktree-1",
            "--contract-version",
            "1",
            "--json",
        ]
    )
    session_observation_cli._cmd(args)
    body = decode(capsys.readouterr().out.encode())
    assert body["kind"] == "history-page" and body["entries"][0]["native_event_id"] == 1


@pytest.mark.parametrize("scenario", [
    "empty-point", "truncated-point", "empty-history", "truncated-history",
    "follow-eof", "finite-point", "finite-history", "follow-going-away",
])
def test_observer_cli_http_eof(endpoint, monkeypatch, capsys, scenario):
    """Only finite replies or explicit retirement may finish the semantic CLI."""
    from http.client import HTTPResponse

    from agent_bridge.client import BridgeClient
    from agent_bridge.session_observation_contract import sse

    history = scenario.endswith("history")
    follow = scenario.startswith("follow")
    snapshot = _body(_read(endpoint))
    payload = _read(endpoint, history=history).content
    if scenario.startswith("empty"):
        payload = b""
    elif scenario.startswith("truncated"):
        payload = b"event: snapshot\ndata: {"
    elif scenario == "follow-going-away":
        payload += sse({
            "schema": OBSERVATIONS, "version": 1, "kind": "going-away",
            "selection": {
                key: snapshot["source"][key]
                for key in ("session_id", "project", "worktree_id", "head_session_id")
            },
            "reason": "hosting-generation-stopping",
        })
    raw = io.BytesIO(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n"
        + payload
    )
    response = HTTPResponse(SimpleNamespace(makefile=lambda *_args: raw))
    response.begin()
    client = BridgeClient("http://fixture.invalid", "synthetic-token", connect_grace=0)
    monkeypatch.setattr(client, "health", lambda: {
        "capabilities": {OBSERVATIONS: 1, HISTORY: 1},
    })
    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: response)
    calls = []

    def get_client(*, ensure):
        calls.append(ensure)
        return client

    monkeypatch.setattr(cli, "_get_client", get_client)
    monkeypatch.delenv("AGENT_WORKTREES_PROJECT_ROUTED", raising=False)
    argv = [
        "--project", "demo",
        "session-observation-history" if history else "observe-session", "session-1",
        "--worktree-id", "worktree-1", "--contract-version", "1", "--json",
    ]
    if follow:
        argv.append("--follow")
    unrelated = io.BytesIO(b"other subscription")
    previous_project = cli._PROJECT_OVERRIDE
    try:
        try:
            cli.main(argv)
            code = 0
        except SystemExit as exc:
            code = exc.code
        lines = [decode(line.encode()) for line in capsys.readouterr().out.splitlines()]
        legitimate = scenario.startswith("finite") or scenario == "follow-going-away"
        assert code == (0 if legitimate else 3)
        assert lines
        if legitimate:
            assert lines[-1]["kind"] == (
                "going-away" if follow else "history-page" if history else "snapshot"
            )
        else:
            assert lines[-1]["kind"] == "error"
            assert lines[-1]["code"] == "source-unavailable"
        if follow:
            assert len(lines) == 2 and lines[0]["kind"] == "snapshot"
        assert calls == [False]
        assert response.isclosed() and raw.closed and not unrelated.closed
    finally:
        cli._set_project_override(previous_project)
        response.close()
        unrelated.close()
