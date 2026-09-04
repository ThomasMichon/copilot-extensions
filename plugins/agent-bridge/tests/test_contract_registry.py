"""Immutable fixture assertions for the agent-bridge contract registry."""
from __future__ import annotations

import asyncio
import base64
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
    fixture = _fixture("fixtures/http/current/health.json")
    body = fixture["response"]["json"]
    assert body["protocol_version"] == bridge_protocol.HTTP_PROTOCOL_VERSION
    assert (
        body["min_protocol_version"]
        == bridge_protocol.HTTP_PROTOCOL_MIN_SUPPORTED
    )


def test_http_protocol_constant_fixture_matches_production() -> None:
    fixture = _fixture("fixtures/http/current/protocol-constants.json")
    assert fixture["declared_range"] == {
        "minimum": bridge_protocol.HTTP_PROTOCOL_MIN_SUPPORTED,
        "current": bridge_protocol.HTTP_PROTOCOL_VERSION,
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
    }


def test_health_fixture_matches_route_serialization(monkeypatch) -> None:
    fixture = _fixture("fixtures/http/current/health.json")
    expected = fixture["response"]["json"]

    class CarrierManager:
        def carrier_diagnostics(self):
            return expected["ssh_carriers"]

    monkeypatch.setattr(health_route, "__version__", expected["version"])
    monkeypatch.setattr(health_route, "get_default_manager", CarrierManager)
    request = SimpleNamespace(
        app=SimpleNamespace(
            state=SimpleNamespace(
                ready=False,
                topology_ready=False,
                credential_relay_ready=False,
            )
        )
    )
    assert asyncio.run(health_route.health(request)) == expected


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
    assert request.model_dump(mode="json") == fixture["accepted_model"]
    assert "future_option" not in request.model_fields_set


def test_session_create_response_fixture_matches_model() -> None:
    fixture = _fixture("fixtures/http/current/session-create-response.json")
    expected = fixture["response"]["json"]
    response = StartSessionResponse(
        session_id=expected["session_id"],
        name=expected["name"],
        status=expected["status"],
    )
    assert response.model_dump(mode="json") == expected


def test_representative_error_fixture_matches_route() -> None:
    fixture = _fixture("fixtures/http/current/representative-error.json")
    request = SimpleNamespace(
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
        ["git", "-C", str(REPO), "show", f"{commit}:{SESSION_HOST_PROTOCOL_PATH}"],
        check=True,
        capture_output=True,
        text=True,
        env=environment,
    )
    module_name = f"_agent_bridge_protocol_{commit[:12]}"
    module = types.ModuleType(module_name)
    sys.modules[module_name] = module
    exec(compile(result.stdout, f"{commit}:{SESSION_HOST_PROTOCOL_PATH}", "exec"), module.__dict__)
    return module


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
