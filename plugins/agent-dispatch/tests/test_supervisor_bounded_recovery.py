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


@pytest.mark.parametrize("observation", ["missing", "non-callable", "empty"])
def test_progress_publication_distinguishes_unsupported_inventory(observation):
    controller = FakeCompanionController()
    supported = observation == "empty"
    if observation != "missing":
        controller.receipt_operation_progress = (lambda: []) if supported else None
    daemon = SupervisorDaemon(
        FakeClient([_reg("unit", machine="host")]), "host",
        launcher=FakeLauncher(), companion_controller=controller,
    )
    frames = []
    daemon._progress_callback = frames.append
    try:
        assert daemon.reconcile_once().started == ["unit"]
        daemon._progress_future.result(2)
        assert frames[-1]["receipt_operations"] == ([] if supported else None)
        assert daemon.progress_snapshot()["receipt_operations"] == ([] if supported else None)
        if not supported:
            assert frames[-1]["observation_error"] == "receipt-progress-unsupported"
    finally:
        daemon.shutdown()


def test_inventory_priming_already_handles_indeterminate_runtime_subclass(monkeypatch, caplog):
    daemon = SupervisorDaemon(
        FakeClient([]), "host", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(),
    )

    def indeterminate(adopted):
        raise CompanionIndeterminate("private detail")

    monkeypatch.setattr(daemon, "_reconcile_companion_receipts", indeterminate)
    try:
        daemon._prime_receipt_inventory(set())
        assert "receipt inventory unavailable: error_type=CompanionIndeterminate" in caplog.text
        assert "private detail" not in caplog.text
    finally:
        daemon.shutdown()


def test_stopped_withdrawal_logs_only_error_type_and_retains_adopted_ids(monkeypatch, caplog):
    daemon = SupervisorDaemon(
        FakeClient([]), "host", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(),
    )
    daemon._managed_uncertain.add("pending")
    observed = []

    def unavailable(adopted):
        observed.append(adopted)
        raise OSError("private/inventory/path")

    monkeypatch.setattr(daemon, "_reconcile_companion_receipts", unavailable)
    try:
        daemon._withdraw_stopped_managed({"pending": object()})
        assert observed == [{"pending"}]
        assert daemon._managed_uncertain == {"pending"}
        assert "error_type=OSError" in caplog.text
        assert "private/inventory/path" not in caplog.text
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("site", ["withdrawal", "poll"])
def test_outer_receipt_observation_errors_do_not_expose_private_paths(monkeypatch, caplog, site):
    daemon = SupervisorDaemon(
        FakeClient([]), "host", launcher=FakeLauncher(),
        companion_controller=FakeCompanionController(),
    )

    def unavailable(*args):
        raise OSError("private/receipt/inventory")

    monkeypatch.setattr(daemon, "_reconcile_companion_receipts", unavailable)
    try:
        if site == "withdrawal":
            daemon._withdraw_stopped_managed({})
        else:
            daemon._resolve_companion_desired({})
        assert "error_type=OSError" in caplog.text
        assert "private/receipt/inventory" not in caplog.text
    finally:
        daemon.shutdown()


def test_inventory_future_failing_between_priming_and_poll_is_classified(tmp_path, monkeypatch, caplog):
    from concurrent.futures import Future
    from agent_dispatch.companion import DefaultCompanionController
    from tests.test_supervisor_daemon import _companion_reg

    controller = DefaultCompanionController(tmp_path / "receipts")
    inventory = Future()
    controller._receipt_inventory_future = inventory
    calls = []

    def provider(*args, **kwargs):
        calls.append("provider")
        assert controller._receipt_inventory_future is inventory
        assert not inventory.done()
        inventory.set_exception(OSError("private/delayed/inventory"))
        return None

    monkeypatch.setattr(controller, "resolver", provider)
    daemon = SupervisorDaemon(
        FakeClient([]), "host", companion_controller=controller, launcher=FakeLauncher(),
    )
    try:
        daemon._reconcile_deadline = time.monotonic() + 1
        daemon._resolve_companion_desired({"unit": _companion_reg("unit")})
        assert calls == ["provider"]
        assert controller._receipt_inventory_future is None
        assert "reconciliation is incomplete: error_type=OSError" in caplog.text
        assert "private/delayed/inventory" not in caplog.text
    finally:
        daemon.shutdown()


@pytest.mark.parametrize("withdraw", [False, True])
def test_slow_publication_readback_survives_deadline_and_settles_unknown(monkeypatch, withdraw):
    from types import SimpleNamespace
    from agent_dispatch import supervisor_daemon_registration_publish as publication

    clock = [0.0]
    monkeypatch.setattr(publication, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    registration = _reg("unit", machine="host")
    target = None if withdraw else registration
    wanted = {} if withdraw else {"unit": registration}

    class Client:
        def __init__(self):
            self.rows = {"unit": registration} if withdraw else {}
            self.reads, self.writes = 0, []

        def list_registrations(self, **kwargs):
            self.reads += 1
            clock[0] += 4
            return list(self.rows.values())

        def register_registration(self, kind, spec, **kwargs):
            self.writes.append(("register", kwargs["reg_id"]))
            self.rows["unit"] = registration
            raise TimeoutError("response lost after publication")

        def remove_registration(self, rid):
            self.writes.append(("remove", rid))
            self.rows.pop(rid)
            raise TimeoutError("response lost after withdrawal")

    publisher, client = publication.BoundedDeclarationPublisher(), Client()
    records = {"unit": {"target": target, "state": "pending"}}
    for cycle in range(4):
        records, error = publisher._advance(client, wanted, records, clock[0] + 3)
        assert error is None
        if cycle == 0:
            assert client.writes == []
            assert records["unit"]["state"] == "pending"
        if cycle == 1:
            assert len(client.writes) == 1
            assert records["unit"]["state"] == "unknown"
    assert client.reads == 2
    assert len(client.writes) == 1
    if withdraw:
        assert records == {}
    else:
        assert records["unit"] == {"target": registration, "state": "confirmed"}


def test_late_publication_observation_invalidated_when_target_changes(monkeypatch):
    from types import SimpleNamespace
    from agent_dispatch import supervisor_daemon_registration_publish as publication

    clock = [0.0]
    monkeypatch.setattr(publication, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    registration = _reg("unit", machine="host")

    class Client:
        reads = 0

        def list_registrations(self, **kwargs):
            self.reads += 1
            clock[0] += 4
            return [registration] if self.reads == 1 else []

    publisher, client = publication.BoundedDeclarationPublisher(), Client()
    records, error = publisher._advance(client, {"unit": registration}, {}, 3)
    assert error is None
    records, error = publisher._advance(client, {}, records, clock[0] + 3)
    assert error is None
    assert client.reads == 2
    assert "unit" in records
    records, error = publisher._advance(client, {}, records, clock[0] + 3)
    assert error is None
    assert records == {}


def test_late_unknown_readback_settles_captured_target_before_changed_declaration(monkeypatch):
    from types import SimpleNamespace
    from agent_dispatch import supervisor_daemon_registration_publish as publication

    clock = [0.0]
    monkeypatch.setattr(publication, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    old = _reg("unit", machine="old-host")
    new = _reg("unit", machine="new-host")

    class Client:
        reads = 0

        def list_registrations(self, **kwargs):
            self.reads += 1
            clock[0] += 4
            return [old]

    publisher, client = publication.BoundedDeclarationPublisher(), Client()
    records = {"unit": {"target": old, "state": "unknown"}}
    records, error = publisher._advance(client, {"unit": old}, records, 3)
    assert error is None
    records, error = publisher._advance(client, {"unit": new}, records, clock[0] + 3)
    assert error is None
    assert client.reads == 1
    assert records["unit"] == {"target": old, "state": "confirmed"}
    records, error = publisher._advance(client, {"unit": new}, records, clock[0] + 3)
    assert error is None
    assert client.reads == 2
    assert records["unit"]["readback"]["target"] == new


def test_publication_poll_harvests_late_readback_without_another_rpc():
    from agent_dispatch.supervisor_daemon_registration_publish import BoundedDeclarationPublisher

    entered, release = threading.Event(), threading.Event()

    class Client:
        reads = 0
        writes = []

        def list_registrations(self, **kwargs):
            self.reads += 1
            entered.set()
            assert release.wait(3)
            return []

        def register_registration(self, kind, spec, **kwargs):
            self.writes.append(kwargs["reg_id"])

    publisher, client = BoundedDeclarationPublisher(), Client()
    registration = _reg("unit", machine="host")
    with ThreadPoolExecutor(max_workers=1) as pool:
        try:
            started = time.monotonic()
            deadline = started + 0.05
            publisher.poll(client, [registration], executor=pool, deadline=deadline)
            assert time.monotonic() - started < 0.5
            assert entered.wait(1)
            assert not publisher.future.done()
            assert client.writes == []
            while time.monotonic() < deadline:
                release.wait(deadline - time.monotonic())
            release.set()
            records, error = publisher.future.result(1)
            assert error is None and "readback" in records["unit"]
            assert client.writes == []
            assert publisher.records["unit"]["state"] == "pending"
            publisher.poll(client, [registration], executor=pool, deadline=time.monotonic() + 1)
            assert client.reads == 1
            assert client.writes == ["unit"]
            assert publisher.records["unit"]["state"] == "confirmed"
        finally:
            release.set()


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
        assert publisher.poll(client, [], executor=pool, deadline=time.monotonic() + 1) == {"declared:one"}
        assert publisher.observe_registry(set(), publisher.withdrawal_epoch - 1) is None
        fresh = client.list_registrations(include_paused=False)
        assert publisher.observe_registry({row["id"] for row in fresh}, publisher.withdrawal_epoch) == {"declared:one"}
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
            assert publisher.poll(Client(), [], executor=pool, deadline=time.monotonic() + 1) == {"declared:one"}
            fresh = Client().list_registrations(include_paused=False)
            assert publisher.observe_registry({row["id"] for row in fresh}, publisher.withdrawal_epoch) == {"declared:one"}
            assert publisher.poll(Client(), [], executor=pool, deadline=time.monotonic() + 1) == set()
        finally:
            gate.set()


@pytest.mark.parametrize("malformed_later_item", [False, True])
def test_publication_worker_does_not_publish_ledger_before_caller_harvest(malformed_later_item):
    import copy
    from agent_dispatch.supervisor_daemon_registration_publish import BoundedDeclarationPublisher

    class Client:
        def __init__(self):
            self.writes = []

        def list_registrations(self, **kwargs):
            return []

        def register_registration(self, kind, spec, **kwargs):
            self.writes.append(kwargs["reg_id"])
            raise TimeoutError("response lost after publication")

    client = Client()
    publisher = BoundedDeclarationPublisher()
    registrations = [_reg("a", machine="host")]
    if malformed_later_item:
        malformed = _reg("z", machine="host")
        del malformed["spec"]
        registrations.append(malformed)
    wanted = {reg["id"]: reg for reg in registrations}
    publisher.records = {rid: {"target": reg, "state": "pending"} for rid, reg in wanted.items()}
    before = copy.deepcopy(publisher.records)
    publisher.inflight_ids = set(wanted)
    with ThreadPoolExecutor(max_workers=1) as pool:
        publisher.future = pool.submit(publisher._advance, client, wanted, publisher.records,
                                       time.monotonic() + 2)
        try:
            publisher.future.result(1)
        except KeyError:
            pass
        assert publisher.records == before
        if malformed_later_item:
            with pytest.raises(KeyError, match="spec"):
                publisher.poll(client, registrations, executor=pool, deadline=time.monotonic())
        else:
            publisher.poll(client, registrations, executor=pool, deadline=time.monotonic())
        assert publisher.future is None
        assert publisher.inflight_ids == set()
        assert publisher.records["a"]["state"] == "unknown"
        assert client.writes == ["a"]


def test_publication_explicit_null_environment_uses_default_for_write_and_readback():
    from agent_dispatch.supervisor_daemon_registration_publish import BoundedDeclarationPublisher

    class Client:
        def __init__(self):
            self.row = None
            self.writes = []

        def list_registrations(self, **kwargs):
            return [] if self.row is None else [self.row]

        def register_registration(self, kind, spec, **kwargs):
            assert kwargs["env"] == "default"
            self.writes.append(kwargs["reg_id"])
            self.row = {"id": kwargs["reg_id"], "kind": kind, "spec": spec,
                        "machine": kwargs["machine"], "env": "default"}
            raise TimeoutError("response lost after publication")

    publisher, client = BoundedDeclarationPublisher(), Client()
    registration = {**_reg("unit", machine="host"), "env": None}
    with ThreadPoolExecutor(max_workers=1) as pool:
        publisher.poll(client, [registration], executor=pool, deadline=time.monotonic() + 1)
        publisher.poll(client, [registration], executor=pool, deadline=time.monotonic() + 1)
    assert client.writes == ["unit"]
    assert publisher.records["unit"]["state"] == "confirmed"


@pytest.mark.parametrize("failure", ["non-object", "invalid-json", "io"])
def test_status_reader_uses_nonidentifying_error_categories(tmp_path, monkeypatch, failure):
    from pathlib import Path
    from agent_dispatch.supervisor_status_io import read_status

    path = tmp_path / "private-status.json"
    path.write_text("[]" if failure == "non-object" else "{", encoding="utf-8")
    if failure == "io":
        def denied(*args, **kwargs):
            raise PermissionError(f"private observation {path}")
        monkeypatch.setattr(Path, "read_text", denied)
    payload, error = read_status(path)
    assert payload is None
    assert error == {
        "non-object": "status-object-required", "invalid-json": "status-json-invalid",
        "io": "status-read-unavailable",
    }[failure]
    assert str(path) not in error


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


@pytest.mark.parametrize("stored_token,current_token", [
    ("old", "current"), ("", "current"), (None, "current"), ("current", None),
])
def test_progress_writer_rejects_reused_or_unconfirmed_native_generation(
    tmp_path, monkeypatch, stored_token, current_token,
):
    import json
    import os
    from agent_dispatch import companion, supervise_cli

    path = tmp_path / "status.json"
    original = {"cycle_id": 1, "generation": {"pid": os.getpid(), "start_token": stored_token}}
    path.write_text(json.dumps(original), encoding="utf-8")
    before = path.read_bytes()
    monkeypatch.setattr(supervise_cli, "_supervisor_runtime_status_path", lambda scope: path)
    monkeypatch.setattr(companion, "process_start_token", lambda pid: current_token)
    with pytest.raises(RuntimeError, match="generation is superseded"):
        supervise_cli._write_supervisor_progress("test", {"cycle_id": 1, "observed_at": time.time()})
    assert path.read_bytes() == before


@pytest.mark.parametrize("progress,expected", [
    (None, None), ({}, None), ({"receipt_operations": None}, None),
    ({"receipt_operations": []}, []), ({"receipt_operations": [{"state": "unknown"}]}, [{"state": "unknown"}]),
])
def test_progress_observer_distinguishes_missing_inventory_from_confirmed_empty(progress, expected):
    result = supervisor_progress_observation({
        "generation": {"pid": 123, "start_token": "exact"},
        "cycle_id": 1, "cycle_started_at": 100.0, "progress": progress,
    }, token_source=lambda pid: "exact", now=101.0)
    assert result["receipt_operations"] == expected
    assert result["recovery_permitted"] is False


def test_progress_writer_accepts_current_native_generation(tmp_path, monkeypatch):
    import json
    import os
    from agent_dispatch import companion, supervise_cli

    path = tmp_path / "status.json"
    generation = {"pid": os.getpid(), "start_token": companion.process_start_token(os.getpid())}
    assert generation["start_token"]
    path.write_text(json.dumps({"cycle_id": 1, "generation": generation}), encoding="utf-8")
    monkeypatch.setattr(supervise_cli, "_supervisor_runtime_status_path", lambda scope: path)
    frame = {"phase": "units", "cycle_id": 1, "observed_at": time.time(), "receipt_operations": []}
    supervise_cli._write_supervisor_progress("test", frame)
    assert json.loads(path.read_text(encoding="utf-8"))["progress"] == {**frame, "generation": generation}
    assert not path.with_suffix(f".{os.getpid()}.tmp").exists()


@pytest.mark.parametrize("failure", [CompanionIndeterminate("pending"), OSError("unavailable")])
def test_reconcile_error_clears_cycle_authority_before_next_attempt(monkeypatch, failure):
    daemon = SupervisorDaemon(
        FakeClient([_reg("unit", machine="host")]), "host",
        launcher=FakeLauncher(), companion_controller=FakeCompanionController(),
    )
    original = daemon._desired

    def fail():
        raise failure

    monkeypatch.setattr(daemon, "_desired", fail)
    try:
        with pytest.raises(type(failure)):
            daemon.reconcile_once()
        assert daemon._reconcile_deadline is None
        assert daemon._reconcile_token is None
        assert daemon._phase_name == "discovery"
        monkeypatch.setattr(daemon, "_desired", original)
        assert daemon.reconcile_once().started == ["unit"]
        assert daemon._reconcile_deadline is None
        assert daemon._reconcile_token is None
    finally:
        daemon.shutdown()


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


def test_publication_readback_retains_unknown_target_across_scope_changes():
    from agent_dispatch.supervisor_daemon_registration_publish import BoundedDeclarationPublisher

    class Client:
        def __init__(self):
            self.rows = {}
            self.writes = []

        def list_registrations(self, **kwargs):
            assert kwargs == {"include_paused": True}
            return list(self.rows.values())

        def register_registration(self, kind, spec, **kwargs):
            self.writes.append((kwargs["machine"], kwargs["env"]))
            self.rows[kwargs["reg_id"]] = {
                "id": kwargs["reg_id"], "kind": kind, "spec": spec,
                "machine": kwargs["machine"], "env": kwargs["env"],
            }
            if len(self.writes) == 1:
                raise TimeoutError("response lost")

    client = Client()
    publisher = BoundedDeclarationPublisher()
    old = _reg("declared:one", machine="old-host", env="old-env")
    new = _reg("declared:one", machine="new-host", env="new-env")
    with ThreadPoolExecutor(max_workers=1) as pool:
        publisher.poll(client, [old], executor=pool, deadline=time.monotonic() + 1)
        publisher.poll(client, [new], executor=pool, deadline=time.monotonic() + 1)
        assert client.writes == [("old-host", "old-env")]
        publisher.poll(client, [new], executor=pool, deadline=time.monotonic() + 1)
        assert client.writes == [("old-host", "old-env"), ("new-host", "new-env")]
