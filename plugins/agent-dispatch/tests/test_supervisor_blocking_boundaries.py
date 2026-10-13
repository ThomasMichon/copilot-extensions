"""Actual slow callbacks must not monopolize the master or discovery workers."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from agent_dispatch.companion import DefaultCompanionController
from agent_dispatch.supervisor_daemon import SupervisorDaemon
from tests.test_supervisor_daemon import FakeClient, FakeCompanionController, FakeLauncher, _companion_reg, _reg


def test_blocked_progress_io_is_coalesced_and_tracked_without_blocking_units():
    entered, release = threading.Event(), threading.Event()
    frames = []
    daemon = SupervisorDaemon(
        FakeClient([_reg("one", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(), reconcile_call_budget=0.05,
    )

    def publish(frame):
        frames.append(frame)
        entered.set()
        assert release.wait(3)

    daemon._progress_callback = publish
    try:
        started = time.monotonic()
        assert daemon.reconcile_once().started == ["one"]
        assert time.monotonic() - started < 0.5
        assert entered.wait(1)
        for index in range(20):
            daemon._phase(f"phase-{index}")
        assert len(frames) == 1
        assert daemon._progress_future in daemon._probe_inflight
        release.set()
        daemon._progress_future.result(2)
        assert frames[-1]["phase"] == "phase-19"
        assert len(frames) <= 2
    finally:
        release.set()
        daemon.shutdown()


def test_slow_receipt_inventory_is_off_master_and_single_inflight(tmp_path, monkeypatch):
    from agent_dispatch import companion_reconcile

    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    controller.receipt_dir.mkdir()
    controller._receipt_path("orphan").write_text(
        '{"schema_version":1,"registration_id":"orphan","pid":100,"start_token":"old"}',
        encoding="utf-8",
    )
    original = companion_reconcile._read_metadata
    entered, release = threading.Event(), threading.Event()
    threads = []

    def read(*args, **kwargs):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(companion_reconcile, "_read_metadata", read)
    monkeypatch.setattr(controller, "_retire_receipt", lambda *args, **kwargs: None)
    daemon = SupervisorDaemon(
        FakeClient([_reg("unit", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=controller, reconcile_call_budget=0.05,
    )
    try:
        started = time.monotonic()
        assert daemon.reconcile_once().started == ["unit"]
        assert time.monotonic() - started < 0.5
        assert entered.wait(1)
        future = controller._receipt_inventory_future
        daemon.reconcile_once()
        assert controller._receipt_inventory_future is future
        assert threading.get_ident() not in threads
        assert future in daemon._probe_inflight
    finally:
        release.set()
        daemon.shutdown()


def test_four_blocked_providers_do_not_hide_authoritative_add_remove():
    controller = FakeCompanionController()
    original = controller.resolve
    release = threading.Event()
    entered = [threading.Event() for _ in range(4)]
    calls = []

    def resolve(registration, **kwargs):
        calls.append(registration["id"])
        entered[int(registration["id"][-1])].set()
        assert release.wait(3)
        return original(registration, **kwargs)

    controller.resolve = resolve
    registrations = [_companion_reg(f"provider-{index}") for index in range(4)]
    for reg in registrations:
        reg["machine"] = "host"
    client = FakeClient([_reg("old", machine="host"), *registrations])
    daemon = SupervisorDaemon(
        client, "host", launcher=FakeLauncher(), companion_controller=controller,
        reconcile_call_budget=0.05,
    )
    try:
        daemon.reconcile_once()
        assert all(event.wait(1) for event in entered)
        client.set_regs([_reg("new", machine="host"), *registrations])
        started = time.monotonic()
        summary = daemon.reconcile_once()
        assert time.monotonic() - started < 0.5
        assert summary.started == ["new"]
        assert summary.stopped == ["old"]
        assert len(calls) == 4
    finally:
        release.set()
        daemon.shutdown()


def test_progress_snapshot_collection_and_final_writer_do_not_block_master():
    entered, release = threading.Event(), threading.Event()
    controller = FakeCompanionController()

    def collect():
        entered.set()
        assert release.wait(3)
        return []

    controller.receipt_operation_progress = collect
    daemon = SupervisorDaemon(
        FakeClient([_reg("unit", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=controller, reconcile_call_budget=0.05,
    )
    written = []
    try:
        started = time.monotonic()
        summary = daemon.reconcile_once()
        daemon.queue_final_status(summary, written.append)
        assert time.monotonic() - started < 0.5
        assert entered.wait(1)
        assert summary.started == ["unit"]
        assert written == []
        release.set()
        daemon._progress_future.result(2)
        assert written == [summary]
    finally:
        release.set()
        daemon.shutdown()


def test_shutdown_refreshes_children_scheduled_by_finishing_inventory(tmp_path, monkeypatch):
    from agent_dispatch import supervisor_daemon

    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    inventory_release, child_release, draining = threading.Event(), threading.Event(), threading.Event()

    def inventory(*args, executor, **kwargs):
        assert inventory_release.wait(3)
        controller._receipt_operations["late"] = executor.submit(lambda: child_release.wait(3))
        return {"late"}

    monkeypatch.setattr(controller, "reconcile_receipts_bounded", inventory)
    monkeypatch.setattr(supervisor_daemon, "_SHUTDOWN_GRACE_SECONDS", 0.2)
    daemon = SupervisorDaemon(
        FakeClient([_reg("unit", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=controller, reconcile_call_budget=0.05,
    )
    original = daemon._drain_tracked_futures

    def drain(timeout):
        draining.set()
        return original(timeout)

    monkeypatch.setattr(daemon, "_drain_tracked_futures", drain)
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            assert daemon.reconcile_once().started == ["unit"]
            shutdown = pool.submit(daemon.shutdown)
            assert draining.wait(1)
            inventory_release.set()
            assert shutdown.result(1) is False
            child = controller._receipt_operations["late"]
            assert child in daemon._probe_inflight
            assert not child.done()
        finally:
            inventory_release.set()
            child_release.set()
            daemon.shutdown()


def test_slow_cycle_start_status_writer_does_not_delay_discovery():
    entered, release = threading.Event(), threading.Event()
    daemon = SupervisorDaemon(
        FakeClient([_reg("unit", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(), reconcile_call_budget=0.05,
    )

    def writer(*args):
        entered.set()
        assert release.wait(3)

    try:
        started = time.monotonic()
        daemon.queue_cycle_start(1, time.time(), writer)
        assert daemon.reconcile_once().started == ["unit"]
        assert time.monotonic() - started < 0.5
        assert entered.wait(1)
        assert daemon._progress_future in daemon._probe_inflight
    finally:
        release.set()
        daemon.shutdown()


def test_new_progress_at_worker_idle_return_is_not_lost(monkeypatch):
    idle, release = threading.Event(), threading.Event()
    daemon = SupervisorDaemon(
        FakeClient([]), "host", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(),
    )
    frames = []
    daemon._progress_callback = frames.append
    publish = daemon._publish_progress

    def exiting():
        publish()
        idle.set()
        assert release.wait(3)

    monkeypatch.setattr(daemon, "_publish_progress", exiting)
    try:
        daemon._phase("first")
        assert idle.wait(1)
        first = daemon._progress_future
        daemon._phase("last")
        release.set()
        first.result(2)
        daemon._progress_future.result(2)
        assert frames[-1]["phase"] == "last"
    finally:
        release.set()
        daemon.shutdown()


def test_superseded_final_status_is_not_associated_with_new_cycle():
    from types import SimpleNamespace

    entered, release = threading.Event(), threading.Event()
    daemon = SupervisorDaemon(
        FakeClient([]), "host", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(),
    )
    daemon._cycle_count = 1
    written, starts = [], []

    def blocked(frame):
        entered.set()
        assert release.wait(3)

    daemon._progress_callback = blocked
    try:
        daemon._phase("blocked")
        assert entered.wait(1)
        daemon.queue_final_status(SimpleNamespace(progress={"cycle_id": 1}), written.append)
        daemon._cycle_count = 2
        daemon.queue_cycle_start(2, time.time(), lambda cycle, stamp: starts.append(cycle))
        release.set()
        daemon._progress_future.result(2)
        assert starts == [2]
        assert written == []
    finally:
        release.set()
        daemon.shutdown()


def test_inline_self_update_keeps_singleton_until_status_publication_settles(tmp_path, monkeypatch):
    from agent_dispatch import supervisor_daemon
    from tests.test_supervisor_daemon import FakeLock

    entered, release = threading.Event(), threading.Event()
    lock, spawned = FakeLock(granted=True), []
    daemon = SupervisorDaemon(
        FakeClient([]), "host", launcher=FakeLauncher(), lock=lock,
        companion_controller=FakeCompanionController(), self_update_enabled=True,
        self_update_argv=["supervise", "serve"], self_update_install_dir=lambda: tmp_path,
        self_update_stale_target=lambda *_: tmp_path / "next",
        self_update_spawn=lambda *args: spawned.append(args),
    )
    monkeypatch.setattr(supervisor_daemon, "native_restart_unit", lambda: None)
    monkeypatch.setattr(supervisor_daemon, "_SHUTDOWN_GRACE_SECONDS", 0.05)

    def publish(frame):
        entered.set()
        assert release.wait(3)

    daemon._progress_callback = publish
    try:
        assert daemon.acquire_singleton()
        daemon._phase("blocked")
        assert entered.wait(1)
        assert daemon._maybe_self_update() is False
        assert not lock.released
        assert spawned == []
        assert daemon._self_update_last_spawn is None
        release.set()
        daemon._progress_future.result(1)
        daemon._self_update_last_check = 0
        assert daemon._maybe_self_update() is True
        assert lock.released
        assert len(spawned) == 1
    finally:
        release.set()
        daemon.shutdown()
