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
    monkeypatch.setattr(recovery, "observe", lambda *a: owner.retained)
    return events


@pytest.mark.parametrize("failure", [
    recovery.RecoveryUnavailable("unsupported"), TimeoutError("predecessor timed out"),
])
def test_unrecoverable_predecessor_does_not_veto_incoming_startup(owner, activation, monkeypatch, failure):
    from zdd import diagnostics

    def unsupported(*args, **kwargs):
        raise failure

    def run(orch, **kwargs):
        activation.append("incoming-startup")
        return CutoverResult(ok=True, committed=True)

    monkeypatch.setattr(recovery, "negotiate", unsupported)
    monkeypatch.setattr(CutoverOrchestrator, "run", run)
    monkeypatch.setattr(control, "spawn_passive", lambda *a, **k: pytest.fail("unexpected native spawn"))
    monkeypatch.setattr(
        diagnostics, "terminate_pid_if_identity", lambda *a, **k: pytest.fail("unexpected native termination"),
    )
    result = control.activate_after_update()
    assert result["action"] == "cutover" and result["result"]["ok"] is True
    assert "incoming-startup" in activation


def test_actual_installer_constructor_binds_guarded_drain_first_contract(owner, activation, monkeypatch):
    monkeypatch.setattr(recovery, "negotiate", lambda *a, **k: owner.retained)

    def run(orch, **kwargs):
        assert orch.expected_old == owner.retained.route and orch.drain_before_publish
        assert orch.refuse_old(owner.route.to_dict()) is None
        assert orch.refuse_restoration == owner.retained.refuse_restoration
        client = orch.make_client(owner.route.base_url)
        assert client.token == "retained-fixture-token" and client.guarded_predecessor is owner.retained
        return CutoverResult(ok=True, committed=True)

    monkeypatch.setattr(CutoverOrchestrator, "run", run)
    assert control.activate_after_update()["result"]["ok"] is True
    assert activation == ["recovery", "reap", "release", "delayed-reap"]


@pytest.mark.parametrize("was_live", [False, True])
def test_guarded_live_owner_without_listener_starts_incoming_from_exact_route(
    owner, activation, monkeypatch, was_live,
):
    from agent_worktrees import status_monitor_runtime
    monkeypatch.setattr(control, "_monitor_control_url_from_route", lambda: None)
    monkeypatch.setattr(status_monitor_runtime, "_restart_status_monitor", lambda **k: pytest.fail("legacy restart"))
    monkeypatch.setattr(recovery, "negotiate", lambda *a, **k: (_ for _ in ()).throw(ConnectionRefusedError()))
    monkeypatch.setattr(CutoverOrchestrator, "run", lambda *a, **k: CutoverResult(ok=True, committed=True))
    result = control.activate_after_update(monitor_was_live=was_live)
    assert result["action"] == "cutover" and result["result"]["ok"] is True
    assert activation == ["recovery", "reap", "release", "delayed-reap"]


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


@pytest.fixture
def forward_cutover(tmp_path, monkeypatch):
    from zdd import diagnostics

    events = []
    starts = {111: "1010", 222: "2020"}
    monkeypatch.setattr(diagnostics, "process_start_time", starts.get)
    monkeypatch.setattr(diagnostics, "terminate_pid_if_identity", lambda *a, **k: pytest.fail("native kill"))
    monkeypatch.setattr(routing, "_listening", lambda *a, **k: True)
    old = routing.publish_active(tmp_path, bind="127.0.0.1", port=61001, pid=111)
    state = SimpleNamespace(healthy=True, drain="ready", shutdown="ready", cleanup=True)

    class Handle:
        pid = 222
        process_start_time = "2020"
        terminated = False

        def terminate(self):
            events.append("terminate-incoming")
            self.terminated = True

        def poll(self):
            return 0 if self.terminated else None

    class Client:
        def drain(self, **kwargs):
            events.append("drain-old")
            if state.drain == "timeout":
                raise TimeoutError("old drain timed out")
            return {"drained": state.drain == "ready", "clean": state.drain == "ready", "forced": False}

        def shutdown(self):
            events.append("shutdown-old")
            if state.shutdown == "timeout":
                raise TimeoutError("old shutdown timed out")
            return {"retired": state.shutdown == "ready"}

        def undrain(self):
            events.append("undrain-old")
            return {"draining": False}

        def adopt_relay(self):
            events.append("adopt-incoming")
            return {"adopted": True}

    def cleanup(endpoint):
        assert endpoint == old
        events.append("cleanup-old")
        if state.cleanup == "error":
            raise OSError("old cleanup unavailable")
        return {"exit_confirmed": state.cleanup}

    handle = Handle()
    ticks = [0.0]

    def clock():
        ticks[0] += 0.1
        return ticks[0]

    orch = CutoverOrchestrator(
        tmp_path, bind="127.0.0.1", version="new",
        spawn_passive=lambda port: events.append("start-incoming") or handle,
        health_check=lambda host, port: state.healthy if port == 61002 else True,
        make_client=lambda url: Client(), pick_free_port=lambda: 61002,
        clock=clock, sleep=lambda seconds: None, expected_old=old,
        refuse_old=lambda current: None, drain_before_publish=True,
        on_published=lambda endpoint: events.append("publish-incoming"),
        retire_failed_predecessor=cleanup,
    )
    return orch, events, state, handle


@pytest.mark.parametrize("failure", ["incompatible", "drain-timeout", "shutdown-refused", "shutdown-timeout"])
@pytest.mark.parametrize("cleanup", [False, True, "error"])
def test_real_cutover_keeps_incoming_after_predecessor_failure(forward_cutover, failure, cleanup):
    from zdd import lifecycle

    orch, events, state, handle = forward_cutover
    state.cleanup = cleanup
    if failure == "incompatible":
        state.drain = "refused"
    elif failure == "drain-timeout":
        state.drain = "timeout"
    elif failure == "shutdown-refused":
        state.shutdown = "refused"
    else:
        state.shutdown = "timeout"
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert result.ok and result.committed and not result.rolled_back
    assert not handle.terminated and "undrain-old" not in events
    assert events.index("start-incoming") < events.index("cleanup-old")
    assert routing.read_table(orch.config_dir)["active"]["pid"] == handle.pid
    assert result.to_dict()["predecessor_cleanup"]["exit_confirmed"] is (cleanup is True)
    records = [json.loads(line) for line in (orch.config_dir / "lifecycle.log").read_text().splitlines()]
    retirement = [row for row in records if row["action"] == lifecycle.CUTOVER_RETIRE][-1]
    assert retirement["outcome"] == (lifecycle.OK if cleanup is True else lifecycle.FAIL)
    assert retirement["detail"]["old_endpoint"] == orch.expected_old.to_dict()
    assert retirement["detail"]["predecessor_cleanup"] == result.predecessor_cleanup


def test_real_cutover_rolls_back_when_incoming_fails_to_start(forward_cutover):
    orch, events, state, handle = forward_cutover
    state.healthy = False
    result = orch.run(health_timeout=0.1, drain_timeout=1)
    assert not result.ok and not result.committed and result.rolled_back
    assert handle.terminated and "cleanup-old" not in events
    assert routing.read_table(orch.config_dir)["active"] == orch.expected_old.to_dict()


@pytest.mark.parametrize("drain", ["refused", "timeout"])
@pytest.mark.parametrize("failure", ["publication", "promotion"])
def test_real_cutover_preserves_predecessor_after_failed_drain_and_incoming_failure(
    forward_cutover, monkeypatch, drain, failure,
):
    orch, events, state, handle = forward_cutover
    state.drain = drain

    def fail(*a, **k):
        raise routing.ActivePublicationRefused("fixture incoming publication/promotion failed")

    if failure == "publication":
        monkeypatch.setattr(routing, "publish_active_with_previous_guarded", fail)
    else:
        orch.on_published = fail
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and not result.committed
    if failure == "promotion":
        assert result.rolled_back
    assert handle.terminated and "cleanup-old" not in events and "shutdown-old" not in events
    assert routing.read_table(orch.config_dir)["active"]["pid"] == orch.expected_old.pid
    assert result.predecessor_cleanup["attempted"] is False


@pytest.mark.parametrize("cleanup", [False, True, "error"])
def test_async_cutover_cleanup_failure_cannot_reverse_serving_successor(forward_cutover, cleanup):
    from zdd import lifecycle

    orch, events, state, handle = forward_cutover
    state.cleanup = cleanup
    orch.self_retirement = True
    orch.drain_before_publish = False
    orch.on_published = None
    orch.activate_successor = lambda endpoint: events.append("activate-incoming")
    orch.serving_health_check = lambda host, port: events.append("serving-incoming") or True
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert result.ok and result.committed and not result.rolled_back and not handle.terminated
    assert "drain-old" not in events and "shutdown-old" not in events and "undrain-old" not in events
    assert events.index("activate-incoming") < events.index("serving-incoming") < events.index("cleanup-old")
    assert result.predecessor_cleanup["exit_confirmed"] is (cleanup is True)
    records = [json.loads(line) for line in (orch.config_dir / "lifecycle.log").read_text().splitlines()]
    retired = [row for row in records if row["action"] == lifecycle.CUTOVER_RETIRE][-1]
    assert retired["outcome"] == (lifecycle.OK if cleanup is True else lifecycle.FAIL)
    assert retired["detail"]["old_endpoint"] == orch.expected_old.to_dict()
    assert retired["detail"]["predecessor_cleanup"] == result.predecessor_cleanup


@pytest.mark.parametrize("identity", [
    "matching", "reused", "unknown", "exited", "exited-retained-birth", "kill-error",
])
def test_failed_recovery_cleanup_preserves_foreign_processes_and_releases_only_old_route(
    tmp_path, monkeypatch, identity,
):
    from zdd import diagnostics

    starts = {111: "1010", 222: "2020"}
    monkeypatch.setattr(diagnostics, "process_start_time", starts.get)
    monkeypatch.setattr(recovery.locks, "process_start_time", starts.get)
    old = routing.publish_active(tmp_path, bind="127.0.0.1", port=61001, pid=111)
    new = routing.publish_active(tmp_path, bind="127.0.0.1", port=61002, pid=222, demote_existing=True)
    table = routing.read_table(tmp_path)
    retained = recovery.GuardedPredecessor(old, recovery.ReceiverIdentity(111, "1010", ""), "fixture")
    calls = []

    def terminate(pid, start, *, exit_timeout):
        assert (pid, start, exit_timeout) == (111, "1010", 10.0)
        calls.append((pid, start))
        if identity == "kill-error":
            raise OSError("termination denied")
        return {"identity_verified": True, "killed": True, "exit_confirmed": True}

    monkeypatch.setattr(diagnostics, "terminate_pid_if_identity", terminate)
    monkeypatch.setattr(diagnostics, "_pid_alive", lambda pid: not identity.startswith("exited"))
    if identity == "reused":
        starts[111] = "9999"
    elif identity in {"unknown", "exited"}:
        starts.pop(111)
    result = recovery.retire_failed_predecessor(tmp_path, retained, new)
    assert bool(calls) is (identity in {"matching", "kill-error"})
    assert result["exit_confirmed"] is (identity in {"matching", "exited", "exited-retained-birth"})
    assert result["route_released"] is True
    following = routing.read_table(tmp_path)
    assert following["active"] == table["active"] and "previous" not in following
    assert following["publication_history"] == table["publication_history"]


@pytest.mark.parametrize("changed", ["active", "previous"])
def test_failed_recovery_cannot_kill_or_release_changed_route(tmp_path, monkeypatch, changed):
    from zdd import diagnostics

    monkeypatch.setattr(diagnostics, "process_start_time", lambda pid: str(pid * 10))
    old = routing.publish_active(tmp_path, bind="127.0.0.1", port=61001, pid=111)
    new = routing.publish_active(tmp_path, bind="127.0.0.1", port=61002, pid=222, demote_existing=True)
    retained = recovery.GuardedPredecessor(old, recovery.ReceiverIdentity(111, "1110", ""), "fixture")
    table = routing.read_table(tmp_path)
    table[changed] = routing.Endpoint("127.0.0.1", 61003, 333, generation=9, process_start_time="3330").to_dict()
    routing.routing_table_path(tmp_path).write_text(json.dumps(table), encoding="utf-8")
    monkeypatch.setattr(diagnostics, "terminate_pid_if_identity", lambda *a, **k: pytest.fail("foreign native kill"))
    assert recovery.retire_failed_predecessor(tmp_path, retained, new)["reason"] == "predecessor-route-changed"
    assert routing.read_table(tmp_path) == table


@pytest.mark.parametrize("identity", ["live", "reused", "unknown", "exited-retained-birth"])
def test_restoration_revalidates_native_owner_inside_publication_fence(
    forward_cutover, monkeypatch, identity,
):
    from zdd import process_exit

    orch, events, state, handle = forward_cutover
    old = orch.expected_old
    retained = recovery.GuardedPredecessor(
        old, recovery.ReceiverIdentity(old.pid, old.process_start_time, ""), "fixture",
    )
    orch.refuse_restoration = retained.refuse_restoration
    publishing = [False]
    publish = routing.publish_active_with_previous_guarded
    checks = []

    @contextmanager
    def reference(pid, start):
        checks.append(publishing[0])
        assert (pid, start) == (old.pid, old.process_start_time)
        if identity in {"reused", "unknown"}:
            raise ProcessLookupError("fixture native identity unavailable")
        yield SimpleNamespace(wait=lambda timeout: identity == "exited-retained-birth")

    def publish_guarded(*args, **kwargs):
        if kwargs.get("pid") == old.pid:
            guard = kwargs["refuse_current"]
            def fenced(active):
                publishing[0] = True
                try:
                    return guard(active)
                finally:
                    publishing[0] = False
            kwargs["refuse_current"] = fenced
        return publish(*args, **kwargs)

    def failed_promotion(endpoint):
        raise RuntimeError("fixture successor failed to serve")

    monkeypatch.setattr(process_exit, "open_process", reference)
    monkeypatch.setattr(routing, "publish_active_with_previous_guarded", publish_guarded)
    orch.on_published = failed_promotion
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert not result.ok and handle.terminated
    assert checks and checks[0] is True
    assert result.rolled_back is (identity == "live")
    active = routing.read_table(orch.config_dir)["active"]
    assert active["pid"] == (old.pid if identity == "live" else handle.pid)
    assert ("undrain-old" in events) is (identity == "live")


@pytest.mark.parametrize("exhausted", [False, True])
def test_best_effort_cutover_boundedly_excludes_predecessor_port(forward_cutover, exhausted):
    from zdd import breadcrumb

    orch, events, state, handle = forward_cutover
    ports = [orch.expected_old.port] * 8 if exhausted else [orch.expected_old.port, 61002]
    calls = []
    before = routing.routing_table_path(orch.config_dir).read_bytes()

    def pick():
        calls.append(ports.pop(0))
        return calls[-1]

    orch.pick_free_port = pick
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert len(calls) == (8 if exhausted else 2)
    if exhausted:
        assert not result.ok and events == [] and not handle.terminated
        assert routing.routing_table_path(orch.config_dir).read_bytes() == before
        assert breadcrumb.read_breadcrumb(orch.config_dir) is None
    else:
        assert result.ok and result.new_port == 61002
        assert routing.read_table(orch.config_dir)["active"]["pid"] == handle.pid


def test_darwin_unavailable_native_termination_keeps_successor_and_reports_custody(
    forward_cutover, monkeypatch,
):
    import signal
    from zdd import diagnostics

    orch, events, state, handle = forward_cutover
    old = orch.expected_old
    retained = recovery.GuardedPredecessor(
        old, recovery.ReceiverIdentity(old.pid, old.process_start_time, ""), "fixture",
    )
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Darwin")
    monkeypatch.delattr(os, "pidfd_open", raising=False)
    monkeypatch.delattr(signal, "pidfd_send_signal", raising=False)
    monkeypatch.setattr(os, "kill", lambda *a: pytest.fail("unsafe PID-only signal"))
    monkeypatch.setattr(diagnostics, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(recovery.locks, "process_start_time", lambda pid: old.process_start_time)
    monkeypatch.setattr(
        diagnostics, "terminate_pid_if_identity",
        lambda pid, start, **kwargs: diagnostics._terminate_posix_if_identity(pid, start, **kwargs),
    )
    orch.retire_failed_predecessor = lambda endpoint: recovery.retire_failed_predecessor(
        orch.config_dir, retained, recovery.active_endpoint(orch.config_dir),
    )
    state.shutdown = "refused"
    result = orch.run(health_timeout=1, drain_timeout=1)
    assert result.ok and result.committed and not result.rolled_back and not handle.terminated
    assert result.predecessor_cleanup == {
        "killed": False, "identity_verified": False, "method": "pidfd-unavailable",
        "exit_confirmed": False, "route_released": True,
    }
    assert routing.read_table(orch.config_dir)["active"]["pid"] == handle.pid
    assert "previous" not in routing.read_table(orch.config_dir)
