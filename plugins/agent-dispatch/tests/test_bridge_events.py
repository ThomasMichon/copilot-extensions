from __future__ import annotations

import threading
import time

from agent_dispatch.bridge_events import (
    BridgeEventError,
    BridgeSubscription,
    SupervisorEventWake,
)


class _Stream:
    def __init__(self, events):
        self.events = list(events)
        self.closed = threading.Event()

    def __iter__(self):
        yield from self.events
        self.closed.wait()

    def close(self):
        self.closed.set()


class _Client:
    def __init__(self, streams):
        self.streams = list(streams)
        self.opened = []
        self.acks = []

    def open_events(self, subscriptions):
        self.opened.append(tuple(subscriptions))
        item = self.streams.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def acknowledge(self, subscription, last_id, continuity_id):
        self.acks.append((subscription, last_id, continuity_id))


class _BlockingClient(_Client):
    def __init__(self, stream):
        super().__init__([stream])
        self.open_started = threading.Event()
        self.release_open = threading.Event()

    def open_events(self, subscriptions):
        self.opened.append(tuple(subscriptions))
        self.open_started.set()
        self.release_open.wait()
        return self.streams.pop(0)


def _event(subscription, event_id, name="assistant.turn_end"):
    return {
        "event": "bridge_event",
        "data": {
            **subscription.as_payload(),
            "event_id": event_id,
            "event": name,
            "data": {},
            "continuity_id": "epoch-a",
        },
    }


def _wait_until(predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached")


def test_event_burst_coalesces_and_acks_highest_cursor():
    subscription = BridgeSubscription("host-a", "session-a", "lane-a")
    client = _Client(
        [_Stream([_event(subscription, 5), _event(subscription, 6)])]
    )
    wake = SupervisorEventWake(client)
    try:
        wake.update([subscription])
        assert wake.wait(1.0) is True
        _wait_until(lambda: wake.health["healthy"])
        assert wake.wait(0.0) is False

        wake.acknowledge()

        assert client.opened == [(subscription,)]
        assert client.acks == [(subscription, 6, "epoch-a")]
    finally:
        wake.close()


def test_subscription_change_replaces_one_aggregate_stream():
    first = BridgeSubscription("host-a", "session-a", "lane-a")
    second = BridgeSubscription("host-a", "session-b", "lane-a")
    first_stream = _Stream([])
    second_stream = _Stream([])
    client = _Client([first_stream, second_stream])
    wake = SupervisorEventWake(client)
    try:
        wake.update([first, second])
        _wait_until(lambda: len(client.opened) == 1)

        wake.update([second])
        _wait_until(lambda: len(client.opened) == 2)

        assert first_stream.closed.is_set()
        assert client.opened == [(first, second), (second,)]
    finally:
        wake.close()


def test_client_exit_wakes_once_before_reconnect_backoff():
    client = _Client(
        [
            BridgeEventError("bridge down"),
            BridgeEventError("still down"),
        ]
    )
    wake = SupervisorEventWake(client, reconnect_max=1.0)
    subscription = BridgeSubscription("host-a", "session-a", "lane-a")
    try:
        wake.update([subscription])

        assert wake.wait(0.5) is True
        assert wake.wait(0.05) is False
        assert wake.health["healthy"] is False
    finally:
        wake.close()


def test_control_event_degrades_and_wakes_reconciliation():
    subscription = BridgeSubscription("host-a", "session-a", "lane-a")
    stream = _Stream(
        [
            {
                "event": "bridge_control",
                "data": {
                    **subscription.as_payload(),
                    "code": "cursor_invalidated",
                    "action": "full_reconcile",
                },
            }
        ]
    )
    wake = SupervisorEventWake(_Client([stream]))
    try:
        wake.update([subscription])

        assert wake.wait(1.0) is True
        assert wake.health["healthy"] is False
        assert "cursor_invalidated" in wake.health["detail"]
    finally:
        wake.close()


def test_cursor_invalidation_acks_authoritative_head_after_reconciliation():
    subscription = BridgeSubscription("host-a", "session-a", "lane-a")
    stream = _Stream(
        [
            {
                "event": "bridge_control",
                "data": {
                    **subscription.as_payload(),
                    "code": "cursor_invalidated",
                    "action": "full_reconcile",
                    "head_id": 3,
                    "current_continuity_id": "epoch-b",
                },
            }
        ]
    )
    client = _Client([stream])
    wake = SupervisorEventWake(client)
    try:
        wake.update([subscription])
        assert wake.wait(1.0) is True

        wake.acknowledge()

        assert client.acks == [(subscription, 3, "epoch-b")]
    finally:
        wake.close()


def test_immediate_control_reconnects_wake_once_per_outage():
    subscription = BridgeSubscription("host-a", "session-a", "lane-a")
    control = {
        "event": "bridge_control",
        "data": {
            **subscription.as_payload(),
            "code": "carrier_unavailable",
            "action": "full_reconcile",
        },
    }
    client = _Client([_Stream([control]), _Stream([control])])
    wake = SupervisorEventWake(client, reconnect_max=1.0)
    try:
        wake.update([subscription])

        assert wake.wait(0.5) is True
        _wait_until(lambda: len(client.opened) == 2, timeout=1.5)
        assert wake.wait(0.1) is False
    finally:
        wake.close()


def test_close_during_open_closes_late_stream_without_publishing_it():
    subscription = BridgeSubscription("host-a", "session-a", "lane-a")
    stream = _Stream([])
    client = _BlockingClient(stream)
    wake = SupervisorEventWake(client)
    wake.update([subscription])
    assert client.open_started.wait(1.0)

    wake.close()
    client.release_open.set()

    assert stream.closed.wait(1.0)
    _wait_until(
        lambda: wake._thread is not None and not wake._thread.is_alive()
    )
