"""Native unavailability proves absence only before admission was attempted."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from agent_bridge import __main__ as core
from agent_bridge.client import BridgeClient, BridgeConnectionError
from agent_bridge.native_bound_prompt import submit_native_bound_prompt
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.send_outcome import run_send


def _args() -> Any:
    return core.build_parser().parse_args([
        "--json", "send", "bridge-a", "closeout",
        "--expected-native-session-id", "native-a",
        "--idempotency-key", "original-wake", "--no-wait",
    ])


def _assert_refusal(output: str) -> None:
    refusal = json.loads(output)
    assert refusal == {
        "outcome": "refused_unavailable", "reason": "native_send_not_attempted",
        "retryable": True, "target": "bridge-a",
        "error": refusal["error"], "native_send_refusal_version": 1,
        "admission_status": "not_attempted",
        "native_session_id": "native-a", "invocation_key": "original-wake",
    }
    assert refusal["error"]


@pytest.mark.parametrize("stage", ["ensure", "client-health"])
def test_real_cli_reports_unavailable_before_constructing_native_admission(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stage: str,
) -> None:
    monkeypatch.setattr(core, "_ensure_daemon", lambda: stage != "ensure")
    client = Mock(spec=BridgeClient)
    client.assert_client_supported.side_effect = BridgeConnectionError("fixture offline")
    factory = Mock(return_value=client)
    monkeypatch.setattr(BridgeClient, "from_config", factory)
    with pytest.raises(SystemExit) as refused:
        _args().func(_args())
    assert refused.value.code == 69
    _assert_refusal(capsys.readouterr().out)
    assert factory.call_count == int(stage == "client-health")
    client._request.assert_not_called()
    client.get_session.assert_not_called()


def test_other_commands_keep_existing_unavailable_client_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(core, "_ensure_daemon", lambda: False)
    client = Mock(spec=BridgeClient)
    client.assert_client_supported.side_effect = BridgeConnectionError("fixture offline")
    monkeypatch.setattr(BridgeClient, "from_config", lambda: client)
    assert core._get_client() is client


@pytest.mark.parametrize("stage", ["initial-health", "target-read", "send"])
def test_transport_distinguishes_preflight_from_a_lost_post_response(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], stage: str,
) -> None:
    client = BridgeClient("http://fixture.invalid", "synthetic", connect_grace=0)
    calls: list[tuple[str, str]] = []

    def request(method: str, path: str, *_: Any, **__: Any) -> dict[str, Any]:
        calls.append((method, path))
        if (
            stage == "initial-health"
            or (stage == "target-read" and "/sessions/" in path)
            or method == "POST"
        ):
            raise BridgeConnectionError("fixture connection unavailable")
        if path == "/health":
            return {"protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1}
        return {"session_id": "bridge-a", "acp_session_id": "native-a"}

    monkeypatch.setattr(client, "_request", request)
    args = SimpleNamespace(
        json=True, target="bridge-a", expected_native_session_id="native-a",
        idempotency_key="original-wake",
    )
    with pytest.raises(SystemExit) as result:
        run_send(args, lambda _: submit_native_bound_prompt(
            client, "bridge-a", "closeout", expected_native_session_id="native-a",
            idempotency_key="original-wake", caller_id="fixture",
        ))
    output = capsys.readouterr().out
    posts = [call for call in calls if call[0] == "POST"]
    if stage == "send":
        assert result.value.code == 70 and len(posts) == 1
        outcome = json.loads(output)
        assert outcome["outcome"] == "unknown"
        assert "native_send_refusal_version" not in outcome
    else:
        assert result.value.code == 69 and posts == []
        _assert_refusal(output)
