"""Privileged metadata preserves source authority without replacing receiver identity."""

import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp import PROTOCOL_VERSION, Client
from acp._transport import memory_transport_pair
from acp.agent.connection import AgentSideConnection
from acp.client.connection import ClientSideConnection
from acp.exceptions import RequestError
from acp.schema import InitializeResponse, NewSessionResponse
from pydantic import ValidationError

from agent_bridge import elevated, privileged_plugins as module, transport, venue_plugins
from agent_bridge.acp_agent import BridgeAgent
from agent_bridge.acp_client import AcpClient
from agent_bridge.models import SessionStatus
from agent_bridge.spawn_target import REQUEST_OVERRIDES_KEY, SpawnTarget
from plugin_resolve.registration import atomic_json
from test_local_plugin_preparation import checkout, marketplace, target_for


class UpstreamClient(Client):
    pass


@pytest.fixture
def receiver(monkeypatch):
    monkeypatch.setattr(elevated, "is_process_elevated", lambda: True)
    monkeypatch.setattr(elevated, "is_subdaemon", lambda: True)
    monkeypatch.setattr("agent_bridge.related_plugins.control_plane_anchors", lambda: [])


def context(**values):
    return module.PrivilegedContext(
        **{"version": 1, "agent": "worker", "controller_cwd": None,
           "controller_origin": "affinity", **values},
    )


@pytest.mark.parametrize("version", [True, "1", 0, 2, None])
def test_context_version_is_explicit_and_strict(version):
    with pytest.raises(ValidationError):
        context(version=version)


def test_unknown_context_fields_are_tolerated_without_changing_identity():
    raw = context().model_dump()
    assert module.PrivilegedContext.model_validate({**raw, "future": "ignored"}).digest() == context().digest()
    raw.pop("version")
    with pytest.raises(ValidationError):
        module.PrivilegedContext.model_validate(raw)


def test_capability_is_actual_receiver_only(monkeypatch):
    monkeypatch.setattr(elevated, "is_subdaemon", lambda: False)
    assert module.capabilities() == {}
    monkeypatch.setattr(elevated, "is_subdaemon", lambda: True)
    assert module.capabilities() == {"field_meta": {module.META_KEY: {"version": 1}}}


@pytest.mark.parametrize("change", [
    {"version": True}, {"agent": "different"},
    {"controller_origin": "explicit"}, {"controller_cwd": "not-absolute"},
    {"env": {"private": "x" * (256 * 1024)}},
])
def test_invalid_context_is_redacted_before_target_resolution(receiver, change):
    raw = {**context().model_dump(), **change}
    with pytest.raises(RequestError) as error:
        module.read_context({module.META_KEY: raw}, "worker")
    assert "private" not in str(error.value) and "not-absolute" not in str(error.value)


def test_ordinary_metadata_and_unprivileged_refusal(monkeypatch):
    assert module.read_context({"unrelated": {}}, "worker") is None
    monkeypatch.setattr(elevated, "is_subdaemon", lambda: False)
    with pytest.raises(RequestError):
        module.read_context({module.META_KEY: context().model_dump()}, "worker")


@pytest.mark.asyncio
async def test_old_peer_refused_before_session_request(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "relay_agent", lambda target: "worker")
    monkeypatch.setattr(venue_plugins, "resolve_controller_cwd", AsyncMock(return_value=str(tmp_path)))
    target = SpawnTarget(type="command", elevated=True)
    session = SimpleNamespace(
        target=target, caller_id="affinity", caller_session_id="caller-session",
        model_override=None, effort_override=None,
    )
    client = SimpleNamespace(
        start=AsyncMock(), _initialize_result=InitializeResponse(protocol_version=PROTOCOL_VERSION),
        _lifecycle_rpc=AsyncMock(),
    )
    with pytest.raises(module.PrivilegedLaunchRefused, match="no session was requested"):
        await module.start_client(client, object(), session)
    client.start.assert_awaited_once()
    client._lifecycle_rpc.assert_not_awaited()


@pytest.mark.asyncio
async def test_sender_only_forwards_request_owned_values(monkeypatch, tmp_path):
    from agent_bridge.session_preferences import CONTEXT_ENV, SOURCE_ENV

    monkeypatch.setattr(module, "relay_agent", lambda target: "worker")
    lookup = AsyncMock(return_value=str(tmp_path))
    monkeypatch.setattr(venue_plugins, "resolve_controller_cwd", lookup)
    monkeypatch.setenv("PRIVATE_AMBIENT", "do-not-forward")
    target = SpawnTarget(
        type="command", elevated=True, caller_worktree="affinity",
        caller_owner_ref="machine/project/worktree#session:caller-session",
        env={"UNRELATED_CONFIG": "not-a-request", SOURCE_ENV: "target-settings", CONTEXT_ENV: "long"},
        venue={REQUEST_OVERRIDES_KEY: {
            "controller_worktree_origin": "affinity", "copilot_args": ["--agent", "reviewer"],
            "env": {"REQUEST": "value"}, "mcp_servers": [],
        }},
    )
    session = SimpleNamespace(
        target=target, caller_id="affinity", caller_session_id="caller-session",
        model_override="selected", effort_override="high",
    )
    client = SimpleNamespace(
        start=AsyncMock(), _initialize_result=InitializeResponse(
            protocol_version=PROTOCOL_VERSION, **module.capabilities(),
        ),
    )
    monkeypatch.setattr(elevated, "is_subdaemon", lambda: True)
    client._initialize_result = InitializeResponse(
        protocol_version=PROTOCOL_VERSION, **module.capabilities(),
    )
    await module.start_client(client, object(), session)
    bound = client._privileged_launch_context
    assert bound.controller_cwd == str(tmp_path) and bound.controller_origin == "affinity"
    assert bound.caller_worktree == "affinity" and bound.caller_owner_ref == target.caller_owner_ref
    assert bound.env == {
        "REQUEST": "value", SOURCE_ENV: "target-settings", CONTEXT_ENV: "long",
    }
    assert bound.copilot_args == ["--agent", "reviewer"]
    assert bound.model == "selected" and bound.effort == "high" and bound.mcp_servers == []
    lookup.assert_awaited_once_with(target, caller_session_id="caller-session")
    serialized = json.dumps(bound.model_dump())
    assert "PRIVATE_AMBIENT" not in serialized and "UNRELATED_CONFIG" not in serialized


@pytest.mark.asyncio
@pytest.mark.parametrize("receipt", [None, {}, {"version": True}, {"version": 1, "digest": "other"}])
async def test_missing_or_mismatched_receipt_cannot_authorize_delivery(receipt):
    client = SimpleNamespace(
        _privileged_launch_context=context(),
        _initialize_result=InitializeResponse(
            protocol_version=PROTOCOL_VERSION, field_meta={module.META_KEY: {"version": 1}},
        ),
        _lifecycle_rpc=AsyncMock(return_value=NewSessionResponse(
            session_id="unconfirmed", field_meta={module.META_KEY: receipt},
        )),
    )
    with pytest.raises(module.PrivilegedLaunchRefused):
        await module.lifecycle_rpc(client, object(), cwd=os.path.abspath(os.curdir))
    assert client._lifecycle_rpc.await_count == 1


@pytest.mark.asyncio
async def test_ordinary_lifecycle_keeps_original_packet():
    method, result = object(), object()
    client = SimpleNamespace(_lifecycle_rpc=AsyncMock(return_value=result))
    assert await module.lifecycle_rpc(client, method, cwd="original", mcp_servers=[]) is result
    client._lifecycle_rpc.assert_awaited_once_with(method, cwd="original", mcp_servers=[])


@pytest.mark.asyncio
async def test_real_sdk_metadata_and_receiver_registration(tmp_path, monkeypatch, receiver):
    from agent_bridge.spawn_target import PluginRef

    controller, allocated = tmp_path / "controller", checkout(tmp_path / "allocated")
    profile = tmp_path / "receiver-profile"
    global_settings = profile / ".copilot" / "settings.json"
    global_settings.parent.mkdir(parents=True)
    atomic_json(global_settings, {"sentinel": "preserve"})
    global_before = global_settings.read_bytes()
    monkeypatch.setenv("HOME", str(profile))
    monkeypatch.setenv("USERPROFILE", str(profile))
    marketplace(controller, "controller")
    child_payload = marketplace(allocated, "child")
    settings = allocated / ".github" / "copilot"
    declared = json.loads((settings / "settings.json").read_text())
    declared["enabledPlugins"] = {"sample@local": True}
    atomic_json(settings / "settings.json", declared)
    monkeypatch.setattr(
        "agent_bridge.related_plugins.related_plugins_for_repo",
        lambda repo, anchors: [PluginRef("sample@local")],
    )
    monkeypatch.setattr(venue_plugins, "resolve_controller_cwd", AsyncMock(side_effect=AssertionError(
        "receiver must not reinterpret sender ownership in its own registry",
    )))
    target = target_for(tmp_path / "registry-anchor")
    target.explicit_cwd = False
    target.elevated = True
    target.env = {"PROFILE": "receiver"}
    allocation = AsyncMock(return_value={"launch": {
        "cmd": [os.sys.executable], "work_dir": str(allocated), "worktree_id": "allocated",
        "env": {"PROFILE": "receiver-plan"},
    }})
    monkeypatch.setattr(transport, "_resolve_worktree", allocation)
    resolver = SimpleNamespace(resolve_async=AsyncMock(return_value=target))
    sessions = {}

    async def start(resolved, **kwargs):
        assert kwargs["copilot_args"] == ["--agent", "reviewer"]
        assert kwargs["env_overrides"] == {"REQUEST": "yes"}
        resolved.copilot_args += kwargs["copilot_args"]
        argv, cwd, env = await transport.resolve_local_launch(resolved)
        assert cwd == str(allocated) == resolved.cwd
        assert env["PROFILE"] == "receiver-plan" and env["REQUEST"] == "yes"
        assert env["HOME"] == str(profile) and env["USERPROFILE"] == str(profile)
        assert argv[-2:] == ["--agent", "reviewer"]
        assert str(child_payload) in argv
        assert (settings / "settings.local.json").is_file()
        session = SimpleNamespace(session_id="owned", target=resolved, status=SessionStatus.IDLE)
        sessions[session.session_id] = session
        return session

    manager = SimpleNamespace(start_session=AsyncMock(side_effect=start), get_session=sessions.get)
    agent = BridgeAgent(manager, resolver=resolver, default_agent="worker")
    upstream, downstream = memory_transport_pair()
    connection = AgentSideConnection(agent, downstream)
    sdk_client = ClientSideConnection(UpstreamClient(), upstream)
    sender = AcpClient()
    sender._connection = sdk_client
    sender._apply_model_config = AsyncMock()
    sender._privileged_launch_context = context(
        controller_cwd=str(controller), controller_origin="explicit",
        caller_worktree=str(controller), copilot_args=["--agent", "reviewer"], env={"REQUEST": "yes"},
    )
    try:
        sender._initialize_result = await sdk_client.initialize(protocol_version=PROTOCOL_VERSION)
        session_id = await asyncio.wait_for(sender.new_session(
            cwd=str(tmp_path / "must-not-select-target"), mcp_servers=[],
        ), timeout=10)
        assert session_id == "owned" and session_id in agent._owned_sessions
        assert module.receipt(sessions[session_id], sender._privileged_launch_context) == {
            module.META_KEY: {"version": 1, "digest": sender._privileged_launch_context.digest()},
        }
        allocation.assert_awaited_once()
        resolver.resolve_async.assert_awaited_once_with("worker")
        await asyncio.wait_for(sender.load_session(
            cwd=str(tmp_path), session_id="owned", mcp_servers=[],
        ), timeout=10)
        assert sender.acp_session_id == "owned"
        current = sessions["owned"]
        current.target = SpawnTarget.from_json(current.target.to_json())
        assert current.target.prepared_plugins is None

        async def resume(session_id, **kwargs):
            assert session_id == "owned" and kwargs["allow_recreate"] is False
            await transport.resolve_local_launch(current.target)
            current.status = SessionStatus.IDLE
            return current

        manager.resume_session = AsyncMock(side_effect=resume)
        with pytest.raises(module.PrivilegedLaunchRefused):
            await sender.load_session(cwd=str(tmp_path), session_id="owned")
        manager.resume_session.assert_not_awaited()
        current.status = SessionStatus.STOPPED
        await asyncio.wait_for(sender.load_session(
            cwd=str(tmp_path), session_id="owned", mcp_servers=[],
        ), timeout=10)
        manager.resume_session.assert_awaited_once()
        assert current.status == SessionStatus.IDLE and sender.acp_session_id == "owned"
        assert module.receipt(current, sender._privileged_launch_context)
        assert global_settings.read_bytes() == global_before
    finally:
        await upstream.close()
        await downstream.close()
        await asyncio.wait_for(connection.listen(), timeout=5)


@pytest.mark.asyncio
async def test_legacy_incumbent_is_refused_before_resume(receiver, tmp_path):
    target = target_for(tmp_path)
    target.elevated = True
    session = SimpleNamespace(target=target, status=SessionStatus.STOPPED)
    manager = SimpleNamespace(get_session=lambda sid: session, resume_session=AsyncMock())
    agent = BridgeAgent(manager, default_agent="worker")
    with pytest.raises(module.PrivilegedLaunchRefused, match="legacy"):
        await agent.load_session(
            str(tmp_path), "old", **{module.META_KEY: context().model_dump()},
        )
    manager.resume_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_false_elevation_and_gitless_targets_never_spawn(tmp_path, monkeypatch):
    target = target_for(tmp_path)
    target.elevated = True
    child = AsyncMock()
    monkeypatch.setattr(transport.asyncio, "create_subprocess_exec", child)
    monkeypatch.setattr(elevated, "is_process_elevated", lambda: False)
    with pytest.raises(module.PrivilegedLaunchRefused):
        await transport.spawn_local(target)
    monkeypatch.setattr(elevated, "is_process_elevated", lambda: True)
    with pytest.raises(RuntimeError, match="repository-backed"):
        await transport.spawn_local(target)
    child.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,attempts", [
    (module.PrivilegedLaunchRefused("legacy context"), 1), (ConnectionError("relay lost"), 2),
])
async def test_parent_provenance_survives_restart_and_refusal_never_recreates(
    session_manager, monkeypatch, tmp_path, mock_acp_client, failure, attempts,
):
    from agent_bridge import session_manager as core

    monkeypatch.setattr(elevated, "is_process_elevated", lambda: False)
    monkeypatch.setattr("agent_bridge.session_resume._MAX_RESUME_ROUNDS", 2)
    monkeypatch.setattr(venue_plugins, "resolve_controller_cwd", AsyncMock(return_value=str(tmp_path)))
    process = MagicMock()
    process.proc.pid = 12345
    spawned = AsyncMock(return_value=process)
    monkeypatch.setattr(core, "spawn", spawned)
    mock_acp_client._initialize_result = InitializeResponse(
        protocol_version=PROTOCOL_VERSION, field_meta={module.META_KEY: {"version": 1}},
    )
    monkeypatch.setattr(core, "AcpClient", MagicMock(return_value=mock_acp_client))
    target = SpawnTarget(
        type="command", elevated=True, cwd=str(tmp_path),
        env={"REQUEST": "kept"},
        spawn_command=["synthetic", "acp-connect", "ws://127.0.0.1:1/acp/worker", "--stdio"],
    )
    session = await session_manager.start_session(
        target, agent_name="worker", caller_id="opaque-affinity", caller_session_id="caller-session",
        copilot_args=["--request"], env_overrides={"REQUEST": "kept"}, mcp_servers=[],
    )
    assert session.status == SessionStatus.IDLE
    original = mock_acp_client._privileged_launch_context
    assert original.controller_origin == "affinity" and original.caller_worktree == "opaque-affinity"
    assert original.copilot_args == ["--request"] and original.env == {"REQUEST": "kept"}
    await session_manager.stop_session(session.session_id)
    session.target = SpawnTarget.from_json(session_manager.db.get_session(session.session_id)["target_json"])
    request = session.target.venue[REQUEST_OVERRIDES_KEY]
    assert request["controller_worktree_origin"] == "affinity"
    assert request["copilot_args"] == ["--request"] and request["env"] == {"REQUEST": "kept"}
    assert request["mcp_servers"] == []
    monkeypatch.setattr(session_manager, "_try_reattach_live_host", AsyncMock(return_value=False))
    monkeypatch.setattr(session_manager, "_resume_via_new_remote_host", AsyncMock(return_value=None))
    mock_acp_client.load_session.side_effect = failure
    mock_acp_client.stderr_tail.return_value = ""
    mock_acp_client.new_session.reset_mock()
    mock_acp_client.shutdown.reset_mock()
    spawned.reset_mock()
    with pytest.raises(type(failure), match="legacy|relay lost"):
        await session_manager.resume_session(session.session_id, allow_recreate=True)
    assert session.status == SessionStatus.STOPPED and spawned.await_count == attempts
    mock_acp_client.new_session.assert_not_awaited()
    assert mock_acp_client.shutdown.await_count == attempts
    assert mock_acp_client._privileged_launch_context.digest() == original.digest()


@pytest.mark.parametrize("kind", ["command", "ssh", "missing-provenance", "wrapped", "container"])
def test_context_does_not_promote_other_execution_owners(receiver, tmp_path, kind):
    target = target_for(tmp_path)
    target.elevated = True
    if kind in {"command", "ssh"}:
        target.type = kind
    elif kind == "missing-provenance":
        target.static_plugin_args = None
    elif kind == "wrapped":
        target.spawn_command = ["wrapper"]
    else:
        target.container = {"name": "not-local"}
    with pytest.raises(module.PrivilegedLaunchRefused):
        module.apply_context(target, context())
