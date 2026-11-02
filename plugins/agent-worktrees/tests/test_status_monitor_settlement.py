"""Native local-status workers remain inside the monitor's actual admission."""

from __future__ import annotations

import argparse
import threading
import time

import pytest
from agent_procutil.daemon_instances import DaemonInstanceGuard, inspect_instances

from agent_worktrees import __main__ as m
from agent_worktrees import self_retire, status_monitor_cutover as cutover
from agent_worktrees import status_monitor_runtime, worktree_status_daemon as status
from agent_worktrees import repository_map


def _wire_request(runtime, results, errors):
    try:
        results.append(status.status_via_daemon(
            runtime.lock_extra(), key="proof:wt",
            payload={"project": "proof", "worktree_id": "wt"},
            fallback=lambda: {"fallback": True}, request_deadline_s=25,
        ))
    except BaseException as exc:
        errors.append(exc)


@pytest.mark.parametrize("worker", ["request", "sweep"])
@pytest.mark.parametrize("exit_mode", [
    "requested", "interrupted", "repeated-interrupt", "forced-supersession", "stop-error",
    "cleanup-oserror", "cleanup-interrupt", "cleanup-repeated-interrupt", "original-error",
    "intake-failed",
])
def test_monitor_keeps_admission_until_native_status_workers_settle(
    tmp_path, monkeypatch, worker, exit_mode,
):
    entered, release = threading.Event(), threading.Event()
    finished, bounded_done = threading.Event(), threading.Event()
    intake_release = threading.Event()
    owners, handles, clients, results, failures, closed = [], [], [], [], [], []
    interrupts = []
    cleanup_failures, guards = [], []
    cleanup_error = (
        KeyboardInterrupt("earlier cleanup interrupted")
        if "interrupt" in exit_mode else OSError("earlier cleanup failed")
    )
    original_error = ValueError("original monitor failure")
    intake_error = OSError("local-status admission close failed")
    start = status.InProcessRuntime.start
    shutdown = status.InProcessRuntime.shutdown
    close_guard = DaemonInstanceGuard.close
    start_sweep = status.start_sweep_thread
    stop_repositories = repository_map.RepositoryMapRuntime.shutdown
    admit = m.status_monitor_cli.cmd_status_monitor.__globals__["admit_daemon_instance"]

    def record_admission(*args, **kwargs):
        guard = admit(*args, **kwargs)
        guards.append(guard)
        return guard

    def fail_earlier_cleanup(owner):
        stop_repositories(owner)
        if not cleanup_failures or (
            exit_mode == "cleanup-repeated-interrupt" and len(cleanup_failures) < 2
        ):
            cleanup_failures.append(cleanup_error)
            raise cleanup_error

    def assemble(project, worktree):
        if worker == "sweep" and not results:
            return {"ok": True}
        entered.set()
        assert release.wait(20)
        finished.set()
        return {"ok": True}

    def record_start(owner, *args):
        start(owner, *args)
        owners.append(owner)
        if exit_mode == "intake-failed":
            close_admission = owner.server.close_admission

            def fail_intake_close(*args):
                if not intake_release.is_set():
                    raise intake_error
                return close_admission(*args)

            monkeypatch.setattr(owner.server, "close_admission", fail_intake_close)
        if worker == "sweep":
            owner.cache._ttl = -1
        if exit_mode == "repeated-interrupt":
            native_predicate = (
                owner._sweep_finished.wait if worker == "sweep"
                else owner.server.active_handler_count
            )

            def interrupted_predicate(*args):
                if bounded_done.is_set() and len(interrupts) < 2:
                    interrupts.append(True)
                    raise KeyboardInterrupt
                return native_predicate(*args)

            if worker == "sweep":
                monkeypatch.setattr(owner._sweep_finished, "wait", interrupted_predicate)
            else:
                monkeypatch.setattr(owner.server, "active_handler_count", interrupted_predicate)

    def bounded_stop(owner):
        try:
            shutdown(owner)
        finally:
            bounded_done.set()
        # Settlement must retain the pre-stop concrete references.
        owner.server = None
        owner._sweep_thread = None
        owner._sweep_finished = threading.Event()
        if exit_mode == "stop-error":
            raise RuntimeError("bounded teardown failed after closing surfaces")

    def record_close(guard):
        if guard is guards[0]:
            closed.append((finished.is_set(), bounded_done.is_set()))
        return close_guard(guard)

    def guard_state():
        return next(
            record["state"] for record in inspect_instances(guards[0].receipt.registry_root)
            if record["instance_id"] == guards[0].receipt.instance_id
        )

    class Control:
        port = 12345

        def __init__(self, handle, **kwargs):
            handles.append(handle)

        def start(self):
            pass

        def current_read_count(self, action="worktrees-read"):
            return 0

        def close(self):
            pass

    def sweep(*args, **kwargs):
        client = threading.Thread(target=_wire_request, args=(owners[0], results, failures))
        clients.append(client)
        client.start()
        if worker == "sweep":
            client.join(5)
            assert not client.is_alive() and results == [{"ok": True}]
        assert entered.wait(5)
        if exit_mode == "interrupted":
            raise KeyboardInterrupt
        if exit_mode == "original-error":
            raise original_error
        if exit_mode != "forced-supersession":
            assert handles[0]("shutdown", {}) == {"shutdown": True}
        return 1

    monkeypatch.setattr(m, "_monitor_lock_path", lambda: tmp_path / "monitor.lock")
    monkeypatch.setattr(m, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(status_monitor_runtime, "_aw_runtime_home", lambda: tmp_path)
    monkeypatch.setattr(m, "_load_hook_client_module", lambda: None)
    monkeypatch.setattr(m.locks, "write_lock", lambda *args, **kwargs: True)
    monkeypatch.setattr(cutover, "active_generation_for_pid", lambda pid: 42)
    monkeypatch.setattr(cutover, "clear_route_if_owner", lambda pid: False)
    monkeypatch.setattr(cutover, "ControlServer", Control)
    monkeypatch.setattr(self_retire, "is_superseded", lambda *args: (
        exit_mode == "forced-supersession" and entered.is_set()
    ))
    monkeypatch.setattr(m.status_monitor_cli, "_SELF_RETIRE_MAX_GRACE_S", 0)
    monkeypatch.setattr(m.status_monitor_cli, "sweep_mux_pane_args", lambda: None)
    monkeypatch.setattr(m.status_monitor_cli, "_wake_interruptible_wait", lambda *args: time.sleep(.01))
    monkeypatch.setattr(m.loop_governance_mod, "LoopGovernance", lambda: type(
        "Governance", (), {"recheck": lambda self, checkpoint: {"status": "ready"}},
    )())
    monkeypatch.setattr(m, "_monitor_sweep", sweep)
    monkeypatch.setattr(m, "_worktree_status_compute", assemble)
    monkeypatch.setattr(status.InProcessRuntime, "start", record_start)
    monkeypatch.setattr(status.InProcessRuntime, "shutdown", bounded_stop)
    monkeypatch.setattr(status, "start_sweep_thread", lambda *args, **kwargs: start_sweep(
        *args, **kwargs, interval=.01,
    ))
    monkeypatch.setattr(DaemonInstanceGuard, "close", record_close)
    monkeypatch.setitem(
        m.status_monitor_cli.cmd_status_monitor.__globals__,
        "admit_daemon_instance", record_admission,
    )
    if exit_mode.startswith("cleanup-") or exit_mode == "original-error":
        monkeypatch.setattr(repository_map.RepositoryMapRuntime, "shutdown", fail_earlier_cleanup)

    def run_monitor():
        try:
            assert m.cmd_status_monitor(argparse.Namespace(interval=2, passive=True)) == 0
        except BaseException as exc:
            failures.append(exc)

    monitor = threading.Thread(target=run_monitor)
    monitor.start()
    try:
        assert bounded_done.wait(10), (failures, closed, guard_state(), not finished.is_set())
        assert entered.is_set() and not finished.is_set()
        assert monitor.is_alive()
        assert closed == []
        assert guard_state() == "running"
        if exit_mode == "intake-failed":
            release.set()
            assert finished.wait(5)
            deadline = time.monotonic() + 5
            while owners[0].server.active_handler_count() and time.monotonic() < deadline:
                time.sleep(.01)
            assert owners[0].server.active_handler_count() == 0
            assert monitor.is_alive() and guard_state() == "running" and not closed
        if exit_mode == "repeated-interrupt":
            deadline = time.monotonic() + 2
            while len(interrupts) < 2 and time.monotonic() < deadline:
                time.sleep(.01)
            assert len(interrupts) == 2
    finally:
        release.set()
        intake_release.set()
        monitor.join(10)
        for client in clients:
            client.join(5)
        if not bounded_done.is_set():
            for owner in owners:
                status.InProcessRuntime.shutdown_and_wait(owner)
    assert not monitor.is_alive() and not any(client.is_alive() for client in clients)
    assert closed == [(True, True)]
    assert guard_state() == "finished"
    assert results == [{"ok": True}]
    expected = {
        "interrupted": KeyboardInterrupt,
        "repeated-interrupt": KeyboardInterrupt,
        "stop-error": RuntimeError,
        "cleanup-oserror": OSError,
        "cleanup-interrupt": KeyboardInterrupt,
        "cleanup-repeated-interrupt": KeyboardInterrupt,
        "original-error": ValueError,
        "intake-failed": OSError,
    }.get(exit_mode)
    assert [type(error) for error in failures] == ([expected] if expected else [])
    if exit_mode.startswith("cleanup-"):
        assert failures == [cleanup_error]
    if exit_mode == "original-error":
        assert failures == [original_error]
    if exit_mode == "intake-failed":
        assert failures == [intake_error]


@pytest.mark.parametrize("startup_error", [RuntimeError, KeyboardInterrupt])
def test_partial_start_waits_for_real_wire_handler_before_clearing_owners(
    tmp_path, monkeypatch, startup_error,
):
    entered, release, bounded_done = threading.Event(), threading.Event(), threading.Event()
    runtime = status.InProcessRuntime()
    clients, results, failures = [], [], []
    shutdown = runtime.shutdown

    def assemble(*args):
        entered.set()
        assert release.wait(15)
        return {"ok": True}

    def fail_sweep_start(*args, **kwargs):
        client = threading.Thread(target=_wire_request, args=(runtime, results, failures))
        clients.append(client)
        client.start()
        assert entered.wait(5)
        raise startup_error("sweep startup failed")

    def bounded_stop():
        shutdown()
        bounded_done.set()

    monkeypatch.setattr(status, "start_sweep_thread", fail_sweep_start)
    monkeypatch.setattr(runtime, "shutdown", bounded_stop)
    startup_failures = []

    def start():
        try:
            runtime.start(tmp_path / "cache.sqlite3", assemble)
        except BaseException as exc:
            startup_failures.append(type(exc))

    starter = threading.Thread(target=start)
    starter.start()
    try:
        assert bounded_done.wait(5)
        assert starter.is_alive()
        assert runtime.server.active_handler_count() == 1
    finally:
        release.set()
        starter.join(5)
        for client in clients:
            client.join(5)
    assert not starter.is_alive() and not any(client.is_alive() for client in clients)
    assert results == [{"ok": True}] and failures == []
    assert startup_failures == ([KeyboardInterrupt] if startup_error is KeyboardInterrupt else [])
    assert runtime.server is runtime.cache is runtime._sweep_thread is None
    monkeypatch.undo()
    runtime.start(tmp_path / "retry.sqlite3", lambda *args: {"ok": True})
    runtime.shutdown_and_wait()
    runtime.shutdown_and_wait()
    assert runtime.server.active_handler_count() == 0
    assert not runtime._sweep_thread.is_alive()


def test_settlement_waits_for_full_response_handler_not_only_compute(tmp_path, monkeypatch):
    runtime = status.InProcessRuntime()
    runtime.start(tmp_path / "cache.sqlite3", lambda *args: {"ok": True})
    entered, release, bounded_done = threading.Event(), threading.Event(), threading.Event()
    results, failures = [], []
    finished = runtime.server._on_request_finished
    shutdown = runtime.shutdown
    server = runtime.server

    def finish_handler():
        entered.set()
        assert release.wait(10)
        finished()

    def bounded_stop():
        shutdown()
        bounded_done.set()

    monkeypatch.setattr(server, "_on_request_finished", finish_handler)
    monkeypatch.setattr(runtime, "shutdown", bounded_stop)
    client = threading.Thread(target=_wire_request, args=(runtime, results, failures))
    client.start()
    assert entered.wait(5)
    closer = threading.Thread(target=runtime.shutdown_and_wait)
    closer.start()
    try:
        assert bounded_done.wait(5)
        client.join(5)
        assert results == [{"ok": True}] and failures == []
        assert closer.is_alive() and server.active_handler_count() > 0
    finally:
        release.set()
        client.join(5)
        closer.join(5)
    assert not closer.is_alive() and not client.is_alive()
    assert server.active_handler_count() == 0
