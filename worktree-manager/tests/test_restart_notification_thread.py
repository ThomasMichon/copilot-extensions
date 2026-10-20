"""Notification I/O must outpace unrelated daemon work without escaping drain."""

from __future__ import annotations

import sys
import threading
import time
from types import SimpleNamespace

import pytest

from worktree_manager import mux_daemon, source_config
from worktree_manager.restart_observer import HostEpoch


@pytest.fixture
def subject(tmp_path, monkeypatch):
    signal = threading.Event()
    checkpointed = threading.Event()
    started = threading.Event()
    closing = threading.Event()
    close_gate = threading.Event()
    close_gate.set()
    events = []

    class Adapter:
        status = {"status": "watching", "qualified": False}
        cleanup_error = None

        def __init__(self, journal, epoch):
            events.append(("construct", threading.get_ident()))

        def start(self):
            events.append(("start", threading.get_ident()))
            started.set()

        def pump(self, *, wait_s=0.0):
            events.append(("pump", threading.get_ident()))
            if signal.wait(wait_s):
                checkpointed.set()
                signal.clear()

        def close(self):
            events.append(("close", threading.get_ident()))
            closing.set()
            assert close_gate.wait(3), "test did not release notification cleanup"
            self.status = {"status": "closed", "qualified": False}

    monkeypatch.setitem(sys.modules, "worktree_manager.restart_notifications", SimpleNamespace(
        LogindShutdownAdapter=Adapter,
    ))
    source_config.config_path(tmp_path).write_text(
        "[restart_recovery]\nshutdown_notifications = true\n", encoding="utf-8",
    )
    runtime = mux_daemon.MuxDaemonRuntime(tmp_path / "mux.json", root=tmp_path)
    runtime.recovery_observer.epoch = HostEpoch("linux", "example-environment", "example-boot")
    runtime.activate_restart_recovery()
    runtime.start()
    assert runtime.server is not None
    subject = SimpleNamespace(
        runtime=runtime, signal=signal, checkpointed=checkpointed, started=started,
        closing=closing, close_gate=close_gate, events=events, adapter_type=Adapter,
        cleanup_failure_expected=False,
    )
    try:
        yield subject
    finally:
        close_gate.set()
        if subject.cleanup_failure_expected:
            with pytest.raises(RuntimeError, match="cleanup failed"):
                runtime.shutdown()
        else:
            runtime.shutdown()


def until(predicate):
    deadline = time.monotonic() + 2
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert predicate()


def test_notification_io_continues_while_ordinary_daemon_mutation_is_blocked(subject):
    runtime = subject.runtime
    runtime.restart_notifications.pump(admitted=True)
    assert subject.started.wait(1)
    try:
        with runtime.loop_mutation():
            subject.signal.set()
            assert subject.checkpointed.wait(0.5), "ordinary daemon work blocked shutdown I/O"
            assert runtime._loop_mutation_active
    finally:
        runtime.restart_notifications.pause()
    names = [name for name, _ in subject.events]
    owners = {owner for _, owner in subject.events}
    assert names[0:2] == ["construct", "start"] and names[-1] == "close"
    assert names.count("construct") == names.count("close") == 1
    assert len(owners) == 1 and threading.get_ident() not in owners


def test_drain_closes_notification_admission_and_waits_for_owned_cleanup(subject):
    runtime = subject.runtime
    runtime.restart_notifications.pump(admitted=True)
    assert subject.started.wait(1)
    subject.close_gate.clear()
    try:
        assert runtime.drain({"timeout": 0})["drained"] is False
        assert subject.closing.wait(0.5), "drain did not stop independent native I/O"
        assert runtime.drain({"timeout": 0})["drained"] is False
        runtime.restart_notifications.pump(admitted=True)
        assert [name for name, _ in subject.events].count("construct") == 1
    finally:
        subject.close_gate.set()
        runtime.restart_notifications.pause()
    assert runtime.drain({"timeout": 0})["drained"] is True
    runtime.undrain()
    subject.started.clear()
    runtime.restart_notifications.pump(admitted=True)
    assert subject.started.wait(1)
    runtime.restart_notifications.pause()
    assert [name for name, _ in subject.events].count("construct") == 2


@pytest.mark.parametrize("change", ["passive", "draining", "republish"])
def test_stale_main_loop_admission_cannot_start_a_notification_owner(subject, change):
    runtime = subject.runtime
    if change == "passive":
        runtime.passive = True
    elif change == "draining":
        runtime.begin_drain()
    else:
        runtime.republish_enabled = False
    runtime.restart_notifications.pump(admitted=True)
    assert runtime.restart_notifications.drained
    assert subject.events == []
    assert runtime.restart_notifications.status["admitted"] is False


def test_io_failure_is_visible_and_never_busy_retried(subject, monkeypatch, caplog):
    def fail(self, *, wait_s):
        raise OSError("synthetic notification I/O failure")

    monkeypatch.setattr(subject.adapter_type, "pump", fail)
    runtime = subject.runtime.restart_notifications
    runtime.pump(admitted=True)
    until(lambda: runtime.drained)
    assert "synthetic notification I/O failure" in runtime.status["blocker"]
    assert runtime.status["capabilities"]["shutdown_notifications"]["status"] == "blocked"
    assert "Restart notification I/O failed" in caplog.text
    for _ in range(3):
        runtime.pump(admitted=True)
    assert [name for name, _ in subject.events].count("construct") == 1
    assert subject.closing.is_set()


def test_thread_start_failure_is_visible_without_disabling_mux(subject, monkeypatch):
    from worktree_manager import restart_notification_runtime

    def fail(self):
        raise RuntimeError("synthetic thread start failure")

    monkeypatch.setattr(restart_notification_runtime.threading.Thread, "start", fail)
    runtime = subject.runtime
    runtime.restart_notifications.pump(admitted=True)
    assert "synthetic thread start failure" in runtime.health()["restart_recovery"]["blocker"]
    assert runtime.restart_notifications.drained
    assert runtime.server is not None and subject.events == []


def test_failed_native_cleanup_is_not_a_clean_drain_and_server_still_closes(subject, monkeypatch):
    def fail(self):
        raise OSError("synthetic native cleanup failure")

    monkeypatch.setattr(subject.adapter_type, "close", fail)
    subject.cleanup_failure_expected = True
    runtime = subject.runtime
    runtime.restart_notifications.pump(admitted=True)
    assert subject.started.wait(1)
    runtime.begin_drain()
    runtime.restart_notifications.pause()
    assert not runtime.restart_notifications.drained
    assert runtime.drain({"timeout": 0})["drained"] is False
    assert "synthetic native cleanup failure" in runtime.health()["restart_recovery"]["blocker"]
    with pytest.raises(RuntimeError, match="cleanup failed"):
        runtime.shutdown()
    assert runtime.server is None
