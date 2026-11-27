"""Ordinary managed input never falls back to lifecycle-changing turn admission."""

from __future__ import annotations

from typing import Any

import pytest

from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
from agent_bridge.client_read_budget import ReadDeadlineExpired
from agent_bridge.managed_messages import CAPABILITY
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION


@pytest.mark.parametrize("capability", [None, True, "1", 0])
def test_missing_capability_refuses_before_mutation(
    monkeypatch: pytest.MonkeyPatch, capability: Any,
) -> None:
    client = BridgeClient("http://fixture", "fixture")
    calls = []

    def request(method: str, path: str, **kwargs: Any) -> Any:
        calls.append((method, path))
        assert kwargs["allow_retry"] is False
        return {"protocol_version": HTTP_PROTOCOL_VERSION, "capabilities": {CAPABILITY: capability}}

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(BridgeClientError) as error:
        client.submit_managed_message("original-target", "original input", idempotency_key="original")
    assert error.value.status == 426
    assert calls == [("GET", "/health")]


@pytest.mark.parametrize("fault", [
    "none", "disconnect", "draining", "wrong-target", "wrong-key", "bad-state", "shape", "interrupt",
    "deadline", "error-shape",
])
def test_exact_one_attempt_preserves_original_operation_and_unknown_effect_fence(
    monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    resolve = []

    def reresolve() -> str:
        resolve.append(True)
        return "http://replacement"

    client = BridgeClient(
        "http://fixture", "fixture", connect_grace=30,
        reresolve=reresolve,
    )
    calls = []

    def request(method: str, path: str, body: Any = None, **kwargs: Any) -> Any:
        calls.append((method, path))
        assert kwargs["allow_retry"] is False
        if method == "GET":
            return {"protocol_version": HTTP_PROTOCOL_VERSION, "capabilities": {CAPABILITY: 1}}
        assert path == "/api/v1/sessions/original-target/messages"
        assert body == {
            "prompt": "original input", "idempotency_key": "original",
            "caller_id": "sender", "expected_native_session_id": "native",
        }
        if fault == "disconnect":
            raise BridgeConnectionError("accepted response lost")
        if fault == "draining":
            raise BridgeClientError(503, "draining")
        if fault == "interrupt":
            raise KeyboardInterrupt
        if fault == "deadline":
            raise ReadDeadlineExpired("POST response budget exhausted")
        if fault == "error-shape":
            raise BridgeClientError(500, {"code": []})
        if fault == "shape":
            return {}
        return {
            "session_id": "other" if fault == "wrong-target" else "original-target",
            "idempotency_key": "other" if fault == "wrong-key" else "original",
            "native_session_id": "native",
            "queue_id": 7, "duplicate": False, "queued": True,
            "delivery_state": [] if fault == "bad-state" else "queued", "turn_index": None,
        }

    monkeypatch.setattr(client, "_request", request)
    if fault == "none":
        receipt = client.submit_managed_message(
            "original-target", "original input", idempotency_key="original",
            caller_id="sender", expected_native_session_id="native",
        )
        assert receipt["queue_id"] == 7
    else:
        with pytest.raises(BridgeClientError) as error:
            client.submit_managed_message(
                "original-target", "original input", idempotency_key="original",
                caller_id="sender", expected_native_session_id="native",
            )
        assert error.value.detail["code"] == "managed_message_acceptance_unknown"
        assert error.value.detail["idempotency_key"] == "original"
    assert [method for method, _ in calls] == ["GET", "POST"]
    assert resolve == []


def test_pre_attempt_deadline_is_not_unknown_acceptance(monkeypatch: pytest.MonkeyPatch) -> None:
    client = BridgeClient("http://fixture", "fixture")
    calls = []

    def request(method: str, path: str, **kwargs: Any) -> Any:
        calls.append((method, path))
        raise ReadDeadlineExpired("health budget exhausted")

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(ReadDeadlineExpired):
        client.submit_managed_message("original-target", "input", idempotency_key="original")
    assert calls == [("GET", "/health")]
