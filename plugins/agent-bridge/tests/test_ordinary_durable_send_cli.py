"""Ordinary CLI selection reaches durable acceptance, not turn/lifecycle admission."""

from __future__ import annotations

import json
from typing import Any

import pytest

from agent_bridge import __main__ as core
from agent_bridge import session_targeting_cli as targeting
from agent_bridge.client import BridgeClientError
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION


class DurableClient:
    def __init__(self, status: str) -> None:
        self.status = status
        self.accepted: list[dict[str, Any]] = []
        self._base = "http://fixture.invalid"
        self._connect_grace = 0.0

    def health(self) -> dict[str, Any]:
        return {
            "protocol_version": HTTP_PROTOCOL_VERSION,
            "capabilities": {
                "durable_managed_messages": 1, "durable_live_inbox": 1, "bridge_inventory": 2,
                "native_send_no_resume": 1,
            },
        }

    def _request(self, method: str, path: str, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        if (method, path) == ("GET", "/health"):
            return self.health()
        raise AssertionError(f"Ordinary send bypassed durable ingress: {method} {path}")

    def daemon_supports(self, _version: int) -> bool:
        return True

    def resolve_live_session(self, _target: str) -> None:
        return None

    def get_session(self, _target: str, **_kwargs: Any) -> dict[str, Any]:
        return {
            "session_id": "managed-original", "acp_session_id": "native-original",
            "status": self.status, "agent_name": "known-agent",
        }

    def resolve_bridge(self, _target: str, **_kwargs: Any) -> dict[str, Any]:
        return {"bridge": {
            "session_id": "native-original",
            "managed_session_id": "managed-original" if self.status != "stopped" else None,
            "retained_managed_session_id": "managed-original" if self.status == "stopped" else None,
            "status": self.status, "route": {},
        }}

    def submit_managed_message(self, session_id: str, prompt: str, **kwargs: Any) -> dict[str, Any]:
        self.accepted.append({"session_id": session_id, "prompt": prompt, **kwargs})
        return {
            "session_id": session_id, "queue_id": 41,
            "idempotency_key": kwargs["idempotency_key"], "native_session_id": "native-original",
            "queued": True, "duplicate": False, "delivery_state": "queued", "turn_index": None,
        }

    def submit_prompt(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Ordinary send must not fall through to immediate turn admission")

    def resume_session(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Durable acceptance cannot resume a stopped target")

    def create_session(self, *_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("A known target cannot be replaced by a new session")


@pytest.mark.parametrize("selection", ["ordinary", "bridge"])
@pytest.mark.parametrize("status", ["stopped", "running", "idle"])
def test_known_managed_target_uses_durable_ingress_before_runtime_state(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    selection: str, status: str,
) -> None:
    client = DurableClient(status)
    monkeypatch.setattr(core, "_get_client", lambda **_kwargs: client)
    monkeypatch.setattr(core, "_caller_id_for", lambda _args: "original-sender")
    monkeypatch.setattr(core, "_sender_repo", lambda: "fixture-project")
    monkeypatch.setattr(core, "_connection_identity", lambda _client, sid: {"session_id": sid})
    monkeypatch.setattr(targeting, "_mark_resume_if_behind", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(targeting, "_busy_session_message", lambda *_args, **_kwargs: "busy fixture")
    args = core.build_parser().parse_args([
        "--json", "send", "managed-original", "original queued input",
        "--idempotency-key", "original-operation", "--no-wait",
        *(["--bridge"] if selection == "bridge" else []),
    ])
    code = 0
    try:
        targeting._cmd_send(args)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else 1
    output = capsys.readouterr()
    assert code == 0, output
    receipt = json.loads(output.out)
    assert receipt["outcome"] == "queued" and receipt["session_id"] == "managed-original"
    assert receipt["queue_id"] == 41
    assert len(client.accepted) == 1
    assert client.accepted[0]["idempotency_key"] == "original-operation"
    assert client.accepted[0]["prompt"] == "original queued input"
    assert client.accepted[0]["expected_native_session_id"] == "native-original"


@pytest.mark.parametrize("status,reason,code", [
    (502, "managed_message_acceptance_unknown", 70),
    (503, "managed_message_write_unconfirmed", 70),
    (404, "session_not_found", 69),
    (409, "managed_message_key_conflict", 65),
])
def test_managed_failure_never_falls_back_and_retains_generated_operation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    status: int, reason: str, code: int,
) -> None:
    client = DurableClient("stopped")

    def fail(session_id: str, prompt: str, **options: Any) -> None:
        client.accepted.append({"session_id": session_id, "prompt": prompt, **options})
        raise BridgeClientError(status, {"code": reason})

    monkeypatch.setattr(client, "submit_managed_message", fail)
    monkeypatch.setattr(core, "_get_client", lambda **_kwargs: client)
    monkeypatch.setattr(core, "_caller_id_for", lambda _args: "original-sender")
    monkeypatch.setattr(core, "_sender_repo", lambda: "fixture-project")
    args = core.build_parser().parse_args(["--json", "send", "managed-original", "original"])
    with pytest.raises(SystemExit) as error:
        targeting._cmd_send(args)
    assert error.value.code == code
    result = json.loads(capsys.readouterr().out)
    assert len(client.accepted) == 1
    assert client.accepted[0]["expected_native_session_id"] == "native-original"
    if code == 70:
        assert result["outcome"] == "unknown" and result["retryable"] is False
        assert result["idempotency_key"] == client.accepted[0]["idempotency_key"]
        assert result["bridge"]["managed_session_id"] == "managed-original"
    else:
        assert result["reason"] == reason


def test_local_text_unknown_prints_original_generated_key_and_selected_identity(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    client = DurableClient("stopped")

    def fail(session_id: str, prompt: str, **options: Any) -> None:
        client.accepted.append({"session_id": session_id, "prompt": prompt, **options})
        raise BridgeClientError(502, {"code": "managed_message_acceptance_unknown"})

    monkeypatch.setattr(client, "submit_managed_message", fail)
    monkeypatch.setattr(core, "_get_client", lambda **_kwargs: client)
    monkeypatch.setattr(core, "_caller_id_for", lambda _args: "original-sender")
    monkeypatch.setattr(core, "_sender_repo", lambda: "fixture-project")
    args = core.build_parser().parse_args(["send", "managed-original", "original"])
    with pytest.raises(SystemExit) as error:
        targeting._cmd_send(args)
    assert error.value.code == 70 and len(client.accepted) == 1
    key = client.accepted[0]["idempotency_key"]
    output = capsys.readouterr()
    assert all(value in output.err for value in ("managed-original", "native-original", key))


@pytest.mark.parametrize("length", [512, 513])
def test_managed_key_limit_has_a_typed_cli_outcome_before_client_mutation(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], length: int,
) -> None:
    client = DurableClient("stopped")
    monkeypatch.setattr(core, "_get_client", lambda **_kwargs: client)
    monkeypatch.setattr(core, "_caller_id_for", lambda _args: "sender")
    monkeypatch.setattr(core, "_sender_repo", lambda: "fixture-project")
    args = core.build_parser().parse_args([
        "--json", "send", "managed-original", "input", "--idempotency-key", "k" * length,
    ])
    if length == 513:
        with pytest.raises(SystemExit) as error:
            targeting._cmd_send(args)
        assert error.value.code == 2
        assert client.accepted == []
        assert json.loads(capsys.readouterr().out)["outcome"] == "refused_invalid"
    else:
        targeting._cmd_send(args)
        assert len(client.accepted) == 1
        assert json.loads(capsys.readouterr().out)["outcome"] == "queued"
