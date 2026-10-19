"""Portable relay adoption policy; native exit integration is tested separately."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from dataclasses import replace
from functools import partial
from types import SimpleNamespace

import pytest
from credential_relay.server import CredentialRelayServer
from zdd import routing
from zdd.private_protocol import Deadline, PrivateIPCError
from zdd.private_receiver import Operation
from zdd.routing import Endpoint

from agent_bridge import predecessor_runtime
from agent_bridge.app import create_app
from agent_bridge.runtime_controls import RuntimeControls
from test_predecessor_runtime import NativeChannel
from test_roster_runtime import roster_capture
from test_runtime_controls import listener, publish, request as register_request
from test_runtime_restore import captured as captured
from test_runtime_transfer import prepare
from test_staged_relay import free_port, ping


def relay_candidate(captured, monkeypatch):
    source, origins, _capture = captured
    source.state.config.enable_credential_relay = True
    source.state.credential_relay = CredentialRelayServer(port=free_port())
    origins = replace(origins, relay_policy=True)
    capture = roster_capture((source, origins, _capture))
    _memory, pending = prepare((source, origins, capture), monkeypatch)
    parent = replace(pending._self_peer, pid=pending._self_peer.pid + 1000)
    native = NativeChannel(parent)
    object.__setattr__(pending, "_channel", native)
    object.__setattr__(pending, "_parent", parent)
    monkeypatch.setattr(predecessor_runtime, "_native_channel_type", lambda: NativeChannel)
    predecessor = Endpoint("127.0.0.1", 12345, parent.pid, "test", 1, parent.start_time)
    origins.config_root.mkdir()
    publish(origins.config_root, predecessor)
    return pending, native, predecessor


def test_relay_adoption_requires_exit_and_fresh_fenced_commit(captured, monkeypatch):
    pending, native, predecessor = relay_candidate(captured, monkeypatch)
    request = partial(register_request, peer=native.peer)
    held = []
    actual_lock = routing.routing_lock

    @contextmanager
    def observe(*args, **kwargs):
        with actual_lock(*args, **kwargs):
            held.append(True)
            try:
                yield
            finally:
                held.pop()

    monkeypatch.setattr(routing, "routing_lock", observe)

    async def scenario():
        app = create_app(restored_runtime=pending.runtime)
        app.state.uvicorn_server = SimpleNamespace(started=True, should_exit=False)
        async with app.router.lifespan_context(app):
            pending.bind_predecessor(app, predecessor)
            pending.acknowledge(app)
            with listener() as sock:
                controls = RuntimeControls(
                    app, sock, pending.runtime.origins, pending=pending,
                    predecessor=predecessor,
                )
                endpoint = replace(controls.endpoint(), generation=2)
                publish(pending.runtime.origins.config_root, endpoint, predecessor)
                controls.execute(native, request(controls, endpoint, Operation.ACTIVATE_RESOURCES))
                pending.prepare_serving(app, Deadline(2))
                controls.execute(native, request(controls, endpoint, Operation.ACTIVATE_SERVING))
                with pytest.raises(PrivateIPCError):
                    controls.execute(native, request(controls, endpoint, Operation.RESERVE_SINGLETON))
                assert pending._relay_stage is None
                native.api.signalled = True
                assert controls.execute(
                    native, request(controls, endpoint, Operation.RESERVE_SINGLETON),
                ) == {"relay_reserved": True, "relay_ready": False, "adopted": False}
                reservation = pending._relay_stage
                prepare_transport = reservation.prepare

                async def outside_fence():
                    assert not held
                    await prepare_transport()

                monkeypatch.setattr(reservation, "prepare", outside_fence)
                await pending.prepare_relay(app)
                assert await ping(pending.runtime.relay.port) == b""
                assert not app.state.credential_relay_ready
                grant = request(controls, endpoint, Operation.ACTIVATE_SINGLETON)
                # A later route invalidates this otherwise live native grant.
                newer = replace(endpoint, generation=3)
                publish(pending.runtime.origins.config_root, newer, predecessor)
                with pytest.raises(PrivateIPCError):
                    controls.execute(native, grant)
                assert not reservation.active
                activate = reservation.activate

                def inside_fence(deadline):
                    assert held
                    activate(deadline)

                monkeypatch.setattr(reservation, "activate", inside_fence)
                assert controls.execute(
                    native, request(controls, newer, Operation.ACTIVATE_SINGLETON),
                ) == {"relay_ready": True, "adopted": False}
                assert await ping(pending.runtime.relay.port) == b"pong\n\n"
                assert app.state.credential_relay_ready
                with pytest.raises(PrivateIPCError):
                    request(controls, newer, Operation.ADOPT)
                pending.verify_resources(app)
        assert reservation._closed and pending._predecessor._closed
        assert not pending.runtime.relay.running

    asyncio.run(scenario())


@pytest.mark.parametrize("failure", [
    "wrong-predecessor", "peer-closed", "deadline", "reserved-expired", "state-drift", "previous",
])
def test_relay_adoption_fresh_grant_refusals_never_admit_credentials(captured, monkeypatch, failure):
    pending, native, predecessor = relay_candidate(captured, monkeypatch)
    request = partial(register_request, peer=native.peer)

    async def scenario():
        app = create_app(restored_runtime=pending.runtime)
        app.state.uvicorn_server = SimpleNamespace(started=True, should_exit=False)
        async with app.router.lifespan_context(app):
            pending.bind_predecessor(app, predecessor)
            pending.acknowledge(app)
            with listener() as sock:
                controls = RuntimeControls(
                    app, sock, pending.runtime.origins, pending=pending,
                    predecessor=predecessor,
                )
                endpoint = replace(controls.endpoint(), generation=2)
                publish(pending.runtime.origins.config_root, endpoint, predecessor)
                controls.execute(native, request(controls, endpoint, Operation.ACTIVATE_RESOURCES))
                pending.prepare_serving(app, Deadline(2))
                controls.execute(native, request(controls, endpoint, Operation.ACTIVATE_SERVING))
                native.api.signalled = True
                controls.execute(native, request(controls, endpoint, Operation.RESERVE_SINGLETON))
                await pending.prepare_relay(app)
                stage = pending._relay_stage
                deadline = Deadline(1)
                wire = request(controls, endpoint, Operation.ACTIVATE_SINGLETON, deadline=deadline)
                if failure == "wrong-predecessor":
                    controls._predecessor = replace(predecessor, generation=99)
                elif failure == "peer-closed":
                    native.close()
                elif failure == "deadline":
                    deadline._expires = 0
                elif failure == "reserved-expired":
                    stage._deadline._expires = 0
                elif failure == "state-drift":
                    app.state.auth_token = "changed"
                else:
                    publish(pending.runtime.origins.config_root, replace(endpoint, generation=3), endpoint)
                with pytest.raises(PrivateIPCError):
                    controls.execute(native, wire)
                assert not stage.active and not pending.runtime.relay.running
                assert await ping(stage._port) == b""
                with pytest.raises(PrivateIPCError):
                    controls.execute(native, wire)
        assert stage._closed

    asyncio.run(scenario())
