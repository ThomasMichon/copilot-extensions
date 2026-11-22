"""Deadline and retained-outcome controls without starting daemon instances."""

from __future__ import annotations

import base64
from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_procutil import _daemon_instance_store as store
from agent_procutil import _daemon_instance_clock as lease_clock
from agent_procutil.daemon_instances import DaemonInstanceReceipt, InstanceCleanupResult, TEST_CONTEXT_ENV


def test_public_exact_cleanup_preserves_original_deadline_and_runtime_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agent_procutil.daemon_instances import cleanup_instance

    receipt = DaemonInstanceReceipt("a" * 32, tmp_path / "registry")
    outcome = InstanceCleanupResult(receipt.instance_id, "held", "fixture")
    calls: list[tuple[DaemonInstanceReceipt, dict[str, Any]]] = []

    def cleanup(original: DaemonInstanceReceipt, **options: Any) -> InstanceCleanupResult:
        calls.append((original, options))
        return outcome

    monkeypatch.setattr(store, "cleanup", cleanup)
    assert cleanup_instance(
        receipt, terminate=False, deadline=30, runtime_root=tmp_path,
    ) is outcome
    assert calls == [(receipt, {
        "terminate": False, "deadline": 30, "runtime_root": tmp_path,
    })]


def test_expired_lifecycle_lock_never_creates_or_deletes_inventory_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(store, "_monotonic_clock", lambda: 30)
    path = tmp_path / "owned.lock"
    with pytest.raises(TimeoutError, match="original"):
        with store._file_lock(path, deadline=30):
            pytest.fail("an expired original bound must not acquire inventory custody")
    assert not path.exists()


def test_local_lock_acquisition_consumes_only_remaining_original_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [20.0]
    calls: list[dict[str, Any] | str] = []

    class Guard:
        def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
            calls.append({"timeout": timeout})
            now[0] = 30
            return True

        def release(self) -> None:
            calls.append("released")

    monkeypatch.setattr(store, "_monotonic_clock", lambda: now[0])
    with pytest.raises(TimeoutError):
        with store._local_custody(Guard(), deadline=30):
            pytest.fail("lock acquisition cannot replenish an expired deadline")
    assert calls == [{"timeout": 10.0}, "released"]


def test_cleanup_overrun_retains_the_record_and_reports_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    now = [20.0]
    receipt = DaemonInstanceReceipt("a" * 32, tmp_path)
    path = receipt.record_path
    path.write_bytes(b"retained fixture record")

    @contextmanager
    def locked(_receipt: DaemonInstanceReceipt, **kwargs: Any) -> Iterator[Path]:
        assert kwargs == {"deadline": 30}
        yield path

    def observed(_pid: int, _birth: str) -> str:
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
    failure = next(record for record in caplog.records if "cleanup failed" in record.message)
    assert receipt.instance_id in failure.message
    assert failure.exc_info is not None and failure.exc_info[0] is TimeoutError


def test_lifecycle_exposes_held_cleanup_without_retrying_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    def fixture_owner(path: Path) -> None:
        assert path.is_relative_to(tmp_path)

    if os.name == "nt":
        from agent_procutil import _daemon_instance_owner as owner

        monkeypatch.setattr(owner, "assert_path_owner", fixture_owner)
    monkeypatch.setattr(store, "_monotonic_clock", lambda: 20)
    monkeypatch.setattr(store, "_root", lambda path, *, create: path)
    monkeypatch.setattr(store, "birth_identity", lambda _pid: "fixture")
    monkeypatch.setattr(store, "_ensure_monitor", lambda: None)
    monkeypatch.setattr(store, "_owners", {})
    monkeypatch.setattr(store, "_guards", {})
    monkeypatch.setattr(store, "_monitor", None)
    monkeypatch.setattr(lease_clock, "bounds", lambda _record: (100, 200))
    record = {"instance_id": "a" * 32}
    calls: list[tuple[DaemonInstanceReceipt, dict[str, Any]]] = []

    @contextmanager
    def locked(receipt: DaemonInstanceReceipt, **kwargs: Any) -> Iterator[Path]:
        assert kwargs == {"wait_seconds": 1, "deadline": 30}
        yield receipt.record_path

    def cleanup(receipt: DaemonInstanceReceipt, **kwargs: Any) -> InstanceCleanupResult:
        calls.append((receipt, kwargs))
        return InstanceCleanupResult(receipt.instance_id, "held", "unconfirmed")

    monkeypatch.setattr(store, "inspect", lambda _root: [record])
    monkeypatch.setattr(store, "_locked", locked)
    monkeypatch.setattr(store, "_read", lambda _path: {
        "deadline": "100", "state": "bound", "pid": "99", "runtime_root": str(tmp_path),
    })
    monkeypatch.setattr(store, "_write", lambda *_args: None)
    monkeypatch.setattr(store, "cleanup", cleanup)
    outcomes: list[InstanceCleanupResult] = []
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
    diagnostic = next(item.message for item in caplog.records if "cleanup held" in item.message)
    assert f"state=bound pid=99 runtime={tmp_path}" in diagnostic


def test_expired_owner_cannot_renew_a_qualification_lease(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "_monotonic_clock", lambda: 30)
    monkeypatch.setattr(store, "_guards", {})
    monkeypatch.setattr(store, "_owners", {"fixture": {"qualification_deadline": 30}})
    monkeypatch.setattr(store, "inspect", lambda _root: pytest.fail("expired owner must not renew"))

    class Done(Exception):
        pass

    class Pause:
        def wait(self, seconds: float) -> None:
            assert seconds == 0.1
            raise Done

    monkeypatch.setattr(store, "_pause", Pause())
    with pytest.raises(Done):
        store._observe_expiry()


def test_monitor_settlement_preserves_other_lifecycle_custody(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "_monotonic_clock", lambda: 20)
    monkeypatch.setattr(store, "_owners", {"other": {}})
    monkeypatch.setattr(store, "_guards", {})
    monkeypatch.setattr(store, "_monitor", SimpleNamespace(
        join=lambda **_kwargs: pytest.fail("must not join another lifecycle's monitor"),
    ))
    with pytest.raises(RuntimeError, match="other live custody"):
        store._settle_lifecycle_monitor(30)
    assert list(store._owners) == ["other"] and not store._owners["other"]


def test_monitor_wait_has_no_new_relative_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
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
