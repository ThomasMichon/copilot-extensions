"""Receipt scheduling preserves unresolved generations without starving siblings."""

import os
import json
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_dispatch import companion
from agent_dispatch.companion import CompanionIndeterminate, DefaultCompanionController
from tests.test_bounded_receipt_reconcile import _receipt


def test_unknown_receipts_do_not_starve_later_withdrawals(tmp_path, monkeypatch):
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    for rid in ("a", "b", "c"):
        _receipt(controller, rid)
    calls = Counter()

    def retire(receipt, **kwargs):
        rid = receipt["registration_id"]
        calls[rid] += 1
        if rid in {"a", "b"}:
            raise CompanionIndeterminate("injected unresolved retirement")

    monkeypatch.setattr(controller, "_retire_receipt", retire)
    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(6):
            controller.reconcile_receipts_bounded(
                set(), executor=pool, deadline=time.monotonic() + 2,
            )
            assert len(controller._receipt_operations) <= 2
            for rid, future in tuple(controller._receipt_operations.items()):
                try:
                    future.result(1)
                except CompanionIndeterminate:
                    assert rid in {"a", "b"}
    completed = controller._operation_record("c")
    assert completed is not None and completed["state"] == "complete"
    assert not controller._receipt_path("c").exists()
    for rid in ("a", "b"):
        assert controller._operation_record(rid)["state"] == "unknown"
        assert controller._receipt_path(rid).exists()
        with pytest.raises(CompanionIndeterminate, match="not confirmed"):
            controller._assert_receipt_available(rid)
    assert calls == {"a": 1, "b": 1, "c": 1}


def test_unmanaged_retirement_requires_exit_confirmation(tmp_path, monkeypatch):
    clock = [time.monotonic()]
    signals = []

    def token(pid):
        return "owner" if pid == os.getpid() else "child-generation"

    controller = DefaultCompanionController(
        tmp_path / "receipts", token_source=token,
        monotonic=lambda: clock[0], sleeper=lambda seconds: clock.__setitem__(0, clock[0] + seconds),
    )
    _receipt(controller)
    before = controller._receipt_path("withdrawn").read_bytes()
    monkeypatch.setattr(companion, "_terminate_windows_tree", lambda pid: signals.append(pid))
    monkeypatch.setattr(companion, "_terminate_posix_group", lambda pid: signals.append(pid))
    with ThreadPoolExecutor(max_workers=2) as pool:
        controller.reconcile_receipts_bounded(set(), executor=pool, deadline=clock[0] + 20)
        with pytest.raises(CompanionIndeterminate, match="retirement is not confirmed"):
            controller._receipt_operations["withdrawn"].result(1)
    assert signals == [100]
    assert controller._receipt_path("withdrawn").read_bytes() == before
    assert controller._operation_record("withdrawn")["state"] == "unknown"
    with pytest.raises(CompanionIndeterminate, match="not confirmed"):
        controller._assert_receipt_available("withdrawn")


@pytest.mark.parametrize("version", [None, 2])
def test_bounded_retirement_retains_unsupported_receipt(tmp_path, monkeypatch, version):
    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    receipt = _receipt(controller)
    if version is None:
        receipt.pop("schema_version")
    else:
        receipt["schema_version"] = version
    path = controller._receipt_path("withdrawn")
    path.write_text(json.dumps(receipt), encoding="utf-8")
    before, retired = path.read_bytes(), []
    monkeypatch.setattr(controller, "_retire_receipt", lambda *args, **kwargs: retired.append(args))
    with ThreadPoolExecutor(max_workers=2) as pool:
        controller.reconcile_receipts_bounded(set(), executor=pool, deadline=time.monotonic() + 2)
        for future in controller.receipt_futures():
            try:
                future.result(1)
            except CompanionIndeterminate:
                pass
    assert retired == []
    assert path.read_bytes() == before
    with pytest.raises(CompanionIndeterminate, match="version"):
        controller._assert_receipt_available("withdrawn")


@pytest.mark.parametrize("corruption", [
    "receipt-json", "operation-json", "operation-digest", "unreadable",
    "operation-receipt-list", "operation-selection-list",
])
def test_corrupt_metadata_fences_only_its_identity(tmp_path, monkeypatch, caplog, corruption):
    from agent_dispatch import companion_reconcile
    from agent_dispatch.managed_runtime import ManagedRuntimeError

    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    _receipt(controller, "a")
    _receipt(controller, "z")
    path = controller._receipt_path("a")
    if corruption.startswith("operation"):
        path = controller._operation_path("a")
    if corruption.endswith("-list"):
        from agent_dispatch.managed_runtime import _canonical_digest
        from tests.test_bounded_receipt_reconcile import _unknown_operation

        _unknown_operation(controller, "a")
        record = controller._operation_record("a")
        record["generation"][corruption.split("-")[1]] = []
        record["generation_digest"] = _canonical_digest(record["generation"])
        path.write_text(json.dumps(record), encoding="utf-8")
    elif corruption == "operation-digest":
        path.write_text(json.dumps({
            "registration_id": "a", "schema_version": 1, "state": "unknown",
            "generation": {"receipt": None, "selection": None}, "generation_digest": "invalid",
        }), encoding="utf-8")
    else:
        path.write_text("{", encoding="utf-8")
    before, retired = path.read_bytes(), []
    read = companion_reconcile._read_metadata

    def metadata(root, candidate):
        if corruption == "unreadable" and candidate == path.absolute():
            raise ManagedRuntimeError("injected unreadable metadata")
        return read(root, candidate)

    monkeypatch.setattr(companion_reconcile, "_read_metadata", metadata)
    monkeypatch.setattr(
        controller, "_retire_receipt", lambda receipt, **kwargs: retired.append(receipt["registration_id"]),
    )
    if corruption.endswith("-list"):
        with pytest.raises(CompanionIndeterminate, match="identity is invalid"):
            controller._operation_record("a")
    with ThreadPoolExecutor(max_workers=2) as pool:
        for _ in range(3):
            controller.reconcile_receipts_bounded(set(), executor=pool, deadline=time.monotonic() + 2)
            for rid, future in tuple(controller._receipt_operations.items()):
                try:
                    future.result(1)
                except CompanionIndeterminate:
                    assert rid == "a"
    assert retired == ["z"]
    assert controller._operation_record("z")["state"] == "complete"
    assert path.read_bytes() == before
    with pytest.raises(CompanionIndeterminate):
        controller._assert_receipt_available("a")
    if corruption.startswith("operation"):
        with pytest.raises(CompanionIndeterminate, match="progress is unavailable") as error:
            controller.receipt_operation_progress()
        assert isinstance(error.value.__cause__, CompanionIndeterminate)
    else:
        assert [record["registration_id"] for record in controller.receipt_operation_progress()] == ["z"]
    assert "a" in caplog.text


def test_corrupt_operation_metadata_is_indeterminate_at_provider_boundary(tmp_path):
    controller = DefaultCompanionController(tmp_path / "receipts")
    controller.receipt_dir.mkdir()
    controller._operation_path("damaged").write_text("{", encoding="utf-8")
    with pytest.raises(CompanionIndeterminate, match="metadata"):
        controller.resolve({"id": "damaged"}, machine="host", env="native")


def test_operation_replacement_waits_for_local_metadata_reader(tmp_path, monkeypatch):
    from agent_dispatch import companion_reconcile
    from tests.test_bounded_receipt_reconcile import _unknown_operation

    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    _unknown_operation(controller, "unit")
    record = controller._operation_record("unit")
    path = controller._operation_path("unit")
    before = path.read_bytes()
    entered, release, writing = threading.Event(), threading.Event(), threading.Event()
    read = companion_reconcile._read_metadata

    def held_read(root, candidate):
        if candidate == path.absolute():
            with candidate.open("rb"):
                entered.set()
                assert release.wait(3)
        return read(root, candidate)

    def write():
        writing.set()
        controller._write_operation("unit", {**record, "state": "complete"})

    monkeypatch.setattr(companion_reconcile, "_read_metadata", held_read)
    with ThreadPoolExecutor(max_workers=2) as pool:
        reader = pool.submit(controller._operation_record, "unit")
        try:
            assert entered.wait(1)
            writer = pool.submit(write)
            assert writing.wait(1)
            with pytest.raises(CompanionIndeterminate, match="remains in use"):
                writer.result(1)
        finally:
            release.set()
            reader.result(1)
    assert path.read_bytes() == before
    assert controller._receipt_metadata_locks == {}
    controller._write_operation("unit", {**record, "state": "complete"})
    assert controller._operation_record("unit")["state"] == "complete"


@pytest.mark.parametrize("state", [[], {}])
def test_unhashable_operation_state_remains_indeterminate(tmp_path, state):
    from tests.test_bounded_receipt_reconcile import _unknown_operation

    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    _unknown_operation(controller, "damaged")
    record = controller._operation_record("damaged")
    controller._write_operation("damaged", {**record, "state": state})
    with pytest.raises(CompanionIndeterminate, match="identity"):
        controller._assert_receipt_available("damaged")


def test_uncertain_operation_write_invalidates_completed_availability(tmp_path, monkeypatch):
    from agent_dispatch import companion_reconcile
    from tests.test_bounded_receipt_reconcile import _unknown_operation
    from tests.test_managed_companion import Executor

    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    _unknown_operation(controller, "unit")
    record = {**controller._operation_record("unit"), "state": "complete"}
    controller._write_operation("unit", record)
    executor = Executor()
    controller.request_receipt_inventory(
        {"unit"}, inventory_executor=executor, executor=executor, availability_executor=executor,
        deadline=time.monotonic() + 1, operation_budget=1, cycle_tag="first",
    )
    controller._assert_receipt_available("unit")
    write = companion_reconcile.write_json_object_atomic

    def response_lost(path, value):
        write(path, value)
        if path == controller._operation_path("unit"):
            raise OSError("injected response loss after committed replacement")

    monkeypatch.setattr(companion_reconcile, "write_json_object_atomic", response_lost)
    with pytest.raises(OSError, match="response loss"):
        controller._write_operation("unit", {**record, "state": "unknown"})
    with pytest.raises(CompanionIndeterminate, match="not confirmed"):
        controller._assert_receipt_available("unit")
    assert controller._operation_record("unit")["state"] == "unknown"


def test_consumed_availability_expires_next_cycle_and_prunes_withdrawn_ids(tmp_path):
    from tests.test_bounded_receipt_reconcile import _unknown_operation
    from tests.test_managed_companion import Executor

    controller = DefaultCompanionController(tmp_path / "receipts", token_source=lambda pid: "owner")
    _unknown_operation(controller, "unit")
    controller._write_operation("unit", {**controller._operation_record("unit"), "state": "complete"})
    executor = Executor()
    observed = []
    for cycle in range(3):
        controller.request_receipt_inventory(
            {"unit"}, inventory_executor=executor, executor=executor, availability_executor=executor,
            deadline=time.monotonic() + 1, operation_budget=1, cycle_tag=cycle,
        )
        assert controller._receipt_availability == {}
        controller._assert_receipt_available("unit")
        observed.append(controller._receipt_availability["unit"].future)
        controller._assert_receipt_available("unit")
        assert controller._receipt_availability["unit"].future is observed[-1]
    assert len(set(observed)) == 3
    controller.request_receipt_inventory(
        set(), inventory_executor=executor, executor=executor, availability_executor=executor,
        deadline=time.monotonic() + 1, operation_budget=1, cycle_tag="withdrawn",
    )
    assert controller._receipt_availability == {}
    assert controller._receipt_metadata_locks == {}


def test_cancelled_availability_is_indeterminate_and_reclaimable(tmp_path):
    from tests.test_managed_companion import Executor

    controller = DefaultCompanionController(tmp_path / "receipts")
    immediate = Executor()
    release = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        occupied = pool.submit(lambda: release.wait(3))
        try:
            controller.request_receipt_inventory(
                {"unit"}, inventory_executor=immediate, executor=immediate,
                availability_executor=pool, deadline=time.monotonic() + 0.05,
                operation_budget=0.05, cycle_tag="before-shutdown",
            )
            with pytest.raises(CompanionIndeterminate, match="pending"):
                controller._assert_receipt_available("unit")
            assert controller._receipt_availability["unit"].future.cancel()
            with pytest.raises(CompanionIndeterminate, match="cancelled"):
                controller._assert_receipt_available("unit")
            assert controller._receipt_availability == {}
        finally:
            release.set()
            occupied.result(1)
    controller.request_receipt_inventory(
        {"unit"}, inventory_executor=immediate, executor=immediate,
        availability_executor=immediate, deadline=time.monotonic() + 1,
        operation_budget=1, cycle_tag="reclaimed",
    )
    controller._assert_receipt_available("unit")
