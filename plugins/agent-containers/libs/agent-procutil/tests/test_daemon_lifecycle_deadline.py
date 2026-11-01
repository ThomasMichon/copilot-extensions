"""Deadline and retained-outcome controls without starting daemon instances."""

from __future__ import annotations

import base64
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_procutil import _daemon_instance_store as store
from agent_procutil.daemon_instances import DaemonInstanceReceipt, InstanceCleanupResult, TEST_CONTEXT_ENV


def test_expired_lifecycle_lock_never_creates_or_deletes_inventory_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(store, "_monotonic_clock", lambda: 30)
    path = tmp_path / "owned.lock"
    with pytest.raises(TimeoutError, match="original"):
        with store._file_lock(path, deadline=30):
            pytest.fail("an expired original bound must not acquire inventory custody")
    assert not path.exists()


def test_local_lock_acquisition_consumes_only_remaining_original_time(monkeypatch) -> None:
    now = [20.0]
    calls = []

    class Guard:
        def acquire(self, **kwargs):
            calls.append(kwargs)
            now[0] = 30
            return True

        def release(self):
            calls.append("released")

    monkeypatch.setattr(store, "_monotonic_clock", lambda: now[0])
    with pytest.raises(TimeoutError):
        with store._local_custody(Guard(), deadline=30):
            pytest.fail("lock acquisition cannot replenish an expired deadline")
    assert calls == [{"timeout": 10.0}, "released"]


def test_cleanup_overrun_retains_the_record_and_reports_held(tmp_path: Path, monkeypatch) -> None:
    now = [20.0]
    receipt = DaemonInstanceReceipt("a" * 32, tmp_path)
    path = receipt.record_path
    path.write_bytes(b"retained fixture record")

    @contextmanager
    def locked(_receipt, **kwargs):
        assert kwargs == {"deadline": 30}
        yield path

    def observed(_pid, _birth):
        now[0] = 30
        return "dead"

    monkeypatch.setattr(store, "_monotonic_clock", lambda: now[0])
    monkeypatch.setattr(store, "_locked", locked)
    monkeypatch.setattr(store, "identity_state", observed)
    monkeypatch.setattr(store, "_read", lambda _path: {
        "state": "finished", "pid": "99", "birth": "fixture",
        "lifecycle": "finite-worker", "purpose": "production",
    })
    result = store.cleanup(receipt, deadline=30)
    assert result.status == "held" and result.reason == "TimeoutError"
    assert path.read_bytes() == b"retained fixture record"


def test_lifecycle_exposes_held_cleanup_without_retrying_it(tmp_path: Path, monkeypatch) -> None:
    def fixture_owner(path: Path) -> None:
        assert path.is_relative_to(tmp_path)

    if store.os.name == "nt":
        from agent_procutil import _daemon_instance_owner as owner

        monkeypatch.setattr(owner, "assert_path_owner", fixture_owner)
    monkeypatch.setattr(store, "_monotonic_clock", lambda: 20)
    monkeypatch.setattr(store, "birth_identity", lambda _pid: "fixture")
    monkeypatch.setattr(store, "_ensure_monitor", lambda: None)
    monkeypatch.setattr(store, "_owners", {})
    monkeypatch.setattr(store, "_guards", {})
    monkeypatch.setattr(store, "_monitor", None)
    monkeypatch.setattr(store.lease_clock, "bounds", lambda _record: (100, 200))
    record = {"instance_id": "a" * 32}
    calls = []

    @contextmanager
    def locked(receipt, **kwargs):
        assert kwargs == {"wait_seconds": 1, "deadline": 30}
        yield receipt.record_path

    def cleanup(receipt, **kwargs):
        calls.append((receipt, kwargs))
        return InstanceCleanupResult(receipt.instance_id, "held", "unconfirmed")

    monkeypatch.setattr(store, "inspect", lambda _root: [record])
    monkeypatch.setattr(store, "_locked", locked)
    monkeypatch.setattr(store, "_read", lambda _path: {"deadline": "100"})
    monkeypatch.setattr(store, "_write", lambda *_args: None)
    monkeypatch.setattr(store, "cleanup", cleanup)
    outcomes = []
    with store.test_lifecycle(
        "fixture", tmp_path / "registry", "unattended-test", None,
        deadline=30, cleanup_results=outcomes,
    ) as environment:
        pairs = base64.b64decode(environment[TEST_CONTEXT_ENV]).splitlines()
        context = {
            key.decode(): base64.b64decode(value).decode()
            for key, value in (pair.split(b"\t") for pair in pairs)
        }
        record["run_id"] = context["run_id"]
        assert "qualification_deadline" not in context
    assert len(calls) == 1 and calls[0][1] == {"deadline": 30}
    assert outcomes == [InstanceCleanupResult("a" * 32, "held", "unconfirmed")]
    assert store._owners == {}


def test_expired_owner_cannot_renew_a_qualification_lease(monkeypatch) -> None:
    monkeypatch.setattr(store, "_monotonic_clock", lambda: 30)
    monkeypatch.setattr(store, "_guards", {})
    monkeypatch.setattr(store, "_owners", {"fixture": {"qualification_deadline": 30}})
    monkeypatch.setattr(store, "inspect", lambda _root: pytest.fail("expired owner must not renew"))

    class Done(Exception):
        pass

    class Pause:
        def wait(self, seconds):
            assert seconds == 0.1
            raise Done

    monkeypatch.setattr(store, "_pause", Pause())
    with pytest.raises(Done):
        store._observe_expiry()


def test_monitor_settlement_preserves_other_lifecycle_custody(monkeypatch) -> None:
    monkeypatch.setattr(store, "_monotonic_clock", lambda: 20)
    monkeypatch.setattr(store, "_owners", {"other": {}})
    monkeypatch.setattr(store, "_guards", {})
    monkeypatch.setattr(store, "_monitor", SimpleNamespace(
        join=lambda **_kwargs: pytest.fail("must not join another lifecycle's monitor"),
    ))
    with pytest.raises(RuntimeError, match="other live custody"):
        store._settle_lifecycle_monitor(30)
    assert store._owners == {"other": {}}


def test_monitor_wait_has_no_new_relative_grace(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(store, "_monotonic_clock", lambda: 29)
    monkeypatch.setattr(store, "_owners", {})
    monkeypatch.setattr(store, "_guards", {})
    monkeypatch.setattr(store, "_monitor", SimpleNamespace(
        join=lambda **kwargs: calls.append(kwargs),
        is_alive=lambda: True,
    ))
    with pytest.raises(TimeoutError, match="monitor"):
        store._settle_lifecycle_monitor(30)
    assert calls == [{"timeout": 1}]
