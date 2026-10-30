"""Original-process record identity without making the adapter the daemon."""

import os
from pathlib import Path

import pytest
from unittest.mock import Mock

from agent_procutil import _daemon_instance_store as store
from agent_procutil._daemon_instance_records import (
    ProcessIdentity, admit_for_process, bind_for_process, finish_for_process, reserve_for_process,
)
from agent_procutil.daemon_instances import DaemonInstanceSpec, DaemonLeasePolicy, reserve_instance


@pytest.fixture
def spec(tmp_path, monkeypatch):
    monkeypatch.delenv("COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT", raising=False)
    return DaemonInstanceSpec(
        "shell-fixture", tmp_path / "runtime", Path(__file__).resolve(),
        registry_root=tmp_path / "registry",
    )


def test_original_shell_not_helper_owns_the_record(spec):
    shell = ProcessIdentity(4200, "windows:123")
    receipt = reserve_for_process(spec, shell)
    record = store.inspect(receipt.registry_root)[0]
    assert shell.pid != os.getpid()
    assert record["owner"] == "process:4200"
    assert shell.matches(record, "owner_")
    assert shell.matches(record, "launcher_")
    bind_for_process(receipt, shell, shell)
    record = admit_for_process(receipt, shell)
    assert shell.matches(record)
    assert record["state"] == "running"
    finish_for_process(receipt, shell)
    assert store.inspect(receipt.registry_root)[0]["state"] == "finished"


def test_public_reservation_cannot_override_its_actual_caller(spec):
    receipt = reserve_instance(spec)
    row = store.inspect(receipt.registry_root)[0]
    assert ProcessIdentity.current().matches(row, "launcher_")
    with pytest.raises(TypeError):
        reserve_instance(spec, launcher=ProcessIdentity(4200, "windows:123"))


@pytest.mark.parametrize("state", ["reserved", "bound", "running", "finished"])
def test_binding_always_requires_original_launcher_even_when_idempotent(spec, state):
    shell = ProcessIdentity(4200, "windows:123")
    child = ProcessIdentity(4300, "windows:124")
    foreign = ProcessIdentity(4200, "windows:125")
    receipt = reserve_for_process(spec, shell)
    if state != "reserved":
        bind_for_process(receipt, child, shell)
    if state in ("running", "finished"):
        admit_for_process(receipt, child)
    if state == "finished":
        finish_for_process(receipt, child)
    before = receipt.record_path.read_bytes()
    with pytest.raises(ValueError, match="different process"):
        bind_for_process(receipt, child, foreign)
    assert receipt.record_path.read_bytes() == before


@pytest.mark.parametrize("operation", ["bind", "admit", "finish"])
def test_reused_pid_never_mutates_the_original_instance(spec, operation):
    shell = ProcessIdentity(4200, "windows:123")
    reused = ProcessIdentity(shell.pid, "windows:124")
    receipt = reserve_for_process(spec, shell)
    bind_for_process(receipt, shell, shell)
    if operation == "finish":
        admit_for_process(receipt, shell)
    before = receipt.record_path.read_bytes()
    with pytest.raises(ValueError, match="process"):
        if operation == "bind":
            bind_for_process(receipt, reused, shell)
        elif operation == "admit":
            admit_for_process(receipt, reused)
        else:
            finish_for_process(receipt, reused)
    assert receipt.record_path.read_bytes() == before


def test_original_owner_remains_distinct_from_authenticated_launcher(spec, monkeypatch):
    context = {
        "purpose": "unattended-test", "owner": "test-runner", "pid": "4400",
        "birth": "windows:126", "registry_root": str(spec.registry_root),
        "ttl": "10", "max_lifetime": "30", "run_id": "fixture",
    }
    monkeypatch.setattr(store, "_test_context", lambda environment: context)
    shell = ProcessIdentity(4200, "windows:123")
    receipt = reserve_for_process(spec, shell, environment={})
    row = store.inspect(receipt.registry_root)[0]
    assert shell.matches(row, "launcher_")
    assert ProcessIdentity(4400, "windows:126").matches(row, "owner_")
    assert row["owner"] == "test-runner"
    assert row["purpose"] == "unattended-test"


def test_shared_admission_cannot_revive_expired_disposable_reservation(spec, monkeypatch):
    from dataclasses import replace

    shell = ProcessIdentity(4200, "windows:123")
    receipt = reserve_for_process(
        replace(spec, purpose="unattended-test", lease=DaemonLeasePolicy(1, 3)), shell,
    )
    before = receipt.record_path.read_bytes()
    wall, mono = store._wall_clock(), store._monotonic_clock()
    monkeypatch.setattr(store, "_wall_clock", lambda: wall + 10)
    monkeypatch.setattr(store, "_monotonic_clock", lambda: mono + 10)
    with pytest.raises(ValueError, match="admission refused"):
        admit_for_process(receipt, shell)
    assert receipt.record_path.read_bytes() == before


def test_shared_production_admission_has_no_expiry(spec, monkeypatch):
    shell = ProcessIdentity(4200, "windows:123")
    receipt = reserve_for_process(spec, shell)
    monkeypatch.setattr(store, "_wall_clock", lambda: 10**20)
    row = admit_for_process(receipt, shell)
    assert row["deadline"] == row["hard_deadline"] == "0"
    assert shell.matches(row)


@pytest.mark.parametrize("fault", ["foreign-process", "unknown-purpose"])
def test_admission_refuses_before_lifetime_setup_or_record_publication(spec, fault):
    identity = ProcessIdentity(4200, "windows:123")
    receipt = reserve_for_process(spec, identity)
    record = store._read(receipt.record_path)
    if fault == "foreign-process":
        record.update(state="bound", pid="4300", birth="windows:124")
    else:
        record["purpose"] = "unknown"
    store._write(receipt.record_path, record)
    before = receipt.record_path.read_bytes()
    setup = Mock(side_effect=AssertionError("unadmitted lifetime effect"))
    with pytest.raises(ValueError):
        admit_for_process(receipt, identity, before_admit=setup)
    setup.assert_not_called()
    assert receipt.record_path.read_bytes() == before


def test_expiry_during_lifetime_setup_cannot_publish_running(spec, monkeypatch):
    identity = ProcessIdentity(4200, "windows:123")
    receipt = reserve_for_process(spec, identity)
    before = receipt.record_path.read_bytes()
    monkeypatch.setattr(store, "_expire", Mock(side_effect=[False, True]))
    setup = Mock()
    with pytest.raises(ValueError, match="expired during lifetime admission"):
        admit_for_process(receipt, identity, before_admit=setup)
    setup.assert_called_once()
    assert receipt.record_path.read_bytes() == before


@pytest.mark.parametrize(
    "pid,birth", [(True, "windows:1"), (0, "windows:1"), (2**32, "windows:1"), (1, ""),
                  (1, "windows:1\n"), (1, 123)],
)
def test_identity_rejects_malformed_protocol_values(pid, birth):
    with pytest.raises(ValueError):
        ProcessIdentity(pid, birth)
