"""The real CLI/client/HTTP inbox path admits a prepared owned seed once."""

import asyncio
import io
import sys
import time
import contextlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from agent_bridge import __main__ as core
from agent_bridge.client import BridgeClient, BridgeClientError, BridgeConnectionError
from agent_bridge.routes.live_sessions import router, post_live_message
from agent_bridge.live_message_models import SendMessageRequest
from test_plugin_readiness import register, claimed, reply


def context(root):
    return {"version": 1, "cwd": str(root), "plugins": [
        {"projected_source": "hook-only@alias", "logical_source": "hook-only@original", "version": "2.0.0"},
    ], "logical_payloads": {"hook-only@original": str(root / "hook-only")}}


@pytest.fixture
def http(tmp_db, tmp_path):
    register(tmp_db, tmp_path)
    app = FastAPI()
    app.include_router(router)
    app.state.db = tmp_db
    with TestClient(app) as client:
        yield client


@pytest.mark.asyncio
@pytest.mark.parametrize("lost_response", [False, True])
@pytest.mark.parametrize("ready", [False, True])
async def test_library_stdin_to_real_send_cli_http_and_native_bound_queue_once(
    tmp_db, tmp_path, monkeypatch, http, lost_response, ready,
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "libs" / "venue-copilot" / "src"))
    from venue_copilot.refs import deliver_note

    client = BridgeClient("http://fixture", "fixture", connect_grace=0)
    lost = []
    def request(method, path, payload=None, **kwargs):
        if path == "/health":
            return {"protocol_version": 44}
        response = http.request(method, path, json=payload)
        if response.status_code >= 400:
            raise BridgeClientError(response.status_code, response.json().get("detail"))
        if lost_response and method == "POST" and path.endswith("/messages") and not lost:
            lost.append(response.json()["message_id"])
            raise BridgeConnectionError("fixture lost the HTTP response after durable admission")
        return response.json()
    client._request = request
    client.resolve_live_session = lambda target: tmp_db.get_live_session(target)
    monkeypatch.setattr(core, "_get_client", lambda: client)
    monkeypatch.setattr(core, "_live_sender_label", lambda args: "venue")
    monkeypatch.setattr(core, "_live_reply_to", lambda args: None)

    def run(argv, *, input, **kwargs):
        args = core.build_parser().parse_args(argv[1:])
        monkeypatch.setattr(sys, "stdin", io.StringIO(input))
        stdout = io.StringIO()
        code = 0
        with contextlib.redirect_stdout(stdout):
            try:
                core._cmd_send(args)
            except SystemExit as exc:
                code = exc.code
            except (BridgeClientError, BridgeConnectionError):
                code = 1
        return SimpleNamespace(returncode=code, stdout=stdout.getvalue())

    task = asyncio.create_task(asyncio.to_thread(
        deliver_note, "native", "owned seed\nreference path", run=run,
        operation="same-operation", prepared_context=context(tmp_path),
    ))
    control = await claimed(tmp_db, "native")
    assert not tmp_db.execute_read("SELECT id FROM live_messages WHERE kind NOT LIKE 'control:%'")
    reply(tmp_db, "native", control, status="ready" if ready else "failed")
    admitted = await task
    if not ready:
        assert admitted.status == "refused"
        assert not tmp_db.execute_read("SELECT id FROM live_messages WHERE kind NOT LIKE 'control:%'")
        return
    assert admitted.status == "admitted"
    if lost_response:
        assert lost == [admitted.message_id]
    assert deliver_note(
        "native", "owned seed\nreference path", run=run,
        operation="same-operation", prepared_context=context(tmp_path),
    )
    rows = tmp_db.execute_read("SELECT kind,delivery,body FROM live_messages WHERE kind NOT LIKE 'control:%'")
    assert len(rows) == 1 and rows[0]["kind"].startswith("startup:prompt:")
    assert rows[0]["delivery"] == "queue" and rows[0]["body"] == "owned seed\nreference path"
    tmp_db.execute_write("INSERT INTO live_session_aliases VALUES (?,?,?)",
                         ("retired-placeholder", "native", time.time()))
    key = tmp_db.execute_read("SELECT idempotency_key FROM live_messages WHERE kind LIKE 'startup:prompt:%'")[0]["idempotency_key"]
    aliased = http.post("/api/v1/live-sessions/retired-placeholder/messages", json={
        "sender": "venue", "body": "owned seed\nreference path", "delivery": "queue",
        "idempotency_key": key, "plugin_readiness": {
            "owner": "bridge", "cwd": str(tmp_path), "plugins": context(tmp_path)["plugins"],
        },
    })
    assert aliased.status_code == 200 and aliased.json()["duplicate"] is True
    changed = context(tmp_path)
    changed["plugins"] = [{"projected_source": "different@alias"}]
    conflict = deliver_note("native", "owned seed\nreference path", run=run,
                            operation="same-operation", prepared_context=changed)
    assert conflict.status == "unknown"
    assert len(tmp_db.execute_read("SELECT id FROM live_messages WHERE kind NOT LIKE 'control:%'")) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["failed", "unsupported", "malformed", "cancel"])
async def test_failed_or_cancelled_readiness_never_admits_owned_prompt(tmp_db, tmp_path, outcome):
    register(tmp_db, tmp_path)
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(db=tmp_db)))
    body = SendMessageRequest(
        sender="venue", body="owned", delivery="queue", idempotency_key="operation",
        plugin_readiness={"owner": "bridge", "cwd": str(tmp_path), "plugins": context(tmp_path)["plugins"]},
    )
    task = asyncio.create_task(post_live_message("native", body, request))
    control = await claimed(tmp_db, "native")
    if outcome == "cancel":
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    else:
        if outcome == "malformed":
            tmp_db.ack_live_messages("native", [control["id"]], time.time(),
                                    controls=True, outcome='{"invalid":true}')
        else:
            reply(tmp_db, "native", control, status=outcome)
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as failure:
            await task
        assert failure.value.status_code == 409
    assert not tmp_db.execute_read("SELECT id FROM live_messages WHERE kind NOT LIKE 'control:%'")
    changed = body.model_copy(update={"body": "changed task"})
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as conflict:
        await post_live_message("native", changed, request)
    assert "input-changed" in str(conflict.value.detail)
    retry = asyncio.create_task(post_live_message("native", body, request))
    control = await claimed(tmp_db, "native")
    reply(tmp_db, "native", control)
    assert (await retry).message_id
    assert len(tmp_db.execute_read("SELECT id FROM live_messages WHERE kind NOT LIKE 'control:%'")) == 1


@pytest.mark.parametrize("generation", [40, 41, 42, 43])
def test_typed_client_old_peer_is_rejected_without_posting_and_legacy_schema_stays_plain(generation):
    client = BridgeClient("http://fixture", "fixture", connect_grace=0)
    calls = []
    client._request = lambda method, path, *args, **kwargs: calls.append((method, path)) or {"protocol_version": generation}
    with pytest.raises(BridgeClientError) as failure:
        client.send_live_message(
            "native", sender="venue", body="owned", delivery="queue", idempotency_key="operation",
            plugin_readiness={"owner": "bridge", "cwd": "/callee", "plugins": []},
        )
    assert failure.value.status == 426
    assert calls == [("GET", "/health")]
    assert "plugin_readiness" not in SendMessageRequest(sender="operator", body="ordinary").model_dump()


@pytest.mark.parametrize("generation", [39, 40, 41, 42, 43])
def test_ordinary_receipt_delivery_retains_older_peer_capability_floor(generation):
    client = BridgeClient("http://fixture", "fixture", connect_grace=0)
    calls = []

    def request(method, path, payload=None, **kwargs):
        calls.append((method, path, payload))
        if path == "/health":
            return {"protocol_version": generation, "min_protocol_version": 1}
        return {"message_id": 7}

    client._request = request
    assert client.send_live_message(
        "native", sender="operator", body="ordinary", wait=True,
    ) == {"message_id": 7}
    assert calls[-1][:2] == ("POST", "/api/v1/live-sessions/native/messages")
    assert "plugin_readiness" not in calls[-1][2]


def test_current_http_accepts_older_ordinary_packet_without_readiness_gate(http, tmp_db):
    response = http.post("/api/v1/live-sessions/native/messages", json={
        "sender": "operator", "body": "ordinary", "delivery": "queue",
    })
    assert response.status_code == 200
    messages = tmp_db.execute_read("SELECT kind,body FROM live_messages")
    assert len(messages) == 1
    assert messages[0]["kind"] == "prompt" and messages[0]["body"] == "ordinary"
