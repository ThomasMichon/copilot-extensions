"""Actual wire/native retirement, independent of the embedding caller."""

from __future__ import annotations

import threading
import time

import pytest
from agent_procutil.daemon_instances import admit_daemon_instance, inspect_instances
from work_coalescing_singleton import CoalescingServer, client

from worktree_manager import mux_daemon


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("mux lifecycle did not settle")


def state(lifetime):
    return next(
        record["state"]
        for record in inspect_instances(lifetime.guard.receipt.registry_root)
        if record["instance_id"] == lifetime.guard.receipt.instance_id
    )


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(mux_daemon, "_status_monitor_lock_path", lambda: None)
    instance = mux_daemon.MuxDaemonRuntime(
        mux_daemon.registry_path(tmp_path), root=tmp_path, passive=True,
    )
    yield instance
    instance.shutdown()
    if instance._handler_lifetime is not None:
        assert instance._handler_lifetime.settled.wait(10)


class HeldHealth:
    def __init__(self, monkeypatch):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.result = {}
        original = mux_daemon.MuxDaemonRuntime.health

        def health(runtime, **kwargs):
            self.entered.set()
            assert self.release.wait(15)
            result = original(runtime, **kwargs)
            self.finished.set()
            return result

        monkeypatch.setattr(mux_daemon.MuxDaemonRuntime, "health", health)

    def start(self, server):
        assert type(server) is CoalescingServer
        endpoint = server.rendezvous()["endpoint"]
        host, _, port = endpoint.rpartition(":")

        def request():
            self.result["response"] = client.request(
                host, int(port), server.token, kind="mux-cutover-health-v1",
                key="blocked-health", payload={}, request_deadline_s=20,
            )

        self.caller = threading.Thread(target=request)
        self.caller.start()
        assert self.entered.wait(10)

    def close(self):
        self.release.set()
        self.caller.join(10)
        assert not self.caller.is_alive()
        assert self.finished.is_set()


def control(server, kind, payload=None):
    host, _, port = server.rendezvous()["endpoint"].rpartition(":")
    return client.request(
        host, int(port), server.token, kind=kind, key=kind, payload=payload or {},
        request_deadline_s=5,
    )


@pytest.mark.parametrize("forced", [False, True])
def test_foreground_ack_returns_but_custody_outlives_real_handler(
    tmp_path, monkeypatch, forced,
):
    held = HeldHealth(monkeypatch)
    captured = {}
    original = mux_daemon.MuxDaemonRuntime.start

    def start(runtime):
        original(runtime)
        captured["runtime"] = runtime
        captured["server"] = runtime.server

    monkeypatch.setattr(mux_daemon.MuxDaemonRuntime, "start", start)
    monkeypatch.setattr(mux_daemon, "_status_monitor_lock_path", lambda: None)
    result = {}

    def run():
        result["code"] = mux_daemon.run_daemon_foreground(
            tmp_path, passive=True, poll_interval_s=0.01,
        )

    owner = threading.Thread(target=run)
    owner.start()
    try:
        wait_for(lambda: "server" in captured)
        server = captured["server"]
        runtime = captured["runtime"]
        held.start(server)
        if forced:
            response = control(server, "mux-cutover-drain-v1", {"force": True})
            assert response["forced"] is True
        else:
            assert control(server, "mux-cutover-shutdown-v1") == {"shutdown": True}
        owner.join(10)
        assert not owner.is_alive()
        assert result == {"code": 0}
        lifetime = runtime._handler_lifetime
        assert lifetime.closed.is_set()
        assert not server._serve_thread.is_alive()
        assert not server._reap_thread.is_alive()
        assert server.active_handler_count() == 1
        assert runtime.handler_tracker.wait_for_drain(0)
        assert state(lifetime) == "running"
        assert not held.finished.is_set()
    finally:
        held.close()
        captured["runtime"].request_shutdown()
        owner.join(10)
    assert lifetime.settled.wait(10)
    assert server.active_handler_count() == 0
    assert state(lifetime) == "finished"


def test_embedding_shutdown_and_repeated_stop_retain_own_admission(runtime, monkeypatch):
    held = HeldHealth(monkeypatch)
    try:
        with admit_daemon_instance(
            "mux-embedding-host", runtime_root=runtime.root,
            source=mux_daemon.__file__, purpose="production",
        ) as host:
            runtime.start()
            lifetime = runtime._handler_lifetime
            assert lifetime.guard.receipt == host.receipt
            server = runtime.server
            held.start(server)
            owner = lifetime.thread
            for _ in range(3):
                runtime.shutdown()
                assert lifetime.thread is owner
                assert server.active_handler_count() == 1
        # The original host scope has exited. No caller cooperation is needed.
        assert state(lifetime) == "running"
        assert not lifetime.settled.is_set()
    finally:
        held.close()
    assert lifetime.settled.wait(10)
    assert server.active_handler_count() == 0
    assert state(lifetime) == "finished"


def test_normal_shutdown_settles_native_threads_and_record(runtime):
    runtime.start()
    lifetime, server = runtime._handler_lifetime, runtime.server
    assert state(lifetime) == "running"
    runtime.shutdown()
    assert lifetime.settled.wait(10)
    assert server.active_handler_count() == 0
    assert not server._serve_thread.is_alive()
    assert not server._reap_thread.is_alive()
    assert state(lifetime) == "finished"


def test_interrupted_shutdown_caller_cannot_retire_live_handler(runtime, monkeypatch):
    held = HeldHealth(monkeypatch)
    runtime.start()
    lifetime, server = runtime._handler_lifetime, runtime.server
    original_wait = lifetime.closed.wait

    def interrupted(timeout):
        raise KeyboardInterrupt

    held.start(server)
    try:
        monkeypatch.setattr(lifetime.closed, "wait", interrupted)
        with pytest.raises(KeyboardInterrupt):
            runtime.shutdown()
        monkeypatch.setattr(lifetime.closed, "wait", original_wait)
        assert lifetime.closed.wait(10)
        assert server.active_handler_count() == 1
        assert state(lifetime) == "running"
    finally:
        monkeypatch.setattr(lifetime.closed, "wait", original_wait)
        held.close()
    assert lifetime.settled.wait(10)
    assert state(lifetime) == "finished"


@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_startup_before_socket_failure_has_no_unowned_work(runtime, monkeypatch, error):
    def fail(*args, **kwargs):
        raise error("startup interrupted")

    monkeypatch.setattr(mux_daemon, "start_server", fail)
    if error is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt):
            runtime.start()
    else:
        runtime.start()
    lifetime = runtime._handler_lifetime
    assert lifetime.settled.wait(10)
    assert lifetime.server is None
    assert runtime.server is None
    assert state(lifetime) == "finished"


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
def test_partial_native_start_keeps_actual_accepted_handler_owned(
    runtime, monkeypatch, error,
):
    held = HeldHealth(monkeypatch)
    original_start = threading.Thread.start

    def start(thread):
        if thread.name == "work-coalescing-singleton-reaper":
            held.start(runtime.server)
            raise error("reaper start failed")
        return original_start(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    try:
        if error is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt):
                runtime.start()
        else:
            runtime.start()
        lifetime = runtime._handler_lifetime
        server = lifetime.server
        assert runtime.server is None
        assert lifetime.closed.is_set()
        assert server._serve_started
        assert not server._reap_started
        assert not server._serve_thread.is_alive()
        assert server.active_handler_count() == 1
        assert state(lifetime) == "running"
    finally:
        held.close()
    assert lifetime.settled.wait(10)
    assert server.active_handler_count() == 0
    assert state(lifetime) == "finished"


def test_close_failure_is_retried_without_claiming_settlement(runtime, monkeypatch):
    runtime.start()
    lifetime, server = runtime._handler_lifetime, runtime.server
    original = server.close
    allow_close = threading.Event()
    failed = threading.Event()

    def close():
        if not failed.is_set():
            failed.set()
            raise OSError("temporary native close failure")
        assert allow_close.wait(15)
        original()

    monkeypatch.setattr(server, "close", close)
    monkeypatch.setattr(runtime, "SHUTDOWN_DRAIN_TIMEOUT_S", 0.05)
    try:
        runtime.shutdown()
        assert failed.is_set()
        assert server._serve_thread.is_alive()
        assert state(lifetime) == "running"
        assert not lifetime.closed.is_set()
    finally:
        allow_close.set()
    assert lifetime.settled.wait(10)
    assert not server._serve_thread.is_alive()
    assert state(lifetime) == "finished"


def test_unknown_handler_count_never_means_settled(runtime, monkeypatch):
    runtime.start()
    lifetime, server = runtime._handler_lifetime, runtime.server
    actual_count = server.active_handler_count
    monkeypatch.setattr(server, "active_handler_count", lambda: None)
    try:
        runtime.shutdown()
        assert lifetime.closed.is_set()
        assert not lifetime.settled.wait(0.1)
        assert state(lifetime) == "running"
    finally:
        monkeypatch.setattr(server, "active_handler_count", actual_count)
    assert actual_count() == 0
    assert lifetime.settled.wait(10)
    assert state(lifetime) == "finished"


@pytest.mark.parametrize("stage", ["publish", "loop"])
@pytest.mark.parametrize("error", [OSError, KeyboardInterrupt])
def test_foreground_startup_and_loop_errors_keep_native_handlers_owned(
    tmp_path, monkeypatch, stage, error,
):
    held = HeldHealth(monkeypatch)
    captured, result = {}, {}
    original_start = mux_daemon.MuxDaemonRuntime.start

    def fail(*args, **kwargs):
        assert held.entered.wait(10)
        raise error("owner failure")

    def start(runtime):
        original_start(runtime)
        captured["runtime"] = runtime
        captured["server"] = runtime.server
        monkeypatch.setattr(runtime.picker_sessions, "pump", lambda: None)
        held.start(runtime.server)

    monkeypatch.setattr(mux_daemon.MuxDaemonRuntime, "start", start)
    monkeypatch.setattr(mux_daemon.MuxDaemonRuntime, "activate_restart_recovery", lambda self: None)
    monkeypatch.setattr(mux_daemon, "_status_monitor_lock_path", lambda: None)
    if stage == "publish":
        monkeypatch.setattr(mux_daemon.mux_daemon_cutover, "publish_route", fail)
    else:
        monkeypatch.setattr(mux_daemon.MuxDaemonRuntime, "has_active_demand", fail)

    def run():
        try:
            mux_daemon.run_daemon_foreground(tmp_path, poll_interval_s=0.01)
        except BaseException as raised:
            result["error"] = type(raised)

    owner = threading.Thread(target=run)
    owner.start()
    try:
        assert held.entered.wait(10)
        owner.join(10)
        assert not owner.is_alive()
        assert result == {"error": error}
        runtime, server = captured["runtime"], captured["server"]
        lifetime = runtime._handler_lifetime
        assert lifetime.closed.is_set()
        assert server.active_handler_count() == 1
        assert not server._serve_thread.is_alive()
        assert state(lifetime) == "running"
        # Custody retention does not seize cutover/election authority.
        lease = mux_daemon._acquire_daemon_lease(tmp_path)
        assert lease is not None
        mux_daemon._release_daemon_lease(lease)
    finally:
        held.close()
        owner.join(10)
    assert lifetime.settled.wait(10)
    assert state(lifetime) == "finished"


def test_cleanup_failure_retains_custody_until_retry_completes(runtime, monkeypatch):
    runtime.start()
    lifetime, server = runtime._handler_lifetime, runtime.server
    original = runtime.restart_notifications.close
    failed = threading.Event()
    allow_cleanup = threading.Event()

    def close():
        if not failed.is_set():
            failed.set()
            raise OSError("native cleanup incomplete")
        assert allow_cleanup.wait(15)
        original()

    monkeypatch.setattr(runtime.restart_notifications, "close", close)
    monkeypatch.setattr(runtime, "SHUTDOWN_DRAIN_TIMEOUT_S", 0.05)
    try:
        with pytest.raises(OSError, match="native cleanup incomplete"):
            runtime.shutdown()
        assert failed.is_set()
        assert state(lifetime) == "running"
        assert not lifetime.settled.is_set()
        assert lifetime.closed.wait(10)
        assert not server._serve_thread.is_alive()
    finally:
        allow_cleanup.set()
    assert lifetime.settled.wait(10)
    assert server.active_handler_count() == 0
    assert not server._serve_thread.is_alive()
    assert state(lifetime) == "finished"


def test_shutdown_from_own_real_control_handler_does_not_join_itself(runtime, monkeypatch):
    runtime.start()
    lifetime, server = runtime._handler_lifetime, runtime.server
    observed = {}
    original = runtime.health

    def health(**kwargs):
        runtime.shutdown()
        observed.update(
            state=state(lifetime), count=server.active_handler_count(),
            settled=lifetime.settled.is_set(),
        )
        return original(**kwargs)

    monkeypatch.setattr(runtime, "health", health)
    response = control(server, "mux-cutover-health-v1")
    assert response["status"] == "ready"
    assert observed == {"state": "running", "count": 1, "settled": False}
    assert lifetime.settled.wait(10)
    assert server.active_handler_count() == 0
    assert state(lifetime) == "finished"


def test_retirement_owner_start_failure_cannot_publish_a_socket(runtime, monkeypatch):
    from worktree_manager import mux_handler_lifetime

    captured = {}
    admit = mux_handler_lifetime.admit_daemon_instance
    start = threading.Thread.start

    def admission(*args, **kwargs):
        captured["guard"] = admit(*args, **kwargs)
        return captured["guard"]

    def start_thread(thread):
        if thread.name == "mux-handler-retirement":
            raise RuntimeError("retirement owner could not start")
        return start(thread)

    monkeypatch.setattr(mux_handler_lifetime, "admit_daemon_instance", admission)
    monkeypatch.setattr(threading.Thread, "start", start_thread)
    with pytest.raises(RuntimeError, match="retirement owner could not start"):
        runtime.start()
    assert runtime.server is None
    receipt = captured["guard"].receipt
    assert next(
        record["state"] for record in inspect_instances(receipt.registry_root)
        if record["instance_id"] == receipt.instance_id
    ) == "finished"


def test_restart_after_confirmed_retirement_gets_a_new_owner(runtime):
    runtime.start()
    previous = runtime._handler_lifetime
    runtime.shutdown()
    assert previous.settled.wait(10)
    runtime.start()
    lifetime = runtime._handler_lifetime
    assert lifetime is not previous
    assert state(previous) == "finished"
    assert state(lifetime) == "running"
    runtime.shutdown()
    assert lifetime.settled.wait(10)
    assert state(lifetime) == "finished"
