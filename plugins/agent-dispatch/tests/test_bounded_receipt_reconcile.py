"""Bounded receipt operations use real locks, journals and captured subprocesses."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_dispatch import companion, managed_retention
from agent_dispatch.companion import CompanionIndeterminate, DefaultCompanionController
from agent_dispatch.managed_runtime import (
    ManagedRuntimeError, ManagedRuntimeLockTimeout, _RootLock, _tree_digest,
)
from agent_dispatch.supervisor_daemon import SupervisorDaemon
from agent_dispatch.supervisor_health import supervisor_progress_observation
from tests.test_managed_retention import RetentionHarness
from tests.test_supervisor_daemon import FakeClient, FakeCompanionController, FakeLauncher, _reg


def _receipt(controller, rid="withdrawn", *, owner_ready=True):
    controller.receipt_dir.mkdir(parents=True, exist_ok=True)
    value = {
        "schema_version": 1, "registration_id": rid, "pid": 100,
        "start_token": "child-generation", "fingerprint": "exact-generation",
    }
    controller._receipt_path(rid).write_text(json.dumps(value), encoding="utf-8")
    if owner_ready:
        controller._receipt_owner_identity = (os.getpid(), controller.token_source(os.getpid()))
    return value


def test_receipt_integrity_pending_does_not_block_unrelated_units(tmp_path, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    _receipt(controller)
    monkeypatch.setattr(controller, "selected_managed", lambda rid: object())

    class Retention:
        def validate_withdrawal(self, *args, **kwargs):
            entered.set()
            assert release.wait(3)
            raise ManagedRuntimeLockTimeout("injected hung integrity")

    monkeypatch.setattr(controller, "_managed_retention", lambda: Retention())
    launcher = FakeLauncher()
    daemon = SupervisorDaemon(
        FakeClient([_reg("unrelated", machine="host")]), "host", launcher=launcher,
        companion_controller=controller, reconcile_call_budget=0.05,
    )
    try:
        started = time.monotonic()
        assert daemon.reconcile_once().started == ["unrelated"]
        assert time.monotonic() - started < 0.5
        assert entered.wait(1)
        future = controller._receipt_operations["withdrawn"]
        time.sleep(0.06)
        started = time.monotonic()
        daemon.reconcile_once()
        elapsed = time.monotonic() - started
        print(f"pending-receipt cycle elapsed={elapsed:.3f}s")
        assert elapsed < 0.5
        assert controller._receipt_operations["withdrawn"] is future
        inventory = controller._receipt_inventory_future
        if inventory is not None:
            inventory.result(timeout=1)
        assert controller._operation_record("withdrawn")["state"] == "timeout"
        assert controller._receipt_path("withdrawn").exists()
        assert len(launcher.launched) == 1
    finally:
        release.set()
        daemon.shutdown()
    assert controller._operation_record("withdrawn")["state"] == "unknown"
    milestones = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in (controller.receipt_dir / "reconcile-history").glob("*.json")
    ]
    assert {record["state"] for record in milestones} == {"pending", "timeout", "unknown"}
    assert len({record["generation_digest"] for record in milestones}) == 1


def test_unknown_response_restart_reads_back_without_replay(tmp_path, monkeypatch):
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    _receipt(controller)
    calls = []
    monkeypatch.setattr(
        controller, "_retire_receipt",
        lambda *args, **kwargs: calls.append(1),
    )
    # Preserve a lost-response state exactly as a killed process would leave it.
    with ThreadPoolExecutor(max_workers=1) as pool:
        gate = threading.Event()
        occupied = pool.submit(lambda: gate.wait(2))
        try:
            controller.reconcile_receipts_bounded(set(), executor=pool, deadline=time.monotonic() + 1)
            pending = controller._receipt_operations["withdrawn"]
            assert pending.cancel()
            replacement = DefaultCompanionController(controller.receipt_dir, token_source=lambda pid: "new-owner")
            blocked = replacement.reconcile_receipts_bounded(
                set(), executor=pool, deadline=time.monotonic() + 1,
            )
            assert blocked == {"withdrawn"}
            assert replacement._operation_record("withdrawn")["state"] == "unknown"
            readback = replacement._receipt_operations["withdrawn"]
            assert not readback.done()
            with pytest.raises(CompanionIndeterminate, match="not confirmed"):
                replacement._assert_receipt_available("withdrawn")
            with pytest.raises(CompanionIndeterminate, match="not confirmed"):
                replacement.launch(
                    SimpleNamespace(registration={"id": "withdrawn"}),
                    fingerprint="exact-generation",
                )
            assert calls == []
        finally:
            gate.set()
            occupied.result(1)
            readback.result(1)


def _unknown_operation(controller, rid):
    receipt = _receipt(controller, rid)
    controller._receipt_path(rid).unlink()
    generation = {"receipt": receipt, "selection": None}
    from agent_dispatch.managed_runtime import _canonical_digest

    controller._write_operation(rid, {
        "schema_version": 1, "registration_id": rid, "generation": generation,
        "generation_digest": _canonical_digest(generation), "state": "unknown",
        "owner_pid": os.getpid(), "owner_start_token": "owner",
        "started_at": time.time() - 10, "observed_at": time.time(),
        "deadline": time.monotonic() - 1,
    })


def test_unknown_identity_readback_never_runs_on_master_or_multiplies_wait(tmp_path, monkeypatch):
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    for index in range(4):
        _unknown_operation(controller, f"withdrawn-{index}")
    entered, release = threading.Event(), threading.Event()
    threads = []

    def slow_identity(pid):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        return "replacement-generation"

    monkeypatch.setattr(controller, "token_source", slow_identity)
    launcher = FakeLauncher()
    daemon = SupervisorDaemon(
        FakeClient([_reg("unrelated", machine="host")]), "host", launcher=launcher,
        companion_controller=controller, reconcile_call_budget=0.15,
    )
    try:
        readback_deadline = time.monotonic() + 2
        controller.reconcile_receipts_bounded(
            set(), executor=daemon._receipt_pool(), deadline=readback_deadline,
        )
        assert entered.wait(2)
        pending = dict(controller._receipt_operations)
        started = time.monotonic()
        assert daemon.reconcile_once().started == ["unrelated"]
        elapsed = time.monotonic() - started
        print(f"four unresolved identity receipts cycle elapsed={elapsed:.3f}s")
        assert elapsed < 0.5
        assert controller._receipt_operations == pending
        started = time.monotonic()
        daemon.reconcile_once()
        assert time.monotonic() - started < 0.5
        assert controller._receipt_operations == pending
        assert 1 <= len(threads) <= 2
        assert threading.get_ident() not in threads
        assert len(launcher.launched) == 1
        # A proven but late identity response cannot settle authority.
        time.sleep(max(0, readback_deadline - time.monotonic()) + 0.02)
    finally:
        release.set()
        daemon.shutdown()
    assert all(
        controller._operation_record(f"withdrawn-{index}")["state"] == "unknown"
        for index in range(4)
    )


def test_owner_identity_probe_uses_shared_deadline_and_shutdown_accounting(tmp_path, monkeypatch):
    controller = DefaultCompanionController(tmp_path / "receipts")
    _receipt(controller, owner_ready=False)
    entered, release = threading.Event(), threading.Event()
    calls = []

    def slow_owner(pid):
        calls.append((pid, threading.get_ident()))
        entered.set()
        assert release.wait(3)
        return "owner"

    monkeypatch.setattr(controller, "token_source", slow_owner)
    daemon = SupervisorDaemon(
        FakeClient([_reg("unrelated", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=controller, reconcile_call_budget=0.05,
    )
    try:
        started = time.monotonic()
        assert daemon.reconcile_once().started == ["unrelated"]
        elapsed = time.monotonic() - started
        print(f"owner identity shared-budget cycle elapsed={elapsed:.3f}s")
        assert elapsed < 0.5
        assert entered.wait(1)
        deadline = time.monotonic() + 1
        while controller._receipt_owner_future is None and time.monotonic() < deadline:
            time.sleep(0.001)
        future = controller._receipt_owner_future
        assert future is not None
        assert (
            future in daemon._probe_inflight
            or controller._receipt_inventory_future in daemon._probe_inflight
        )
        daemon.reconcile_once()
        assert controller._receipt_owner_future is future
        assert calls == [(os.getpid(), calls[0][1])]
        assert calls[0][1] != threading.get_ident()
        assert controller._receipt_operations == {}
        assert controller._read_receipt("withdrawn") is not None
    finally:
        release.set()
        daemon.shutdown()


def test_partial_receipt_schedule_failure_still_accounts_every_running_mutation(tmp_path, monkeypatch):
    from agent_dispatch import supervisor_daemon

    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    for rid in ("a-withdrawn", "b-withdrawn"):
        _receipt(controller, rid)
    _unknown_operation(controller, "z-malformed")
    malformed = controller._operation_record("z-malformed")
    malformed["generation_digest"] = "invalid"
    controller._operation_path("z-malformed").write_text(json.dumps(malformed), encoding="utf-8")
    entered = {rid: threading.Event() for rid in ("a-withdrawn", "b-withdrawn")}
    release = threading.Event()

    def mutation(receipt, **kwargs):
        entered[receipt["registration_id"]].set()
        assert release.wait(3)

    monkeypatch.setattr(controller, "_retire_receipt", mutation)
    monkeypatch.setattr(supervisor_daemon, "_SHUTDOWN_GRACE_SECONDS", 0.05)
    daemon = SupervisorDaemon(
        FakeClient([_reg("unrelated", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=controller, reconcile_call_budget=1,
    )
    try:
        assert daemon.reconcile_once().started == ["unrelated"]
        assert all(event.wait(1) for event in entered.values())
        running = tuple(controller._receipt_operations.values())
        assert len(running) == 2
        assert (
            set(running) <= daemon._probe_inflight
            or controller._receipt_inventory_future in daemon._probe_inflight
        )
        assert all(not future.done() for future in running)
        started = time.monotonic()
        assert daemon.shutdown() is False
        assert set(running) <= daemon._probe_inflight
        elapsed = time.monotonic() - started
        print(f"partial-schedule failed drain elapsed={elapsed:.3f}s")
        assert elapsed < 0.5
        assert all(
            controller._operation_record(rid)["state"] in {"pending", "timeout", "unknown"}
            for rid in entered
        )
    finally:
        release.set()
        for future in controller._receipt_operations.values():
            future.result(2)
        daemon.shutdown()


@pytest.mark.parametrize("outcome", [OSError("transient"), TimeoutError("transient"), None, "", 123])
def test_completed_owner_probe_failure_can_retry_in_same_daemon(tmp_path, monkeypatch, outcome):
    controller = DefaultCompanionController(tmp_path / "receipts")
    _receipt(controller, owner_ready=False)
    outcomes = [outcome, "owner"]
    calls = []
    retired = []

    def identity(pid):
        calls.append(threading.get_ident())
        value = outcomes.pop(0) if outcomes else "owner"
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(controller, "token_source", identity)
    monkeypatch.setattr(controller, "_retire_receipt", lambda *args, **kwargs: retired.append(1))
    daemon = SupervisorDaemon(
        FakeClient([_reg("unrelated", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=controller, reconcile_call_budget=2,
    )
    try:
        assert daemon.reconcile_once().started == ["unrelated"]
        if controller._receipt_inventory_future is not None:
            with pytest.raises((OSError, CompanionIndeterminate)):
                controller._receipt_inventory_future.result(2)
        assert controller._receipt_owner_future is None
        assert controller._receipt_owner_identity is None
        assert controller._receipt_operations == {}
        assert controller._read_receipt("withdrawn") is not None
        assert daemon.reconcile_once().running == ["unrelated"]
        assert daemon.reconcile_once().running == ["unrelated"]
        if controller._receipt_inventory_future is not None:
            controller._receipt_inventory_future.result(2)
        controller._receipt_operations["withdrawn"].result(2)
        assert controller._receipt_owner_future is None
        assert controller._receipt_owner_identity == (os.getpid(), "owner")
        assert outcomes == []
        assert retired == [1]
        assert len(daemon.launcher.launched) == 1
        assert threading.get_ident() not in calls
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("foreign_domain", [False, True])
def test_absent_receipts_cannot_complete_while_durable_selection_pin_remains(tmp_path, foreign_domain):
    h = RetentionHarness(tmp_path)
    snapshot = h.generation("1")
    rid = snapshot.to_dict()["registration"]["id"]
    keeper = h.make_retention(domain="b" * 64) if foreign_domain else h.retention
    keeper.select(snapshot, h.state)
    controller = DefaultCompanionController(h.state, retention=h.retention, token_source=h.tokens.get)
    _unknown_operation(controller, rid)
    pins = {path: path.read_bytes() for path in h.record_paths("selections")}
    with ThreadPoolExecutor(max_workers=1) as pool:
        assert controller.reconcile_receipts_bounded(
            set(), executor=pool, deadline=time.monotonic() + 2,
        ) == {rid}
        controller._receipt_operations[rid].result(2)
    assert controller._receipt_generation(rid) == {"receipt": None, "selection": None}
    assert controller._operation_record(rid)["state"] == "unknown"
    assert {path: path.read_bytes() for path in h.record_paths("selections")} == pins
    assert snapshot.runtimes[0].cell.exists()
    with pytest.raises(CompanionIndeterminate, match="not confirmed"):
        controller._assert_receipt_available(rid)


def test_retirement_cannot_complete_or_replay_when_only_root_pin_survives(tmp_path, monkeypatch):
    h = RetentionHarness(tmp_path)
    snapshot = h.generation("1")
    rid = snapshot.to_dict()["registration"]["id"]
    h.retention.select(snapshot, h.state)
    controller = DefaultCompanionController(h.state, retention=h.retention, token_source=h.tokens.get)
    _receipt(controller, rid)
    retired = []
    monkeypatch.setattr(controller, "_retire_receipt", lambda *args, **kwargs: retired.append(1))
    with ThreadPoolExecutor(max_workers=1) as pool:
        controller.reconcile_receipts_bounded(set(), executor=pool, deadline=time.monotonic() + 2)
        with pytest.raises(CompanionIndeterminate, match="withdrawal is not confirmed"):
            controller._receipt_operations[rid].result(2)
        assert controller._receipt_generation(rid) == {"receipt": None, "selection": None}
        assert controller._operation_record(rid)["state"] == "unknown"
        controller.reconcile_receipts_bounded(set(), executor=pool, deadline=time.monotonic() + 2)
        controller._receipt_operations[rid].result(2)
    assert controller._operation_record(rid)["state"] == "unknown"
    assert h.record_paths("selections")
    assert retired == [1]


def test_readback_completes_only_after_durable_pin_is_verified_withdrawn(tmp_path):
    h = RetentionHarness(tmp_path)
    snapshot = h.generation("1")
    rid = snapshot.to_dict()["registration"]["id"]
    h.retention.select(snapshot, h.state)
    controller = DefaultCompanionController(h.state, retention=h.retention, token_source=h.tokens.get)
    _unknown_operation(controller, rid)
    with ThreadPoolExecutor(max_workers=1) as pool:
        controller.reconcile_receipts_bounded(set(), executor=pool, deadline=time.monotonic() + 2)
        controller._receipt_operations[rid].result(2)
        assert controller._operation_record(rid)["state"] == "unknown"
        h.retention.forget(rid, h.state)
        controller.reconcile_receipts_bounded(set(), executor=pool, deadline=time.monotonic() + 2)
        controller._receipt_operations[rid].result(2)
    assert controller._operation_record(rid)["state"] == "complete"
    assert h.record_paths("selections") == []
    controller._assert_receipt_available(rid)


def test_pin_absence_probe_bounds_root_lock_contention(tmp_path):
    h = RetentionHarness(tmp_path)
    snapshot = h.generation("1")
    rid = snapshot.to_dict()["registration"]["id"]
    h.retention.select(snapshot, h.state)
    with _RootLock(h.policy.root):
        started = time.monotonic()
        with pytest.raises(ManagedRuntimeLockTimeout):
            h.retention.selection_withdrawn(rid, h.state, deadline=started + 0.05)
        elapsed = time.monotonic() - started
        print(f"durable-pin readback contention elapsed={elapsed:.3f}s")
        assert elapsed < 0.7
    assert h.record_paths("selections")


def test_readback_rechecks_receipt_absence_after_identity_probe(tmp_path, monkeypatch):
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    _unknown_operation(controller, "withdrawn")
    calls = []
    monkeypatch.setattr(controller, "_retire_receipt", lambda *args, **kwargs: calls.append(1))

    def identity(pid):
        replacement = {
            "schema_version": 1, "registration_id": "withdrawn", "pid": 101,
            "start_token": "new-generation", "fingerprint": "new-selection",
        }
        controller._receipt_path("withdrawn").write_text(json.dumps(replacement), encoding="utf-8")
        return "replacement-generation"

    monkeypatch.setattr(controller, "token_source", identity)
    with ThreadPoolExecutor(max_workers=1) as pool:
        blocked = controller.reconcile_receipts_bounded(
            set(), executor=pool, deadline=time.monotonic() + 1,
        )
        assert blocked == {"withdrawn"}
        controller._receipt_operations["withdrawn"].result(1)
    assert controller._operation_record("withdrawn")["state"] == "unknown"
    assert controller._read_receipt("withdrawn")["start_token"] == "new-generation"
    assert calls == []


def test_failed_receipt_deletion_keeps_unknown_and_never_retires_twice(tmp_path, monkeypatch):
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    receipt = _receipt(controller)
    calls = []
    monkeypatch.setattr(controller, "_retire_receipt", lambda *args, **kwargs: calls.append(1))
    original = type(controller.receipt_dir).unlink

    def unlink(path, *args, **kwargs):
        if path == controller._receipt_path("withdrawn"):
            raise PermissionError("injected receipt deletion failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(controller.receipt_dir), "unlink", unlink)
    with ThreadPoolExecutor(max_workers=2) as pool:
        controller.reconcile_receipts_bounded(set(), executor=pool, deadline=time.monotonic() + 1)
        with pytest.raises(CompanionIndeterminate, match="deletion is not confirmed"):
            controller._receipt_operations["withdrawn"].result(1)
        for _ in range(3):
            blocked = controller.reconcile_receipts_bounded(
                set(), executor=pool, deadline=time.monotonic() + 1,
            )
            assert blocked == {"withdrawn"}
            controller._receipt_operations["withdrawn"].result(1)
        assert calls == [1]
        assert controller._operation_record("withdrawn")["state"] == "unknown"
        assert controller._read_receipt("withdrawn") == receipt
        with pytest.raises(CompanionIndeterminate, match="not confirmed"):
            controller.launch(SimpleNamespace(registration={"id": "withdrawn"}), fingerprint="same")


@pytest.mark.parametrize("failure", ["missing", "corrupt", "unwritable"])
def test_status_io_failure_cannot_abort_desired_work(tmp_path, monkeypatch, caplog, failure):
    from agent_dispatch import supervise_cli

    path = tmp_path / "status.json"
    monkeypatch.setattr(supervise_cli, "_supervisor_runtime_status_path", lambda scope: path)
    if failure == "corrupt":
        path.write_text("{invalid", encoding="utf-8")
    elif failure == "unwritable":
        path.write_text(json.dumps({
            "cycle_id": 1, "generation": {"pid": os.getpid(), "start_token": "fixture"},
        }), encoding="utf-8")
        original = type(path).write_text

        def write(candidate, *args, **kwargs):
            if candidate.parent == path.parent and candidate.suffix == ".tmp":
                raise PermissionError("sensitive observation contents")
            return original(candidate, *args, **kwargs)

        monkeypatch.setattr(type(path), "write_text", write)
    client = FakeClient([_reg("unrelated", machine="host")])
    daemon = SupervisorDaemon(
        client, "host", launcher=FakeLauncher(), companion_controller=FakeCompanionController(),
    )
    daemon._cycle_count = 1
    daemon._progress_callback = lambda progress: supervise_cli._write_supervisor_progress("test", progress)
    try:
        assert daemon.reconcile_once().started == ["unrelated"]
        client.set_regs([_reg("unrelated", machine="host"), _reg("another", machine="host")])
        assert daemon.reconcile_once().started == ["another"]
        daemon._progress_future.result(2)
        assert daemon._reconcile_deadline is None
        warnings = [
            record.getMessage() for record in caplog.records
            if "optional progress unavailable" in record.getMessage()
        ]
        assert len(warnings) == 1
        assert "publication-unavailable" in warnings[0]
        assert len(warnings[0]) < 180
        assert "sensitive observation contents" not in caplog.text
        assert str(path) not in caplog.text
    finally:
        daemon.shutdown()


def test_completion_progress_failure_cannot_hide_completed_mutation(caplog):
    daemon = SupervisorDaemon(
        FakeClient([_reg("unrelated", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(),
    )

    def completion(progress):
        if progress["phase"] == "complete":
            raise RuntimeError("sensitive observation contents")

    daemon._progress_callback = completion
    try:
        summary = daemon.reconcile_once()
        assert summary.started == summary.running == ["unrelated"]
        assert summary.progress["phase"] == "complete"
        assert daemon._reconcile_deadline is None
        assert daemon.reconcile_once().started == []
        daemon._progress_future.result(2)
        assert len(daemon.launcher.launched) == 1
        assert "publication-unavailable" in caplog.text
        assert "sensitive observation contents" not in caplog.text
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("error", [OSError, ValueError, ManagedRuntimeError, CompanionIndeterminate])
def test_receipt_progress_failure_is_unknown_observation_not_control_failure(caplog, error):
    controller = FakeCompanionController()

    def unavailable():
        raise error("sensitive observation contents")

    controller.receipt_operation_progress = unavailable
    daemon = SupervisorDaemon(
        FakeClient([_reg("unrelated", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=controller,
    )
    observations = []
    daemon._progress_callback = observations.append
    try:
        summary = daemon.reconcile_once()
        assert summary.started == ["unrelated"]
        assert summary.progress["receipt_operations"] is None
        daemon._progress_future.result(2)
        assert daemon.progress_snapshot()["observation_error"] == "receipt-progress-unavailable"
        assert all(item["receipt_operations"] is None for item in observations)
        warnings = [
            record.getMessage() for record in caplog.records
            if "optional progress unavailable" in record.getMessage()
        ]
        assert len(warnings) == 1
        assert "receipt-progress-unavailable" in warnings[0]
        assert "sensitive observation contents" not in caplog.text
    finally:
        daemon.shutdown()


def test_exact_generation_and_readopted_receipt_are_not_retired(tmp_path, monkeypatch):
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    _receipt(controller)
    calls = []
    monkeypatch.setattr(controller, "_retire_receipt", lambda *args, **kwargs: calls.append(1))
    with ThreadPoolExecutor(max_workers=1) as pool:
        gate = threading.Event()
        occupied = pool.submit(lambda: gate.wait(2))
        try:
            controller.reconcile_receipts_bounded(set(), executor=pool, deadline=time.monotonic() + 1)
            future = controller._receipt_operations["withdrawn"]
            record = _receipt(controller)
            record["start_token"] = "replacement-generation"
            controller._receipt_path("withdrawn").write_text(json.dumps(record), encoding="utf-8")
            controller.reconcile_receipts_bounded(
                {"withdrawn"}, executor=pool, deadline=time.monotonic() + 1,
            )
            gate.set()
            occupied.result(1)
            future.result(1)
            assert calls == []
            assert controller._operation_record("withdrawn")["state"] == "superseded"
            assert controller._read_receipt("withdrawn") == record
        finally:
            gate.set()


def test_pending_operation_respects_receipt_owner_lock(tmp_path, monkeypatch):
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    _receipt(controller)
    calls = []
    monkeypatch.setattr(controller, "_retire_receipt", lambda *args, **kwargs: calls.append(1))
    from agent_dispatch.managed_runtime import _canonical_digest

    with _RootLock(controller.receipt_dir, scope=_canonical_digest("withdrawn")):
        with ThreadPoolExecutor(max_workers=1) as pool:
            controller.reconcile_receipts_bounded(set(), executor=pool, deadline=time.monotonic() + 1)
            future = controller._receipt_operations["withdrawn"]
            started = time.monotonic()
            with pytest.raises(ManagedRuntimeLockTimeout):
                future.result(1)
            elapsed = time.monotonic() - started
            print(f"root-lock contention elapsed={elapsed:.3f}s")
            assert elapsed < 0.7
    assert calls == []
    assert controller._operation_record("withdrawn")["state"] == "unknown"


def test_foreign_operation_owner_cannot_mutate_receipt(tmp_path, monkeypatch):
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    receipt = _receipt(controller)
    calls = []
    monkeypatch.setattr(controller, "_retire_receipt", lambda *args, **kwargs: calls.append(1))
    with ThreadPoolExecutor(max_workers=1) as pool:
        gate = threading.Event()
        occupied = pool.submit(lambda: gate.wait(2))
        try:
            controller.reconcile_receipts_bounded(set(), executor=pool, deadline=time.monotonic() + 1)
            future = controller._receipt_operations["withdrawn"]
            record = controller._operation_record("withdrawn")
            record["owner_start_token"] = "foreign-owner-generation"
            controller._operation_path("withdrawn").write_text(json.dumps(record), encoding="utf-8")
            gate.set()
            occupied.result(1)
            with pytest.raises(CompanionIndeterminate, match="owner is superseded"):
                future.result(1)
            assert controller._read_receipt("withdrawn") == receipt
            assert controller._operation_record("withdrawn") == record
            assert calls == []
        finally:
            gate.set()


def test_integrity_hash_does_not_hold_root_bookkeeping_lock(tmp_path, monkeypatch):
    h = RetentionHarness(tmp_path)
    snapshot = h.generation("1")
    h.retention.select(snapshot, h.state)
    entered, release = threading.Event(), threading.Event()
    original = managed_retention._tree_digest

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)

    monkeypatch.setattr(managed_retention, "_tree_digest", blocked)
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(h.retention.forget, snapshot.to_dict()["registration"]["id"], h.state)
        try:
            assert entered.wait(1)
            started = time.monotonic()
            with _RootLock(h.policy.root, timeout=0.2):
                assert time.monotonic() - started < 0.3
            assert h.record_paths("selections")
        finally:
            release.set()
        future.result(2)
    assert h.record_paths("selections") == []


def test_changed_selection_after_integrity_preserves_pin(tmp_path, monkeypatch):
    h = RetentionHarness(tmp_path)
    first, second = h.generation("1"), h.generation("2")
    h.retention.select(first, h.state)
    original = managed_retention._tree_digest
    changed = False

    def replace_selection(*args, **kwargs):
        nonlocal changed
        if not changed:
            changed = True
            monkeypatch.setattr(managed_retention, "_tree_digest", original)
            h.retention.select(second, h.state)
        return original(*args, **kwargs)

    monkeypatch.setattr(managed_retention, "_tree_digest", replace_selection)
    with pytest.raises(ManagedRuntimeError, match="changed during validation"):
        h.retention.forget(first.to_dict()["registration"]["id"], h.state)
    assert h.record_paths("selections")


def test_root_contention_honors_operation_deadline(tmp_path):
    h = RetentionHarness(tmp_path)
    snapshot = h.generation("1")
    h.retention.select(snapshot, h.state)
    with _RootLock(h.policy.root):
        started = time.monotonic()
        with pytest.raises(ManagedRuntimeLockTimeout):
            h.retention.validate_withdrawal(
                snapshot.to_dict()["registration"]["id"], h.state, deadline=started + 0.05,
            )
        assert time.monotonic() - started < 0.7
    assert h.record_paths("selections")


def test_retirement_finishes_metadata_without_rehashing_expired_execution_budget(
    tmp_path, monkeypatch,
):
    from agent_dispatch.managed_runtime import _canonical_digest

    h = RetentionHarness(tmp_path)
    snapshot = h.generation("1")
    rid = snapshot.to_dict()["registration"]["id"]
    controller = DefaultCompanionController(h.state, retention=h.retention, token_source=h.tokens.get)
    controller._publish_selection(snapshot)
    _receipt(controller, rid)
    generation = controller._receipt_generation(rid)
    operation = {
        "schema_version": 1, "registration_id": rid, "generation": generation,
        "generation_digest": _canonical_digest(generation), "state": "pending",
        "owner_pid": os.getpid(), "owner_start_token": h.tokens[os.getpid()],
        "started_at": time.time(), "observed_at": time.time(),
        "deadline": time.monotonic() + 2,
    }
    controller._write_operation(rid, operation)
    retired = []
    hashes = []
    original_digest = managed_retention._tree_digest

    def digest(*args, **kwargs):
        assert retired == [], "runtime integrity must precede irreversible retirement"
        hashes.append(1)
        return original_digest(*args, **kwargs)

    def retire(*args, **kwargs):
        retired.append(1)
        operation["deadline"] = time.monotonic() - 1

    monkeypatch.setattr(managed_retention, "_tree_digest", digest)
    monkeypatch.setattr(controller, "_retire_receipt", retire)
    controller._reconcile_receipt_generation(rid, operation)
    assert hashes
    assert retired == [1]
    assert controller._operation_record(rid)["state"] == "complete"
    assert controller._receipt_generation(rid) == {"receipt": None, "selection": None}
    assert h.record_paths("selections") == []
    assert snapshot.runtimes[0].cell.exists()


@pytest.mark.parametrize("error", [CompanionIndeterminate, TimeoutError])
def test_legacy_receipt_failure_clears_completed_future_for_next_cycle(error):
    controller = FakeCompanionController()
    attempts = []

    def reconcile(ids):
        attempts.append(ids)
        if len(attempts) == 1:
            raise error("transient legacy receipt failure")

    controller.reconcile_receipts = reconcile
    daemon = SupervisorDaemon(
        FakeClient([]), "host", launcher=FakeLauncher(),
        companion_controller=controller, reconcile_call_budget=1,
    )
    try:
        daemon._reconcile_deadline = time.monotonic() + 2
        with pytest.raises(error, match="transient legacy"):
            daemon._reconcile_companion_receipts(set())
        assert daemon._receipt_reconcile_future is None
        daemon._reconcile_deadline = time.monotonic() + 2
        daemon._reconcile_companion_receipts(set())
        assert len(attempts) == 2
        assert daemon._receipt_reconcile_future is None
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("mutation", ["replacement", "foreign-receipt"])
def test_validated_withdrawal_rechecks_exact_metadata_before_unlink(tmp_path, mutation):
    h = RetentionHarness(tmp_path)
    first = h.generation("1")
    rid = first.to_dict()["registration"]["id"]
    h.retention.select(first, h.state)
    validated = h.retention.validate_withdrawal(rid, h.state, deadline=time.monotonic() + 2)
    target_state = h.state
    if mutation == "replacement":
        h.retention.select(h.generation("2"), h.state)
        message = "changed during validation"
    else:
        target_state = h.state.with_name("other-receipt")
        message = "different selection"
    pins = {path: path.read_bytes() for path in h.record_paths("selections")}
    with pytest.raises(ManagedRuntimeError, match=message):
        with h.retention.withdraw_selection(
            rid, target_state, validated=validated, deadline=time.monotonic() + 1,
        ):
            pytest.fail("invalid metadata reached the mutation boundary")
    assert {path: path.read_bytes() for path in h.record_paths("selections")} == pins


@pytest.mark.parametrize("finished", [None, 201.0])
def test_progress_observation_refuses_future_cycle_start(finished):
    observation = supervisor_progress_observation(
        {
            "generation": {"pid": 123, "start_token": "owner"},
            "cycle_started_at": 200.0, "updated_at": finished,
        },
        token_source=lambda pid: "owner", now=100.0,
    )
    assert observation["state"] == "unknown"
    assert observation["recovery_permitted"] is False
    assert observation["retain_authority"] is True


def test_integrity_deadline_never_accepts_partial_digest(tmp_path):
    (tmp_path / "file").write_bytes(b"content")
    started = time.monotonic()
    with pytest.raises(ManagedRuntimeLockTimeout, match="integrity deadline"):
        _tree_digest(tmp_path, deadline=started - 1)
    assert time.monotonic() - started < 0.2


@pytest.mark.parametrize("token,expected", [
    ("same-generation", "stalled"),
    ("reused-pid", "generation-unconfirmed"),
    (None, "generation-unconfirmed"),
])
def test_independent_watchdog_seam_never_authorizes_duplicate(token, expected):
    status = {
        "generation": {"pid": 123, "start_token": "same-generation"},
        "cycle_started_at": 100.0, "updated_at": 99.0, "cycle_id": 7,
        "progress": {
            "phase": "receipts",
            "receipt_operations": [{"state": "unknown", "generation_digest": "a" * 64}],
        },
    }
    observation = supervisor_progress_observation(
        status, token_source=lambda pid: token, now=400.0,
    )
    assert observation["state"] == expected
    assert observation["recovery_permitted"] is False
    assert observation["retain_authority"] is True


def test_native_first_touch_progress_writer(tmp_path, monkeypatch):
    from agent_dispatch import supervise_cli

    path = tmp_path / "supervisor.status.json"
    monkeypatch.setattr(supervise_cli, "_supervisor_runtime_status_path", lambda scope: path)
    started = time.monotonic()
    supervise_cli._write_supervisor_cycle_start("isolated", 1, time.time() - 200)
    supervise_cli._write_supervisor_progress("isolated", {
        "cycle_id": 1, "phase": "receipts", "receipt_operations": [],
    })
    status, error = supervise_cli._read_supervisor_runtime_status("isolated")
    assert error is None
    assert status["generation"]["pid"] == os.getpid()
    assert status["generation"]["start_token"] == companion.process_start_token(os.getpid())
    observation = supervisor_progress_observation(
        status, token_source=companion.process_start_token,
    )
    assert observation["state"] == "stalled"
    assert observation["recovery_permitted"] is False
    assert time.monotonic() - started < 0.5


def test_discovery_and_receipt_wait_share_one_cycle_budget():
    gate = threading.Event()
    controller = FakeCompanionController()
    original = controller.reconcile_receipts

    def stuck(ids):
        assert gate.wait(3)
        original(ids)

    daemon = SupervisorDaemon(
        FakeClient([_reg("unrelated", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=controller, reconcile_call_budget=0.05,
    )
    try:
        assert daemon.reconcile_once().started == ["unrelated"]
        controller.reconcile_receipts = stuck
        daemon.declared_source = lambda: (gate.wait(3) and [])
        started = time.monotonic()
        assert daemon.reconcile_once().running == ["unrelated"]
        assert time.monotonic() - started < 0.25
        future = daemon._receipt_reconcile_future
        daemon.reconcile_once()
        assert daemon._receipt_reconcile_future is future
    finally:
        gate.set()
        daemon.shutdown()


def test_native_captured_timeout_bounds_inherited_descendant_pipes(tmp_path):
    # A first-touch subprocess, not a mocked communicate boundary.
    child_code = (
        "import os,sys,time\n"
        "end=time.monotonic()+15\n"
        "while not os.path.exists(sys.argv[1]) and time.monotonic()<end: time.sleep(0.05)\n"
        "open(sys.argv[2],'w').write('done')\n"
    )
    script = (
        "import os,sys,subprocess,time;"
        "from agent_procutil import no_window_kwargs;"
        f"child=subprocess.Popen([sys.executable,'-c',{child_code!r},sys.argv[2],sys.argv[3]],"
        "stdout=sys.stdout,stderr=sys.stderr,close_fds=False,"
        "**(no_window_kwargs() if os.name=='nt' else {}));"
        "open(sys.argv[1],'w').write(str(child.pid));time.sleep(10)"
    )
    ready = tmp_path / "descendant-ready"
    release = tmp_path / "descendant-release"
    exited = tmp_path / "descendant-exited"
    started = time.monotonic()
    try:
        with pytest.raises(CompanionIndeterminate, match="timed out"):
            companion._run_captured(
                (sys.executable, "-c", script, str(ready), str(release), str(exited)), cwd=str(tmp_path),
                environment=dict(os.environ), timeout=4.0,
            )
        assert ready.exists()
        assert companion._process_exists(int(ready.read_text()))
        elapsed = time.monotonic() - started
        print(f"ready inherited-pipe timeout elapsed={elapsed:.3f}s")
        assert 4.8 <= elapsed < 5.8
    finally:
        release.write_text("release", encoding="utf-8")
        cleanup_deadline = time.monotonic() + 5
        while ready.exists() and not exited.exists() and time.monotonic() < cleanup_deadline:
            time.sleep(0.05)
        if ready.exists():
            assert exited.exists(), "fixture descendant did not acknowledge release"
