"""Pure guarded recovery/schema and actual installer-adapter contracts."""

from __future__ import annotations

import copy
import json
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from zdd import routing
from zdd.cutover import CutoverOrchestrator, CutoverResult

from agent_worktrees import status_monitor_cutover as control
from agent_worktrees import status_monitor_recovery_contract as recovery


@pytest.fixture
def owner(monkeypatch):
    pid = os.getpid()
    route = routing.Endpoint("127.0.0.1", 61001, pid, "old", 7, "1010")
    receiver = recovery.ReceiverIdentity(pid, "1010", "runtime-a")
    state = SimpleNamespace(route=route, start="1010", locked=False, opened=0, closed=0)
    monkeypatch.setattr(recovery.locks, "process_start_time", lambda p: state.start)
    monkeypatch.setattr(recovery, "active_endpoint", lambda directory: state.route)

    @contextmanager
    def route_lock(directory, *, deadline):
        assert not state.locked
        state.locked = True
        try:
            yield
        finally:
            state.locked = False

    monkeypatch.setattr(recovery.routing, "routing_lock", route_lock)
    retained = recovery.GuardedPredecessor(route, receiver, "retained-fixture-token")
    state.retained = retained
    state.payload = {
        "contractVersion": 1, "expectedReceiver": receiver.to_dict(), "expectedRoute": route.to_dict(),
    }
    return state


def receive(state, payload=None, *, generation="runtime-a", port=61001, reopen=None):
    def opened():
        assert state.locked
        state.opened += 1
        return {"draining": False}

    def closed():
        state.closed += 1

    return recovery.handle(
        state.payload if payload is None else payload, directory=Path("unused"),
        generation=generation, port=port, reopen=opened if reopen is None else reopen,
        close_on_failure=closed, lock_timeout=5,
    )


def test_guarded_receiver_reopens_only_under_existing_authority_lock(owner):
    result = receive(owner)
    assert result == {
        "contractVersion": 1, "status": "undrained", "receiver": owner.retained.receiver.to_dict(),
        "route": owner.route.to_dict(),
    }
    assert owner.opened == 1 and owner.closed == 0 and not owner.locked


@pytest.mark.parametrize("field,value", [
    ("contractVersion", True), ("contractVersion", 2),
    ("expectedReceiver", {}), ("expectedRoute", {}),
])
def test_invalid_operation_cannot_reopen(owner, field, value):
    payload = copy.deepcopy(owner.payload)
    payload[field] = value
    assert receive(owner, payload)["status"] == "refused"
    assert owner.opened == owner.closed == 0


@pytest.mark.parametrize("change", ["start", "route", "generation", "port"])
def test_owner_changes_are_rejected_before_any_admission_effect(owner, change):
    kwargs = {}
    if change == "start":
        owner.start = "9999"
    elif change == "route":
        owner.route = routing.Endpoint("127.0.0.1", 61002, 202, "third", 9, "2020")
    elif change == "generation":
        kwargs["generation"] = "runtime-b"
    else:
        kwargs["port"] = 61003
    assert receive(owner, **kwargs)["status"] == "refused"
    assert owner.opened == owner.closed == 0


@pytest.mark.parametrize("failure", ["false", "exception", "cancellation"])
def test_partial_reopen_failure_closes_admission_and_preserves_error(owner, failure):
    error = KeyboardInterrupt("cancel") if failure == "cancellation" else RuntimeError("reopen failed")

    def reopen():
        assert owner.locked
        owner.opened += 1
        if failure != "false":
            raise error
        return {"draining": True}

    if failure == "false":
        assert receive(owner, reopen=reopen)["status"] == "refused"
    else:
        with pytest.raises(type(error)) as caught:
            receive(owner, reopen=reopen)
        assert caught.value is error
    assert owner.opened == owner.closed == 1
    assert not owner.locked


def test_legitimate_restored_route_generation_is_not_process_generation(owner, monkeypatch):
    restored = routing.Endpoint("127.0.0.1", 61001, owner.route.pid, "old", 11, "1010")
    owner.route = restored
    client = control.ControlClient(
        restored.base_url, token="retained-fixture-token", config_dir=Path("unused"),
        guarded_predecessor=owner.retained,
    )
    calls = []

    def request(action, payload, **kwargs):
        calls.append((action, payload))
        return receive(owner, payload)

    monkeypatch.setattr(client, "_request", request)
    result = client.undrain()
    assert result["draining"] is False and result["route"]["generation"] == 11
    assert calls[0][0] == recovery.ACTION and len(calls) == 1
    assert calls[0][1]["expectedReceiver"]["runtimeGeneration"] == "runtime-a"


@pytest.mark.parametrize("reply", [
    {"draining": False}, {"contractVersion": True, "status": "undrained"},
    {"contractVersion": 1, "status": "refused", "reason": "route-changed"},
])
def test_unqualified_reply_never_falls_back_to_legacy_undrain(owner, monkeypatch, reply):
    calls = []
    client = control.ControlClient(
        owner.route.base_url, token="retained-fixture-token", config_dir=Path("unused"),
        guarded_predecessor=owner.retained,
    )
    monkeypatch.setattr(client, "_request", lambda action, *a, **k: calls.append(action) or reply)
    with pytest.raises(recovery.RecoveryUnavailable):
        client.undrain()
    assert calls == [recovery.ACTION]


def test_strict_frame_and_duplicate_keys(owner):
    frame = {"version": 1, "token": "fixture", "action": recovery.ACTION, "payload": owner.payload}
    raw = json.dumps(frame).encode() + b"\n"
    assert recovery.decode_request(raw) == frame
    for malformed in (
        raw[:-1], raw.replace(b'"version": 1', b'"version": true'),
        raw.replace(b'"version": 1', b'"version": 1, "version": 1'),
        json.dumps({**frame, "extra": True}).encode() + b"\n",
        json.dumps({**frame, "payload": []}).encode() + b"\n",
    ):
        with pytest.raises(recovery.RecoveryUnavailable):
            recovery.decode_request(malformed)


def test_preflight_retains_one_existing_token_and_one_health_response(owner):
    calls = []

    class Client:
        def __init__(self, base_url, **kwargs):
            calls.append((base_url, kwargs))

        def health(self):
            return {
                "pid": owner.route.pid, "runtime_generation": "runtime-a", "status": "ready",
                "capabilities": [recovery.CAPABILITY],
            }

    token = SimpleNamespace(read_text=lambda **kwargs: "retained-fixture-token\n")
    selected = recovery.negotiate(Path("unused"), token, Client, timeout=5)
    assert selected == owner.retained and len(calls) == 1
    assert calls[0][1]["token"] == "retained-fixture-token"
    assert "retained-fixture-token" not in repr(selected)


@pytest.fixture
def activation(monkeypatch, owner):
    from agent_worktrees import status_monitor_runtime, status_monitor_reap_stale

    events = []
    monkeypatch.setattr(recovery, "enabled", lambda: True)
    monkeypatch.setattr(control, "routing_dir", lambda *a: Path("unused"))
    monkeypatch.setattr(status_monitor_runtime, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(control, "_monitor_lock_is_live", lambda: True)
    monkeypatch.setattr(control, "_monitor_control_url_from_route", lambda: owner.route.base_url)
    monkeypatch.setattr(control.breadcrumb, "read_breadcrumb", lambda *a: None)
    monkeypatch.setattr(control.breadcrumb, "recovery_refusal", lambda *a: None)
    monkeypatch.setattr(control, "_acquire_cutover_lock", lambda *a: SimpleNamespace(release=lambda: events.append("release")))
    monkeypatch.setattr(control.breadcrumb, "recover_stale_cutover", lambda *a, **k: events.append("recovery") or {})
    monkeypatch.setattr(control, "_reap_abandoned_passive", lambda *a: events.append("reap") or {})
    monkeypatch.setattr(status_monitor_reap_stale, "schedule_delayed_daemon_health_reap", lambda: events.append("delayed-reap"))
    monkeypatch.setattr(control, "load_or_create_control_token", lambda *a: pytest.fail("token minted"))
    return events


def test_unsupported_predecessor_refuses_before_every_transition_effect(owner, activation, monkeypatch):
    def unsupported(*args, **kwargs):
        raise recovery.RecoveryUnavailable("unsupported")

    monkeypatch.setattr(recovery, "negotiate", unsupported)
    monkeypatch.setattr(control, "spawn_passive", lambda *a, **k: pytest.fail("spawned"))
    result = control.activate_after_update()
    assert result["action"] == "refused" and result["result"]["ok"] is False
    assert activation == ["release"]


def test_actual_installer_constructor_binds_guarded_drain_first_contract(owner, activation, monkeypatch):
    monkeypatch.setattr(recovery, "negotiate", lambda *a, **k: owner.retained)

    def run(orch, **kwargs):
        assert orch.expected_old == owner.retained.route and orch.drain_before_publish
        assert orch.refuse_old(owner.route.to_dict()) is None
        client = orch.make_client(owner.route.base_url)
        assert client.token == "retained-fixture-token" and client.guarded_predecessor is owner.retained
        return CutoverResult(ok=True, committed=True)

    monkeypatch.setattr(CutoverOrchestrator, "run", run)
    assert control.activate_after_update()["result"]["ok"] is True
    assert activation == ["recovery", "reap", "release", "delayed-reap"]


@pytest.mark.parametrize("was_live", [False, True])
def test_guarded_live_owner_without_url_never_restarts(owner, activation, monkeypatch, was_live):
    from agent_worktrees import status_monitor_runtime
    monkeypatch.setattr(control, "_monitor_control_url_from_route", lambda: None)
    monkeypatch.setattr(status_monitor_runtime, "_restart_status_monitor", lambda **k: activation.append("restart"))
    result = control.activate_after_update(monitor_was_live=was_live)
    assert result["action"] == "refused" and result["result"]["ok"] is False
    assert activation == []


@pytest.mark.parametrize("accepted,exited", [(False, False), (True, False), (True, True)])
def test_guarded_shutdown_requires_native_exit_not_only_acceptance(owner, monkeypatch, accepted, exited):
    from zdd import process_exit
    events = []
    @contextmanager
    def opened(pid, start):
        assert (pid, start) == (owner.route.pid, owner.start)
        events.append("capture")
        def wait(timeout):
            assert 0 <= timeout <= 0.25 and events[-1] == "shutdown"
            events.append("wait")
            return exited
        try:
            yield SimpleNamespace(wait=wait)
        finally:
            events.append("close")
    monkeypatch.setattr(process_exit, "open_process", opened)
    client = control.ControlClient(owner.route.base_url, token="fixture", timeout=0.25,
                                   guarded_predecessor=owner.retained)
    def request(action, **kwargs):
        assert action == "shutdown" and events == ["capture"]
        assert 0 < kwargs["total_timeout"] <= 0.25
        events.append(action)
        return {"shutdown": accepted}
    monkeypatch.setattr(client, "_request", request)
    if accepted and exited:
        assert client.shutdown() == {"shutdown": True, "retired": True}
    else:
        with pytest.raises(recovery.RecoveryUnavailable, match="retirement"):
            client.shutdown()
    assert events == (["capture", "shutdown", "wait", "close"] if accepted
                      else ["capture", "shutdown", "close"])


def test_guarded_shutdown_native_identity_failure_precedes_control(owner, monkeypatch):
    from zdd import process_exit
    def refused(*a):
        raise ProcessLookupError("identity mismatch")
    monkeypatch.setattr(process_exit, "open_process", refused)
    client = control.ControlClient(owner.route.base_url, token="fixture", guarded_predecessor=owner.retained)
    monkeypatch.setattr(client, "_request", lambda *a, **k: pytest.fail("shutdown before native identity"))
    with pytest.raises(ProcessLookupError):
        client.shutdown()


@pytest.mark.parametrize("expiry", [None, "connect", "send"])
def test_real_shutdown_request_bounds_connect_send_and_retirement(owner, monkeypatch, expiry):
    from zdd import process_exit

    clock, events, timeouts = [100.0], [], []
    monkeypatch.setattr(control, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    @contextmanager
    def opened(pid, start):
        def wait(timeout):
            events.append("wait")
            assert timeout == 27
            return True
        yield SimpleNamespace(wait=wait)

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            events.append("close")

        def settimeout(self, timeout):
            timeouts.append(timeout)

        def sendall(self, body):
            assert json.loads(body)["action"] == "shutdown"
            events.append("send")
            clock[0] = 130 if expiry == "send" else 102

        def shutdown(self, direction):
            events.append("shutdown")

        def recv(self, size):
            events.append("recv")
            clock[0] = 103
            return b'{"version":1,"result":{"shutdown":true}}\n'

    def connect(address, *, timeout):
        assert timeout == 30
        events.append("connect")
        clock[0] = 130 if expiry == "connect" else 101
        return Socket()

    monkeypatch.setattr(process_exit, "open_process", opened)
    monkeypatch.setattr(control.socket, "create_connection", connect)
    client = control.ControlClient(owner.route.base_url, token="fixture", timeout=65,
                                   guarded_predecessor=owner.retained)
    if expiry:
        with pytest.raises(TimeoutError, match="deadline"):
            client.shutdown()
        assert events == (["connect", "close"] if expiry == "connect"
                          else ["connect", "send", "close"])
        assert timeouts == ([] if expiry == "connect" else [29])
    else:
        assert client.shutdown() == {"shutdown": True, "retired": True}
        assert events == ["connect", "send", "shutdown", "recv", "close", "wait"]
        assert timeouts == [29, 28, 28]


@pytest.mark.parametrize("missing", [None, "published", "worktrees", "repositories", "pid"])
def test_successor_serving_gate_is_stricter_than_passive_preflight(owner, missing):
    health = {"status": "ready", "pid": owner.route.pid, "published": True,
              "worktrees": {"status": "ready"}, "repositories": {"admitting": True}}
    if missing is not None:
        health.pop(missing)
    make_client = lambda url: SimpleNamespace(health=lambda: health)
    assert recovery.successor_healthy("127.0.0.1", 61001, make_client) is True
    assert recovery.successor_healthy("127.0.0.1", 61001, make_client, expected=owner.route) is (missing is None)
    passive = {"status": "ready", "pid": owner.route.pid, "published": False,
               "worktrees": {"status": "unready"}, "repositories": {"admitting": False}}
    make_client = lambda url: SimpleNamespace(health=lambda: passive)
    assert recovery.successor_healthy("127.0.0.1", 61001, make_client) is True
    assert recovery.successor_healthy("127.0.0.1", 61001, make_client, expected=owner.route) is False
