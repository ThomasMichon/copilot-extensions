"""Reclaimed-master pools, publication custody and optional progress contracts."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_dispatch import supervisor_daemon
from agent_dispatch.companion import CompanionIndeterminate
from agent_dispatch.supervisor_daemon import SupervisorDaemon
from agent_dispatch.supervisor_health import supervisor_progress_observation
from tests.test_supervisor_daemon import FakeClient, FakeCompanionController, FakeLauncher, FakeLock, _reg


def test_failed_self_update_reclaims_with_live_pools_and_tracks_old_work(tmp_path, monkeypatch):
    gate = threading.Event()
    daemon = SupervisorDaemon(
        FakeClient([]), "host", launcher=FakeLauncher(), lock=FakeLock(granted=True),
        companion_controller=FakeCompanionController(), self_update_enabled=True,
        self_update_argv=["supervise", "serve"], self_update_install_dir=lambda: tmp_path,
        self_update_stale_target=lambda *_: tmp_path / "next",
        self_update_spawn=lambda *_: (_ for _ in ()).throw(OSError("injected spawn failure")),
    )
    monkeypatch.setattr(supervisor_daemon, "native_restart_unit", lambda: None)
    monkeypatch.setattr(supervisor_daemon, "_SHUTDOWN_GRACE_SECONDS", 0.05)
    old_read, old_receipt = daemon._read_pool(), daemon._receipt_pool()
    futures = [pool.submit(lambda: gate.wait(3)) for pool in (old_read, old_receipt)]
    daemon._discovery_futures["held"] = futures[0]
    daemon._receipt_reconcile_future = futures[1]
    daemon._probe_inflight.update(futures)
    try:
        assert daemon._maybe_self_update() is False
        assert all(not future.done() for future in futures)
        assert set(futures) <= daemon._probe_inflight
        assert daemon._read_pool() is not old_read
        assert daemon._receipt_pool() is not old_receipt
        assert daemon._discovery_futures["held"] is futures[0]
        assert daemon._receipt_reconcile_future is futures[1]
        assert {old_read, old_receipt} <= set(daemon._retired_bounded_executors)
        assert daemon._read_pool().submit(lambda: "read").result(1) == "read"
        assert daemon._receipt_pool().submit(lambda: "receipt").result(1) == "receipt"
        assert daemon.shutdown() is False
    finally:
        gate.set()
        for future in futures:
            future.result(1)
        daemon.shutdown()


@pytest.mark.parametrize("error", [TypeError, RuntimeError])
def test_final_snapshot_data_state_failure_cannot_hide_mutation(error, caplog):
    controller = FakeCompanionController()
    controller.receipt_operation_progress = lambda: (_ for _ in ()).throw(error("private detail"))
    daemon = SupervisorDaemon(
        FakeClient([_reg("unit", machine="host")]), "host",
        launcher=FakeLauncher(), companion_controller=controller,
    )
    try:
        summary = daemon.reconcile_once()
        daemon._progress_future.result(2)
        assert summary.started == ["unit"]
        assert summary.progress["receipt_operations"] is None
        assert daemon.progress_snapshot()["observation_error"] == "receipt-progress-unavailable"
        assert "private detail" not in caplog.text
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("progress,expected", [
    ({"cycle_id": 7, "observed_at": 399.0}, "progressing"),
    ({"cycle_id": 6, "observed_at": 399.0}, "stalled"),
    ({"cycle_id": 7, "observed_at": 401.0}, "stalled"),
    ({"cycle_id": 7, "observed_at": float("nan")}, "stalled"),
    ({"cycle_id": 7, "observed_at": True}, "stalled"),
    ({"cycle_id": 7, "observed_at": 90.0}, "stalled"),
    ({"cycle_id": 7, "observed_at": 399.0, "generation": {"pid": 124, "start_token": "other"}}, "stalled"),
])
def test_current_cycle_progress_not_cycle_age_controls_liveness(progress, expected):
    observation = supervisor_progress_observation({
        "generation": {"pid": 123, "start_token": "exact"},
        "cycle_id": 7, "cycle_started_at": 100.0, "updated_at": 99.0,
        "progress": progress,
    }, token_source=lambda pid: "exact", now=400.0)
    assert observation["state"] == expected
    assert observation["recovery_permitted"] is False


def test_declaration_publication_is_bounded_and_does_not_duplicate_pending_write(monkeypatch):
    from agent_dispatch import registrar_reconcile

    monkeypatch.setattr(registrar_reconcile, "declared_registrations", lambda data, **kwargs: data)
    gate, entered = threading.Event(), threading.Event()

    class Client(FakeClient):
        def __init__(self):
            super().__init__([])
            self.writes = []

        def register_registration(self, kind, spec, **kwargs):
            self.writes.append(kwargs["reg_id"])
            entered.set()
            assert gate.wait(3)

    client = Client()
    daemon = SupervisorDaemon(
        client, "host", launcher=FakeLauncher(), companion_controller=FakeCompanionController(),
        declared_source=lambda: [_reg("declared:one", machine="host")],
        reconcile_call_budget=0.05,
    )
    try:
        assert daemon._store_registrations() == []
        started = time.monotonic()
        assert daemon.reconcile_once().started == ["declared:one"]
        assert time.monotonic() - started < 0.5
        deadline = time.monotonic() + 1
        while not entered.is_set() and time.monotonic() < deadline:
            cycle_started = time.monotonic()
            daemon.reconcile_once()
            assert time.monotonic() - cycle_started < 0.5
            entered.wait(0.01)
        assert entered.is_set()
        daemon.reconcile_once()
        assert client.writes == ["declared:one"]
        assert any(not future.done() for future in daemon._probe_inflight)
    finally:
        gate.set()
        daemon.shutdown()


def test_publication_timeout_readback_precedes_retry_and_preserves_stale_suppression():
    from agent_dispatch.supervisor_daemon_registration_publish import BoundedDeclarationPublisher

    class Client:
        def __init__(self):
            self.rows = {}
            self.writes = []
            self.removes = []
            self.fail_publish = True
            self.fail_remove = True

        def register_registration(self, kind, spec, **kwargs):
            rid = kwargs["reg_id"]
            self.writes.append(rid)
            self.rows[rid] = {"id": rid, "kind": kind, "spec": spec, "machine": kwargs["machine"], "env": kwargs["env"]}
            if self.fail_publish:
                self.fail_publish = False
                raise TimeoutError("response lost after publication")

        def remove_registration(self, rid):
            self.removes.append(rid)
            self.rows.pop(rid, None)
            if self.fail_remove:
                self.fail_remove = False
                raise TimeoutError("response lost after withdrawal")

        def list_registrations(self, **kwargs):
            return list(self.rows.values())

    client = Client()
    publisher = BoundedDeclarationPublisher()
    with ThreadPoolExecutor(max_workers=1) as pool:
        publisher.poll(client, [_reg("declared:one")], executor=pool, deadline=time.monotonic() + 1)
        publisher.poll(client, [_reg("declared:one")], executor=pool, deadline=time.monotonic() + 1)
        assert client.writes == ["declared:one"]
        assert "declared:one" in publisher.poll(client, [], executor=pool, deadline=time.monotonic() + 1)
        publisher.poll(client, [], executor=pool, deadline=time.monotonic() + 1)
        assert client.removes == ["declared:one"]
        assert publisher.poll(client, [], executor=pool, deadline=time.monotonic() + 1) == set()


def test_slow_declaration_conversion_is_inside_shared_budget(monkeypatch):
    from agent_dispatch import registrar_reconcile

    entered, release = threading.Event(), threading.Event()

    def conversion(data, **kwargs):
        entered.set()
        assert release.wait(3)
        return data

    monkeypatch.setattr(registrar_reconcile, "declared_registrations", conversion)
    daemon = SupervisorDaemon(
        FakeClient([_reg("unrelated", machine="host")]), "host", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(), declared_source=lambda: [],
        reconcile_call_budget=0.05,
    )
    try:
        started = time.monotonic()
        with pytest.raises(CompanionIndeterminate, match="confirmed declaration snapshot"):
            daemon.reconcile_once()
        assert time.monotonic() - started < 0.5
        assert entered.wait(1)
        assert any(not future.done() for future in daemon._probe_inflight)
    finally:
        release.set()
        daemon.shutdown()


def test_publication_cancellation_preserves_owned_ids_until_readback():
    from agent_dispatch.supervisor_daemon_registration_publish import BoundedDeclarationPublisher

    class Client:
        def list_registrations(self, **kwargs):
            return []

    publisher = BoundedDeclarationPublisher()
    gate = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        occupied = pool.submit(lambda: gate.wait(3))
        try:
            assert publisher.poll(
                Client(), [_reg("declared:one")], executor=pool, deadline=time.monotonic() + 0.02,
            ) == {"declared:one"}
            assert publisher.future.cancel()
            assert "declared:one" in publisher.poll(
                Client(), [], executor=pool, deadline=time.monotonic() + 0.02,
            )
            gate.set()
            occupied.result(1)
            publisher.future.result(1)
            assert publisher.poll(Client(), [], executor=pool, deadline=time.monotonic() + 1) == set()
        finally:
            gate.set()


def test_progress_writer_rejects_another_live_generation(tmp_path, monkeypatch):
    import json
    import os
    from agent_dispatch import supervise_cli

    path = tmp_path / "status.json"
    original = {"cycle_id": 1, "generation": {"pid": os.getpid() + 1, "start_token": "other"}}
    path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(supervise_cli, "_supervisor_runtime_status_path", lambda scope: path)
    with pytest.raises(RuntimeError, match="generation is superseded"):
        supervise_cli._write_supervisor_progress("test", {"cycle_id": 1, "observed_at": time.time()})
    assert json.loads(path.read_text(encoding="utf-8")) == original


def test_unknown_publication_generation_is_read_back_before_new_target():
    from agent_dispatch.supervisor_daemon_registration_publish import BoundedDeclarationPublisher

    class Client:
        def __init__(self):
            self.rows = {}
            self.writes = []

        def list_registrations(self, **kwargs):
            return list(self.rows.values())

        def register_registration(self, kind, spec, **kwargs):
            self.writes.append(spec["labels"])
            self.rows[kwargs["reg_id"]] = {
                "id": kwargs["reg_id"], "kind": kind, "spec": spec,
                "machine": kwargs["machine"], "env": kwargs["env"],
            }
            if len(self.writes) == 1:
                raise TimeoutError("response lost")

    client = Client()
    publisher = BoundedDeclarationPublisher()
    old = _reg("declared:one")
    old["spec"]["labels"] = ["old"]
    new = _reg("declared:one")
    new["spec"]["labels"] = ["new"]
    with ThreadPoolExecutor(max_workers=1) as pool:
        publisher.poll(client, [old], executor=pool, deadline=time.monotonic() + 1)
        publisher.poll(client, [new], executor=pool, deadline=time.monotonic() + 1)
        assert client.writes == [["old"]]
        publisher.poll(client, [new], executor=pool, deadline=time.monotonic() + 1)
        assert client.writes == [["old"], ["new"]]
