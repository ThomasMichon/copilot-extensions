"""The supervisor admission outlives its real executor work on every local exit."""

from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from agent_dispatch import diagnostic_logs, supervisor_daemon
from agent_dispatch.supervisor_daemon import SupervisorDaemon, SELF_UPDATE_EXIT_CODE
from agent_procutil.daemon_instances import INSTANCE_ENV, daemon_test_lifecycle, inspect_instances


@pytest.mark.parametrize("work", ["materialize", "probe", "publication", "retired", "callback"])
@pytest.mark.parametrize("exit_kind", ["once", "interrupt", "startup-error"])
def test_supervisor_admission_waits_for_actual_executor_retirement(
    monkeypatch, tmp_path, work, exit_kind,
):
    _exercise_retirement(monkeypatch, tmp_path, work=work, exit_kind=exit_kind)


def test_supervisor_repeated_cleanup_interrupts_defer_until_retirement(monkeypatch, tmp_path):
    _exercise_retirement(monkeypatch, tmp_path, work="publication", exit_kind="cleanup-interrupt")


def test_supervisor_does_not_shutdown_a_borrowed_executor(monkeypatch, tmp_path):
    _exercise_retirement(monkeypatch, tmp_path, work="borrowed", exit_kind="once")


@pytest.mark.parametrize("force_exit", ["returns", "raises", "shutdown-interrupt"])
def test_self_update_retains_old_admission_if_force_exit_does_not_terminate(
    monkeypatch, tmp_path, force_exit,
):
    _exercise_retirement(
        monkeypatch, tmp_path, work="probe", exit_kind="self-update", force_exit=force_exit,
    )


@pytest.mark.parametrize("force_exit", ["returns", "raises"])
def test_self_update_done_future_does_not_hide_live_owned_callback(monkeypatch, tmp_path, force_exit):
    _exercise_retirement(
        monkeypatch, tmp_path, work="callback", exit_kind="self-update", force_exit=force_exit,
    )


@pytest.mark.parametrize("force_exit", ["returns", "raises"])
@pytest.mark.parametrize("cleanup_failure", ["client", "singleton", "both"])
def test_self_update_cleanup_interrupt_cannot_bypass_retirement(
    monkeypatch, tmp_path, force_exit, cleanup_failure,
):
    _exercise_retirement(
        monkeypatch, tmp_path, work="probe", exit_kind="self-update",
        force_exit=force_exit, cleanup_failure=cleanup_failure,
    )


@pytest.mark.parametrize("force_exit", ["returns", "raises"])
def test_self_update_preserves_original_cancellation_over_cleanup_and_exit_errors(
    monkeypatch, tmp_path, force_exit,
):
    _exercise_retirement(
        monkeypatch, tmp_path, work="probe", exit_kind="self-update",
        force_exit=force_exit, cleanup_failure="both", original_cancel=True,
    )


def test_self_update_callback_can_retire_within_existing_grace(monkeypatch, tmp_path):
    _exercise_retirement(
        monkeypatch, tmp_path, work="callback", exit_kind="self-update",
        shutdown_grace=0.5, expect_force=False,
    )


def test_self_update_cached_false_drain_does_not_grant_another_grace(monkeypatch, tmp_path):
    _exercise_retirement(
        monkeypatch, tmp_path, work="probe", exit_kind="self-update", prior_shutdown=True,
    )


def test_self_update_does_not_force_exit_for_unrelated_borrowed_idle_pool(monkeypatch, tmp_path):
    registry = tmp_path / "registry"
    with ThreadPoolExecutor(max_workers=1) as borrowed:
        daemon = SupervisorDaemon(
            SimpleNamespace(), "private-test", runtime_executor=borrowed,
            companion_controller=SimpleNamespace(receipt_futures=lambda: []),
        )
        tracked = borrowed.submit(lambda: 7)
        assert tracked.result(timeout=2) == 7
        daemon._probe_inflight.add(tracked)
        monkeypatch.setattr(diagnostic_logs, "maintain_file_logging", lambda: None)
        monkeypatch.setattr(daemon, "_recheck_governance", lambda checkpoint: None)
        monkeypatch.setattr(daemon, "_maybe_self_update", lambda: True)
        monkeypatch.setattr(
            daemon, "reconcile_once", lambda: SimpleNamespace(running=[], backing_off=[], dead=[]),
        )

        def forbidden_exit(code):
            raise AssertionError("borrowed idle pool is not retained supervisor work")

        monkeypatch.setattr(supervisor_daemon.os, "_exit", forbidden_exit)
        with daemon_test_lifecycle(owner="dispatch-borrowed-idle", registry_root=registry) as env:
            for name, value in env.items():
                monkeypatch.setenv(name, value)
            monkeypatch.delenv(INSTANCE_ENV, raising=False)
            assert daemon.serve(once=True, single_instance=False) == SELF_UPDATE_EXIT_CODE
            assert inspect_instances(registry)[0]["state"] == "finished"
        assert borrowed.submit(lambda: 8).result(timeout=2) == 8


def _exercise_retirement(
    monkeypatch, tmp_path, *, work, exit_kind, force_exit="returns",
    cleanup_failure=None, original_cancel=False, shutdown_grace=0.02, expect_force=True,
    prior_shutdown=False,
):
    entered = threading.Event()
    release = threading.Event()
    pending = threading.Event()
    returned = threading.Event()
    singleton_released = threading.Event()
    client_closed = threading.Event()
    errors = []
    observed = []
    forced = []
    pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="private-dispatch-retirement")
    body_release = threading.Event()
    callback_entered = threading.Event()
    cleanup_calls = []

    def close_client():
        cleanup_calls.append("client")
        client_closed.set()
        if cleanup_failure in ("client", "both"):
            raise KeyboardInterrupt("client cleanup interrupted")

    def release_singleton():
        cleanup_calls.append("singleton")
        singleton_released.set()
        if cleanup_failure in ("singleton", "both"):
            raise asyncio.CancelledError("singleton cleanup cancelled")

    client = SimpleNamespace(close=close_client)
    daemon = SupervisorDaemon(
        client, "private-test",
        lock=SimpleNamespace(acquire=lambda: True, release=release_singleton),
        client_factory=lambda: client,
        companion_controller=SimpleNamespace(receipt_futures=lambda: []),
        runtime_executor=pool if work == "borrowed" else None,
    )
    if work != "borrowed":
        if work == "publication":
            daemon._publication_executor = pool
        elif work == "retired":
            daemon._retired_runtime_executors.append(pool)
        else:
            daemon._runtime_executor = pool

    def block():
        entered.set()
        gate = body_release if work == "callback" else release
        assert gate.wait(15), "test failed to release its executor worker"

    future = pool.submit(block)
    assert entered.wait(5)
    if work == "callback":
        def finish_callback(completed):
            callback_entered.set()
            assert release.wait(15), "test failed to release its executor callback"

        future.add_done_callback(finish_callback)
        body_release.set()
        assert callback_entered.wait(5)
    workers = tuple(pool._threads)
    if work == "materialize":
        daemon._managed_runtime_inflight.add(future)
    else:
        daemon._probe_inflight.add(future)
    monkeypatch.setattr(supervisor_daemon, "_SHUTDOWN_GRACE_SECONDS", shutdown_grace)
    monkeypatch.setattr(diagnostic_logs, "maintain_file_logging", lambda: None)
    monkeypatch.setattr(
        daemon, "_maybe_self_update", lambda: exit_kind == "self-update",
    )

    def governance(checkpoint):
        if exit_kind == "startup-error":
            raise RuntimeError("startup boundary failed")
        return None

    monkeypatch.setattr(daemon, "_recheck_governance", governance)

    def reconcile():
        if original_cancel:
            daemon.self_update_triggered = True
            raise asyncio.CancelledError("original owner cancellation")
        if exit_kind == "interrupt":
            raise KeyboardInterrupt
        return SimpleNamespace(running=[], backing_off=[], dead=[])

    monkeypatch.setattr(daemon, "reconcile_once", reconcile)
    real_shutdown = daemon.shutdown
    native_join_budgets = []
    if prior_shutdown:
        assert real_shutdown() is False
        for worker in workers:
            real_join = worker.join

            def join(timeout=None, real_join=real_join):
                if timeout is not None:
                    native_join_budgets.append(timeout)
                return real_join(timeout=timeout)

            monkeypatch.setattr(worker, "join", join)
    calls = []

    def shutdown():
        result = real_shutdown()
        calls.append(result)
        assert result is (work == "callback")
        if force_exit == "shutdown-interrupt" and len(calls) == 1:
            pending.set()
            raise KeyboardInterrupt("self-update shutdown interrupted")
        if exit_kind == "cleanup-interrupt":
            if len(calls) == 2:
                raise KeyboardInterrupt("cleanup interrupt")
            if len(calls) == 3:
                pending.set()
                raise asyncio.CancelledError("repeated cleanup cancellation")
        else:
            pending.set()
        return result

    monkeypatch.setattr(daemon, "shutdown", shutdown)
    registry = tmp_path / "registry"

    def force_exit_without_termination(code):
        forced.append(code)
        assert inspect_instances(registry)[0]["state"] == "running"
        assert future.done() if work == "callback" else future.running()
        if force_exit == "raises":
            raise RuntimeError("force exit refused")

    monkeypatch.setattr(supervisor_daemon.os, "_exit", force_exit_without_termination)

    def observe():
        try:
            assert pending.wait(10), "bounded shutdown never reported its live worker"
            time.sleep(0.1)
            record, = inspect_instances(registry)
            assert record["state"] == "running"
            assert not returned.is_set(), "serve returned with a real executor worker pending"
            assert record["purpose"] == "unattended-test"
            assert future.done() if work == "callback" else future.running()
            assert any(thread.is_alive() for thread in workers)
            if exit_kind != "self-update":
                assert not singleton_released.is_set()
                assert not client_closed.is_set()
            observed.append(record["instance_id"])
        except BaseException as exc:
            errors.append(exc)
        finally:
            release.set()
            body_release.set()

    observer = threading.Thread(target=observe, daemon=True)
    try:
        with daemon_test_lifecycle(owner="dispatch-supervisor-retirement", registry_root=registry) as env:
            for name, value in env.items():
                monkeypatch.setenv(name, value)
            monkeypatch.delenv(INSTANCE_ENV, raising=False)
            observer.start()
            try:
                if exit_kind == "startup-error":
                    with pytest.raises(RuntimeError, match="startup boundary failed"):
                        daemon.serve(once=True)
                elif exit_kind == "cleanup-interrupt":
                    with pytest.raises(KeyboardInterrupt, match="cleanup interrupt"):
                        daemon.serve(once=True)
                elif original_cancel:
                    with pytest.raises(asyncio.CancelledError, match="original owner cancellation"):
                        daemon.serve(once=True)
                elif cleanup_failure in ("client", "both"):
                    with pytest.raises(KeyboardInterrupt, match="client cleanup interrupted"):
                        daemon.serve(once=True)
                elif cleanup_failure == "singleton":
                    with pytest.raises(asyncio.CancelledError, match="singleton cleanup cancelled"):
                        daemon.serve(once=True)
                elif exit_kind == "self-update" and force_exit == "raises":
                    with pytest.raises(RuntimeError, match="force exit refused"):
                        daemon.serve(once=True)
                elif exit_kind == "self-update" and force_exit == "shutdown-interrupt":
                    with pytest.raises(KeyboardInterrupt, match="self-update shutdown interrupted"):
                        daemon.serve(once=True)
                else:
                    result = daemon.serve(once=True)
                    assert result == (SELF_UPDATE_EXIT_CODE if exit_kind == "self-update" else 0)
            finally:
                returned.set()
            observer.join(5)
            if errors:
                raise errors[0]
            assert observed
            assert future.done()
            assert inspect_instances(registry)[0]["state"] == "finished"
            assert singleton_released.is_set()
            assert client_closed.is_set()
            assert cleanup_calls == ["client", "singleton"]
            if work == "borrowed":
                assert pool.submit(lambda: 7).result(timeout=2) == 7
            else:
                assert all(not thread.is_alive() for thread in workers)
            if exit_kind == "self-update":
                assert forced == ([SELF_UPDATE_EXIT_CODE] if expect_force else [])
            if prior_shutdown:
                assert native_join_budgets
                assert all(budget == 0 for budget in native_join_budgets)
    finally:
        release.set()
        pool.shutdown(wait=True, cancel_futures=True)
        if observer.ident is not None:
            observer.join(5)
