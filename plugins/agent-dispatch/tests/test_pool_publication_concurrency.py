"""Real contained fork and deterministic concurrency regressions for preparation."""

from __future__ import annotations

import os
import signal
import time
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event
from typing import Any

import pytest

from agent_dispatch.single_instance import SingleInstance
from agent_dispatch.single_instance import SingletonAdmissionError
from agent_dispatch.pool_publication_custody import PoolPublicationError
from tests.test_pool_publication_custody import owner as owner


@pytest.mark.skipif(not hasattr(os, "fork"), reason="native fork is a POSIX-only boundary")
def test_real_fork_resets_mutex_held_by_a_vanished_thread(tmp_path: Any) -> None:
    lock = SingleInstance(tmp_path / "singleton.lock")
    assert lock.acquire()
    acquisition = lock.acquisition
    entered = Event()
    leave = Event()

    def hold() -> None:
        with lock.guard_acquisition(acquisition):
            entered.set()
            assert leave.wait(5)

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            holding = executor.submit(hold)
            assert entered.wait(1)
            try:
                pid = os.fork()
                if pid == 0:
                    # A regressed inherited mutex must fail finitely, not wedge the runner.
                    signal.alarm(2)
                    try:
                        if lock.acquisition is not None:
                            os._exit(1)
                        lock.release()
                        if lock.acquire():
                            os._exit(3)
                    except BaseException:
                        os._exit(2)
                    os._exit(0)
                waited, status = os.waitpid(pid, 0)
                assert waited == pid and os.waitstatus_to_exitcode(status) == 0
            finally:
                leave.set()
            holding.result(timeout=1)
        assert lock.acquisition == acquisition
        contender = SingleInstance(lock.lock_path)
        try:
            assert not contender.acquire()
        finally:
            contender.release()
    finally:
        leave.set()
        lock.release()


def test_capture_deadline_survives_actual_concurrent_reconcile_cleanup(
    owner: Any, monkeypatch: Any,
) -> None:
    daemon, _queue, _calls = owner
    daemon._reconcile_call_budget = 0.1
    units = Event()
    finish_tick = Event()
    release_read = Event()
    recheck_started = Event()
    read_catalog = daemon.client.pool_catalog_metadata
    override_calls = 0

    def units_phase(summary: Any, _deadline: float) -> Any:
        units.set()
        assert finish_tick.wait(2)
        return summary

    def overrides() -> dict:
        nonlocal override_calls
        override_calls += 1
        if override_calls == 2:
            recheck_started.set()
            assert release_read.wait(2)
        return {}

    monkeypatch.setattr(daemon, "_reconcile_cycle", units_phase)
    daemon.overrides_source = overrides
    with ThreadPoolExecutor(max_workers=2) as executor:
        tick = executor.submit(daemon.reconcile_once)
        assert units.wait(1)

        def catalog() -> dict:
            finish_tick.set()
            tick.result(timeout=1)
            assert daemon._reconcile_deadline is None
            return read_catalog()

        daemon.client.pool_catalog_metadata = catalog
        def capture_inputs() -> tuple[Any, float | None]:
            inputs = daemon.capture_pool_publication_inputs()
            return inputs, daemon._pool_capture_deadline.get()

        capture = executor.submit(capture_inputs)
        try:
            assert recheck_started.wait(1)
            inputs, captured_deadline = capture.result(timeout=0.75)
            assert not inputs.coherent
            assert "local-input-capture-unavailable" in inputs.reasons
            assert captured_deadline is None
            # The expired capture did not keep the singleton release mutex.
            released = executor.submit(daemon.release_singleton)
            released.result(timeout=0.75)
            assert daemon._lock.acquisition is None
        finally:
            finish_tick.set()
            release_read.set()
            capture.result(timeout=1)
            tick.result(timeout=1)


def test_standalone_captures_prune_completed_results_without_losing_pending_work(owner: Any) -> None:
    daemon, _queue, _calls = owner
    pending: Future = Future()
    probes = daemon._probe_inflight
    probes.add(pending)
    try:
        for _ in range(10):
            assert daemon.capture_pool_publication_inputs().coherent
            assert daemon._probe_inflight is probes
            assert daemon._probe_inflight == {pending}
    finally:
        pending.set_result(None)
    daemon.capture_pool_publication_inputs()
    assert daemon._probe_inflight == set()


@pytest.mark.parametrize("operation", ["capture_pool_publication_inputs", "prepare_pool_metadata_request"])
@pytest.mark.parametrize("locked", ["singleton", "sources"])
def test_admission_lock_waits_share_the_preparation_deadline(
    owner: Any, operation: str, locked: str,
) -> None:
    daemon, _queue, calls = owner
    daemon._reconcile_call_budget = 0.05
    acquisition = daemon._lock.acquisition
    entered = Event()
    leave = Event()

    def hold() -> None:
        guard = daemon._lock.guard_acquisition(acquisition) if locked == "singleton" else daemon._source_collection_lock
        with guard:
            entered.set()
            assert leave.wait(2)

    with ThreadPoolExecutor(max_workers=2) as executor:
        holding = executor.submit(hold)
        assert entered.wait(1)
        started = time.monotonic()
        try:
            with pytest.raises((SingletonAdmissionError, PoolPublicationError), match="deadline exhausted"):
                getattr(daemon, operation)()
            assert time.monotonic() - started < 0.5
            assert daemon._pool_capture_deadline.get() is None
            assert calls == []
            assert daemon._pool_publisher_custody is None
            if locked == "sources":
                executor.submit(daemon.release_singleton).result(timeout=0.5)
                assert daemon._lock.acquisition is None
        finally:
            leave.set()
            holding.result(timeout=1)


def test_request_preparation_does_not_restart_the_nested_capture_budget(
    owner: Any, monkeypatch: Any,
) -> None:
    daemon, _queue, _calls = owner
    capture = daemon.capture_pool_publication_inputs
    bounded_read = daemon._bounded_read
    outer = []
    reads = []

    def nested_capture() -> Any:
        outer.append(daemon._pool_capture_deadline.get())
        return capture()

    def read(key: str, callback: Any) -> Any:
        reads.append(daemon._pool_capture_deadline.get())
        return bounded_read(key, callback)

    monkeypatch.setattr(daemon, "capture_pool_publication_inputs", nested_capture)
    monkeypatch.setattr(daemon, "_bounded_read", read)
    request = daemon.prepare_pool_metadata_request()
    assert request.operation == "begin"
    assert len(outer) == 1 and outer[0] is not None
    assert reads and all(deadline == outer[0] for deadline in reads)
    assert daemon._pool_capture_deadline.get() is None
