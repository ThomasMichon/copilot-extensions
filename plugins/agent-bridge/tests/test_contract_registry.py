"""Immutable fixture assertions for the agent-bridge contract registry."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import struct
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from agent_bridge import protocol as bridge_protocol
from agent_bridge.models import StartSessionRequest, StartSessionResponse
from agent_bridge.routes import health as health_route
from agent_bridge.routes.sessions import start_session
from agent_bridge.session_host import protocol as host_protocol

pytestmark = pytest.mark.guard

CONTRACT = Path(__file__).resolve().parents[1] / "contract"
REPO = Path(__file__).resolve().parents[3]
SESSION_HOST_PROTOCOL_PATH = (
    "plugins/agent-bridge/src/agent_bridge/session_host/protocol.py"
)
_GIT_ENV_NAMES = {
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_PREFIX",
    "GIT_SUPER_PREFIX",
    "GIT_QUARANTINE_PATH",
    "GIT_NAMESPACE",
    "GIT_CONFIG",
    "GIT_CONFIG_SYSTEM",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_NOSYSTEM",
    "GIT_CONFIG_COUNT",
}


def _fixture(relative: str) -> dict:
    return json.loads((CONTRACT / relative).read_text(encoding="utf-8"))


def test_http_declared_generation_matches_current_fixture() -> None:
    fixture = _fixture("fixtures/http/current/native-worktree-resume-generation-40.json")
    body = fixture["response"]
    assert body["protocol_version"] == bridge_protocol.HTTP_PROTOCOL_VERSION
    assert (
        body["min_protocol_version"]
        == bridge_protocol.HTTP_PROTOCOL_MIN_SUPPORTED
    )


def test_native_resume_does_not_reuse_the_published_receipt_generation() -> None:
    assert bridge_protocol.REMOTE_ACP_PROTOCOL_VERSION == 38
    assert bridge_protocol.LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION == 39
    assert bridge_protocol.WORKTREE_NATIVE_RESUME_PROTOCOL_VERSION == 40
    assert bridge_protocol.HTTP_PROTOCOL_VERSION == 40
    assert bridge_protocol.HTTP_PROTOCOL_MIN_SUPPORTED == 1


@pytest.mark.parametrize("mode", ["acp", "cli"])
def test_native_resume_fixture_selects_mode_qualified_client_route(monkeypatch, mode):
    from agent_bridge.client import BridgeClient
    from unittest.mock import Mock

    fixture = _fixture("fixtures/http/current/native-worktree-resume-generation-40.json")
    assert fixture["capability"]["required_protocol_version"] == bridge_protocol.WORKTREE_NATIVE_RESUME_PROTOCOL_VERSION
    client = BridgeClient("http://localhost:9999", "test")
    monkeypatch.setattr(client, "daemon_supports", lambda version: version <= 40)
    request = Mock(return_value={"durable_session_id": "native-example-head", "mode": mode})
    monkeypatch.setattr(client, "_request", request)
    result = client.resume_worktree("example-worktree", mode=mode)
    request.assert_called_once_with(
        fixture["request"]["method"],
        fixture["request"]["path"].format(mode=mode),
        params=None, request_timeout=None,
    )
    assert result["durable_session_id"] == fixture["identity"]["durable_session_id"]
    assert fixture["capability"]["native_history_replacement"] is False


@pytest.mark.asyncio
async def test_owner_record_fixture_matches_real_cold_lookup(monkeypatch):
    from agent_bridge.agent_registry import AgentConfig
    from agent_bridge.routes import worktree_probe, worktrees
    from unittest.mock import AsyncMock

    fixture = _fixture("fixtures/http/current/worktree-owner-record-generation-40.json")
    owner = AgentConfig(name="example", project="example")
    resolver = SimpleNamespace(agents={"example": owner})
    command = AsyncMock(return_value=json.dumps(fixture["required_owner_record"]))
    monkeypatch.setattr(worktrees, "_run_for_agent", command)
    match = await worktree_probe.probe_live_worktree("example-worktree", resolver)
    assert match is not None
    agent, entry = match
    assert agent == "example" and entry.id == "example-worktree"
    assert entry.path == fixture["required_owner_record"]["worktree"]["path"]
    command.assert_awaited_once_with("example", owner, resolver, fixture["owner_query"])


def test_provider_creation_fixture_requires_explicit_capability() -> None:
    fixture = _fixture("fixtures/http/current/provider-creation-generation-36.json")
    request = StartSessionRequest.model_validate(fixture["request"]["json"])
    assert request.provider_instance and request.creation_key
    assert fixture["capability"]["provider_creation"] == bridge_protocol.PROVIDER_CREATION_PROTOCOL_VERSION
    response = StartSessionResponse.model_validate(fixture["response"])
    assert response.creation["native_session_id"] and response.creation["venue"]["instance_id"]
    assert response.creation["venue"]["capabilities"]["instance_pinned_exec"] is True
    assert "worktree" not in response.creation
    assert StartSessionRequest(creation_key="ordinary").provider_instance is False

def test_workspace_creation_fixture_matches_additive_models_and_capability_floors() -> None:
    fixture = _fixture("fixtures/http/current/workspace-creation-generation-34.json")
    request = StartSessionRequest.model_validate(fixture["request"]["json"])
    assert request.creation_key == "logical-work"
    assert request.creation_prompt is None
    response = StartSessionResponse.model_validate(fixture["response"])
    assert response.creation == fixture["response"]["creation"]
    assert fixture["capability"]["workspace_creation"] == bridge_protocol.WORKSPACE_CREATION_PROTOCOL_VERSION
    assert fixture["capability"]["owner_qualified_acquisition"] == bridge_protocol.ACQUISITION_WORKSPACE_PROTOCOL_VERSION
    assert fixture["capability"]["native_bound_prompt"] == bridge_protocol.NATIVE_BOUND_PROMPT_PROTOCOL_VERSION


@pytest.mark.parametrize("generation", [35, bridge_protocol.HTTP_PROTOCOL_VERSION])
def test_qualified_peer_contract_matches_current_fixture(generation) -> None:
    from agent_bridge.live_peer_address import LivePeerAddress
    from agent_bridge.remote_operations import REMOTE_PEER_REPLY_MIN_VERSION, REMOTE_RECEIPT_REPLY_MIN_VERSION
    from agent_bridge.routes.remote import RemoteLiveMessageRequest

    fixture = _fixture(f"fixtures/http/current/peer-replies-generation-{generation}.json")
    capability = fixture["capability"]
    assert capability["required_protocol_version"] == (
        bridge_protocol.QUALIFIED_LIVE_PEER_PROTOCOL_VERSION if generation == 35
        else bridge_protocol.LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION
    )
    assert capability["required_carrier_operation_version"] == (
        REMOTE_PEER_REPLY_MIN_VERSION if generation == 35 else REMOTE_RECEIPT_REPLY_MIN_VERSION
    )
    assert str(LivePeerAddress.parse(capability["address"])) == capability["address"]
    body = RemoteLiveMessageRequest.model_validate(fixture["request"]["json"])
    assert body.reply_to == capability["address"]
    assert body.wait and body.wait_timeout == 120
    assert body.timeout >= body.wait_timeout + 15


def test_live_registration_fixture_preserves_opt_in_wire_shape_and_default() -> None:
    from agent_bridge.cli_mode_models import RegisterLiveSessionRequest

    fixture = _fixture("fixtures/http/current/live-registration-generation-35.json")
    payload = fixture["request"]["json"]
    model = RegisterLiveSessionRequest.model_validate(payload)
    assert model.model_dump(mode="json", exclude_unset=True) == payload
    assert model.clear_targeting_metadata is True
    ordinary = RegisterLiveSessionRequest(session_id=payload["session_id"])
    assert ordinary.clear_targeting_metadata is fixture["compatibility"]["clear_targeting_metadata_default"]
    assert fixture["compatibility"]["ordinary_null_heartbeat_retains_metadata"] is True


@pytest.mark.parametrize("generation", [35, 36, 37, 38, 39, 40])
@pytest.mark.parametrize("owner", ["prompt_models", "routes-prompts"])
def test_native_bound_wire_is_preserved_at_later_generations(owner, generation) -> None:
    from agent_bridge.prompt_models import SubmitPromptRequest, SubmitPromptResponse

    previous = _fixture(f"fixtures/http/current/native-bound-extracted-{owner}-generation-33.json")
    current = _fixture(f"fixtures/http/current/native-bound-extracted-{owner}-generation-{generation}.json")
    assert current["request"] == previous["request"]
    assert current["response"] == previous["response"]
    assert current["capability"] == previous["capability"]
    assert current["health"]["protocol_version"] == generation
    assert current["health"]["protocol_version"] <= bridge_protocol.HTTP_PROTOCOL_VERSION
    assert SubmitPromptRequest.model_validate(current["request"]["json"]).model_dump(
        mode="json", exclude_unset=True,
    ) == current["request"]["json"]
    assert SubmitPromptResponse.model_validate(current["response"]).model_dump(
        mode="json",
    ) == current["response"]


def test_worktree_render_contract_declares_versioned_opt_in_and_retained_holder() -> None:
    from agent_bridge.routes.worktree_refresh import LIVE_WAIT_SECONDS

    fixture = _fixture("fixtures/http/current/worktree-render-generation-37.json")
    capability = fixture["capability"]
    assert capability["required_protocol_version"] == bridge_protocol.WORKTREE_RENDER_CACHE_PROTOCOL_VERSION
    assert capability["live_wall_wait_seconds"] == LIVE_WAIT_SECONDS
    assert capability["caller_expiry_releases_holder"] is False
    assert capability["legacy_cold_request_blocks"] is True


@pytest.mark.asyncio
@pytest.mark.parametrize("generation", [38, 39, 40])
async def test_remote_acp_route_matches_independent_expected_wire(monkeypatch, generation):
    from agent_bridge import remote_acp_sessions
    from agent_bridge.models import SessionStatus

    fixture = _fixture(f"fixtures/http/current/remote-acp-models-generation-{generation}.json")
    observed = []

    async def connect(manager, name, **kwargs):
        observed.append((name, kwargs))
        return SimpleNamespace(
            session_id="bridge-example", name="example-agent",
            status=SessionStatus.IDLE, caller_session_id=None,
        )

    monkeypatch.setattr(remote_acp_sessions, "connect_remote_session", connect)
    manager = SimpleNamespace(is_draining=False)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(
        session_manager=manager, resolver=None, ready=True,
    )))
    response = await start_session(
        StartSessionRequest.model_validate(fixture["request"]["json"]), request,
    )
    expected = {**fixture["response"], "protocol_version": bridge_protocol.HTTP_PROTOCOL_VERSION}
    assert response.model_dump(mode="json", exclude_none=True) == expected
    assert observed == [("example-agent", {
        "cwd": "/workspace", "caller_id": None, "caller_session_id": None,
        "remote_session_id": "saved-example",
    })]


@pytest.mark.parametrize("generation", [38, 39, 40])
def test_remote_acp_clients_retain_native_request_identity(generation):
    from agent_bridge.client import BridgeClient, BridgeClientError

    fixture = _fixture(f"fixtures/http/current/remote-acp-models-generation-{generation}.json")
    assert fixture["capability"]["remote_acp"] == bridge_protocol.REMOTE_ACP_PROTOCOL_VERSION
    client = BridgeClient.__new__(BridgeClient)
    client._daemon_proto = (generation, 1)
    calls = []
    client._request = lambda *args, **kwargs: calls.append((args, kwargs)) or {}
    client.start_session(**fixture["request"]["json"])
    expected = {**fixture["client_requests"]["start"], "protocol_version": bridge_protocol.HTTP_PROTOCOL_VERSION}
    assert calls[-1][0][2] == expected
    client.answer_ask_user("bridge-example", **fixture["client_requests"]["form"])
    assert calls[-1][1]["body"] == fixture["client_requests"]["form"]
    client.answer_permission("bridge-example", **fixture["client_requests"]["permission"])
    assert calls[-1][1]["body"] == fixture["client_requests"]["permission"]
    calls.clear()
    client._daemon_proto = (37, 1)
    with pytest.raises(BridgeClientError) as refused:
        client.start_session(**fixture["request"]["json"])
    assert refused.value.status == fixture["capability"]["unsupported_status"]
    with pytest.raises(BridgeClientError) as refused:
        client.answer_ask_user("bridge-example", **fixture["client_requests"]["form"])
    assert refused.value.status == fixture["capability"]["unsupported_status"]
    assert calls == []


def test_retirement_recovery_capability_matches_current_fixture() -> None:
    fixture = _fixture("fixtures/http/current/acquisition-protocol.json")
    assert fixture["capability"]["required_protocol_version"] == (
        bridge_protocol.ACQUISITION_RETIREMENT_RECOVERY_PROTOCOL_VERSION
    )
    registry = _fixture("registry.json")
    contract = next(item for item in registry["contracts"] if item["id"] == "agent-bridge.http-wire")
    assert contract["capability_versions"]["acquisition_retirement_recovery"] == (
        bridge_protocol.ACQUISITION_RETIREMENT_RECOVERY_PROTOCOL_VERSION
    )


@pytest.mark.parametrize("generation", [30, 31, 32])
def test_historical_acquisition_fixture_retains_its_generation_and_floor(generation: int) -> None:
    fixture = _fixture(f"fixtures/http/previous-generation-{generation}/acquisition-protocol.json")
    assert fixture["captured_from"]["protocol_generation"] == generation
    assert fixture["response"]["protocol_version"] == generation
    assert fixture["capability"]["required_protocol_version"] == generation


def test_current_workspace_fixture_preserves_original_creation_floor() -> None:
    fixture = _fixture("fixtures/http/current/workspace-creation-generation-39.json")
    assert fixture["capability"]["required_protocol_version"] == bridge_protocol.WORKSPACE_CREATION_PROTOCOL_VERSION


def test_live_message_receipt_generation_and_typed_fixture() -> None:
    from agent_bridge.models import SendMessageRequest, SendMessageResult

    fixture = _fixture("fixtures/http/current/live-message-receipt.json")
    assert fixture["capability"]["required_protocol_version"] == (
        bridge_protocol.LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION
    )
    registry = _fixture("registry.json")
    contract = next(item for item in registry["contracts"] if item["id"] == "agent-bridge.http-wire")
    assert contract["capability_versions"]["live_message_receipt"] == (
        bridge_protocol.LIVE_MESSAGE_RECEIPT_PROTOCOL_VERSION
    )
    assert SendMessageRequest.model_validate(fixture["request"]).model_dump() == fixture["accepted_model"]
    assert SendMessageResult.model_validate(fixture["response"]).model_dump() == fixture["response"]


def test_http_protocol_constant_fixture_matches_production() -> None:
    fixture = _fixture("fixtures/http/current/protocol-constants.json")
    assert fixture["declared_range"] == {
        "minimum": bridge_protocol.HTTP_PROTOCOL_MIN_SUPPORTED,
        "current": fixture["captured_from"]["protocol_generation"],
    }
    assert fixture["capability_versions"] == {
        "relay_interrupt": bridge_protocol.RELAY_INTERRUPT_PROTOCOL_VERSION,
        "failed_acp_handshake": (
            bridge_protocol.FAILED_ACP_HANDSHAKE_PROTOCOL_VERSION
        ),
        "container_recreate": bridge_protocol.CONTAINER_RECREATE_PROTOCOL_VERSION,
        "machine_metadata": bridge_protocol.MACHINE_METADATA_PROTOCOL_VERSION,
        "result_snapshot": bridge_protocol.RESULT_SNAPSHOT_PROTOCOL_VERSION,
        "represented_result_snapshot": (
            bridge_protocol.REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION
        ),
        "provider_target_refresh": (
            bridge_protocol.PROVIDER_TARGET_REFRESH_PROTOCOL_VERSION
        ),
        "at_rest_projection": bridge_protocol.AT_REST_PROJECTION_PROTOCOL_VERSION,
        "attention_wait": bridge_protocol.ATTENTION_WAIT_PROTOCOL_VERSION,
        "remote_operations": bridge_protocol.REMOTE_OPERATIONS_PROTOCOL_VERSION,
        "conditional_idle_end": (
            bridge_protocol.CONDITIONAL_IDLE_END_PROTOCOL_VERSION
        ),
        "remote_event_multiplex": (
            bridge_protocol.REMOTE_EVENT_MULTIPLEX_PROTOCOL_VERSION
        ),
        "remote_commands": bridge_protocol.REMOTE_COMMANDS_PROTOCOL_VERSION,
        "dispatch_task_session": (
            bridge_protocol.DISPATCH_TASK_SESSION_PROTOCOL_VERSION
        ),
        "bare_session_transcript": (
            bridge_protocol.BARE_SESSION_TRANSCRIPT_PROTOCOL_VERSION
        ),
        "remote_session_copilot_args": (
            bridge_protocol.REMOTE_SESSION_COPILOT_ARGS_PROTOCOL_VERSION
        ),
        "live_session_mode": bridge_protocol.LIVE_SESSION_MODE_PROTOCOL_VERSION,
        "cli_mode_unclaimed_release": (
            bridge_protocol.CLI_MODE_UNCLAIMED_RELEASE_PROTOCOL_VERSION
        ),
        "live_session_alias": bridge_protocol.LIVE_SESSION_ALIAS_PROTOCOL_VERSION,
        "agent_roster_cache": bridge_protocol.AGENT_ROSTER_CACHE_PROTOCOL_VERSION,
        "events_before_paging": (
            bridge_protocol.EVENTS_BEFORE_PAGING_PROTOCOL_VERSION
        ),
        "caller_session_id": bridge_protocol.CALLER_SESSION_ID_PROTOCOL_VERSION,
        "cooperative_stop": bridge_protocol.COOPERATIVE_STOP_PROTOCOL_VERSION,
        "codespace_launch_policy": bridge_protocol.CODESPACE_LAUNCH_POLICY_PROTOCOL_VERSION,
        "target_preferences": bridge_protocol.TARGET_PREFERENCES_PROTOCOL_VERSION,
        "strict_resume": bridge_protocol.STRICT_RESUME_PROTOCOL_VERSION,
        "current_attention": bridge_protocol.CURRENT_ATTENTION_PROTOCOL_VERSION,
    }


def test_health_fixture_matches_route_serialization(tmp_path, monkeypatch) -> None:
    fixture = _fixture("fixtures/http/current/health.json")
    expected = fixture["response"]["json"]

    class CarrierManager:
        def carrier_diagnostics(self):
            return expected["ssh_carriers"]

    monkeypatch.setattr(health_route, "__version__", expected["version"])
    monkeypatch.setattr(health_route, "get_default_manager", CarrierManager)
    # slot_descriptor reads the routing table via config_dir() -- point it at
    # an empty directory so the fixture's "slot" value is deterministic
    # (no routing table anywhere -> role "unknown", active/previous None)
    # rather than depending on whatever real ~/.agent-bridge happens to hold
    # on the machine running the test.
    monkeypatch.setattr(health_route, "config_dir", lambda: tmp_path / "no-routing")
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                ready=False,
                topology_ready=False,
                credential_relay_ready=False,
            )
        )
    )
    actual = asyncio.run(health_route.health(request))
    expected["protocol_version"] = bridge_protocol.HTTP_PROTOCOL_VERSION
    # "slot"."pid" is this test process's real pid (slot_descriptor calls
    # os.getpid() directly) -- not fixture-comparable byte-for-byte. Verify it
    # separately, then compare the rest of "slot" (and the whole body) exactly.
    actual_slot = actual.pop("slot")
    expected_slot = expected.pop("slot")
    assert isinstance(actual_slot["pid"], int)
    assert actual_slot == {**expected_slot, "pid": actual_slot["pid"]}
    assert actual == expected


@pytest.mark.parametrize(
    ("relative", "version", "generation"),
    [
        (
            "fixtures/http/previous-generation-11/health.json",
            "0.4.0-dev429",
            11,
        ),
        (
            "fixtures/http/previous-generation-9/health.json",
            "0.4.0-dev423",
            9,
        ),
        (
            "fixtures/http/prior-runtime-dev424/health.json",
            "0.4.0-dev424",
            10,
        ),
    ],
)
def test_historical_health_fixture_preserves_complete_route_shape(
    relative: str,
    version: str,
    generation: int,
) -> None:
    current = _fixture("fixtures/http/current/health.json")["response"]["json"]
    expected = dict(current)
    expected["version"] = version
    expected["protocol_version"] = generation
    historical = _fixture(relative)["response"]["json"]
    assert historical == expected


def test_session_create_request_fixture_proves_unknown_field_tolerance() -> None:
    fixture = _fixture("fixtures/http/current/session-create-request.json")
    request = StartSessionRequest.model_validate(fixture["request"])
    assert request.model_dump(mode="json") == {
        **fixture["accepted_model"], "creation_key": None, "creation_prompt": None,
        "provider_instance": False, "remote_session_id": None,
    }
    assert "future_option" not in request.model_fields_set


def test_session_create_response_fixture_matches_model() -> None:
    fixture = _fixture("fixtures/http/current/session-create-response.json")
    expected = fixture["response"]["json"]
    response = StartSessionResponse(
        session_id=expected["session_id"],
        name=expected["name"],
        status=expected["status"],
        protocol_version=expected["protocol_version"],
    )
    assert response.model_dump(mode="json") == {**expected, "creation": None}


@pytest.mark.asyncio
async def test_agents_list_response_fixture_matches_route_serialization() -> None:
    """The ``agent-roster-cache`` capability (generation 22): with a
    resolver present (topology ready, no empty roster yet scanned/cached),
    ``GET /api/v1/agents`` must produce exactly the registered empty-roster
    shape -- the same default, non-``require_complete`` response shape
    every pre-3b caller already relies on. (A *resolver-absent* daemon is a
    distinct, even more degenerate case -- see
    ``test_list_agents_no_resolver_returns_pre_3b_bare_shape`` in
    ``test_routes.py`` -- covered separately, not by this fixture.)"""
    from agent_bridge.agent_registry import AgentResolver
    from agent_bridge.routes.agents import list_agents

    fixture = _fixture("fixtures/http/current/agents-list-response.json")
    expected = fixture["response"]["json"]
    resolver = AgentResolver({}, {})
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                resolver=resolver, topology_ready=True, agent_roster_cache=None,
            ),
        ),
    )
    actual = await list_agents(request)
    assert actual == expected


def test_representative_error_fixture_matches_route() -> None:
    fixture = _fixture("fixtures/http/current/representative-error.json")
    request = SimpleNamespace(
        url=SimpleNamespace(path="/api/v1/sessions"),
        app=SimpleNamespace(
            state=SimpleNamespace(
                ready=False,
                session_manager=SimpleNamespace(),
            )
        )
    )
    with pytest.raises(HTTPException) as raised:
        asyncio.run(
            start_session(
                StartSessionRequest(agent="example-agent"),
                request,
            )
        )
    assert raised.value.status_code == fixture["response"]["status_code"]
    assert {"detail": raised.value.detail} == fixture["response"]["json"]


def _historical_protocol(commit: str):
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in _GIT_ENV_NAMES
        and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    environment["GIT_NO_LAZY_FETCH"] = "1"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    command = ["git", "-c", f"safe.directory={REPO}", "-C", str(REPO)]
    options = {
        "check": False, "capture_output": True, "text": True, "env": environment,
        "timeout": 10, "creationflags": subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    }
    available = subprocess.run(
        [*command, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}"], **options,
    )
    if available.returncode == 1:
        frozen = _fixture("fixtures/session-host/prior-runtime-dev150/protocol-source.json")
        captured = frozen["captured_from"]
        assert captured["commit"] == commit
        assert captured["source_path"] == SESSION_HOST_PROTOCOL_PATH
        historical_source = frozen["source_text"]
        raw = historical_source.encode("utf-8")
        assert hashlib.sha256(raw).hexdigest() == captured["source_sha256"]
        assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == captured["source_git_blob"]
    else:
        available.check_returncode()
        result = subprocess.run(
            [*command, "show", f"{commit}:{SESSION_HOST_PROTOCOL_PATH}"], **options,
        )
        result.check_returncode()
        historical_source = result.stdout
    module_name = f"_agent_bridge_protocol_{commit[:12]}"
    module = types.ModuleType(module_name)
    sys.modules[module_name] = module
    exec(compile(historical_source, f"{commit}:{SESSION_HOST_PROTOCOL_PATH}", "exec"), module.__dict__)
    return module


def test_historical_protocol_without_commit_uses_hash_verified_source(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a[0], 1, "", "",
    ))
    captured = _fixture("fixtures/session-host/prior-runtime-dev150/protocol-source.json")
    protocol = _historical_protocol(captured["captured_from"]["commit"])
    assert protocol.PROTOCOL_VERSION == 1


def test_historical_protocol_rejects_corrupted_frozen_source(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a[0], 1, "", "",
    ))
    captured = _fixture("fixtures/session-host/prior-runtime-dev150/protocol-source.json")
    captured["source_text"] += "\n"
    monkeypatch.setattr(sys.modules[__name__], "_fixture", lambda _: captured)
    with pytest.raises(AssertionError):
        _historical_protocol(captured["captured_from"]["commit"])


def test_historical_protocol_preserves_unrelated_git_failures(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a[0], 128, "", "fatal: failed to read repository",
    ))
    with pytest.raises(subprocess.CalledProcessError):
        _historical_protocol("4ed08dcdd0e72377b95a67d6bd22aec819bf7fec")


def test_available_historical_commit_with_missing_source_never_uses_frozen_fallback(monkeypatch):
    def git(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0 if "rev-parse" in command else 128, "",
            "" if "rev-parse" in command else "fatal: path missing from existing commit",
        )

    monkeypatch.setattr(subprocess, "run", git)
    with pytest.raises(subprocess.CalledProcessError):
        _historical_protocol("4ed08dcdd0e72377b95a67d6bd22aec819bf7fec")


def _message_frames(protocol) -> dict[str, dict[str, str]]:
    u32 = struct.Struct(">I")
    messages = {
        "attach": (
            protocol.MsgType.ATTACH,
            protocol.pack_attach(7, b"nonce"),
        ),
        "ack": (protocol.MsgType.ACK, protocol.pack_u64(7)),
        "write": (protocol.MsgType.WRITE, b'{"jsonrpc":"2.0"}\n'),
        "terminate": (protocol.MsgType.TERMINATE, b""),
        "status": (protocol.MsgType.STATUS, protocol.pack_flag(True)),
        "detach": (protocol.MsgType.DETACH, protocol.pack_flag(False)),
        "hello": (
            protocol.MsgType.HELLO,
            protocol.pack_u64(11) + protocol.pack_u64(4242),
        ),
        "frame": (
            protocol.MsgType.FRAME,
            protocol.pack_frame(12, b'{"type":"result"}\n'),
        ),
        "liveness": (
            protocol.MsgType.LIVENESS,
            protocol.pack_liveness(False, 7),
        ),
        "unknown": (None, b"future"),
    }
    if hasattr(protocol.MsgType, "ATTACH_PRIVATE"):
        messages["attach_private"] = (
            protocol.MsgType.ATTACH_PRIVATE, b"synthetic-native-ticket",
        )
    result = {}
    for name, (message_type, payload) in messages.items():
        type_bytes = message_type.value if message_type is not None else b"Z"
        frame = (
            protocol.encode(message_type, payload)
            if message_type is not None
            else u32.pack(1 + len(payload)) + type_bytes + payload
        )
        result[name] = {
            "type_base64": base64.b64encode(type_bytes).decode("ascii"),
            "payload_base64": base64.b64encode(payload).decode("ascii"),
            "frame_base64": base64.b64encode(frame).decode("ascii"),
        }
    return result


@pytest.mark.parametrize(
    ("relative", "historical_commit"),
    [
        ("fixtures/session-host/current/messages.json", None),
        (
            "fixtures/session-host/prior-runtime-dev150/messages.json",
            "4ed08dcdd0e72377b95a67d6bd22aec819bf7fec",
        ),
    ],
)
def test_session_host_fixture_matches_generation_one(
    relative: str,
    historical_commit: str | None,
) -> None:
    fixture = _fixture(relative)
    protocol = (
        _historical_protocol(historical_commit)
        if historical_commit is not None
        else host_protocol
    )
    assert fixture["captured_from"]["protocol_generation"] == (
        protocol.PROTOCOL_VERSION
    )
    assert fixture["messages"] == _message_frames(protocol)


def test_session_host_version_mux_fixture_matches_production() -> None:
    from agent_bridge.session_host import version_mux

    fixture = _fixture("fixtures/session-host/current/version-mux.json")
    assert fixture["supported_protocol_versions"] == sorted(
        version_mux.SUPPORTED_PROTOCOL_VERSIONS
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "relative",
    [
        "fixtures/session-host/current/messages.json",
        "fixtures/session-host/prior-runtime-dev150/messages.json",
    ],
)
async def test_session_host_fixture_frames_decode(relative: str) -> None:
    fixture = _fixture(relative)
    for name, encoded_message in fixture["messages"].items():
        reader = asyncio.StreamReader()
        reader.feed_data(base64.b64decode(encoded_message["frame_base64"]))
        reader.feed_eof()
        message_type, payload = await host_protocol.read_message(reader)
        if name == "unknown":
            assert message_type is None
        else:
            assert message_type is host_protocol.MsgType[name.upper()]
        assert payload == base64.b64decode(encoded_message["payload_base64"])
