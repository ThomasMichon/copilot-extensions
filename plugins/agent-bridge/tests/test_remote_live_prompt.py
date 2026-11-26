"""Carrier admission preserves the original exact inbox, key and uncertainty."""

from __future__ import annotations

import asyncio
import importlib
import time
from typing import Any
from unittest.mock import AsyncMock

import pytest
from ssh_manager import CarrierRemoteError, CarrierUnavailable, Envelope, EnvelopeType

from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.remote_errors import RemoteBridgeError
from agent_bridge.remote_live_prompt import OPERATION, ExactLivePromptRequest, validate_receipt
from agent_bridge.remote_operations import CarrierRequestRouter, RemoteOperationService

api = importlib.import_module("tests.test_live_message_receipts").api
service_for = importlib.import_module("tests.test_remote_native_prompt").service_for


def request(**changes: Any) -> dict[str, Any]:
    return {
        "version": 2, "operation": OPERATION, "session_id": "live-1",
        "invocation_key": "original-key", "sender": "fixture-controller",
        "prompt": "Continue the original assignment.", **changes,
    }


def receipt(**changes: Any) -> dict[str, Any]:
    return {
        "version": 1, "session_id": "live-1", "invocation_key": "original-key",
        "sender": "fixture-controller", "admission": "accepted", "message_id": 1,
        **changes,
    }


async def submit(service: RemoteOperationService, **changes: Any) -> dict[str, Any]:
    options = {key: value for key, value in request(**changes).items() if key not in {"version", "operation"}}
    return await service.submit_exact_live_prompt("host-b", **options)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"version": 1}, {"session_id": "../other"}, {"session_id": ""},
    {"invocation_key": ""}, {"invocation_key": True}, {"sender": ""},
    {"prompt": " "}, {"prompt": True}, {"exact_session": False},
    {"wait": True, "version": 4}, {"delivery": "queue"},
])
async def test_invalid_or_prior_request_does_not_acquire_an_inbox_client(changes: dict[str, Any]) -> None:
    def factory() -> Any:
        pytest.fail("invalid exact prompt acquired an inbox client")

    response = await CarrierRequestRouter(factory)(
        Envelope(EnvelopeType.REQUEST, payload=request(**changes)),
    )
    assert isinstance(response, Envelope) and response.type == EnvelopeType.ERROR
    assert response.payload["status"] == (426 if changes.get("version") == 1 else 400)


@pytest.mark.parametrize("changes", [
    {"version": True}, {"session_id": "successor"}, {"invocation_key": "fresh-key"},
    {"sender": "other-sender"}, {"admission": "delivered"}, {"message_id": True},
    {"message_id": 0}, {"message_id": "1"},
])
def test_malformed_receipt_retains_original_identity_and_unknown_admission(changes: dict[str, Any]) -> None:
    selected = ExactLivePromptRequest.model_validate({
        key: value for key, value in request().items() if key not in {"version", "operation"}
    })
    with pytest.raises(RemoteBridgeError) as error:
        validate_receipt(receipt(**changes), selected)
    assert error.value.code == "exact_live_prompt_admission_unknown"
    assert error.value.details["invocation_key"] == "original-key"
    assert error.value.details["session_id"] == "live-1"
    assert error.value.details["admission_status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["none", "lost-reply", "post-426", "wrong-session"])
async def test_real_inbox_api_admits_one_exact_original_message(
    api: Any, tmp_db: Any, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    client = BridgeClient("http://fixture", "fixture-token")
    calls = []

    def http_request(method: str, path: str, body: Any = None, **kwargs: Any) -> Any:
        calls.append((method, path, body, kwargs))
        if path == "/health":
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                "capabilities": {"live_exact_send": 1},
            }
        response = api.request(method, path, json=body)
        if response.is_error:
            raise BridgeClientError(response.status_code, response.json().get("detail"))
        if fault == "lost-reply":
            raise BridgeConnectionError("fixture lost accepted response")
        if fault == "post-426":
            raise BridgeClientError(426, "fixture replaced accepted response")
        if fault == "wrong-session":
            return {**response.json(), "session_id": "successor"}
        return response.json()

    monkeypatch.setattr(client, "_request", http_request)
    receiver = CarrierRequestRouter(lambda: client)

    async def transport(payload: dict[str, Any], **_kwargs: Any) -> Envelope:
        assert payload["operation"] == OPERATION and payload["version"] == 2
        response = await receiver(Envelope(EnvelopeType.REQUEST, payload=payload))
        assert isinstance(response, Envelope)
        if response.type == EnvelopeType.ERROR:
            raise CarrierRemoteError(response.payload)
        return response

    attempt = AsyncMock(side_effect=transport)
    service, release = service_for(monkeypatch, attempt)
    if fault != "none":
        with pytest.raises(RemoteBridgeError) as unknown:
            await submit(service)
        assert unknown.value.code == "exact_live_prompt_admission_unknown"
        assert unknown.value.details["invocation_key"] == "original-key"
    else:
        result = await submit(service)
        assert result == receipt(message_id=result["message_id"])
    posts = [call for call in calls if call[0] == "POST"]
    assert len(posts) == 1
    _method, path, body, options = posts[0]
    assert path == "/api/v1/live-sessions/live-1/messages"
    assert options["allow_retry"] is False
    assert body["exact_session"] is True and body["expected_session_id"] == "live-1"
    assert body["idempotency_key"] == "original-key" and body["body"] == request()["prompt"]
    pending = tmp_db.list_pending_live_messages("live-1")
    assert len(pending) == 1 and pending[0]["exact_session"] == 1
    assert tmp_db.live_message_receipt("live-1", pending[0]["id"])["stage"] == "admitted"
    attempt.assert_awaited_once()
    release.assert_awaited_once()


@pytest.mark.parametrize("fault", ["duplicate", "changed-body", "alias", "takeover", "stale"])
def test_real_inbox_keeps_key_conflicts_and_exact_target_assertions(
    api: Any, tmp_db: Any, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    from agent_bridge.remote_live_prompt import _submit

    client = BridgeClient("http://fixture", "fixture-token")

    def http_request(method: str, path: str, body: Any = None, **_kwargs: Any) -> Any:
        if path == "/health":
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                "capabilities": {"live_exact_send": 1},
            }
        response = api.request(method, path, json=body)
        if response.is_error:
            raise BridgeClientError(response.status_code, response.json().get("detail"))
        return response.json()

    monkeypatch.setattr(client, "_request", http_request)
    selected = ExactLivePromptRequest.model_validate({
        key: value for key, value in request().items() if key not in {"version", "operation"}
    })
    if fault in {"duplicate", "changed-body"}:
        first = _submit(lambda: client, selected)
        before = [dict(row) for row in tmp_db.execute_read("SELECT * FROM live_messages")]
        if fault == "duplicate":
            assert _submit(lambda: client, selected) == first
        else:
            with pytest.raises(RemoteBridgeError) as error:
                _submit(lambda: client, selected.model_copy(update={"prompt": "Different intent"}))
            assert error.value.code == "exact_live_prompt_admission_unknown"
        assert [dict(row) for row in tmp_db.execute_read("SELECT * FROM live_messages")] == before
        return
    if fault == "takeover":
        importlib.import_module("tests.test_live_keyed_receipt").rollover(tmp_db)
    elif fault == "alias":
        tmp_db.register_live_session(
            "successor", machine=None, cwd=None, worktree_id="other", repo=None,
            branch=None, pid=None, role=None, now=time.time(),
        )
        tmp_db.execute_write(
            "INSERT INTO live_session_aliases(alias_session_id,target_session_id,created_at) VALUES (?,?,?)",
            ("live-1", "successor", time.time()),
        )
    else:
        tmp_db.execute_write("UPDATE live_sessions SET updated_at=0 WHERE session_id='live-1'")
        accepted = _submit(lambda: client, selected)
        assert accepted["message_id"] is not None
        stored, = tmp_db.execute_read("SELECT * FROM live_messages")
        assert stored["producer_target_json"] is not None
        assert stored["delivered_at"] is None and stored["consumed_at"] is None
        return
    with pytest.raises(RemoteBridgeError) as error:
        _submit(lambda: client, selected)
    assert error.value.code == "exact_live_prompt_admission_unknown"
    assert not tmp_db.execute_read("SELECT * FROM live_messages")


@pytest.mark.asyncio
async def test_prior_http_provider_refuses_before_post(monkeypatch: pytest.MonkeyPatch) -> None:
    client = BridgeClient("http://fixture", "fixture-token")
    calls = []

    def http_request(method: str, path: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append((method, path))
        assert method == "GET" and path == "/health"
        return {"protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1, "capabilities": {}}

    monkeypatch.setattr(client, "_request", http_request)
    response = await CarrierRequestRouter(lambda: client)(
        Envelope(EnvelopeType.REQUEST, payload=request()),
    )
    assert isinstance(response, Envelope) and response.type == EnvelopeType.ERROR
    assert response.payload["code"] == "exact_live_prompt_refused"
    assert response.payload["status"] == 426
    assert calls == [("GET", "/health")]


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["disconnect", "timeout", "remote", "receipt", "cleanup", "prior"])
async def test_carrier_faults_never_replay_or_reconnect(
    monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    async def transport(*_args: Any, **_kwargs: Any) -> Envelope:
        if fault == "disconnect":
            raise CarrierUnavailable("fixture disconnected", reconnectable=True)
        if fault == "timeout":
            raise TimeoutError("fixture timed out")
        if fault in {"remote", "prior"}:
            raise CarrierRemoteError({
                "status": 501 if fault == "prior" else 503,
                "code": "unsupported_operation" if fault == "prior" else "draining",
                "message": "fixture",
            })
        return Envelope(
            EnvelopeType.RESPONSE,
            payload={"result": receipt(session_id="successor") if fault == "receipt" else receipt()},
        )

    attempt = AsyncMock(side_effect=transport)
    service, release = service_for(monkeypatch, attempt)
    if fault == "cleanup":
        release.side_effect = RuntimeError("fixture cleanup failed")
    with pytest.raises(RemoteBridgeError) as error:
        await submit(service)
    assert error.value.code == (
        "unsupported_operation" if fault == "prior" else "exact_live_prompt_admission_unknown"
    )
    attempt.assert_awaited_once()
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_encoded_frame_refuses_before_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    attempt = AsyncMock()
    service, release = service_for(monkeypatch, attempt, max_frame_size=200)
    with pytest.raises(RemoteBridgeError) as error:
        await submit(service, prompt="x" * 1000)
    assert (error.value.status, error.value.code) == (413, "exact_live_prompt_frame_refused")
    attempt.assert_not_awaited()
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_caller_deadline_cancels_observation_and_releases_without_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = asyncio.Event()

    async def transport(*_args: Any, **_kwargs: Any) -> Any:
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    attempt = AsyncMock(side_effect=transport)
    service, release = service_for(monkeypatch, attempt)
    with pytest.raises(RemoteBridgeError) as error:
        await submit(service, timeout=0.01)
    assert (error.value.status, error.value.code) == (504, "exact_live_prompt_admission_unknown")
    assert cancelled.is_set()
    attempt.assert_awaited_once()
    release.assert_awaited_once()
