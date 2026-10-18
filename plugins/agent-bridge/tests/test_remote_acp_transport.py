"""Remote ACP contract coverage; synthetic origins and credentials only."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import httpx
from acp.schema import PermissionOption, ToolCallUpdate, PromptResponse
from credential_relay.http_auth import HttpAuthContext, SourceHttpAuth

from agent_bridge.acp_client import AcpClient
from agent_bridge.remote_acp_config import NAMESPACE, PROFILE, RemoteAcpTarget, validate_capabilities
from agent_bridge.remote_acp_http import HttpAcpTransport
from agent_bridge.remote_acp_transport import RemoteTransport, RemoteTransportError, rpc_key
from agent_bridge.remote_acp_ws import WsAcpTransport


def target(**overrides):
    return RemoteAcpTarget(
        endpoint="https://example.com/acp", transport="streamable-http",
        auth={"source": "git-credential", "identity": "example"},
        **overrides,
    )


def test_endpoint_refuses_secrets_cleartext_and_unknown_fields():
    base = target().model_dump()
    for change in [
        {"endpoint": "http://example.com/acp"},
        {"endpoint": "https://user:secret@example.com/acp"},
        {"endpoint": "https://example.com/acp?token=synthetic"},
        {"headers": {"Authorization": "synthetic"}},
        {"profile": "unqualified-draft"},
    ]:
        with pytest.raises(ValueError):
            RemoteAcpTarget.model_validate({**base, **change})
    assert HttpAcpTransport is not None and WsAcpTransport is not None


@pytest.mark.parametrize("raw", [b"not-json", b"[]", b'{"jsonrpc":"1.0"}', b"\xff"])
def test_malformed_frames_fail_without_returning_payload(raw):
    wire = RemoteTransport(target())
    with pytest.raises(RemoteTransportError, match="Malformed"):
        wire.decode(raw)


def test_invalid_config_is_sanitized_and_never_uses_defaults(tmp_path, monkeypatch):
    from agent_bridge import config
    from agent_bridge.models import ServiceConfig

    invalid = {**target().model_dump(), "endpoint": "https://user:synthetic-private@example.com/acp"}
    with pytest.raises(ValueError) as refused:
        ServiceConfig(remote_acp={"example": invalid})
    assert "synthetic-private" not in str(refused.value)
    monkeypatch.setattr(config, "config_dir", lambda: tmp_path)
    (tmp_path / "config.yaml").write_text(json.dumps({"remote_acp": {"example": invalid}}))
    with pytest.raises(ValueError, match="refusing configuration fallback"):
        config.load_config()


@pytest.mark.parametrize("version", [True, 1.0, "1", None])
def test_approval_version_is_not_coerced(version):
    with pytest.raises(PermissionError):
        validate_capabilities(SimpleNamespace(field_meta={NAMESPACE: {
            "profile": PROFILE, "applicationApprovals": {"version": version, "required": True},
        }}))


@pytest.mark.asyncio
async def test_target_identity_is_immutable_and_injected_auth_is_scope_qualified():
    spec = RemoteAcpTarget(
        endpoint="https://example.com/acp", transport="streamable-http",
        auth={"source": "injected", "identity": "example",
              "resource": "https://example.com/resource", "scope": "read"},
    )
    with pytest.raises(ValueError):
        spec.endpoint = "https://other.example.com/acp"
    with pytest.raises(ValueError):
        spec.auth.identity = "other"
    wire = RemoteTransport(spec)
    with pytest.raises(RemoteTransportError, match="provider"):
        await wire.headers()
    seen = []

    class Injector:
        async def headers(self, context):
            seen.append(context)
            return {"X-Synthetic-Auth": "synthetic"}

        async def invalidate(self, context):
            seen.append(context)

    wire.auth = Injector()
    assert await wire.headers() == {"X-Synthetic-Auth": "synthetic"}
    assert seen[0].origin == spec.origin and seen[0].identity == "example"
    assert seen[0].resource == "https://example.com/resource" and seen[0].scope == "read"
    await wire.close()


@pytest.mark.asyncio
async def test_scoped_auth_builds_real_header_and_refuses_other_identity():
    calls = []

    class Source:
        def supports(self, action, fields):
            return action == "get"

        async def resolve(self, action, fields, *, timeout):
            calls.append(fields)
            return "password=synthetic-credential\n\n"

    context = HttpAuthContext("https://example.com", "example", "generation")
    injector = SourceHttpAuth(Source(), context, "https://example.com/acp")
    headers = await injector.headers(context)
    scheme = "Bear" + "er "
    assert headers == {"Authorization": scheme + "synthetic-credential"}
    assert calls[0]["username"] == "example"
    with pytest.raises(PermissionError):
        await injector.headers(HttpAuthContext(context.origin, "other", "generation"))
    await injector.invalidate(context)
    await injector.headers(context)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_queue_frames_rpc_types_and_cross_session_isolation():
    transport = RemoteTransport(target(
        max_queue_messages=1, max_frame_bytes=1024, timeout_seconds=1,
    ))
    assert rpc_key(7) != rpc_key("7")
    transport.sessions.add("one")
    await transport.incoming({
        "jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "one"},
    }, "one")
    with pytest.raises(RemoteTransportError, match="queue"):
        await transport.incoming({"jsonrpc": "2.0", "method": "notice"})
    with pytest.raises(RemoteTransportError, match="frame"):
        transport.decode("x" * 1025)
    with pytest.raises(RemoteTransportError, match="routing"):
        await transport.incoming({
            "jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "two"},
        }, "one")
    await transport.close()
    assert await transport.receive() is None


@pytest.mark.asyncio
async def test_callback_response_preserves_session_routing():
    transport = RemoteTransport(target())
    transport.sessions.update({"one", "two"})
    await transport.incoming({
        "jsonrpc": "2.0", "id": "opaque", "method": "session/request_permission",
        "params": {"sessionId": "two"},
    }, "two")
    transport.outgoing_session({
        "jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "two"},
    })
    assert transport.outgoing_session({
        "jsonrpc": "2.0", "id": "opaque", "result": {},
    }) == "two"
    with pytest.raises(RemoteTransportError):
        transport.outgoing_session({"jsonrpc": "2.0", "id": "opaque", "result": {}})
    await transport.close()


@pytest.mark.asyncio
async def test_sse_request_uses_idle_budget_even_with_injected_client_timeout():
    requests = []

    async def respond(request):
        requests.append(request)
        return httpx.Response(200, extensions={"http_version": b"HTTP/2"}, content=b"")

    spec = RemoteAcpTarget(
        endpoint="https://localhost/acp", transport="streamable-http",
        auth={"source": "none", "identity": "synthetic"}, allow_loopback_test=True,
        timeout_seconds=1, idle_seconds=5,
    )
    wire = HttpAcpTransport(
        spec,
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(respond), timeout=0.1,
        ),
    )
    try:
        for method in ("GET", "POST"):
            response = await wire._request(method, {})
            await response.aclose()
        assert requests[0].extensions["timeout"] == {
            "connect": 1, "read": 5, "write": 1, "pool": 1,
        }
        assert requests[1].extensions["timeout"]["read"] == 1
    finally:
        await wire.close()


def live_client():
    events = []
    client = AcpClient(on_event=lambda kind, data: events.append((kind, data)))
    client._remote_transport = RemoteTransport(target())
    client._remote_answers = {}
    client._remote_negotiated = True
    client._host_mode = True
    client._host_transport_alive = True
    client._connection = object()
    client._acp_session_id = "session-one"
    client.auto_approve = True  # Application approvals must ignore this.
    return client, events


@pytest.mark.asyncio
@pytest.mark.parametrize("option", ["accept-opaque", "reject-opaque"])
async def test_explicit_agent_and_operator_permission_answers(option):
    client, events = live_client()
    options = [
        PermissionOption(option_id="accept-opaque", name="Accept", kind="allow_once"),
        PermissionOption(option_id="reject-opaque", name="Reject", kind="reject_once"),
    ]
    decision = {"applicationApproval": {"version": 1, "decisionId": "durable/opaque"}}
    task = asyncio.create_task(client._remote_permission(
        options, "session-one", ToolCallUpdate(tool_call_id="tool/opaque"),
        {NAMESPACE: decision},
    ))
    await asyncio.sleep(0)
    assert not task.done()
    data = events[-1][1]
    assert data["decision_id"] == "durable/opaque"
    assert data["tool_call_id"] == "tool/opaque"
    assert data["request_id"] != data["decision_id"]
    assert not client.resolve_permission("yes", option)
    with pytest.raises(ValueError):
        client.resolve_permission(data["request_id"], "not-offered")
    assert client.resolve_permission(data["request_id"], option)
    assert not client.resolve_permission(data["request_id"], option)
    result = await task
    assert result.field_meta[NAMESPACE] == decision
    assert result.outcome.option_id == option


@pytest.mark.asyncio
async def test_form_requires_live_token_preserves_input_and_cancels_stale():
    client, events = live_client()
    mode = SimpleNamespace(
        session_id="session-one", tool_call_id="server-tool", requested_schema={"type": "object"},
    )
    task = asyncio.create_task(client._remote_elicitation(
        "Decide", mode,
        {NAMESPACE: {"applicationApproval": {"version": 1, "decisionId": "decision"}}},
    ))
    await asyncio.sleep(0)
    token = events[-1][1]["request_id"]
    content = {"opaque": ["unchanged"], "count": 7, "flag": False}
    assert not client.resolve_elicitation("server-tool", content)
    assert not client.resolve_elicitation("wrong-tool", content, request_id=token)
    assert client.resolve_elicitation("server-tool", content, request_id=token)
    assert (await task).content == content
    assert not client.resolve_elicitation("server-tool", content, request_id=token)
    task = asyncio.create_task(client._remote_elicitation("Again", mode, {}))
    await asyncio.sleep(0)
    client._remote_lost()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not client.resolve_elicitation("server-tool", content, request_id=token)


@pytest.mark.asyncio
async def test_replay_and_wrong_session_never_create_answer_authority():
    client, events = live_client()
    with pytest.raises(ValueError):
        await client._remote_permission([], "other-session", ToolCallUpdate(tool_call_id="tool"), {})
    with pytest.raises(ValueError):
        await client._remote_elicitation(
            "Wrong session",
            SimpleNamespace(session_id="other-session", tool_call_id="tool", requested_schema={}), {},
        )
    client._loading_session = True
    with pytest.raises(ValueError):
        await client._remote_elicitation(
            "Replayed", SimpleNamespace(session_id="session-one", tool_call_id="tool", requested_schema={}), {},
        )
    assert events == []


@pytest.mark.asyncio
async def test_native_decision_id_withdrawal_and_cross_client_answers():
    client, events = live_client()
    other, _ = live_client()
    task = asyncio.create_task(client._remote_permission(
        [PermissionOption(option_id="offered", name="Allow", kind="allow_once")],
        "session-one", ToolCallUpdate(tool_call_id="native-durable-id"),
        {NAMESPACE: {"applicationApproval": {"version": 1}}},
    ))
    await asyncio.sleep(0)
    data = events[-1][1]
    assert data["decision_id"] == data["tool_call_id"] == "native-durable-id"
    assert not other.resolve_permission(data["request_id"], "offered")
    assert client.resolve_permission(data["request_id"], "offered")
    assert (await task).outcome.option_id == "offered"
    assert not client.resolve_permission(data["request_id"], "offered")
    task = asyncio.create_task(client._remote_elicitation(
        "Withdraw", SimpleNamespace(
            session_id="session-one", tool_call_id="native-durable-id", requested_schema={},
        ), {NAMESPACE: {"applicationApproval": {"version": 1}}},
    ))
    await asyncio.sleep(0)
    token = events[-1][1]["request_id"]
    client._remote_withdraw("native-durable-id", {})
    assert not task.done()
    client._remote_withdraw("separate-elicitation-id", {NAMESPACE: {
        "applicationApproval": {"version": 1, "decisionId": "native-durable-id"},
    }})
    assert (await task).action == "cancel"
    assert not client.resolve_elicitation("native-durable-id", {}, request_id=token)
    assert events[-1][1]["native_elicitation_id"] == "separate-elicitation-id"


@pytest.mark.asyncio
async def test_rpc_deadline_is_bounded_independently_of_stream_updates():
    transport = RemoteTransport(target(rpc_timeout_seconds=1))
    transport.sessions.add("one")
    transport.outgoing_session({
        "jsonrpc": "2.0", "id": 7, "method": "session/prompt",
        "params": {"sessionId": "one"},
    })

    async def updates():
        while not transport._ended.is_set():
            await transport.incoming({
                "jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "one"},
            }, "one")
            await asyncio.sleep(0.05)

    async def drain():
        while True:
            await transport.receive()

    producer = asyncio.create_task(updates())
    try:
        with pytest.raises(RemoteTransportError, match="deadline"):
            await asyncio.wait_for(drain(), 3)
        with pytest.raises(RemoteTransportError, match="closed"):
            transport.outgoing_session({"method": "session/prompt", "params": {"sessionId": "one"}})
    finally:
        producer.cancel()
        await asyncio.gather(producer, return_exceptions=True)
        await transport.close()


@pytest.mark.asyncio
async def test_unadvertised_load_refuses_before_any_rpc():
    client, _ = live_client()
    client._remote_can_load = False
    with pytest.raises(PermissionError, match="session/load"):
        await client.load_session(cwd="/workspace", session_id="saved")
    assert client._remote_transport._pending == {}


@pytest.mark.asyncio
async def test_stalled_sse_stream_has_an_independent_idle_deadline():
    class Stalled(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b": ready\n\n"
            await asyncio.Event().wait()

    async def handler(request):
        if request.method == "DELETE":
            return httpx.Response(202, extensions={"http_version": b"HTTP/2"})
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, stream=Stalled(),
            extensions={"http_version": b"HTTP/2"},
        )

    spec = RemoteAcpTarget(
        endpoint="https://localhost/acp", transport="streamable-http",
        auth={"source": "none", "identity": "synthetic"}, allow_loopback_test=True,
        idle_seconds=1,
    )
    wire = HttpAcpTransport(
        spec, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    wire._connection_id = "synthetic-connection"
    try:
        await wire._open_stream(None)
        with pytest.raises(RemoteTransportError, match="stream lost"):
            await asyncio.wait_for(wire.receive(), 3)
    finally:
        await wire.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", [b"\n", b"\r", b"\r\n"])
@pytest.mark.parametrize("split_bytes", [False, True])
async def test_sse_line_endings_and_chunk_boundaries(ending, split_bytes):
    message = {"jsonrpc": "2.0", "method": "synthetic/notice", "params": {"value": 7}}
    frame = ending.join([
        b": heartbeat", b'data: {"jsonrpc":"2.0",', b'data: "method":"synthetic/notice",',
        b'data: "params":{"value":7}}', b"", b"",
    ])

    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self):
            for chunk in ([bytes([byte]) for byte in frame] if split_bytes else [frame]):
                yield chunk
            await asyncio.Event().wait()

    async def respond(request):
        return httpx.Response(
            202 if request.method == "DELETE" else 200,
            headers={"content-type": "text/event-stream"}, stream=Stream(),
            extensions={"http_version": b"HTTP/2"},
        )

    spec = RemoteAcpTarget(
        endpoint="https://localhost/acp", transport="streamable-http",
        auth={"source": "none", "identity": "synthetic"}, allow_loopback_test=True,
    )
    wire = HttpAcpTransport(
        spec, client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    )
    wire._connection_id = "synthetic"
    try:
        await wire._open_stream(None)
        assert await asyncio.wait_for(wire.receive(), 3) == message
    finally:
        await wire.close()


@pytest.mark.asyncio
async def test_ws_handshake_finishing_after_close_releases_acquired_socket(monkeypatch):
    from agent_bridge import remote_acp_ws

    entered, release = asyncio.Event(), asyncio.Event()
    socket = SimpleNamespace(close=AsyncMock())

    async def handshake(*args, **kwargs):
        entered.set()
        await release.wait()
        return socket

    spec = RemoteAcpTarget(
        endpoint="https://localhost/acp", transport="websocket",
        auth={"source": "none", "identity": "synthetic"}, allow_loopback_test=True,
    )
    monkeypatch.setattr(remote_acp_ws, "_NoRedirectConnect", handshake)
    wire = WsAcpTransport(spec)
    opening = asyncio.create_task(wire.open())
    await entered.wait()
    await wire.close()
    release.set()
    with pytest.raises(RemoteTransportError, match="handshake failed"):
        await opening
    socket.close.assert_awaited_once()
    assert wire.closed and not wire._tasks


@pytest.mark.asyncio
async def test_session_events_wait_for_owner_adoption_after_lifecycle_result():
    wire = RemoteTransport(target())
    wire.outgoing_session({"jsonrpc": "2.0", "id": 1, "method": "session/new", "params": {}})
    result = {"jsonrpc": "2.0", "id": 1, "result": {"sessionId": "new-session"}}
    await wire.incoming(result)
    update = {"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": "new-session"}}
    arriving = asyncio.create_task(wire.incoming(update, "new-session"))
    await asyncio.sleep(0)
    assert not arriving.done()
    assert await wire.receive() == result
    await wire.adopt_session("new-session")
    await arriving
    assert await wire.receive() == update
    await wire.close()


@pytest.mark.asyncio
async def test_re_raised_sdk_validation_error_contains_no_untrusted_result_value():
    client, _ = live_client()

    async def invalid_result():
        return PromptResponse.model_validate({"stopReason": "SYNTHETIC-SENSITIVE-MARKER"})

    with pytest.raises(RemoteTransportError) as failure:
        await client._remote_sdk_rpc(invalid_result)
    assert "SYNTHETIC-SENSITIVE-MARKER" not in str(failure.value)
    assert "SYNTHETIC-SENSITIVE-MARKER" not in repr(failure.value)
    await client._remote_transport.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["permission", "form"])
async def test_idle_approval_has_independent_deadline_and_refuses_late_answer(kind):
    client, events = live_client()
    client._remote_transport = RemoteTransport(target(rpc_timeout_seconds=1))
    assert not client._remote_transport._pending
    if kind == "permission":
        pending = asyncio.create_task(client._remote_permission(
            [PermissionOption(option_id="explicit", name="Choose", kind="allow_once")],
            "session-one", ToolCallUpdate(tool_call_id="tool"), {},
        ))
    else:
        pending = asyncio.create_task(client._remote_elicitation(
            "Choose", SimpleNamespace(
                session_id="session-one", tool_call_id="tool", requested_schema={},
            ), {},
        ))
    await asyncio.sleep(0)
    token = events[-1][1]["request_id"]
    with pytest.raises(RemoteTransportError, match="approval deadline"):
        await asyncio.wait_for(pending, 3)
    if kind == "permission":
        assert not client.resolve_permission(token, "explicit")
    else:
        assert not client.resolve_elicitation("tool", {}, request_id=token)
    assert not client._remote_answers
    await client._remote_transport.close()


@pytest.mark.asyncio
async def test_close_while_sse_get_opens_cannot_report_ready_success():
    entered = asyncio.Event()
    spec = RemoteAcpTarget(
        endpoint="https://localhost/acp", transport="streamable-http",
        auth={"source": "none", "identity": "synthetic"}, allow_loopback_test=True,
    )
    wire = HttpAcpTransport(spec)
    wire._connection_id = "synthetic"

    async def request(method, headers, content=None, **kwargs):
        if method == "GET":
            entered.set()
            await asyncio.Event().wait()
        return httpx.Response(202, extensions={"http_version": b"HTTP/2"})

    wire._request = request
    opening = asyncio.create_task(wire._open_stream("session-one"))
    await entered.wait()
    await wire.close()
    with pytest.raises(RemoteTransportError, match="closed during stream setup"):
        await opening


@pytest.mark.asyncio
async def test_permission_event_retains_native_action_details_available_only_in_callback():
    client, events = live_client()
    call = ToolCallUpdate.model_validate({
        "toolCallId": "native-tool", "title": "Inspect action",
        "rawInput": {"command": "echo synthetic", "target": "/workspace/example.txt"},
        "content": [{"type": "content", "content": {
            "type": "text", "text": "Exact action details",
        }}],
        "locations": [{"path": "/workspace/example.txt", "line": 7}],
    })
    pending = asyncio.create_task(client._remote_permission(
        [PermissionOption(option_id="explicit", name="Choose", kind="allow_once")],
        "session-one", call, {},
    ))
    await asyncio.sleep(0)
    event = events[-1][1]
    assert event["tool_call"] == call.model_dump(by_alias=True, exclude_none=True)
    assert client.resolve_permission(event["request_id"], "explicit")
    await pending
    await client._remote_transport.close()


@pytest.mark.asyncio
async def test_completed_native_request_retires_form_without_losing_callback_route():
    client, events = live_client()
    wire = client._remote_transport
    wire.sessions.add("session-one")
    wire.on_request_completed = client._remote_rpc_completed
    wire.outgoing_session({
        "jsonrpc": "2.0", "id": 7, "method": "session/prompt",
        "params": {"sessionId": "session-one"},
    })
    await wire.incoming({
        "jsonrpc": "2.0", "id": "server-rpc", "method": "elicitation/create",
        "params": {"requestId": 7},
    }, "session-one")
    await wire.receive()
    pending = asyncio.create_task(client._remote_elicitation(
        "Choose", SimpleNamespace(request_id=7, requested_schema={}), {},
    ))
    await asyncio.sleep(0)
    token = events[-1][1]["request_id"]
    await wire.incoming({
        "jsonrpc": "2.0", "id": 7, "result": {"stopReason": "end_turn"},
    }, "session-one")
    assert (await pending).action == "cancel"
    assert client.pending_ask_user() == [] and not client.has_pending_elicitation_request(token)
    assert not client.resolve_elicitation("", {}, request_id=token)
    assert events[-1][0] == "ask_user_withdrawn" and events[-1][1]["request_id"] == token
    assert wire.outgoing_session({
        "jsonrpc": "2.0", "id": "server-rpc", "result": {"action": "cancel"},
    }) == "session-one"
    assert wire._failure is None and client.is_running
    await wire.close()
