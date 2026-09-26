"""End-to-end wire + client tests: real TCP against a CoalescingServer."""

from __future__ import annotations

import threading
import time

from work_coalescing_singleton import client
from work_coalescing_singleton.server import CoalescingServer


def _endpoint(server: CoalescingServer) -> tuple[str, int, str]:
    rv = server.rendezvous()
    host, port_s = rv["endpoint"].split(":")
    return host, int(port_s), rv["token"]


def test_active_handler_count_is_nonzero_during_a_slow_request_and_zero_after():
    """copilot-extensions#3798: `active_handler_count()` must reflect a
    connection accepted and dispatched to its handler thread even while
    that handler is mid-execution (not just while a client holds a
    subscribe lease) -- and drop back to zero once the handler returns."""
    release_gate = threading.Event()
    seen_count_during_compute: list[int] = []

    def _slow_compute(kind, payload):
        seen_count_during_compute.append(server.active_handler_count())
        release_gate.wait(timeout=5)
        return {"ok": True}

    server = CoalescingServer(_slow_compute, linger_seconds=0.2)
    server.start()
    try:
        assert server.active_handler_count() == 0
        host, port, token = _endpoint(server)
        cid = client.new_client_id()
        t = threading.Thread(
            target=client.request,
            kwargs={
                "host": host, "port": port, "token": token,
                "kind": "slow", "key": "k1", "payload": {},
                "request_deadline_s": 5.0, "client_id": cid,
            },
        )
        t.start()
        try:
            deadline = time.time() + 2.0
            while not seen_count_during_compute and time.time() < deadline:
                time.sleep(0.02)
            assert seen_count_during_compute == [1]
            assert server.active_handler_count() == 1
        finally:
            release_gate.set()
            t.join(timeout=5)
        deadline = time.time() + 2.0
        while server.active_handler_count() != 0 and time.time() < deadline:
            time.sleep(0.02)
        assert server.active_handler_count() == 0
    finally:
        server.close()


def test_full_roundtrip_subscribe_request_release():
    server = CoalescingServer(
        lambda kind, payload: {"doubled": payload["n"] * 2}, linger_seconds=0.2
    )
    server.start()
    try:
        host, port, token = _endpoint(server)
        cid = client.new_client_id()
        client.subscribe(host, port, token, cid)
        assert server.subscriber_count() == 1

        result = client.request(
            host, port, token, kind="double", key="k1", payload={"n": 21},
            request_deadline_s=2.0, client_id=cid,
        )
        assert result == {"doubled": 42}

        client.release(host, port, token, cid)
        assert server.subscriber_count() == 0
    finally:
        server.close()


def test_concurrent_clients_share_one_coalesced_execution_over_the_wire():
    calls = []
    gate = threading.Event()

    def compute(kind, payload):
        calls.append(1)
        gate.wait(timeout=2)
        return {"n": payload["n"]}

    server = CoalescingServer(compute, linger_seconds=0.2)
    server.start()
    try:
        host, port, token = _endpoint(server)
        results: list[dict] = [None, None]  # type: ignore[list-item]

        def call(idx, n):
            results[idx] = client.request(
                host, port, token, kind="k", key="shared", payload={"n": n},
                request_deadline_s=3.0,
            )

        t1 = threading.Thread(target=call, args=(0, 1))
        t1.start()
        time.sleep(0.2)
        t2 = threading.Thread(target=call, args=(1, 2))
        t2.start()
        time.sleep(0.2)
        gate.set()
        t1.join(timeout=3)
        t2.join(timeout=3)

        assert len(calls) == 1  # the second caller joined instead of recomputing
        assert results[0] == results[1] == {"n": 1}
    finally:
        server.close()


def test_wrong_token_is_ignored_not_crashed():
    server = CoalescingServer(lambda kind, payload: {"ok": True}, linger_seconds=0.2)
    server.start()
    try:
        host, port, _real_token = _endpoint(server)
        try:
            client.request(
                host, port, "wrong-token", kind="k", key="k1", payload={},
                request_deadline_s=0.5,
            )
        except client.DaemonUnavailable:
            pass
        else:
            raise AssertionError("expected DaemonUnavailable for a bad token")
    finally:
        server.close()


def test_call_with_fallback_uses_fallback_when_no_daemon_reachable():
    calls = {"boot": 0, "fallback": 0}

    def dial():
        return None  # never discoverable in this test

    def boot():
        calls["boot"] += 1

    def fallback():
        calls["fallback"] += 1
        return {"inline": True}

    result = client.call_with_fallback(
        dial=dial, boot=boot, boot_wait_s=0.2,
        kind="k", key="k1", payload={},
        request_deadline_s=0.2, fallback=fallback,
        poll_interval_s=0.02,
    )
    assert result == {"inline": True}
    assert calls["boot"] == 1
    assert calls["fallback"] == 1


def test_call_with_fallback_uses_daemon_when_dial_succeeds():
    server = CoalescingServer(lambda kind, payload: {"from": "daemon"}, linger_seconds=0.2)
    server.start()
    try:
        host, port, token = _endpoint(server)

        result = client.call_with_fallback(
            dial=lambda: (host, port, token),
            boot=None,
            boot_wait_s=1.0,
            kind="k", key="k1", payload={},
            request_deadline_s=1.0,
            fallback=lambda: {"from": "fallback"},
        )
        assert result == {"from": "daemon"}
    finally:
        server.close()


def test_call_with_fallback_falls_back_on_request_deadline_exceeded():
    gate = threading.Event()  # never set -- compute hangs past the deadline

    server = CoalescingServer(
        lambda kind, payload: gate.wait(timeout=5) and {}, linger_seconds=0.2
    )
    server.start()
    try:
        host, port, token = _endpoint(server)
        result = client.call_with_fallback(
            dial=lambda: (host, port, token),
            boot=None,
            boot_wait_s=1.0,
            kind="k", key="hang", payload={},
            request_deadline_s=0.2,
            fallback=lambda: {"from": "fallback"},
        )
        assert result == {"from": "fallback"}
    finally:
        gate.set()
        server.close()


def test_call_with_fallback_falls_back_when_dial_itself_raises():
    """Copilot review finding: ``call_with_fallback``'s own docstring
    promises it never raises past this call, but a caller-supplied
    ``dial``/``boot`` that itself raises (rather than returning ``None``)
    used to escape uncaught."""

    def _boom_dial():
        raise RuntimeError("transient rendezvous read failure")

    result = client.call_with_fallback(
        dial=_boom_dial,
        boot=None,
        boot_wait_s=0.2,
        kind="k", key="k1", payload={},
        request_deadline_s=0.2,
        fallback=lambda: {"inline": True},
    )
    assert result == {"inline": True}


def test_call_with_fallback_falls_back_when_boot_itself_raises():
    calls = {"dial": 0}

    def _dial():
        calls["dial"] += 1
        return None  # never discoverable, even after "boot"

    def _boom_boot():
        raise RuntimeError("daemon spawn failed")

    result = client.call_with_fallback(
        dial=_dial,
        boot=_boom_boot,
        boot_wait_s=0.1,
        kind="k", key="k1", payload={},
        request_deadline_s=0.2,
        fallback=lambda: {"inline": True},
        poll_interval_s=0.02,
    )
    assert result == {"inline": True}
    assert calls["dial"] >= 1
