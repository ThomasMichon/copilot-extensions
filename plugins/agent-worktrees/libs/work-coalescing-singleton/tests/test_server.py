"""Unit tests for CoalescingServer: request coalescing, ref-count/linger, reap."""

from __future__ import annotations

import threading
import time

import pytest

from work_coalescing_singleton.server import CoalescingServer, Unavailable


def _make_server(**kwargs) -> CoalescingServer:
    kwargs.setdefault("linger_seconds", 0.15)
    kwargs.setdefault("subscriber_ttl", 0.2)
    kwargs.setdefault("reap_interval", 0.05)
    return CoalescingServer(lambda kind, payload: {"kind": kind, **payload}, **kwargs)


def test_identical_key_coalesces_into_one_execution():
    calls = []
    gate = threading.Event()

    def compute(kind, payload):
        calls.append(payload["n"])
        gate.wait(timeout=2)
        return {"echo": payload["n"]}

    server = CoalescingServer(compute, linger_seconds=0.1)
    try:
        results = {}

        def owner():
            results["owner"] = server.handle_request("k", "same-key", {"n": 1}, time.time() + 5)

        t = threading.Thread(target=owner)
        t.start()
        time.sleep(0.1)  # let the owner grab the in-flight slot before the joiner arrives

        joiner_result = None

        def joiner():
            nonlocal joiner_result
            joiner_result = server.handle_request("k", "same-key", {"n": 2}, time.time() + 5)

        j = threading.Thread(target=joiner)
        j.start()
        time.sleep(0.1)
        gate.set()
        t.join(timeout=2)
        j.join(timeout=2)

        assert calls == [1]  # only the owner's payload was ever computed
        assert results["owner"] == {"echo": 1}
        assert joiner_result == {"echo": 1}  # the joiner got the owner's result, not its own
    finally:
        server.close()


def test_distinct_keys_never_coalesced():
    calls = []

    def compute(kind, payload):
        calls.append(payload["n"])
        return {"n": payload["n"]}

    server = CoalescingServer(compute, linger_seconds=0.1)
    try:
        r1 = server.handle_request("k", "key-1", {"n": 1}, time.time() + 5)
        r2 = server.handle_request("k", "key-2", {"n": 2}, time.time() + 5)
        assert sorted(calls) == [1, 2]
        assert r1 == {"n": 1}
        assert r2 == {"n": 2}
    finally:
        server.close()


def test_expired_deadline_raises_unavailable_without_blocking_owner():
    gate = threading.Event()

    def compute(kind, payload):
        gate.wait(timeout=2)
        return {"ok": True}

    server = CoalescingServer(compute, linger_seconds=0.1)
    try:
        owner_result = {}

        def owner():
            owner_result["r"] = server.handle_request("k", "slow", {}, time.time() + 5)

        t = threading.Thread(target=owner)
        t.start()
        time.sleep(0.1)

        with pytest.raises(Unavailable):
            server.handle_request("k", "slow", {}, time.time() + 0.05)

        gate.set()
        t.join(timeout=2)
        assert owner_result["r"] == {"ok": True}
    finally:
        server.close()


def test_compute_error_is_raised_to_every_joiner():
    gate = threading.Event()

    def compute(kind, payload):
        gate.wait(timeout=2)
        raise RuntimeError("boom")

    server = CoalescingServer(compute, linger_seconds=0.1)
    try:
        owner_exc = {}

        def owner():
            try:
                server.handle_request("k", "err", {}, time.time() + 5)
            except RuntimeError as exc:
                owner_exc["e"] = exc

        t = threading.Thread(target=owner)
        t.start()
        time.sleep(0.1)

        joiner_exc = {}

        def joiner():
            try:
                server.handle_request("k", "err", {}, time.time() + 5)
            except RuntimeError as exc:
                joiner_exc["e"] = exc

        j = threading.Thread(target=joiner)
        j.start()
        time.sleep(0.1)
        gate.set()
        t.join(timeout=2)
        j.join(timeout=2)

        assert str(owner_exc["e"]) == "boom"
        assert str(joiner_exc["e"]) == "boom"
    finally:
        server.close()


def test_refcount_linger_fires_only_after_last_release():
    idle = threading.Event()
    server = _make_server(on_idle=idle.set)
    try:
        server.subscribe("a")
        server.subscribe("b")
        server.release("a")
        assert not idle.wait(timeout=0.3)  # "b" still subscribed -- must not go idle
        assert server.subscriber_count() == 1

        server.release("b")
        assert idle.wait(timeout=1.0)  # linger expired with zero subscribers
    finally:
        server.close()


def test_new_subscribe_cancels_pending_linger():
    idle = threading.Event()
    server = _make_server(on_idle=idle.set, linger_seconds=0.2)
    try:
        server.subscribe("a")
        server.release("a")
        time.sleep(0.05)  # linger started but not yet expired
        server.subscribe("b")  # must cancel it
        assert not idle.wait(timeout=0.35)
        assert server.subscriber_count() == 1
    finally:
        server.close()


def test_liveness_reap_drops_stale_subscriber_and_goes_idle():
    idle = threading.Event()
    server = _make_server(on_idle=idle.set, subscriber_ttl=0.1, reap_interval=0.05)
    server.start()  # the reap loop only runs once started
    try:
        server.subscribe("crashed-client")
        # No release ever sent -- simulates a crash. The reaper must drop it
        # on its own and then start (and let expire) the linger.
        assert idle.wait(timeout=2.0)
        assert server.subscriber_count() == 0
    finally:
        server.close()


def test_touch_keeps_a_fire_and_forget_caller_alive():
    idle = threading.Event()
    server = _make_server(on_idle=idle.set, subscriber_ttl=0.3, reap_interval=0.05)
    server.start()  # the reap loop only runs once started
    try:
        deadline = time.time() + 1.0
        while time.time() < deadline:
            server.touch("poller")
            time.sleep(0.05)
        assert not idle.is_set()  # kept alive throughout by repeated touch()
    finally:
        server.close()
