"""HTTP prompt expansion preserves exact binding and one-attempt admission."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from email.message import Message
import importlib
import io
import json
import math
from types import SimpleNamespace
import time
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
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION, NATIVE_SEND_NO_RESUME_CAPABILITY
from agent_bridge.remote_operations import CarrierRequestRouter
from agent_bridge.routes.remote import router

carrier_tests = importlib.import_module("tests.test_remote_native_prompt")
native_send = carrier_tests.native_send
transport_owner = importlib.import_module("tests.test_native_turn_transport").transport_owner


def options(**changes: Any) -> dict[str, Any]:
    return {
        key: value for key, value in carrier_tests.request(**changes).items()
        if key not in {"version", "operation"}
    }


def health(capability: Any = 1) -> dict[str, Any]:
    return {
        "protocol_version": HTTP_PROTOCOL_VERSION, "min_protocol_version": 1,
        "capabilities": {"remote_native_prompt": capability},
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
        client.submit_remote_native_prompt("host-b", **options())
    assert error.value.status == 426
    assert calls == [("GET", "/health")]


@pytest.mark.parametrize("fault", [
    "draining", "disconnect", "unbound-426", "wrong-host", "wrong-key", "shape", "interrupt",
])
def test_post_failure_never_reconnects_or_replays(
    monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    resolve = []
    def reresolve() -> str:
        resolve.append(True)
        return "http://replacement"

    client = BridgeClient(
        "http://observer", "fixture", connect_grace=30,
        reresolve=reresolve,
    )
    calls = []

    def request(method: str, path: str, body: Any = None, **kwargs: Any) -> Any:
        calls.append((method, path))
        assert kwargs["allow_retry"] is False
        assert kwargs["_endpoint"] == "http://observer"
        if method == "GET":
            return health()
        assert body == {key: value for key, value in options().items() if key != "session_id"}
        if fault == "draining":
            raise BridgeClientError(503, "draining")
        if fault == "disconnect":
            raise BridgeConnectionError("accepted response lost")
        if fault == "interrupt":
            raise KeyboardInterrupt
        if fault == "unbound-426":
            raise BridgeClientError(426, {"code": "unsupported_version"})
        return carrier_tests.receipt(
            host="other" if fault == "wrong-host" else "host-b",
            invocation_key="fresh-key" if fault == "wrong-key" else "original-key",
        ) if fault != "shape" else None

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(BridgeClientError) as error:
        client.submit_remote_native_prompt("host-b", **options())
    detail = error.value.detail
    assert detail["code"] == "native_prompt_admission_unknown"
    assert detail["admission_status"] == "unknown"
    assert detail["host"] == "host-b"
    assert detail["invocation_key"] == "original-key"
    assert detail["session_id"] == "bridge-a" and detail["native_session_id"] == "native-a"
    assert [method for method, _path in calls] == ["GET", "POST"]
    assert resolve == []


def test_interrupt_before_admission_keeps_ordinary_interrupt_without_post(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = BridgeClient("http://observer", "fixture")
    calls = []

    def request(method: str, path: str, *_args: Any, **_kwargs: Any) -> Any:
        calls.append((method, path))
        raise KeyboardInterrupt

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(KeyboardInterrupt):
        client.submit_remote_native_prompt("host-b", **options())
    assert calls == [("GET", "/health")]


@pytest.mark.parametrize("code,status", [
    ("unsupported_operation", 501), ("unsupported_version", 426),
    ("native_prompt_frame_refused", 413), ("native_prompt_refused", 409),
    ("native_prompt_admission_unknown", 504),
])
@pytest.mark.parametrize("bound", [True, False])
def test_only_bound_typed_errors_retain_meaning(
    monkeypatch: pytest.MonkeyPatch, code: str, status: int, bound: bool,
) -> None:
    client = BridgeClient("http://observer", "fixture")
    detail = {
        "code": code, "reconnectable": False, "host": "host-b",
        **{key: options()[key] for key in ("session_id", "native_session_id", "invocation_key")},
    }
    if not bound:
        detail["native_session_id"] = "replacement"

    def request(method: str, _path: str, *_args: Any, **_kwargs: Any) -> Any:
        if method == "GET":
            return health()
        raise BridgeClientError(status, detail)

    monkeypatch.setattr(client, "_request", request)
    with pytest.raises(BridgeClientError) as error:
        client.submit_remote_native_prompt("host-b", **options())
    assert error.value.detail["code"] == (code if bound else "native_prompt_admission_unknown")
    assert error.value.detail["native_session_id"] == "native-a"
    if bound:
        assert error.value.status == status


@pytest.mark.parametrize("phase", ["health", "post"])
def test_one_deadline_covers_health_and_admission(
    monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    client = BridgeClient("http://observer", "fixture")
    calls = []

    def request(method: str, path: str, *_args: Any, **_kwargs: Any) -> Any:
        calls.append((method, path))
        assert client_read_budget.remaining() == 5
        if phase == "health" or method == "POST":
            clock[0] = 6
        return health() if method == "GET" else carrier_tests.receipt(host="host-b")

    monkeypatch.setattr(client, "_request", request)
    with client_read_budget.command_deadline(5):
        expected = client_read_budget.ReadDeadlineExpired if phase == "health" else BridgeClientError
        with pytest.raises(expected) as error:
            client.submit_remote_native_prompt("host-b", **options())
    if phase == "post":
        assert isinstance(error.value, BridgeClientError)
        assert error.value.status == 504
        assert error.value.detail["code"] == "native_prompt_admission_unknown"
    assert [method for method, _path in calls] == (["GET"] if phase == "health" else ["GET", "POST"])


@pytest.mark.parametrize("changes", [
    {"invocation_key": " "}, {"native_session_id": ""}, {"prompt": True},
])
def test_invalid_client_request_never_acquires_service(
    monkeypatch: pytest.MonkeyPatch, changes: dict[str, Any],
) -> None:
    client = BridgeClient("http://observer", "fixture")
    monkeypatch.setattr(client, "_request", lambda *_a, **_kw: pytest.fail("invalid prompt sent"))
    with pytest.raises(BridgeClientError) as error:
        client.submit_remote_native_prompt("host-b", **options(**changes))
    assert error.value.status == 400


@pytest.mark.parametrize("field,value", [("no_resume", False), ("prompt", " "), ("invocation_key", "")])
def test_http_rejects_invalid_body_before_service(field: str, value: Any) -> None:
    app = FastAPI()
    app.state.remote_operations = SimpleNamespace(
        submit_native_prompt=lambda *_a, **_kw: pytest.fail("invalid prompt reached service"),
    )
    app.include_router(router)
    body = {key: value for key, value in options().items() if key != "session_id"}
    body[field] = value
    with TestClient(app) as api:
        response = api.post("/api/v1/remote/host-b/sessions/bridge-a/native-prompt", json=body)
    assert response.status_code == 422


def test_http_prior_carrier_refusal_retains_binding_without_native_access(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def prior(_payload: Any, **_kwargs: Any) -> Any:
        raise CarrierRemoteError({
            "code": "unsupported_operation", "status": 501, "message": "prior carrier",
        })

    transport = AsyncMock(side_effect=prior)
    service, release = carrier_tests.service_for(monkeypatch, transport)
    app = FastAPI()
    app.state.remote_operations = service
    app.include_router(router)
    with TestClient(app) as api:
        response = api.post(
            "/api/v1/remote/host-b/sessions/bridge-a/native-prompt",
            json={key: value for key, value in options().items() if key != "session_id"},
        )
    assert response.status_code == 501
    detail = response.json()["detail"]
    assert detail["code"] == "unsupported_operation"
    assert detail["host"] == "host-b"
    assert detail["session_id"] == "bridge-a" and detail["native_session_id"] == "native-a"
    assert detail["invocation_key"] == "original-key" and detail["reconnectable"] is False
    assert transport.await_count == 1 and release.await_count == 1


@pytest.mark.parametrize("consumer,lost_reply", [
    ("client", False), ("client", True), ("cli", False), ("cli", True), ("cli-text", False),
    ("cli-text", True), ("cli-hosting-required", False),
    ("cli-interrupt-json", False), ("cli-interrupt-text", False),
])
def test_http_carrier_native_api_admits_one_original_turn(
    native_send: Any, monkeypatch: pytest.MonkeyPatch, lost_reply: bool,
    consumer: str, capsys: pytest.CaptureFixture[str], request: pytest.FixtureRequest,
) -> None:
    from agent_bridge import __main__ as cli
    from agent_bridge.routes.sessions import router as native_router

    interrupted = consumer in {"cli-interrupt-json", "cli-interrupt-text"}
    as_text = consumer in {"cli-text", "cli-interrupt-text"}
    generated_key = interrupted or consumer == "cli-text" and lost_reply
    cli_entry = consumer != "client"
    manager, session, _original = native_send
    if consumer == "cli-text":
        from agent_bridge.events import EventLog
        from agent_bridge.session_status import SessionStatus

        selected = request.getfixturevalue("transport_owner")
        manager, session, _client, owner, _producer, admission = selected
        session.event_log = EventLog(db=manager.db, session_id=session.session_id)
        asyncio.run(manager.enroll_native_turn_transport(session.session_id, owner, admission))

        async def finish(active: Any, index: int, _prompt: str, *, native_attempt: Any) -> None:
            assert native_attempt is not None
            row = manager.db.get_turn(active.session_id, index)
            assert row["native_handoff_json"]
            manager.db.update_turn(
                active.session_id, index, response_text="Exact completed native result.",
                completed_at=time.time(), stop_reason="end_turn",
            )
            manager.db.flush()
            active.status = SessionStatus.IDLE

        manager._run_prompt.side_effect = finish
    session_id = session.session_id
    native = FastAPI()
    native.state.session_manager = manager
    native.include_router(native_router)
    native_client = BridgeClient("http://native", "fixture")
    receiver = CarrierRequestRouter(lambda: native_client)
    carrier_calls = []

    async def transport(payload: dict[str, Any], **_kwargs: Any) -> Envelope:
        carrier_calls.append(payload)
        result = await receiver(Envelope(EnvelopeType.REQUEST, payload=payload))
        assert isinstance(result, Envelope)
        if result.type == EnvelopeType.ERROR:
            raise CarrierRemoteError(result.payload)
        return result

    service, release = carrier_tests.service_for(monkeypatch, transport)
    observer = FastAPI()
    observer.state.remote_operations = service
    observer.include_router(router)
    calls = []

    class Response(io.BytesIO):
        status = 200

    with TestClient(native) as native_api, TestClient(observer) as observer_api:
        @contextmanager
        def open_response(req: Any, *, timeout: float) -> Any:
            url = urlsplit(req.full_url)
            method = req.get_method()
            body = json.loads(req.data) if req.data else None
            calls.append((url.netloc, method, url.path))
            if client_read_budget.remaining() is None:
                assert cli_entry and method == "GET"
                limit = 25.0 if url.path == "/api/v1/bridges" else 120.0
            else:
                limit = 20.0
            assert timeout > 0
            assert timeout <= limit or math.isclose(
                timeout, limit, rel_tol=0, abs_tol=time.get_clock_info("monotonic").resolution,
            )
            if url.netloc == "native" or method == "POST":
                assert client_read_budget.remaining() is not None
            if url.path == "/health":
                value = {
                    **health(), "capabilities": {
                        "remote_native_prompt": 1, "bridge_inventory": 2, "remote_native_result": 1,
                        "repository_agent_hosting": 1,
                    },
                } if url.netloc == "observer" else {
                    **health(), "capabilities": {
                        NATIVE_SEND_NO_RESUME_CAPABILITY: 1, "result_native_identity": 1,
                    },
                }
            elif url.path == "/api/v1/bridges":
                from urllib.parse import parse_qs

                assert parse_qs(url.query) == {"hosts": ["host-b"], "project": ["demo"]}
                value = {
                    "version": 1, "identity_version": 2, "complete": True, "sources": {},
                    "bridges": [{
                        "handle": "workspace-a", "status": "running", "control": "managed",
                        "managed_session_id": session_id, "session_id": "native-a",
                        "route": {"host": "host-b"},
                    }],
                }
            else:
                api = observer_api if url.netloc == "observer" else native_api
                response = api.request(method, url.path + ("?" + url.query if url.query else ""), json=body)
                if response.is_error:
                    raise HTTPError(req.full_url, response.status_code, "fixture", Message(), io.BytesIO(response.content))
                value = response.json()
                if interrupted and url.netloc == "observer" and method == "POST":
                    raise KeyboardInterrupt
                if lost_reply and url.netloc == "observer" and method == "POST":
                    raise BridgeConnectionError("observer response lost after admission")
            with Response(json.dumps(value).encode()) as raw_response:
                yield raw_response

        monkeypatch.setattr(client_read_budget, "open_response", open_response)
        client = BridgeClient("http://observer", "fixture")
        if cli_entry:
            monkeypatch.setattr(cli, "_get_client", lambda **_kwargs: client)
            monkeypatch.setattr(cli, "_sender_repo", lambda: "demo")
            monkeypatch.setattr(cli, "_caller_id_for", lambda _args: "fixture-controller")
            argv = [
                *([] if as_text else ["--json"]),
                "send", "workspace-a", carrier_tests.request()["prompt"],
                "--bridge", "--host", "host-b",
                *([] if generated_key else ["--idempotency-key", "original-key"]),
                *(["--require-agent-hosting"] if consumer == "cli-hosting-required" else []),
            ]
            monkeypatch.setattr(cli, "_phased_timeouts", lambda: SimpleNamespace(command=5.0))
            if consumer == "cli-hosting-required":
                with pytest.raises(SystemExit) as error:
                    cli.main(argv)
                assert error.value.code == 2
                assert json.loads(capsys.readouterr().out)["reason"] == "bridge_send_options"
            elif lost_reply or interrupted:
                with pytest.raises(SystemExit) as error:
                    cli.main(argv)
                assert error.value.code == 70
                output = capsys.readouterr()
                if as_text:
                    prompt = next(value for value in carrier_calls if value["operation"] == "session.native_prompt")
                    key = prompt["invocation_key"]
                    assert len(key) == 32
                    assert not output.out
                    assert all(value in output.err for value in ("host-b", session_id, "native-a", key))
                else:
                    payload = json.loads(output.out)
                    assert payload["outcome"] == "unknown" and payload["retryable"] is False
                    if generated_key:
                        prompt = next(value for value in carrier_calls if value["operation"] == "session.native_prompt")
                        assert payload["idempotency_key"] == prompt["invocation_key"]
                        assert payload["bridge"]["host"] == "host-b"
                        assert payload["bridge"]["managed_session_id"] == session_id
                        assert payload["bridge"]["session_id"] == "native-a"
            else:
                cli.main(argv)
                output = capsys.readouterr()
                if consumer == "cli-text":
                    assert session._prompt_task is not None and session._prompt_task.done()
                    session._prompt_task.result()
                    assert "Exact completed native result." in output.out
                    assert "observation" not in output.err
                else:
                    result = json.loads(output.out)
                    assert result["outcome"] == "delivered"
                    assert result["host"] == "host-b" and result["native_session_id"] == "native-a"
                    assert result["invocation_key"] == "original-key"
        elif lost_reply:
            with pytest.raises(BridgeClientError) as unknown:
                client.submit_remote_native_prompt("host-b", **options())
            assert unknown.value.detail["code"] == "native_prompt_admission_unknown"
        else:
            assert client.submit_remote_native_prompt("host-b", **options()) == carrier_tests.receipt(host="host-b")
    if consumer == "cli-hosting-required":
        assert not calls and not carrier_calls and release.await_count == 0
        assert session.turn_count == 0
        manager._run_prompt.assert_not_awaited()
        manager.resume_session.assert_not_awaited()
        return
    prompts = [value for value in carrier_calls if value["operation"] == "session.native_prompt"]
    assert len(prompts) == 1
    if not generated_key:
        assert prompts[0]["invocation_key"] == "original-key"
    assert release.await_count == len(carrier_calls)
    if not as_text:
        assert len(carrier_calls) == 1
    assert session.turn_count == 1
    manager._run_prompt.assert_awaited_once()
    manager.resume_session.assert_not_awaited()
    manager.handoff_session.assert_not_awaited()
    assert [(host, path) for host, method, path in calls if method == "POST"] == [
        ("observer", f"/api/v1/remote/host-b/sessions/{session_id}/native-prompt"),
        ("native", f"/api/v1/sessions/{session_id}/turns/native-bound/send"),
    ]
