"""Durable callback contract, including actual subprocess and disk boundaries."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from agent_procutil import no_window_kwargs

from agent_pull_requests.watch_contract import MERGED, PRSnapshot
from agent_pull_requests.watch_daemon import WatchDaemon, read_subscriptions_state
from agent_pull_requests.watch_notification import (
    ACKNOWLEDGED_NOTIFICATIONS as PROTOCOL, default_notify, event_payload,
)
from agent_pull_requests.watch_registry import WatchKey, WatchRegistry
from agent_pull_requests.watch_storage import write_subscriptions_state
from agent_pull_requests.watch_subscription import subscribe

pytestmark = pytest.mark.contract("agent_pull_requests.watch.acknowledged_delivery")


def wait(predicate, timeout=3):
    def ready():
        try:
            return predicate()
        except IndexError:
            return False

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if ready():
            return
        time.sleep(0.01)
    assert ready()


def spec(identity="one", **changes):
    return {
        "repo": "example/project", "number": 1, "subscriber_id": identity,
        "until": [MERGED], "notify": {"argv": [sys.executable, "consumer.py"]},
        "notification_protocol": PROTOCOL, **changes,
    }


@pytest.fixture
def make(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path))
    daemons = []

    def create(**kwargs):
        daemon = WatchDaemon(
            fetch=kwargs.pop("fetch", lambda *_: PRSnapshot(merged=True, pr_state="closed")),
            poll_interval=0.01, idle_exit=0.01, **kwargs,
        )
        daemons.append(daemon)
        return daemon

    yield create
    for daemon in reversed(daemons):
        daemon.close()


@pytest.mark.parametrize("code", [0, 7])
def test_actual_callback_stdin_and_exit_code(tmp_path, code):
    output = tmp_path / "callback.json"
    registry = WatchRegistry()
    registry.register(
        WatchKey("example/project", 1), "one", until=(MERGED,), acknowledged=True,
        notify={"argv": [
            sys.executable, "-c",
            "import pathlib,sys; pathlib.Path(sys.argv[1]).write_text(sys.stdin.read());"
            "sys.exit(int(sys.argv[2]))",
            str(output), str(code),
        ]},
    )
    event = registry.apply_snapshot(WatchKey("example/project", 1), PRSnapshot(merged=True))[0]
    expected = event_payload(event)
    assert default_notify(event) == code
    assert json.loads(output.read_text()) == expected
    assert expected["event_id"] and expected["registration_id"]
    assert expected["notification_protocol"] == PROTOCOL
    assert expected["transitions"] == ["merged"] and expected["merged"] is True


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "exception"])
def test_failed_callback_remains_pending_and_replays_after_restart(make, failure):
    payloads = []
    observed_disk = []

    def fail(event):
        payloads.append(json.loads(json.dumps(event_payload(event))))
        observed_disk.append(read_subscriptions_state())
        if failure == "timeout":
            raise subprocess.TimeoutExpired("redacted", 1)
        if failure == "exception":
            raise OSError("sensitive callback diagnostic must not be surfaced")
        return 5

    first = make(notify=fail)
    registered = first.compute("register", spec())
    assert registered["notification_protocol"] == PROTOCOL
    wait(lambda: first.status()["pending_deliveries"][0]["attempts"] == 1)
    first.close()
    saved = read_subscriptions_state()[0]
    assert observed_disk[0][0]["pending"]["payload"] == payloads[0]
    assert saved["pending"]["last_error"] == {
        "nonzero": "callback_nonzero", "timeout": "callback_timeout",
        "exception": "callback_error",
    }[failure]
    assert 0 < saved["pending"]["next_attempt"] - time.time() <= 1
    assert "sensitive" not in json.dumps(saved)

    def ack(event):
        payloads.append(json.loads(json.dumps(event_payload(event))))
        return 0

    second = make(notify=ack, fetch=lambda *_: pytest.fail("pending replay must not fetch"))
    wait(lambda: second.status()["subscribers"] == {})
    assert payloads == [saved["pending"]["payload"]] * 2
    assert read_subscriptions_state() == []


def test_local_ack_write_failure_replays_successful_remote_commit(make, monkeypatch):
    from agent_pull_requests import watch_daemon

    committed = []
    original_write = watch_daemon.write_subscriptions_state
    failed = threading.Event()

    def write(entries):
        if not entries and not failed.is_set():
            failed.set()
            raise OSError("local ACK write failed after remote commit")
        original_write(entries)

    monkeypatch.setattr(watch_daemon, "write_subscriptions_state", write)

    def ack(event):
        committed.append(event_payload(event).copy())
        return 0

    daemon = make(notify=ack)
    daemon.compute("register", spec())
    assert failed.wait(2)
    assert read_subscriptions_state()[0]["pending"]["payload"] == committed[0]
    wait(lambda: daemon.status()["subscribers"] == {})
    assert committed == [committed[0], committed[0]]
    assert daemon.status()["persistence_error"] is None


def test_state_write_outage_is_visible_and_never_delivers_before_persistence(make, monkeypatch):
    from agent_pull_requests import watch_daemon

    calls = []
    daemon = make(notify=lambda e: calls.append(e) or 0)
    original_write = watch_daemon.write_subscriptions_state

    def fail(entries):
        raise OSError("private filesystem diagnostic")

    monkeypatch.setattr(watch_daemon, "write_subscriptions_state", fail)
    with pytest.raises(OSError):
        daemon.compute("register", spec())
    assert daemon.status()["persistence_error"] == "state_write_failed"
    assert daemon.compute("health", {})["persistence_error"] == "state_write_failed"
    assert calls == []
    assert daemon.status()["subscribers"] == {}
    monkeypatch.setattr(watch_daemon, "write_subscriptions_state", original_write)
    daemon.compute("register", spec("other", number=2))
    wait(lambda: len(calls) == 1 and not daemon.status()["subscribers"])
    assert read_subscriptions_state() == []
    assert daemon.status()["persistence_error"] is None


def test_failed_replacement_preserves_original_registration(make, monkeypatch):
    from agent_pull_requests import watch_daemon

    daemon = make(fetch=lambda *_: PRSnapshot())
    first = daemon.compute("register", spec(timeout=60))
    key = WatchKey("example/project", 1)
    original = daemon._registry.subscriber(key, "one")

    def fail(entries):
        raise OSError("state unavailable")

    monkeypatch.setattr(watch_daemon, "write_subscriptions_state", fail)
    with pytest.raises(OSError):
        daemon.compute("register", spec(timeout=1))
    assert daemon._registry.subscriber(key, "one") is original
    assert read_subscriptions_state()[0]["registration_id"] == first["registration_id"]

def test_failed_cancellation_preserves_pending_registration(make, monkeypatch):
    from agent_pull_requests import watch_daemon

    entered, release = threading.Event(), threading.Event()

    def notify(event):
        entered.set()
        assert release.wait(3)
        return 1

    daemon = make(notify=notify)
    registered = daemon.compute("register", spec())
    assert entered.wait(2)
    key = WatchKey("example/project", 1)
    original = daemon._registry.subscriber(key, "one")
    saved = read_subscriptions_state()
    write = watch_daemon.write_subscriptions_state

    def fail(entries):
        raise OSError("cancel write unavailable")

    try:
        monkeypatch.setattr(watch_daemon, "write_subscriptions_state", fail)
        with pytest.raises(OSError):
            daemon.compute("unregister", {
                "repo": key.repo, "number": key.number, "subscriber_id": "one",
                "registration_id": registered["registration_id"],
            })
        assert daemon._registry.subscriber(key, "one") is original
        assert read_subscriptions_state() == saved
        assert daemon.status()["pending_deliveries"][0]["event_id"] == saved[0]["pending"]["event_id"]
        monkeypatch.setattr(watch_daemon, "write_subscriptions_state", write)
        assert daemon.compute("unregister", {
            "repo": key.repo, "number": key.number, "subscriber_id": "one",
            "registration_id": registered["registration_id"],
        })["unregistered"] is True
        assert read_subscriptions_state() == []
    finally:
        release.set()

@pytest.mark.parametrize("operation", ["register", "unregister"])
def test_post_replace_failure_retains_committed_memory_state(make, monkeypatch, operation):
    from agent_pull_requests import watch_daemon
    from agent_pull_requests.watch_storage import StateCommitUncertain

    daemon = make(fetch=lambda *_: PRSnapshot())
    old = daemon.compute("register", spec(timeout=60))
    write = watch_daemon.write_subscriptions_state

    def uncertain(entries):
        write(entries)
        raise StateCommitUncertain("directory sync failed")

    monkeypatch.setattr(watch_daemon, "write_subscriptions_state", uncertain)
    if operation == "register":
        result = daemon.compute("register", spec(timeout=20))
        assert result["ambiguous_registration"]
        memory = daemon._registry.subscriber(WatchKey("example/project", 1), "one")
        assert memory.registration_id != old["registration_id"]
        assert memory.registration_id == read_subscriptions_state()[0]["registration_id"]
    else:
        result = daemon.compute("unregister", {
            "repo": "example/project", "number": 1, "subscriber_id": "one",
            "registration_id": old["registration_id"],
        })
        assert result["ambiguous_cancellation"]
        assert daemon.status()["subscribers"] == {} and read_subscriptions_state() == []


@pytest.mark.parametrize("identity", [None, "", False, 1])
def test_explicit_malformed_cancellation_identity_never_disables_fence(make, identity):
    daemon = make(fetch=lambda *_: PRSnapshot())
    daemon.compute("register", spec(timeout=60))
    result = daemon.compute("unregister", {
        "repo": "example/project", "number": 1, "subscriber_id": "one",
        "registration_id": identity,
    })
    assert "error" in result
    assert daemon.status()["subscribers"] == {"example/project#1": ["one"]}


def test_cli_explicit_empty_cancellation_identity_is_forwarded(monkeypatch, capsys):
    from agent_pull_requests import __main__ as cli

    def request(kind, payload):
        if kind == "health":
            return {"capabilities": [PROTOCOL]}
        assert kind == "unregister"
        assert payload["registration_id"] == ""
        return {"error": "invalid cancellation registration identity"}

    monkeypatch.setattr(cli, "_watch_request", request)
    assert cli._cmd_watch_unsubscribe(SimpleNamespace(
        repo="example/project", number=1, subscriber_id="one", registration_id="", json=True,
    )) == 1
    assert "error" in json.loads(capsys.readouterr().out)

@pytest.mark.parametrize("supported", [True, False])
def test_fenced_cancellation_requires_capability_and_echo(monkeypatch, capsys, supported):
    from agent_pull_requests import __main__ as cli

    calls = []

    def request(kind, payload):
        calls.append(kind)
        if kind == "health":
            return {"capabilities": [PROTOCOL] if supported else []}
        return {"unregistered": True}

    monkeypatch.setattr(cli, "_watch_request", request)
    assert cli._cmd_watch_unsubscribe(SimpleNamespace(
        repo="example/project", number=1, subscriber_id="one", registration_id="old", json=True,
    )) == 1
    result = json.loads(capsys.readouterr().out)
    assert "error" in result
    assert calls == (["health", "unregister"] if supported else ["health"])
    if supported:
        assert result["ambiguous_cancellation"]


def test_boolean_pending_event_number_fails_closed():
    registry = WatchRegistry()
    key = WatchKey("example/project", 1)
    registry.register(key, "one", until=(MERGED,), acknowledged=True,
                      notify={"argv": ["consumer"]})
    registry.apply_snapshot(key, PRSnapshot(merged=True))
    entries = registry.snapshot_state()
    entries[0]["pending"]["payload"]["number"] = True
    with pytest.raises(ValueError, match="invalid persisted acknowledged subscription"):
        WatchRegistry().restore_state(entries)

@pytest.mark.parametrize("kind", ["register", "unregister"])
def test_mutation_requests_never_coalesce_different_subscribers(monkeypatch, kind):
    import work_coalescing_singleton
    from agent_pull_requests import __main__ as cli

    keys = []

    def call(**kwargs):
        keys.append(kwargs["key"])
        return {}

    monkeypatch.setattr(work_coalescing_singleton, "call_with_fallback", call)
    for identity in ("one", "two", "one"):
        cli._watch_request(kind, {"repo": "example/project", "number": 1, "subscriber_id": identity})
    assert len(set(keys)) == 3

def test_registration_transport_fallback_reports_ambiguous_mutation(monkeypatch):
    import work_coalescing_singleton
    from agent_pull_requests import __main__ as cli

    monkeypatch.setattr(work_coalescing_singleton, "call_with_fallback",
                        lambda **kwargs: kwargs["fallback"]())
    result = cli._watch_request("register", spec())
    assert result["ambiguous_registration"] is True and "registered" not in result
    cancelled = cli._watch_request("unregister", spec())
    assert cancelled["ambiguous_cancellation"] is True and "unregistered" not in cancelled

def test_watch_fetch_has_finite_subprocess_deadline(monkeypatch):
    from agent_pull_requests import __main__ as cli

    monkeypatch.setattr(cli, "_agent_worktrees_command", lambda: "owner-command")

    def run(*args, **kwargs):
        assert kwargs["timeout"] == 20
        raise subprocess.TimeoutExpired("owner-command", 20)

    monkeypatch.setattr(cli.subprocess, "run", run)
    with pytest.raises(subprocess.TimeoutExpired):
        cli._watch_github_snapshot("example/project", 1)


def test_absolute_deadline_alone_cannot_downgrade_corrupt_durable_state():
    entry = {
        "repo": "example/project", "number": 1, "subscriber_id": "one",
        "until": [MERGED], "notify": {}, "deadline_at": time.time() + 10,
    }
    with pytest.raises(ValueError, match="invalid persisted acknowledged subscription"):
        WatchRegistry().restore_state([entry])

def test_null_absolute_deadline_requires_null_remaining_timeout():
    registry = WatchRegistry()
    registry.register(WatchKey("example/project", 1), "one", until=(MERGED,),
                      acknowledged=True, notify={"argv": ["consumer"]})
    entries = registry.snapshot_state()
    entries[0]["remaining_timeout"] = 0
    with pytest.raises(ValueError, match="invalid persisted acknowledged subscription"):
        WatchRegistry().restore_state(entries)


def test_legacy_restored_notify_timeout_keeps_fixed_ceiling(monkeypatch):
    from agent_pull_requests import watch_notification

    def run(*args, **kwargs):
        assert kwargs["timeout"] == 30
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(watch_notification.subprocess, "run", run)
    registry = WatchRegistry()
    key = WatchKey("example/project", 1)
    registry.register(key, "legacy", until=(MERGED,), notify={"argv": ["consumer"], "timeout": 99999})
    event = registry.apply_snapshot(key, PRSnapshot(merged=True))[0]
    assert default_notify(event) == 0


def test_unchanged_snapshots_do_not_rewrite_registry(make, monkeypatch):
    from agent_pull_requests import watch_daemon

    writes, polls = [], []
    write = watch_daemon.write_subscriptions_state

    def record(entries):
        writes.append(entries)
        write(entries)

    def fetch(*_):
        polls.append(1)
        return PRSnapshot()

    monkeypatch.setattr(watch_daemon, "write_subscriptions_state", record)
    daemon = make(fetch=fetch)
    daemon.compute("register", spec())
    wait(lambda: len(polls) >= 4)
    daemon.close()
    assert len(writes) == 2
    assert writes[-1][0]["baseline"] is not None

def test_callback_burst_has_bounded_concurrency_and_no_lost_events(make):
    release = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0
    calls = []

    def notify(event):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            calls.append(event.subscriber.subscriber_id)
        try:
            assert release.wait(3)
            return 0
        finally:
            with lock:
                active -= 1

    daemon = make(notify=notify)
    try:
        for number in range(20):
            daemon.compute("register", spec(str(number)))
        wait(lambda: active == 8)
        assert len(daemon._deliveries.workers) == 8
        release.set()
        wait(lambda: len(calls) == 20 and not daemon.status()["subscribers"])
        assert peak == 8 and len(set(calls)) == 20
    finally:
        release.set()

def test_pending_only_key_retires_poller_and_scheduler_waits_for_retry(make):
    daemon = make(notify=lambda event: 1)
    daemon.compute("register", spec())
    wait(lambda: daemon.status()["pending_deliveries"][0]["attempts"] == 1)
    wait(lambda: not daemon._pollers)
    assert daemon._deliveries.scheduler.is_alive()
    attempts = daemon.status()["pending_deliveries"][0]["attempts"]
    time.sleep(0.1)
    assert daemon.status()["pending_deliveries"][0]["attempts"] == attempts

def test_scheduler_wait_is_bounded_for_oversized_retry_timestamp(make):
    registry = WatchRegistry()
    key = WatchKey("example/project", 1)
    registry.register(key, "one", until=(MERGED,), acknowledged=True,
                      notify={"argv": ["consumer"]})
    registry.apply_snapshot(key, PRSnapshot(merged=True))
    entries = registry.snapshot_state()
    entries[0]["pending"]["next_attempt"] = 1e100
    write_subscriptions_state(entries)
    daemon = make(notify=lambda event: pytest.fail("retry is not due"))
    time.sleep(0.1)
    assert daemon._deliveries.scheduler.is_alive()
    daemon.close()

def test_shutdown_drains_legacy_callbacks_queued_beyond_worker_limit(make):
    release = threading.Event()
    calls = []
    lock = threading.Lock()

    def notify(event):
        with lock:
            calls.append(event.subscriber.subscriber_id)
        assert release.wait(3)

    daemon = make(notify=notify)
    for number in range(20):
        registration = spec(str(number))
        del registration["notification_protocol"]
        daemon.compute("register", registration)
    wait(lambda: len(calls) == 8 and len(daemon._deliveries.legacy_queue) == 12)
    daemon.compute("shutdown", {})
    release.set()
    daemon.close()
    assert len(calls) == len(set(calls)) == 20
    assert not daemon._deliveries.legacy_queue

def test_already_fired_legacy_event_is_queued_after_shutdown(make):
    calls = []
    daemon = make(notify=lambda event: calls.append(event))
    registry = WatchRegistry()
    key = WatchKey("example/project", 1)
    registry.register(key, "legacy", until=(MERGED,), notify={})
    event = registry.apply_snapshot(key, PRSnapshot(merged=True))[0]
    daemon.compute("shutdown", {})
    daemon._deliveries.legacy(event)
    daemon.close()
    assert calls == [event]


@pytest.mark.parametrize("field,value", [
    ("repo", None), ("subscriber_id", 42), ("registration_id", "  "),
])
def test_acknowledged_raw_identity_must_be_nonblank_string(field, value):
    registry = WatchRegistry()
    registry.register(WatchKey("example/project", 1), "one", until=(MERGED,),
                      acknowledged=True, notify={"argv": ["consumer"]})
    entries = registry.snapshot_state()
    entries[0][field] = value
    with pytest.raises(ValueError, match="invalid persisted acknowledged subscription"):
        WatchRegistry().restore_state(entries)

@pytest.mark.parametrize("durable_first", [True, False])
def test_mixed_protocol_duplicate_never_overwrites_durable_event(durable_first):
    registry = WatchRegistry()
    key = WatchKey("example/project", 1)
    registry.register(key, "one", until=(MERGED,), acknowledged=True,
                      notify={"argv": ["consumer"]})
    registry.apply_snapshot(key, PRSnapshot(merged=True))
    durable = registry.snapshot_state()[0]
    legacy = {"repo": key.repo, "number": 1, "subscriber_id": "one", "until": [MERGED], "notify": {}}
    entries = [durable, legacy] if durable_first else [legacy, durable]
    with pytest.raises(ValueError, match="invalid persisted acknowledged subscription"):
        WatchRegistry().restore_state(entries)


def test_shutdown_during_fetch_preserves_subscriber_for_successor(make):
    entered, release = threading.Event(), threading.Event()
    callbacks = []

    def fetch(*_):
        entered.set()
        assert release.wait(3)
        return PRSnapshot(merged=True)

    daemon = make(fetch=fetch, notify=lambda e: callbacks.append(e) or 0)
    daemon.compute("register", spec())
    assert entered.wait(2)
    daemon.compute("shutdown", {})
    release.set()
    daemon.close()
    assert callbacks == []
    saved = read_subscriptions_state()
    assert len(saved) == 1 and saved[0]["pending"] is None


def test_acknowledged_state_requires_absolute_deadline_field():
    registry = WatchRegistry()
    registry.register(WatchKey("example/project", 1), "one", until=(MERGED,),
                      acknowledged=True, notify={"argv": ["consumer"]}, timeout=20)
    entries = registry.snapshot_state()
    del entries[0]["deadline_at"]
    with pytest.raises(ValueError, match="invalid persisted acknowledged subscription"):
        WatchRegistry().restore_state(entries)


def test_default_shutdown_drains_actual_callback_beyond_old_deadline(make, tmp_path):
    started, finished = tmp_path / "started", tmp_path / "finished"
    daemon = make()
    notify = {"argv": [
        sys.executable, "-c",
        "import pathlib,sys,time; pathlib.Path(sys.argv[1]).touch(); "
        "time.sleep(6); pathlib.Path(sys.argv[2]).touch()",
        str(started), str(finished),
    ]}
    daemon.compute("register", spec(notify=notify))
    wait(started.exists)
    daemon.compute("shutdown", {})
    daemon.close()
    assert finished.exists()
    assert daemon.status()["subscribers"] == {}
    assert not daemon._deliveries.workers


def test_duplicate_recovery_and_immutable_payload_with_downtime_deadline(monkeypatch):
    registry = WatchRegistry()
    key = WatchKey("example/project", 1)
    sub = registry.register(
        key, "one", until=(MERGED,), notify={"argv": ["consumer"]},
        acknowledged=True, timeout=20,
    )
    saved_wait = registry.snapshot_state()
    monkeypatch.setattr(time, "time", lambda: saved_wait[0]["deadline_at"] + 1)
    restored = WatchRegistry()
    assert restored.restore_state(saved_wait) == 1
    event = restored.apply_snapshot(key, PRSnapshot())[0]
    assert event.timed_out and sub.registration_id == event.subscriber.registration_id
    payload = event_payload(event)
    payload["transitions"].append("corruption")
    assert event_payload(event)["transitions"] == []
    saved = restored.snapshot_state()
    duplicate = json.loads(json.dumps(saved[0]))
    duplicate.update(repo="another/project", number=2)
    duplicate["pending"]["payload"].update(repo="another/project", number=2)
    with pytest.raises(ValueError, match="invalid persisted acknowledged subscription"):
        WatchRegistry().restore_state([saved[0], duplicate])
    saved[0]["pending"]["payload"]["transitions"] = ["invalid_transition"]
    with pytest.raises(ValueError, match="invalid persisted acknowledged subscription"):
        WatchRegistry().restore_state(saved)


@pytest.mark.parametrize("mode", ["timeout", "launch-error"])
def test_real_callback_timeout_and_launch_error_stay_pending(make, mode):
    notify = (
        {"argv": [sys.executable, "-c", "import time; time.sleep(20)"], "timeout": 0.05}
        if mode == "timeout" else {"argv": ["nonexistent-watch-test-callback-89217"]}
    )
    daemon = make()
    assert daemon.compute("register", spec(notify=notify))["registered"]
    wait(lambda: daemon.status()["pending_deliveries"][0]["attempts"] == 1)
    daemon.close()
    assert read_subscriptions_state()[0]["pending"]["last_error"] == (
        "callback_timeout" if mode == "timeout" else "callback_error"
    )


def test_unregister_reregister_and_late_ack_do_not_remove_new_registration(make):
    started, release = threading.Event(), threading.Event()
    payloads = []

    def notify(event):
        payloads.append(event_payload(event).copy())
        first = len(payloads) == 1
        if first:
            started.set()
            assert release.wait(3)
        return 0 if first else 9

    daemon = make(notify=notify)
    old = daemon.compute("register", spec())
    assert started.wait(2)
    try:
        assert daemon.compute("unregister", {
            "repo": "example/project", "number": 1, "subscriber_id": "one",
            "registration_id": old["registration_id"],
        })["unregistered"] is True
        new = daemon.compute("register", spec())
        assert new["registration_id"] != old["registration_id"]
        assert daemon.compute("unregister", {
            "repo": "example/project", "number": 1, "subscriber_id": "one",
            "registration_id": old["registration_id"],
        }) == {"unregistered": False}
        wait(lambda: len(payloads) == 2)
        release.set()
        wait(lambda: len(daemon._deliveries.workers) == 1)
        assert read_subscriptions_state()[0]["registration_id"] == new["registration_id"]
    finally:
        release.set()


def test_blocked_callback_never_starves_same_key_subscriber_or_overlaps(make):
    started, release = threading.Event(), threading.Event()
    calls = []

    def notify(event):
        identity = event.subscriber.subscriber_id
        calls.append(identity)
        if identity == "slow":
            started.set()
            assert release.wait(3)
        return 0

    daemon = make(notify=notify)
    daemon.compute("register", spec("slow"))
    assert started.wait(2)
    try:
        daemon.compute("register", spec("fast"))
        wait(lambda: "fast" in calls and daemon._registry.subscriber_count(
            WatchKey("example/project", 1)
        ) == 1)
        for _ in range(10):
            daemon._deliveries.resume()
        assert calls.count("slow") == 1
        assert daemon._registry.subscriber_count(WatchKey("example/project", 1)) == 1
        with pytest.raises(TimeoutError, match="pr-notify:"):
            daemon.close(timeout=0.01)
    finally:
        release.set()
        daemon.close()
    assert read_subscriptions_state() == []


def test_slow_legacy_callback_does_not_block_opted_in_observation(make):
    started, release = threading.Event(), threading.Event()
    calls = []

    def notify(event):
        calls.append(event.subscriber.subscriber_id)
        if event.subscriber.subscriber_id == "legacy":
            started.set()
            assert release.wait(3)
        return 0

    daemon = make(notify=notify)
    legacy = spec("legacy")
    legacy.pop("notification_protocol")
    daemon.compute("register", legacy)
    assert started.wait(2)
    try:
        daemon.compute("register", spec("opted-in"))
        wait(lambda: "opted-in" in calls and daemon.status()["subscribers"] == {})
    finally:
        release.set()


@pytest.mark.parametrize("changes", [
    {"timeout": v} for v in [0, -1, float("nan"), float("inf"), True, "1"]
] + [
    {"notify": v} for v in [
        None, {}, {"argv": []}, {"argv": "callback"}, {"argv": [""]},
        {"argv": [True]}, {"argv": ["bad\0command"]},
        {"argv": ["callback"], "timeout": float("nan")},
        {"argv": ["callback"], "timeout": True},
        {"argv": ["callback"], "timeout": 31},
        {"argv": ["callback"], "unexpected": 1},
    ]
] + [
    {"notification_protocol": "unsupported/v2"}, {"notification_protocol": None},
    {"number": True}, {"until": "merged"}, {"until": []},
])
def test_invalid_opt_in_registration_is_explicit_failure(make, changes):
    daemon = make(notify=lambda _: 0)
    assert "error" in daemon.compute("register", spec(**changes))
    assert daemon.status()["subscribers"] == {}
    assert read_subscriptions_state() == []


def test_corrupt_opt_in_state_fails_closed_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(tmp_path))
    write_subscriptions_state([spec(pending={"secret": "not-to-be-printed"})])
    before = (tmp_path / "watch-subscriptions.json").read_bytes()
    with pytest.raises(ValueError, match="invalid persisted acknowledged subscription") as error:
        WatchDaemon(fetch=lambda *_: PRSnapshot())
    assert "not-to-be-printed" not in str(error.value)
    assert (tmp_path / "watch-subscriptions.json").read_bytes() == before


def test_expired_wait_during_fetch_outage_is_durably_notified(make):
    events = []

    def unavailable(*_):
        raise OSError("provider unavailable")

    daemon = make(fetch=unavailable, notify=lambda e: events.append(event_payload(e)) or 0)
    daemon.compute("register", spec(timeout=0.02))
    wait(lambda: len(events) == 1 and daemon.status()["subscribers"] == {})
    assert events[0]["timed_out"] is True and events[0]["transitions"] == []
    assert "pr_state" not in events[0]


def test_legacy_state_and_wire_remain_unchanged(make):
    write_subscriptions_state([{
        "repo": "example/project", "number": 1, "subscriber_id": "legacy",
        "until": [MERGED], "notify": {},
    }])
    payloads = []
    daemon = make(notify=lambda e: payloads.append(event_payload(e)))
    wait(lambda: len(payloads) == 1)
    assert "event_id" not in payloads[0] and "notification_protocol" not in payloads[0]
    assert daemon.status()["subscribers"] == {}


def test_cli_negotiates_without_new_fields_to_old_owner(capsys):
    calls = []
    args = SimpleNamespace(
        repo="example/project", number=1, subscriber_id="one", until=None,
        notify_argv=["consumer"], timeout=10, json=True,
        acknowledged_notifications=True, notify_timeout=2,
    )

    def request(kind, payload):
        calls.append((kind, payload))
        return {"capabilities": []}

    assert subscribe(args, request) == 1
    assert [kind for kind, _ in calls] == ["health"]
    assert "lacks acknowledged_notifications/v1" in capsys.readouterr().out
    args.acknowledged_notifications = False
    args.notify_timeout = None
    calls.clear()
    subscribe(args, request)
    assert calls[0][0] == "register"
    assert "notification_protocol" not in calls[0][1]
    assert calls[0][1]["notify"] == {"argv": ["consumer"]}

@pytest.mark.parametrize("response", [
    {"registered": True},
    {"registered": True, "notification_protocol": PROTOCOL, "registration_id": ""},
    {"registered": True, "notification_protocol": "legacy", "registration_id": "one"},
])
def test_cli_owner_rollover_requires_registration_protocol_echo(capsys, response):
    args = SimpleNamespace(
        repo="example/project", number=1, subscriber_id="one", until=None,
        notify_argv=["consumer"], timeout=10, json=True,
        acknowledged_notifications=True, notify_timeout=2,
    )

    def request(kind, payload):
        return {"capabilities": [PROTOCOL]} if kind == "health" else response

    assert subscribe(args, request) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["ambiguous_registration"] is True
    assert "registered" not in result
    assert result["subscriber_id"] == "one"


@pytest.mark.parametrize("changes", [
    {"merged": "false"}, {"closed": 0}, {"review_decision": False},
    {"mergeable": []}, {"checks_state": 5},
])
def test_corrupt_acknowledged_baseline_fails_closed(changes):
    registry = WatchRegistry()
    key = WatchKey("example/project", 1)
    registry.register(key, "one", until=(MERGED,), acknowledged=True,
                      notify={"argv": ["consumer"]})
    registry.apply_snapshot(key, PRSnapshot())
    entries = registry.snapshot_state()
    entries[0]["baseline"].update(changes)
    with pytest.raises(ValueError, match="invalid persisted acknowledged subscription"):
        WatchRegistry().restore_state(entries)


def test_isolated_process_crash_then_restart_replays_original_payload(tmp_path):
    output = tmp_path / "delivered.json"
    env = {**os.environ, "AGENT_PULL_REQUESTS_HOME": str(tmp_path)}
    crash = """
import sys,os
from agent_pull_requests.watch_registry import WatchRegistry,WatchKey
from agent_pull_requests.watch_contract import PRSnapshot
from agent_pull_requests.watch_notification import ACKNOWLEDGED_NOTIFICATIONS
from agent_pull_requests.watch_storage import write_subscriptions_state
r=WatchRegistry(); k=WatchKey('example/project',1)
r.register(k,'one',until=('merged',),acknowledged=True,notify={'argv':[
sys.executable,'-c','import sys,pathlib; pathlib.Path(sys.argv[1]).write_text(sys.stdin.read())',
sys.argv[1]]})
r.apply_snapshot(k,PRSnapshot(merged=True))
write_subscriptions_state(r.snapshot_state())
os._exit(23)
"""
    result = subprocess.run(
        [sys.executable, "-c", crash, str(output)], env=env, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 23 and not output.exists()
    expected = json.loads((tmp_path / "watch-subscriptions.json").read_text())[0]["pending"]["payload"]
    restart = """
import time
from agent_pull_requests.watch_daemon import WatchDaemon
def fail(*args): raise AssertionError('pending recovery must not poll')
d=WatchDaemon(fetch=fail,poll_interval=.01)
deadline=time.monotonic()+5
while d.status()['subscribers'] and time.monotonic()<deadline: time.sleep(.01)
d.close()
assert not d.status()['subscribers']
"""
    result = subprocess.run(
        [sys.executable, "-c", restart], env=env, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0
    assert json.loads(output.read_text()) == expected
    assert json.loads((tmp_path / "watch-subscriptions.json").read_text()) == []
