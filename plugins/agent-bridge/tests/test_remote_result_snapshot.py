"""Remote native observations keep identity, bounds and cursor custody."""

from __future__ import annotations

import asyncio
import copy
import importlib
import io
import json
from contextlib import contextmanager
from types import SimpleNamespace
import time
from typing import Any, cast
from unittest.mock import AsyncMock
from urllib.parse import parse_qsl, urlsplit

import pytest
from ssh_manager import CarrierRemoteError, Envelope, EnvelopeType

from agent_bridge.client import BridgeClient, BridgeClientError
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION
from agent_bridge.remote_errors import RemoteBridgeError
from agent_bridge.remote_operations import CarrierRequestRouter, RemoteOperationService
from agent_bridge.remote_observation import OPERATION, ResultRequest, read_native_result

api = importlib.import_module("tests.test_live_message_receipts").api
prepare = importlib.import_module("tests.test_native_bound_prompt").prepare


def snapshot(session_id: str = "managed-a") -> dict[str, Any]:
    unavailable = {"availability": "not_yet_observed"}
    return {
        "identity": {
            "logical_delegate_kind": "session",
            "logical_delegate_id": session_id,
            "requested_ref": session_id,
            "snapshot_session_id": session_id,
            "current_session_id": session_id,
            "native_session_id": "native-a",
        },
        "fidelity": {"level": "reduced", "event_retention": "durable"},
        "state": {
            "session_status": "idle",
            "at_rest": True,
            "attention": unavailable,
            "active_work": unavailable,
            "pending_input": unavailable,
        },
        "latest_result": unavailable,
        "incremental": unavailable,
        "limits": {"max_items": 2, "max_text_chars": 256, "used_text_chars": 0},
    }


def request(**changes: Any) -> dict[str, Any]:
    return {
        "version": 1,
        "operation": OPERATION,
        "session_id": "managed-a",
        "native_session_id": "native-a",
        "represented": False,
        "max_items": 2,
        "max_text_chars": 256,
        **changes,
    }


async def route(client: Any, **changes: Any) -> Envelope:
    response = await CarrierRequestRouter(lambda: client)(
        Envelope(EnvelopeType.REQUEST, payload=request(**changes)),
    )
    assert isinstance(response, Envelope)
    return response


def service_for(
    monkeypatch: pytest.MonkeyPatch,
    receiver: CarrierRequestRouter,
) -> tuple[RemoteOperationService, AsyncMock]:
    async def transport(payload: dict[str, Any], **_kwargs: Any) -> Envelope:
        response = await receiver(
            Envelope(
                EnvelopeType.REQUEST,
                payload=json.loads(json.dumps(payload)),
            )
        )
        assert isinstance(response, Envelope)
        if response.type == EnvelopeType.ERROR:
            raise CarrierRemoteError(response.payload)
        return response

    release = AsyncMock()
    lease = SimpleNamespace(carrier=SimpleNamespace(request=transport), release=release)
    service = RemoteOperationService(cast(Any, SimpleNamespace()))
    monkeypatch.setattr(service, "_lease", AsyncMock(return_value=lease))
    return service, release


@pytest.mark.asyncio
@pytest.mark.parametrize("represented", [False, True])
async def test_carrier_uses_only_selected_native_owner_and_forwards_bounds(
    represented: bool,
) -> None:
    calls = []

    def read(session_id: str, **bounds: Any) -> dict[str, Any]:
        calls.append((session_id, bounds))
        return snapshot()

    client = SimpleNamespace(
        **{
            "get_live_result_snapshot" if represented else "get_result_snapshot": read,
        }
    )
    response = await route(client, represented=represented, position="opaque")
    assert response.type == EnvelopeType.RESPONSE
    assert response.payload["result"]["identity"]["native_session_id"] == "native-a"
    assert calls == [
        (
            "managed-a",
            {
                "require_native_identity": True,
                "position": "opaque",
                "max_items": 2,
                "max_text_chars": 256,
            },
        )
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"max_items": True},
        {"max_items": 0},
        {"max_items": 101},
        {"max_text_chars": 255},
        {"max_text_chars": 20001},
        {"max_items": "2"},
        {"position": "x" * 2049},
        {"represented": "false"},
        {"session_id": "../other"},
        {"native_session_id": ""},
        {"force": True},
    ],
)
async def test_invalid_request_refuses_before_client_acquisition(changes: dict[str, Any]) -> None:
    response = await route(SimpleNamespace(), **changes)
    assert response.type == EnvelopeType.ERROR
    assert response.payload["status"] == 400
    assert response.payload["code"] == "invalid_request"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["native", "alias", "shape", "bounds"])
async def test_carrier_refuses_changed_identity_or_invalid_response(fault: str) -> None:
    value = snapshot()
    if fault == "native":
        value["identity"]["native_session_id"] = "replacement"
    elif fault == "alias":
        value["identity"]["snapshot_session_id"] = "replacement"
    elif fault == "bounds":
        value["limits"]["used_text_chars"] = 257
    else:
        value.pop("state")
    response = await route(SimpleNamespace(get_result_snapshot=lambda *_a, **_kw: value))
    assert response.type == EnvelopeType.ERROR
    assert response.payload["status"] == (409 if fault in {"native", "alias"} else 502)
    assert "result" not in response.payload


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["receiver", "requester"])
@pytest.mark.parametrize(
    "content",
    ["latest", "active", "milestones", "pending_input", "pending_messages", "incremental", "aggregate"],
)
async def test_underreported_text_cannot_exceed_actual_result_budget(
    monkeypatch: pytest.MonkeyPatch, adapter: str, content: str,
) -> None:
    value = snapshot()
    if content in {"latest", "aggregate"}:
        value["latest_result"] = {"availability": "available", "value": {"text": "x" * 200}}
        if content == "latest":
            value["latest_result"]["value"]["text"] *= 20
    if content == "active":
        value["state"]["active_work"] = {
            "availability": "available", "value": {"tool": {"command": "x" * 4096}},
        }
    elif content == "milestones":
        value["state"]["active_work"] = {
            "availability": "available", "value": {"milestones": {"x" * 200: "y" * 200}},
        }
    elif content == "pending_input":
        value["state"]["pending_input"] = {
            "availability": "available", "value": [{"message": "x" * 4096}],
        }
    elif content == "pending_messages":
        value["state"]["pending_messages"] = {
            "availability": "available", "value": [{"sender": "x" * 200, "kind": "y" * 200}],
        }
    elif content in {"incremental", "aggregate"}:
        value["incremental"] = {
            "availability": "available",
            "items": [{
                "event_id": 1, "kind": "progress", "summary": "x" * 200,
                "status": "y" * (200 if content == "incremental" else 0),
                "timestamp": 1.0, "detail_ref": "event:1",
            }],
        }
    assert value["limits"]["used_text_chars"] == 0
    if adapter == "receiver":
        response = await route(SimpleNamespace(get_result_snapshot=lambda *_a, **_kw: value))
        assert response.type == EnvelopeType.ERROR
        assert response.payload["status"] == 502
    else:
        service = RemoteOperationService(cast(Any, SimpleNamespace()))
        monkeypatch.setattr(service, "_request", AsyncMock(return_value={"result": value}))
        with pytest.raises(RemoteBridgeError) as error:
            await service.result_snapshot(
                "host-b", session_id="managed-a", native_session_id="native-a",
                represented=False, max_items=2, max_text_chars=256,
            )
        assert error.value.status == 502


@pytest.mark.asyncio
async def test_actual_text_budget_excludes_native_metadata_and_fixed_tool_fallback() -> None:
    value = snapshot()
    value["latest_result"] = {
        "availability": "available",
        "value": {"text": "x" * 256, "completed_at": "2026-01-01T00:00:00+00:00"},
    }
    value["state"]["active_work"] = {"availability": "available", "value": {"tool": {"title": "tool"}}}
    value["limits"]["used_text_chars"] = 256
    response = await route(SimpleNamespace(get_result_snapshot=lambda *_a, **_kw: value))
    assert response.type == EnvelopeType.RESPONSE


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter", ["receiver", "requester"])
@pytest.mark.parametrize("count", [2, 3])
async def test_pending_message_count_obeys_requested_item_limit(
    monkeypatch: pytest.MonkeyPatch, adapter: str, count: int,
) -> None:
    value = snapshot()
    value["state"]["pending_messages"] = {
        "availability": "available",
        "value": [{"message_id": index, "sender": "", "kind": ""} for index in range(count)],
    }
    if adapter == "receiver":
        response = await route(SimpleNamespace(get_result_snapshot=lambda *_a, **_kw: value))
        assert response.type == (EnvelopeType.RESPONSE if count == 2 else EnvelopeType.ERROR)
        if count == 3:
            assert response.payload["status"] == 502
    else:
        service = RemoteOperationService(cast(Any, SimpleNamespace()))
        monkeypatch.setattr(service, "_request", AsyncMock(return_value={"result": value}))
        observe = service.result_snapshot(
            "host-b", session_id="managed-a", native_session_id="native-a",
            represented=False, max_items=2, max_text_chars=256,
        )
        if count == 2:
            assert len((await observe)["state"]["pending_messages"]["value"]) == 2
        else:
            with pytest.raises(RemoteBridgeError) as error:
                await observe
            assert error.value.status == 502


@pytest.mark.asyncio
async def test_native_capability_refusal_survives_without_legacy_fallback() -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> Any:
        raise BridgeClientError(
            426, {"code": "unsupported_version", "message": "native origin unavailable"}
        )

    response = await route(SimpleNamespace(get_result_snapshot=refuse))
    assert response.type == EnvelopeType.ERROR
    assert response.payload["status"] == 426
    assert response.payload["code"] == "unsupported_version"


@pytest.mark.asyncio
@pytest.mark.parametrize("represented", [False, True])
async def test_old_http_provider_receives_only_health_reads(
    monkeypatch: pytest.MonkeyPatch,
    represented: bool,
) -> None:
    client = BridgeClient("http://fixture", token="fixture")
    calls = []

    def health_only(method: str, path: str, **_kwargs: Any) -> Any:
        calls.append((method, path))
        assert (method, path) == ("GET", "/health")
        return {
            "protocol_version": HTTP_PROTOCOL_VERSION,
            "min_protocol_version": 1,
            "capabilities": {},
        }

    monkeypatch.setattr(client, "_request", health_only)
    response = await route(client, represented=represented)
    assert response.type == EnvelopeType.ERROR
    assert response.payload["status"] == 426
    assert calls == [("GET", "/health"), ("GET", "/health")]


@pytest.mark.asyncio
async def test_prior_carrier_refuses_new_read_and_releases_original_lease(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receiver = CarrierRequestRouter(lambda: pytest.fail("old operation cannot acquire client"))
    monkeypatch.setattr(CarrierRequestRouter, "_OPERATIONS", receiver._OPERATIONS - {OPERATION})
    service, release = service_for(monkeypatch, receiver)
    with pytest.raises(RemoteBridgeError) as error:
        await service.result_snapshot(
            "host-b",
            session_id="managed-a",
            native_session_id="native-a",
            represented=False,
        )
    assert (error.value.status, error.value.code) == (501, "unsupported_operation")
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_uses_readonly_v1_and_revalidates_peer_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = RemoteOperationService(cast(Any, SimpleNamespace()))
    calls = []
    value = snapshot()

    async def transport(host: str, payload: Any, **kwargs: Any) -> dict[str, Any]:
        calls.append((host, payload, kwargs))
        return {"result": value}

    monkeypatch.setattr(service, "_request", transport)
    result = await service.result_snapshot(
        "host-b",
        session_id="managed-a",
        native_session_id="native-a",
        represented=False,
        max_items=2,
        max_text_chars=256,
    )
    assert result["identity"]["native_session_id"] == "native-a"
    from agent_bridge.remote_operation_versions import request_operation_version

    assert request_operation_version(calls[0][1]) == 1
    assert calls == [
        (
            "host-b",
            {key: val for key, val in {**request(), "position": None}.items() if key != "version"},
            {"timeout": 15.0},
        ),
    ]
    value["identity"]["native_session_id"] = "replacement"
    with pytest.raises(RemoteBridgeError) as error:
        await service.result_snapshot(
            "host-b",
            session_id="managed-a",
            native_session_id="native-a",
            represented=False,
            max_items=2,
            max_text_chars=256,
        )
    assert error.value.code == "bridge_observation_identity_changed"


@pytest.mark.asyncio
async def test_service_timeout_includes_acquisition_and_preserves_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge import remote_observation as module

    monkeypatch.setattr(module, "READ_TIMEOUT", 0.02)
    cancelled = asyncio.Event()
    service = RemoteOperationService(cast(Any, SimpleNamespace()))

    async def blocked(*_a: Any, **_kw: Any) -> Any:
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    monkeypatch.setattr(service, "_request", blocked)
    with pytest.raises(RemoteBridgeError) as error:
        await service.result_snapshot(
            "host-b",
            session_id="managed-a",
            native_session_id="native-a",
            represented=False,
        )
    assert error.value.status == 504
    assert cancelled.is_set()


def test_native_http_negotiation_and_result_share_one_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])

    def factory() -> Any:
        clock[0] = 15.1
        return SimpleNamespace()

    with pytest.raises(RemoteBridgeError) as error:
        read_native_result(
            factory,
            ResultRequest(
                session_id="managed-a",
                native_session_id="native-a",
                represented=False,
            ),
        )
    assert error.value.status == 504


@pytest.mark.asyncio
@pytest.mark.parametrize("represented", [False, True])
async def test_carrier_reaches_real_native_client_api_without_cursor_writes(
    represented: bool,
    api: Any,
    session_manager: Any,
    tmp_db: Any,
    mock_acp_client: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent_bridge.events import EventLog
    from agent_bridge.routes.sessions import router

    if represented:
        session_id, native = "live-1", "live-1"
        http = api
    else:
        session_id, native = "bridge-a", "native-a"
        session = prepare(session_manager, tmp_db, mock_acp_client)
        session.event_log = EventLog(session_id=session_id)
        app = FastAPI()
        app.state.session_manager = session_manager
        app.include_router(router)
        http = TestClient(app)
    client = BridgeClient("http://fixture", token="fixture")
    calls = []

    def http_request(method: str, path: str, params: Any) -> Any:
        calls.append((method, path, copy.deepcopy(params)))
        if path == "/health":
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION,
                "min_protocol_version": 1,
                "capabilities": {"result_native_identity": 1},
            }
        response = http.request(method, path, params=params)
        if response.status_code >= 400:
            raise BridgeClientError(response.status_code, response.json().get("detail"))
        return response.json()

    from agent_bridge import client_read_budget

    class Response(io.BytesIO):
        status = 200

    @contextmanager
    def open_response(req: Any, *, timeout: float) -> Any:
        assert timeout > 0
        left = client_read_budget.remaining()
        assert left is not None and 0 < left <= 15
        url = urlsplit(req.full_url)
        value = http_request(req.get_method(), url.path, dict(parse_qsl(url.query)))
        with Response(json.dumps(value).encode()) as response:
            yield response

    monkeypatch.setattr(client_read_budget, "open_response", open_response)
    service, release = service_for(monkeypatch, CarrierRequestRouter(lambda: client))
    before = list(tmp_db._get_conn().iterdump())
    result = await service.result_snapshot(
        "host-b",
        represented=represented,
        session_id=session_id,
        native_session_id=native,
        max_items=2,
        max_text_chars=256,
    )
    assert result["identity"]["native_session_id"] == native
    release.assert_awaited_once()
    prefix = "live-sessions" if represented else "sessions"
    assert [entry[:2] for entry in calls] == [
        ("GET", "/health"),
        ("GET", "/health"),
        ("GET", f"/api/v1/{prefix}/{session_id}/result"),
    ]
    assert calls[-1][2]["native_only"] == "true"
    assert list(tmp_db._get_conn().iterdump()) == before
