"""Remote prompt admission keeps the original key, native identity and uncertainty."""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from ssh_manager import CarrierRemoteError, CarrierUnavailable, Envelope, EnvelopeType, PersistentCarrier
from ssh_manager.carrier_protocol import DEFAULT_MAX_FRAME_SIZE

from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION, NATIVE_SEND_NO_RESUME_CAPABILITY
from agent_bridge.remote_errors import RemoteBridgeError
from agent_bridge.remote_native_prompt import OPERATION, NativePromptRequest, validate_receipt
from agent_bridge.remote_operations import CarrierRequestRouter, RemoteOperationService

native_send = importlib.import_module("tests.test_native_send_admission").native_send


def request(**changes: Any) -> dict[str, Any]:
    return {
        "version": 2, "operation": OPERATION, "session_id": "bridge-a",
        "native_session_id": "native-a", "invocation_key": "original-key",
        "prompt": "Continue the original assignment.", "caller_id": "fixture-controller",
        **changes,
    }


def receipt(**changes: Any) -> dict[str, Any]:
    return {
        "version": 1, "session_id": "bridge-a", "native_session_id": "native-a",
        "invocation_key": "original-key", "turn_index": 0,
        "queued": False, "admission": "accepted", **changes,
    }


def service_for(
    monkeypatch: pytest.MonkeyPatch, transport: Any, *, max_frame_size: int = DEFAULT_MAX_FRAME_SIZE,
) -> tuple[RemoteOperationService, AsyncMock]:
    release = AsyncMock()
    carrier = SimpleNamespace(request=transport, _outbound_max_frame_size=max_frame_size)
    carrier.encode_request = lambda payload, **kwargs: PersistentCarrier.encode_request(
        cast(Any, carrier), payload, **kwargs,
    )
    lease = SimpleNamespace(carrier=carrier, release=release)
    service = RemoteOperationService(cast(Any, SimpleNamespace()))
    monkeypatch.setattr(service, "_lease", AsyncMock(return_value=lease))
    return service, release


async def submit(service: RemoteOperationService, **changes: Any) -> dict[str, Any]:
    options = {key: value for key, value in request(**changes).items() if key not in {"version", "operation"}}
    return await service.submit_native_prompt("host-b", **options)


@pytest.mark.asyncio
@pytest.mark.parametrize("changes", [
    {"version": 1}, {"session_id": "../other"}, {"native_session_id": ""},
    {"invocation_key": ""}, {"invocation_key": " "}, {"prompt": " "},
    {"prompt": True}, {"caller_id": ""}, {"queue": True}, {"no_resume": False},
])
async def test_invalid_or_prior_request_refuses_before_native_client(changes: dict[str, Any]) -> None:
    def factory() -> Any:
        pytest.fail("invalid prompt acquired a native client")

    response = await CarrierRequestRouter(factory)(
        Envelope(EnvelopeType.REQUEST, payload=request(**changes)),
    )
    assert isinstance(response, Envelope) and response.type == EnvelopeType.ERROR
    assert response.payload["status"] == (426 if changes.get("version") == 1 else 400)


@pytest.mark.parametrize("changes", [
    {"version": True}, {"session_id": "replacement"}, {"native_session_id": "replacement"},
    {"invocation_key": "fresh-key"}, {"queued": True}, {"turn_index": True},
    {"turn_index": -1}, {"admission": "queued"},
])
def test_unconfirmed_receipt_is_unknown_not_success_or_refusal(changes: dict[str, Any]) -> None:
    selected = NativePromptRequest.model_validate({
        key: value for key, value in request().items() if key not in {"version", "operation"}
    })
    with pytest.raises(RemoteBridgeError) as error:
        validate_receipt(receipt(**changes), selected)
    assert error.value.code == "native_prompt_admission_unknown"
    assert error.value.details["invocation_key"] == "original-key"
    assert error.value.details["admission_status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_reply", [False, True])
async def test_carrier_to_real_native_api_preserves_one_original_turn(
    native_send: Any, monkeypatch: pytest.MonkeyPatch, lost_reply: bool,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from agent_bridge.routes.sessions import router

    manager, session, _original = native_send
    app = FastAPI()
    app.state.session_manager = manager
    app.include_router(router)
    client = BridgeClient("http://fixture", "fixture-token")
    calls = []
    lose_once = [lost_reply]

    with TestClient(app) as api:
        def http_request(method: str, path: str, body: Any = None, **_kwargs: Any) -> Any:
            if path == "/health":
                return {
                    "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                    "capabilities": {NATIVE_SEND_NO_RESUME_CAPABILITY: 1},
                }
            calls.append((method, path, body))
            response = api.request(method, path, json=body)
            if response.is_error:
                raise BridgeClientError(response.status_code, response.json().get("detail"))
            if method == "POST" and lose_once[0]:
                lose_once[0] = False
                raise BridgeConnectionError("fixture lost accepted response")
            return response.json()

        monkeypatch.setattr(client, "_request", http_request)
        receiver = CarrierRequestRouter(lambda: client)

        async def transport(payload: dict[str, Any], **_kwargs: Any) -> Envelope:
            assert payload["version"] == 2 and payload["operation"] == OPERATION
            response = await receiver(Envelope(EnvelopeType.REQUEST, payload=payload))
            assert isinstance(response, Envelope)
            if response.type == EnvelopeType.ERROR:
                raise CarrierRemoteError(response.payload)
            return response

        service, release = service_for(monkeypatch, transport)
        if lost_reply:
            with pytest.raises(RemoteBridgeError) as unknown:
                await submit(service)
            assert unknown.value.code == "native_prompt_admission_unknown"
            assert unknown.value.details["invocation_key"] == "original-key"
            assert len([call for call in calls if call[0] == "POST"]) == 1
            assert session.turn_count == 1
        first = await submit(service)
        assert first == receipt()
        manager.db.flush()
        after = list(manager.db._get_conn().iterdump())
        assert await submit(service) == first
        manager.db.flush()
        assert list(manager.db._get_conn().iterdump()) == after
        with pytest.raises(RemoteBridgeError) as conflict:
            await submit(service, prompt="Different intent")
        assert conflict.value.code == "native_prompt_refused"
        assert conflict.value.details["refusal"]["reason"] == "native_invocation_conflict"
        manager.db.flush()
        assert list(manager.db._get_conn().iterdump()) == after
        assert session.turn_count == 1
        manager._run_prompt.assert_awaited_once()
        manager.resume_session.assert_not_awaited()
        manager.handoff_session.assert_not_awaited()
        assert release.await_count == (4 if lost_reply else 3)
        posts = [call for call in calls if call[0] == "POST"]
        assert len(posts) == (4 if lost_reply else 3)
        assert all(path.endswith("/turns/native-bound/send") for _method, path, _body in posts)
        assert all(body["no_resume"] is True and body["invocation_key"] == "original-key" for _m, _p, body in posts)


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["prior-native", "owner-alias", "native-mismatch"])
async def test_native_provider_refusal_happens_before_post(
    monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    client = BridgeClient("http://fixture", "fixture-token")
    calls = []

    def http_request(method: str, path: str, **_kwargs: Any) -> dict[str, Any]:
        calls.append((method, path))
        assert method == "GET"
        if path == "/health":
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                "capabilities": {} if fault == "prior-native" else {NATIVE_SEND_NO_RESUME_CAPABILITY: 1},
            }
        return {
            "session_id": "alias-owner" if fault == "owner-alias" else "bridge-a",
            "acp_session_id": "other-native" if fault == "native-mismatch" else "native-a",
        }

    monkeypatch.setattr(client, "_request", http_request)
    response = await CarrierRequestRouter(lambda: client)(
        Envelope(EnvelopeType.REQUEST, payload=request()),
    )
    assert isinstance(response, Envelope) and response.type == EnvelopeType.ERROR
    assert response.payload["code"] == "native_prompt_refused"
    assert response.payload["status"] == (426 if fault == "prior-native" else 409)
    assert all(method == "GET" for method, _path in calls)
    if fault == "owner-alias":
        assert response.payload["details"]["refusal"]["reason"] == "expected_owner_mismatch"


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["disconnect", "timeout", "remote", "receipt", "shape", "kind", "cleanup"])
async def test_unknown_carrier_admission_never_reconnects_or_replays(
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    async def transport(*_args: Any, **_kwargs: Any) -> Envelope:
        if failure == "disconnect":
            raise CarrierUnavailable("fixture disconnected", reconnectable=True)
        if failure == "timeout":
            raise TimeoutError("fixture")
        if failure == "remote":
            raise CarrierRemoteError({"status": 500, "code": "unclassified_failure", "message": "fixture"})
        if failure == "shape":
            return Envelope(EnvelopeType.RESPONSE, payload=cast(Any, None))
        if failure in {"kind", "cleanup"}:
            return Envelope(
                EnvelopeType.REQUEST if failure == "kind" else EnvelopeType.RESPONSE,
                payload={"result": receipt()},
            )
        return Envelope(EnvelopeType.RESPONSE, payload={"result": receipt(invocation_key="other")})

    attempt = AsyncMock(side_effect=transport)
    service, release = service_for(monkeypatch, attempt)
    if failure == "cleanup":
        release.side_effect = RuntimeError("fixture lease cleanup failed")
    with pytest.raises(RemoteBridgeError) as error:
        await submit(service)
    assert error.value.code == "native_prompt_admission_unknown"
    assert error.value.details["invocation_key"] == "original-key"
    attempt.assert_awaited_once()
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_caller_deadline_releases_lease_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert error.value.status == 504
    assert error.value.code == "native_prompt_admission_unknown"
    assert cancelled.is_set()
    attempt.assert_awaited_once()
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_prior_carrier_refusal_is_explicit_without_native_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = AsyncMock(side_effect=CarrierRemoteError({
        "status": 501, "code": "unsupported_operation", "message": "prior carrier",
    }))
    service, release = service_for(monkeypatch, attempt)
    with pytest.raises(RemoteBridgeError) as error:
        await submit(service)
    assert (error.value.status, error.value.code) == (501, "unsupported_operation")
    attempt.assert_awaited_once()
    release.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("prompt", [
    "x" * DEFAULT_MAX_FRAME_SIZE,
    "\U0001f600" * 100_000,
    '"' * 600_000,
], ids=["ascii-envelope-overhead", "escaped-unicode", "escaped-quotes"])
async def test_encoded_frame_refuses_before_request_without_unknown_admission(
    monkeypatch: pytest.MonkeyPatch, prompt: str,
) -> None:
    attempt = AsyncMock()
    service, release = service_for(monkeypatch, attempt)
    with pytest.raises(RemoteBridgeError) as error:
        await submit(service, prompt=prompt)
    assert (error.value.status, error.value.code) == (413, "native_prompt_frame_refused")
    assert error.value.details["invocation_key"] == "original-key"
    attempt.assert_not_awaited()
    release.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("over_limit", [False, True])
async def test_frame_preflight_uses_negotiated_limit_and_exact_correlation_envelope(
    monkeypatch: pytest.MonkeyPatch, over_limit: bool,
) -> None:
    wire = request(prompt="bounded payload")
    carrier = SimpleNamespace(_outbound_max_frame_size=DEFAULT_MAX_FRAME_SIZE)
    frame = PersistentCarrier.encode_request(cast(Any, carrier), wire, request_id="0" * 32)
    limit = len(frame) - 4 - int(over_limit)

    async def transport(payload: dict[str, Any], *, request_id: str, **_kwargs: Any) -> Envelope:
        assert payload == wire
        assert len(request_id) == 32
        actual = PersistentCarrier.encode_request(cast(Any, carrier), payload, request_id=request_id)
        assert len(actual) == len(frame)
        return Envelope(EnvelopeType.RESPONSE, payload={"result": receipt()})

    attempt = AsyncMock(side_effect=transport)
    service, release = service_for(monkeypatch, attempt, max_frame_size=limit)
    if over_limit:
        with pytest.raises(RemoteBridgeError) as error:
            await submit(service, prompt="bounded payload")
        assert error.value.status == 413
        attempt.assert_not_awaited()
    else:
        assert await submit(service, prompt="bounded payload") == receipt()
        attempt.assert_awaited_once()
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_prior_local_carrier_without_encoding_contract_refuses_before_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = AsyncMock()
    service, release = service_for(monkeypatch, attempt)
    lease = await service._lease("host-b")
    monkeypatch.delattr(lease.carrier, "encode_request")
    with pytest.raises(RemoteBridgeError) as error:
        await submit(service)
    assert (error.value.status, error.value.code) == (426, "unsupported_version")
    attempt.assert_not_awaited()
    release.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["native_prompt_refused", "native_prompt_admission_unknown"])
@pytest.mark.parametrize("binding", [
    "valid", "missing-owner", "wrong-owner", "missing-native", "wrong-native", "missing-key", "wrong-key",
])
async def test_native_error_reply_requires_original_request_binding(
    monkeypatch: pytest.MonkeyPatch, code: str, binding: str,
) -> None:
    details = {
        "session_id": "bridge-a", "native_session_id": "native-a", "invocation_key": "original-key",
    }
    if binding != "valid":
        change, name = binding.split("-")
        key = {"owner": "session_id", "native": "native_session_id", "key": "invocation_key"}[name]
        if change == "missing":
            details.pop(key)
        else:
            details[key] = "other"
    attempt = AsyncMock(side_effect=CarrierRemoteError({
        "status": 409 if code == "native_prompt_refused" else 503,
        "code": code, "message": "fixture", "details": details,
    }))
    service, release = service_for(monkeypatch, attempt)
    with pytest.raises(RemoteBridgeError) as error:
        await submit(service)
    assert error.value.code == (code if binding == "valid" else "native_prompt_admission_unknown")
    assert error.value.details["invocation_key"] == "original-key"
    assert error.value.details["session_id"] == "bridge-a"
    assert error.value.details["native_session_id"] == "native-a"
    if binding != "valid":
        assert error.value.details["admission_status"] == "unknown"
    attempt.assert_awaited_once()
    release.assert_awaited_once()
