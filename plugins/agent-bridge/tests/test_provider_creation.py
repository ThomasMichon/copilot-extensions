"""Prepared provider creation never fabricates a workspace or a second native."""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge import workspace_creation
from agent_bridge.acquisition_contract import AcquisitionRefused
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.models import SessionStatus
from agent_bridge.routes.sessions import router
from agent_bridge.session_manager import DaemonDrainingError, ProviderTargetRefreshError, SessionManager
from agent_bridge.transport import SpawnTarget
from test_acquisition_runtime import rig as _connected_rig

rig = _connected_rig
pytestmark = pytest.mark.contract("agent_bridge.provider_creation")


async def create(manager, target, **options):
    return await workspace_creation.start(manager, target, provider_instance=True, **options)


@pytest.fixture
def provider_rig(rig):
    rig.target = SpawnTarget(
        type="command", spawn_command=["provider", "exec", "original-instance"],
        user="1000", venue={
            "provider": "fixture-provider", "kind": "container",
            "target_id": "container:prepared-original", "instance_id": "original-instance",
            "scope": "provider-instance", "transport": "provider-exec", "ready": True,
            "workspace_folder": "/workspace", "security_profile": "restricted",
            "capabilities": {"instance_pinned_exec": True},
        },
    )
    return rig


@pytest.mark.asyncio
async def test_exact_replay_retains_native_without_workspace_or_prompt(provider_rig):
    rig = provider_rig
    first, initial = await create(
        rig.manager, rig.target, key="logical-work", agent_name="container:prepared-original",
        copilot_args=["--allow-all"],
    )
    second, replay = await create(
        rig.manager, rig.target, key="logical-work", agent_name="container:prepared-original",
        copilot_args=["--allow-all"],
    )
    assert first is second and replay["replayed"] and not initial["replayed"]
    assert initial["native_session_id"] == replay["native_session_id"] == first.acp_session_id
    assert replay["venue"]["instance_id"] == "original-instance"
    assert "worktree" not in replay and replay["turn_index"] is None
    assert first.target.worktree_id is None and first.target.project is None
    assert len(rig.manager.list_sessions()) == 1
    assert not rig.db.execute_read("SELECT * FROM workspace_embodiments")
    assert not rig.db.execute_read("SELECT * FROM turns")
    assert not rig.calls
    await asyncio.sleep(0)


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["non-ascii-\u00e9", "escaped-surrogate-\ud800"])
async def test_opaque_json_keys_replay_without_encoding_failure(provider_rig, key):
    rig = provider_rig
    first, initial = await create(rig.manager, rig.target, key=key)
    second, replay = await create(rig.manager, rig.target, key=key)
    assert first is second and replay["replayed"]
    assert initial["native_session_id"] == replay["native_session_id"]
    assert len(rig.manager.list_sessions()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["instance", "command", "user", "args", "env"])
async def test_changed_facts_cannot_reuse_same_key(provider_rig, change):
    rig = provider_rig
    await create(rig.manager, rig.target, key="logical-work")
    changed = replace(rig.target, venue=dict(rig.target.venue))
    options = {}
    if change == "instance":
        changed.venue["instance_id"] = "replacement-instance"
    elif change == "command":
        changed.spawn_command = ["provider", "exec", "replacement-instance"]
    elif change == "user":
        changed.user = "0"
    elif change == "args":
        options["copilot_args"] = ["--different"]
    else:
        changed.env = {"SCOPE": "different"}
    with pytest.raises(AcquisitionRefused) as refused:
        await create(rig.manager, changed, key="logical-work", **options)
    assert refused.value.code == "creation_conflict"
    assert len(rig.manager.list_sessions()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", [
    "prompt", "worktree", "project", "cwd", "unready", "instance", "scope", "transport",
    "unpinned",
])
async def test_unsupported_request_refuses_before_any_effect(provider_rig, change):
    rig = provider_rig
    target = replace(rig.target, venue=dict(rig.target.venue))
    options = {}
    if change == "prompt":
        options["prompt"] = "Must wait for authority"
    elif change == "worktree":
        target.worktree_id = "not-a-real-worktree"
    elif change == "project":
        target.project = "not-a-real-project"
    elif change == "cwd":
        target.explicit_cwd = True
    elif change == "unready":
        target.venue["ready"] = False
    elif change == "instance":
        target.venue.pop("instance_id")
    elif change == "scope":
        target.venue["scope"] = "unspecified"
    elif change == "transport":
        target.venue["transport"] = "ssh"
    else:
        target.venue["capabilities"] = {}
    with pytest.raises(AcquisitionRefused) as refused:
        await create(rig.manager, target, key="logical-work", **options)
    assert refused.value.effect == "not_started"
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM provider_embodiments")


@pytest.mark.asyncio
async def test_name_only_command_cannot_claim_an_observed_instance(provider_rig, monkeypatch):
    rig = provider_rig
    target = replace(
        rig.target, spawn_command=["provider", "exec", "reused-name"],
        venue={**rig.target.venue, "capabilities": {}, "instance_id": "instance-a"},
    )
    monkeypatch.setattr(rig.manager, "start_session", lambda *_a, **_k: pytest.fail(
        "name-based exec could now launch replacement instance-b",
    ))
    with pytest.raises(AcquisitionRefused) as refused:
        await create(rig.manager, target, key="logical-work")
    assert refused.value.code == "creation_venue_unsupported"
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM provider_embodiments")


@pytest.mark.asyncio
async def test_lost_native_ack_recovers_only_original_session(provider_rig, monkeypatch):
    rig = provider_rig
    original = rig.manager.start_session
    calls = []

    async def interrupted(*args, **kwargs):
        calls.append(kwargs)
        await original(*args, **kwargs)
        raise RuntimeError("lost acknowledgement")

    monkeypatch.setattr(rig.manager, "start_session", interrupted)
    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        await create(rig.manager, rig.target, key="logical-work")
    session, replay = await create(rig.manager, rig.target, key="logical-work")
    assert replay["replayed"] and replay["native_session_id"] == session.acp_session_id
    assert len(calls) == len(rig.manager.list_sessions()) == 1


@pytest.mark.asyncio
async def test_lost_ack_then_ordinary_send_cannot_recreate_native(provider_rig, monkeypatch):
    rig = provider_rig
    original = rig.manager.start_session

    async def interrupted(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("lost acknowledgement")

    monkeypatch.setattr(rig.manager, "start_session", interrupted)
    with pytest.raises(RuntimeError, match="lost acknowledgement"):
        await create(rig.manager, rig.target, key="logical-work")
    session = rig.manager.list_sessions()[0]
    native = session.acp_session_id
    assert rig.db.execute_read("SELECT native_session_id FROM provider_embodiments")[0][0] is None
    session.status = SessionStatus.STOPPED
    session.client = None
    rig.client.load_session = AsyncMock(side_effect=RuntimeError("original native cannot load"))
    rig.client.stderr_tail.return_value = ""
    with pytest.raises(RuntimeError, match="original native cannot load"):
        await rig.manager.submit_prompt(session.session_id, "ordinary automatic resume")
    rig.client.new_session.assert_awaited_once()
    assert session.acp_session_id == native and session.status == SessionStatus.STOPPED
    replay, receipt = await create(rig.manager, rig.target, key="logical-work")
    assert replay is session and receipt["native_session_id"] == native
    with pytest.raises(RuntimeError, match="original native cannot load"):
        await rig.manager.resume_session(session.session_id, allow_recreate=True)
    rig.client.new_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_original_native_cannot_be_created_by_resume(provider_rig):
    rig = provider_rig
    session, _ = await create(rig.manager, rig.target, key="logical-work")
    session.status = SessionStatus.STOPPED
    session.acp_session_id = None
    with pytest.raises(RuntimeError, match="no ACP session ID"):
        await rig.manager.resume_session(session.session_id, allow_recreate=True)
    rig.client.new_session.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["instance", "command", "workspace", "user"])
async def test_cold_resume_refuses_provider_placement_drift_before_spawn(
    provider_rig, monkeypatch, change,
):
    from agent_bridge import session_manager as core

    rig = provider_rig
    session, _ = await create(
        rig.manager, rig.target, key="logical-work", agent_name="container:prepared-original",
    )
    retained = session.target
    saved = rig.db.get_session(session.session_id)["target_json"]
    changed = replace(rig.target, venue=dict(rig.target.venue))
    if change == "instance":
        changed.venue["instance_id"] = "replacement"
    elif change == "command":
        changed.spawn_command = ["provider", "exec", "replacement"]
    elif change == "workspace":
        changed.venue["workspace_folder"] = "/different-workspace"
    else:
        changed.user = "other-user"
    rig.manager.set_resolver(SimpleNamespace(resolve_async=AsyncMock(return_value=changed)))
    session.status = SessionStatus.STOPPED
    session.client = None
    spawn = AsyncMock(side_effect=AssertionError("placement drift must refuse before spawn"))
    monkeypatch.setattr(core, "spawn", spawn)
    with pytest.raises(ProviderTargetRefreshError, match="placement changed"):
        await rig.manager.resume_session(session.session_id)
    spawn.assert_not_awaited()
    rig.client.load_session.assert_not_awaited()
    assert session.target is retained and session.status == SessionStatus.STOPPED
    assert rig.db.get_session(session.session_id)["target_json"] == saved
    rig.client.new_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_successful_cold_resume_uses_guest_workspace_not_host_cwd(provider_rig):
    rig = provider_rig
    session, initial = await create(
        rig.manager, rig.target, key="logical-work", agent_name="container:prepared-original",
    )
    rig.manager.set_resolver(SimpleNamespace(resolve_async=AsyncMock(return_value=rig.target)))
    session.status = SessionStatus.STOPPED
    session.client = None
    resumed = await rig.manager.resume_session(session.session_id, drain=False)
    assert resumed is session and resumed.acp_session_id == initial["native_session_id"]
    assert rig.client.load_session.await_args.kwargs["cwd"] == "/workspace"
    assert resumed.target.cwd is None
    rig.client.new_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_caller_handoff_keeps_creation_worktree_scope(provider_rig):
    rig = provider_rig
    original_ref = "machine/project/worktree#session-one"
    first, _ = await create(
        rig.manager, rig.target, key="logical-work", caller_owner_ref=original_ref,
    )
    second, receipt = await create(
        rig.manager, rig.target, key="logical-work",
        caller_owner_ref="machine/project/worktree#session-two",
    )
    assert second is first and receipt["replayed"]
    assert first.target.caller_owner_ref == original_ref
    with pytest.raises(AcquisitionRefused, match="different provider facts"):
        await create(
            rig.manager, rig.target, key="logical-work",
            caller_owner_ref="machine/project/other-worktree#session-two",
        )
    assert len(rig.manager.list_sessions()) == 1


@pytest.mark.asyncio
async def test_uncertain_launch_is_not_retry_permission(provider_rig, monkeypatch):
    rig = provider_rig
    calls = []

    async def interrupted(*args, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("unknown launch result")

    monkeypatch.setattr(rig.manager, "start_session", interrupted)
    with pytest.raises(RuntimeError):
        await create(rig.manager, rig.target, key="logical-work")
    for _ in range(2):
        with pytest.raises(AcquisitionRefused) as refused:
            await create(rig.manager, rig.target, key="logical-work")
        assert refused.value.code == "creation_pending" and refused.value.effect is None
    assert len(calls) == 1
    assert rig.db.execute_read("SELECT state FROM provider_embodiments")[0]["state"] == "launching"


@pytest.mark.asyncio
async def test_proven_drain_before_launch_can_retry_same_key(provider_rig, monkeypatch):
    rig = provider_rig
    original = rig.manager.start_session

    async def drained(*args, **kwargs):
        raise DaemonDrainingError("session")

    monkeypatch.setattr(rig.manager, "start_session", drained)
    with pytest.raises(AcquisitionRefused) as refused:
        await create(rig.manager, rig.target, key="logical-work")
    assert refused.value.effect == "not_started"
    assert rig.db.execute_read("SELECT state FROM provider_embodiments")[0]["state"] == "prepared"
    monkeypatch.setattr(rig.manager, "start_session", original)
    await create(rig.manager, rig.target, key="logical-work")
    assert len(rig.manager.list_sessions()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["ended", "native", "missing", "placement"])
async def test_retired_or_unproven_native_is_never_replaced(provider_rig, change):
    rig = provider_rig
    session, _ = await create(rig.manager, rig.target, key="logical-work")
    if change == "ended":
        session.status = SessionStatus.ENDED
    elif change == "native":
        session.acp_session_id = "replacement-native"
    elif change == "missing":
        rig.manager._sessions.pop(session.session_id)
    else:
        session.target = replace(session.target, user="0")
    for _ in range(2):
        with pytest.raises(AcquisitionRefused):
            await create(rig.manager, rig.target, key="logical-work")
    assert len(rig.db.execute_read("SELECT * FROM sessions")) == 1
    assert not rig.db.execute_read("SELECT * FROM turns")


@pytest.mark.asyncio
async def test_concurrent_creation_cannot_launch_twice(provider_rig):
    rig = provider_rig
    results = await asyncio.gather(*(
        create(rig.manager, rig.target, key="logical-work") for _ in range(2)
    ), return_exceptions=True)
    assert any(isinstance(result, tuple) for result in results)
    assert all(isinstance(result, (tuple, AcquisitionRefused)) for result in results)
    assert len(rig.manager.list_sessions()) == 1


@pytest.mark.asyncio
async def test_restarted_manager_replays_original_stopped_native(provider_rig, monkeypatch):
    rig = provider_rig
    original, first = await create(rig.manager, rig.target, key="logical-work")
    restarted = SessionManager(rig.db)
    monkeypatch.setattr(restarted, "start_session", lambda *_a, **_k: pytest.fail("must not start"))
    monkeypatch.setattr(restarted, "resume_session", lambda *_a, **_k: pytest.fail("must not resume"))
    retained, replay = await create(restarted, rig.target, key="logical-work")
    assert retained.session_id == original.session_id
    assert retained.acp_session_id == first["native_session_id"] == replay["native_session_id"]
    assert retained.status == SessionStatus.STOPPED and replay["replayed"]
    assert len(rig.db.execute_read("SELECT * FROM sessions")) == 1
    assert not rig.db.execute_read("SELECT * FROM turns")


def test_http_keyed_provider_creation_has_no_fabricated_worktree(provider_rig):
    rig = provider_rig
    app = FastAPI()
    app.state.session_manager = rig.manager

    async def resolve(_name, **_kwargs):
        return replace(rig.target)

    app.state.resolver = SimpleNamespace(resolve_async=resolve)
    app.include_router(router)
    body = {
        "agent": "container:prepared-original", "creation_key": "logical-work",
        "provider_instance": True,
    }
    with TestClient(app) as client:
        initial = client.post("/api/v1/sessions/workspace-creations", json=body)
        replay = client.post("/api/v1/sessions/workspace-creations", json=body)
    assert initial.status_code == replay.status_code == 200, (initial.text, replay.text)
    assert initial.json()["session_id"] == replay.json()["session_id"]
    assert replay.json()["creation"]["replayed"]
    assert "worktree" not in replay.json()["creation"]
    assert rig.manager._active_acquisitions == 0


def test_http_missing_provider_key_refuses_before_caller_affinity(provider_rig):
    rig = provider_rig
    app = FastAPI()
    app.state.session_manager = rig.manager

    async def resolve(_name, **_kwargs):
        return replace(rig.target)

    app.state.resolver = SimpleNamespace(resolve_async=resolve)
    app.include_router(router)
    ordinary = {"agent": "container:prepared-original", "caller_id": "same-caller"}
    with TestClient(app) as client:
        initial = client.post("/api/v1/sessions", json=ordinary)
        assert initial.status_code == 201, initial.text
        refused = client.post("/api/v1/sessions", json={**ordinary, "provider_instance": True})
        assert refused.status_code == 422, refused.text
        assert "provider_instance requires creation_key" in refused.text
    assert len(rig.manager.list_sessions()) == 1
    assert not rig.db.execute_read("SELECT * FROM provider_embodiments")


@pytest.mark.asyncio
async def test_ordinary_keyed_creation_still_refuses_provider_target(provider_rig):
    rig = provider_rig
    with pytest.raises(AcquisitionRefused) as refused:
        await workspace_creation.start(rig.manager, rig.target, key="logical-work")
    assert refused.value.code == "creation_venue_unsupported"
    assert not rig.manager.list_sessions()
    assert not rig.db.execute_read("SELECT * FROM provider_embodiments")


def test_client_refuses_older_provider_creation_before_post(monkeypatch):
    from agent_bridge.protocol import PROVIDER_CREATION_PROTOCOL_VERSION

    client = BridgeClient(base_url="http://127.0.0.1:1", token="test")
    monkeypatch.setattr(client, "daemon_supports", lambda floor: floor < PROVIDER_CREATION_PROTOCOL_VERSION)
    monkeypatch.setattr(client, "_request", lambda *_a, **_k: pytest.fail("must not post"))
    with pytest.raises(BridgeClientError) as refused:
        client.start_session(agent="container:prepared", creation_key="logical-work", provider_instance=True)
    assert refused.value.status == 426
    with pytest.raises(ValueError, match="requires creation_key"):
        client.start_session(agent="container:prepared", provider_instance=True)


def test_client_provider_creation_opt_in_is_explicit(monkeypatch):
    client = BridgeClient(base_url="http://127.0.0.1:1", token="test")
    monkeypatch.setattr(client, "daemon_supports", lambda floor: True)
    calls = []
    monkeypatch.setattr(client, "_request", lambda *args, **kwargs: calls.append(args) or {})
    client.start_session(agent="container:prepared", creation_key="logical-work")
    client.start_session(agent="container:prepared", creation_key="logical-work", provider_instance=True)
    assert "provider_instance" not in calls[0][2]
    assert calls[1][1] == "/api/v1/sessions/workspace-creations"
    assert calls[1][2]["provider_instance"] is True


def test_cli_provider_creation_without_key_never_falls_back(monkeypatch, capsys):
    from agent_bridge.session_targeting_cli import _cmd_create

    with pytest.raises(SystemExit) as refused:
        _cmd_create(SimpleNamespace(provider_instance=True, creation_key=None))
    assert refused.value.code == 2 and "requires --creation-key" in capsys.readouterr().err


def test_cli_provider_receipt_has_native_and_venue_not_workspace(monkeypatch, capsys):
    from agent_bridge import __main__ as core, session_start_cli, session_targeting_cli
    from agent_bridge.protocol import PROVIDER_CREATION_PROTOCOL_VERSION
    from agent_bridge.workspace_creation_cli import create as cli_create

    floors, calls = [], []
    venue = {"instance_id": "original-instance", "provider": "fixture-provider"}
    result = {
        "session_id": "bridge-a", "creation": {
            "status": "complete", "native_session_id": "native-a", "venue": venue,
        },
    }
    monkeypatch.setattr(session_targeting_cli, "_hold_protocol_floor", lambda _client, floor: floors.append(floor))
    monkeypatch.setattr(session_start_cli, "_read_agent_inventory", lambda _client: [])
    monkeypatch.setattr(session_start_cli, "_prepare_sender_preference", lambda *a, **k: SimpleNamespace(project=None))
    monkeypatch.setattr(session_targeting_cli, "_resolve_prompt", lambda *a, **k: None)
    monkeypatch.setattr(session_targeting_cli, "_companion_seed_prompt", lambda prompt: prompt)
    monkeypatch.setattr(core, "_get_caller_session_id", lambda: None)
    monkeypatch.setattr(core, "_worktrees_get", lambda _key: None)
    monkeypatch.setattr(core, "_connection_identity", lambda *a: {"worktree_id": None})
    client = SimpleNamespace(
        start_session=lambda **kwargs: calls.append(kwargs) or result,
        get_session=lambda _sid: {"status": "idle", "turn_count": 0},
    )
    cli_create(SimpleNamespace(
        target="container:prepared", creation_key="logical-work", provider_instance=True, json=True,
    ), client, caller_id=None)
    output = json.loads(capsys.readouterr().out)
    assert floors == [PROVIDER_CREATION_PROTOCOL_VERSION] and calls[0]["provider_instance"] is True
    assert output["ready"] and output["status"] == "awaiting_first_prompt"
    assert output["connection"]["native_session_id"] == "native-a"
    assert output["connection"]["venue"] == venue and output["connection"]["worktree_id"] is None
