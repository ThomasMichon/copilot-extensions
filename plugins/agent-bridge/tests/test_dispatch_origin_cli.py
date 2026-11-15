"""Ordinary origin carriers negotiate the executing CLI and selected daemon."""

from __future__ import annotations

import io
import json
import sys
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge import __main__ as core, session_start_cli, session_targeting_cli
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.dispatch_origin import CAPABILITY, inspect
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.routes import health, sessions
from test_dispatch_session_origin import origin
from test_workspace_creation import creation_rig as _creation_rig
from test_workspace_creation import rig as _rig

creation_rig = _creation_rig
rig = _rig
pytestmark = pytest.mark.contract("agent_bridge.dispatch_session_origin")


@pytest.mark.parametrize("from_file", [False, True])
def test_origin_cli_reaches_actual_keyed_owner_without_acquisition(
    creation_rig, monkeypatch, tmp_path, capsys, from_file,
):
    rig = creation_rig
    app = FastAPI()
    app.include_router(sessions.router)
    app.include_router(health.router)
    app.state.session_manager = rig.manager
    app.state.ready = True

    async def resolve(_name, **_kwargs):
        return rig.target

    app.state.resolver = SimpleNamespace(resolve_async=resolve)
    monkeypatch.setattr(health, "observe_worktree_writer", lambda: None)
    monkeypatch.setattr(health, "slot_descriptor", lambda *_a, **_kw: {})
    monkeypatch.setattr(health, "get_default_manager", lambda: SimpleNamespace(carrier_diagnostics=lambda: {}))
    path = tmp_path / "origin.json"
    text = json.dumps(origin())
    if from_file:
        path.write_text(text, encoding="utf-8")
    else:
        monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    with TestClient(app) as http:
        client = BridgeClient("http://fixture", token="fixture", connect_grace=0)
        posts = []

        def send(method, endpoint, body=None, **_kwargs):
            if method == "POST":
                posts.append((endpoint, body))
            response = http.request(method, endpoint, json=body)
            if response.status_code >= 400:
                raise BridgeClientError(response.status_code, response.json().get("detail"))
            return response.json()

        monkeypatch.setattr(client, "_request", send)
        monkeypatch.setattr(client, "daemon_supports", lambda _floor: True)
        monkeypatch.setattr(core, "_get_client", lambda: client)
        monkeypatch.setattr(core, "_caller_id_for", lambda _args: None)
        monkeypatch.setattr(core, "_get_caller_session_id", lambda: None)
        monkeypatch.setattr(core, "_worktrees_get", lambda _field: None)
        monkeypatch.setattr(core, "_connection_identity", lambda *_args: {})
        monkeypatch.setattr(session_start_cli, "_read_agent_inventory", lambda _client: {})
        monkeypatch.setattr(session_start_cli, "_prepare_sender_preference",
                            lambda *_args, **_kwargs: SimpleNamespace(project="example"))
        declared = core.build_parser().parse_args(["version", "--contracts-json"])
        declared.func(declared)
        contracts = json.loads(capsys.readouterr().out)["contracts"]
        assert "keyed-dispatch-origin" in contracts["agent-bridge.create"]["capabilities"]
        assert "dispatch-cli-session-origin" in contracts["agent-bridge.acquire"]["capabilities"]
        args = core.build_parser().parse_args([
            "--json", "create", "worker", "--creation-key", "original-key",
            "--worktree-id", rig.row["id"], "--dispatch-origin-file", str(path) if from_file else "-",
        ])
        for _ in range(2):
            if not from_file:
                monkeypatch.setattr(sys, "stdin", io.StringIO(text))
            session_targeting_cli._cmd_create(args)
            response = json.loads(capsys.readouterr().out)
            assert inspect(rig.db.get_session(response["session_id"])) == origin()
        assert len(posts) == 2 and all(
            endpoint == "/api/v1/sessions/workspace-creations" and body["dispatch_origin"] == origin()
            for endpoint, body in posts
        )
        assert len(rig.db.list_sessions()) == 1
        assert not rig.db.execute_read("SELECT 1 FROM acquisition_attempts")


@pytest.mark.parametrize("origin_cap,writer_cap", [
    (None, 1), (False, 1), ("1", 1), (0, 1), (1, None), (1, 1),
])
def test_origin_creation_client_composes_required_selected_peer_capabilities(origin_cap, writer_cap):
    from agent_bridge.creation_worktree_writer import CAPABILITY as WRITER

    client = BridgeClient("http://fixture", token="fixture", connect_grace=0)
    client.daemon_supports = lambda _floor: True
    calls = []

    def send(method, path, *body, **kwargs):
        calls.append((method, path))
        assert kwargs["_endpoint"] == "http://fixture"
        if path == "/health":
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                "capabilities": {CAPABILITY: origin_cap, WRITER: writer_cap},
            }
        assert body[0]["dispatch_origin"] == origin() and "worktree_writer" in body[0]
        return {"session_id": "original"}

    client._request = send
    options = {
        "agent": "worker", "creation_key": "original-key", "worktree_id": "worktree-a",
        "dispatch_origin": origin(), "worktree_writer": {
            "identity": {"installation_root": "/owner", "runtime_generation": "a" * 32},
            "creation_nonce": "original-allocation",
        },
    }
    if type(origin_cap) is int and origin_cap == 1 and writer_cap == 1:
        assert client.start_session(**options)["session_id"] == "original"
        assert calls == [("GET", "/health"), ("POST", "/api/v1/sessions/workspace-creations")]
    else:
        with pytest.raises(BridgeClientError) as refused:
            client.start_session(**options)
        assert refused.value.status == 426 and calls == [("GET", "/health")]


def test_origin_creation_client_rechecks_after_cutover_without_baseline_replay():
    client = BridgeClient("http://fixture", token="fixture", connect_grace=2)
    client.daemon_supports = lambda _floor: True
    client._reresolve = lambda: "http://older"
    posts = []

    def send(method, path, *body, **kwargs):
        assert kwargs["allow_retry"] is False
        if path == "/health":
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                "capabilities": {CAPABILITY: 1} if not posts else {},
            }
        posts.append(body[0])
        raise BridgeClientError(503, "draining")

    client._request = send
    with pytest.raises(BridgeClientError) as refused:
        client.start_session(
            agent="worker", creation_key="original-key", worktree_id="worktree-a",
            dispatch_origin=origin(),
        )
    assert refused.value.status == 426
    assert len(posts) == 1 and posts[0]["dispatch_origin"] == origin()


@pytest.mark.parametrize("changes", [
    {"creation_key": None}, {"worktree_id": None}, {"provider_instance": True},
    {"target_dir": "/elsewhere"}, {"agent": "remote-acp:fixture"}, {"dispatch_origin": {}},
])
def test_origin_creation_client_invalid_scope_refuses_before_peer_discovery(changes):
    client = BridgeClient("http://fixture", token="fixture", connect_grace=0)
    client.daemon_supports = lambda _floor: pytest.fail("invalid origin must not discover peer")
    client._request = lambda *_a, **_kw: pytest.fail("invalid origin must not send")
    with pytest.raises(ValueError):
        client.start_session(**{
            "agent": "worker", "creation_key": "original-key", "worktree_id": "worktree-a",
            "dispatch_origin": origin(), **changes,
        })


@pytest.mark.parametrize("target,options,text", [
    ("worker", [], "{}"),
    ("worker", ["--creation-key", "key"], "{}"),
    ("worker", ["--creation-key", "key", "--worktree-id", "w", "--provider-instance"], "{}"),
    ("worker", ["--creation-key", "key", "--worktree-id", "w", "--cli"], "{}"),
    ("worker", ["--creation-key", "key", "--worktree-id", "w", "--target-dir", "/elsewhere"], "{}"),
    ("worker", ["--creation-key", "key", "--worktree-id", "w", "--prompt-file", "-"], "{}"),
    ("worker", ["--creation-key", "key", "--worktree-id", "w", "--worktree-writer-file", "-"], "{}"),
    ("worker", ["--creation-key", "key", "--worktree-id", "w"], "not json"),
    ("remote-acp:fixture", ["--creation-key", "key", "--worktree-id", "w"], "{}"),
    ("https://example.com/acp", ["--creation-key", "key", "--worktree-id", "w"], "{}"),
])
def test_origin_cli_invalid_forms_refuse_before_connect(monkeypatch, target, options, text):
    monkeypatch.setattr(sys, "stdin", io.StringIO(text))
    monkeypatch.setattr(core, "_get_client", lambda: pytest.fail("invalid origin must not connect"))
    args = core.build_parser().parse_args([
        "create", target, *options, "--dispatch-origin-file", "-",
    ])
    with pytest.raises(SystemExit) as refused:
        session_targeting_cli._cmd_create(args)
    assert refused.value.code == 2
