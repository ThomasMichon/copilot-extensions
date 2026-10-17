"""Actual slow callbacks must not monopolize the master or discovery workers."""

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

import pytest

from agent_dispatch.companion import CompanionIndeterminate, DefaultCompanionController
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


@pytest.mark.parametrize("failure", [KeyError("private-detail"), AssertionError("private-detail")])
def test_failed_progress_worker_does_not_abort_next_reconciliation(caplog, failure):
    daemon = SupervisorDaemon(
        FakeClient([_reg("unit", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(),
    )
    calls = []

    def publish(frame):
        calls.append(frame["phase"])
        if len(calls) == 1:
            raise failure

    daemon._progress_callback = publish
    try:
        daemon._phase("first")
        with pytest.raises(type(failure)):
            daemon._progress_future.result(2)
        assert daemon.reconcile_once().started == ["unit"]
        daemon._progress_future.result(2)
        assert calls[-1] == "complete"
        assert calls.count("first") == 1
        assert "progress-worker-unavailable" in caplog.text
        assert "private-detail" not in caplog.text
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("active_deadline", [None, 100.025, 0.0])
def test_receipt_inventory_without_active_deadline_receives_forward_budget(monkeypatch, active_deadline):
    controller = FakeCompanionController()
    observed = []

    def inventory(adopted, *, deadline, operation_budget, **kwargs):
        observed.append((adopted, deadline, operation_budget))
        expected = active_deadline if active_deadline is not None else time.monotonic() + operation_budget
        assert deadline == pytest.approx(expected)
        return {"pending"}

    controller.request_receipt_inventory = inventory
    controller.receipt_futures = lambda: ()
    daemon = SupervisorDaemon(
        FakeClient([]), "host", launcher=FakeLauncher(),
        companion_controller=controller, reconcile_call_budget=0.05,
    )
    monkeypatch.setattr("agent_dispatch.supervisor_reconcile.time.monotonic", lambda: 100.0)
    try:
        daemon._reconcile_deadline = active_deadline
        daemon._reconcile_companion_receipts({"adopted"})
        expected = active_deadline if active_deadline is not None else 100.05
        assert observed == [({"adopted"}, expected, 0.05)]
        assert daemon._managed_uncertain == {"pending"}
    finally:
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


def test_shutdown_samples_receipt_children_after_pruning_finishing_parent(monkeypatch):
    controller = FakeCompanionController()
    parent, child = Future(), Future()
    parent.set_running_or_notify_cancel()
    child.set_running_or_notify_cancel()
    daemon = SupervisorDaemon(
        FakeClient([]), "host", launcher=FakeLauncher(),
        companion_controller=controller, reconcile_call_budget=0.05,
    )
    daemon._probe_inflight.add(parent)
    daemon._shutdown_waited_futures = set()
    daemon._shutdown_drained = True
    controller.receipt_futures = lambda: (child,) if parent.done() else ()
    original = daemon._prune_probe_inflight

    def complete_inventory_then_prune():
        if not parent.done():
            parent.set_result(None)
        original()

    monkeypatch.setattr(daemon, "_prune_probe_inflight", complete_inventory_then_prune)
    try:
        daemon._drain_tracked_futures(0.01)
        assert daemon._shutdown_drained is False
        assert child in daemon._probe_inflight
        assert not child.done()
    finally:
        child.set_result(None)
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
        FakeClient([_reg("unit", machine="host")]), "host", launcher=FakeLauncher(), lock=lock,
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
        assert daemon.reconcile_once().started == ["unit"]
        process = daemon.launcher.proc_for("unit")
        assert entered.wait(1)
        assert daemon._maybe_self_update() is False
        assert not process.terminated
        assert "unit" in daemon._units
        assert not lock.released
        assert spawned == []
        assert daemon._self_update_last_spawn is None
        release.set()
        daemon._progress_future.result(1)
        daemon._self_update_last_check = 0
        assert daemon._maybe_self_update() is True
        assert process.terminated
        assert lock.released
        assert len(spawned) == 1
    finally:
        release.set()
        daemon.shutdown()


def test_cold_registry_timeout_preserves_receipts_until_first_confirmed_snapshot(tmp_path, monkeypatch):
    from tests.test_bounded_receipt_reconcile import _receipt

    entered, release = threading.Event(), threading.Event()
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    _receipt(controller, rid="persisted")
    before = controller._receipt_path("persisted").read_bytes()
    registration = _companion_reg("persisted")
    registration["machine"] = "host"
    client = FakeClient([registration, _reg("unrelated", machine="host")])
    original_read = client.list_registrations
    inventories = []
    original_inventory = controller.request_receipt_inventory
    process_controller = FakeCompanionController()

    def read(**kwargs):
        entered.set()
        assert release.wait(3)
        return original_read(**kwargs)

    def inventory(adopted, **kwargs):
        inventories.append(set(adopted))
        return original_inventory(adopted, **kwargs)

    monkeypatch.setattr(client, "list_registrations", read)
    monkeypatch.setattr(controller, "request_receipt_inventory", inventory)
    monkeypatch.setattr(controller, "resolve", process_controller.resolve)
    monkeypatch.setattr(controller, "launch", process_controller.launch)
    monkeypatch.setattr(controller, "stop", process_controller.stop)
    daemon = SupervisorDaemon(
        client, "host", launcher=FakeLauncher(), companion_controller=controller,
        reconcile_call_budget=0.05,
    )
    try:
        started = time.monotonic()
        with pytest.raises(CompanionIndeterminate, match="confirmed registry snapshot"):
            daemon.reconcile_once()
        assert time.monotonic() - started < 0.5
        assert entered.is_set()
        assert inventories == []
        assert controller._receipt_path("persisted").read_bytes() == before
        assert daemon.launcher.launched == []
        release.set()
        daemon._discovery_futures["registrations"].result(1)
        assert set(daemon.reconcile_once().started) == {"persisted", "unrelated"}
        assert inventories and all("persisted" in adopted for adopted in inventories)
        assert controller._receipt_path("persisted").read_bytes() == before
    finally:
        release.set()
        daemon.shutdown()


def test_cold_declaration_timeout_preserves_declared_receipt(tmp_path, monkeypatch):
    from agent_dispatch import registrar_reconcile
    from tests.test_bounded_receipt_reconcile import _receipt

    entered, release = threading.Event(), threading.Event()
    rid = "declared:persisted"
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    _receipt(controller, rid)
    before = controller._receipt_path(rid).read_bytes()
    registration = _companion_reg(rid)
    registration["machine"] = "host"
    processes = FakeCompanionController()
    for method in ("resolve", "launch", "stop", "health"):
        monkeypatch.setattr(controller, method, getattr(processes, method))
    inventories = []
    original_inventory = controller.request_receipt_inventory

    def inventory(adopted, **kwargs):
        inventories.append(set(adopted))
        return original_inventory(adopted, **kwargs)

    def declarations():
        entered.set()
        assert release.wait(3)
        return [registration]

    monkeypatch.setattr(controller, "request_receipt_inventory", inventory)
    monkeypatch.setattr(registrar_reconcile, "declared_registrations", lambda data, **kwargs: data)
    daemon = SupervisorDaemon(
        FakeClient([]), "host", launcher=FakeLauncher(), companion_controller=controller,
        declared_source=declarations, reconcile_call_budget=0.05,
    )
    try:
        assert daemon._store_registrations() == []
        with pytest.raises(CompanionIndeterminate, match="confirmed declaration snapshot"):
            daemon.reconcile_once()
        assert entered.is_set()
        future = daemon._discovery_futures["declarations"]
        with pytest.raises(CompanionIndeterminate, match="confirmed declaration snapshot"):
            daemon.reconcile_once()
        assert daemon._discovery_futures["declarations"] is future
        assert inventories == []
        assert controller._receipt_path(rid).read_bytes() == before
        release.set()
        future.result(1)
        assert daemon.reconcile_once().started == [rid]
        assert inventories and all(rid in adopted for adopted in inventories)
        assert controller._receipt_path(rid).read_bytes() == before
    finally:
        release.set()
        daemon.shutdown()


@pytest.mark.parametrize("existing", [False, True])
def test_indeterminate_unmanaged_provider_preserves_receipt_without_stale_authority(tmp_path, monkeypatch, existing):
    from tests.test_bounded_receipt_reconcile import _receipt

    entered, release = threading.Event(), threading.Event()
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    _receipt(controller, "persisted")
    before = controller._receipt_path("persisted").read_bytes()
    processes = FakeCompanionController()
    for method in ("resolve", "launch", "stop", "health"):
        monkeypatch.setattr(controller, method, getattr(processes, method))
    registration = _companion_reg("persisted")
    registration["machine"] = "host"
    client = FakeClient([registration])
    daemon = SupervisorDaemon(
        client, "host", launcher=FakeLauncher(), companion_controller=controller,
        reconcile_call_budget=0.05,
    )
    inventories = []
    original_inventory = controller.request_receipt_inventory

    def inventory(adopted, **kwargs):
        inventories.append(set(adopted))
        return original_inventory(adopted, **kwargs)

    def resolve(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return processes.resolve(*args, **kwargs)

    try:
        if existing:
            assert daemon.reconcile_once().started == ["persisted"]
            process = processes.launched[-1][1]
            registration = _companion_reg("persisted")
            registration["machine"] = "host"
            registration["spec"]["config_provider"] = ["bin/changed.py"]
            client.set_regs([registration])
        monkeypatch.setattr(controller, "resolve", resolve)
        monkeypatch.setattr(controller, "request_receipt_inventory", inventory)
        summary = daemon.reconcile_once()
        assert entered.is_set()
        assert inventories and all("persisted" in adopted for adopted in inventories)
        assert controller._receipt_path("persisted").read_bytes() == before
        assert summary.started == summary.restarted == []
        assert summary.stopped == (["persisted"] if existing else [])
        if existing:
            assert process.terminated
            assert summary.running == []
        release.set()
        daemon._resolution_futures["persisted"][1].result(1)
        summary = daemon.reconcile_once()
        assert summary.started == ["persisted"]
    finally:
        release.set()
        daemon.shutdown()


def test_companionless_cycles_prune_completed_probe_futures_and_retain_pending():
    pending = Future()
    daemon = SupervisorDaemon(
        FakeClient([_reg("unit", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(),
    )
    daemon._probe_inflight.add(pending)
    try:
        for _ in range(8):
            daemon.reconcile_once()
            daemon._progress_future.result(1)
            for future in tuple(daemon._probe_inflight):
                if future is not pending:
                    future.result(1)
            assert pending in daemon._probe_inflight
        assert len(daemon._probe_inflight) <= 9
    finally:
        pending.set_result(None)
        daemon.shutdown()


@pytest.mark.parametrize("pending_at_removal", [False, True])
def test_removed_provider_result_is_never_reused_after_reintroduction(pending_at_removal):
    controller = FakeCompanionController()
    resolve = controller.resolve
    entered, release = threading.Event(), threading.Event()
    calls = []

    def provider(registration, **kwargs):
        calls.append(registration["id"])
        if len(calls) == 1:
            entered.set()
            assert release.wait(5)
        controller.resolve_outcomes.append({
            "arguments": [str(len(calls))], "environment": {}, "environment_digest": "same",
        })
        return resolve(registration, **kwargs)

    controller.resolve = provider
    registration = _companion_reg("companion")
    daemon = SupervisorDaemon(
        FakeClient([]), "host", launcher=FakeLauncher(), companion_controller=controller,
        reconcile_call_budget=0.05,
    )
    try:
        daemon._reconcile_deadline = time.monotonic() + 0.05
        desired = {"companion": registration}
        daemon._resolve_companion_desired(desired)
        assert entered.wait(1)
        future = daemon._resolution_futures["companion"][1]
        if not pending_at_removal:
            release.set()
            future.result(1)
        daemon._resolve_companion_desired({})
        if pending_at_removal:
            assert future in daemon._probe_inflight
            assert not future.done()
            reintroduced = {"companion": registration}
            daemon._resolve_companion_desired(reintroduced)
            assert reintroduced == {}
            assert calls == ["companion"]
            assert daemon._resolution_futures["companion"][1] is future
        else:
            assert "companion" not in daemon._resolution_futures
        release.set()
        future.result(1)
        daemon._reconcile_deadline = time.monotonic() + 1
        desired = {"companion": registration}
        daemon._resolve_companion_desired(desired)
        assert calls == ["companion", "companion"]
        assert desired["companion"]["companion_runtime"]["arguments"] == ["2"]
        assert "companion" not in daemon._resolution_futures
    finally:
        release.set()
        daemon.shutdown()


def test_corrupt_companion_operation_does_not_abort_sibling_reconciliation(tmp_path, caplog):
    controller = DefaultCompanionController(tmp_path / "receipts")
    controller.receipt_dir.mkdir()
    damaged = controller._operation_path("damaged")
    damaged.write_text("{", encoding="utf-8")
    registration = _companion_reg("damaged")
    registration["machine"] = "host"
    daemon = SupervisorDaemon(
        FakeClient([registration, _reg("unrelated", machine="host")]), "host",
        launcher=FakeLauncher(), companion_controller=controller,
    )
    try:
        assert daemon.reconcile_once().started == ["unrelated"]
        future = daemon._resolution_futures.get("damaged")
        if future is not None:
            with pytest.raises(CompanionIndeterminate, match="metadata"):
                future[1].result(1)
        assert daemon.reconcile_once().running == ["unrelated"]
        assert damaged.read_text(encoding="utf-8") == "{"
        assert len(daemon.launcher.launched) == 1
        assert "damaged" in caplog.text
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("blocked_id", ["orphan", "sibling"])
def test_metadata_read_cannot_hold_master_launch_availability(tmp_path, monkeypatch, blocked_id):
    from agent_dispatch import companion, companion_reconcile
    from agent_dispatch.companion import CompanionResolution
    from agent_dispatch.supervisor_daemon import companion_authority_fingerprint
    from tests.test_bounded_receipt_reconcile import _unknown_operation
    from tests.test_supervisor_daemon import FakeProc

    class Process(FakeProc):
        pid = 123

        def release(self):
            pass

    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    for rid in ("orphan", "sibling"):
        _unknown_operation(controller, rid)
        controller._write_operation(rid, {**controller._operation_record(rid), "state": "complete"})
    registration = _companion_reg("sibling")
    registration["machine"] = "host"
    resolution = CompanionResolution(
        registration, ("unused",), None, None, str(tmp_path), {}, 0.5, 0.5, 0.5,
    )
    monkeypatch.setattr(controller, "resolver", lambda *args, **kwargs: resolution)
    daemon = SupervisorDaemon(
        FakeClient([registration]), "host", companion_controller=controller,
        reconcile_call_budget=0.05,
    )
    confirmed = Future()
    confirmed.set_result(resolution)
    daemon._resolution_futures["sibling"] = (companion_authority_fingerprint(registration), confirmed)
    entered, release = threading.Event(), threading.Event()
    read = companion_reconcile._read_metadata

    def blocked(root, path):
        if path == controller._operation_path(blocked_id).absolute() and not entered.is_set():
            entered.set()
            assert release.wait(2)
        return read(root, path)

    monkeypatch.setattr(companion_reconcile, "_read_metadata", blocked)
    monkeypatch.setattr(companion, "_launch_gated", lambda resolution: Process())
    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(controller.receipt_operation_progress)
        try:
            assert entered.wait(1)
            started = time.monotonic()
            summary = daemon.reconcile_once()
            assert time.monotonic() - started < 0.5
            if blocked_id == "orphan":
                admission_deadline = started + 1
                while not summary.started and time.monotonic() < admission_deadline:
                    cycle_started = time.monotonic()
                    summary = daemon.reconcile_once()
                    assert time.monotonic() - cycle_started < 0.5
                assert summary.started == ["sibling"]
            else:
                assert summary.started == []
                assert any(not future.done() for future in controller.receipt_futures())
        finally:
            release.set()
            holder.result(1)
            daemon.shutdown()


def test_confirmed_withdrawal_rejects_prewithdrawal_registry_read(tmp_path, monkeypatch):
    from agent_dispatch import registrar_reconcile
    from tests.test_managed_companion import Executor

    rid = "declared:withdrawn"
    declared = [_reg(rid, machine="host")]
    unrelated = _reg("unrelated", machine="host")
    unrelated["spec"]["labels"] = ["independent"]
    client = FakeClient([unrelated])
    daemon = SupervisorDaemon(
        client, "host", launcher=FakeLauncher(), companion_controller=FakeCompanionController(),
        declared_source=lambda: list(declared), reconcile_call_budget=0.05,
    )
    daemon._publication_executor = Executor()
    monkeypatch.setattr(registrar_reconcile, "declared_registrations", lambda values, **kwargs: values)
    read = client.list_registrations
    entered, release = threading.Event(), threading.Event()
    delay = [False]

    def registry(**kwargs):
        rows = read(**kwargs)
        if delay[0] and kwargs.get("include_paused") is False:
            entered.set()
            assert release.wait(3)
        return rows

    monkeypatch.setattr(client, "list_registrations", registry)
    try:
        assert daemon._declared() == declared
        assert {reg["id"] for reg in daemon._store_registrations()} == {rid, "unrelated"}
        assert set(daemon.reconcile_once().started) == {rid, "unrelated"}
        delay[0] = True
        daemon.reconcile_once()
        assert entered.is_set()
        old_read = daemon._discovery_futures["registrations"]
        declared.clear()
        assert daemon.reconcile_once().stopped == [rid]
        assert daemon.reconcile_once().started == []
        assert rid in daemon._published_declared_ids
        delay[0] = False
        release.set()
        old_read.result(1)
        assert daemon.reconcile_once().started == []
        assert daemon.reconcile_once().started == []
        assert daemon.reconcile_once().running == ["unrelated"]
        assert rid not in daemon._declaration_publisher.seen_ids
        assert [name for name, _ in daemon.launcher.launched].count(rid) == 1
    finally:
        release.set()
        daemon.shutdown()


def test_cancelled_provider_read_does_not_abort_unrelated_units():
    from agent_dispatch.supervisor_daemon import companion_authority_fingerprint

    registration = _companion_reg("queued")
    registration["machine"] = "host"
    controller = FakeCompanionController()
    daemon = SupervisorDaemon(
        FakeClient([registration, _reg("unrelated", machine="host")]), "host",
        launcher=FakeLauncher(), companion_controller=controller,
    )
    release = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        daemon._provider_executor = pool
        occupied = pool.submit(lambda: release.wait(3))
        try:
            daemon._reconcile_deadline = time.monotonic() + 0.05
            with pytest.raises(CompanionIndeterminate, match="pending"):
                daemon._resolve_companion("queued", registration, companion_authority_fingerprint(registration))
            assert daemon._resolution_futures["queued"][1].cancel()
            assert daemon.reconcile_once().started == ["unrelated"]
            assert "queued" not in daemon._resolution_futures
            release.set()
            occupied.result(1)
            assert daemon.reconcile_once().started == ["queued"]
        finally:
            release.set()
            daemon.shutdown()


def test_cancelled_registry_read_preserves_confirmed_snapshot():
    daemon = SupervisorDaemon(
        FakeClient([_reg("unrelated", machine="host")]), "host",
        launcher=FakeLauncher(), companion_controller=FakeCompanionController(),
    )
    release = threading.Event()
    assert [reg["id"] for reg in daemon._store_registrations()] == ["unrelated"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        daemon._read_executor = pool
        occupied = pool.submit(lambda: release.wait(3))
        try:
            daemon._reconcile_deadline = time.monotonic() + 0.05
            assert [reg["id"] for reg in daemon._store_registrations()] == ["unrelated"]
            assert daemon._discovery_futures["registrations"].cancel()
            assert daemon.reconcile_once().started == ["unrelated"]
            assert "registrations" not in daemon._discovery_futures
        finally:
            release.set()
            occupied.result(1)
            daemon.shutdown()
