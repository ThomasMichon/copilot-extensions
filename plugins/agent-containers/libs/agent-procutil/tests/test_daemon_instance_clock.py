"""Producer and consumers never translate a published Linux lease from wall time."""

from __future__ import annotations

import os
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agent_procutil import _daemon_instance_clock as clock
from agent_procutil import _daemon_instance_linux as linux
from agent_procutil import _daemon_instance_store as store
from agent_procutil.daemon_instances import DaemonInstanceSpec, DaemonLeasePolicy

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux lease clock")


@pytest.fixture
def reserved(tmp_path, monkeypatch):
    current = SimpleNamespace(wall=10000.0, mono=100.0)
    monkeypatch.setattr(store, "_wall_clock", lambda: current.wall)
    monkeypatch.setattr(store, "_monotonic_clock", lambda: current.mono)
    monkeypatch.setattr(store, "_ensure_monitor", lambda: None)
    monkeypatch.delenv("COPILOT_EXTENSIONS_DAEMON_TEST_CONTEXT", raising=False)
    receipt = store.reserve(DaemonInstanceSpec(
        "clock-fixture", tmp_path, Path(__file__).resolve(), purpose="unattended-test",
        registry_root=tmp_path / "registry", lease=DaemonLeasePolicy(1, 10),
    ))
    return receipt, current


@pytest.mark.parametrize("before,after", [(3600, -7200), (-7200, 3600)])
def test_renewal_and_expiry_use_publication_clock_not_observation_wall_time(reserved, before, after):
    receipt, current = reserved
    current.mono = 100.9
    current.wall += before
    store.renew(receipt)
    record = store._read(receipt.record_path)
    assert float(record["deadline_mono"]) == pytest.approx(101.9)
    assert float(record["hard_deadline_mono"]) == 110
    current.wall += after
    current.mono = 101.2
    assert not store._expire(record, current.wall)
    current.mono = 102
    assert store._expire(record, current.wall)
    with pytest.raises(ValueError, match="cannot be revived"):
        store.renew(receipt)


@pytest.mark.parametrize("step", [-86400, 86400])
def test_supervisor_reads_real_committed_renewal_after_wall_step(reserved, monkeypatch, step):
    receipt, current = reserved
    with store._locked(receipt) as path:
        original = store._read(path)
        original.update(pid="123", birth="child", supervisor_pid="456", supervisor_birth="supervisor")
        store._write(path, original)
    current.mono = 100.9
    store.renew(receipt)
    current.wall += step
    current.mono = 101.2
    exited = SimpleNamespace(si_status=37, si_code=os.CLD_EXITED)
    wait = Mock(side_effect=[None, exited])
    monkeypatch.setattr(linux, "os", SimpleNamespace(
        waitid=wait, P_PID=os.P_PID, WEXITED=os.WEXITED, WNOHANG=os.WNOHANG,
        WNOWAIT=os.WNOWAIT, CLD_EXITED=os.CLD_EXITED,
    ))
    monkeypatch.setattr(linux, "time", SimpleNamespace(
        monotonic=lambda: current.mono, sleep=Mock(),
        time=lambda: pytest.fail("supervisor borrowed wall time"),
    ))
    monkeypatch.setattr(linux, "_exited", lambda fd: False)
    lease = linux._Lease(110, 101)
    assert linux._watch_child(receipt, original, 123, 99, lease, lambda: False) == 37
    assert lease.lease_mono == pytest.approx(101.9)
    current.mono = 102
    wait.side_effect = [None]
    assert linux._watch_child(receipt, original, 123, 99, lease, lambda: False) == 124


@pytest.mark.parametrize("domain", ["different-boot", "different-time-namespace"])
def test_foreign_clock_domain_never_admits_renews_or_signals(reserved, monkeypatch, domain):
    receipt, current = reserved
    with store._locked(receipt) as path:
        record = store._read(path)
        record["clock_domain"] = domain
        store._write(path, record)
    with pytest.raises(ValueError, match="clock domain"):
        linux._validate(record)
    with pytest.raises(ValueError, match="clock domain"):
        store.renew(receipt)
    store.bind_process(receipt, os.getpid())
    monkeypatch.setattr(store, "stop_identity", Mock(side_effect=AssertionError("foreign clock signaled")))
    assert store.cleanup(receipt).status == "held"


def test_legacy_linux_lease_is_explicitly_held_not_converted(reserved, monkeypatch):
    receipt, _ = reserved
    with store._locked(receipt) as path:
        record = store._read(path)
        record["schema"] = "1"
        for field in ("clock_domain", "deadline_mono", "hard_deadline_mono"):
            record.pop(field)
        store._write(path, record)
    with pytest.raises(ValueError, match="monotonic record schema 2"):
        linux._validate(record)
    with pytest.raises(ValueError, match="monotonic record schema 2"):
        store.renew(receipt)
    store.bind_process(receipt, os.getpid())
    monkeypatch.setattr(store, "stop_identity", Mock(side_effect=AssertionError("legacy clock signaled")))
    assert store.cleanup(receipt).status == "held"


@pytest.mark.parametrize("field,value", [
    ("deadline_mono", "nan"), ("deadline_mono", "inf"), ("deadline_mono", "-1"),
    ("deadline_mono", "111"), ("hard_deadline_mono", "inf"),
])
def test_invalid_monotonic_bounds_refuse(reserved, field, value):
    receipt, _ = reserved
    record = store._read(receipt.record_path)
    record[field] = value
    with pytest.raises(ValueError, match="monotonic expiry"):
        clock.bounds(record)


@pytest.mark.parametrize("step", [-86400, 86400])
def test_owner_renewal_prefilter_and_self_monitor_ignore_wall_steps(reserved, monkeypatch, step):
    receipt, current = reserved
    with store._locked(receipt) as path:
        record = store._read(path)
        record.update(run_id="fixture-run", state="running", pid=str(os.getpid()),
                      birth=store.birth_identity(os.getpid()))
        store._write(path, record)
    current.mono = 100.9
    current.wall += step
    monkeypatch.setattr(store, "_owners", {"fixture-run": {
        "run_id": "fixture-run", "registry_root": str(receipt.registry_root),
        "ttl": "1", "next_renew": 0,
    }})
    monkeypatch.setattr(store, "_guards", {receipt.instance_id: (
        receipt, SimpleNamespace(is_set=lambda: False),
    )})
    monkeypatch.setattr(store, "retire_self", Mock(side_effect=AssertionError("valid lease retired")))
    monkeypatch.setattr(store, "_pause", SimpleNamespace(wait=Mock(side_effect=StopIteration)))
    with pytest.raises(StopIteration):
        store._observe_expiry()
    assert float(store._read(receipt.record_path)["deadline_mono"]) == pytest.approx(101.9)


def test_teardown_shortens_the_same_monotonic_deadline(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "_ensure_monitor", lambda: None)
    outcomes = []
    monkeypatch.setattr(store, "cleanup", lambda receipt: outcomes.append(
        store._read(receipt.record_path),
    ) or SimpleNamespace(status="live"))
    with store.test_lifecycle("clock-fixture", tmp_path / "registry", "unattended-test", None) as env:
        receipt = store.reserve(DaemonInstanceSpec(
            "clock-fixture", tmp_path, Path(__file__).resolve(),
        ), environment=env)
        before = store._monotonic_clock()
    assert outcomes[0]["instance_id"] == receipt.instance_id
    assert before <= float(outcomes[0]["deadline_mono"]) <= store._monotonic_clock()


def test_unavailable_time_namespace_refuses_without_epoch_fallback(monkeypatch):
    def unavailable():
        raise OSError("time namespace unavailable")

    monkeypatch.setattr(clock, "domain", unavailable)
    with pytest.raises(OSError, match="time namespace unavailable"):
        clock.fields(1, 10, 100)


def test_clock_domain_contains_live_boot_and_time_namespace():
    namespace = Path("/proc/self/ns/time").stat()
    boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    assert clock.domain() == f"linux-monotonic:{boot}:{namespace.st_dev}:{namespace.st_ino}"
