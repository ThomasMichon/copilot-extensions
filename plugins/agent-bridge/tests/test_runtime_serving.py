"""Actual authenticated foreground serving requires fresh receiver authority."""

import asyncio
import sys
from types import SimpleNamespace

import httpx
import pytest
from zdd.private_protocol import Deadline, PrivateIPCError, Refusal, read_frame, write_frame
from zdd.private_receiver import Operation

from agent_bridge.app import create_app
from agent_bridge.runtime_controls import RuntimeControls
from test_roster_runtime import roster_capture
from test_runtime_controls import listener, publish, request
from test_runtime_restore import captured as captured
from test_runtime_transfer import native_child, prepare
from agent_bridge.runtime_transfer import transfer_runtime
from dataclasses import replace


def serving_candidate(captured, monkeypatch):
    app, origins, _capture = captured
    capture = roster_capture(captured)
    return prepare((app, origins, capture), monkeypatch)


def test_foreground_serves_real_authenticated_routes_only_after_fenced_commit(
    captured, monkeypatch,
):
    channel, pending = serving_candidate(captured, monkeypatch)
    pending.runtime.origins.config_root.mkdir()

    def forbidden(*args, **kwargs):
        pytest.fail("serving preparation rediscovered state or attached hosts")

    monkeypatch.setattr("agent_bridge.config.load_config", forbidden)
    monkeypatch.setattr("agent_bridge.app.daemon_resolver", forbidden)
    monkeypatch.setattr(type(pending.runtime.resolver), "refresh_provider_resolvers", forbidden)
    monkeypatch.setattr("agent_bridge.session_manager.SessionManager.reattach_session_hosts", forbidden)

    async def scenario():
        app = create_app(restored_runtime=pending.runtime)
        server = SimpleNamespace(started=True, should_exit=False)
        app.state.uvicorn_server = server
        async with app.router.lifespan_context(app):
            pending.acknowledge(app)
            with listener() as sock:
                controls = RuntimeControls(app, sock, pending.runtime.origins, pending=pending)
                endpoint = replace(controls.endpoint(), generation=1)
                publish(pending.runtime.origins.config_root, endpoint)
                with pytest.raises(PrivateIPCError):
                    controls.execute(channel, request(controls, endpoint, Operation.ACTIVATE_SERVING))
                controls.execute(channel, request(controls, endpoint, Operation.ACTIVATE_RESOURCES))
                pending.prepare_serving(app, Deadline(2))
                writer = app.state.db._writer_thread
                assert writer.is_alive() and not app.state.ready and app.state.passive
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://fixture",
                ) as client:
                    assert (await client.get("/api/v1/sessions")).status_code == 404
                    assert (await client.get("/health")).json()["ready"] is False
                    wire = request(controls, endpoint, Operation.ACTIVATE_SERVING)
                    assert controls.execute(channel, wire) == {
                        "serving": True, "ready": True, "adopted": False,
                    }
                    assert controls.execute(
                        channel, request(controls, endpoint, Operation.ATTEST),
                    )["ready"] is True
                    assert (await client.get("/api/v1/sessions")).status_code == 401
                    scheme = "Bear" + "er "
                    headers = {"Authorization": scheme + pending.runtime.auth_token}
                    assert headers["Authorization"][7:] == pending.runtime.auth_token
                    response = await client.get("/api/v1/sessions", headers=headers)
                    assert response.status_code == 200
                    roster = await client.get("/api/v1/agents?require_complete=true", headers=headers)
                    assert roster.status_code == 200 and roster.json()["agents"][0]["name"] == "actual"
                    forced = await client.get(
                        "/api/v1/agents?require_complete=true&force_refresh=true", headers=headers,
                    )
                    assert forced.status_code == 503
                    assert app.state.agent_roster_cache is pending.runtime.roster
                    assert not app.state.publish_on_ready and not app.state.passive
                    assert app.state.credential_relay is None or not app.state.credential_relay.running
                    with pytest.raises(PrivateIPCError):
                        controls.execute(channel, wire)
                    with pytest.raises(PrivateIPCError):
                        controls.execute(channel, request(controls, endpoint, Operation.ACTIVATE_SERVING))
                    with pytest.raises(PrivateIPCError):
                        request(controls, endpoint, Operation.ADOPT)
                    pending.verify_resources(app)
        assert not writer.is_alive() and not app.state.ready
        assert app._serving_target is None

    asyncio.run(scenario())


@pytest.mark.parametrize("drift", [
    "route", "previous", "server", "server-not-started", "server-exiting",
    "roster-expired", "config", "listener", "target", "writer-error", "middleware",
    "sessions",
])
def test_serving_commit_refuses_drift_and_never_exposes_foreground(captured, monkeypatch, drift):
    channel, pending = serving_candidate(captured, monkeypatch)
    pending.runtime.origins.config_root.mkdir()

    async def scenario():
        app = create_app(restored_runtime=pending.runtime)
        app.state.uvicorn_server = SimpleNamespace(started=True, should_exit=False)
        async with app.router.lifespan_context(app):
            pending.acknowledge(app)
            with listener() as sock:
                controls = RuntimeControls(app, sock, pending.runtime.origins, pending=pending)
                endpoint = replace(controls.endpoint(), generation=1)
                publish(pending.runtime.origins.config_root, endpoint)
                controls.execute(channel, request(controls, endpoint, Operation.ACTIVATE_RESOURCES))
                pending.prepare_serving(app, Deadline(2))
                wire = request(controls, endpoint, Operation.ACTIVATE_SERVING)
                if drift == "route":
                    publish(pending.runtime.origins.config_root, replace(endpoint, generation=2))
                elif drift == "previous":
                    active = replace(endpoint, generation=2)
                    publish(pending.runtime.origins.config_root, active, endpoint)
                    wire = request(controls, endpoint, Operation.ACTIVATE_SERVING, active=active)
                elif drift == "server":
                    app.state.uvicorn_server = SimpleNamespace(started=True, should_exit=False)
                elif drift == "server-not-started":
                    app.state.uvicorn_server.started = False
                elif drift == "server-exiting":
                    app.state.uvicorn_server.should_exit = True
                elif drift == "roster-expired":
                    monkeypatch.setattr(type(pending.runtime.roster), "ready", lambda self: False)
                elif drift == "config":
                    app.state.config.port += 1
                elif drift == "listener":
                    sock.close()
                elif drift == "target":
                    pending._serving._target.routes.clear()
                elif drift == "middleware":
                    from agent_bridge.auth import BearerAuthMiddleware
                    stack = pending._serving._target.middleware_stack
                    while type(stack) is not BearerAuthMiddleware:
                        stack = stack.app
                    stack._token = "different-fixture-token"
                elif drift == "sessions":
                    app.state.session_manager._sessions["uncovered"] = object()
                else:
                    app.state.db._writer_error = RuntimeError("fixture writer failure")
                with pytest.raises(PrivateIPCError):
                    controls.execute(channel, wire)
                assert not app.state.ready and app.state.passive
                assert app._serving_target is None
                if drift == "writer-error":
                    app.state.db._writer_error = None

    asyncio.run(scenario())


def test_serving_preparation_starts_worker_outside_fence_and_commit_never_schedules(
    captured, monkeypatch,
):
    from contextlib import contextmanager
    from agent_bridge.db import Database
    from agent_bridge.serving_runtime import CandidateServing
    from zdd import routing

    channel, pending = serving_candidate(captured, monkeypatch)
    pending.runtime.origins.config_root.mkdir()
    held = []
    lock = routing.routing_lock
    start = Database.start_writer
    activate = CandidateServing.activate

    @contextmanager
    def observed(*args, **kwargs):
        with lock(*args, **kwargs):
            held.append(True)
            try:
                yield
            finally:
                held.pop()

    def outside(db):
        assert not held
        start(db)

    def inside(serving):
        assert held
        activate(serving)

    monkeypatch.setattr(routing, "routing_lock", observed)
    monkeypatch.setattr(Database, "start_writer", outside)
    monkeypatch.setattr(CandidateServing, "activate", inside)

    async def scenario():
        app = create_app(restored_runtime=pending.runtime)
        app.state.uvicorn_server = SimpleNamespace(started=True, should_exit=False)
        async with app.router.lifespan_context(app):
            pending.acknowledge(app)
            with listener() as sock:
                controls = RuntimeControls(app, sock, pending.runtime.origins, pending=pending)
                endpoint = replace(controls.endpoint(), generation=1)
                publish(pending.runtime.origins.config_root, endpoint)
                controls.execute(channel, request(controls, endpoint, Operation.ACTIVATE_RESOURCES))
                pending.prepare_serving(app, Deadline(2))
                with monkeypatch.context() as patch:
                    patch.setattr(asyncio, "create_task", lambda *a, **kw: pytest.fail("scheduled"))
                    assert controls.execute(
                        channel, request(controls, endpoint, Operation.ACTIVATE_SERVING),
                    )["ready"] is True
                assert not held

    asyncio.run(scenario())


def test_missing_roster_never_gets_serving_authority(captured, monkeypatch):
    channel, pending = prepare(captured, monkeypatch)
    pending.runtime.origins.config_root.mkdir()

    async def scenario():
        app = create_app(restored_runtime=pending.runtime)
        async with app.router.lifespan_context(app):
            pending.acknowledge(app)
            with listener() as sock:
                controls = RuntimeControls(app, sock, pending.runtime.origins, pending=pending)
                endpoint = replace(controls.endpoint(), generation=1)
                publish(pending.runtime.origins.config_root, endpoint)
                controls.execute(channel, request(controls, endpoint, Operation.ACTIVATE_RESOURCES))
                with pytest.raises(PrivateIPCError):
                    pending.prepare_serving(app, Deadline(2))
                assert app.state.db._writer_thread is None
                with pytest.raises(PrivateIPCError):
                    request(controls, endpoint, Operation.ACTIVATE_SERVING)

    asyncio.run(scenario())


def test_roster_only_hydrated_sessions_cannot_bypass_nonempty_serving_refusal(
    captured, monkeypatch,
):
    import time
    from agent_bridge.events import EventLog
    from agent_bridge.models import SessionStatus

    predecessor_db = captured[0].state.db
    now = time.time()
    predecessor_db.create_session("live", "live", None, ".", "local", "running", now)
    predecessor_db.create_turn("live", 0, "synthetic live turn", now)
    log = EventLog(db=predecessor_db, session_id="live")
    log.append("user_message", {"content": "synthetic live turn"})
    predecessor_db.flush()
    predecessor_db.set_cursor("reader", "live", 1, now)
    channel, pending = serving_candidate(captured, monkeypatch)
    pending.runtime.origins.config_root.mkdir()

    async def scenario():
        app = create_app(restored_runtime=pending.runtime)
        app.state.uvicorn_server = SimpleNamespace(started=True, should_exit=False)
        async with app.router.lifespan_context(app):
            pending.acknowledge(app)
            hydrated = app.state.session_manager.get_session("live")
            readonly = app.state.db
            assert hydrated.event_log._db is readonly
            with listener() as sock:
                controls = RuntimeControls(app, sock, pending.runtime.origins, pending=pending)
                endpoint = replace(controls.endpoint(), generation=1)
                publish(pending.runtime.origins.config_root, endpoint)
                controls.execute(channel, request(controls, endpoint, Operation.ACTIVATE_RESOURCES))
                assert hydrated.event_log._db is app.state.db
                assert pending.runtime.sessions is None
                with pytest.raises(PrivateIPCError):
                    pending.prepare_serving(app, Deadline(2))
                assert app._serving_target is None and not app.state.ready
                assert app.state.db._writer_thread is None
                assert hydrated.status is SessionStatus.RUNNING and hydrated.client is None
                assert app.state.db.get_turn("live", 0)["completed_at"] is None
                assert app.state.db.get_cursor("reader", "live") == 1
                assert [row["event_id"] for row in app.state.db.execute_read(
                    "SELECT event_id FROM events WHERE session_id=? ORDER BY event_id", ("live",),
                )] == [1]
                pending.verify_resources(app)
        assert readonly._local.conn is None

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["writer", "verification"])
def test_expired_preparation_retains_cleanup_but_never_allows_later_commit(
    captured, monkeypatch, stage,
):
    channel, pending = serving_candidate(captured, monkeypatch)
    pending.runtime.origins.config_root.mkdir()
    from agent_bridge.serving_runtime import CandidateServing
    actual = CandidateServing.prepare_writer
    deadline = Deadline(2)

    def expire(self):
        actual(self)
        if stage == "writer":
            deadline._expires = 0

    monkeypatch.setattr(CandidateServing, "prepare_writer", expire)
    verify = type(pending).verify_resources

    def expire_after_verification(self, app):
        verify(self, app)
        if stage == "verification" and self._serving is not None:
            deadline._expires = 0

    monkeypatch.setattr(type(pending), "verify_resources", expire_after_verification)

    async def scenario():
        app = create_app(restored_runtime=pending.runtime)
        app.state.uvicorn_server = SimpleNamespace(started=True, should_exit=False)
        async with app.router.lifespan_context(app):
            pending.acknowledge(app)
            with listener() as sock:
                controls = RuntimeControls(app, sock, pending.runtime.origins, pending=pending)
                endpoint = replace(controls.endpoint(), generation=1)
                publish(pending.runtime.origins.config_root, endpoint)
                controls.execute(channel, request(controls, endpoint, Operation.ACTIVATE_RESOURCES))
                with pytest.raises(PrivateIPCError):
                    pending.prepare_serving(app, deadline)
                writer = app.state.db._writer_thread
                assert writer.is_alive()
                assert not pending._serving.prepared and not app.state.ready
                with pytest.raises(PrivateIPCError):
                    controls.execute(channel, request(controls, endpoint, Operation.ACTIVATE_SERVING))
                with pytest.raises(PrivateIPCError):
                    pending.prepare_serving(app, Deadline(2))
        assert not writer.is_alive()

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="native private channels")
def test_real_native_candidate_serves_authenticated_http_after_exact_grant(captured, tmp_path):
    app, origins, _capture = captured
    capture = roster_capture(captured)
    snapshot = capture.capture()
    with native_child(origins, tmp_path, "serving") as (channel, child, namespace):
        deadline = Deadline(10)
        transfer_runtime(
            channel, child, capture, snapshot, namespace=namespace, deadline=deadline,
        )
        for operation in (Operation.ACTIVATE_RESOURCES, Operation.ACTIVATE_SERVING, Operation.ATTEST):
            descriptor = read_frame(channel, deadline)
            write_frame(channel, {
                "grant": descriptor["grant"], "operation": operation.value,
            }, deadline)
            reply = read_frame(channel, deadline)
            if operation is Operation.ACTIVATE_RESOURCES:
                assert reply == {"resources_active": True, "ready": False}
            elif operation is Operation.ACTIVATE_SERVING:
                assert reply == {"serving": True, "ready": True, "adopted": False}
            else:
                assert reply == {
                    "pid": child.peer.pid, "process_start_time": child.peer.start_time,
                    "version": descriptor["endpoint"]["version"],
                    "config_identity": snapshot.identity, "ready": True,
                }
            assert read_frame(channel, deadline) == {"replay": Refusal.AUTHORITY.value}
        assert read_frame(channel, deadline) == {"http_ready": True, "adopted": False}
        child.check(deadline)
        assert capture.matches(snapshot)
        assert app.state.ready and not app.state.session_manager.is_draining
