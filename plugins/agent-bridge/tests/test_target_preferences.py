"""Target preference authority is optional, explicit, and execution-bound."""

import asyncio
import base64
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from acp.schema import ConfigOptionUpdate
from fastapi import HTTPException

from agent_bridge.acp_client import AcpClient
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.models import ServiceConfig, SessionStatus, StartSessionRequest
from agent_bridge.protocol import TARGET_PREFERENCES_PROTOCOL_VERSION
from agent_bridge.routes.sessions import start_session
from agent_bridge.session_host import protocol
from agent_bridge.session_host.client import SessionHostClient
from agent_bridge.session_host.host import SessionHost
from agent_bridge.session_preferences import (
    CONTEXT_ENV, PreferenceApplicationError, SOURCE_ENV, client_preferences, execution_receipt,
    execution_settings, validate_receipt,
)

pytestmark = pytest.mark.contract("agent_bridge.target_preferences")


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for key in (
        "AGENT_BRIDGE_ACP_MODEL", "AGENT_CODESPACES_ACP_MODEL",
        "AGENT_BRIDGE_ACP_EFFORT", "AGENT_CODESPACES_ACP_EFFORT",
        "AGENT_BRIDGE_ACP_CONTEXT", "AGENT_CODESPACES_ACP_CONTEXT",
        "AGENT_BRIDGE_MODEL_PROPAGATE", "AGENT_CODESPACES_MODEL_PROPAGATE",
        "COPILOT_PROVIDER_BASE_URL", "COPILOT_MODEL", "COPILOT_OFFLINE",
    ):
        monkeypatch.delenv(key, raising=False)


def options(context=False):
    values = [
        {"id": "model", "currentValue": "native", "options": [
            {"value": value} for value in ("native", "target", "requested", "local", "saved")
        ]},
        {"id": "reasoning_effort", "currentValue": "low",
         "options": [{"value": value} for value in ("low", "medium", "high")]},
    ]
    if context:
        values.append({"id": "context", "currentValue": "default",
                       "options": [{"value": "default"}, {"value": "long_context"}]})
    return values


def client(**kwargs):
    events = []
    value = AcpClient(
        preference_source="target-settings",
        on_event=lambda kind, data: events.append((kind, data)),
        **kwargs,
    )
    value._connection = MagicMock()

    async def set_option(*, config_id, session_id, value: str):
        response = copy.deepcopy(owner._verified_options)
        for option in response:
            if option["id"] == config_id:
                option["currentValue"] = value
        return SimpleNamespace(config_options=response)

    owner = value
    value._connection.set_config_option = AsyncMock(side_effect=set_option)
    value._acp_session_id = "session"
    return value, events


def calls(value):
    return {c.kwargs["config_id"]: c.kwargs["value"]
            for c in value._connection.set_config_option.call_args_list}


@pytest.mark.parametrize("raw,status", [
    (None, "missing"), ("not json", "error"), ("[]", "error"),
    ('{//comment\n"model":"target","effortLevel":"medium","contextTier":"long_context"}',
     "resolved"),
])
def test_execution_side_settings(monkeypatch, tmp_path, raw, status):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    if raw is not None:
        settings = tmp_path / ".copilot" / "settings.json"
        settings.parent.mkdir()
        settings.write_text(raw, encoding="utf-8")
    receipt = execution_settings(["copilot"], {}, 123)
    assert receipt["status"] == status
    assert validate_receipt(receipt, 123) is None  # Unsealed data is not authority.
    if status == "resolved":
        assert receipt["values"] == {
            "model": "target", "reasoning_effort": "medium", "context": "long_context",
        }


def test_backend_profile_and_opaque_launch(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    receipt = execution_settings(
        ["copilot", "--model", "local", "--reasoning-effort=medium"],
        {"COPILOT_PROVIDER_BASE_URL": "http://localhost:1", "COPILOT_MODEL": "other"}, 123,
    )
    assert receipt["values"] == {"model": "local", "reasoning_effort": "medium"}
    assert set(receipt["sources"].values()) == {"launch-profile"}
    assert execution_receipt(["bash", "-lc", "copilot"], {}, 123)["status"] == "unsupported"
    assert validate_receipt(receipt, 456) is None
    assert validate_receipt({**receipt, "version": 2}, 123) is None


def test_executable_basename_cannot_attest_execution_identity(monkeypatch, tmp_path):
    fake = tmp_path / "copilot"
    fake.write_text("#!/bin/sh\nHOME=/other exec real-copilot\n", encoding="utf-8")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for executable in (str(fake), "copilot", "copilot.exe"):
        receipt = execution_receipt([executable], {}, 123)
        assert receipt["status"] == "unsupported"
        assert receipt["values"] == {}
        assert receipt["reason"] == "unverified-executable-provenance"


def test_unattested_v1_values_and_provider_claims_are_rejected():
    receipt = {"version": 1, "child_pid": 123, "status": "unsupported",
               "values": {"model": "target"}, "sources": {"model": "target-settings"}}
    assert validate_receipt(receipt, 123) is None
    receipt.update(values={}, sources={}, provider_selected=True)
    assert validate_receipt(receipt, 123) is None


@pytest.mark.parametrize("receipt", [
    None, {"version": 1, "child_pid": 123, "status": "missing", "values": {}, "sources": {}},
    {"version": 1, "child_pid": 123, "status": "unsupported", "values": {}, "sources": {}},
    {"version": 1, "child_pid": 999, "status": "missing", "values": {}, "sources": {}},
    b"not-json",
])
def test_hello_extension_and_legacy_peer(receipt):
    async def scenario():
        payload = protocol.pack_u64(7) + protocol.pack_u64(123)
        if receipt is not None:
            payload += receipt if isinstance(receipt, bytes) else json.dumps(receipt).encode()
        reader = asyncio.StreamReader()
        reader.feed_data(protocol.encode(protocol.MsgType.HELLO, payload))
        reader.feed_eof()
        writer = MagicMock()
        writer.drain = AsyncMock()
        hello = await SessionHostClient(reader, writer).attach()
        assert (hello.max_seq, hello.child_pid) == (7, 123)
        assert protocol.unpack_u64(payload[:8]) == 7
        valid = (
            isinstance(receipt, dict) and receipt["child_pid"] == 123
            and receipt["status"] == "unsupported"
        )
        assert hello.preference_receipt == (receipt if valid else None)
    asyncio.run(scenario())


@pytest.mark.parametrize("size", [0, 7, 8, 15])
def test_short_hello_is_a_connection_error(size):
    async def scenario():
        reader = asyncio.StreamReader()
        reader.feed_data(protocol.encode(protocol.MsgType.HELLO, b"\0" * size))
        reader.feed_eof()
        writer = MagicMock()
        writer.drain = AsyncMock()
        with pytest.raises(ConnectionError, match="truncated cursor/PID prefix"):
            await SessionHostClient(reader, writer).attach()
    asyncio.run(scenario())


def test_host_sends_execution_bound_receipt():
    async def scenario():
        stdout = asyncio.StreamReader()
        stdout.feed_eof()
        child = SimpleNamespace(pid=123, returncode=0, stdout=stdout, stdin=None,
                                wait=AsyncMock(return_value=0))
        receipt = {"version": 1, "child_pid": 123, "status": "unsupported",
                   "values": {}, "sources": {}}
        host = SessionHost(child, preference_receipt=receipt)
        sock = None
        try:
            port = await host.serve(port=0)
            sock = await SessionHostClient.connect(port=port)
            hello = await sock.attach()
            assert hello.preference_receipt == receipt
        finally:
            if sock:
                await sock.close()
            await host.close()
    asyncio.run(scenario())


def test_golden_unsealed_settings_are_not_accepted_as_authority():
    async def scenario():
        fixture = json.loads((
            Path(__file__).parents[1] / "contract" / "fixtures" / "session-host"
            / "current" / "messages.json"
        ).read_text(encoding="utf-8"))["hello_preferences"]
        reader = asyncio.StreamReader()
        reader.feed_data(base64.b64decode(fixture["frame_base64"]))
        reader.feed_eof()
        writer = MagicMock()
        writer.drain = AsyncMock()
        hello = await SessionHostClient(reader, writer).attach()
        assert hello.preference_receipt is None
    asyncio.run(scenario())


def test_connection_propagates_host_authority():
    from agent_bridge.session_host_connection import _SessionHostConnectionMixin
    from agent_bridge.transport import SpawnTarget

    async def scenario():
        manager = _SessionHostConnectionMixin()
        manager._db = MagicMock()
        manager._db.execute_read.return_value = []
        manager._timeouts = SimpleNamespace(session_start=1, session_new=1)
        manager._host_index = None
        receipt = {"version": 1, "child_pid": 123, "status": "unsupported",
                   "values": {}, "sources": {}}
        spawned = SimpleNamespace(local_port=1, child_pid=123, nonce="nonce", boundary="test")
        spawner = SimpleNamespace(boundary="test", spawn=AsyncMock(return_value=spawned))
        sock = SimpleNamespace(attach=AsyncMock(return_value=SimpleNamespace(
            preference_receipt=receipt,
        )), close=AsyncMock())
        streams = SimpleNamespace(reader=None, writer=None, aclose=AsyncMock(),
                                  child_exit_code=None)
        fake_client = SimpleNamespace(
            start_streams=AsyncMock(), new_session=AsyncMock(return_value="acp"),
            mark_transport_lost=lambda: None, mark_host_child_exited=lambda *_: None,
        )
        with (
            patch("agent_bridge.session_host.client.SessionHostClient.connect",
                  new=AsyncMock(return_value=sock)),
            patch("agent_bridge.session_host.acp_adapter.open_acp_streams",
                  new=AsyncMock(return_value=streams)),
            patch("agent_bridge.session_manager.AcpClient", return_value=fake_client) as factory,
        ):
            await manager._connect_via_session_host(
                SpawnTarget(type="local", env={SOURCE_ENV: "target-settings"}),
                tracker=MagicMock(), session_id="session", on_acp_event=lambda *_: None,
                permission_callback=None, spawner=spawner, remote_child_argv=["copilot"],
                remote_cwd="workspace",
            )
            assert factory.call_args.kwargs["preference_source"] == "target-settings"
            assert factory.call_args.kwargs["target_preferences"] == receipt
    asyncio.run(scenario())


def test_unequal_caller_target_and_context_capability(monkeypatch):
    monkeypatch.setattr("agent_bridge.acp_client.resolve_acp_model_config",
                        lambda: pytest.fail("target mode must not read caller settings"))
    receipt = {"status": "resolved", "values": {
        "model": "target", "reasoning_effort": "medium", "context": "long_context",
    }, "sources": {}}
    value, events = client(target_preferences=receipt)
    asyncio.run(value._apply_model_config(options(context=True)))
    assert calls(value) == receipt["values"]
    assert value.confirmed_preferences == receipt["values"]
    value, events = client(target_preferences=receipt)
    asyncio.run(value._apply_model_config(options()))
    fallback = next(data for kind, data in events if kind == "model_fallback")
    assert fallback["fallbacks"][0]["reason"] == "not-advertised"
    assert "context" not in value.confirmed_preferences


def test_missing_authority_never_uses_caller_defaults(monkeypatch):
    monkeypatch.setattr("agent_bridge.acp_client.resolve_acp_model_config",
                        lambda: pytest.fail("caller fallback is forbidden"))
    value, events = client()
    with pytest.raises(PreferenceApplicationError):
        asyncio.run(value._apply_model_config(options()))
    assert calls(value) == {}
    assert ("preference_resolution", {
        "source": "target-settings", "status": "unsupported", "reason": "authority-unavailable",
    }) in events
    assert value.confirmed_preferences == {}
    application = next(data for kind, data in events if kind == "preference_application")
    assert application["status"] == "unsupported"
    assert application["effective"]["model"] == "native"
    assert not any(kind == "preference_selected" for kind, _ in events)


def test_explicit_request_and_backend_precedence(monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_ACP_MODEL", "target")
    receipt = {"status": "resolved", "values": {"model": "local"},
               "sources": {"model": "launch-profile"}}
    value, _ = client(target_preferences=receipt)
    asyncio.run(value._apply_model_config(options()))
    assert calls(value)["model"] == "local"
    value, _ = client(target_preferences=receipt, model_override="requested")
    asyncio.run(value._apply_model_config(options()))
    assert calls(value)["model"] == "requested"


def test_unoffered_and_failed_rpc_are_visible():
    value, events = client(model_override="requested")
    advertised = options()
    advertised[0]["options"] = []
    with pytest.raises(PreferenceApplicationError):
        asyncio.run(value._apply_model_config(advertised))
    assert not calls(value)
    assert next(d for k, d in events if k == "model_fallback")["fallbacks"][0][
        "reason"
    ] == "not-offered"
    value, events = client(model_override="native", context_override="long_context")
    value._connection.set_config_option.side_effect = RuntimeError("test failure")
    asyncio.run(value._apply_model_config(options(context=True)))
    assert next(d for k, d in events if k == "model_fallback")["fallbacks"][0][
        "reason"
    ] == "rpc-failed"
    assert "context" not in value.confirmed_preferences


def test_load_and_recreate_preserve_confirmed_choices():
    receipt = {"status": "resolved", "values": {"model": "target"}, "sources": {}}
    for resuming in (True, False):
        value, _ = client(
            target_preferences=receipt, model_override="requested",
            confirmed_preferences={"model": "saved", "reasoning_effort": "high"},
        )
        asyncio.run(value._apply_model_config(options(), resuming=resuming))
        assert calls(value) == {"model": "saved", "reasoning_effort": "high"}
    value, _ = client(target_preferences=receipt, model_override="requested")
    with pytest.raises(PreferenceApplicationError):
        asyncio.run(value._apply_model_config(options(), resuming=True))
    assert calls(value) == {}
    assert value.confirmed_preferences == {}


def test_selected_notification_and_persistence():
    value, events = client()
    changed = options()
    changed[0]["currentValue"] = "saved"
    update = ConfigOptionUpdate.model_construct(config_options=changed)
    value._handle_session_update(update)
    assert not events
    value._preferences_ready = True
    value._handle_session_update(update)
    snapshot = next(data for kind, data in events if kind == "preference_selected")
    assert snapshot["model"] == "saved"
    value.confirmed_preferences["context"] = "long_context"
    value._handle_session_update(update)
    assert "context" not in value.confirmed_preferences
    db = MagicMock()
    db.execute_read.return_value = [{"data_json": json.dumps(snapshot)}]
    target = SimpleNamespace(env={SOURCE_ENV: "target-settings"}, copilot_args=[])
    assert client_preferences(target, db, "session")["confirmed_preferences"] == snapshot
    db.flush.assert_called_once()
    assert "LIMIT 1" in db.execute_read.call_args.args[0]


@pytest.mark.parametrize("failure", [
    "not-offered", "not-advertised", "rpc-failed", "not-confirmed", "readback-unavailable",
])
def test_required_model_failure_never_claims_a_ready_selection(failure):
    value, events = client(model_override="requested")
    offered = options()
    if failure == "not-offered":
        offered[0]["options"] = [{"value": "native"}]
    elif failure == "not-advertised":
        offered = offered[1:]
    elif failure == "rpc-failed":
        value._connection.set_config_option.side_effect = RuntimeError("test failure")
    elif failure == "not-confirmed":
        value._connection.set_config_option.side_effect = None
        value._connection.set_config_option.return_value = SimpleNamespace(config_options=options())
    else:
        value._connection.set_config_option.side_effect = None
        value._connection.set_config_option.return_value = SimpleNamespace()
    with pytest.raises(PreferenceApplicationError):
        asyncio.run(value._apply_model_config(offered))
    assert not value._preferences_ready
    assert not any(kind in {"model_applied", "preference_selected"} for kind, _ in events)
    assert value.confirmed_preferences == {}
    application = next(data for kind, data in events if kind == "preference_application")
    assert application["status"] in {"unsupported", "error"}
    assert application["effective"].get("model") != "requested"


def test_failed_restore_keeps_the_prior_confirmed_intent():
    value, events = client(confirmed_preferences={"model": "saved"})
    value._connection.set_config_option.side_effect = None
    value._connection.set_config_option.return_value = SimpleNamespace(config_options=options())
    with pytest.raises(PreferenceApplicationError):
        asyncio.run(value._apply_model_config(options(), resuming=True))
    assert value.confirmed_preferences == {"model": "saved"}
    assert not any(kind == "preference_selected" for kind, _ in events)


def test_grouped_advertised_model_choice_is_verified():
    value, _ = client(model_override="requested")
    offered = options()
    offered[0]["options"] = [{"group": "choices", "options": [{"value": "requested"}]}]
    asyncio.run(value._apply_model_config(offered))
    assert value.confirmed_preferences["model"] == "requested"


def test_final_readback_cannot_change_model_behind_effort():
    value, events = client(model_override="requested", effort_override="medium")
    original = value._connection.set_config_option.side_effect

    async def reset_model(**kwargs):
        result = await original(**kwargs)
        if kwargs["config_id"] == "reasoning_effort":
            result.config_options[0]["currentValue"] = "native"
        return result

    value._connection.set_config_option.side_effect = reset_model
    with pytest.raises(PreferenceApplicationError):
        asyncio.run(value._apply_model_config(options()))
    assert not any(kind == "preference_selected" for kind, _ in events)
    application = next(data for kind, data in events if kind == "preference_application")
    assert application["effective"]["model"] == "native"


def test_intentional_native_provider_precedes_cloud_environment(monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_ACP_MODEL", "target")
    receipt = {"status": "resolved", "provider_selected": True,
               "values": {"reasoning_effort": "medium"}, "sources": {}}
    value, _ = client(target_preferences=receipt)
    asyncio.run(value._apply_model_config(options()))
    assert calls(value) == {"reasoning_effort": "medium"}
    assert value.confirmed_preferences["model"] == "native"


@pytest.mark.asyncio
@pytest.mark.parametrize("current,expected", [
    ("native", SessionStatus.FAILED), ("requested", SessionStatus.IDLE),
])
async def test_manager_never_readies_an_unverified_model(
    session_manager, monkeypatch, current, expected,
):
    from agent_bridge.transport import SpawnTarget

    process = SimpleNamespace(
        proc=MagicMock(), pid=123, alive=True, kill=AsyncMock(),
    )
    process.kill.side_effect = lambda: setattr(process, "alive", False)
    created = []

    def factory(**kwargs):
        value = AcpClient(**kwargs)
        value.start = AsyncMock()
        value.shutdown = AsyncMock()
        value._connection = MagicMock()
        offered = options()
        offered[0]["currentValue"] = current
        value._connection.new_session = AsyncMock(return_value=SimpleNamespace(
            session_id="acp", config_options=offered,
        ))
        value._connection.set_config_option = AsyncMock(return_value=SimpleNamespace(
            config_options=offered,
        ))
        created.append(value)
        return value

    monkeypatch.setattr("agent_bridge.session_manager.spawn", AsyncMock(return_value=process))
    monkeypatch.setattr("agent_bridge.session_manager.AcpClient", factory)
    session = await session_manager.start_session(
        SpawnTarget(type="command", spawn_command=["fake-provider"],
                    env={SOURCE_ENV: "target-settings"}),
        model="requested",
    )
    assert session.status == expected
    assert created[0]._preferences_ready == (expected == SessionStatus.IDLE)
    events = session_manager.db.get_events(session.session_id)
    selected = [event for event in events if event["event_type"] == "preference_selected"]
    if expected == SessionStatus.FAILED:
        assert not selected
        process.kill.assert_awaited_once()
    else:
        assert selected[-1]["data"]["model"] == "requested"


def test_client_capability_gate_and_legacy_default():
    bridge = BridgeClient("http://localhost:1", "test")
    bridge._daemon_proto = (TARGET_PREFERENCES_PROTOCOL_VERSION - 1, 1)
    with patch.object(bridge, "_request", return_value={}) as request:
        with pytest.raises(BridgeClientError):
            bridge.start_session(preference_source="target-settings")
        request.assert_not_called()
        bridge.start_session()
        assert "preference_source" not in request.call_args.args[2]
        bridge._daemon_proto = (TARGET_PREFERENCES_PROTOCOL_VERSION, 1)
        bridge.start_session(preference_source="target-settings", context="long_context")
        assert request.call_args.args[2]["preference_source"] == "target-settings"
    assert ServiceConfig().preference_source == "caller-settings"


@pytest.mark.parametrize("policy", ["", False, 0, {}, []])
def test_client_rejects_every_explicit_invalid_policy_before_request(policy):
    bridge = BridgeClient("http://localhost:1", "test")
    bridge._daemon_proto = (TARGET_PREFERENCES_PROTOCOL_VERSION - 1, 1)
    with patch.object(bridge, "_request", return_value={}) as request:
        with pytest.raises(ValueError, match="unsupported preference_source"):
            bridge.start_session(preference_source=policy)
        request.assert_not_called()


@pytest.mark.parametrize("policy", ["target-settings", "target-setting", "null", "false", "''"])
def test_invalid_target_configuration_does_not_fall_back(monkeypatch, tmp_path, policy):
    from agent_bridge import config

    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / "config.yaml").write_text(
        f"preference_source: {policy}\nport: not-a-port\n", encoding="utf-8",
    )
    with pytest.raises(ValueError, match="refusing caller-settings fallback"):
        config.load_config()


def test_affinity_reuse_cannot_ignore_explicit_preferences_or_provider():
    from agent_bridge.preference_requests import reused_preference_source

    existing = SimpleNamespace(
        target=SimpleNamespace(env={SOURCE_ENV: "target-settings"}),
        client=SimpleNamespace(
            _preferences_ready=True,
            confirmed_preferences={"model": "saved", "reasoning_effort": "medium"},
        ),
    )
    assert reused_preference_source(
        StartSessionRequest(model="saved", effort="medium"), existing,
    ) == "target-settings"
    for request in (
        StartSessionRequest(model="requested"),
        StartSessionRequest(env={"COPILOT_PROVIDER_BASE_URL": "http://localhost:1"}),
        StartSessionRequest(copilot_args=["--model=requested"]),
    ):
        with pytest.raises(HTTPException) as error:
            reused_preference_source(request, existing)
        assert error.value.status_code == 409
    existing.target.env = {}
    assert reused_preference_source(
        StartSessionRequest(model="requested"), existing,
    ) == "caller-settings"
    for request in (
        StartSessionRequest(context="long_context"),
        StartSessionRequest(env={CONTEXT_ENV: "long_context"}),
    ):
        with pytest.raises(HTTPException) as error:
            reused_preference_source(request, existing)
        assert error.value.status_code == 422


def test_target_propagation_off_keeps_explicit_environment_choices(monkeypatch):
    monkeypatch.setenv("AGENT_BRIDGE_MODEL_PROPAGATE", "0")
    monkeypatch.setenv("AGENT_BRIDGE_ACP_MODEL", "target")
    monkeypatch.setenv("AGENT_BRIDGE_ACP_EFFORT", "medium")
    instance, _ = client()
    asyncio.run(instance._apply_model_config(options()))
    assert calls(instance) == {"model": "target", "reasoning_effort": "medium"}
    assert instance.confirmed_preferences["model"] == "target"


@pytest.mark.parametrize("policy", ["", "target-setting"])
def test_invalid_policy_environment_never_selects_another_authority(policy):
    from agent_bridge.preference_requests import apply_request_preferences, reused_preference_source

    request = StartSessionRequest(env={SOURCE_ENV: policy})
    target = SimpleNamespace(env={})
    state = SimpleNamespace(config=ServiceConfig())
    with pytest.raises(HTTPException) as error:
        apply_request_preferences(request, state, target)
    assert error.value.status_code == 422
    with pytest.raises(HTTPException) as error:
        reused_preference_source(request, SimpleNamespace(target=target))
    assert error.value.status_code == 422


@pytest.mark.parametrize("key,value", [("model", "requested"), ("reasoning_effort", "medium")])
def test_unoffered_already_current_value_cannot_ready_target_mode(key, value):
    instance, events = client(model_override="native" if key != "model" else value,
                              effort_override=value if key == "reasoning_effort" else None)
    offered = options()
    selected = next(item for item in offered if item["id"] == key)
    selected["currentValue"] = value
    selected["options"] = []
    with pytest.raises(PreferenceApplicationError):
        asyncio.run(instance._apply_model_config(offered))
    assert not instance._preferences_ready
    assert not any(kind == "preference_selected" for kind, _ in events)
    applied = [data for kind, data in events if kind == "model_applied"]
    assert not any(key in data for data in applied)


def test_rpc_current_value_must_remain_offered():
    instance, events = client(model_override="requested")
    readback = options()
    readback[0]["currentValue"] = "requested"
    readback[0]["options"] = []
    instance._connection.set_config_option.side_effect = None
    instance._connection.set_config_option.return_value = SimpleNamespace(config_options=readback)
    with pytest.raises(PreferenceApplicationError):
        asyncio.run(instance._apply_model_config(options()))
    assert not instance._preferences_ready
    assert not any(kind == "preference_selected" for kind, _ in events)


def test_route_persists_policy_as_request_owned_env():
    async def scenario():
        mgr = MagicMock()
        mgr.is_draining = False
        mgr.start_session = AsyncMock(return_value=SimpleNamespace(
            session_id="new", name="new", status=SessionStatus.IDLE, caller_session_id=None,
        ))
        request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
            session_manager=mgr, config=ServiceConfig(preference_source="target-settings"),
        )))
        response = await start_session(StartSessionRequest(context="long_context"), request)
        assert response.preference_source == "target-settings"
        call = mgr.start_session.call_args
        target = call.args[0]
        assert target.env[SOURCE_ENV] == "target-settings"
        assert call.kwargs["env_overrides"] == target.env
        with pytest.raises(HTTPException):
            await start_session(StartSessionRequest(
                preference_source="caller-settings", context="long_context",
            ), request)
    asyncio.run(scenario())
