"""Original-owner launch settlement never weakens unknown-instance reconciliation."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from agent_procutil import _daemon_instance_store as store
from agent_procutil import _daemon_instance_records as records
from agent_procutil._daemon_instance_records import (
    ProcessIdentity, cancel_for_process, finish_failed_for_process, reserve_for_process,
)
from agent_procutil.daemon_instances import DaemonInstanceReceipt, DaemonInstanceSpec


@pytest.fixture
def reserved(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[DaemonInstanceReceipt, ProcessIdentity]]:
    monkeypatch.setattr(store, "_test_context", lambda environment=None: None)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    launcher = ProcessIdentity.current()
    receipt = reserve_for_process(
        DaemonInstanceSpec(
            "fixture-launch", runtime, Path(__file__).resolve(),
            purpose="production", registry_root=tmp_path / "instances",
        ),
        launcher, environment={},
    )
    yield receipt, launcher
    if receipt.record_path.exists():
        record = store._read(receipt.record_path)
        if not record.get("pid"):
            cancel_for_process(receipt, launcher, creation_refused=True)
        else:
            monkeypatch.setattr(store, "identity_state", lambda pid, birth: "dead")
            result = store.cleanup(receipt, terminate=False)
            assert result.status == "settled", result


def test_only_the_original_producer_can_cancel_a_refused_creation(
    reserved: tuple[DaemonInstanceReceipt, ProcessIdentity],
) -> None:
    receipt, launcher = reserved
    before = receipt.record_path.read_bytes()
    held = store.cleanup(receipt, terminate=False)
    assert held.status == "held" and held.reason == "launch-outcome-unknown"
    with pytest.raises(ValueError, match="different|cannot be cancelled"):
        cancel_for_process(
            receipt, ProcessIdentity(launcher.pid + 1, "foreign"), creation_refused=True,
        )
    with pytest.raises(ValueError, match="refused creation"):
        cancel_for_process(receipt, launcher, creation_refused=False)
    assert receipt.record_path.read_bytes() == before
    cancel_for_process(receipt, launcher, creation_refused=True)
    assert not receipt.record_path.exists()


def test_identified_exited_child_settles_without_touching_application_files(
    reserved: tuple[DaemonInstanceReceipt, ProcessIdentity], monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt, launcher = reserved
    marker = receipt.registry_root.parent / "application-state"
    marker.write_text("retained", encoding="utf-8")
    monkeypatch.setattr(records, "identity_state", lambda pid, birth: "dead")
    assert finish_failed_for_process(
        receipt, ProcessIdentity(123, "fixture-child"), launcher, admission_possible=False,
    )
    assert not receipt.record_path.exists()
    assert marker.read_text(encoding="utf-8") == "retained"


@pytest.mark.parametrize("field", ["artifact_count", "protected"])
def test_refused_creation_cannot_discard_retained_effects(
    reserved: tuple[DaemonInstanceReceipt, ProcessIdentity], field: str,
) -> None:
    receipt, launcher = reserved
    original = store._read(receipt.record_path)
    record = {**original, field: "1"}
    store._write(receipt.record_path, record)
    before = receipt.record_path.read_bytes()
    try:
        with pytest.raises(ValueError, match="cannot be cancelled"):
            cancel_for_process(receipt, launcher, creation_refused=True)
        assert receipt.record_path.read_bytes() == before
    finally:
        store._write(receipt.record_path, original)


@pytest.mark.parametrize("observation", ["matching", "unknown"])
def test_unconfirmed_child_remains_tracked(
    reserved: tuple[DaemonInstanceReceipt, ProcessIdentity],
    monkeypatch: pytest.MonkeyPatch, observation: str,
) -> None:
    receipt, launcher = reserved
    child = ProcessIdentity(123, "fixture-child")
    monkeypatch.setattr(records, "identity_state", lambda pid, birth: observation)
    assert not finish_failed_for_process(receipt, child, launcher, admission_possible=False)
    assert store._read(receipt.record_path)["spawn_failure"] == "process-" + observation
    monkeypatch.setattr(records, "identity_state", lambda pid, birth: "dead")
    assert finish_failed_for_process(receipt, child, launcher, admission_possible=False)


@pytest.mark.parametrize("later_observation", ["dead", "matching", "unknown"])
@pytest.mark.parametrize("initial_observation", ["dead", "unknown"])
def test_possible_application_admission_cannot_be_downgraded_by_a_retry(
    reserved: tuple[DaemonInstanceReceipt, ProcessIdentity], monkeypatch: pytest.MonkeyPatch,
    later_observation: str, initial_observation: str,
) -> None:
    receipt, launcher = reserved
    child = ProcessIdentity(123, "fixture-child")
    monkeypatch.setattr(records, "identity_state", lambda pid, birth: initial_observation)
    assert not finish_failed_for_process(receipt, child, launcher, admission_possible=True)
    assert store._read(receipt.record_path)["spawn_admission_possible"] == "1"
    if later_observation != "dead":
        monkeypatch.setattr(records, "identity_state", lambda pid, birth: later_observation)
        assert not finish_failed_for_process(receipt, child, launcher, admission_possible=False)
    monkeypatch.setattr(records, "identity_state", lambda pid, birth: "dead")
    assert not finish_failed_for_process(receipt, child, launcher, admission_possible=False)
    assert store._read(receipt.record_path)["spawn_failure"] == "exit-confirmed-effects-held"
