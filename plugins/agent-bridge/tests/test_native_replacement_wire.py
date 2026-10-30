"""Replacement authority crosses the existing CLI/client/HTTP boundaries."""

from dataclasses import replace
import json
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.routes.sessions import router
from test_native_replacement import (
    OLD, NEW, replacement_rig as replacement_rig,
    creation_rig as creation_rig, rig as rig,
)

pytestmark = pytest.mark.contract("agent_bridge.workspace_creation")


def test_replacement_client_http_replay_preserves_authority(replacement_rig, monkeypatch):
    rig = replacement_rig
    app = FastAPI()
    app.state.session_manager = rig.manager

    async def resolve(name, **_kwargs):
        if name == "dispatch:task-a":
            return replace(rig.target, venue=rig.source)
        assert name == "test-project@local"
        return replace(rig.target)

    app.state.resolver = SimpleNamespace(resolve_async=resolve)
    app.include_router(router)
    client = BridgeClient(base_url="http://127.0.0.1:1", token="test")
    floors, bodies = [], []
    monkeypatch.setattr(client, "daemon_supports", lambda floor: floors.append(floor) or True)
    with TestClient(app) as http:
        def request(method, path, body, **_kwargs):
            assert path == "/api/v1/sessions/workspace-creations"
            bodies.append(body)
            response = http.request(method, path, json=body)
            assert response.status_code == 200, response.text
            return response.json()

        monkeypatch.setattr(client, "_request", request)
        arguments = dict(
            agent="test-project@local", worktree_id=rig.row["id"],
            creation_key="replacement-key", replacement_task="task-a",
        )
        first = client.start_session(**arguments)
        replay = client.start_session(**arguments)
    assert floors.count(52) == 2
    assert first["session_id"] == replay["session_id"]
    assert replay["creation"]["replayed"] is True
    grant = first["creation"]["native_replacement"]
    assert grant == replay["creation"]["native_replacement"]
    assert grant["expected_head"] == OLD and grant["replacement_session"] == NEW
    assert all(body["replacement_task"] == "task-a" for body in bodies)
    assert all("creation_prompt" not in body for body in bodies)
    assert len(rig.promotions) == len(rig.manager.list_sessions()) == 1
    assert not rig.db.execute_read("SELECT * FROM turns")


@pytest.mark.parametrize("change", [
    {"creation_key": None}, {"worktree_id": None}, {"provider_instance": True},
    {"creation_prompt": "unexpected"}, {"creation_prompt": ""},
    {"parity_fault": "unexpected"}, {"replacement_task": ""},
])
def test_replacement_http_invalid_shape_never_resolves(replacement_rig, change):
    rig = replacement_rig
    app = FastAPI()
    app.state.session_manager = rig.manager
    app.include_router(router)
    body = {
        "agent": "test-project@local", "worktree_id": rig.row["id"],
        "creation_key": "replacement-key", "replacement_task": "task-a",
        **change,
    }
    with TestClient(app) as http:
        result = http.post("/api/v1/sessions/workspace-creations", json=body)
    assert result.status_code == 422, result.text
    assert not rig.owner_calls
    assert not rig.manager.list_sessions()


def test_replacement_client_old_receiver_never_posts(monkeypatch):
    client = BridgeClient(base_url="http://127.0.0.1:1", token="test")
    floors = []
    monkeypatch.setattr(client, "daemon_supports", lambda floor: floors.append(floor) or floor < 52)
    monkeypatch.setattr(client, "_request", lambda *_a, **_k: pytest.fail("must not post"))
    with pytest.raises(BridgeClientError) as error:
        client.start_session(
            agent="test-project@local", worktree_id="worktree-a",
            creation_key="replacement-key", replacement_task="task-a",
        )
    assert error.value.status == 426 and floors == [52]


def test_real_creation_parser_forwards_replacement_without_prompt(monkeypatch, capsys):
    from agent_bridge import __main__ as core, session_start_cli, session_targeting_cli
    from agent_bridge.workspace_creation_cli import create

    calls, floors = [], []
    receipt = {
        "session_id": "bridge-a",
        "creation": {
            "status": "complete", "native_session_id": NEW,
            "native_replacement": {"expected_head": OLD, "replacement_session": NEW},
            "worktree": {"id": "worktree-a", "path": "synthetic-path"},
        },
    }
    client = SimpleNamespace(
        start_session=lambda **kw: calls.append(kw) or receipt,
        get_session=lambda _sid: {"status": "idle", "turn_count": 0},
    )
    monkeypatch.setattr(session_start_cli, "_read_agent_inventory", lambda _client: {})
    monkeypatch.setattr(session_start_cli, "_prepare_sender_preference",
                        lambda *_a, **_kw: SimpleNamespace(project="test-project"))
    monkeypatch.setattr(session_targeting_cli, "_hold_protocol_floor",
                        lambda _client, floor: floors.append(floor))
    monkeypatch.setattr(core, "_get_caller_session_id", lambda: None)
    monkeypatch.setattr(core, "_worktrees_get", lambda _field: None)
    monkeypatch.setattr(core, "_connection_identity", lambda *_a: {})
    args = core.build_parser().parse_args([
        "--json", "create", "test-project@local", "--worktree-id", "worktree-a",
        "--creation-key", "replacement-key", "--replacement-task", "task-a",
        "--min-daemon-protocol", "52",
    ])
    create(args, client, caller_id="test-caller")
    assert calls[0]["replacement_task"] == "task-a"
    assert calls[0]["creation_key"] == "replacement-key"
    assert calls[0]["worktree_id"] == "worktree-a"
    assert calls[0]["creation_prompt"] is None
    assert 52 in floors
    assert json.loads(capsys.readouterr().out)["creation"] == receipt["creation"]
