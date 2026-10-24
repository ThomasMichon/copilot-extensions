"""Privileged metadata preserves source authority without replacing receiver identity."""

import asyncio
import copy
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
from acp.schema import InitializeResponse, NewSessionResponse, TextContentBlock
from pydantic import ValidationError

from agent_bridge import elevated, privileged_plugins as module, transport, venue_plugins
from agent_bridge.acp_agent import BridgeAgent
from agent_bridge.acp_client import AcpClient
from agent_bridge.models import SessionStatus
from agent_bridge.session_manager import SessionManager
from agent_bridge.session_preferences import SOURCE_ENV
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
    assert module.capabilities() == {
        "field_meta": {module.META_KEY: {"version": 1, "repository_hosting_version": 1}},
    }


@pytest.mark.parametrize("version", [None, True, "1", 0, 2])
def test_preparation_capability_without_exact_hosting_version_is_not_launch_authority(version):
    client = SimpleNamespace(_initialize_result=InitializeResponse(
        protocol_version=PROTOCOL_VERSION, field_meta={
            module.META_KEY: {"version": 1, "repository_hosting_version": version},
        },
    ))
    with pytest.raises(module.PrivilegedLaunchRefused, match="no session was requested"):
        module._require_capability(client)


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
            protocol_version=PROTOCOL_VERSION, field_meta={
                module.META_KEY: {"version": 1, "repository_hosting_version": 1},
            },
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
@pytest.mark.parametrize("subdirectory", ["", "nested"])
@pytest.mark.parametrize("preference_source", ["caller-settings", "target-settings"])
async def test_real_sdk_metadata_and_receiver_registration(
    tmp_path, monkeypatch, receiver, subdirectory, preference_source,
):
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
    execution = allocated / subdirectory
    execution.mkdir(exist_ok=True)
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
    anchor = tmp_path / "registry-anchor"
    anchor.mkdir()
    target = target_for(anchor)
    target.explicit_cwd = False
    target.elevated = True
    target.env = {"PROFILE": "receiver"}
    allocation = AsyncMock(return_value={"launch": {
        "cmd": [os.sys.executable], "work_dir": str(execution), "worktree_id": "allocated",
        "env": {"PROFILE": "receiver-plan"},
    }})
    monkeypatch.setattr(transport, "_resolve_worktree", allocation)

    async def record(worktree_id, project, **_kwargs):
        assert worktree_id == "allocated" and project == "sample-project"
        return {"id": worktree_id, "repo": project, "path": str(execution)}

    monkeypatch.setattr("agent_bridge.acquisition_authority.read_worktree_record", record)
    resolver = SimpleNamespace(resolve_async=AsyncMock(return_value=target))
    sessions = {}
    request_env = {"REQUEST": "yes", SOURCE_ENV: preference_source}

    async def start(resolved, **kwargs):
        assert kwargs["copilot_args"] == ["--agent", "reviewer"]
        assert kwargs["env_overrides"] == request_env
        resolved.copilot_args += kwargs["copilot_args"]
        argv, cwd, env = await transport.resolve_local_launch(resolved)
        assert cwd == str(execution) == resolved.cwd
        assert env["PROFILE"] == "receiver-plan" and env["REQUEST"] == "yes"
        assert env["HOME"] == str(profile) and env["USERPROFILE"] == str(profile)
        assert argv[-2:] == ["--agent", "reviewer"]
        assert str(child_payload) in argv
        assert (settings / "settings.local.json").is_file()
        downstream_client = await verified_preferences() if preference_source == "target-settings" else None
        session = SimpleNamespace(
            session_id="owned", target=resolved, status=SessionStatus.IDLE, client=downstream_client,
        )
        sessions[session.session_id] = session
        return session

    manager = SimpleNamespace(start_session=AsyncMock(side_effect=start), get_session=sessions.get)
    agent = BridgeAgent(manager, resolver=resolver, default_agent="worker")
    upstream, downstream = memory_transport_pair()
    connection = AgentSideConnection(agent, downstream)
    sdk_client = ClientSideConnection(UpstreamClient(), upstream)
    sender = AcpClient(preference_source=preference_source)
    sender._connection = sdk_client
    sender._resolve_caller_preferences = lambda: {}
    sender._privileged_launch_context = context(
        controller_cwd=str(controller), controller_origin="explicit",
        caller_worktree=str(controller), copilot_args=["--agent", "reviewer"], env=request_env,
    )
    try:
        sender._initialize_result = await sdk_client.initialize(protocol_version=PROTOCOL_VERSION)
        session_id = await asyncio.wait_for(sender.new_session(
            cwd=str(tmp_path / "must-not-select-target"), mcp_servers=[],
        ), timeout=10)
        assert session_id == "owned" and session_id in agent._owned_sessions
        confirmed = module.receipt(sessions[session_id], sender._privileged_launch_context)[module.META_KEY]
        assert confirmed["version"] == 1 and confirmed["digest"] == sender._privileged_launch_context.digest()
        assert sessions[session_id].target.prepared_plugins["cwd"] == str(allocated)
        assert sessions[session_id].target.prepared_plugins["execution_cwd"] == str(execution)
        if preference_source == "target-settings":
            assert sender._preferences_ready is True
            assert sender.confirmed_preferences == confirmed["preferences"] == {
                "model": "target", "reasoning_effort": "high",
            }
        allocation.assert_awaited_once()
        resolver.resolve_async.assert_awaited_once_with("worker")
        await asyncio.wait_for(sender.load_session(
            cwd=str(tmp_path), session_id="owned", mcp_servers=[],
        ), timeout=10)
        assert sender.acp_session_id == "owned"
        if preference_source == "target-settings":
            assert sender._preferences_ready is True
            assert sender.confirmed_preferences["model"] == "target"
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


async def verified_preferences():
    """Use actual preference application/readback, not a mocked ready bit."""
    client = AcpClient(
        preference_source="target-settings",
        target_preferences={
            "status": "resolved", "values": {"model": "target", "reasoning_effort": "high"},
            "sources": {"model": "target-settings", "reasoning_effort": "target-settings"},
        },
    )
    client._acp_session_id = "native"
    options = [
        {"id": "model", "name": "Model", "type": "select", "currentValue": "native",
         "options": [{"value": value, "name": value} for value in ("native", "target")]},
        {"id": "reasoning_effort", "name": "Effort", "type": "select", "currentValue": "low",
         "options": [{"value": value, "name": value} for value in ("low", "high")]},
    ]
    client._connection = MagicMock()

    async def set_option(*, config_id, session_id, value):
        assert session_id == "native"
        result = copy.deepcopy(client._verified_options)
        for option in result:
            if option["id"] == config_id:
                option["currentValue"] = value
        return SimpleNamespace(config_options=result)

    client._connection.set_config_option = AsyncMock(side_effect=set_option)
    await client._apply_model_config(options)
    assert client._connection.set_config_option.await_count == 2
    assert client._preferences_ready is True
    return client


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["unready", "mismatched", "missing-model"])
async def test_receiver_never_attests_unverified_preferences(receiver, invalid):
    client = await verified_preferences()
    if invalid == "unready":
        client._preferences_ready = False
    elif invalid == "mismatched":
        client.confirmed_preferences = {"model": "other"}
    else:
        client._verified_options = []
        client.confirmed_preferences = {}
    with pytest.raises(module.PrivilegedLaunchRefused, match="preference"):
        module.preference_options(SimpleNamespace(client=client), context(env={SOURCE_ENV: "target-settings"}))


@pytest.mark.asyncio
async def test_primary_rejects_unconfirmed_receiver_preferences():
    bound = context(env={SOURCE_ENV: "target-settings"})
    client = AcpClient(preference_source="target-settings")
    client._privileged_launch_context = bound
    client._initialize_result = InitializeResponse(
        protocol_version=PROTOCOL_VERSION, field_meta={
            module.META_KEY: {"version": 1, "repository_hosting_version": 1},
        },
    )
    client._lifecycle_rpc = AsyncMock(return_value=NewSessionResponse(
        session_id="unconfirmed", field_meta={module.META_KEY: {"version": 1, "digest": bound.digest()}},
    ))
    with pytest.raises(module.PrivilegedLaunchRefused, match="preferences"):
        await module.lifecycle_rpc(client, object())
    assert client._preferences_ready is False and client.confirmed_preferences == {}


@pytest.mark.asyncio
@pytest.mark.parametrize("resume_failure", [None, "load", "missing-native"])
async def test_receiver_cold_resume_keeps_binding_through_real_manager(
    tmp_path, monkeypatch, receiver, session_manager, mock_acp_client, resume_failure,
):
    from agent_bridge import session_manager as core

    root = checkout(tmp_path / "receiver")
    payload = marketplace(root)
    settings = root / ".github" / "copilot" / "settings.json"
    declaration = json.loads(settings.read_text())
    declaration["enabledPlugins"] = {"sample@local": True}
    atomic_json(settings, declaration)
    bound = context(controller_cwd=str(root), controller_origin="explicit")
    target = target_for(root)
    target.elevated = True
    target = module.apply_context(target, bound)
    resolver = SimpleNamespace(resolve_async=AsyncMock(side_effect=AssertionError(
        "static privileged metadata is not a provider declaration",
    )))
    session_manager._resolver = resolver

    async def prepare(target, **kwargs):
        argv, cwd, _env = await transport.resolve_local_launch(target)
        assert str(payload) in argv and cwd == str(root)
        return mock_acp_client, "native"

    monkeypatch.setattr(session_manager, "_connect_via_session_host", AsyncMock(side_effect=prepare))
    session = await session_manager.start_session(target, agent_name="worker")
    module.receipt(session, bound)
    await session_manager.stop_session(session.session_id)
    restarted = SessionManager(session_manager.db)
    restarted._resolver = resolver
    current = restarted.get_session(session.session_id)
    assert current is not session and current.target.prepared_plugins is None
    module.require_bound_session(current, bound)
    assert not restarted._provider_backed_target(current.target)
    monkeypatch.setattr(restarted, "_try_reattach_live_host", AsyncMock(return_value=False))

    async def spawn(target, **kwargs):
        await prepare(target)
        process = MagicMock()
        process.proc.pid = 12345
        return process

    monkeypatch.setattr(core, "spawn", AsyncMock(side_effect=spawn))
    monkeypatch.setattr(core, "AcpClient", MagicMock(return_value=mock_acp_client))
    agent = BridgeAgent(restarted, resolver=resolver, default_agent="worker")
    native_id = current.acp_session_id
    if resume_failure is not None:
        monkeypatch.setattr("agent_bridge.session_resume._MAX_RESUME_ROUNDS", 2)
        mock_acp_client.new_session.reset_mock()
        mock_acp_client.send_prompt.reset_mock()
        mock_acp_client.stderr_tail.return_value = ""
        if resume_failure == "load":
            mock_acp_client.load_session.side_effect = ConnectionError("native load unavailable")
            error, message, attempts = ConnectionError, "native load unavailable", 2
        else:
            current.acp_session_id = None
            error, message, attempts = RuntimeError, "no ACP session ID", 0
        with pytest.raises(error, match=message):
            await agent.prompt(
                [TextContentBlock(type="text", text="must not create a replacement")],
                current.session_id,
            )
        assert current.status == SessionStatus.STOPPED
        assert current.acp_session_id == (native_id if resume_failure == "load" else None)
        assert restarted.db.get_session(current.session_id)["acp_session_id"] == native_id
        assert mock_acp_client.load_session.await_count == attempts
        mock_acp_client.new_session.assert_not_awaited()
        mock_acp_client.send_prompt.assert_not_awaited()
        resolver.resolve_async.assert_not_awaited()
        return
    response = await agent.load_session(str(root), session.session_id, **{module.META_KEY: bound.model_dump()})
    assert response.field_meta == module.receipt(current, bound)
    assert current.status == SessionStatus.IDLE
    resolver.resolve_async.assert_not_awaited()
    mock_acp_client.load_session.assert_awaited_once()
    await restarted.stop_session(current.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("startup_reattach,preference_source,missing_proof", [
    (False, "caller-settings", False), (True, "caller-settings", False),
    (False, "target-settings", False), (True, "target-settings", False),
    (False, "target-settings", True),
])
async def test_receiver_restart_recovers_same_surviving_host_without_preparation(
    tmp_path, monkeypatch, receiver, session_manager, startup_reattach, preference_source, missing_proof,
):
    from agent_bridge import session_manager as core
    from agent_bridge.session_host import acp_adapter
    from agent_bridge.session_host.client import SessionHostClient
    from agent_bridge.session_host.host_index import HostRecord

    root = checkout(tmp_path / "receiver")
    marketplace(root)
    bound = context(
        controller_cwd=str(root), controller_origin="explicit",
        env={SOURCE_ENV: preference_source},
    )
    target = target_for(root)
    target.elevated = True
    target = module.apply_context(target, bound)
    original_client = await verified_preferences() if preference_source == "target-settings" else AcpClient()
    original_client._acp_session_id = "native"
    hosts = []

    async def connect(target, **kwargs):
        await transport.resolve_local_launch(target)
        host = HostRecord(
            session_id=kwargs["session_id"], port=1234, host_pid=2345, child_pid=3456,
            nonce="synthetic-same-host",
        )
        hosts.append(host)
        session_manager._host_index.register(host)
        module.remember_host_launch(session_manager.db, target, original_client, host, "native")
        return original_client, "native"

    monkeypatch.setattr(session_manager, "_connect_via_session_host", AsyncMock(side_effect=connect))
    session = await session_manager.start_session(target, agent_name="worker")
    if preference_source == "target-settings":
        original_client._on_event = lambda kind, data: session_manager._route_acp_event(session, kind, data)
        original_client._emit("preference_selected", dict(original_client.confirmed_preferences))
        options = copy.deepcopy(original_client._verified_options)
        options[0]["currentValue"] = "native"
        original_client._remember_preferences(options)
        assert original_client.confirmed_preferences["model"] == "native"
    session_manager.db.flush()
    restarted = SessionManager(session_manager.db)
    current = restarted.get_session(session.session_id)
    assert current.target.prepared_plugins is None and current.status == SessionStatus.STOPPED
    resolve = AsyncMock(side_effect=AssertionError("surviving child must not be prepared or replaced"))
    monkeypatch.setattr(transport, "resolve_local_launch", resolve)
    monkeypatch.setattr(core, "spawn", resolve)
    monkeypatch.setattr(restarted, "_ensure_forward", AsyncMock())
    sock = SimpleNamespace(attach=AsyncMock(), close=AsyncMock())
    streams = SimpleNamespace(
        reader=object(), writer=object(), child_exit_code=None, aclose=AsyncMock(),
    )
    monkeypatch.setattr(SessionHostClient, "connect", AsyncMock(return_value=sock))
    monkeypatch.setattr(acp_adapter, "open_acp_streams", AsyncMock(return_value=streams))
    clients = []

    def client_factory(**kwargs):
        client = AcpClient(**kwargs)
        client.start_streams = AsyncMock()
        client.new_session = AsyncMock(side_effect=AssertionError("must not create a conversation"))
        client.load_session = AsyncMock(side_effect=AssertionError("must not reload a running child"))
        clients.append(client)
        return client

    monkeypatch.setattr(core, "AcpClient", client_factory)

    async def reattach(current):
        return await restarted._reattach_one(hosts[0], current, new_status=SessionStatus.IDLE)

    monkeypatch.setattr(restarted, "_try_reattach_live_host", reattach)
    agent = BridgeAgent(restarted, default_agent="worker")
    if missing_proof:
        current.target.venue[module.CONTEXT_KEY].pop("prepared_launch")
        with pytest.raises(module.PrivilegedLaunchRefused, match="evidence"):
            await agent.load_session(
                str(root), current.session_id, **{module.META_KEY: bound.model_dump()},
            )
        assert current.status == SessionStatus.STOPPED and current.target.prepared_plugins is None
        assert current.acp_session_id == "native"
        assert restarted._host_index.get(current.session_id) is not None
        resolve.assert_not_awaited()
        clients[0].new_session.assert_not_awaited()
        clients[0].load_session.assert_not_awaited()
        return
    if startup_reattach:
        assert await reattach(current)
    result = await agent.load_session(
        str(root), current.session_id, **{module.META_KEY: bound.model_dump()},
    )
    assert current.status == SessionStatus.IDLE and current.acp_session_id == "native"
    assert result.field_meta == module.receipt(current, bound)
    assert current.target.prepared_plugins["execution_cwd"] == str(root)
    assert restarted.db.get_session(current.session_id)["acp_session_id"] == "native"
    assert len(clients) == 1
    sock.attach.assert_awaited_once_with(0, nonce=b"synthetic-same-host")
    resolve.assert_not_awaited()
    clients[0].new_session.assert_not_awaited()
    clients[0].load_session.assert_not_awaited()
    if preference_source == "target-settings":
        assert clients[0].confirmed_preferences == {"model": "native", "reasoning_effort": "high"}
        assert module.preference_options(current, bound) is not None
    await restarted.stop_session(current.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("claim_hosts", [False, True])
async def test_batch_host_recovery_retains_refused_host_and_continues(
    session_manager, monkeypatch, caplog, claim_hosts,
):
    from agent_bridge.session_manager import Session
    from agent_bridge.session_host.host_index import HostRecord

    records = [
        HostRecord(
            session_id=name, port=1234, host_pid=os.getpid(), child_pid=os.getpid(), nonce="synthetic",
        )
        for name in ("refused", "healthy")
    ]
    for host in records:
        session = Session(host.session_id, host.session_id, SpawnTarget(type="local"), "worker")
        session.status = SessionStatus.STOPPED
        session.acp_session_id = "native-" + host.session_id
        session_manager._sessions[host.session_id] = session
        session_manager._host_index.register(host)
    monkeypatch.setattr(session_manager, "_recover_remote_host_records", AsyncMock(return_value=0))
    monkeypatch.setattr(session_manager, "_prune_dead_hosts", MagicMock())
    monkeypatch.setattr(session_manager, "_claim_host_record", MagicMock(return_value=True))
    reap = MagicMock(side_effect=AssertionError("refusal must retain the surviving host"))
    monkeypatch.setattr(session_manager, "_reap_host_record", reap)
    attach = AsyncMock(side_effect=[module.PrivilegedLaunchRefused("missing witness"), True])
    monkeypatch.setattr(session_manager, "_reattach_one", attach)
    assert await session_manager.reattach_session_hosts(claim_hosts=claim_hosts) == 1
    assert [call.args[0].session_id for call in attach.await_args_list] == ["refused", "healthy"]
    assert session_manager._host_index.get("refused") is not None
    assert "refused for refused; retaining its host" in caplog.text
    reap.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["host", "conversation", "digest", "missing"])
async def test_surviving_host_evidence_never_authorizes_another_launch(
    tmp_path, monkeypatch, receiver, session_manager, mismatch,
):
    from agent_bridge.session_host.host_index import HostRecord

    root = checkout(tmp_path / "receiver")
    bound = context(controller_cwd=str(root), controller_origin="explicit")
    target = target_for(root)
    target.elevated = True
    target = module.apply_context(target, bound)
    await transport.resolve_local_launch(target)
    session_manager.db.create_session("owned", "test", "worker", str(root), "local", "stopped", 1)
    host = HostRecord(session_id="owned", port=1, host_pid=2, child_pid=3, nonce="original")
    module.remember_host_launch(session_manager.db, target, None, host, "native")
    restored = SpawnTarget.from_json(target.to_json())
    session = SimpleNamespace(target=restored, acp_session_id="native")
    if mismatch == "host":
        host.nonce = "different"
    elif mismatch == "conversation":
        session.acp_session_id = "different"
    elif mismatch == "digest":
        restored.venue[module.CONTEXT_KEY]["digest"] = "different"
    else:
        restored.venue[module.CONTEXT_KEY].pop("prepared_launch")
    with pytest.raises(module.PrivilegedLaunchRefused, match="evidence"):
        module.restore_host_launch(session, host, None)
    assert restored.prepared_plugins is None


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
        protocol_version=PROTOCOL_VERSION, field_meta={
            module.META_KEY: {"version": 1, "repository_hosting_version": 1},
        },
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
        model="requested", effort="high",
    )
    assert session.status == SessionStatus.IDLE
    original = mock_acp_client._privileged_launch_context
    assert original.controller_origin == "affinity" and original.caller_worktree == "opaque-affinity"
    assert original.copilot_args == ["--request"] and original.env == {"REQUEST": "kept"}
    await session_manager.stop_session(session.session_id)
    previous = session
    session_manager = SessionManager(session_manager.db)
    session = session_manager.get_session(session.session_id)
    assert session is not previous
    assert session.model_override == "requested" and session.effort_override == "high"
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
