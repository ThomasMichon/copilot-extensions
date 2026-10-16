"""Actual authentication consumers and one-time codes keep exact ownership."""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import httpx
import pytest
from starlette.requests import Request
from zdd.private_protocol import Deadline, PrivateIPCError

from agent_bridge.app import create_app
from agent_bridge.app_routes import configure_serving_app
from agent_bridge.auth import BearerAuthMiddleware
from agent_bridge.auth_custody import AuthCustody, AuthCustodyMiddleware
from agent_bridge.auth_runtime import capture_authentication, restore_authentication
from agent_bridge.effective_runtime import ServingRuntimeCapture
from agent_bridge.db import Database
from agent_bridge.live_runtime import capture_live_events, restore_live_events
from agent_bridge.models import ServiceConfig
from agent_bridge.result_tokens import _event_ref, _position_token, _span_ref
from agent_bridge.routes import ui
from agent_bridge.runtime_restore import restore_runtime
from agent_bridge.runtime_transfer import _self, receive_runtime
from test_live_runtime import represented_graph
from test_runtime_restore import captured as captured
from test_runtime_transfer import MemoryChannel, request
from test_worktree_runtime import full_capture


def serving():
    app = configure_serving_app(ServiceConfig(), "synthetic-auth-value", lifespan=None)
    app.middleware_stack = app.build_middleware_stack()
    return app


def headers():
    scheme = "Bear" + "er "
    value = scheme + "synthetic-auth-value"
    assert value[7:] == "synthetic-auth-value" and "*" not in value
    return {"Authorization": value}

def layer(app, kind):
    current = app.middleware_stack
    while type(current) is not kind:
        current = current.app
    return current


@pytest.mark.parametrize("state", ["absent", "none", "empty", "codes"])
def test_code_roundtrip_preserves_absence_expiry_and_detachment(state):
    app = serving()
    if state != "absent":
        app.state.ui_login_codes = (
            None if state == "none" else {} if state == "empty" else {
                "unused": time.monotonic() + 50.0, "expired": time.monotonic() - 1.0,
            }
        )
    value = capture_authentication(app)
    restored = restore_authentication(value)
    target = serving()
    restored.bind(target)
    restored.verify(target)
    assert restored.private_payload() == value
    if state == "codes":
        assert target.state.ui_login_codes is not app.state.ui_login_codes
        assert target.state.ui_login_codes == app.state.ui_login_codes
        app.state.ui_login_codes["unused"] += 1.0
        assert restored.private_payload() == value
    assert "unused" not in repr(restored) and "synthetic-auth-value" not in repr(restored)


@pytest.mark.parametrize("mutation", [
    "unbuilt", "state-token", "middleware-token", "declaration", "custody",
    "stack", "active",
])
def test_actual_http_state_or_ownership_disagreement_refuses(mutation):
    app = serving()
    if mutation == "unbuilt":
        app.middleware_stack = None
    elif mutation == "state-token":
        app.state.auth_token = "different"
    elif mutation == "middleware-token":
        layer(app, BearerAuthMiddleware)._token = "different"
    elif mutation == "declaration":
        app.user_middleware[-1].kwargs["token"] = "different"
    elif mutation == "custody":
        app.state.auth_custody = AuthCustody()
    elif mutation == "stack":
        layer(app, AuthCustodyMiddleware).app = app.router
    else:
        app.state.auth_custody._inflight = 1
    with pytest.raises(PrivateIPCError):
        capture_authentication(app)


@pytest.mark.parametrize("mutation", ["extra", "bool", "state", "code", "expiry", "large"])
def test_malformed_private_authentication_refuses_without_coercion(mutation):
    value = {"schema": 1, "codes_state": "present", "codes": {"code": 123.0}}
    if mutation == "extra":
        value["extra"] = "secret"
    elif mutation == "bool":
        value["schema"] = True
    elif mutation == "state":
        value["codes_state"] = "absent"
    elif mutation == "code":
        value["codes"] = {True: 123.0}
    elif mutation == "expiry":
        value["codes"]["code"] = float("inf")
    else:
        value["codes"] = {str(i): 123.0 for i in range(257)}
    with pytest.raises(PrivateIPCError):
        restore_authentication(value)


@pytest.mark.asyncio
async def test_restored_http_ui_and_websocket_authentication_use_same_actual_token(monkeypatch):
    source = serving()
    source.state.ui_login_codes = {
        "unused": time.monotonic() + 50.0, "expired": time.monotonic() - 1.0,
    }
    restored = restore_authentication(capture_authentication(source))
    app = configure_serving_app(
        ServiceConfig(), source.state.auth_token, lifespan=None, auth_custody=restored.custody,
    )
    restored.bind(app)
    app.state.session_manager = SimpleNamespace()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test",
    ) as client:
        assert (await client.post("/api/v1/ui/login-codes")).status_code == 401
        minted = await client.post("/api/v1/ui/login-codes", headers=headers())
        assert minted.status_code == 200 and minted.json()["expires_in"] == 60.0
        expired = await client.post("/ui/exchange", json={"code": "expired"})
        assert expired.status_code == 403
        exchange = await client.post("/ui/exchange", json={"code": "unused"})
        assert exchange.json() == {"token": source.state.auth_token}
        assert exchange.headers["cache-control"] == "no-store"
        assert (await client.post("/ui/exchange", json={"code": "unused"})).status_code == 403
    from agent_bridge.routes.acp_ws import _run_bridge_ws
    from starlette.websockets import WebSocket

    sent = []
    async def send(message):
        sent.append(message)
    async def receive():
        return {"type": "websocket.connect"}
    ws = WebSocket({
        "type": "websocket", "app": app, "path": "/acp/actual",
        "headers": [], "query_string": b"", "scheme": "ws", "server": ("test", 80),
    }, receive, send)
    await _run_bridge_ws(ws, agent_name="actual", adopt_session_id=None)
    assert sent == [{"type": "websocket.close", "code": 1008, "reason": ""}]
    def reached_target(_agent):
        assert restored.custody._inflight == 1
        raise RuntimeError("authenticated target reached")
    app.state.resolver = SimpleNamespace(canonical_agent_name=reached_target)
    for offered, header in (
        ([], [(b"authorization", headers()["Authorization"].encode())]),
        (["bearer.synthetic-auth-value"], []),
    ):
        ws.scope["subprotocols"], ws.scope["headers"] = offered, header
        with pytest.raises(RuntimeError, match="authenticated target reached"):
            await _run_bridge_ws(WebSocket(ws.scope, receive, send), agent_name="actual", adopt_session_id=None)
    assert restored.custody._inflight == 0


@pytest.mark.asyncio
async def test_direct_ui_body_await_and_cancel_remain_owned():
    app = serving()
    entered, release = asyncio.Event(), asyncio.Event()
    async def receive():
        entered.set()
        await release.wait()
        return {"type": "http.request", "body": b'{"code":"unused"}'}
    req = Request({"type": "http", "app": app}, receive)
    task = asyncio.create_task(ui.exchange_login_code(req))
    await entered.wait()
    with pytest.raises(PrivateIPCError):
        capture_authentication(app)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert app.state.auth_custody._inflight == 0
    assert capture_authentication(app)["codes_state"] == "absent"


@pytest.mark.asyncio
@pytest.mark.parametrize("scope_type", ["http", "websocket"])
async def test_entire_asgi_operation_including_response_or_connection_is_owned(scope_type):
    app = serving()
    entered, release = asyncio.Event(), asyncio.Event()
    async def endpoint(scope, receive, send):
        entered.set()
        await release.wait()
    # Retain the real custody wrapper and replace only its downstream test handler.
    wrapper = layer(app, AuthCustodyMiddleware)
    assert type(wrapper.app) is BearerAuthMiddleware
    wrapper.app = endpoint
    task = asyncio.create_task(wrapper({"type": scope_type}, None, None))
    await entered.wait()
    with pytest.raises(PrivateIPCError):
        with app.state.auth_custody.quiescent():
            pytest.fail("active operation captured")
    release.set()
    await task
    assert app.state.auth_custody._inflight == 0


@pytest.mark.asyncio
async def test_full_private_runtime_retains_exact_authentication_graph(captured, monkeypatch):
    source, origins, _capture = captured
    full_capture(captured)
    source.state.live_event_store, _ = represented_graph()
    configured = serving()
    source.state.auth_token = configured.state.auth_token
    source.state.auth_custody = configured.state.auth_custody
    source.user_middleware = configured.user_middleware
    source.middleware_stack = source.build_middleware_stack()
    source.state.ui_login_codes = {"unused": time.monotonic() + 50.0}
    capture = ServingRuntimeCapture(
        source, origins, include_resolver=True, include_relay=True, include_roster=True,
        include_sessions=True, include_worktrees=True, include_live_events=True,
        include_authentication=True, session_deadline=Deadline(10),
    )
    payload = capture.capture().private_payload()
    assert restore_runtime(payload).authentication.private_payload() == payload["runtime"]["authentication"]
    old = capture.capture()
    source.state.ui_login_codes["unused"] += 1.0
    assert not capture.matches(old)
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(origins.config_root))
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(origins.install_root))
    channel = MemoryChannel(_self())
    request(capture, channel, "c" * 64)
    pending = receive_runtime(channel, channel.peer, namespace="c" * 64, deadline=Deadline(10))
    app = create_app(restored_runtime=pending.runtime)
    async with app.router.lifespan_context(app):
        pending.acknowledge(app)
        pending.verify_resources(app)
        codes = app.state.ui_login_codes
        app.state.ui_login_codes = dict(codes)
        with pytest.raises(PrivateIPCError):
            pending.verify_resources(app)
        app.state.ui_login_codes = codes
        codes["unused"] += 1.0
        with pytest.raises(PrivateIPCError):
            pending.verify_resources(app)
        assert not pending.serving and app.state.ready is False


@pytest.mark.asyncio
async def test_actual_result_routes_retarget_position_event_and_span_after_restoration(tmp_path):
    app = serving()
    db = Database(tmp_path / "represented.db")
    app.state.db = db
    source, continuities = represented_graph()
    app.state.live_event_store = source
    try:
        db.create_cli_mode_reservation("synthetic-venue", now=time.time())
        for sid in ("a", "b", "c"):
            assert db.register_live_session(
                sid, machine="synthetic", cwd="/synthetic", worktree_id="synthetic-venue",
                repo=None, branch=None, pid=123, role=None, now=time.time(), process_started_at=100.0,
            ) == "live"
        assert set(db.live_session_aliases_to("c")) >= {"a", "b"}
        tokens = {
            "position": _position_token("represented", "b", continuities["b"], 1),
            "event": _event_ref("represented", "b", continuities["b"], 1),
            "span": _span_ref("represented", "b", continuities["b"], 1, 1),
        }
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test", headers=headers(),
        ) as client:
            async def consume():
                position = await client.get(
                    "/api/v1/live-sessions/c/result", params={"position": tokens["position"]},
                )
                details = [
                    await client.get("/api/v1/live-sessions/c/result/detail", params={"ref": tokens[kind]})
                    for kind in ("event", "span")
                ]
                assert position.status_code == 200
                assert all(detail.status_code == 200 for detail in details)
                return [position.json(), *(detail.json() for detail in details)]

            before = await consume()
            app.state.live_event_store = restore_live_events(capture_live_events(source))
            assert await consume() == before
            assert app.state.live_event_store is not source
    finally:
        db.close()
