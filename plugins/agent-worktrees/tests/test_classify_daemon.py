"""Tests for the classify_daemon scaffolding (plugin-process-hygiene #2323).

These exercise the wire wrappers in isolation -- no ``__main__.py``
integration exists yet, per the module's own docstring.
"""

from __future__ import annotations

import threading
import time

from agent_worktrees import classify_daemon


def _endpoint_dict(server) -> dict:
    return classify_daemon.rendezvous_fields(server)


def test_rendezvous_fields_are_namespaced_and_parseable():
    server = classify_daemon.start_server(lambda kind, payload: {"ok": True})
    server.start()
    try:
        fields = _endpoint_dict(server)
        assert set(fields) == {
            "classify_transport",
            "classify_endpoint",
            "classify_token",
            "classify_generation",
        }
        endpoint = classify_daemon.endpoint_from_rendezvous(fields)
        assert endpoint is not None
        host, port, token = endpoint
        assert host == "127.0.0.1"
        assert isinstance(port, int) and port > 0
        assert token == fields["classify_token"]
    finally:
        server.close()


def test_endpoint_from_rendezvous_rejects_malformed_or_absent_data():
    assert classify_daemon.endpoint_from_rendezvous(None) is None
    assert classify_daemon.endpoint_from_rendezvous({}) is None
    assert classify_daemon.endpoint_from_rendezvous({"classify_endpoint": "bad"}) is None
    assert (
        classify_daemon.endpoint_from_rendezvous(
            {"classify_endpoint": "127.0.0.1:9", "classify_token": ""}
        )
        is None
    )


def test_classify_via_daemon_uses_fallback_when_no_lock_data():
    calls = {"fallback": 0}

    def fallback():
        calls["fallback"] += 1
        return {"from": "fallback"}

    result = classify_daemon.classify_via_daemon(
        None, key="proj", payload={"project": "proj"}, fallback=fallback
    )
    assert result == {"from": "fallback"}
    assert calls["fallback"] == 1


def test_classify_via_daemon_uses_live_daemon_when_reachable():
    server = classify_daemon.start_server(
        lambda kind, payload: {"project": payload["project"], "state": "CLEAN"}
    )
    server.start()
    try:
        lock_data = _endpoint_dict(server)
        result = classify_daemon.classify_via_daemon(
            lock_data,
            key="proj-a",
            payload={"project": "proj-a"},
            fallback=lambda: {"from": "fallback"},
        )
        assert result == {"project": "proj-a", "state": "CLEAN"}
    finally:
        server.close()


def test_classify_via_daemon_falls_back_when_daemon_exceeds_deadline():
    gate = threading.Event()  # never set

    server = classify_daemon.start_server(lambda kind, payload: gate.wait(timeout=5) and {})
    server.start()
    try:
        lock_data = _endpoint_dict(server)
        result = classify_daemon.classify_via_daemon(
            lock_data,
            key="slow-proj",
            payload={"project": "slow-proj"},
            fallback=lambda: {"from": "fallback"},
            request_deadline_s=0.2,
        )
        assert result == {"from": "fallback"}
    finally:
        gate.set()
        server.close()


def test_two_concurrent_callers_for_the_same_project_coalesce():
    calls = []
    gate = threading.Event()

    def compute(kind, payload):
        calls.append(payload["project"])
        gate.wait(timeout=2)
        return {"project": payload["project"], "state": "CLEAN"}

    server = classify_daemon.start_server(compute)
    server.start()
    try:
        lock_data = _endpoint_dict(server)
        results = [None, None]

        def call(idx):
            results[idx] = classify_daemon.classify_via_daemon(
                lock_data,
                key="shared-proj",
                payload={"project": "shared-proj"},
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

        assert calls == ["shared-proj"]  # only computed once
        assert results[0] == results[1] == {"project": "shared-proj", "state": "CLEAN"}
    finally:
        server.close()
