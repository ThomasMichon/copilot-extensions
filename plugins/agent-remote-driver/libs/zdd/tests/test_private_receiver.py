"""Receiver controls consume owner grants under native routing authority."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import replace

import pytest

from zdd import diagnostics, routing
from zdd.private_protocol import Deadline, PeerIdentity, PrivateIPCError, Refusal
from zdd.private_receiver import Operation, PrivateReceiver
from zdd.routing import Endpoint


class PeerChannel:
    def __init__(self, peer: PeerIdentity) -> None:
        self.peer = peer
        self.alive = True
        self.checks = 0

    def check_peer(self, deadline: Deadline) -> None:
        deadline.remaining()
        self.checks += 1
        if not self.alive:
            raise PrivateIPCError(Refusal.IDENTITY)


@pytest.fixture
def owner(tmp_path, monkeypatch):
    start = diagnostics.process_start_time(os.getpid())
    assert start
    endpoint = Endpoint("127.0.0.1", 12345, os.getpid(), "1", 1, start)
    successor = Endpoint("127.0.0.1", 12346, os.getpid() + 1, "2", 2, "123")
    actual_start = diagnostics.process_start_time
    monkeypatch.setattr(
        diagnostics, "process_start_time",
        lambda pid: successor.process_start_time if pid == successor.pid else actual_start(pid),
    )
    state = {"identity": "nonsecret-config", "endpoint": endpoint, "calls": []}
    peer = PeerIdentity(os.getpid(), start, "fixture-principal")
    channel = PeerChannel(peer)

    def action():
        state["calls"].append("mutation")
        return {"ok": True}

    receiver = PrivateReceiver(
        tmp_path, config_identity=lambda: state["identity"],
        endpoint=lambda: state["endpoint"],
        callbacks={op: action for op in Operation},
    )
    receiver._callbacks[Operation.ATTEST] = lambda: {
        "pid": endpoint.pid, "process_start_time": endpoint.process_start_time,
        "version": endpoint.version, "config_identity": state["identity"], "ready": True,
    }

    def publish(active=endpoint, previous=None):
        routing.routing_table_path(tmp_path).write_text(json.dumps({
            "active": active.to_dict(),
            "previous": previous.to_dict() if previous else None,
        }), encoding="utf-8")

    def request(op=Operation.CAPTURE, active=endpoint, deadline=None):
        token = receiver.register(
            peer, op, receiver=state["endpoint"], active=active,
            config_identity=state["identity"], deadline=deadline or Deadline(1),
        )
        return {"grant": token, "operation": op.value}

    publish()
    return receiver, state, channel, endpoint, successor, publish, request


def test_capture_and_single_use(owner):
    receiver, state, channel, _old, _new, _publish, request = owner
    wire = request()
    assert receiver.execute(channel, wire) == {"ok": True}
    assert channel.checks == 2
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        receiver.execute(channel, wire)
    assert state["calls"] == ["mutation"]


@pytest.mark.parametrize("failed", [False, True])
def test_callback_deadline_is_original_and_cleared_after_execution(owner, failed):
    receiver, _state, channel, endpoint, _new, _publish, request = owner
    deadline = Deadline(1)
    wire = request(Operation.ACTIVATE_RESOURCES, active=endpoint, deadline=deadline)
    with pytest.raises(PrivateIPCError):
        receiver.execution_deadline()

    def action():
        assert receiver.execution_deadline() is deadline
        if failed:
            raise ValueError("synthetic failure")
        return {"resources_active": True}

    receiver._callbacks[Operation.ACTIVATE_RESOURCES] = action
    if failed:
        with pytest.raises(PrivateIPCError):
            receiver.execute(channel, wire)
    else:
        assert receiver.execute(channel, wire) == {"resources_active": True}
    with pytest.raises(PrivateIPCError):
        receiver.execution_deadline()


@pytest.mark.parametrize("change", ["pid", "start", "generation", "port", "version", "bind"])
def test_exact_active_publication_required(owner, change):
    receiver, state, channel, old, _new, publish, request = owner
    wire = request()
    replacement = {
        "pid": old.pid + 2, "start": "99", "generation": 3,
        "port": 12347, "version": "3", "bind": "localhost",
    }
    field = "process_start_time" if change == "start" else change
    publish(replace(old, **{field: replacement[change]}))
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        receiver.execute(channel, wire)
    assert not state["calls"]


@pytest.mark.parametrize("change", ["peer", "dead-peer", "config", "self-endpoint", "self-start"])
def test_receiver_rechecks_local_and_native_state(owner, change, monkeypatch):
    receiver, state, channel, old, _new, _publish, request = owner
    wire = request()
    if change == "peer":
        channel.peer = replace(channel.peer, start_time="99")
    elif change == "dead-peer":
        channel.alive = False
    elif change == "config":
        state["identity"] = "changed"
    elif change == "self-endpoint":
        state["endpoint"] = replace(old, port=12347)
    else:
        monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: "99")
    with pytest.raises(PrivateIPCError):
        receiver.execute(channel, wire)
    assert not state["calls"]


@pytest.mark.parametrize("op", [Operation.DRAIN, Operation.RETIRE])
def test_predecessor_control_requires_distinct_live_successor(owner, op, monkeypatch):
    receiver, state, channel, old, new, publish, request = owner
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        receiver.execute(channel, request(op))
    publish(new, old)
    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: old.process_start_time)
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        receiver.execute(channel, request(op, active=new))
    assert not state["calls"]


@pytest.mark.parametrize("op", [Operation.DRAIN, Operation.RETIRE])
def test_predecessor_control_under_exact_successor(owner, op):
    receiver, state, channel, old, new, publish, request = owner
    publish(new, old)
    assert receiver.execute(channel, request(op, active=new)) == {"ok": True}
    assert state["calls"] == ["mutation"]


@pytest.mark.parametrize("op", [
    Operation.CAPTURE, Operation.ADOPT, Operation.UNDRAIN, Operation.ACTIVATE_RESOURCES,
    Operation.RESERVE_SINGLETON, Operation.ACTIVATE_SINGLETON,
])
def test_capture_and_adopt_require_self_active(owner, op):
    receiver, state, channel, old, new, publish, request = owner
    publish(new, old)
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        receiver.execute(channel, request(op, active=new))
    assert not state["calls"]


@pytest.mark.parametrize("operation", [
    Operation.CAPTURE, Operation.RESERVE_SINGLETON, Operation.ACTIVATE_SINGLETON,
])
@pytest.mark.parametrize("field", ["generation", "port"])
def test_numeric_publication_coercion_refuses_before_any_mutation(owner, operation, field):
    receiver, state, channel, old, _new, publish, request = owner
    old = replace(old, **{field: 1})
    state["endpoint"] = old
    publish(old)
    wire = request(operation, active=old)
    table = routing.read_table(receiver._root, strict=True)
    table["active"][field] = True
    routing.routing_table_path(receiver._root).write_text(json.dumps(table), encoding="utf-8")
    with pytest.raises(PrivateIPCError):
        receiver.execute(channel, wire)
    assert not state["calls"]
    with pytest.raises(PrivateIPCError):
        receiver.execute(channel, wire)


@pytest.mark.parametrize("operation", [Operation.ATTEST, Operation.ATTEST_PREPARED])
def test_attestation_never_acquires_routing_lock(owner, monkeypatch, operation):
    receiver, state, channel, _old, _new, _publish, request = owner
    if operation is Operation.ATTEST_PREPARED:
        def prepared():
            result = receiver._callbacks[Operation.ATTEST]()
            result["prepared"] = result.pop("ready")
            return result
        receiver._callbacks[operation] = prepared

    def forbidden(*args, **kwargs):
        pytest.fail("attestation would deadlock a final cutover's routing fence")

    monkeypatch.setattr(routing, "routing_lock", forbidden)
    attested = receiver.execute(channel, request(operation, active=None))
    assert attested["ready" if operation is Operation.ATTEST else "prepared"] is True
    assert not state["calls"]


def test_callback_runs_inside_native_fence(owner, monkeypatch):
    receiver, _state, channel, _old, _new, _publish, request = owner
    locked = []
    actual = routing.routing_lock
    from contextlib import contextmanager

    @contextmanager
    def observe(*args, **kwargs):
        with actual(*args, **kwargs):
            locked.append(True)
            try:
                yield
            finally:
                locked.pop()

    monkeypatch.setattr(routing, "routing_lock", observe)
    receiver._callbacks[Operation.CAPTURE] = lambda: {"locked": bool(locked)}
    assert receiver.execute(channel, request()) == {"locked": True}


def test_thread_mismatch_never_schedules_mutation(owner):
    receiver, state, channel, _old, _new, _publish, request = owner
    wire = request()
    failures = []

    def foreign():
        try:
            receiver.execute(channel, wire)
        except PrivateIPCError as exc:
            failures.append(exc.reason)

    thread = threading.Thread(target=foreign)
    thread.start()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert failures == [Refusal.AUTHORITY]
    assert not state["calls"]
    assert receiver.execute(channel, wire) == {"ok": True}


def test_cancelled_deadline_consumes_grant(owner):
    receiver, state, channel, _old, _new, _publish, request = owner
    cancelled = threading.Event()
    wire = request(deadline=Deadline(1, cancelled=cancelled))
    cancelled.set()
    with pytest.raises(PrivateIPCError, match=Refusal.CANCELLED.value):
        receiver.execute(channel, wire)
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        receiver.execute(channel, wire)
    assert not state["calls"]


def test_capped_registrations_and_revocation(owner):
    receiver, _state, _channel, _old, _new, _publish, request = owner
    receiver._capacity = 1
    wire = request()
    with pytest.raises(PrivateIPCError, match=Refusal.CAPACITY.value):
        request()
    receiver.revoke(wire["grant"])
    assert request()


def test_wire_cannot_register_peer_or_reset_budget(owner):
    receiver, state, channel, _old, _new, _publish, request = owner
    wire = request()
    wire["timeout"] = 30
    with pytest.raises(PrivateIPCError, match=Refusal.PROTOCOL.value):
        receiver.execute(channel, wire)
    del wire["timeout"]
    wire["operation"] = Operation.RETIRE.value
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        receiver.execute(channel, wire)
    wire["operation"] = Operation.CAPTURE.value
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        receiver.execute(channel, wire)
    assert not state["calls"]


def test_sensitive_callback_errors_are_redacted(owner):
    receiver, _state, channel, _old, _new, _publish, request = owner

    def fail():
        raise RuntimeError("fixture-secret")

    receiver._callbacks[Operation.CAPTURE] = fail
    with pytest.raises(PrivateIPCError) as caught:
        receiver.execute(channel, request())
    assert str(caught.value) == Refusal.AUTHORITY.value
    assert caught.value.__cause__ is None
    assert "fixture-secret" not in repr(caught.value)


def test_async_callback_is_refused_not_scheduled(owner):
    receiver, state, channel, _old, _new, _publish, request = owner

    async def delayed():
        state["calls"].append("late")
        return {"ok": True}

    receiver._callbacks[Operation.CAPTURE] = delayed
    with pytest.raises(PrivateIPCError, match=Refusal.AUTHORITY.value):
        receiver.execute(channel, request())
    assert not state["calls"]


def test_rollback_generation_comes_from_native_route(owner):
    receiver, state, channel, old, _new, publish, _request = owner
    restored = replace(old, generation=3)
    publish(restored)
    token = receiver.register(
        channel.peer, Operation.UNDRAIN, receiver=restored, active=restored,
        config_identity=state["identity"], deadline=Deadline(1),
    )
    assert receiver.execute(channel, {
        "grant": token, "operation": Operation.UNDRAIN.value,
    }) == {"ok": True}


def test_known_async_callback_refused_at_construction(owner, tmp_path):
    _receiver, state, _channel, _old, _new, _publish, _request = owner

    async def invalid():
        state["calls"].append("unexpected")
        return {"ok": True}

    with pytest.raises(ValueError):
        PrivateReceiver(
            tmp_path, config_identity=lambda: state["identity"],
            endpoint=lambda: state["endpoint"], callbacks={Operation.DRAIN: invalid},
        )
    assert not state["calls"]


@pytest.mark.parametrize("operation", [Operation.ATTEST, Operation.ATTEST_PREPARED])
def test_attestation_cannot_return_arbitrary_private_payload(owner, operation):
    receiver, state, channel, _old, _new, _publish, request = owner
    receiver._callbacks[operation] = lambda: {"secret": "fixture-sensitive-value"}
    with pytest.raises(PrivateIPCError, match=Refusal.PROTOCOL.value):
        receiver.execute(channel, request(operation, active=None))
    assert not state["calls"]


@pytest.mark.parametrize("value", [1, "true", None])
def test_preparation_requires_exact_boolean_not_readiness(owner, value):
    receiver, state, channel, _old, _new, _publish, request = owner
    def invalid():
        result = receiver._callbacks[Operation.ATTEST]()
        result["prepared"] = value
        del result["ready"]
        return result
    receiver._callbacks[Operation.ATTEST_PREPARED] = invalid
    with pytest.raises(PrivateIPCError, match=Refusal.PROTOCOL.value):
        receiver.execute(channel, request(Operation.ATTEST_PREPARED, active=None))
    assert not state["calls"]
