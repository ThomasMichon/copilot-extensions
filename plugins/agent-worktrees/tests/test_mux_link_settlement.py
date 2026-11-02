"""Managed-mux custody follows actual accepted wire handlers, not host return."""

from __future__ import annotations

import threading
import time

import pytest
from agent_procutil.daemon_instances import admit_daemon_instance, inspect_instances
from work_coalescing_singleton import CoalescingServer, client

from agent_worktrees import mux_link


def record_state(receipt):
    return next(
        record["state"] for record in inspect_instances(receipt.registry_root)
        if record["instance_id"] == receipt.instance_id
    )


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    pytest.fail("managed-mux retirement did not complete")


class HeldObservation:
    def __init__(self, monkeypatch):
        self.entered = threading.Event()
        self.release = threading.Event()
        self.finished = threading.Event()
        self.result = {}
        original = mux_link.ManagedMuxCache.apply_observation

        def apply(cache, payload):
            self.entered.set()
            assert self.release.wait(15)
            result = original(cache, payload)
            self.finished.set()
            return result

        monkeypatch.setattr(mux_link.ManagedMuxCache, "apply_observation", apply)

    def start(self, server):
        assert type(server) is CoalescingServer
        host, _, port = server.rendezvous()["endpoint"].rpartition(":")

        def request():
            self.result["response"] = client.request(
                host, int(port), server.token, kind=mux_link.KIND, key="held",
                payload={
                    "project": "test", "worktree_id": "wt", "mux_session": "test-wt",
                    "mapping_revision": 1, "live": True,
                },
                request_deadline_s=20,
            )

        self.caller = threading.Thread(target=request)
        self.caller.start()
        assert self.entered.wait(10)

    def close(self):
        self.release.set()
        self.caller.join(10)
        assert not self.caller.is_alive()
        assert self.finished.is_set()


@pytest.mark.parametrize("earlier", [None, OSError("earlier cleanup"), KeyboardInterrupt("earlier interrupt")])
def test_host_shutdown_cannot_finish_admission_before_actual_mux_handler(tmp_path, monkeypatch, earlier):
    held = HeldObservation(monkeypatch)
    runtime = mux_link.InProcessRuntime()
    caught = None
    try:
        try:
            with admit_daemon_instance(
                "status-monitor", runtime_root=tmp_path, source=mux_link.__file__,
                purpose="production",
            ) as host:
                runtime.start(tmp_path / "managed-mux.json")
                owner = runtime._retirement
                assert owner.guard.receipt == host.receipt
                server, cache = runtime.server, runtime.cache
                held.start(server)
                try:
                    if earlier is not None:
                        raise earlier
                finally:
                    runtime.shutdown()
        except BaseException as error:
            caught = error
        assert caught is earlier
        assert cache._closed
        assert not server._serve_thread.is_alive()
        assert not server._reap_thread.is_alive()
        assert server.active_handler_count() == 1
        # The real host admission scope is over, but its accepted work is not.
        assert record_state(host.receipt) == "running"
        assert not held.finished.is_set()
    finally:
        held.close()
        runtime.shutdown()
    wait_for(lambda: server.active_handler_count() == 0)
    wait_for(lambda: record_state(host.receipt) == "finished")
    assert held.result["response"] == {"applied": False, "reason": "closed"}


def test_normal_and_repeated_shutdown_confirm_native_and_record_retirement(tmp_path):
    runtime = mux_link.InProcessRuntime()
    with admit_daemon_instance(
        "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
    ) as host:
        runtime.start(tmp_path / "cache.json")
        server, cache, owner = runtime.server, runtime.cache, runtime._retirement
        thread = owner.thread
        for _ in range(3):
            runtime.shutdown()
            assert owner.thread is thread
            assert owner.closed.is_set()
            assert cache._closed
        assert not server._serve_thread.is_alive()
        assert not server._reap_thread.is_alive()
        assert server.active_handler_count() == 0
    assert owner.settled.wait(10)
    assert record_state(host.receipt) == "finished"


@pytest.mark.parametrize("stage", ["server", "cache"])
@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_close_error_preserves_original_after_native_retry_not_before_settlement(
    tmp_path, monkeypatch, stage, error_type,
):
    held = HeldObservation(monkeypatch)
    runtime = mux_link.InProcessRuntime()
    first_error = error_type("first close failure")
    calls, native_closed = [], threading.Event()
    try:
        with admit_daemon_instance(
            "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
        ) as host:
            runtime.start(tmp_path / "cache.json")
            server, cache, owner = runtime.server, runtime.cache, runtime._retirement
            held.start(server)
            subject = server if stage == "server" else cache
            original = subject.close

            def close():
                calls.append(threading.get_ident())
                if len(calls) == 1:
                    raise first_error
                original()
                native_closed.set()

            monkeypatch.setattr(subject, "close", close)
            with pytest.raises(error_type) as raised:
                runtime.shutdown()
            assert raised.value is first_error
            assert native_closed.is_set()
            assert owner.closed.is_set()
            assert cache._closed
            assert not server._serve_thread.is_alive()
            assert server.active_handler_count() == 1
            assert runtime.server is runtime.cache is None
        assert record_state(host.receipt) == "running"
    finally:
        held.close()
        runtime.shutdown()
    assert owner.settled.wait(10)
    assert server.active_handler_count() == 0
    assert record_state(host.receipt) == "finished"


def test_repeated_interrupts_do_not_replace_first_error_or_release_custody(tmp_path, monkeypatch):
    held = HeldObservation(monkeypatch)
    runtime = mux_link.InProcessRuntime()
    initial = OSError("original stop error")
    allow_native, native_closed = threading.Event(), threading.Event()
    try:
        with admit_daemon_instance(
            "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
        ) as host:
            runtime.start(tmp_path / "cache.json")
            server, owner = runtime.server, runtime._retirement
            held.start(server)
            original_close, original_wait = server.close, owner.closed.wait
            closes, interrupts = [], []

            def close():
                closes.append(True)
                if len(closes) == 1:
                    raise initial
                assert allow_native.wait(10)
                original_close()
                native_closed.set()

            def wait(timeout):
                interrupts.append(True)
                if len(interrupts) <= 2:
                    if len(interrupts) == 2:
                        allow_native.set()
                    raise KeyboardInterrupt("later interrupt")
                return original_wait(timeout)

            monkeypatch.setattr(server, "close", close)
            monkeypatch.setattr(owner.closed, "wait", wait)
            with pytest.raises(OSError) as raised:
                runtime.shutdown()
            assert raised.value is initial
            assert len(interrupts) >= 2
            assert native_closed.is_set()
            assert server.active_handler_count() == 1
        assert record_state(host.receipt) == "running"
    finally:
        allow_native.set()
        held.close()
        runtime.shutdown()
    assert owner.settled.wait(10)
    assert record_state(host.receipt) == "finished"


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_partial_reaper_start_keeps_concrete_native_handler_owned(tmp_path, monkeypatch, error_type):
    held = HeldObservation(monkeypatch)
    runtime = mux_link.InProcessRuntime()
    start_thread = threading.Thread.start

    def start(thread):
        if thread.name == "work-coalescing-singleton-reaper":
            held.start(runtime.server)
            raise error_type("partial native start")
        return start_thread(thread)

    monkeypatch.setattr(threading.Thread, "start", start)
    try:
        with admit_daemon_instance(
            "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
        ) as host:
            if error_type is KeyboardInterrupt:
                with pytest.raises(KeyboardInterrupt):
                    runtime.start(tmp_path / "cache.json")
            else:
                runtime.start(tmp_path / "cache.json")
            owner = runtime._retirement
            server, cache = owner.server, owner.cache
            assert runtime.server is runtime.cache is None
            assert owner.closed.is_set()
            assert cache._closed
            assert server._serve_started and not server._reap_started
            assert not server._serve_thread.is_alive()
            assert server.active_handler_count() == 1
        assert record_state(host.receipt) == "running"
    finally:
        held.close()
        runtime.shutdown()
    assert owner.settled.wait(10)
    assert record_state(host.receipt) == "finished"


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_pre_socket_start_failure_fences_cache_and_settles_owner(tmp_path, monkeypatch, error_type):
    runtime = mux_link.InProcessRuntime()

    def fail(*args, **kwargs):
        raise error_type("no server constructed")

    monkeypatch.setattr(mux_link, "start_server", fail)
    with admit_daemon_instance(
        "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
    ) as host:
        if error_type is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt):
                runtime.start(tmp_path / "cache.json")
        else:
            runtime.start(tmp_path / "cache.json")
        owner = runtime._retirement
        assert owner.server is None and owner.cache._closed
        assert runtime.server is runtime.cache is None
    assert owner.settled.wait(10)
    assert record_state(host.receipt) == "finished"


def test_unknown_handler_count_is_not_native_settlement(tmp_path, monkeypatch):
    runtime = mux_link.InProcessRuntime()
    try:
        with admit_daemon_instance(
            "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
        ) as host:
            runtime.start(tmp_path / "cache.json")
            server, owner = runtime.server, runtime._retirement
            actual_count = server.active_handler_count
            monkeypatch.setattr(server, "active_handler_count", lambda: None)
            runtime.shutdown()
        assert owner.closed.is_set()
        assert not owner.settled.wait(0.1)
        assert record_state(host.receipt) == "running"
    finally:
        monkeypatch.setattr(server, "active_handler_count", actual_count)
        runtime.shutdown()
    assert actual_count() == 0
    assert owner.settled.wait(10)
    assert record_state(host.receipt) == "finished"


def test_zero_handlers_cannot_settle_a_failed_intake_close(tmp_path, monkeypatch):
    runtime = mux_link.InProcessRuntime()
    allow_close = threading.Event()
    monkeypatch.setattr(mux_link, "_NATIVE_CLOSE_WAIT_S", 0.05)
    try:
        with admit_daemon_instance(
            "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
        ) as host:
            runtime.start(tmp_path / "cache.json")
            server, owner = runtime.server, runtime._retirement
            original = server.close

            def close():
                if not allow_close.is_set():
                    raise OSError("intake close still unavailable")
                original()

            monkeypatch.setattr(server, "close", close)
            with pytest.raises(OSError, match="intake close still unavailable"):
                runtime.shutdown()
        assert server.active_handler_count() == 0
        assert server._serve_thread.is_alive()
        assert not owner.closed.is_set()
        assert not owner.settled.is_set()
        assert record_state(host.receipt) == "running"
    finally:
        allow_close.set()
    assert owner.settled.wait(10)
    assert not server._serve_thread.is_alive()
    assert record_state(host.receipt) == "finished"


def test_cache_lock_fence_waits_without_joining_the_handler_that_owns_it(tmp_path, monkeypatch):
    runtime = mux_link.InProcessRuntime()
    entered, release = threading.Event(), threading.Event()
    result = {}
    original = mux_link.ManagedMuxCache._persist_locked

    def persist(cache):
        entered.set()
        assert release.wait(15)
        original(cache)

    monkeypatch.setattr(mux_link.ManagedMuxCache, "_persist_locked", persist)
    with admit_daemon_instance(
        "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
    ) as host:
        runtime.start(tmp_path / "cache.json")
        server, cache, owner = runtime.server, runtime.cache, runtime._retirement
        address, _, port = server.rendezvous()["endpoint"].rpartition(":")

        def push():
            result["response"] = client.request(
                address, int(port), server.token, kind=mux_link.KIND, key="inside-cache-lock",
                payload={"project": "test", "worktree_id": "wt", "mux_session": "test-wt", "mapping_revision": 1},
                request_deadline_s=20,
            )

        caller = threading.Thread(target=push)
        shutdown = threading.Thread(target=runtime.shutdown)
        caller.start()
        try:
            assert entered.wait(10)
            shutdown.start()
            wait_for(lambda: not server._serve_thread.is_alive())
            assert shutdown.is_alive()
            assert server.active_handler_count() == 1
            assert record_state(host.receipt) == "running"
        finally:
            release.set()
            caller.join(10)
            if shutdown.ident is not None:
                shutdown.join(10)
            runtime.shutdown()
        assert not caller.is_alive() and not shutdown.is_alive()
        assert cache._closed
        assert result["response"] == {"applied": True, "revision": 1}
    assert owner.settled.wait(10)
    assert record_state(host.receipt) == "finished"


def test_shutdown_from_own_wire_handler_does_not_wait_for_itself(tmp_path, monkeypatch):
    runtime = mux_link.InProcessRuntime()
    observed = {}
    original = mux_link.ManagedMuxCache.apply_observation
    with admit_daemon_instance(
        "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
    ) as host:
        runtime.start(tmp_path / "cache.json")
        server, owner = runtime.server, runtime._retirement

        def apply(cache, payload):
            runtime.shutdown()
            observed.update(state=record_state(host.receipt), handlers=server.active_handler_count())
            return original(cache, payload)

        monkeypatch.setattr(mux_link.ManagedMuxCache, "apply_observation", apply)
        address, _, port = server.rendezvous()["endpoint"].rpartition(":")
        response = client.request(
            address, int(port), server.token, kind=mux_link.KIND, key="self-stop",
            payload={"project": "test", "worktree_id": "wt", "mux_session": "test-wt", "mapping_revision": 1},
            request_deadline_s=5,
        )
        assert observed == {"state": "running", "handlers": 1}
        assert response == {"applied": False, "reason": "closed"}
    assert owner.settled.wait(10)
    assert record_state(host.receipt) == "finished"


def test_restart_retains_the_previous_generation_until_its_real_handler_finishes(tmp_path, monkeypatch):
    held = HeldObservation(monkeypatch)
    runtime = mux_link.InProcessRuntime()
    try:
        with admit_daemon_instance(
            "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
        ) as host:
            runtime.start(tmp_path / "cache.json")
            previous, server = runtime._retirement, runtime.server
            held.start(server)
            runtime.shutdown()
            runtime.start(tmp_path / "cache.json")
            current = runtime._retirement
            assert current is not previous
            assert current.guard.receipt == previous.guard.receipt == host.receipt
            runtime.shutdown()
            assert current.settled.wait(10)
            assert server.active_handler_count() == 1
            assert not previous.settled.is_set()
        assert record_state(host.receipt) == "running"
    finally:
        held.close()
        runtime.shutdown()
    assert previous.settled.wait(10)
    assert record_state(host.receipt) == "finished"


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_retirement_owner_start_failure_precedes_cache_and_socket(tmp_path, monkeypatch, error_type):
    admit, start = mux_link.admit_daemon_instance, threading.Thread.start
    captured = {}

    def admission(*args, **kwargs):
        captured["guard"] = admit(*args, **kwargs)
        return captured["guard"]

    def launch(thread):
        if thread.name == "managed-mux-retirement":
            raise error_type("owner launch refused")
        return start(thread)

    monkeypatch.setattr(mux_link, "admit_daemon_instance", admission)
    monkeypatch.setattr(threading.Thread, "start", launch)
    runtime = mux_link.InProcessRuntime()
    with admit_daemon_instance(
        "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
    ) as host:
        if error_type is KeyboardInterrupt:
            with pytest.raises(KeyboardInterrupt):
                runtime.start(tmp_path / "cache.json")
        else:
            runtime.start(tmp_path / "cache.json")
        assert runtime.server is runtime.cache is runtime._retirement is None
        assert captured["guard"].receipt == host.receipt
        assert captured["guard"]._closed
        assert not (tmp_path / "cache.json").exists()
    assert record_state(host.receipt) == "finished"


def test_native_thread_completion_is_required_even_when_close_returns(tmp_path, monkeypatch):
    runtime = mux_link.InProcessRuntime()
    allow_native = threading.Event()
    try:
        with admit_daemon_instance(
            "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
        ) as host:
            runtime.start(tmp_path / "cache.json")
            server, owner = runtime.server, runtime._retirement
            original = server.close

            def close():
                if allow_native.is_set():
                    original()

            monkeypatch.setattr(server, "close", close)
            runtime.shutdown()
        assert server.active_handler_count() == 0
        assert server._serve_thread.is_alive()
        assert not owner.closed.is_set()
        assert not owner.settled.is_set()
        assert record_state(host.receipt) == "running"
    finally:
        allow_native.set()
    assert owner.settled.wait(10)
    assert not server._serve_thread.is_alive()
    assert record_state(host.receipt) == "finished"


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_startup_error_survives_a_second_cleanup_error_after_native_retry(
    tmp_path, monkeypatch, error_type,
):
    runtime = mux_link.InProcessRuntime()
    held = HeldObservation(monkeypatch)
    primary = error_type("initial startup failure")
    secondary = OSError("later cleanup failure")
    original = mux_link.start_server
    captured = {}

    def construct(compute):
        server = original(compute)
        captured["server"] = server
        start, close = server.start, server.close
        attempts = []

        def start_then_fail():
            start()
            held.start(server)
            raise primary

        def close_then_retry():
            attempts.append(True)
            if len(attempts) == 1:
                raise secondary
            close()

        monkeypatch.setattr(server, "start", start_then_fail)
        monkeypatch.setattr(server, "close", close_then_retry)
        return server

    monkeypatch.setattr(mux_link, "start_server", construct)
    try:
        with admit_daemon_instance(
            "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
        ) as host:
            with pytest.raises(error_type) as raised:
                runtime.start(tmp_path / "cache.json")
            assert raised.value is primary
            owner, server = runtime._retirement, captured["server"]
            assert owner.closed.is_set() and owner.cache._closed
            assert not server._serve_thread.is_alive()
            assert server.active_handler_count() == 1
        assert record_state(host.receipt) == "running"
    finally:
        held.close()
        runtime.shutdown()
    assert owner.settled.wait(10)
    assert record_state(host.receipt) == "finished"


@pytest.mark.parametrize("error_type", [RuntimeError, KeyboardInterrupt])
def test_actual_accept_loop_retires_when_start_is_interrupted_before_flag_commit(
    tmp_path, monkeypatch, error_type,
):
    runtime = mux_link.InProcessRuntime()
    held = HeldObservation(monkeypatch)
    original_start = threading.Thread.start

    def start(thread):
        result = original_start(thread)
        if thread.name == "work-coalescing-singleton":
            assert runtime.server._serve_running.wait(10)
            held.start(runtime.server)
            raise error_type("native start completed before flag commit")
        return result

    monkeypatch.setattr(threading.Thread, "start", start)
    try:
        with admit_daemon_instance(
            "status-monitor", runtime_root=tmp_path, source=mux_link.__file__, purpose="production",
        ) as host:
            if error_type is KeyboardInterrupt:
                with pytest.raises(KeyboardInterrupt):
                    runtime.start(tmp_path / "cache.json")
            else:
                runtime.start(tmp_path / "cache.json")
            owner = runtime._retirement
            server = owner.server
            assert not server._serve_started
            assert server._serve_running.is_set()
            assert not server._serve_thread.is_alive()
            assert owner.closed.is_set() and owner.cache._closed
            assert server.active_handler_count() == 1
        assert record_state(host.receipt) == "running"
    finally:
        held.close()
        if server._serve_thread.is_alive():
            server._server.shutdown()
            server._serve_thread.join(10)
        runtime.shutdown()
    assert owner.settled.wait(10)
    assert record_state(host.receipt) == "finished"
