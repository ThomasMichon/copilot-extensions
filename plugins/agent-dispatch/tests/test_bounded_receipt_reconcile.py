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


def _receipt(controller, rid="withdrawn"):
    controller.receipt_dir.mkdir(parents=True, exist_ok=True)
    value = {
        "schema_version": 1, "registration_id": rid, "pid": 100,
        "start_token": "child-generation", "fingerprint": "exact-generation",
    }
    controller._receipt_path(rid).write_text(json.dumps(value), encoding="utf-8")
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
            assert replacement._receipt_operations == {}
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
    script = (
        "import os,sys,subprocess,time;"
        "from agent_procutil import no_window_kwargs;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(3.5)'],"
        "stdout=sys.stdout,stderr=sys.stderr,close_fds=False,"
        "**(no_window_kwargs() if os.name=='nt' else {}));"
        "open(sys.argv[1],'w').write(str(child.pid));time.sleep(10)"
    )
    ready = tmp_path / "descendant-ready"
    started = time.monotonic()
    try:
        with pytest.raises(CompanionIndeterminate, match="timed out"):
            companion._run_captured(
                (sys.executable, "-c", script, str(ready)), cwd=str(tmp_path),
                environment=dict(os.environ), timeout=1.0,
            )
        assert ready.exists()
        assert companion._process_exists(int(ready.read_text()))
        elapsed = time.monotonic() - started
        print(f"ready inherited-pipe timeout elapsed={elapsed:.3f}s")
        assert 1.8 <= elapsed < 2.8
    finally:
        # The contained fixture's descendant exits on its own bounded clock.
        time.sleep(max(0, 4.5 - (time.monotonic() - started)))
