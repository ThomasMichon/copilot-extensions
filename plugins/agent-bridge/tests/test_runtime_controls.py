"""Application mutations require exact, single-use private receiver grants."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import sys
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Iterator

import pytest
from fastapi import FastAPI
from zdd import diagnostics, routing
from zdd.private_protocol import Deadline, PeerIdentity, PrivateIPCError, Refusal, read_frame, write_frame
from zdd.private_receiver import Operation

from agent_bridge.app import create_app
from agent_bridge.effective_runtime import RuntimeOrigins, ServingRuntimeCapture
from agent_bridge.runtime_controls import RuntimeControls
from agent_bridge.runtime_transfer import _self, transfer_runtime
from test_runtime_restore import captured as captured
from test_runtime_transfer import MemoryChannel, native_child, prepare

CapturedFixture = tuple[FastAPI, RuntimeOrigins, ServingRuntimeCapture]

@contextmanager
def listener() -> Iterator[socket.socket]:
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        yield sock
    finally:
        sock.close()


def configure(
    captured: CapturedFixture, monkeypatch: pytest.MonkeyPatch,
) -> tuple[FastAPI, RuntimeOrigins]:
    app, origins, _capture = captured
    origins.config_root.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(origins.config_root))
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(origins.install_root))
    app.state.runtime_origins = origins
    app.state.uvicorn_server = SimpleNamespace(started=True, should_exit=False)
    return app, origins


def publish(root: Path, active: routing.Endpoint, previous: routing.Endpoint | None = None) -> None:
    routing.routing_table_path(root).write_text(json.dumps({
        "active": active.to_dict(),
        "previous": previous.to_dict() if previous else None,
    }), encoding="utf-8")


def request(
    controls: RuntimeControls, endpoint: routing.Endpoint, op: Operation, *,
    active: routing.Endpoint | None = None, deadline: Deadline | None = None,
    peer: PeerIdentity | None = None,
) -> dict[str, object]:
    token = controls.register(
        peer or _self(), op, receiver=endpoint,
        active=(
            endpoint if active is None and op not in (Operation.ATTEST, Operation.ATTEST_PREPARED)
            else active
        ),
        deadline=deadline or Deadline(2),
    )
    return {"grant": token, "operation": op.value}


def test_private_capture_attest_and_replay(
    captured: CapturedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, origins = configure(captured, monkeypatch)

    async def scenario() -> None:
        with listener() as sock:
            controls = RuntimeControls(app, sock, origins)
            endpoint = replace(controls.endpoint(), generation=1)
            publish(origins.config_root, endpoint)
            channel = MemoryChannel(_self())
            wire = request(controls, endpoint, Operation.CAPTURE)
            reply = controls.execute(channel, wire)
            assert reply["runtime"]["auth_token"] == app.state.auth_token
            assert reply["identity"] == controls.identity()
            with pytest.raises(PrivateIPCError):
                controls.execute(channel, wire)
            assert controls.execute(
                channel, request(controls, endpoint, Operation.ATTEST),
            ) == {
                "pid": os.getpid(), "process_start_time": endpoint.process_start_time,
                "version": endpoint.version, "config_identity": reply["identity"],
                "ready": True,
            }
            assert not app.state.session_manager.is_draining
            assert not app.state.uvicorn_server.should_exit
            with pytest.raises(PrivateIPCError):
                request(controls, endpoint, Operation.ADOPT)

    asyncio.run(scenario())


@pytest.mark.parametrize("drift", [
    "route", "config", "auth", "environment", "manager", "resolver",
    "config-root", "install-root", "listener", "server", "readiness", "database",
])
def test_controls_refuse_drift_before_mutation(
    captured: CapturedFixture, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    app, origins = configure(captured, monkeypatch)

    async def scenario() -> None:
        with listener() as sock:
            controls = RuntimeControls(app, sock, origins)
            endpoint = replace(controls.endpoint(), generation=1)
            publish(origins.config_root, endpoint)
            wire = request(controls, endpoint, Operation.UNDRAIN)
            if drift == "route":
                publish(origins.config_root, replace(endpoint, generation=2))
            elif drift == "config":
                app.state.config.retention.max_age_hours += 1
            elif drift == "auth":
                app.state.auth_token = "changed"
            elif drift == "environment":
                monkeypatch.setenv("RUNTIME_FIXTURE_SECRET", "changed")
            elif drift == "manager":
                app.state.session_manager._idle_reap_ttl_seconds += 1
            elif drift == "resolver":
                app.state.resolver.agents.clear()
            elif drift == "config-root":
                monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(origins.install_root))
            elif drift == "install-root":
                monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(origins.config_root))
            elif drift == "listener":
                sock.close()
            elif drift == "server":
                app.state.uvicorn_server = SimpleNamespace(started=True, should_exit=False)
            elif drift == "readiness":
                app.state.ready = False
            else:
                app.state.session_manager._db = object()
            with pytest.raises(PrivateIPCError):
                controls.execute(MemoryChannel(_self()), wire)
            assert not app.state.session_manager.is_draining
            assert not app.state.uvicorn_server.should_exit

    asyncio.run(scenario())


def test_fenced_private_drain_rollback_and_retire_never_schedule(
    captured: CapturedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, origins = configure(captured, monkeypatch)
    held = []
    native_lock = routing.routing_lock

    @contextmanager
    def observe(*args: object, **kwargs: object) -> Iterator[None]:
        with native_lock(*args, **kwargs):
            held.append(True)
            try:
                yield
            finally:
                held.pop()

    monkeypatch.setattr(routing, "routing_lock", observe)

    async def scenario() -> None:
        with listener() as sock:
            controls = RuntimeControls(app, sock, origins)
            old = replace(controls.endpoint(), generation=1)
            # The distinct successor uses the real parent process birth here;
            # full native handoff acceptance is a separate real-child matrix.
            parent_start = diagnostics.process_start_time(os.getppid())
            assert parent_start
            new = replace(old, pid=os.getppid(), process_start_time=parent_start, generation=2)
            publish(origins.config_root, new, old)
            manager = app.state.session_manager
            actual = manager.set_private_draining

            def guarded(value: bool) -> None:
                assert held
                actual(value)

            monkeypatch.setattr(manager, "set_private_draining", guarded)
            monkeypatch.setattr(manager, "_arm_drain_watchdog", lambda: pytest.fail("scheduled"))
            monkeypatch.setattr(manager, "_cancel_drain_watchdog", lambda: pytest.fail("cancelled"))
            channel = MemoryChannel(_self())
            with pytest.raises(PrivateIPCError):
                controls.execute(channel, request(controls, old, Operation.RETIRE, active=new))
            assert not app.state.uvicorn_server.should_exit
            assert controls.execute(
                channel, request(controls, old, Operation.DRAIN, active=new),
            ) == {"draining": True}
            assert manager.is_draining and manager._drain_private
            assert manager._drain_watchdog is None
            assert manager.drain_status()["auto_release_at"] is None
            with pytest.raises(RuntimeError, match="receiver-bound"):
                manager.set_draining(False)
            assert manager.is_draining
            assert controls.execute(
                channel, request(controls, old, Operation.ATTEST),
            )["ready"] is False
            # A later publication must invalidate the earlier rollback grant.
            publish(origins.config_root, old)
            rollback = request(controls, old, Operation.UNDRAIN)
            publish(origins.config_root, new, old)
            with pytest.raises(PrivateIPCError):
                controls.execute(channel, rollback)
            assert manager.is_draining
            publish(origins.config_root, old)
            assert controls.execute(
                channel, request(controls, old, Operation.UNDRAIN),
            ) == {"draining": False}
            assert not manager._drain_private
            publish(origins.config_root, new, old)
            controls.execute(channel, request(controls, old, Operation.DRAIN, active=new))
            assert controls.execute(
                channel, request(controls, old, Operation.RETIRE, active=new),
            ) == {"shutting_down": True}
            assert app.state.uvicorn_server.should_exit
            # This is graceful intent, never a claim of confirmed native exit.
            assert diagnostics.process_start_time(os.getpid()) == old.process_start_time

    asyncio.run(scenario())


def test_private_drain_refuses_ordinary_watchdog(captured: CapturedFixture) -> None:
    manager = captured[0].state.session_manager

    async def scenario() -> None:
        manager.set_draining(True)
        watchdog = manager._drain_watchdog
        assert watchdog is not None
        try:
            with pytest.raises(RuntimeError, match="ordinary drain"):
                manager.set_private_draining(True)
            assert manager._drain_watchdog is watchdog
            assert manager.is_draining and not manager._drain_private
        finally:
            manager.set_draining(False)
            await asyncio.gather(watchdog, return_exceptions=True)

    asyncio.run(scenario())


def test_candidate_attestation_is_readonly_route_lock_free(
    captured: CapturedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel, pending = prepare(captured, monkeypatch)

    async def scenario() -> None:
        app = create_app(restored_runtime=pending.runtime)
        async with app.router.lifespan_context(app):
            with listener() as sock:
                with pytest.raises(PrivateIPCError):
                    RuntimeControls(app, sock, pending.runtime.origins, pending=pending)
                pending.acknowledge(app)
                controls = RuntimeControls(app, sock, pending.runtime.origins, pending=pending)
                endpoint = replace(controls.endpoint(), generation=1)

                def forbidden(*args: object, **kwargs: object) -> None:
                    pytest.fail("passive attestation acquired routing lock")

                monkeypatch.setattr(routing, "routing_lock", forbidden)
                assert controls.execute(
                    channel, request(controls, endpoint, Operation.ATTEST),
                )["ready"] is False
                assert controls.execute(
                    channel, request(controls, endpoint, Operation.ATTEST_PREPARED),
                ) == {
                    "pid": endpoint.pid, "process_start_time": endpoint.process_start_time,
                    "version": endpoint.version, "config_identity": controls.identity(),
                    "prepared": True,
                }
                for operation in set(Operation) - {
                    Operation.ATTEST, Operation.ATTEST_PREPARED, Operation.ACTIVATE_RESOURCES,
                }:
                    with pytest.raises(PrivateIPCError):
                        request(controls, endpoint, operation)
                assert app.state.db.read_only and not app.state.ready
                wire = request(controls, endpoint, Operation.ATTEST)
                app.state.session_manager._idle_reap_ttl_seconds += 1
                with pytest.raises(PrivateIPCError):
                    controls.execute(channel, wire)

    asyncio.run(scenario())


def test_candidate_writable_resource_adoption_is_fenced_not_readiness(
    captured: CapturedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_bridge.db import Database

    channel, pending = prepare(captured, monkeypatch)
    pending.runtime.origins.config_root.mkdir()
    held = []
    native_lock = routing.routing_lock

    @contextmanager
    def observe(*args: object, **kwargs: object) -> Iterator[None]:
        with native_lock(*args, **kwargs):
            held.append(True)
            try:
                yield
            finally:
                held.pop()

    monkeypatch.setattr(routing, "routing_lock", observe)

    async def scenario() -> None:
        app = create_app(restored_runtime=pending.runtime)
        async with app.router.lifespan_context(app):
            readonly = app.state.db
            pending.acknowledge(app)
            with listener() as sock:
                controls = RuntimeControls(app, sock, pending.runtime.origins, pending=pending)
                endpoint = replace(controls.endpoint(), generation=1)
                publish(pending.runtime.origins.config_root, endpoint)
                wire = request(controls, endpoint, Operation.ACTIVATE_RESOURCES)
                actual = pending.activate_resources

                def guarded(self, target: FastAPI, deadline: Deadline) -> None:
                    assert held
                    actual(target, deadline)

                monkeypatch.setattr(type(pending), "activate_resources", guarded)
                monkeypatch.setattr(Database, "_init_schema", lambda *a: pytest.fail("migration"))
                monkeypatch.setattr(Database, "start_writer", lambda *a: pytest.fail("writer"))
                assert controls.execute(channel, wire) == {
                    "resources_active": True, "ready": False,
                }
                writable = app.state.db
                assert writable is not readonly
                assert not writable.read_only
                assert app.state.session_manager._db is writable
                assert readonly.read_only and writable._writer_thread is None
                assert controls.execute(
                    channel, request(controls, endpoint, Operation.ATTEST),
                )["ready"] is False
                assert controls.execute(
                    channel, request(controls, endpoint, Operation.ATTEST_PREPARED),
                )["prepared"] is True
                assert not app.state.ready and app.state.passive
                assert pending.runtime.relay is None or not pending.runtime.relay.running
                assert {r.path for r in app.routes} == {"/health"}
                # A second, freshly registered activation also refuses.
                for retry in (wire, request(controls, endpoint, Operation.ACTIVATE_RESOURCES)):
                    with pytest.raises(PrivateIPCError):
                        controls.execute(channel, retry)
                with pytest.raises(PrivateIPCError):
                    request(controls, endpoint, Operation.ADOPT)
                with pytest.raises(PrivateIPCError):
                    pending.verify_candidate(app)
                pending.verify_resources(app)
                assert writable._local.conn.execute("PRAGMA query_only").fetchone()[0] == 0
                writable.execute_write(
                    "UPDATE schema_version SET version=version",
                )
        assert readonly._local.conn is None
        assert writable._local.conn is None

    asyncio.run(scenario())


@pytest.mark.parametrize("drift", ["route", "previous", "config", "database", "schema", "deleted"])
def test_candidate_resource_adoption_refuses_without_changing_owner(
    captured: CapturedFixture, monkeypatch: pytest.MonkeyPatch, drift: str,
) -> None:
    import sqlite3

    channel, pending = prepare(captured, monkeypatch)
    pending.runtime.origins.config_root.mkdir()

    async def scenario() -> None:
        app = create_app(restored_runtime=pending.runtime)
        async with app.router.lifespan_context(app):
            readonly = app.state.db
            pending.acknowledge(app)
            with listener() as sock:
                controls = RuntimeControls(app, sock, pending.runtime.origins, pending=pending)
                endpoint = replace(controls.endpoint(), generation=1)
                publish(pending.runtime.origins.config_root, endpoint)
                wire = request(controls, endpoint, Operation.ACTIVATE_RESOURCES)
                if drift == "route":
                    publish(pending.runtime.origins.config_root, replace(endpoint, generation=2))
                elif drift == "previous":
                    active = replace(endpoint, generation=2)
                    publish(pending.runtime.origins.config_root, active, endpoint)
                    wire = request(controls, endpoint, Operation.ACTIVATE_RESOURCES, active=active)
                elif drift == "config":
                    app.state.config.port += 1
                elif drift == "database":
                    app.state.session_manager._db = object()
                elif drift == "schema":
                    with sqlite3.connect(readonly.db_path) as conn:
                        conn.execute("UPDATE schema_version SET version=version+1")
                else:
                    captured[0].state.db.close()
                    readonly._local.conn.close()
                    readonly._local.conn = None
                    readonly.db_path.rename(readonly.db_path.with_suffix(".removed"))
                with pytest.raises(PrivateIPCError):
                    controls.execute(channel, wire)
                assert app.state.db is readonly and readonly.read_only
                assert readonly._writer_thread is None
                assert not app.state.ready

    asyncio.run(scenario())


@pytest.mark.parametrize("change", ["expired", "cancelled", "config"])
def test_resource_open_rechecks_original_deadline_and_configuration_before_swap(
    captured: CapturedFixture, monkeypatch: pytest.MonkeyPatch, change: str,
) -> None:
    import threading
    import agent_bridge.runtime_transfer as transfer

    channel, pending = prepare(captured, monkeypatch)
    pending.runtime.origins.config_root.mkdir()
    cancelled = threading.Event()
    deadline = Deadline(2, cancelled=cancelled)
    opened = []
    actual = transfer.open_existing_writable

    async def scenario() -> None:
        app = create_app(restored_runtime=pending.runtime)
        async with app.router.lifespan_context(app):
            readonly = app.state.db
            pending.acknowledge(app)
            with listener() as sock:
                controls = RuntimeControls(app, sock, pending.runtime.origins, pending=pending)
                endpoint = replace(controls.endpoint(), generation=1)
                publish(pending.runtime.origins.config_root, endpoint)
                wire = request(
                    controls, endpoint, Operation.ACTIVATE_RESOURCES, deadline=deadline,
                )

                def changed(source):
                    db = actual(source)
                    opened.append(db)
                    if change == "expired":
                        deadline._expires = 0
                    elif change == "cancelled":
                        cancelled.set()
                    else:
                        app.state.config.port += 1
                    return db

                monkeypatch.setattr(transfer, "open_existing_writable", changed)
                with pytest.raises(PrivateIPCError):
                    controls.execute(channel, wire)
                assert app.state.db is readonly
                assert app.state.session_manager._db is readonly
                assert readonly.read_only and not app.state.ready
                assert len(opened) == 1 and opened[0]._local.conn is None

    asyncio.run(scenario())


def test_uncertain_receipt_does_not_enable_candidate_controls(
    captured: CapturedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel, pending = prepare(captured, monkeypatch)

    def failed_write(*args: object, **kwargs: object) -> None:
        raise PrivateIPCError(Refusal.DEADLINE)

    monkeypatch.setattr(channel, "write_all", failed_write)

    async def scenario() -> None:
        app = create_app(restored_runtime=pending.runtime)
        async with app.router.lifespan_context(app):
            with pytest.raises(PrivateIPCError):
                pending.acknowledge(app)
            assert not pending.prepared
            with listener() as sock, pytest.raises(PrivateIPCError):
                RuntimeControls(app, sock, pending.runtime.origins, pending=pending)
            with pytest.raises(PrivateIPCError):
                pending.acknowledge(app)

    asyncio.run(scenario())


def test_unbound_or_wrong_owner_controls_refuse(
    captured: CapturedFixture, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, origins = configure(captured, monkeypatch)
    with listener() as sock:
        with pytest.raises(PrivateIPCError):
            RuntimeControls(app, sock, origins)
        async def create() -> RuntimeControls:
            return RuntimeControls(app, sock, origins)
        controls = asyncio.run(create())
        async def other_loop() -> None:
            with pytest.raises(PrivateIPCError):
                controls.identity()
        asyncio.run(other_loop())


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="native private channels")
def test_real_candidate_controls_bind_receipt_native_peer_and_readiness(
    captured: CapturedFixture, tmp_path: Path,
) -> None:
    app, origins, capture = captured
    before = capture.capture()
    with native_child(origins, tmp_path, "controls") as (channel, child, namespace):
        deadline = Deadline(5)
        transfer_runtime(channel, child, capture, before, namespace=namespace, deadline=deadline)
        for operation, flag, value in (
            (Operation.ATTEST, "ready", False), (Operation.ATTEST_PREPARED, "prepared", True),
        ):
            descriptor = read_frame(channel, deadline)
            write_frame(channel, {
                "grant": descriptor["grant"], "operation": operation.value,
            }, deadline)
            reply = read_frame(channel, deadline)
            assert reply == {
                "pid": child.peer.pid, "process_start_time": child.peer.start_time,
                "version": descriptor["endpoint"]["version"], "config_identity": before.identity,
                flag: value,
            }
            assert read_frame(channel, deadline) == {"replay": Refusal.AUTHORITY.value}
        child.check(deadline)
        assert app.state.ready and not app.state.session_manager.is_draining
        assert capture.matches(before)


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="native private channels")
def test_real_private_peer_executes_serving_capture_and_attestation(
    captured: CapturedFixture, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, origins = configure(captured, monkeypatch)

    async def scenario() -> None:
        with listener() as sock, native_child(origins, tmp_path, "control-peer") as (
            channel, child, _namespace,
        ):
            controls = RuntimeControls(app, sock, origins)
            endpoint = replace(controls.endpoint(), generation=1)
            publish(origins.config_root, endpoint)
            deadline = Deadline(5)
            for operation in (Operation.CAPTURE, Operation.ATTEST):
                token = controls.register(
                    child.peer, operation, receiver=endpoint, active=endpoint, deadline=deadline,
                )
                write_frame(channel, {"grant": token, "operation": operation.value}, deadline)
                reply = controls.execute(channel, read_frame(channel, deadline))
                write_frame(channel, reply, deadline)
                assert read_frame(channel, deadline) == {"received": True}
                child.check(deadline)
            assert not app.state.session_manager.is_draining
            assert not app.state.uvicorn_server.should_exit

    asyncio.run(scenario())


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="native private channels")
def test_real_candidate_adopts_writable_owner_only_through_native_grant(
    captured: CapturedFixture, tmp_path: Path,
) -> None:
    app, origins, capture = captured
    before = capture.capture()
    with native_child(origins, tmp_path, "resources") as (channel, child, namespace):
        deadline = Deadline(5)
        transfer_runtime(channel, child, capture, before, namespace=namespace, deadline=deadline)
        for operation in (
            Operation.ACTIVATE_RESOURCES, Operation.ATTEST, Operation.ATTEST_PREPARED,
        ):
            descriptor = read_frame(channel, deadline)
            write_frame(channel, {
                "grant": descriptor["grant"], "operation": operation.value,
            }, deadline)
            reply = read_frame(channel, deadline)
            if operation is Operation.ACTIVATE_RESOURCES:
                assert reply == {"resources_active": True, "ready": False}
            else:
                flag = "ready" if operation is Operation.ATTEST else "prepared"
                assert reply == {
                    "pid": child.peer.pid, "process_start_time": child.peer.start_time,
                    "version": descriptor["endpoint"]["version"], "config_identity": before.identity,
                    flag: flag == "prepared",
                }
            assert read_frame(channel, deadline) == {"replay": Refusal.AUTHORITY.value}
        child.check(deadline)
        assert app.state.ready and not app.state.session_manager.is_draining
        assert capture.matches(before)
