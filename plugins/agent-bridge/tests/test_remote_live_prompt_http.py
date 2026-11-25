"""HTTP and carrier admission preserve the exact registered inbox and key."""

from __future__ import annotations

from contextlib import contextmanager
from email.message import Message
import importlib
import io
import json
from typing import Any
from urllib.error import HTTPError
from urllib.parse import urlsplit
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from ssh_manager import CarrierRemoteError, Envelope, EnvelopeType

from agent_bridge import client_read_budget
from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.remote_operations import CarrierRequestRouter
from agent_bridge.routes.remote import router

carrier_tests = importlib.import_module("tests.test_remote_live_prompt")
api = carrier_tests.api


def options(**changes: Any) -> dict[str, Any]:
    return {
        key: value for key, value in carrier_tests.request(**changes).items()
        if key not in {"version", "operation"}
    }


def health(capability: Any = 1) -> dict[str, Any]:
    return {
        "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
        "capabilities": {"remote_exact_live_prompt": capability, "live_exact_send": 1},
    }


@pytest.mark.parametrize("capability", [None, True, "1", 0])
def test_prior_http_refuses_before_mutation(
    monkeypatch: pytest.MonkeyPatch, capability: Any,
) -> None:
    client = BridgeClient("http://observer", "fixture")
    calls = []

    def request(method: str, path: str, **kwargs: Any) -> Any:
        calls.append((method, path))
        assert kwargs["allow_retry"] is False
        return health(capability)

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(BridgeClientError) as error:
        client.submit_remote_exact_live_prompt("host-b", **options())
    assert error.value.status == 426
    assert calls == [("GET", "/health")]


@pytest.mark.parametrize("changes", [
    {"session_id": "../other"}, {"sender": ""}, {"invocation_key": True}, {"prompt": " "},
])
def test_invalid_client_request_never_acquires_service(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any],
) -> None:
    client = BridgeClient("http://observer", "fixture")
    monkeypatch.setattr(client, "_request", lambda *_a, **_kw: pytest.fail("invalid prompt sent"))
    with pytest.raises(BridgeClientError) as error:
        client.submit_remote_exact_live_prompt("host-b", **options(**changes))
    assert error.value.status == 400


@pytest.mark.parametrize("malformed", [False, True])
def test_failed_health_never_reresolves_or_posts(
    monkeypatch: pytest.MonkeyPatch, malformed: bool,
) -> None:
    client = BridgeClient(
        "http://observer", "fixture", connect_grace=30,
        reresolve=lambda: pytest.fail("failed health must not select a replacement endpoint"),
    )
    calls = []

    def request(method: str, path: str, **_kwargs: Any) -> Any:
        calls.append((method, path))
        if malformed:
            return {**health(), "protocol_version": "invalid"}
        raise BridgeConnectionError("fixture health check failed")

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(BridgeConnectionError):
        client.submit_remote_exact_live_prompt("host-b", **options())
    assert calls == [("GET", "/health")]


@pytest.mark.parametrize("changes", [
    {"delivery": "queue"}, {"sender": True}, {"prompt": " "},
    {"invocation_key": " "}, {"session_id": "successor"},
])
def test_http_rejects_invalid_body_before_service(changes: dict[str, Any]) -> None:
    app = FastAPI()
    app.state.remote_operations = type("NoAdmission", (), {
        "submit_exact_live_prompt": lambda *_a, **_kw: pytest.fail("invalid prompt admitted"),
    })()
    app.include_router(router)
    body = {key: value for key, value in options().items() if key != "session_id"}
    with TestClient(app) as observer:
        response = observer.post(
            "/api/v1/remote/host-b/live-sessions/live-1/exact-prompt",
            json={**body, **changes},
        )
    assert response.status_code == 422


def test_http_prior_carrier_refusal_keeps_original_binding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def prior(*_args: Any, **_kwargs: Any) -> Any:
        raise CarrierRemoteError({
            "code": "unsupported_operation", "status": 501, "message": "prior carrier",
        })

    attempt = AsyncMock(side_effect=prior)
    service, release = carrier_tests.service_for(monkeypatch, attempt)
    app = FastAPI()
    app.state.remote_operations = service
    app.include_router(router)
    with TestClient(app) as observer:
        response = observer.post(
            "/api/v1/remote/host-b/live-sessions/live-1/exact-prompt",
            json={key: value for key, value in options().items() if key != "session_id"},
        )
    assert response.status_code == 501
    detail = response.json()["detail"]
    assert detail["code"] == "unsupported_operation" and detail["reconnectable"] is False
    assert all(detail[key] == value for key, value in {
        "host": "host-b", "session_id": "live-1", "sender": "fixture-controller",
        "invocation_key": "original-key",
    }.items())
    attempt.assert_awaited_once()
    release.assert_awaited_once()


@pytest.mark.parametrize("fault", ["none", "lost-reply", "interrupt", "prior"])
def test_http_carrier_real_inbox_admits_at_most_one_original_message(
    api: Any, tmp_db: Any, monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    receiver = CarrierRequestRouter(lambda: BridgeClient("http://receiver", "fixture"))
    carrier_calls = []

    async def transport(payload: dict[str, Any], **_kwargs: Any) -> Envelope:
        carrier_calls.append(payload)
        response = await receiver(Envelope(EnvelopeType.REQUEST, payload=payload))
        assert isinstance(response, Envelope)
        if response.type == EnvelopeType.ERROR:
            raise CarrierRemoteError(response.payload)
        return response

    attempt = AsyncMock(side_effect=transport)
    service, release = carrier_tests.service_for(monkeypatch, attempt)
    app = FastAPI()
    app.state.remote_operations = service
    app.include_router(router)
    calls = []

    class Response(io.BytesIO):
        status = 200

    with TestClient(app) as observer:
        @contextmanager
        def open_response(req: Any, *, timeout: float) -> Any:
            url = urlsplit(req.full_url)
            method = req.get_method()
            body = json.loads(req.data) if req.data else None
            calls.append((url.netloc, method, url.path))
            assert client_read_budget.remaining() is not None
            assert 0 < timeout <= 20.01
            if url.path == "/health":
                value = health(None if fault == "prior" and url.netloc == "observer" else 1)
            else:
                target = observer if url.netloc == "observer" else api
                response = target.request(method, url.path, json=body)
                if response.is_error:
                    raise HTTPError(
                        req.full_url, response.status_code, "fixture",
                        Message(), io.BytesIO(response.content),
                    )
                value = response.json()
                if url.netloc == "observer":
                    if fault == "lost-reply":
                        raise BridgeConnectionError("accepted response lost")
                    if fault == "interrupt":
                        raise KeyboardInterrupt
            with Response(json.dumps(value).encode()) as raw:
                yield raw

        monkeypatch.setattr(client_read_budget, "open_response", open_response)
        client = BridgeClient("http://observer", "fixture", reresolve=lambda: pytest.fail("reconnect"))
        if fault == "none":
            result = client.submit_remote_exact_live_prompt("host-b", **options())
            assert result == carrier_tests.receipt(host="host-b", message_id=result["message_id"])
        else:
            with pytest.raises(BridgeClientError) as error:
                client.submit_remote_exact_live_prompt("host-b", **options())
            if fault == "prior":
                assert error.value.status == 426
            else:
                assert error.value.detail["code"] == "exact_live_prompt_admission_unknown"
                assert error.value.detail["invocation_key"] == "original-key"
    pending = tmp_db.list_pending_live_messages("live-1")
    if fault == "prior":
        assert calls == [("observer", "GET", "/health")]
        assert not carrier_calls and not pending
        return
    assert len(pending) == 1 and pending[0]["exact_session"] == 1
    assert pending[0]["idempotency_key"] == "original-key"
    assert tmp_db.live_message_receipt("live-1", pending[0]["id"])["stage"] == "admitted"
    assert carrier_calls == [carrier_tests.request()]
    attempt.assert_awaited_once()
    release.assert_awaited_once()
    assert [(host, path) for host, method, path in calls if method == "POST"] == [
        ("observer", "/api/v1/remote/host-b/live-sessions/live-1/exact-prompt"),
        ("receiver", "/api/v1/live-sessions/live-1/messages"),
    ]
