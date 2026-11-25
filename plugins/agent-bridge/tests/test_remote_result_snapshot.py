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
from agent_bridge.remote_observation import (
    OPERATION, ResultRequest, read_native_result, validate_result,
)

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
@pytest.mark.parametrize("entry", ["carrier", "http", "cli-read", "cli-wait"])
async def test_carrier_reaches_real_native_client_api_without_cursor_writes(
    represented: bool,
    entry: str,
    api: Any,
    session_manager: Any,
    tmp_db: Any,
    mock_acp_client: Any,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent_bridge.events import EventLog
    from agent_bridge.routes.sessions import router

    if represented:
        session_id, native = "live-1", "live-1"
        http = api
        if entry == "cli-wait":
            tmp_db.update_live_turn_state(session_id, turn_state="idle", last_activity_at=time.time())
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
    observer_calls = []
    observer_http = None

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
        url = urlsplit(req.full_url)
        if entry in {"carrier", "http"} or url.path.endswith("/result"):
            assert left is not None and 0 < left <= 15
        elif left is not None:
            assert 0 < timeout <= left + 0.05
        params = dict(parse_qsl(url.query))
        if url.netloc == "observer":
            observer_calls.append((req.get_method(), url.path, params))
            if url.path == "/health":
                value = {
                    "protocol_version": HTTP_PROTOCOL_VERSION,
                    "min_protocol_version": 1,
                    "capabilities": {"remote_native_result": 1, "bridge_inventory": 2},
                }
            else:
                assert observer_http is not None
                proxy_response = observer_http.request(req.get_method(), url.path, params=params)
                assert proxy_response.status_code == 200, proxy_response.text
                value = proxy_response.json()
        else:
            value = http_request(req.get_method(), url.path, params)
        with Response(json.dumps(value).encode()) as response:
            yield response

    monkeypatch.setattr(client_read_budget, "open_response", open_response)
    service, release = service_for(monkeypatch, CarrierRequestRouter(lambda: client))
    before = list(tmp_db._get_conn().iterdump())
    options: dict[str, Any] = {
        "represented": represented, "session_id": session_id,
        "native_session_id": native, "max_items": 2, "max_text_chars": 256,
    }
    if entry != "carrier":
        from agent_bridge.routes.remote import router as remote_router

        observer = FastAPI()
        observer.state.remote_operations = service
        observer.include_router(remote_router)

        @observer.get("/api/v1/bridges")
        def bridges(hosts: str) -> dict[str, Any]:
            assert hosts == "host-b"
            return {
                "version": 1, "identity_version": 2, "complete": True, "sources": {},
                "bridges": [{
                    "handle": "workspace-a", "status": "running", "control": "available",
                    "session_id": native, "route": {"host": hosts},
                    "managed_session_id": None if represented else session_id,
                    "registered_session_id": session_id if represented else None,
                }],
            }

        observer_http = TestClient(observer)
        observer_client = BridgeClient("http://observer", "fixture")
        if entry == "http":
            result = observer_client.get_remote_result_snapshot("host-b", **options)
            assert observer_calls == [
                ("GET", "/health", {}),
                ("GET", f"/api/v1/remote/host-b/sessions/{session_id}/result", {
                    "native_session_id": native,
                    "represented": "true" if represented else "false",
                    "max_items": "2", "max_text_chars": "256",
                }),
            ]
        else:
            from agent_bridge import __main__ as cli

            def get_client(**kwargs: Any) -> BridgeClient:
                assert kwargs == {"ensure": False}
                return observer_client

            monkeypatch.setattr(cli, "_get_client", get_client)
            monkeypatch.setattr(cli, "_sender_repo", lambda: "demo")
            command = entry.removeprefix("cli-")
            extra = ["--max-items", "2", "--max-text-chars", "256"] if command == "read" else []
            cli.main([
                "--json", command, "workspace-a", "--bridge", "--host", "host-b", *extra,
            ])
            result = json.loads(capsys.readouterr().out)
            if command == "wait":
                assert result["settled"] is True and result["reason"] == "idle"
                result = result["snapshot"]
            assert all(method == "GET" for method, _path, _params in observer_calls)
            assert any(path == "/api/v1/bridges" for _method, path, _params in observer_calls)
            result_path = f"/api/v1/remote/host-b/sessions/{session_id}/result"
            result_index = next(i for i, call in enumerate(observer_calls) if call[1] == result_path)
            if command == "wait" and represented:
                registration_path = f"/api/v1/remote/host-b/live-sessions/{session_id}"
                assert observer_calls[-1][1] == registration_path
                assert result_index < len(observer_calls) - 1
    else:
        result = await service.result_snapshot("host-b", **options)
    assert result["identity"]["native_session_id"] == native
    assert release.await_count == (2 if entry == "cli-wait" and represented else 1)
    prefix = "live-sessions" if represented else "sessions"
    assert [entry[:2] for entry in calls] == [
        ("GET", "/health"),
        ("GET", "/health"),
        ("GET", f"/api/v1/{prefix}/{session_id}/result"),
        *([("GET", "/api/v1/live-sessions/resolve")] if entry == "cli-wait" and represented else []),
    ]
    assert calls[2][2]["native_only"] == "true"
    assert list(tmp_db._get_conn().iterdump()) == before


@pytest.mark.parametrize("advertised", [None, True, "1", 0])
def test_remote_http_read_refuses_prior_or_invalid_capability_before_request(
    monkeypatch: pytest.MonkeyPatch, advertised: Any,
) -> None:
    client = BridgeClient("http://observer", "fixture")
    calls = []

    def health_only(method: str, path: str, **_kwargs: Any) -> Any:
        calls.append((method, path))
        assert (method, path) == ("GET", "/health")
        return {
            "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
            "capabilities": {"remote_native_result": advertised},
        }

    monkeypatch.setattr(client, "_request", health_only)
    with pytest.raises(BridgeClientError) as error:
        client.get_remote_result_snapshot(
            "host-b", session_id="managed-a", native_session_id="native-a", represented=False,
        )
    assert error.value.status == 426
    assert calls == [("GET", "/health")]


@pytest.mark.parametrize(
    ("changes", "status"),
    [
        ({"native_session_id": "../other"}, 400),
        ({"native_session_id": ""}, 422),
        ({"represented": None}, 422),
        ({"max_items": 0}, 422),
        ({"position": "x" * 2049}, 422),
    ],
)
def test_remote_http_read_rejects_invalid_selection_before_service(
    changes: dict[str, Any], status: int,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent_bridge.routes.remote import router

    app = FastAPI()
    app.include_router(router)
    params = {
        key: value for key, value in {
            "native_session_id": "native-a", "represented": "false", **changes,
        }.items() if value is not None
    }
    response = TestClient(app).get(
        "/api/v1/remote/host-b/sessions/managed-a/result", params=params,
    )
    assert response.status_code == status


def test_remote_http_read_preserves_prior_carrier_refusal_without_native_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent_bridge.routes.remote import router

    receiver = CarrierRequestRouter(lambda: pytest.fail("prior carrier cannot read native state"))
    monkeypatch.setattr(CarrierRequestRouter, "_OPERATIONS", receiver._OPERATIONS - {OPERATION})
    service, release = service_for(monkeypatch, receiver)
    app = FastAPI()
    app.state.remote_operations = service
    app.include_router(router)
    response = TestClient(app).get(
        "/api/v1/remote/host-b/sessions/managed-a/result",
        params={"native_session_id": "native-a", "represented": "false"},
    )
    assert response.status_code == 501
    assert response.json()["detail"]["code"] == "unsupported_operation"
    release.assert_awaited_once()


@pytest.mark.parametrize("phase", ["health", "result", "validation"])
def test_remote_http_read_keeps_one_deadline_for_negotiation_and_result(
    monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    from agent_bridge.client_read_budget import ReadDeadlineExpired, command_deadline, remaining

    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    client = BridgeClient("http://observer", "fixture")
    calls = []
    from agent_bridge import client_remote_observation

    def checked(*args: Any) -> Any:
        value = validate_result(*args)
        if phase == "validation":
            clock[0] = 1.1
        return value

    monkeypatch.setattr(client_remote_observation, "validate_result", checked)

    def read(method: str, path: str, **_kwargs: Any) -> Any:
        calls.append((method, path, remaining()))
        if path == "/health":
            clock[0] = 1.1 if phase == "health" else 0.6
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                "capabilities": {"remote_native_result": 1},
            }
        clock[0] = 0.8 if phase == "validation" else 1.1
        return snapshot()

    monkeypatch.setattr(client, "_request", read)
    with pytest.raises(ReadDeadlineExpired), command_deadline(1):
        client.get_remote_result_snapshot(
            "host-b", session_id="managed-a", native_session_id="native-a",
            represented=False, max_items=2, max_text_chars=256,
        )
    assert calls[0] == ("GET", "/health", 1)
    assert len(calls) == (1 if phase == "health" else 2)


@pytest.mark.parametrize("fault", ["native", "owner", "bounds"])
def test_remote_http_read_rejects_unconfirmed_result_before_return(
    monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    value = snapshot()
    if fault == "bounds":
        value["latest_result"] = {"availability": "available", "value": {"text": "x" * 257}}
    else:
        field = "native_session_id" if fault == "native" else "snapshot_session_id"
        value["identity"][field] = "replacement"
    client = BridgeClient("http://observer", "fixture")

    def read(_method: str, path: str, **_kwargs: Any) -> Any:
        if path == "/health":
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
                "capabilities": {"remote_native_result": 1},
            }
        return value

    monkeypatch.setattr(client, "_request", read)
    with pytest.raises(BridgeClientError) as error:
        client.get_remote_result_snapshot(
            "host-b", session_id="managed-a", native_session_id="native-a",
            represented=False, max_items=2, max_text_chars=256,
        )
    assert error.value.status == (502 if fault == "bounds" else 409)
