"""Tests for the worktree_status_daemon wire wrappers
(agent-worktrees-external-status-accelerator effort, Phase 2).

Mirrors ``test_classify_daemon.py``'s style, plus coverage for the
cache-aware ``build_cached_compute`` factory this daemon adds beyond the
classify precedent.
"""

from __future__ import annotations

import threading
import time

from agent_worktrees import worktree_status_daemon
from agent_worktrees.worktree_status_cache import WorktreeStatusCache


def _endpoint_dict(server) -> dict:
    return worktree_status_daemon.rendezvous_fields(server)


def test_coalescing_key_is_injective_despite_a_pipe_in_either_component():
    """Copilot review finding: a plain `f"{project}|{worktree_id}"` key is
    not injective -- neither component rejects `|` (unlike `/`, `\\`, `..`,
    NUL, which matter for the filesystem path each later builds), so
    `("a", "b|c")` and `("a|b", "c")` would coalesce onto the identical key,
    letting two different worktrees' concurrent requests join the same
    in-flight computation and each receive the other's bundle."""
    key1 = worktree_status_daemon.coalescing_key("a", "b|c")
    key2 = worktree_status_daemon.coalescing_key("a|b", "c")
    assert key1 != key2
    # Same pair, same key -- still deterministic/idempotent.
    assert worktree_status_daemon.coalescing_key("a", "b|c") == key1


def test_rendezvous_fields_are_namespaced_and_parseable():
    server = worktree_status_daemon.start_server(lambda kind, payload: {"ok": True})
    server.start()
    try:
        fields = _endpoint_dict(server)
        assert set(fields) == {
            "worktree_status_transport",
            "worktree_status_endpoint",
            "worktree_status_token",
            "worktree_status_generation",
        }
        endpoint = worktree_status_daemon.endpoint_from_rendezvous(fields)
        assert endpoint is not None
        host, port, token = endpoint
        assert host == "127.0.0.1"
        assert isinstance(port, int) and port > 0
        assert token == fields["worktree_status_token"]
    finally:
        server.close()


def test_endpoint_from_rendezvous_rejects_malformed_or_absent_data():
    assert worktree_status_daemon.endpoint_from_rendezvous(None) is None
    assert worktree_status_daemon.endpoint_from_rendezvous({}) is None
    assert (
        worktree_status_daemon.endpoint_from_rendezvous(
            {"worktree_status_endpoint": "bad"}
        )
        is None
    )
    assert (
        worktree_status_daemon.endpoint_from_rendezvous(
            {"worktree_status_endpoint": "127.0.0.1:9", "worktree_status_token": ""}
        )
        is None
    )


def test_status_via_daemon_uses_fallback_when_no_lock_data():
    calls = {"fallback": 0}

    def fallback():
        calls["fallback"] += 1
        return {"from": "fallback"}

    result = worktree_status_daemon.status_via_daemon(
        None, key="proj|wt1", payload={"project": "proj", "worktree_id": "wt1"}, fallback=fallback
    )
    assert result == {"from": "fallback"}
    assert calls["fallback"] == 1


def test_status_via_daemon_uses_live_daemon_when_reachable():
    server = worktree_status_daemon.start_server(
        lambda kind, payload: {"worktree_id": payload["worktree_id"], "state": "CLEAN"}
    )
    server.start()
    try:
        lock_data = _endpoint_dict(server)
        result = worktree_status_daemon.status_via_daemon(
            lock_data,
            key="proj|wt-a",
            payload={"project": "proj", "worktree_id": "wt-a"},
            fallback=lambda: {"from": "fallback"},
        )
        assert result == {"worktree_id": "wt-a", "state": "CLEAN"}
    finally:
        server.close()


def test_status_with_boot_uses_default_request_deadline(monkeypatch):
    expected_deadline = 8.0
    observed = {}

    def request(*args, **kwargs):
        observed["request_deadline_s"] = kwargs["request_deadline_s"]
        return {"state": "CLEAN"}

    def release(*args, **kwargs):
        observed["release_timeout"] = kwargs["timeout"]

    monkeypatch.setattr(worktree_status_daemon.wcs_client, "new_client_id", lambda: "client")
    monkeypatch.setattr(worktree_status_daemon.wcs_client, "request", request)
    monkeypatch.setattr(worktree_status_daemon.wcs_client, "release", release)

    result = worktree_status_daemon.status_with_boot(
        read_lock_data=lambda: {
            "worktree_status_endpoint": "127.0.0.1:1234",
            "worktree_status_token": "token",
        },
        ensure_monitor=None,
        key="proj|wt-a",
        payload={"project": "proj", "worktree_id": "wt-a"},
        fallback=lambda: {"from": "fallback"},
    )

    assert result == {"state": "CLEAN"}
    assert worktree_status_daemon.REQUEST_DEADLINE_S == expected_deadline
    assert observed == {
        "request_deadline_s": worktree_status_daemon.REQUEST_DEADLINE_S,
        "release_timeout": worktree_status_daemon.REQUEST_DEADLINE_S,
    }


def test_status_via_daemon_registers_and_releases_a_client_id():
    """Copilot review finding: unlike `status_with_boot`, this no-boot
    helper previously sent no `client_id`, so the server handler never
    `touch()`ed a subscriber. During a cold/slow compute the cache has no
    completed entry yet and `InProcessRuntime.has_active_demand()` sees no
    live subscriber either, letting the monitor's empty-strike shutdown
    close the runtime/cache while this request was still running."""
    observed_counts = []

    def _compute(kind, payload):
        # The server subscribes the caller's client_id before dispatching
        # to `compute` -- if this request never registers one, this count
        # would already be back to 0 mid-handler.
        observed_counts.append(server.subscriber_count())
        return {"worktree_id": payload["worktree_id"], "state": "CLEAN"}

    server = worktree_status_daemon.start_server(_compute)
    server.start()
    try:
        lock_data = _endpoint_dict(server)
        worktree_status_daemon.status_via_daemon(
            lock_data,
            key="proj|wt-a",
            payload={"project": "proj", "worktree_id": "wt-a"},
            fallback=lambda: {"from": "fallback"},
        )
        assert observed_counts == [1]
        # Released afterward -- never left dangling.
        assert server.subscriber_count() == 0
    finally:
        server.close()


def test_status_via_daemon_falls_back_when_daemon_exceeds_deadline():
    gate = threading.Event()  # never set

    server = worktree_status_daemon.start_server(
        lambda kind, payload: gate.wait(timeout=5) and {}
    )
    server.start()
    try:
        lock_data = _endpoint_dict(server)
        result = worktree_status_daemon.status_via_daemon(
            lock_data,
            key="proj|slow",
            payload={"project": "proj", "worktree_id": "slow"},
            fallback=lambda: {"from": "fallback"},
            request_deadline_s=0.2,
        )
        assert result == {"from": "fallback"}
    finally:
        gate.set()
        server.close()


def test_two_concurrent_callers_for_the_same_worktree_coalesce():
    calls = []
    gate = threading.Event()

    def compute(kind, payload):
        calls.append(payload["worktree_id"])
        gate.wait(timeout=2)
        return {"worktree_id": payload["worktree_id"], "state": "CLEAN"}

    server = worktree_status_daemon.start_server(compute)
    server.start()
    try:
        lock_data = _endpoint_dict(server)
        results = [None, None]

        def call(idx):
            results[idx] = worktree_status_daemon.status_via_daemon(
                lock_data,
                key="proj|shared-wt",
                payload={"project": "proj", "worktree_id": "shared-wt"},
                fallback=lambda: {"from": "fallback"},
                request_deadline_s=3.0,
            )

        t1 = threading.Thread(target=call, args=(0,))
        t1.start()
        time.sleep(0.2)
        t2 = threading.Thread(target=call, args=(1,))
        t2.start()
        time.sleep(0.2)
        gate.set()
        t1.join(timeout=3)
        t2.join(timeout=3)

        assert calls == ["shared-wt"]  # only computed once
        assert results[0] == results[1] == {"worktree_id": "shared-wt", "state": "CLEAN"}
    finally:
        server.close()


def test_a_different_worktree_key_never_waits_on_an_unrelated_in_flight_compute():
    gate_a = threading.Event()  # held open deliberately

    def compute(kind, payload):
        if payload["worktree_id"] == "wt-a":
            gate_a.wait(timeout=5)
        return {"worktree_id": payload["worktree_id"]}

    server = worktree_status_daemon.start_server(compute)
    server.start()
    try:
        lock_data = _endpoint_dict(server)

        def call_a():
            worktree_status_daemon.status_via_daemon(
                lock_data,
                key="proj|wt-a",
                payload={"project": "proj", "worktree_id": "wt-a"},
                fallback=lambda: {"from": "fallback"},
                request_deadline_s=5.0,
            )

        t_a = threading.Thread(target=call_a, daemon=True)
        t_a.start()
        time.sleep(0.2)  # wt-a is now in-flight, blocked on gate_a

        result_b = worktree_status_daemon.status_via_daemon(
            lock_data,
            key="proj|wt-b",
            payload={"project": "proj", "worktree_id": "wt-b"},
            fallback=lambda: {"from": "fallback"},
            request_deadline_s=1.0,
        )
        assert result_b == {"worktree_id": "wt-b"}
    finally:
        gate_a.set()
        server.close()


def test_build_cached_compute_rejects_path_traversal_in_project_or_worktree_id(tmp_path):
    """Copilot review finding: a client that can read the daemon's
    rendezvous token could otherwise submit a crafted id to make the
    monitor load a YAML outside the selected project's tracking dir."""
    cache = WorktreeStatusCache(tmp_path / "cache.sqlite3")
    assembled = []
    compute = worktree_status_daemon.build_cached_compute(
        cache, lambda project, worktree_id: assembled.append(1) or {"ok": True}
    )
    for bad_project, bad_worktree_id in (
        ("../evil", "wt1"),
        ("proj", "../../etc/passwd"),
        ("proj", "a/b"),
        ("proj", "a\\b"),
        ("proj", "wt\x00x"),
    ):
        try:
            compute(
                "worktree_status",
                {"project": bad_project, "worktree_id": bad_worktree_id},
            )
            raised = False
        except ValueError:
            raised = True
        assert raised, (bad_project, bad_worktree_id)
    assert not assembled  # never reached the fact-assembly function


def test_validated_refresh_rejects_path_traversal_for_the_sweep_path_too(tmp_path):
    """Copilot review finding: warm-restored SQLite rows are not identity-
    validated on load, so a corrupt/tampered row could otherwise let the
    background sweep bypass the request path's own validation and read
    outside the selected project's tracking directory."""
    assembled = []
    refresh = worktree_status_daemon.validated_refresh(
        lambda project, worktree_id: assembled.append(1) or {"ok": True}
    )
    try:
        refresh("../evil", "wt1")
        raised = False
    except ValueError:
        raised = True
    assert raised
    assert not assembled


def test_build_cached_compute_raises_on_missing_identity(tmp_path):
    cache = WorktreeStatusCache(tmp_path / "cache.sqlite3")
    compute = worktree_status_daemon.build_cached_compute(
        cache, lambda project, worktree_id: {"assembled": True}
    )
    try:
        compute("worktree_status", {})
        raised = False
    except ValueError:
        raised = True
    assert raised


def test_in_process_runtime_starts_and_shuts_down_cleanly(tmp_path):
    runtime = worktree_status_daemon.InProcessRuntime()
    runtime.start(tmp_path / "cache.sqlite3", lambda project, worktree_id: {"ok": True})
    try:
        assert runtime.server is not None
        assert runtime.cache is not None
        assert set(runtime.lock_extra()) == {
            "worktree_status_transport",
            "worktree_status_endpoint",
            "worktree_status_token",
            "worktree_status_generation",
        }
    finally:
        runtime.shutdown()
    assert runtime.cache._closed  # shutdown actually closed the cache


def test_in_process_runtime_has_active_demand_counts_a_live_subscriber(tmp_path):
    """Copilot review finding: `has_active_demand()` previously only
    consulted the cache's own demand-registration table, which only gets an
    entry once a request *completes* and publishes a result. A fresh cold
    request has a live `CoalescingServer` subscriber for the whole time its
    compute is in flight, with nothing in the cache yet -- missing that
    signal could let the monitor's idle-shutdown strikes close this runtime
    (and its cache) mid-compute, dropping the in-flight result."""
    runtime = worktree_status_daemon.InProcessRuntime()
    runtime.start(tmp_path / "cache.sqlite3", lambda project, worktree_id: {"ok": True})
    try:
        assert runtime.has_active_demand() is False  # nothing subscribed yet
        client_id = "probe-client"
        runtime.server.subscribe(client_id)
        try:
            assert runtime.has_active_demand() is True
        finally:
            runtime.server.release(client_id)
        assert runtime.has_active_demand() is False
    finally:
        runtime.shutdown()


def test_in_process_runtime_start_tears_down_partial_state_on_failure(tmp_path, monkeypatch):
    """Copilot review finding: a failure partway through `start()` (cache
    and server already up, sweep thread start raises) previously only
    cleared `self.server` -- leaking an open SQLite connection and a
    still-running server, while a non-``None`` `self.cache` could keep
    `has_active_demand()` reporting activity for a runtime that no longer
    actually serves. `start()` must tear everything back down via the same
    `shutdown()` path a normal stop uses."""

    def _boom(*args, **kwargs):
        raise RuntimeError("sweep thread start failed")

    monkeypatch.setattr(worktree_status_daemon, "start_sweep_thread", _boom)

    runtime = worktree_status_daemon.InProcessRuntime()
    runtime.start(tmp_path / "cache.sqlite3", lambda project, worktree_id: {"ok": True})

    assert runtime.server is None
    assert runtime.cache is None
    assert runtime.lock_extra() == {}
    assert runtime.has_active_demand() is False
    # A second start() must work cleanly against the freshly reset state --
    # not raise because `self._sweep_stop` was left set from the failed
    # attempt (a stale set Event would make the new sweep thread exit
    # immediately on its very first wait()).
    monkeypatch.undo()
    runtime.start(tmp_path / "cache2.sqlite3", lambda project, worktree_id: {"ok": True})
    try:
        assert runtime.server is not None
    finally:
        runtime.shutdown()


def test_build_cached_compute_serves_cache_hits_without_reassembling(tmp_path):
    cache = WorktreeStatusCache(tmp_path / "cache.sqlite3")
    calls = []

    def assemble(project, worktree_id):
        calls.append((project, worktree_id))
        return {"project": project, "worktree_id": worktree_id, "n": len(calls)}

    compute = worktree_status_daemon.build_cached_compute(cache, assemble)
    payload = {"project": "proj", "worktree_id": "wt1"}
    first = compute("worktree_status", payload)
    second = compute("worktree_status", payload)
    assert first == second == {"project": "proj", "worktree_id": "wt1", "n": 1}
    assert len(calls) == 1


def test_build_cached_compute_honors_force_flag(tmp_path):
    cache = WorktreeStatusCache(tmp_path / "cache.sqlite3")
    calls = []

    def assemble(project, worktree_id):
        calls.append(1)
        return {"n": len(calls)}

    compute = worktree_status_daemon.build_cached_compute(cache, assemble)
    compute("worktree_status", {"project": "proj", "worktree_id": "wt1"})
    forced = compute(
        "worktree_status", {"project": "proj", "worktree_id": "wt1", "force": True}
    )
    assert forced == {"n": 2}
    assert len(calls) == 2


def test_sweep_thread_stops_promptly_after_stop_event_set(tmp_path):
    """The sweep thread must be joinable so shutdown can wait for it to
    finish before closing the cache (Copilot review finding: closing the
    cache's SQLite connection while the sweep thread is mid-`_persist`
    races a successor monitor's own writer)."""
    cache = WorktreeStatusCache(tmp_path / "cache.sqlite3")
    stop = threading.Event()
    thread = worktree_status_daemon.start_sweep_thread(
        cache, lambda project, worktree_id: {}, interval=0.05, stop_event=stop
    )
    assert thread.is_alive()
    stop.set()
    thread.join(timeout=2)
    assert not thread.is_alive()
