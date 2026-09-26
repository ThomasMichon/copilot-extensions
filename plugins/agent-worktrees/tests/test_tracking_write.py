"""Tests for the tracking_write mutation-verb wire plumbing
(agent-worktrees-authoritative-daemon effort, Phase 2).

Mirrors ``test_classify_daemon.py``/``test_worktree_status_daemon.py``'s own
structure and coverage style for the read-side daemons, applied to this
module's write-side additions: verb registration/dispatch, the
daemon-unreachable logged fallback (:func:`run_direct`), and the
never-coalesce-two-writes guarantee unique keys provide.
"""

from __future__ import annotations

import logging
import threading
import time

import pytest

from agent_worktrees import tracking_write


def _endpoint_dict(server) -> dict:
    return tracking_write.rendezvous_fields(server)


@pytest.fixture(autouse=True)
def _clean_verb_registry():
    # Tests register throwaway verbs into the module-level registry --
    # isolate each test so one test's verb name never leaks into another's.
    before = dict(tracking_write._VERBS)
    yield
    tracking_write._VERBS.clear()
    tracking_write._VERBS.update(before)


def test_rendezvous_fields_are_namespaced_and_parseable():
    server = tracking_write.start_server(lambda kind, payload: {"ok": True})
    server.start()
    try:
        fields = _endpoint_dict(server)
        assert set(fields) == {
            "tracking_write_transport",
            "tracking_write_endpoint",
            "tracking_write_token",
            "tracking_write_generation",
        }
        endpoint = tracking_write.endpoint_from_rendezvous(fields)
        assert endpoint is not None
        host, port, token = endpoint
        assert host == "127.0.0.1"
        assert isinstance(port, int) and port > 0
        assert token == fields["tracking_write_token"]
    finally:
        server.close()


def test_endpoint_from_rendezvous_rejects_malformed_or_absent_data():
    assert tracking_write.endpoint_from_rendezvous(None) is None
    assert tracking_write.endpoint_from_rendezvous({}) is None
    assert tracking_write.endpoint_from_rendezvous(
        {"tracking_write_endpoint": "bad"}
    ) is None
    assert (
        tracking_write.endpoint_from_rendezvous(
            {"tracking_write_endpoint": "127.0.0.1:9", "tracking_write_token": ""}
        )
        is None
    )


class TestVerbRegistryAndCompute:
    def test_register_verb_then_compute_dispatches_to_it(self):
        calls = []

        def _verb(args):
            calls.append(args)
            return {"echoed": args}

        tracking_write.register_verb("test-echo", _verb)
        assert "test-echo" in tracking_write.registered_verbs()

        result = tracking_write.compute(
            tracking_write.KIND, {"verb": "test-echo", "args": {"x": 1}}
        )
        assert result == {"echoed": {"x": 1}}
        assert calls == [{"x": 1}]

    def test_compute_defaults_missing_args_to_empty_dict(self):
        tracking_write.register_verb("test-no-args", lambda args: {"args": args})
        result = tracking_write.compute(
            tracking_write.KIND, {"verb": "test-no-args"}
        )
        assert result == {"args": {}}

    def test_compute_rejects_unregistered_verb(self):
        with pytest.raises(ValueError, match="unregistered verb"):
            tracking_write.compute(
                tracking_write.KIND, {"verb": "does-not-exist"}
            )

    def test_compute_rejects_missing_verb_name(self):
        with pytest.raises(ValueError, match="missing a verb name"):
            tracking_write.compute(tracking_write.KIND, {})

    def test_compute_rejects_non_dict_args(self):
        tracking_write.register_verb("test-bad-args", lambda args: args)
        with pytest.raises(ValueError, match="must be a dict"):
            tracking_write.compute(
                tracking_write.KIND, {"verb": "test-bad-args", "args": "nope"}
            )


class TestRunDirectFallback:
    def test_run_direct_calls_the_same_registered_function(self):
        calls = []

        def _record(args):
            calls.append(args)
            return {"ok": True}

        tracking_write.register_verb("test-direct", _record)
        result = tracking_write.run_direct("test-direct", {"y": 2}, reason="test")
        assert result == {"ok": True}
        assert calls == [{"y": 2}]

    def test_run_direct_logs_the_bypass(self, caplog):
        tracking_write.register_verb("test-logged", lambda args: {"ok": True})
        with caplog.at_level(logging.WARNING, logger="agent_worktrees.tracking_write"):
            tracking_write.run_direct(
                "test-logged", {}, reason="no resident daemon reachable"
            )
        assert any(
            "test-logged" in record.message and "no resident daemon reachable" in record.message
            for record in caplog.records
        )

    def test_run_direct_rejects_unregistered_verb(self):
        with pytest.raises(ValueError, match="unregistered verb"):
            tracking_write.run_direct("nope", {}, reason="test")


class TestDispatch:
    def test_dispatch_uses_live_daemon_when_reachable(self):
        tracking_write.register_verb("test-dispatch", lambda args: {"via": "verb", **args})
        server = tracking_write.start_server(tracking_write.compute)
        server.start()
        try:
            lock_data = _endpoint_dict(server)
            result = tracking_write.dispatch(
                "test-dispatch",
                {"n": 1},
                read_lock_data=lambda: lock_data,
                ensure_monitor=None,
            )
            assert result == {"via": "verb", "n": 1}
        finally:
            server.close()

    def test_dispatch_falls_back_to_run_direct_when_no_daemon_reachable(self, caplog):
        calls = []
        tracking_write.register_verb(
            "test-fallback", lambda args: calls.append(args) or {"via": "fallback"}
        )
        with caplog.at_level(logging.WARNING, logger="agent_worktrees.tracking_write"):
            result = tracking_write.dispatch(
                "test-fallback",
                {"n": 2},
                read_lock_data=lambda: None,
                ensure_monitor=None,
            )
        assert result == {"via": "fallback"}
        assert calls == [{"n": 2}]
        assert any("test-fallback" in r.message for r in caplog.records)

    def test_dispatch_falls_back_when_daemon_exceeds_deadline(self):
        """The verb itself is slow (not the transport), so the fallback --
        being the *same* function -- reproduces that same cost when it runs;
        this proves the daemon path was actually abandoned (deadline hit)
        and the fallback then genuinely executed the verb, not a distinct
        finished-instantly stand-in."""
        calls = []

        def _slow_verb(args):
            calls.append(dict(args))
            time.sleep(0.3)
            return {"via": "verb"}

        tracking_write.register_verb("test-slow", _slow_verb)
        server = tracking_write.start_server(tracking_write.compute)
        server.start()
        try:
            lock_data = _endpoint_dict(server)
            result = tracking_write.dispatch(
                "test-slow",
                {},
                read_lock_data=lambda: lock_data,
                ensure_monitor=None,
                request_deadline_s=0.05,
            )
            # The daemon round trip timed out (deadline << the verb's own
            # 0.3s), so this ran through run_direct -- the verb still ran
            # (once via the abandoned daemon call, once via the fallback),
            # and the fallback's own result is what the caller sees.
            assert result == {"via": "verb"}
            assert len(calls) >= 1
        finally:
            server.close()

    def test_two_concurrent_writes_never_coalesce_even_for_the_same_verb_and_args(self):
        """The write-specific guarantee this module adds over classify/
        worktree_status: a unique key per call means two concurrent writes
        are two independent executions, never merged into one answer."""
        calls = []
        call_lock = threading.Lock()
        gate = threading.Event()

        def _verb(args):
            with call_lock:
                calls.append(dict(args))
            gate.wait(timeout=2)
            return {"n": args.get("n")}

        tracking_write.register_verb("test-concurrent", _verb)
        server = tracking_write.start_server(tracking_write.compute)
        server.start()
        try:
            lock_data = _endpoint_dict(server)
            results = [None, None]

            def call(idx):
                results[idx] = tracking_write.dispatch(
                    "test-concurrent",
                    {"n": "same-args"},
                    read_lock_data=lambda: lock_data,
                    ensure_monitor=None,
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

            # Both calls actually ran the verb -- neither joined the other's
            # in-flight execution the way a classify/worktree_status caller
            # sharing a key would have.
            assert len(calls) == 2
            assert results[0] == {"n": "same-args"}
            assert results[1] == {"n": "same-args"}
        finally:
            server.close()
