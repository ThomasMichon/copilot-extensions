"""Actual TLS HTTP/2/SSE and WS with a separate synthetic server process."""

from __future__ import annotations

import asyncio
import datetime
import logging
import json
import os
import socket
import ssl
import subprocess
import sys
import time
import traceback
from pathlib import Path

import httpx
import pytest
from agent_procutil import no_window_kwargs
from credential_relay.http_auth import SourceHttpAuth
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from agent_bridge.acp_client import AcpClient
from agent_bridge.remote_acp_config import RemoteAcpTarget
from agent_bridge.remote_acp_http import HttpAcpTransport
from agent_bridge.remote_acp_ws import WsAcpTransport
from agent_bridge.remote_acp_transport import RemoteTransportError
from agent_bridge.remote_acp_sessions import connect_remote_session
from agent_bridge.session_manager import session_manager_from_config
from agent_bridge.models import ServiceConfig
from agent_bridge.events import EventLog
from agent_bridge import telemetry
from agent_bridge.launch_runtime import executing_executable


def authenticated_wire(spec, context, cookies=None):
    wire = (
        HttpAcpTransport(spec, client_factory=lambda: httpx.AsyncClient(
            http2=True, verify=context, trust_env=False, follow_redirects=False,
            cookies=cookies,
        )) if spec.transport == "streamable-http"
        else WsAcpTransport(spec, ssl=context, cookies=cookies)
    )

    class Source:
        def supports(self, action, fields):
            return action == "get"

        async def resolve(self, action, fields, *, timeout):
            return "\n".join(("username=synthetic", "password=synthetic-credential", ""))

    wire.auth = SourceHttpAuth(Source(), wire.context, spec.endpoint)
    return wire


@pytest.fixture
def peer(tmp_path):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder().subject_name(subject).issuer_name(subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))
    with socket.socket() as sock:
        sock.bind(("localhost", 0))
        port = sock.getsockname()[1]
    context = ssl.create_default_context(cafile=str(cert_path))
    error_path = tmp_path / "peer-stderr.log"
    error_log = error_path.open("wb")
    proc = subprocess.Popen(
        [str(executing_executable()), str(Path(__file__).with_name("remote_acp_peer.py")),
         str(port), str(cert_path), str(key_path)],
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        stdout=subprocess.DEVNULL, stderr=error_log, **no_window_kwargs(),
    )
    url = f"https://localhost:{port}"
    try:
        with httpx.Client(verify=context, trust_env=False, timeout=1) as probe:
            for _ in range(100):
                if proc.poll() is not None:
                    pytest.fail("Synthetic server failed: " + error_path.read_text()[-8192:])
                try:
                    if probe.get(url + "/evidence").status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.05)
            else:
                pytest.fail("Synthetic peer did not become ready")
        yield url, context, proc
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
        error_log.close()
        diagnostics = error_path.read_text(encoding="utf-8", errors="replace")
        if diagnostics:
            print("Synthetic peer stderr:\n" + diagnostics[-8192:], file=sys.stderr)


async def wait_event(events, kind, wire=None):
    for _ in range(100):
        match = [data for event, data in events if event == kind]
        if match:
            return match[-1]
        await asyncio.sleep(0.02)
    failure = getattr(wire, "_failure", None)
    pytest.fail(f"No expected {kind} event; transport failure: {failure}")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["streamable-http", "websocket"])
async def test_actual_remote_lifecycle_permission_form_cancel_and_survival(peer, mode, caplog):
    caplog.set_level(logging.DEBUG)
    url, context, proc = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/acp", transport=mode, allow_loopback_test=True,
        auth={"source": "git-credential", "identity": "synthetic"},
    )
    events = []

    def transport(cookies=None):
        if mode == "streamable-http":
            wire = HttpAcpTransport(spec, client_factory=lambda: httpx.AsyncClient(
                http2=True, verify=context, trust_env=False,
                timeout=5, follow_redirects=False, cookies=cookies,
            ))
        else:
            wire = WsAcpTransport(spec, ssl=context, cookies=cookies)

        class Source:
            calls = 0

            def supports(self, action, fields):
                return action == "get"

            async def resolve(self, action, fields, *, timeout):
                self.calls += 1
                return "password=" + (
                    "expired" if self.calls == 1 else "synthetic-credential"
                ) + "\n\n"

        wire.auth = SourceHttpAuth(Source(), wire.context, spec.endpoint)
        return wire

    client = AcpClient(on_event=lambda kind, data: events.append((kind, data)))
    wire = transport()
    wire.on_lost = client._remote_lost
    try:
        await client.start_remote(spec, transport=wire)
        sid = await asyncio.wait_for(client.new_session(cwd="/workspace"), 5)
        assert client.pid is None
        prompt = asyncio.create_task(client.send_prompt("permission"))
        event = await wait_event(events, "permission_request", wire)
        assert event["decision_id"] == "decision/opaque"
        selected = "allow/opaque" if mode == "streamable-http" else "reject/opaque"
        assert client.resolve_permission(event["request_id"], selected)
        result = await asyncio.wait_for(prompt, 5)
        assert result["response_text"] == "synthetic response"
        assert not client.resolve_permission(event["request_id"], "allow/opaque")

        prompt = asyncio.create_task(client.send_prompt("form"))
        event = await wait_event(events, "ask_user_request", wire)
        assert client.resolve_elicitation(
            "tool/opaque", {"choice": "explicit"}, request_id=event["request_id"],
        )
        await asyncio.wait_for(prompt, 5)
        events.clear()
        prompt = asyncio.create_task(client.send_prompt("form-request"))
        event = await wait_event(events, "ask_user_request", wire)
        assert type(event["native_request_id"]) is int
        assert event["tool_call_id"] == ""
        assert client.resolve_elicitation(
            "", {"choice": "explicit"}, request_id=event["request_id"],
        )
        await asyncio.wait_for(prompt, 5)
        events.clear()
        prompt = asyncio.create_task(client.send_prompt("permission"))
        event = await wait_event(events, "permission_request", wire)
        await asyncio.wait_for(client.cancel_prompt(), 5)
        assert (await asyncio.wait_for(prompt, 5))["stop_reason"] == "cancelled"
        assert not client.resolve_permission(event["request_id"], "allow/opaque")
        prompt = asyncio.create_task(client.send_prompt("wait"))
        await asyncio.sleep(0.1)
        await asyncio.wait_for(client.cancel_prompt(), 5)
        assert (await asyncio.wait_for(prompt, 5))["stop_reason"] == "cancelled"
        with pytest.raises((ConnectionError, RemoteTransportError)):
            await asyncio.wait_for(client.send_prompt("drop"), 5)
    finally:
        await client.shutdown()
    assert proc.poll() is None
    assert "synthetic-credential" not in caplog.text
    assert "affinity=synthetic" not in caplog.text

    resumed = AcpClient()
    wire = transport(wire.cookies)
    wire.on_lost = resumed._remote_lost
    try:
        await resumed.start_remote(spec, transport=wire)
        await asyncio.wait_for(resumed.load_session("/workspace", sid), 5)
        assert resumed.acp_session_id == sid
        result = await asyncio.wait_for(resumed.send_prompt("fresh"), 5)
        assert result["response_text"] == "synthetic response"
    finally:
        await resumed.shutdown()
    async with httpx.AsyncClient(verify=context, trust_env=False) as probe:
        evidence = (await probe.get(url + "/evidence")).json()
    assert evidence["prompts"] == 7  # No prompt replay on load/reconnect.
    assert len(evidence["answers"]) == 3
    assert evidence["accepted_actions"] == (3 if mode == "streamable-http" else 2)
    assert evidence["auth_refused"] == 2
    assert evidence["auth_ok"] >= 2
    if mode == "streamable-http":
        assert set(evidence["http_versions"]) == {"2"}
        assert evidence["deletes"] == 2
    else:
        assert evidence["ws_cookies"] == 1
    assert proc.poll() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["streamable-http", "websocket"])
async def test_actual_missing_approval_capability_refuses_before_prompt(peer, mode):
    url, context, proc = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/unsupported", transport=mode, allow_loopback_test=True,
        auth={"source": "none", "identity": "synthetic"},
    )
    wire = (
        HttpAcpTransport(spec, client_factory=lambda: httpx.AsyncClient(
            http2=True, verify=context, trust_env=False, follow_redirects=False,
        )) if mode == "streamable-http" else WsAcpTransport(spec, ssl=context)
    )
    client = AcpClient()
    with pytest.raises(PermissionError, match="capability"):
        await client.start_remote(spec, transport=wire)
    assert client._connection is None
    async with httpx.AsyncClient(verify=context, trust_env=False) as probe:
        data = (await probe.get(url + "/evidence")).json()
    assert data["prompts"] == 0
    assert data["accepted_actions"] == 0
    assert data["created_sessions"] == 0
    assert proc.poll() is None


@pytest.mark.asyncio
async def test_actual_untrusted_tls_and_http1_are_not_fallbacks(peer):
    url, context, proc = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/acp", transport="streamable-http", allow_loopback_test=True,
        auth={"source": "none", "identity": "synthetic"},
    )
    for client_factory in [
        lambda: httpx.AsyncClient(http2=True, trust_env=False),
        lambda: httpx.AsyncClient(http2=False, trust_env=False, verify=context),
    ]:
        wire = HttpAcpTransport(spec, client_factory=client_factory)
        try:
            with pytest.raises(RemoteTransportError):
                await wire.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        finally:
            await wire.close()
    assert proc.poll() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["streamable-http", "websocket"])
async def test_actual_sessionless_form_before_new_has_no_invented_session(peer, mode):
    url, context, proc = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/creation-question", transport=mode, allow_loopback_test=True,
        auth={"source": "none", "identity": "synthetic"},
    )
    wire = (
        HttpAcpTransport(spec, client_factory=lambda: httpx.AsyncClient(
            http2=True, verify=context, trust_env=False, follow_redirects=False,
        )) if mode == "streamable-http" else WsAcpTransport(spec, ssl=context)
    )
    events = []
    client = AcpClient(on_event=lambda kind, data: events.append((kind, data)))
    wire.on_lost = client._remote_lost
    pending = None
    try:
        await client.start_remote(spec, transport=wire)
        pending = asyncio.create_task(client.new_session(cwd="/workspace"))
        event = await wait_event(events, "ask_user_request")
        assert event["remote_session_id"] is None
        assert event["tool_call_id"] == ""
        assert type(event["native_request_id"]) is int
        assert not pending.done()
        async with httpx.AsyncClient(verify=context, trust_env=False) as probe:
            data = (await probe.get(url + "/evidence")).json()
        assert data["created_sessions"] == data["accepted_actions"] == 0
        assert client.resolve_elicitation(
            "", {"choice": "explicit"}, request_id=event["request_id"],
        )
        assert await asyncio.wait_for(pending, 5)
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await client.shutdown()
    assert proc.poll() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["streamable-http", "websocket"])
async def test_actual_service_resume_retains_history_as_replay_without_spawn(peer, tmp_db, monkeypatch, mode):
    url, context, proc = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/acp", transport=mode, allow_loopback_test=True,
        auth={"source": "injected", "identity": "synthetic"},
        max_queue_messages=2,
    )
    original = AcpClient.start_remote

    class Source:
        def supports(self, action, fields):
            return action == "get"

        async def resolve(self, action, fields, *, timeout):
            return "password=synthetic-credential\n\n"

    async def start(client, target, *, cookies=None):
        wire = HttpAcpTransport(target, client_factory=lambda: httpx.AsyncClient(
            http2=True, verify=context, trust_env=False, follow_redirects=False,
            cookies=cookies,
        )) if mode == "streamable-http" else WsAcpTransport(target, ssl=context, cookies=cookies)
        wire.auth = SourceHttpAuth(Source(), wire.context, target.endpoint)
        await original(client, target, transport=wire)

    monkeypatch.setattr(AcpClient, "start_remote", start)
    records = []
    monkeypatch.setattr(telemetry, "emit", records.append)
    manager = session_manager_from_config(tmp_db, ServiceConfig(remote_acp={"peer": spec}))
    session = await connect_remote_session(manager, "peer", cwd="/workspace")
    try:
        await manager.submit_prompt(session.session_id, "history")
        await asyncio.wait_for(session._prompt_task, 5)
        await manager.submit_prompt(session.session_id, "drop")
        await asyncio.wait_for(session._prompt_task, 5)
        sid = session.acp_session_id
        await manager.stop_session(session.session_id)
        assert session.client is None
        assert session._remote_affinity_cookies is not None
        manager.db.enqueue_prompt(session.session_id, "queued-unsent", time.time())
        await manager.resume_session(session.session_id)
        await asyncio.wait_for(session._prompt_task, 5)
        assert session.acp_session_id == sid and session.pid is None
        persisted = str(manager.db.get_session(session.session_id))
        assert "synthetic-credential" not in persisted and "affinity=" not in persisted
        assert manager.db.count_pending_prompts(session.session_id) == 0
        async with httpx.AsyncClient(verify=context, trust_env=False) as probe:
            data = (await probe.get(url + "/evidence")).json()
        assert data["prompts"] == 3
        replay = [
            event for event in session.event_log.get_events()
            if event.data.get("history_replay")
        ]
        assert len([event for event in replay if event.event == "agent_message"]) == 6
        assert any(event.data.get("text") == "unseen during disconnect" for event in replay)
        assert session.client.pending_ask_user() == []
        assert not session.client.has_active_background_tasks
        assert any(event.event == "tool_call_start" for event in replay)
        assert session.event_log.active_tool_call() is None
        assert "historical/tool" not in json.dumps(records)
        restored = EventLog.from_db(manager.db, session.session_id)
        assert restored.active_tool_call() is None
        restored.rebuild([(event.event, event.data) for event in restored.get_events()])
        assert restored.active_tool_call() is None
    finally:
        await manager.stop_session(session.session_id)
    assert proc.poll() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["streamable-http", "websocket"])
async def test_actual_remote_redirect_refused_without_forwarding(peer, mode):
    url, context, proc = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/redirect", transport=mode, allow_loopback_test=True,
        auth={"source": "none", "identity": "synthetic"},
    )
    wire = (
        HttpAcpTransport(spec, client_factory=lambda: httpx.AsyncClient(
            http2=True, verify=context, trust_env=False, follow_redirects=False,
        )) if mode == "streamable-http" else WsAcpTransport(spec, ssl=context)
    )
    try:
        with pytest.raises(RemoteTransportError):
            if mode == "streamable-http":
                await wire.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
            else:
                await wire.open()
    finally:
        await wire.close()
    async with httpx.AsyncClient(verify=context, trust_env=False) as probe:
        data = (await probe.get(url + "/evidence")).json()
    assert data["redirect_followed"] == 0
    if mode == "streamable-http":
        assert data["redirect_body_bytes"] > 0
    assert data["prompts"] == 0
    assert proc.poll() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["streamable-http", "websocket"])
@pytest.mark.parametrize("scheme", ["https", "HTTPS", "HtTpS"])
async def test_answer_admitted_before_cancel_still_has_terminal_route(peer, mode, scheme):
    url, context, proc = peer
    spec = RemoteAcpTarget(
        endpoint=url.replace("https:", scheme + ":") + "/acp",
        transport=mode, allow_loopback_test=True,
        auth={"source": "injected", "identity": "synthetic"},
    )
    wire = (
        HttpAcpTransport(spec, client_factory=lambda: httpx.AsyncClient(
            http2=True, verify=context, trust_env=False, follow_redirects=False,
        )) if mode == "streamable-http" else WsAcpTransport(spec, ssl=context)
    )

    class Source:
        def supports(self, action, fields):
            return action == "get"

        async def resolve(self, action, fields, *, timeout):
            return "\n".join(("username=synthetic", "password=synthetic-credential", ""))

    wire.auth = SourceHttpAuth(Source(), wire.context, spec.endpoint)
    events = []
    client = AcpClient(on_event=lambda kind, data: events.append((kind, data)))
    admitted, release = asyncio.Event(), asyncio.Event()
    send = wire.send

    async def gated_send(message):
        if "method" not in message and message.get("id") == "rpc/opaque":
            admitted.set()
            await release.wait()
        await send(message)

    wire.send = gated_send
    pending = None
    try:
        await client.start_remote(spec, transport=wire)
        await client.new_session(cwd="/workspace")
        pending = asyncio.create_task(client.send_prompt("permission"))
        question = await wait_event(events, "permission_request")
        assert client.resolve_permission(question["request_id"], "allow/opaque")
        await asyncio.wait_for(admitted.wait(), 5)
        await asyncio.wait_for(client.cancel_prompt(), 5)
        await asyncio.wait_for(pending, 5)
        release.set()
        assert await asyncio.wait_for(client.send_prompt("fresh"), 5)
        assert wire._failure is None and client.is_running
    finally:
        release.set()
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await client.shutdown()
    async with httpx.AsyncClient(verify=context, trust_env=False) as probe:
        data = (await probe.get(url + "/evidence")).json()
    assert data["prompts"] == 2 and data["accepted_actions"] == 0
    assert proc.poll() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["streamable-http", "websocket"])
@pytest.mark.parametrize("failure", ["wait", "drop-session-only"])
async def test_failure_closes_wire_and_sibling_resources_without_stop(peer, mode, failure):
    url, context, proc = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/acp", transport=mode, allow_loopback_test=True,
        auth={"source": "injected", "identity": "synthetic"},
        rpc_timeout_seconds=2, timeout_seconds=5, idle_seconds=5,
    )
    wire = (
        HttpAcpTransport(spec, client_factory=lambda: httpx.AsyncClient(
            http2=True, verify=context, trust_env=False, follow_redirects=False,
        )) if mode == "streamable-http" else WsAcpTransport(spec, ssl=context)
    )

    class Source:
        def supports(self, action, fields):
            return action == "get"

        async def resolve(self, action, fields, *, timeout):
            return "\n".join(("username=synthetic", "password=synthetic-credential", ""))

    wire.auth = SourceHttpAuth(Source(), wire.context, spec.endpoint)
    client = AcpClient()
    try:
        await client.start_remote(spec, transport=wire)
        await client.new_session(cwd="/workspace")
        with pytest.raises((ConnectionError, RemoteTransportError)):
            await asyncio.wait_for(client.send_prompt(failure), 5)
        for _ in range(200):
            if wire._close_task is not None and wire._close_task.done():
                break
            await asyncio.sleep(0.02)
        assert wire._close_task is not None and wire._close_task.done()
        assert wire._close_task.exception() is None
        assert all(task.done() for task in wire._tasks)
        if mode == "streamable-http":
            assert wire._client.is_closed
        else:
            assert wire._ws.close_code is not None
        assert proc.poll() is None
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_quiet_ws_keeps_ping_pong_liveness_beyond_application_idle(peer):
    url, context, proc = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/acp", transport="websocket", allow_loopback_test=True,
        auth={"source": "injected", "identity": "synthetic"}, idle_seconds=1,
    )
    wire = authenticated_wire(spec, context)
    client = AcpClient()
    try:
        await client.start_remote(spec, transport=wire)
        await client.new_session(cwd="/workspace")
        await asyncio.wait_for(await wire._ws.ping(), 2)
        await asyncio.sleep(1.5)
        await asyncio.wait_for(await wire._ws.ping(), 2)
        assert client.is_running
        assert await asyncio.wait_for(client.send_prompt("fresh"), 5)
    finally:
        await client.shutdown()
    assert proc.poll() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["streamable-http", "websocket"])
@pytest.mark.parametrize("malformed", ["malformed-result", "malformed-notification"])
async def test_malformed_sdk_results_never_reach_logs_events_or_failure_records(
    peer, tmp_db, monkeypatch, caplog, mode, malformed,
):
    url, context, _ = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/acp", transport=mode, allow_loopback_test=True,
        auth={"source": "injected", "identity": "synthetic"},
    )
    original = AcpClient.start_remote

    async def start(client, target, *, cookies=None):
        await original(client, target, transport=authenticated_wire(target, context, cookies))

    monkeypatch.setattr(AcpClient, "start_remote", start)
    manager = session_manager_from_config(tmp_db, ServiceConfig(remote_acp={"peer": spec}))
    session = await connect_remote_session(manager, "peer")
    try:
        await manager.submit_prompt(session.session_id, malformed)
        await asyncio.wait_for(session._prompt_task, 5)
        rows = manager.db.execute_read(
            "SELECT stop_reason FROM turns WHERE session_id=?", (session.session_id,),
        )
        evidence = str([dict(row) for row in rows]) + str(session.event_log.get_events()) + caplog.text
        assert "SYNTHETIC-SENSITIVE-MARKER" not in evidence
        if malformed == "malformed-result":
            assert "Remote ACP SDK request failed" in evidence
        else:
            assert "Remote ACP SDK diagnostic (details redacted)" in caplog.text
    finally:
        await manager.stop_session(session.session_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["streamable-http", "websocket"])
async def test_immediate_session_stream_update_waits_for_owner_adoption(peer, mode):
    url, context, _ = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/immediate", transport=mode, allow_loopback_test=True,
        auth={"source": "injected", "identity": "synthetic"},
    )
    events = []
    client = AcpClient(on_event=lambda kind, data: events.append((kind, data)))
    wire = authenticated_wire(spec, context)
    try:
        await client.start_remote(spec, transport=wire)
        sid = await client.new_session(cwd="/workspace")
        event = await wait_event(events, "agent_message")
        assert event["text"] == "immediately after new"
        assert client.acp_session_id == sid and wire._failure is None
        assert not any(kind == "error" for kind, data in events)
    finally:
        await client.shutdown()


@pytest.mark.asyncio
async def test_manager_stop_during_session_sse_open_keeps_attachment_stopped(
    peer, tmp_db, monkeypatch,
):
    url, context, proc = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/acp", transport="streamable-http", allow_loopback_test=True,
        auth={"source": "injected", "identity": "synthetic"},
    )
    original = AcpClient.start_remote
    entered = asyncio.Event()

    async def start(client, target, *, cookies=None):
        wire = authenticated_wire(target, context, cookies)
        request = wire._request

        async def gated(method, headers, content=None, **kwargs):
            if method == "GET" and headers.get("Acp-Session-Id") and not entered.is_set():
                entered.set()
                await asyncio.Event().wait()
            return await request(method, headers, content, **kwargs)

        wire._request = gated
        await original(client, target, transport=wire)

    monkeypatch.setattr(AcpClient, "start_remote", start)
    manager = session_manager_from_config(tmp_db, ServiceConfig(remote_acp={"peer": spec}))
    pending = asyncio.create_task(connect_remote_session(manager, "peer"))
    await asyncio.wait_for(entered.wait(), 5)
    session = next(iter(manager._sessions.values()))
    await asyncio.wait_for(manager.stop_session(session.session_id), 5)
    with pytest.raises(RemoteTransportError, match="closed during stream setup"):
        await asyncio.wait_for(pending, 5)
    assert session.client is None and session.status.value == "stopped"
    assert manager.db.get_session(session.session_id)["status"] == "stopped"
    accepted_sid = manager.db.get_session(session.session_id)["acp_session_id"]
    assert accepted_sid
    await asyncio.wait_for(manager.resume_session(session.session_id), 5)
    assert session.acp_session_id == accepted_sid and session.status.value == "idle"
    await manager.stop_session(session.session_id)
    assert proc.poll() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["streamable-http", "websocket"])
async def test_auth_refresh_exception_never_exposes_provider_details(peer, mode):
    url, context, proc = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/acp", transport=mode, allow_loopback_test=True,
        auth={"source": "injected", "identity": "synthetic"},
    )
    wire = authenticated_wire(spec, context)

    class Provider:
        async def headers(self, context):
            return {}

        async def invalidate(self, context):
            raise RuntimeError("SYNTHETIC-SENSITIVE-MARKER")

    wire.auth = Provider()
    try:
        with pytest.raises(RemoteTransportError) as failure:
            if mode == "streamable-http":
                await wire._request("POST", {"Content-Type": "application/json"}, b"{}")
            else:
                await wire.open()
        assert "SYNTHETIC-SENSITIVE-MARKER" not in str(failure.value)
        assert "SYNTHETIC-SENSITIVE-MARKER" not in "".join(traceback.format_exception(failure.value))
    finally:
        await wire.close()
    assert proc.poll() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["streamable-http", "websocket"])
async def test_auth_retry_retains_affinity_cookie_from_401(peer, mode):
    url, context, proc = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/auth-cookie", transport=mode, allow_loopback_test=True,
        auth={"source": "injected", "identity": "synthetic"},
        timeout_seconds=5, rpc_timeout_seconds=5,
    )
    wire = authenticated_wire(spec, context)

    class Source:
        calls = 0

        def supports(self, action, fields):
            return action == "get"

        async def resolve(self, action, fields, *, timeout):
            self.calls += 1
            value = "expired" if self.calls == 1 else "synthetic-credential"
            return "\n".join(("username=synthetic", "password=" + value, ""))

    source = Source()
    wire.auth = SourceHttpAuth(source, wire.context, spec.endpoint)
    client = AcpClient()
    try:
        await asyncio.wait_for(client.start_remote(spec, transport=wire), 6)
        await asyncio.wait_for(client.new_session(cwd="/workspace"), 6)
        assert source.calls >= 2
    finally:
        await client.shutdown()
    async with httpx.AsyncClient(verify=context, trust_env=False) as probe:
        evidence = (await probe.get(url + "/evidence")).json()
    assert evidence["auth_refused"] == 1 and evidence["created_sessions"] == 1
    assert proc.poll() is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["streamable-http", "websocket"])
async def test_completed_rpc_withdraws_unanswered_form_and_keeps_connection_usable(peer, mode):
    url, context, proc = peer
    spec = RemoteAcpTarget(
        endpoint=url + "/acp", transport=mode, allow_loopback_test=True,
        auth={"source": "injected", "identity": "synthetic"},
    )
    events = []
    client = AcpClient(on_event=lambda kind, data: events.append((kind, data)))
    wire = authenticated_wire(spec, context)
    try:
        await client.start_remote(spec, transport=wire)
        await client.new_session(cwd="/workspace")
        prompt = asyncio.create_task(client.send_prompt("form-request-finish"))
        request = await wait_event(events, "ask_user_request")
        assert client.has_pending_elicitation_request(request["request_id"])
        async with httpx.AsyncClient(verify=context, trust_env=False) as control:
            response = await control.post(
                url + "/complete-request", json={"session_id": client.acp_session_id},
            )
            assert response.status_code == 200
        await asyncio.wait_for(prompt, 5)
        withdrawn = await wait_event(events, "ask_user_withdrawn")
        assert withdrawn["request_id"] == request["request_id"]
        assert client.pending_ask_user() == []
        assert not client.has_pending_elicitation_request(request["request_id"])
        assert not client.resolve_elicitation("", {}, request_id=request["request_id"])
        assert await asyncio.wait_for(client.send_prompt("fresh"), 5)
        assert client.is_running and wire._failure is None
    finally:
        await client.shutdown()
    assert proc.poll() is None
