"""Exact bridge selection reaches the existing, fixture-owned admission owners."""

from __future__ import annotations

import importlib
import io
import json
import time
import urllib.error
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any, NoReturn
from urllib.parse import urlsplit

import pytest

from agent_bridge import __main__ as cli
from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
from agent_bridge.protocol import HTTP_PROTOCOL_VERSION

rollover = importlib.import_module("tests.test_live_keyed_receipt").rollover
api = importlib.import_module("tests.test_live_message_receipts").api
prepare = importlib.import_module("tests.test_native_bound_prompt").prepare


def selected(*, represented: bool = True) -> dict[str, Any]:
    return {
        "bridge": {
            "status": "running",
            "session_id": "live-1" if represented else "native-a",
            "registered_session_id": "live-1" if represented else None,
            "managed_session_id": None if represented else "bridge-a",
        },
    }


def wire(
    monkeypatch: pytest.MonkeyPatch,
    api: Any,
    *,
    represented: bool = True,
    before_send: Any = None,
    capability: Any = 1,
    row_changes: Any = None,
    after_send: Any = None,
    transport: bool = False,
) -> list[tuple[str, str, Any]]:
    client = BridgeClient("http://fixture", token="fixture")
    calls: list[tuple[str, str, Any]] = []

    def request(method: str, path: str, body: Any = None, **kwargs: Any) -> Any:
        calls.append((method, path, body))
        if path == "/health":
            return {
                "protocol_version": HTTP_PROTOCOL_VERSION,
                "min_protocol_version": 1,
                "capabilities": {"live_exact_send": capability, "native_send_no_resume": 1},
            }
        if method == "POST" and before_send:
            before_send()
        response = api.request(method, path, json=body)
        if response.status_code >= 400:
            raise BridgeClientError(response.status_code, response.json().get("detail"))
        result = response.json()
        return after_send(result) if method == "POST" and after_send else result

    if transport:
        from agent_bridge import client_read_budget

        class Response(io.BytesIO):
            status = 200

        @contextmanager
        def open_response(req: Any, *, timeout: float) -> Iterator[Response]:
            result = request(
                req.get_method(),
                urlsplit(req.full_url).path,
                json.loads(req.data) if req.data else None,
            )
            with Response(json.dumps(result).encode()) as response:
                yield response

        monkeypatch.setattr(client_read_budget, "open_response", open_response)
    else:
        monkeypatch.setattr(client, "_request", request)
    monkeypatch.setattr(
        client,
        "resolve_bridge",
        lambda *_a, **_kw: {
            "bridge": {**selected(represented=represented)["bridge"], **(row_changes or {})},
        },
    )

    def get_client(**kwargs: Any) -> BridgeClient:
        assert kwargs == {"ensure": False}
        return client

    monkeypatch.setattr(cli, "_get_client", get_client)
    monkeypatch.setattr(cli, "_sender_repo", lambda: "demo")
    monkeypatch.setattr(cli, "_live_sender_label", lambda _args: "peer")
    from agent_bridge import session_targeting_cli

    monkeypatch.setattr(session_targeting_cli, "_caller_worktree_handle", lambda: "caller")
    monkeypatch.setattr(client, "resolve_live_session", lambda _handle: {})
    for name in ("AGENT_BRIDGE_SESSION_ID", "SESSION_ID", "COPILOT_AGENT_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(cli, "_caller_id_for", lambda _args: None)
    monkeypatch.setattr(cli, "_connection_identity", lambda *_a: {})
    return calls


def test_selected_registered_send_persists_exact_input_and_original_receipt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    api: Any,
    tmp_db: Any,
) -> None:
    calls = wire(monkeypatch, api)
    argv = ["--json", "send", "workspace-a", "work", "--bridge", "--idempotency-key", "original"]
    cli.main(argv)
    first = json.loads(capsys.readouterr().out)
    cli.main(argv)
    repeated = json.loads(capsys.readouterr().out)
    assert first["outcome"] == "admitted" and first["delivered"] is False
    assert repeated["outcome"] == "duplicate"
    assert first["message_id"] == repeated["message_id"]
    (row,) = tmp_db.execute_read("SELECT * FROM live_messages")
    assert row["session_id"] == "live-1" and row["exact_session"] == 1
    assert row["body"] == "work" and row["sender"] == "peer"
    assert row["reply_to"] == "caller"
    assert not tmp_db.execute_read("SELECT * FROM pending_prompts")
    posts = [body for method, _path, body in calls if method == "POST"]
    assert len(posts) == 2
    assert all(body["expected_session_id"] == "live-1" and body["exact_session"] for body in posts)


def test_registered_rollover_after_selection_cannot_admit_to_successor(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    api: Any,
    tmp_db: Any,
) -> None:
    calls = wire(monkeypatch, api, before_send=lambda: rollover(tmp_db))
    with pytest.raises(SystemExit) as exc:
        cli.main(["--json", "send", "workspace-a", "work", "--bridge"])
    assert exc.value.code == 69
    assert json.loads(capsys.readouterr().out)["outcome"] == "refused_unavailable"
    assert len([item for item in calls if item[0] == "POST"]) == 1
    assert not tmp_db.execute_read("SELECT * FROM live_messages")


@pytest.mark.parametrize("capability", [None, False, 0, "1"])
def test_registered_provider_rollback_refuses_before_post(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    api: Any,
    tmp_db: Any,
    capability: Any,
) -> None:
    calls = wire(monkeypatch, api, capability=capability)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--json", "send", "workspace-a", "work", "--bridge"])
    assert exc.value.code == 69
    assert "live_exact_send" in json.loads(capsys.readouterr().out)["error"]
    assert not any(method == "POST" for method, _path, _body in calls)
    assert not tmp_db.execute_read("SELECT * FROM live_messages")


@pytest.mark.parametrize("fault", [None, "replaced", "stopped", "busy"])
def test_selected_managed_send_uses_native_turn_lock_without_resume(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    session_manager: Any,
    tmp_db: Any,
    mock_acp_client: Any,
    fault: str | None,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent_bridge.session_status import SessionStatus
    from agent_bridge.routes.sessions import router

    session = prepare(session_manager, tmp_db, mock_acp_client)
    app = FastAPI()
    app.state.session_manager = session_manager
    app.include_router(router)

    def change() -> None:
        if fault == "replaced":
            session.acp_session_id = "native-b"
        elif fault:
            session.status = SessionStatus.STOPPED if fault == "stopped" else SessionStatus.RUNNING

    calls = wire(monkeypatch, TestClient(app), represented=False, before_send=change)
    argv = ["--json", "send", "workspace-a", "work", "--bridge", "--no-wait"]
    if fault:
        with pytest.raises(SystemExit) as exc:
            cli.main(argv)
        assert exc.value.code == (75 if fault == "busy" else 69)
    else:
        cli.main(argv)
        result = json.loads(capsys.readouterr().out)
        assert result["session_id"] == "bridge-a" and result["native_session_id"] == "native-a"
        assert result["queued"] is False
    (post,) = [item for item in calls if item[0] == "POST"]
    assert post[1].endswith("/bridge-a/turns/native-bound")
    assert post[2]["expected_native_session_id"] == "native-a" and post[2]["no_resume"]
    session_manager.resume_session.assert_not_awaited()
    session_manager.handoff_session.assert_not_awaited()
    assert not tmp_db.execute_read("SELECT * FROM pending_prompts")


def test_admitted_selected_prompt_does_not_move_during_later_rollover(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    api: Any,
    tmp_db: Any,
) -> None:
    wire(monkeypatch, api)
    cli.main(["--json", "send", "workspace-a", "work", "--bridge"])
    mid = json.loads(capsys.readouterr().out)["message_id"]
    rollover(tmp_db)
    (row,) = tmp_db.execute_read("SELECT * FROM live_messages")
    assert row["id"] == mid and row["session_id"] == "live-1"
    assert api.get("/api/v1/live-sessions/successor/messages").json()["messages"] == []
    assert tmp_db.consume_live_message("successor", mid, time.time()) == 0


@pytest.mark.parametrize(
    "changes",
    [
        {"status": "dormant"},
        {"status": "retained"},
        {"session_id": None},
        {"managed_session_id": None, "registered_session_id": None},
        {"registered_session_id": "other"},
    ],
)
def test_unavailable_selected_bridge_never_reaches_admission(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    api: Any,
    tmp_db: Any,
    changes: dict[str, Any],
) -> None:
    calls = wire(monkeypatch, api, row_changes=changes)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--json", "send", "workspace-a", "work", "--bridge"])
    assert exc.value.code == 69
    assert json.loads(capsys.readouterr().out)["reason"] == "bridge_send_unavailable"
    assert not calls and not tmp_db.execute_read("SELECT * FROM live_messages")


@pytest.mark.parametrize(
    "error",
    [BridgeConnectionError("fixture unavailable"), BridgeClientError(503, "fixture unavailable")],
)
def test_selection_outage_returns_json_refusal_without_admission(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    api: Any,
    tmp_db: Any,
    error: Exception,
) -> None:
    calls = wire(monkeypatch, api)

    def unavailable(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise error

    monkeypatch.setattr(cli._get_client(ensure=False), "resolve_bridge", unavailable)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--json", "send", "workspace-a", "work", "--bridge"])
    result = json.loads(capsys.readouterr().out)
    assert exc.value.code == 69
    assert result["outcome"] == "refused_unavailable"
    assert result["reason"] == "bridge_selection_unavailable"
    assert not calls
    assert not tmp_db.execute_read("SELECT * FROM live_messages")
    assert not tmp_db.execute_read("SELECT * FROM pending_prompts")


@pytest.mark.parametrize("fault", ["connection", "server", "response", "unrecognized"])
def test_unknown_admission_preserves_exact_binding_without_replay(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    api: Any,
    tmp_db: Any,
    fault: str,
) -> None:
    from agent_bridge.client import BridgeConnectionError

    def unknown(result: dict[str, Any]) -> dict[str, Any]:
        if fault == "connection":
            raise BridgeConnectionError("fixture response lost")
        if fault == "server":
            raise BridgeClientError(500, "fixture response failed after commit")
        if fault == "unrecognized":
            raise BridgeClientError(409, {"code": "fixture_unrecognized"})
        return {**result, "session_id": "unexpected"}

    calls = wire(monkeypatch, api, after_send=unknown)
    with pytest.raises(SystemExit) as exc:
        cli.main(
            [
                "--json",
                "send",
                "workspace-a",
                "work",
                "--bridge",
                "--idempotency-key",
                "original",
            ]
        )
    assert exc.value.code == 70
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "unknown" and result["retryable"] is False
    assert result["bridge"]["session_id"] == "live-1"
    assert result["idempotency_key"] == "original"
    assert len([item for item in calls if item[0] == "POST"]) == 1
    (row,) = tmp_db.execute_read("SELECT * FROM live_messages")
    assert row["session_id"] == "live-1" and row["idempotency_key"] == "original"


@pytest.mark.parametrize(
    "option",
    [
        "--native-invocation-file",
        "--expected-native-session-id",
        "--expected-session-id",
        "--idempotency-key",
    ],
)
def test_empty_special_options_cannot_be_ignored_by_exact_send(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    api: Any,
    tmp_db: Any,
    option: str,
) -> None:
    calls = wire(monkeypatch, api)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--json", "send", "workspace-a", "work", "--bridge", option, ""])
    assert exc.value.code == 2
    assert json.loads(capsys.readouterr().out)["reason"] == "bridge_send_options"
    assert not calls and not tmp_db.execute_read("SELECT * FROM live_messages")


@pytest.mark.parametrize("response", [None, {"native_session_id": "replacement"}, ["invalid"]])
def test_managed_invalid_admission_response_retains_unknown_selected_identity(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    session_manager: Any,
    tmp_db: Any,
    mock_acp_client: Any,
    response: Any,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent_bridge.routes.sessions import router

    session = prepare(session_manager, tmp_db, mock_acp_client)
    app = FastAPI()
    app.state.session_manager = session_manager
    app.include_router(router)
    calls = wire(
        monkeypatch,
        TestClient(app),
        represented=False,
        after_send=lambda result: (
            {**result, **response} if isinstance(response, dict) else response
        ),
    )
    with pytest.raises(SystemExit) as exc:
        cli.main(["--json", "send", "workspace-a", "work", "--bridge", "--no-wait"])
    assert exc.value.code == 70
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "unknown" and result["retryable"] is False
    assert result["bridge"] == {
        "session_id": "native-a",
        "managed_session_id": "bridge-a",
        "registered_session_id": None,
    }
    assert len([item for item in calls if item[0] == "POST"]) == 1
    assert session.turn_count == 1
    session_manager.resume_session.assert_not_awaited()


def test_unkeyed_managed_lost_response_cannot_retry_the_real_http_request(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    session_manager: Any,
    tmp_db: Any,
    mock_acp_client: Any,
) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from agent_bridge.routes.sessions import router
    from agent_bridge.session_status import SessionStatus

    session = prepare(session_manager, tmp_db, mock_acp_client)
    app = FastAPI()
    app.state.session_manager = session_manager
    app.include_router(router)
    lost = False

    def lose_first_response(result: dict[str, Any]) -> dict[str, Any]:
        nonlocal lost
        session.status = SessionStatus.IDLE
        if not lost:
            lost = True
            raise urllib.error.URLError(OSError("fixture admission response lost"))
        return result

    calls = wire(
        monkeypatch,
        TestClient(app),
        represented=False,
        transport=True,
        after_send=lose_first_response,
    )
    with pytest.raises(SystemExit) as exc:
        cli.main(["--json", "send", "workspace-a", "work", "--bridge", "--no-wait"])
    assert exc.value.code == 70
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "unknown" and result["bridge"]["session_id"] == "native-a"
    assert result["retryable"] is False
    assert len([item for item in calls if item[0] == "POST"]) == 1
    assert session.turn_count == 1
    session_manager.resume_session.assert_not_awaited()


def test_retained_inbox_pre_admission_refusal_has_one_typed_json_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    api: Any,
    tmp_db: Any,
) -> None:
    from agent_bridge.runtime_custody import RuntimeCustodyError

    calls = wire(monkeypatch, api)

    def refuse(*_args: Any, **_kwargs: Any) -> NoReturn:
        raise RuntimeCustodyError("fixture consumer unavailable")

    monkeypatch.setattr(tmp_db, "enqueue_live_message_if_fresh", refuse)
    with pytest.raises(SystemExit) as exc:
        cli.main(["--json", "send", "workspace-a", "work", "--bridge"])
    assert exc.value.code == 69
    result = json.loads(capsys.readouterr().out)
    assert result["outcome"] == "refused_unavailable"
    assert result["reason"] == "retained_inbox_refused" and result["retryable"] is False
    assert len([item for item in calls if item[0] == "POST"]) == 1
    assert not tmp_db.execute_read("SELECT * FROM live_messages")
    assert not tmp_db.execute_read("SELECT * FROM pending_prompts")
