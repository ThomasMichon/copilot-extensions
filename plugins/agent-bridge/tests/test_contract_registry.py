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
    fixture = _fixture("fixtures/http/current/peer-replies-generation-35.json")
    body = fixture["response"]
    assert body["protocol_version"] == bridge_protocol.HTTP_PROTOCOL_VERSION
    assert (
        body["min_protocol_version"]
        == bridge_protocol.HTTP_PROTOCOL_MIN_SUPPORTED
    )

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


def test_qualified_peer_contract_matches_current_fixture() -> None:
    from agent_bridge.live_peer_address import LivePeerAddress
    from agent_bridge.remote_operations import REMOTE_PEER_REPLY_MIN_VERSION
    from agent_bridge.routes.remote import RemoteLiveMessageRequest

    fixture = _fixture("fixtures/http/current/peer-replies-generation-35.json")
    capability = fixture["capability"]
    assert capability["required_protocol_version"] == bridge_protocol.QUALIFIED_LIVE_PEER_PROTOCOL_VERSION
    assert capability["required_carrier_operation_version"] == REMOTE_PEER_REPLY_MIN_VERSION
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


@pytest.mark.parametrize("owner", ["prompt_models", "routes-prompts"])
def test_native_bound_wire_is_preserved_at_the_new_peer_generation(owner) -> None:
    from agent_bridge.prompt_models import SubmitPromptRequest, SubmitPromptResponse

    previous = _fixture(f"fixtures/http/current/native-bound-extracted-{owner}-generation-33.json")
    current = _fixture(f"fixtures/http/current/native-bound-extracted-{owner}-generation-35.json")
    assert current["request"] == previous["request"]
    assert current["response"] == previous["response"]
    assert current["capability"] == previous["capability"]
    assert current["health"]["protocol_version"] == bridge_protocol.HTTP_PROTOCOL_VERSION
    assert SubmitPromptRequest.model_validate(current["request"]["json"]).model_dump(
        mode="json", exclude_unset=True,
    ) == current["request"]["json"]
    assert SubmitPromptResponse.model_validate(current["response"]).model_dump(
        mode="json",
    ) == current["response"]


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
    result = subprocess.run(
        ["git", "-c", f"safe.directory={REPO}", "-C", str(REPO),
         "show", f"{commit}:{SESSION_HOST_PROTOCOL_PATH}"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    if result.returncode:
        if not any(marker in result.stderr for marker in ("invalid object name", "bad object")):
            result.check_returncode()
        frozen = _fixture("fixtures/session-host/prior-runtime-dev150/protocol-source.json")
        captured = frozen["captured_from"]
        assert captured["commit"] == commit
        assert captured["source_path"] == SESSION_HOST_PROTOCOL_PATH
        historical_source = frozen["source_text"]
        raw = historical_source.encode("utf-8")
        assert hashlib.sha256(raw).hexdigest() == captured["source_sha256"]
        assert hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest() == captured["source_git_blob"]
    else:
        historical_source = result.stdout
    module_name = f"_agent_bridge_protocol_{commit[:12]}"
    module = types.ModuleType(module_name)
    sys.modules[module_name] = module
    exec(compile(historical_source, f"{commit}:{SESSION_HOST_PROTOCOL_PATH}", "exec"), module.__dict__)
    return module


def test_historical_protocol_without_commit_uses_hash_verified_source(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a[0], 128, "", "fatal: invalid object name",
    ))
    captured = _fixture("fixtures/session-host/prior-runtime-dev150/protocol-source.json")
    protocol = _historical_protocol(captured["captured_from"]["commit"])
    assert protocol.PROTOCOL_VERSION == 1


def test_historical_protocol_rejects_corrupted_frozen_source(monkeypatch) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a[0], 128, "", "fatal: invalid object name",
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
