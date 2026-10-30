"""Mixed-version receivers must opt in before a managed launch is sent."""

from __future__ import annotations

import argparse
import json
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_bridge import session_lifecycle_cli, session_targeting_cli
from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.client_protocol_floor import protocol_floored_request
from agent_bridge.hosting_admission_cli import (
    HOSTING_CAPABILITY,
    HOSTING_CAPABILITY_VERSION,
    configure_client,
)


class Response:
    def __init__(self, body: dict) -> None:
        self.body = body
        self.status = 200

    def __enter__(self) -> Response:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def read(self) -> bytes:
        return json.dumps(self.body).encode()


def _health(capabilities: object, protocol: int = 47) -> dict:
    return {"protocol_version": protocol, "capabilities": capabilities}


@pytest.mark.parametrize("capabilities", [
    None, {}, [], {HOSTING_CAPABILITY: 0}, {HOSTING_CAPABILITY: True},
    {HOSTING_CAPABILITY: "1"}, {HOSTING_CAPABILITY: 1.0},
])
@pytest.mark.parametrize("command", ["create", "send", "resume"])
def test_cli_refuses_unsupported_daemon_before_mutation(
    monkeypatch: pytest.MonkeyPatch, capabilities: object, command: str,
) -> None:
    client = BridgeClient("http://127.0.0.1:57585", "test", connect_grace=0)
    seen = []

    def urlopen(request: object, **kwargs: object) -> Response:
        seen.append(request.full_url)
        assert request.full_url.endswith("/health")
        return Response(_health(capabilities))

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", urlopen)
    core = SimpleNamespace(
        _get_client=lambda: client, _caller_id_for=lambda args: "caller",
        _json_out=Mock(), _match_agents=Mock(), _startup_request_timeout=lambda **kw: 10,
    )
    monkeypatch.setattr(session_targeting_cli, "_core", lambda: core)
    monkeypatch.setattr(session_lifecycle_cli, "_core", lambda: core)
    args = argparse.Namespace(
        require_agent_hosting=True, target="remote:worker", session_id="stopped",
        prompt="hello", prompt_file=None, new=False,
    )
    # Test the real command's client wiring, not a helper-only substitute.
    expected_error = SystemExit if command == "resume" else BridgeClientError
    with pytest.raises(expected_error) as exc:
        if command == "create":
            session_targeting_cli._cmd_create(args)
        elif command == "send":
            session_targeting_cli._send(args)
        else:
            session_lifecycle_cli._cmd_resume(args)
    if command == "resume":
        assert exc.value.code == 1
    else:
        assert "repository_agent_hosting" in str(exc.value)
    assert seen and all(url.endswith("/health") for url in seen)


@pytest.mark.parametrize("capability_first", [True, False])
@pytest.mark.parametrize("replacement_protocol,replacement_capabilities,expected", [
    (47, {}, "repository_agent_hosting"),
    (46, {HOSTING_CAPABILITY: 1}, "predates protocol 47"),
    (47, {HOSTING_CAPABILITY: 1}, None),
])
def test_every_retry_preserves_both_fences(
    monkeypatch: pytest.MonkeyPatch, capability_first: bool,
    replacement_protocol: int, replacement_capabilities: dict, expected: str | None,
) -> None:
    base, replacement = "http://127.0.0.1:57585", "http://127.0.0.1:57586"
    client = BridgeClient(base, "test", connect_grace=10, reresolve=lambda: replacement)
    monkeypatch.setattr("agent_bridge.client_protocol_floor.time.sleep", lambda _: None)
    args = argparse.Namespace(require_agent_hosting=True)
    if capability_first:
        configure_client(client, args)
    client._request = protocol_floored_request(client, 47)
    if not capability_first:
        configure_client(client, args)
    seen = []

    def urlopen(request: object, **kwargs: object) -> Response:
        seen.append(request.full_url)
        if request.full_url.endswith("/health"):
            return Response(
                _health({HOSTING_CAPABILITY: 1}) if request.full_url.startswith(base)
                else _health(replacement_capabilities, replacement_protocol)
            )
        if request.full_url.startswith(base):
            raise urllib.error.URLError(ConnectionRefusedError("refused"))
        return Response({"session_id": "created"})

    monkeypatch.setattr("agent_bridge.client.urllib.request.urlopen", urlopen)
    if expected:
        with pytest.raises(BridgeClientError, match=expected):
            client._request("POST", "/api/v1/sessions", {"agent": "remote:worker"})
        assert seen == [base + "/health", base + "/api/v1/sessions", replacement + "/health"]
    else:
        assert client._request("POST", "/api/v1/sessions", {}) == {"session_id": "created"}
        assert seen[-2:] == [replacement + "/health", replacement + "/api/v1/sessions"]


@pytest.mark.parametrize("command", ["create", "send", "resume"])
def test_receiver_parsers_accept_mandatory_contract(command: str) -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers()
    if command == "resume":
        session_lifecycle_cli.register_session_lifecycle_commands(sub)
    else:
        session_targeting_cli.register_session_targeting_commands(sub)
    args = parser.parse_args([command, "worker", "--require-agent-hosting"])
    assert args.require_agent_hosting is True


def test_capability_rejects_cli_mode_without_launch(monkeypatch: pytest.MonkeyPatch) -> None:
    launch = Mock(side_effect=AssertionError("must not launch"))
    monkeypatch.setattr(session_targeting_cli, "_cmd_create_cli", launch)
    with pytest.raises(SystemExit) as exc:
        session_targeting_cli._cmd_create(argparse.Namespace(
            require_agent_hosting=True, cli=True, target="remote:worker",
        ))
    assert exc.value.code == 2
    launch.assert_not_called()


@pytest.mark.asyncio
async def test_running_receiver_advertises_implemented_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.routes import health

    monkeypatch.setattr(health, "get_default_manager", lambda: SimpleNamespace(
        carrier_diagnostics=lambda: {},
    ))
    monkeypatch.setattr(health, "config_dir", lambda: None)
    monkeypatch.setattr(health, "slot_descriptor", lambda *args, **kwargs: {})
    result = await health.health(SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace())))
    assert result["capabilities"][HOSTING_CAPABILITY] == HOSTING_CAPABILITY_VERSION
