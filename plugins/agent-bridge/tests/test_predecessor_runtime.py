"""Exact-publication binding policy; native transport proof lives separately."""

from __future__ import annotations

import asyncio
import json
import os
import threading
from dataclasses import replace
from pathlib import Path

import pytest
from zdd.private_peer import PeerProcess
from zdd.private_protocol import Deadline, PeerIdentity, PrivateIPCError, Refusal
from zdd.routing import Endpoint

from agent_bridge import predecessor_runtime as module
from agent_bridge import runtime_transfer as transfer
from agent_bridge.app import create_app
from test_runtime_transfer import MemoryChannel, prepare
from test_runtime_restore import captured as captured


class NativeAPI:
    def __init__(self) -> None:
        self.k = self
        self.signalled = False
        self.closed = 0

    def WaitForSingleObject(self, reference: int, timeout: int) -> int:
        assert reference == 123 and timeout == 0
        return 0 if self.signalled else 258

    def close(self, reference: int) -> None:
        assert reference == 123
        self.closed += 1


class NativeChannel(MemoryChannel):
    def __init__(self, peer: PeerIdentity) -> None:
        super().__init__(peer)
        self.api = NativeAPI()
        self.retained = 0
        self.closed = False

    def check_peer(self, deadline: Deadline) -> None:
        super().check_peer(deadline)
        if self.closed:
            raise PrivateIPCError(Refusal.CLOSED)

    def retain_peer(self, deadline: Deadline) -> PeerProcess:
        self.check_peer(deadline)
        self.retained += 1
        return PeerProcess(123, self.peer, api=self.api)

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def bound_input(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple:
    candidate = transfer._self()
    parent = PeerIdentity(candidate.pid + 1000, candidate.start_time, candidate.principal)
    channel = NativeChannel(parent)
    endpoint = Endpoint("127.0.0.1", 12345, parent.pid, "test", 7, parent.start_time)
    monkeypatch.setattr(module, "_native_channel_type", lambda: NativeChannel)
    path = tmp_path / "active.json"

    def publish(value: Endpoint = endpoint) -> None:
        path.write_text(json.dumps({"active": value.to_dict(), "previous": None}), encoding="utf-8")

    publish()
    return channel, parent, candidate, endpoint, tmp_path, publish


def acquire(values: tuple | list, deadline: Deadline | None = None) -> module.PredecessorBinding:
    channel, parent, candidate, endpoint, root, _publish = values
    return module.PredecessorBinding.acquire(
        channel, parent, candidate, endpoint=endpoint, identity="opaque",
        root=root, deadline=deadline or Deadline(1),
    )


@pytest.mark.parametrize(("field", "value"), [
    ("pid", True), ("pid", 1), ("process_start_time", "0"),
    ("generation", 0), ("generation", True), ("port", True), ("port", 65536),
    ("bind", ""), ("version", None),
])
def test_invalid_independent_endpoint_refuses_before_duplication(
    bound_input: tuple, field: str, value: object,
) -> None:
    values = list(bound_input)
    values[3] = replace(values[3], **{field: value})
    with pytest.raises(PrivateIPCError) as caught:
        acquire(values)
    assert caught.value.reason is Refusal.IDENTITY
    assert values[0].retained == 0


@pytest.mark.parametrize("case", ["self", "foreign", "unknown", "stale", "expired"])
def test_wrong_identity_transport_publication_or_budget_refuses(bound_input: tuple, case: str) -> None:
    values = list(bound_input)
    if case == "self":
        values[1] = values[2]
        values[0].peer = values[2]
        values[3] = replace(values[3], pid=values[2].pid)
    elif case == "foreign":
        values[1] = replace(values[1], principal="foreign")
        values[0].peer = values[1]
    elif case == "unknown":
        values[0] = MemoryChannel(values[1])
    elif case == "stale":
        values[5](replace(values[3], generation=8))
    deadline = Deadline(1)
    if case == "expired":
        deadline._expires = 0
    with pytest.raises(PrivateIPCError):
        acquire(values, deadline)
    if case != "unknown":
        assert values[0].retained == 0


def test_retained_exit_requires_same_full_binding_after_channel_closure(bound_input: tuple) -> None:
    channel, _parent, _candidate, endpoint, _root, _publish = bound_input
    binding = acquire(bound_input)
    channel.close()
    with pytest.raises(PrivateIPCError) as caught:
        binding.require_exit(endpoint, "opaque", Deadline(1))
    assert caught.value.reason is Refusal.AUTHORITY
    channel.api.signalled = True
    for changed in (replace(endpoint, generation=8), replace(endpoint, version="other")):
        with pytest.raises(PrivateIPCError):
            binding.require_exit(changed, "opaque", Deadline(1))
    with pytest.raises(PrivateIPCError):
        binding.require_exit(endpoint, "changed", Deadline(1))
    binding.require_exit(endpoint, "opaque", Deadline(1))
    binding.close()
    binding.close()
    assert channel.api.closed == 1
    with pytest.raises(PrivateIPCError) as caught:
        binding.require_exit(endpoint, "opaque", Deadline(1))
    assert caught.value.reason is Refusal.CLOSED


def test_duplication_then_failed_revalidation_releases_reference(
    bound_input: tuple, monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = bound_input[0]
    retain = channel.retain_peer

    def changed(deadline: Deadline) -> PeerProcess:
        reference = retain(deadline)
        channel.peer = replace(channel.peer, start_time="0")
        return reference

    monkeypatch.setattr(channel, "retain_peer", changed)
    # The final check must recompare identity, not merely test channel liveness.
    with pytest.raises(PrivateIPCError):
        acquire(bound_input)
    assert channel.api.closed == 1


def test_exit_requires_owner_and_current_budget(bound_input: tuple) -> None:
    binding = acquire(bound_input)
    errors = []

    def wrong_thread() -> None:
        try:
            binding.require_exit(bound_input[3], "opaque", Deadline(1))
        except PrivateIPCError as error:
            errors.append(error.reason)

    thread = threading.Thread(target=wrong_thread)
    thread.start()
    thread.join(timeout=1)
    assert errors == [Refusal.AUTHORITY]
    expired = Deadline(1)
    expired._expires = 0
    with pytest.raises(PrivateIPCError) as caught:
        binding.require_exit(bound_input[3], "opaque", expired)
    assert caught.value.reason is Refusal.DEADLINE
    binding.close()


@pytest.mark.parametrize("field", ["generation", "port"])
def test_published_numeric_coercion_is_not_exact_identity(
    bound_input: tuple, field: str,
) -> None:
    values = list(bound_input)
    values[3] = replace(values[3], **{field: 1})
    values[5](values[3])
    binding = acquire(values)
    values[0].api.signalled = True
    with pytest.raises(PrivateIPCError):
        binding.require_exit(replace(values[3], **{field: True}), "opaque", Deadline(1))
    raw = values[3].to_dict()
    raw[field] = True
    (values[4] / "active.json").write_text(json.dumps({"active": raw}), encoding="utf-8")
    with pytest.raises(PrivateIPCError):
        binding.verify_preparation(values[0], "opaque", values[4], Deadline(1))
    binding.close()
    before = values[0].retained
    with pytest.raises(PrivateIPCError):
        acquire(values)
    assert values[0].retained == before


@pytest.mark.parametrize("failure", [None, "drift", "write", "route"])
def test_pending_binding_owned_by_lifespan_and_failed_ack_consumed(
    captured: tuple, monkeypatch: pytest.MonkeyPatch, failure: str | None,
) -> None:
    _memory, pending = prepare(captured, monkeypatch)
    parent = replace(pending._self_peer, pid=os.getpid() + 1000)
    channel = NativeChannel(parent)
    object.__setattr__(pending, "_parent", parent)
    object.__setattr__(pending, "_channel", channel)
    monkeypatch.setattr(module, "_native_channel_type", lambda: NativeChannel)
    endpoint = Endpoint("127.0.0.1", 12345, parent.pid, "test", 1, parent.start_time)
    root = pending.runtime.origins.config_root
    root.mkdir()
    route = root / "active.json"
    route.write_text(json.dumps({"active": endpoint.to_dict(), "previous": None}), encoding="utf-8")

    async def scenario() -> None:
        app = create_app(restored_runtime=pending.runtime)
        async with app.router.lifespan_context(app):
            pending.bind_predecessor(app, endpoint)
            if failure == "drift":
                app.state.auth_token = "changed"
            elif failure == "route":
                route.write_text(json.dumps({"active": replace(endpoint, generation=2).to_dict()}))
            elif failure == "write":
                def failed_write(*args: object) -> None:
                    raise PrivateIPCError(Refusal.IO)
                monkeypatch.setattr(channel, "write_all", failed_write)
            if failure is None:
                pending.acknowledge(app)
                assert pending.prepared
                channel.close()
                with pytest.raises(PrivateIPCError):
                    pending.require_predecessor_exit(endpoint, Deadline(1))
                channel.api.signalled = True
                pending.require_predecessor_exit(endpoint, Deadline(1))
            else:
                with pytest.raises(PrivateIPCError):
                    pending.acknowledge(app)
                assert not pending.prepared
                assert channel.api.closed == 1
                with pytest.raises(PrivateIPCError):
                    pending.acknowledge(app)
            with pytest.raises(PrivateIPCError):
                pending.bind_predecessor(app, endpoint)
        assert channel.api.closed == 1

    asyncio.run(scenario())
