"""Ordinary receipt-only CLI/ASGI observations, not installed-runtime proof."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from agent_bridge import __main__ as core
from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
from agent_bridge.models import SessionStatus


def observe(api, manager, session, body, vector, monkeypatch, capsys, *, client_factory) -> dict:
    assert client_factory.__module__ == core.__name__
    assert client_factory.__name__ == "_get_client"
    route = vector["requests"]["receipt_route"]
    advertised = api.get("/health").json()
    observed = {"capability": advertised["capabilities"]["native_send_receipt"]}
    original_status = session.status
    before = (
        manager.db.execute_read("SELECT * FROM turns"),
        manager.db.execute_read("SELECT * FROM events"),
        manager.db.execute_read("SELECT * FROM sessions"),
        manager.db.execute_read("SELECT * FROM pending_prompts"),
        manager.db.execute_read("SELECT * FROM live_messages"),
        session.turn_count,
    )
    saved_resume = manager.resume_session.await_count
    saved_handoff = manager.handoff_session.await_count
    calls = []
    old_capability = [False]
    missing_provider = [False]

    def transport(method, path, payload=None, **kwargs):
        if method == "POST":
            calls.append(path)
        if missing_provider[0]:
            raise BridgeConnectionError("synthetic receipt provider unavailable")
        response = api.request(method, path, json=payload)
        if response.is_error:
            raise BridgeClientError(response.status_code, response.json().get("detail"))
        value = response.json()
        if path == "/health" and old_capability[0]:
            value["capabilities"].pop("native_send_receipt", None)
        return value

    def configured_client(_cls):
        client = BridgeClient("http://testserver", "fixture-token", connect_grace=0)
        client._request = transport
        return client

    def cli(*, missing=False, keyed=False):
        selector = "keyed_receipt_command" if keyed else "receipt_command"
        command = list(vector["requests"][selector])
        if missing:
            command[command.index("--idempotency-key") + 1] = "missing-operation"
        with monkeypatch.context() as patch:
            factory = Mock(wraps=client_factory)
            ensure = Mock(side_effect=AssertionError("receipt observation ensured a daemon"))
            start = Mock(side_effect=AssertionError("receipt observation started a daemon"))
            patch.setattr(core, "_get_client", factory)
            patch.setattr(core, "_ensure_daemon", ensure)
            patch.setattr(core, "_launch_bridge_daemon", start)
            patch.setattr(BridgeClient, "from_config", classmethod(configured_client))
            stdin = (
                SimpleNamespace(buffer=io.BytesIO(
                    json.dumps(vector["requests"]["keyed_receipt_request"]).encode(),
                )) if keyed else io.StringIO(body["prompt"])
            )
            patch.setattr("sys.stdin", stdin)
            capsys.readouterr()
            try:
                core.main(command)
                status = 0
            except SystemExit as exc:
                status = exc.code
            captured = capsys.readouterr()
            output = json.loads(captured.out) if captured.out.strip() else None
            assert factory.call_count == 1
            assert factory.call_args.args == () and factory.call_args.kwargs == {"ensure": False}
            ensure.assert_not_called()
            start.assert_not_called()
            client_observation = {
                "ensure": factory.call_args.kwargs["ensure"],
                "ensure_calls": ensure.call_count, "daemon_start_calls": start.call_count,
            }
        return status, output, client_observation, captured.err

    with monkeypatch.context() as patch:
        forbidden = AsyncMock(side_effect=AssertionError("receipt observation submitted a turn"))
        patch.setattr(manager, "_submit_prompt_locked", forbidden)
        session.status = SessionStatus.STOPPED
        found = api.post(route, json=body)
        observed["found"] = {"status": found.status_code, "body": found.json()}
        status, output, client_observation, _ = cli()
        observed["cli_found"] = {
            "exit": status, "body": output, "posts": list(calls), **client_observation,
        }
        calls.clear()
        missing = api.post(route, json={**body, "invocation_key": "missing-operation"})
        observed["missing"] = {"status": missing.status_code, "body": missing.json()}
        status, output, client_observation, _ = cli(missing=True)
        observed["cli_missing"] = {
            "exit": status,
            **{key: output[key] for key in ("outcome", "reason", "retryable")},
            "posts": list(calls), **client_observation,
        }
        calls.clear()
        old_capability[0] = True
        status, output, client_observation, _ = cli()
        observed["old_capability"] = {
            "exit": status,
            **{key: output[key] for key in ("outcome", "reason", "retryable")},
            "posts": list(calls), **client_observation,
        }
        old_capability[0] = False
        calls.clear()
        missing_provider[0] = True
        status, output, client_observation, _ = cli()
        observed["missing_provider"] = {
            "exit": status,
            **{key: output[key] for key in ("outcome", "reason", "retryable")},
            "posts": list(calls), **client_observation,
        }
        calls.clear()
        status, output, client_observation, error = cli(keyed=True)
        observed["keyed_missing_provider"] = {
            "exit": status, "json_output": output is not None,
            "unavailable_error": "agent-bridge is unavailable" in error,
            "posts": list(calls), **client_observation,
        }
        missing_provider[0] = False
        observed["stopped_preserved"] = session.status == SessionStatus.STOPPED
        session.status = SessionStatus.RUNNING
        busy = api.post(route, json=body)
        observed["busy"] = {
            "status": busy.status_code, "same_receipt": busy.json() == found.json(),
            "preserved": session.status == SessionStatus.RUNNING,
        }
        observed["wrong_identity"] = {
            key: api.post(route, json={**body, key: value}).status_code
            for key, value in (
                ("prompt", "different"), ("caller_id", "other-caller"),
                ("expected_native_session_id", "other-native"),
            )
        }
        journal, = manager.db.execute_read(
            "SELECT native_admission_json FROM turns WHERE acquisition_delivery_id=?",
            ("native-send:v1:" + body["invocation_key"],),
        )
        original = journal["native_admission_json"]
        manager.db.execute_write(
            "UPDATE turns SET native_admission_json=? WHERE acquisition_delivery_id=?",
            ("{", "native-send:v1:" + body["invocation_key"]),
        )
        try:
            corrupt = api.post(route, json=body)
            observed["corrupt"] = {"status": corrupt.status_code, "body": corrupt.json()}
        finally:
            manager.db.execute_write(
                "UPDATE turns SET native_admission_json=? WHERE acquisition_delivery_id=?",
                (original, "native-send:v1:" + body["invocation_key"]),
            )
        forbidden.assert_not_awaited()
    session.status = original_status
    observed["no_effects"] = (
        before == (
            manager.db.execute_read("SELECT * FROM turns"),
            manager.db.execute_read("SELECT * FROM events"),
            manager.db.execute_read("SELECT * FROM sessions"),
            manager.db.execute_read("SELECT * FROM pending_prompts"),
            manager.db.execute_read("SELECT * FROM live_messages"),
            session.turn_count,
        )
        and manager.resume_session.await_count == saved_resume
        and manager.handoff_session.await_count == saved_handoff
    )
    with monkeypatch.context() as patch:
        patch.delitem(api.headers, "Authorization")
        observed["unauthenticated"] = api.post(route, json=body).status_code
    return observed
