"""Inactive state adoption and bounded, lossless watch-worker quiescence."""

from __future__ import annotations

import threading
import time
from types import SimpleNamespace

import pytest

from agent_pull_requests.watch_contract import MERGED, PRSnapshot
from agent_pull_requests.watch_daemon import WatchDaemon
from agent_pull_requests.watch_notification import event_payload
from agent_pull_requests.watch_registry import WatchKey, WatchRegistry
from agent_pull_requests.watch_storage import (
    read_subscriptions_state, subscriptions_path, write_subscriptions_state,
)

pytestmark = pytest.mark.contract("agent_pull_requests.watch.governed_quiescence")


def wait(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert predicate()


def invoke(function, results, errors):
    try:
        results.append(function())
    except Exception as exc:
        errors.append(exc)


def registration(identity="one"):
    return {
        "repo": "example/project", "number": 1, "subscriber_id": identity,
        "until": [MERGED], "notify": {},
    }


def pending(identity):
    registry = WatchRegistry()
    key = WatchKey("example/project", 1)
    registry.register(
        key, identity, until=(MERGED,), acknowledged=True,
        notify={"argv": ["fixture-callback"]},
    )
    registry.apply_snapshot(key, PRSnapshot(merged=True, pr_state="closed"))
    entries = registry.snapshot_state()
    write_subscriptions_state(entries)
    return entries[0]


@pytest.fixture
def make():
    owners = []

    def create(**kwargs):
        daemon = WatchDaemon(
            fetch=kwargs.pop("fetch", lambda *_: PRSnapshot(pr_state="open")),
            poll_interval=0.01, idle_exit=0.01, **kwargs,
        )
        owners.append(daemon)
        return daemon

    yield create
    for daemon in reversed(owners):
        daemon.close()
        assert not any(thread.is_alive() for thread in daemon._pollers.values())


def test_passive_reads_no_state_and_adopts_only_the_final_snapshot(make, monkeypatch):
    from agent_pull_requests import watch_daemon

    pending("earlier")
    original = subscriptions_path().read_bytes()
    delivered = []
    with monkeypatch.context() as patch:
        patch.setattr(
            watch_daemon, "read_subscriptions_state",
            lambda: pytest.fail("passive construction must not read subscriptions"),
        )
        daemon = make(
            active=False, fetch=lambda *_: pytest.fail("pending replay must not fetch"),
            notify=lambda event: delivered.append(event_payload(event)) or 0,
        )
        assert daemon.compute("health", {})["subscriber_count"] == 0
        assert daemon.compute("register", registration())["retryable"] is True
        assert daemon.compute("unregister", registration())["retryable"] is True
        daemon.quiesce(timeout=1)
    assert not daemon._pollers and daemon._deliveries.scheduler is None
    assert delivered == [] and subscriptions_path().read_bytes() == original

    final = pending("latest")
    original = subscriptions_path().read_bytes()
    daemon.reload_state()
    assert daemon.status()["subscribers"] == {"example/project#1": ["latest"]}
    assert daemon.status()["pending_deliveries"][0]["event_id"] == final["pending"]["event_id"]
    assert delivered == [] and subscriptions_path().read_bytes() == original
    assert not daemon._pollers and daemon._deliveries.scheduler is None

    daemon.resume_effects()
    wait(lambda: read_subscriptions_state() == [])
    assert delivered == [final["pending"]["payload"]]


def test_quiescence_preserves_foreground_lifecycle_wait(make):
    daemon = make()
    waiter = threading.Thread(target=daemon.wait_for_shutdown)
    waiter.start()
    try:
        daemon.quiesce(timeout=1)
        assert daemon.quiescent and waiter.is_alive()
        assert "pid" in daemon.compute("health", {})
        assert daemon.compute("register", registration())["retryable"] is True
        assert daemon.compute("shutdown", {}) == {"shutting_down": True}
    finally:
        daemon.compute("shutdown", {})
        waiter.join(timeout=3)
        assert not waiter.is_alive()


def test_quiescence_keeps_real_authenticated_control_plane_reachable(make):
    from work_coalescing_singleton import CoalescingServer
    from work_coalescing_singleton.client import DaemonUnavailable, request

    daemon = make()
    server = CoalescingServer(daemon.compute, on_idle=None)
    server.start()
    rendezvous = server.rendezvous()
    host, port = rendezvous["endpoint"].split(":")

    def call(kind, *, token=None):
        return request(
            host, int(port), token or rendezvous["token"], kind=kind, key=kind,
            payload=registration() if kind == "register" else {}, request_deadline_s=1,
        )

    try:
        daemon.quiesce(timeout=1)
        with pytest.raises(DaemonUnavailable):
            call("register", token="wrong-token")
        assert "pid" in call("health")
        assert call("register")["retryable"] is True
        assert call("shutdown") == {"shutting_down": True}
        wait(lambda: server.active_handler_count() == 0)
        assert read_subscriptions_state() == []
    finally:
        server.close()


@pytest.mark.parametrize("kind", ["register", "unregister"])
def test_quiescence_waits_for_admitted_mutation_and_refuses_new_requests(make, monkeypatch, kind):
    daemon = make()
    if kind == "unregister":
        daemon.compute("register", registration())
    admitted, proceed = threading.Event(), threading.Event()
    dispatch = daemon._compute_request
    replies, stopped, errors = [], [], []

    def dispatch_admitted(operation, payload):
        if operation == kind:
            admitted.set()
            assert proceed.wait(3)
        return dispatch(operation, payload)

    monkeypatch.setattr(daemon, "_compute_request", dispatch_admitted)
    mutation = threading.Thread(target=invoke, args=(
        lambda: daemon.compute(kind, registration()), replies, errors,
    ))
    drain = threading.Thread(target=invoke, args=(
        lambda: daemon.quiesce(timeout=2), stopped, errors,
    ))
    mutation.start()
    try:
        assert admitted.wait(2)
        drain.start()
        wait(lambda: not daemon._accepting_mutations)
        assert drain.is_alive() and not daemon.quiescent
        assert daemon.compute("register", registration("late"))["retryable"] is True
        proceed.set()
    finally:
        proceed.set()
        mutation.join(timeout=3)
        if drain.ident is not None:
            drain.join(timeout=3)
    assert not mutation.is_alive() and not drain.is_alive()
    assert errors == [] and stopped == [None]
    assert replies[0]["registered" if kind == "register" else "unregistered"] is True
    assert [entry["subscriber_id"] for entry in read_subscriptions_state()] == (
        ["one"] if kind == "register" else []
    )


def test_incomplete_quiescence_retains_legacy_batches_for_same_owner_resume(make):
    started, proceed = threading.Event(), threading.Event()
    delivered = []

    def notify(event):
        started.set()
        assert proceed.wait(3)
        delivered.append(event.subscriber.subscriber_id)

    daemon = make(notify=notify)
    daemon._deliveries.max_workers = 1
    registry = WatchRegistry()
    key = WatchKey("example/project", 1)
    for identity in ("first", "second", "third"):
        registry.register(key, identity, until=(MERGED,), notify={})
    events = registry.apply_snapshot(key, PRSnapshot(merged=True))
    daemon._deliveries.legacy(events[0])
    assert started.wait(2)
    for event in events[1:]:
        daemon._deliveries.legacy(event)
    retained = daemon._registry, daemon._deliveries
    try:
        before = time.monotonic()
        with pytest.raises(TimeoutError, match="workers"):
            daemon.quiesce(timeout=0.05)
        assert time.monotonic() - before < 1
        assert not daemon.quiescent and len(daemon._deliveries.legacy_queue) == 2
        with pytest.raises(RuntimeError, match="quiescen"):
            daemon.reload_state()
        daemon.resume_effects(timeout=1)
        assert (daemon._registry, daemon._deliveries) == retained
    finally:
        proceed.set()
    wait(lambda: len(delivered) == 3)
    assert delivered == ["first", "second", "third"]


def test_quiescence_waits_for_callback_ack_before_state_transfer(make):
    original = pending("one")
    started, proceed = threading.Event(), threading.Event()
    delivered, stopped, errors = [], [], []

    def notify(event):
        delivered.append(event_payload(event))
        started.set()
        assert proceed.wait(3)
        return 0

    daemon = make(notify=notify)
    assert started.wait(2)
    drain = threading.Thread(target=invoke, args=(
        lambda: daemon.quiesce(timeout=2), stopped, errors,
    ))
    drain.start()
    try:
        wait(lambda: not daemon._accepting_mutations)
        assert drain.is_alive() and read_subscriptions_state()[0]["pending"] is not None
    finally:
        proceed.set()
        drain.join(timeout=3)
    assert not drain.is_alive() and errors == [] and stopped == [None]
    assert daemon.quiescent and read_subscriptions_state() == []
    assert delivered == [original["pending"]["payload"]]
    successor = make(active=False)
    successor.reload_state()
    assert successor.status()["subscribers"] == {}


def test_incomplete_ack_drain_resumes_without_replacing_or_duplicating_delivery(make):
    original = pending("one")
    started, proceed = threading.Event(), threading.Event()
    delivered = []

    def notify(event):
        delivered.append(event_payload(event))
        started.set()
        assert proceed.wait(3)
        return 0

    daemon = make(notify=notify)
    assert started.wait(2)
    retained = daemon._registry, daemon._deliveries
    try:
        with pytest.raises(TimeoutError, match="workers"):
            daemon.quiesce(timeout=0.05)
        assert not daemon.quiescent and read_subscriptions_state()[0]["pending"] is not None
        with pytest.raises(RuntimeError, match="quiescen"):
            daemon.reload_state()
        daemon.resume_effects(timeout=1)
        assert (daemon._registry, daemon._deliveries) == retained
        assert original["registration_id"] in daemon._deliveries.workers
    finally:
        proceed.set()
    wait(lambda: read_subscriptions_state() == [])
    assert delivered == [original["pending"]["payload"]]


def test_failed_reload_cannot_activate_a_stale_cached_registry(make):
    pending("one")
    daemon = make(active=False)
    daemon.reload_state()
    subscriptions_path().write_text("{invalid", encoding="utf-8")
    with pytest.raises(ValueError, match="persisted"):
        daemon.reload_state()
    with pytest.raises(RuntimeError, match="adopt"):
        daemon.resume_effects()
    assert subscriptions_path().read_text() == "{invalid"
    assert not daemon._pollers and daemon._deliveries.scheduler is None


@pytest.mark.parametrize("entry", [
    {}, {"repo": "example/project", "number": True, "subscriber_id": "one", "until": ["merged"]},
    {"repo": "example/project", "number": 1, "subscriber_id": "one", "until": ["unknown"]},
])
def test_read_ready_adoption_rejects_invalid_legacy_entries(make, entry):
    write_subscriptions_state([entry])
    original = subscriptions_path().read_bytes()
    daemon = make(active=False)
    with pytest.raises(ValueError, match="persisted"):
        daemon.reload_state()
    with pytest.raises(RuntimeError, match="adopt"):
        daemon.resume_effects()
    assert subscriptions_path().read_bytes() == original


def test_partial_poller_start_failure_retains_owner_for_cleanup_and_retry(make, monkeypatch):
    entry = registration()
    entry.update(baseline=None, remaining_timeout=None)
    write_subscriptions_state([entry])
    daemon = make(active=False)
    daemon.reload_state()
    start = threading.Thread.start
    failed = False

    def start_poller(thread):
        nonlocal failed
        if thread.name.startswith("pr-watch:") and not failed:
            failed = True
            raise RuntimeError("fixture poller start failure")
        return start(thread)

    monkeypatch.setattr(threading.Thread, "start", start_poller)
    with pytest.raises(RuntimeError, match="poller start"):
        daemon.resume_effects()
    assert not daemon._accepting_mutations and len(daemon._pollers) == 1
    assert not next(iter(daemon._pollers.values())).is_alive()
    daemon.resume_effects()
    assert daemon._accepting_mutations
    wait(lambda: next(iter(daemon._pollers.values())).is_alive())


def test_final_persistence_failure_is_not_successful_quiescence(make, monkeypatch):
    from agent_pull_requests import watch_daemon

    daemon = make()

    def fail(_):
        raise OSError("fixture final-state write failure")

    monkeypatch.setattr(watch_daemon, "write_subscriptions_state", fail)
    with pytest.raises(OSError, match="final-state"):
        daemon.quiesce(timeout=1)
    assert not daemon.quiescent and not daemon._accepting_mutations
    assert daemon.compute("health", {})["persistence_error"] == "state_write_failed"


@pytest.mark.parametrize("operation", ["quiesce", "reload_state", "resume_effects", "close"])
@pytest.mark.parametrize("timeout", [0, -1, True, "1", float("nan"), float("inf")])
def test_invalid_lifecycle_budget_refuses_before_changing_owner(make, operation, timeout):
    daemon = make()
    with pytest.raises(ValueError, match="finite positive"):
        getattr(daemon, operation)(timeout=timeout)
    assert daemon._accepting_mutations and not daemon._shutdown_event.is_set()
    assert not daemon._lifecycle_shutdown_event.is_set()
    assert not subscriptions_path().exists()


def test_quiescence_does_not_report_success_after_a_late_final_write(make, monkeypatch):
    from agent_pull_requests import watch_daemon

    daemon = make()
    value = [0.0]
    write = watch_daemon.write_subscriptions_state

    def finish_late(entries):
        write(entries)
        value[0] = 2.0

    with monkeypatch.context() as patch:
        patch.setattr(watch_daemon, "write_subscriptions_state", finish_late)
        patch.setattr(watch_daemon, "time", SimpleNamespace(
            **{**vars(time), "monotonic": lambda: value[0]},
        ))
        with pytest.raises(TimeoutError, match="deadline"):
            daemon.quiesce(timeout=1)
    assert not daemon.quiescent and not daemon._accepting_mutations
    assert read_subscriptions_state() == []


def test_late_state_read_cannot_authorize_stale_or_late_activation(make, monkeypatch):
    from agent_pull_requests import watch_daemon

    pending("earlier")
    daemon = make(active=False)
    daemon.reload_state()
    pending("latest")
    value = [0.0]
    read = watch_daemon.read_subscriptions_state

    def finish_late():
        entries = read()
        value[0] = 2.0
        return entries

    with monkeypatch.context() as patch:
        patch.setattr(watch_daemon, "read_subscriptions_state", finish_late)
        patch.setattr(watch_daemon, "time", SimpleNamespace(
            **{**vars(time), "monotonic": lambda: value[0]},
        ))
        with pytest.raises(TimeoutError, match="deadline"):
            daemon.reload_state(timeout=1)
    with pytest.raises(RuntimeError, match="adopt"):
        daemon.resume_effects()
    assert not daemon._pollers and daemon._deliveries.scheduler is None


def test_late_worker_start_keeps_mutation_admission_closed(make, monkeypatch):
    from agent_pull_requests import watch_daemon

    entry = registration()
    entry.update(baseline=None, remaining_timeout=None)
    write_subscriptions_state([entry])
    daemon = make(active=False)
    daemon.reload_state()
    value = [0.0]
    ensure = daemon._ensure_poller

    def start_late(key):
        ensure(key)
        value[0] = 2.0

    with monkeypatch.context() as patch:
        patch.setattr(daemon, "_ensure_poller", start_late)
        patch.setattr(watch_daemon, "time", SimpleNamespace(
            **{**vars(time), "monotonic": lambda: value[0]},
        ))
        with pytest.raises(TimeoutError, match="deadline"):
            daemon.resume_effects(timeout=1)
    assert not daemon._accepting_mutations and not daemon.quiescent
    assert daemon.compute("register", registration("late"))["retryable"] is True


@pytest.mark.parametrize("terminal", ["shutdown", "close"])
def test_terminal_request_during_resume_cannot_restart_worker_effects(
    make, monkeypatch, terminal,
):
    daemon = make()
    daemon._deliveries.resume()
    daemon.quiesce(timeout=1)
    scheduler = daemon._deliveries.scheduler
    paused, proceed = threading.Event(), threading.Event()
    joined = scheduler.join
    resume = daemon._deliveries.resume
    effects, resumed, resume_errors, closed, close_errors = [], [], [], [], []
    first_join = True

    def join_before_clear(timeout=None):
        nonlocal first_join
        joined(timeout)
        if first_join:
            first_join = False
            paused.set()
            assert proceed.wait(3)

    def observe_effect_start():
        if daemon._lifecycle_shutdown_event.is_set():
            effects.append("activation after terminal request")
        return resume()

    monkeypatch.setattr(scheduler, "join", join_before_clear)
    monkeypatch.setattr(daemon._deliveries, "resume", observe_effect_start)
    activation = threading.Thread(target=invoke, args=(
        lambda: daemon.resume_effects(timeout=2), resumed, resume_errors,
    ))
    cleanup = None
    activation.start()
    try:
        assert paused.wait(2)
        if terminal == "shutdown":
            assert daemon.compute("shutdown", {}) == {"shutting_down": True}
        else:
            cleanup = threading.Thread(target=invoke, args=(
                lambda: daemon.close(timeout=2), closed, close_errors,
            ))
            cleanup.start()
            wait(daemon._lifecycle_shutdown_event.is_set)
    finally:
        proceed.set()
        activation.join(timeout=3)
        if cleanup is not None:
            cleanup.join(timeout=3)
    assert not activation.is_alive() and resumed == []
    assert len(resume_errors) == 1 and isinstance(resume_errors[0], RuntimeError)
    assert effects == [] and daemon._shutdown_event.is_set()
    assert not daemon._accepting_mutations and not scheduler.is_alive()
    assert not any(thread.is_alive() for thread in daemon._pollers.values())
    if cleanup is not None:
        assert not cleanup.is_alive() and closed == [None] and close_errors == []


def test_competing_lifecycle_request_has_a_bounded_wait_without_reopening_owner(make):
    started, proceed = threading.Event(), threading.Event()
    stopped, errors = [], []

    def fetch(*_):
        started.set()
        assert proceed.wait(3)
        return PRSnapshot(pr_state="open")

    daemon = make(fetch=fetch)
    daemon.compute("register", registration())
    assert started.wait(2)
    drain = threading.Thread(target=invoke, args=(
        lambda: daemon.quiesce(timeout=2), stopped, errors,
    ))
    drain.start()
    try:
        wait(lambda: not daemon._accepting_mutations)
        before = time.monotonic()
        with pytest.raises(TimeoutError, match="busy"):
            daemon.resume_effects(timeout=0.05)
        assert time.monotonic() - before < 1
        assert daemon._shutdown_event.is_set() and not daemon._accepting_mutations
        assert "pid" in daemon.compute("health", {})
    finally:
        proceed.set()
        drain.join(timeout=3)
    assert not drain.is_alive() and errors == [] and stopped == [None]
    assert daemon.quiescent
