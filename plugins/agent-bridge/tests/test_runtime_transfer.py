"""Private runtime transfer is bound to native children and passive app state."""

from __future__ import annotations

import asyncio
import copy
import json
import os
import secrets
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest
from zdd.private_child import OwnedChild
from zdd.private_protocol import Deadline, PeerIdentity, PrivateIPCError, Refusal, write_frame
from zdd.private_protocol import read_frame

from agent_bridge.app import create_app
from agent_bridge.runtime_transfer import (
    PendingRuntime, _ack, _self, receive_runtime, transfer_runtime,
)
from test_runtime_restore import captured as captured


class MemoryChannel:
    def __init__(self, peer: PeerIdentity) -> None:
        self.peer = peer
        self.data = bytearray()
        self.writes = 0

    def check_peer(self, deadline: Deadline) -> None:
        deadline.remaining()

    def read_exact(self, count: int, deadline: Deadline) -> bytes:
        deadline.remaining()
        assert len(self.data) >= count
        result = bytes(self.data[:count])
        del self.data[:count]
        return result

    def write_all(self, data: bytes, deadline: Deadline) -> None:
        deadline.remaining()
        self.writes += 1
        self.data.extend(data)

    def close(self) -> None:
        pass


def request(capture, channel: MemoryChannel, namespace: str) -> None:
    from agent_bridge.launch_runtime import executing_executable

    write_frame(channel, {
        "operation": "prepare-candidate", "namespace": namespace,
        "executable": str(executing_executable()), "capture": capture.capture().private_payload(),
    }, Deadline(1))
    channel.writes = 0


def test_receiver_authenticates_before_consuming_secret_frame(captured) -> None:
    _app, _origins, capture = captured
    channel = MemoryChannel(_self())
    namespace = secrets.token_hex(32)
    request(capture, channel, namespace)
    before = bytes(channel.data)
    wrong = PeerIdentity(channel.peer.pid, "0", channel.peer.principal)
    with pytest.raises(PrivateIPCError) as caught:
        receive_runtime(channel, wrong, namespace=namespace, deadline=Deadline(1))
    assert caught.value.reason is Refusal.IDENTITY
    assert bytes(channel.data) == before
    assert channel.writes == 0


@pytest.mark.parametrize("field", ["namespace", "executable", "operation", "extra"])
def test_receiver_refuses_changed_launch_binding(captured, field: str) -> None:
    from agent_bridge.launch_runtime import executing_executable

    _app, _origins, capture = captured
    channel = MemoryChannel(_self())
    namespace = secrets.token_hex(32)
    value = {
        "operation": "prepare-candidate", "namespace": namespace,
        "executable": str(executing_executable()), "capture": capture.capture().private_payload(),
    }
    value[field] = "wrong"
    write_frame(channel, value, Deadline(1))
    channel.writes = 0
    with pytest.raises(PrivateIPCError):
        receive_runtime(channel, channel.peer, namespace=namespace, deadline=Deadline(1))
    assert channel.writes == 0


def prepare(captured, monkeypatch) -> tuple[MemoryChannel, PendingRuntime]:
    _app, origins, capture = captured
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(origins.config_root))
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(origins.install_root))
    channel = MemoryChannel(_self())
    namespace = secrets.token_hex(32)
    request(capture, channel, namespace)
    pending = receive_runtime(channel, channel.peer, namespace=namespace, deadline=Deadline(2))
    assert "private-fixture" not in repr(pending)
    return channel, pending


def test_candidate_ack_is_owner_bound_single_use_and_not_active(captured, monkeypatch) -> None:
    channel, pending = prepare(captured, monkeypatch)

    async def scenario() -> None:
        app = create_app(restored_runtime=pending.runtime)
        async with app.router.lifespan_context(app):
            pending.acknowledge(app)
            assert channel.writes == 1
            value = json.loads(bytes(channel.data[5:]))
            assert value["candidate_ready"] is True and value["ready"] is False
            with pytest.raises(PrivateIPCError):
                pending.acknowledge(app)
            assert channel.writes == 1
            assert not app.state.ready and not app.state.publish_on_ready
            assert app.state.db.read_only

    asyncio.run(scenario())


@pytest.mark.parametrize("mutation", [
    "not-ready", "active", "database", "resolver", "manager", "auth",
    "environment", "namespace-root", "executable", "config",
])
def test_ack_refuses_actual_candidate_drift(captured, monkeypatch, mutation: str) -> None:
    channel, pending = prepare(captured, monkeypatch)

    async def scenario() -> None:
        app = create_app(restored_runtime=pending.runtime)
        async with app.router.lifespan_context(app):
            state = app.state
            if mutation == "not-ready":
                state.candidate_ready = False
            elif mutation == "active":
                state.ready = True
            elif mutation == "database":
                state.session_manager._db = object()
            elif mutation == "resolver":
                state.resolver.agents.clear()
            elif mutation == "manager":
                state.session_manager._idle_reap_ttl_seconds += 1
            elif mutation == "auth":
                state.auth_token = "changed"
            elif mutation == "environment":
                monkeypatch.setenv("RUNTIME_FIXTURE_SECRET", "changed")
            elif mutation == "namespace-root":
                monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(Path.cwd()))
            elif mutation == "executable":
                state.runtime_executable = Path(__file__)
            elif mutation == "config":
                state.config.port += 1
            with pytest.raises(PrivateIPCError) as caught:
                pending.acknowledge(app)
            assert caught.value.reason is Refusal.AUTHORITY
            assert channel.writes == 0

    asyncio.run(scenario())


def _child_main() -> None:
    endpoint, pid, start, principal, namespace, mode = sys.argv[1:7]
    parent = PeerIdentity(int(pid), start, principal)
    expected_predecessor = None
    if mode == "predecessor":
        from zdd.routing import Endpoint

        expected_predecessor = Endpoint(**json.loads(sys.argv[7]))
    deadline = Deadline(10)
    if os.name == "nt":
        from zdd.private_windows import connect

        channel = connect(endpoint, parent, deadline)
    else:
        from zdd.private_unix import connect

        channel = connect(Path(endpoint), parent, deadline)
    try:
        if mode == "idle":
            sys.stdin.buffer.read(1)
            return
        if mode == "control-peer":
            for _ in range(2):
                write_frame(channel, read_frame(channel, deadline), deadline)
                reply = read_frame(channel, deadline)
                write_frame(channel, {"received": type(reply) is dict}, deadline)
            sys.stdin.buffer.read(1)
            return
        pending = receive_runtime(channel, parent, namespace=namespace, deadline=deadline)
        environment = pending.runtime.environment_for_child(os.environ)
        os.environ.clear()
        os.environ.update(environment)

        async def scenario() -> None:
            app = create_app(restored_runtime=pending.runtime)
            async with app.router.lifespan_context(app):
                if mode == "serving":
                    pending.acknowledge(app)
                    await _serving_controls(app, pending, parent, channel, deadline)
                elif mode in ("valid", "continuity", "controls", "resources", "predecessor"):
                    if mode == "predecessor":
                        pending.bind_predecessor(app, expected_predecessor)
                    if mode == "continuity":
                        from agent_bridge.models import SessionStatus

                        assert app.state.session_manager.get_session("live").status is SessionStatus.RUNNING
                        turn = app.state.db.get_turn("live", 0)
                        assert turn is not None and turn["completed_at"] is None
                        assert turn["stop_reason"] is None
                        assert app.state.db.get_cursor("reader", "live") == 7
                        assert app.state.db.get_events("live")[0]["data"] == {"synthetic": True}
                        assert app.state.db._writer_thread is None
                    pending.acknowledge(app)
                    if mode == "predecessor":
                        with pytest.raises(PrivateIPCError) as caught:
                            pending.require_predecessor_exit(expected_predecessor, Deadline(1))
                        assert caught.value.reason is Refusal.AUTHORITY
                        write_frame(channel, {"live_refused": True}, deadline)
                        channel.close()
                        with pytest.raises(PrivateIPCError) as caught:
                            pending.require_predecessor_exit(expected_predecessor, Deadline(1))
                        assert caught.value.reason is Refusal.AUTHORITY
                        assert not pending._predecessor._closed
                    if mode in ("controls", "resources"):
                        import socket
                        from dataclasses import replace
                        from agent_bridge.runtime_controls import RuntimeControls
                        from zdd.private_receiver import Operation
                        from zdd import routing

                        with socket.socket() as sock:
                            sock.bind(("127.0.0.1", 0))
                            sock.listen()
                            controls = RuntimeControls(
                                app, sock, pending.runtime.origins, pending=pending,
                            )
                            endpoint = replace(controls.endpoint(), generation=1)
                            operations = (Operation.ATTEST, Operation.ATTEST_PREPARED)
                            if mode == "resources":
                                pending.runtime.origins.config_root.mkdir()
                                routing.routing_table_path(pending.runtime.origins.config_root).write_text(
                                    json.dumps({"active": endpoint.to_dict(), "previous": None}),
                                    encoding="utf-8",
                                )
                                operations = (
                                    Operation.ACTIVATE_RESOURCES, Operation.ATTEST,
                                    Operation.ATTEST_PREPARED,
                                )
                            for operation in operations:
                                token = controls.register(
                                    parent, operation, receiver=endpoint,
                                    active=endpoint if operation is Operation.ACTIVATE_RESOURCES
                                    else None, deadline=deadline,
                                )
                                write_frame(channel, {
                                    "endpoint": endpoint.to_dict(), "grant": token,
                                }, deadline)
                                wire = read_frame(channel, deadline)
                                write_frame(channel, controls.execute(channel, wire), deadline)
                                with pytest.raises(PrivateIPCError) as caught:
                                    controls.execute(channel, wire)
                                write_frame(
                                    channel, {"replay": caught.value.reason.value}, deadline,
                                )
                            assert app.state.db.read_only is (mode != "resources")
                            assert not app.state.ready
                            assert app.state.db._writer_thread is None
                            if mode == "resources":
                                app.state.db.execute_write(
                                    "UPDATE schema_version SET version=version",
                                )
                                pending.verify_resources(app)
                else:
                    value = _ack(
                        pending.runtime, namespace, app.state.runtime_executable, _self(),
                    )
                    if mode == "extra":
                        value["extra"] = "unexpected"
                    elif mode == "boolean":
                        value["candidate_ready"] = 1
                    else:
                        value[mode] = "wrong"
                    write_frame(channel, value, deadline)
                await asyncio.to_thread(sys.stdin.buffer.read, 1)
            if mode == "predecessor":
                assert pending._predecessor._closed

        asyncio.run(scenario())
    finally:
        channel.close()


async def _serving_controls(app, pending, parent, channel, deadline):
    """Exercise the actual native receiver and foreground TCP listener."""
    import httpx
    import socket
    import uvicorn
    from dataclasses import replace
    from agent_bridge.runtime_controls import RuntimeControls
    from zdd import routing
    from zdd.private_receiver import Operation

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        server = uvicorn.Server(uvicorn.Config(
            app, lifespan="off", access_log=False, log_level="critical",
        ))
        app.state.uvicorn_server = server
        task = asyncio.create_task(server.serve(sockets=[sock]))
        try:
            while not server.started:
                deadline.remaining()
                if task.done():
                    await task
                    raise AssertionError("fixture server did not start")
                await asyncio.sleep(0.005)
            controls = RuntimeControls(app, sock, pending.runtime.origins, pending=pending)
            endpoint = replace(controls.endpoint(), generation=1)
            pending.runtime.origins.config_root.mkdir()
            routing.routing_table_path(pending.runtime.origins.config_root).write_text(
                json.dumps({"active": endpoint.to_dict(), "previous": None}), encoding="utf-8",
            )
            for operation in (Operation.ACTIVATE_RESOURCES, Operation.ACTIVATE_SERVING, Operation.ATTEST):
                if operation is Operation.ACTIVATE_SERVING:
                    pending.prepare_serving(app, deadline)
                token = controls.register(
                    parent, operation, receiver=endpoint,
                    active=endpoint if operation is not Operation.ATTEST else None,
                    deadline=deadline,
                )
                write_frame(channel, {"endpoint": endpoint.to_dict(), "grant": token}, deadline)
                wire = read_frame(channel, deadline)
                write_frame(channel, controls.execute(channel, wire), deadline)
                with pytest.raises(PrivateIPCError) as caught:
                    controls.execute(channel, wire)
                write_frame(channel, {"replay": caught.value.reason.value}, deadline)
            scheme = "Bear" + "er "
            headers = {"Authorization": scheme + pending.runtime.auth_token}
            async with httpx.AsyncClient(
                base_url=endpoint.base_url, timeout=deadline.remaining(), trust_env=False,
            ) as client:
                sessions = await client.get("/api/v1/sessions", headers=headers)
                health = await client.get("/health")
                roster = await client.get("/api/v1/agents?require_complete=true", headers=headers)
                unauthorized = await client.get("/api/v1/sessions")
            assert sessions.status_code == roster.status_code == 200
            assert unauthorized.status_code == 401
            assert health.json()["ready"] is True
            assert app.state.db._writer_thread.is_alive()
            assert app.state.credential_relay is None or not app.state.credential_relay.running
            pending.verify_resources(app)
            write_frame(channel, {"http_ready": True, "adopted": False}, deadline)
        finally:
            server.should_exit = True
            await task


@contextmanager
def native_child(
    origins, tmp_path: Path, mode: str, *, predecessor_endpoint=None,
) -> Iterator[tuple[object, OwnedChild, str]]:
    from agent_bridge.launch_runtime import executing_executable

    namespace = secrets.token_hex(32)
    parent = _self()
    if os.name == "nt":
        from zdd.private_windows import WindowsListener

        endpoint = rf"\\.\pipe\private-runtime-test-{namespace}"
        listener = WindowsListener(endpoint)
    else:
        from zdd.private_unix import UnixListener

        tmp_path.chmod(0o700)
        endpoint = str(tmp_path / "transfer.sock")
        listener = UnixListener(Path(endpoint))
    environment = dict(os.environ)
    for name in origins.required_environment:
        environment.pop(name, None)
    environment["PYTHONPATH"] = os.pathsep.join(sys.path)
    executable = executing_executable()
    process = subprocess.Popen(
        [
            str(executable), "-c", "from test_runtime_transfer import _child_main; _child_main()",
            endpoint, str(parent.pid), parent.start_time, parent.principal, namespace, mode,
            *([json.dumps(predecessor_endpoint.to_dict())] if predecessor_endpoint is not None else []),
        ],
        env=environment, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    custody = channel = None
    try:
        custody = OwnedChild(process, executable, Deadline(2))
        channel = listener.accept(Deadline(5), expected=custody.peer)
        yield channel, custody, namespace
    finally:
        if channel is not None:
            channel.close()
        if custody is not None:
            custody.close()
        out, error = process.communicate(b"x", timeout=10)
        listener.close()
        assert process.returncode == 0, error.decode(errors="replace")
        assert out == b""


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="native private channels")
def test_real_candidate_pins_independent_predecessor_and_refuses_live_exit(captured, tmp_path):
    from zdd.routing import Endpoint

    app, origins, capture = captured
    parent = _self()
    endpoint = Endpoint("127.0.0.1", 12345, parent.pid, "test", 1, parent.start_time)
    origins.config_root.mkdir()
    (origins.config_root / "active.json").write_text(
        json.dumps({"active": endpoint.to_dict(), "previous": None}), encoding="utf-8",
    )
    snapshot = capture.capture()
    with native_child(
        origins, tmp_path, "predecessor", predecessor_endpoint=endpoint,
    ) as (channel, child, namespace):
        transfer_runtime(
            channel, child, capture, snapshot, namespace=namespace, deadline=Deadline(5),
        )
        assert read_frame(channel, Deadline(1)) == {"live_refused": True}
        child.check(Deadline(1))
        assert app.state.ready
        assert capture.matches(snapshot)


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="native private channels")
def test_real_owned_child_private_transfer_keeps_predecessor_and_environment(captured, tmp_path) -> None:
    predecessor, origins, capture = captured
    db = predecessor.state.db
    db.create_session("live", "live", None, ".", "local", "running", time.time())
    db.create_turn("live", 0, "synthetic open turn", time.time())
    db.append_event("live", 1, "synthetic", {"synthetic": True}, time.time())
    before_events = db.get_events("live")
    db.set_cursor("reader", "live", 7, time.time())
    before_sessions = db.list_sessions()
    before_turn = db.get_turn("live", 0)
    captured_state = capture.capture()
    before = copy.deepcopy(captured_state.private_payload())
    environment = dict(os.environ)
    with native_child(origins, tmp_path, "continuity") as (channel, child, namespace):
        transfer_runtime(
            channel, child, capture, captured_state, namespace=namespace, deadline=Deadline(5),
        )
        child.check(Deadline(1))
        assert predecessor.state.ready is True
        assert capture.capture().private_payload() == before
        assert dict(os.environ) == environment
        assert db.list_sessions() == before_sessions
        assert db.get_turn("live", 0) == before_turn
        assert db.get_cursor("reader", "live") == 7
        assert db.get_events("live") == before_events


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="native private channels")
@pytest.mark.parametrize("field", [
    "namespace", "identity", "pid", "start_time", "principal", "executable",
    "config_root", "install_root", "database", "boolean", "extra",
])
def test_real_child_receipt_does_not_accept_claimed_fields(captured, tmp_path, field: str) -> None:
    _app, origins, capture = captured
    snapshot = capture.capture()
    with native_child(origins, tmp_path, field) as (channel, child, namespace):
        with pytest.raises(PrivateIPCError) as caught:
            transfer_runtime(
                channel, child, capture, snapshot, namespace=namespace, deadline=Deadline(5),
            )
        assert caught.value.reason is Refusal.AUTHORITY
        child.check(Deadline(1))


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="native private channels")
def test_stale_capture_is_refused_before_private_write(captured, tmp_path, monkeypatch) -> None:
    app, origins, capture = captured
    snapshot = capture.capture()
    app.state.auth_token = "changed-after-capture"
    with native_child(origins, tmp_path, "idle") as (channel, child, namespace):
        def forbidden(*args, **kwargs):
            raise AssertionError("stale capture was transmitted")

        monkeypatch.setattr(channel, "write_all", forbidden)
        with pytest.raises(PrivateIPCError) as caught:
            transfer_runtime(
                channel, child, capture, snapshot, namespace=namespace, deadline=Deadline(2),
            )
        assert caught.value.reason is Refusal.AUTHORITY


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="native private channels")
def test_capture_change_during_ack_refuses(captured, tmp_path, monkeypatch) -> None:
    app, origins, capture = captured
    snapshot = capture.capture()
    with native_child(origins, tmp_path, "valid") as (channel, child, namespace):
        read = channel.read_exact

        def changed(count: int, deadline: Deadline) -> bytes:
            result = read(count, deadline)
            app.state.auth_token = "changed-during-ack"
            return result

        monkeypatch.setattr(channel, "read_exact", changed)
        with pytest.raises(PrivateIPCError) as caught:
            transfer_runtime(
                channel, child, capture, snapshot, namespace=namespace, deadline=Deadline(3),
            )
        assert caught.value.reason is Refusal.AUTHORITY


@pytest.mark.skipif(sys.platform not in ("linux", "win32"), reason="native private channels")
def test_unacknowledged_private_transfer_respects_shared_deadline(captured, tmp_path) -> None:
    _app, origins, capture = captured
    snapshot = capture.capture()
    with native_child(origins, tmp_path, "idle") as (channel, child, namespace):
        started = time.monotonic()
        with pytest.raises(PrivateIPCError) as caught:
            transfer_runtime(
                channel, child, capture, snapshot, namespace=namespace, deadline=Deadline(0.1),
            )
        assert caught.value.reason is Refusal.DEADLINE
        assert time.monotonic() - started < 0.6
        child.check(Deadline(1))
