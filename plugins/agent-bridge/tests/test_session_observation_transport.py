"""Owning parser/HTTP boundary, with authentication and no SDK process."""

from __future__ import annotations

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
