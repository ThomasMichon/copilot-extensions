"""Explicit opt-in and active admission, without a real native bus."""

from __future__ import annotations

import sys
import threading
import time
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from worktree_manager import restart_notification_runtime, source_config
from worktree_manager.restart_observer import HostEpoch, RestartObserver


def build_runtime(tmp_path):
    observer = RestartObserver(
        tmp_path, epoch=HostEpoch("linux", "example-environment", "example-boot"),
    )
    return restart_notification_runtime.RestartNotificationRuntime(tmp_path, observer)


def test_default_off_and_passive_do_not_construct_transport(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("default-off/passive runtime constructed a native connection")

    monkeypatch.setitem(sys.modules, "worktree_manager.restart_notifications", SimpleNamespace(
        LogindShutdownAdapter=forbidden,
    ))
    runtime = build_runtime(tmp_path)
    runtime.pump(admitted=False)
    assert runtime.status["admitted"] is False
    runtime.activate()
    runtime.pump(admitted=True)
    assert runtime.status["capabilities"]["shutdown_notifications"]["status"] == "disabled"
    assert not list(tmp_path.iterdir())
    runtime.close()


def test_adapter_is_pumped_and_closed_on_owning_thread(tmp_path, monkeypatch):
    events = []
    owner = threading.get_ident()

    class Adapter:
        status = {"status": "watching", "qualified": False}

        def __init__(self, journal, epoch):
            events.append(("construct", threading.get_ident()))

        def start(self):
            events.append(("start", threading.get_ident()))

        def pump(self):
            events.append(("pump", threading.get_ident()))

        def close(self):
            events.append(("close", threading.get_ident()))

    monkeypatch.setitem(sys.modules, "worktree_manager.restart_notifications", SimpleNamespace(
        LogindShutdownAdapter=Adapter,
    ))
    source_config.config_path(tmp_path).write_text(
        "[restart_recovery]\nshutdown_notifications = true\n", encoding="utf-8",
    )
    runtime = build_runtime(tmp_path)
    runtime.pump(admitted=False)
    assert events == []
    runtime.activate()
    assert events == []
    runtime.pump(admitted=True)
    assert events == [(name, owner) for name in ("construct", "start", "pump")]
    assert runtime.status["capabilities"]["shutdown_notifications"]["qualified"] is False
    runtime.pump(admitted=False)
    assert events[-1] == ("close", owner)
    assert runtime.status["admitted"] is False
    runtime.close()
    assert events.count(("close", owner)) == 1


@pytest.mark.parametrize("platform", ["wsl", "windows"])
def test_unsupported_notification_platform_refuses_before_import(
    tmp_path, monkeypatch, platform,
):
    source_config.config_path(tmp_path).write_text(
        "[restart_recovery]\nshutdown_notifications = true\n", encoding="utf-8",
    )
    monkeypatch.setitem(sys.modules, "worktree_manager.restart_notifications", None)
    runtime = build_runtime(tmp_path)
    runtime.observer.epoch = HostEpoch(platform, "example-environment", "example-boot")
    runtime.activate()
    runtime.pump(admitted=True)
    assert runtime.status["blocker"]
    assert runtime.status["capabilities"]["shutdown_notifications"]["status"] == "blocked"
    runtime.close()


def test_invalid_config_blocks_recovery_without_disabling_mux_daemon(tmp_path):
    from worktree_manager.mux_daemon import MuxDaemonRuntime

    source_config.config_path(tmp_path).write_text(
        "[restart_recovery]\nautomatic_restore = 1\n", encoding="utf-8",
    )
    runtime = MuxDaemonRuntime(tmp_path / "mux.json", root=tmp_path)
    runtime.recovery_observer.epoch = HostEpoch("linux", "example-environment", "example-boot")
    runtime.restart_notifications.activate()
    runtime.start()
    assert runtime.server is not None and runtime.port is not None
    runtime.restart_notifications.pump(admitted=True)
    assert runtime.health()["status"] == "ready"
    assert "must be a boolean" in runtime.health()["restart_recovery"]["blocker"]
    assert runtime.accepting_requests() is True
    runtime.shutdown()


def test_health_keeps_adapter_snapshot_during_concurrent_pause(tmp_path):
    runtime = build_runtime(tmp_path)
    reading = threading.Event()
    paused = threading.Event()
    adapter = SimpleNamespace(status={"status": "watching", "qualified": False}, close=lambda: None)
    runtime._adapter = adapter

    class InterleavedRuntime(type(runtime)):
        def __getattribute__(self, name):
            value = super().__getattribute__(name)
            if name == "_adapter" and threading.current_thread() is reader:
                reading.set()
                assert paused.wait(3)
            return value

    runtime.__class__ = InterleavedRuntime
    results = []
    errors = []

    def health():
        try:
            results.append(runtime.status)
        except Exception as exc:
            errors.append(exc)

    reader = threading.Thread(target=health)
    reader.start()
    try:
        assert reading.wait(3)
        runtime.pause()
    finally:
        paused.set()
        reader.join(3)
    assert not reader.is_alive()
    assert not errors
    assert results[0]["capabilities"]["shutdown_notifications"]["status"] == "watching"
    assert runtime.status["admitted"] is False


def test_non_utf8_config_blocks_recovery_without_disabling_mux_daemon(tmp_path):
    from worktree_manager.mux_daemon import MuxDaemonRuntime

    source_config.config_path(tmp_path).write_bytes("[restart_recovery]\n".encode("utf-16"))
    runtime = MuxDaemonRuntime(tmp_path / "mux.json", root=tmp_path)
    try:
        runtime.recovery_observer.epoch = HostEpoch("linux", "example-environment", "example-boot")
        runtime.restart_notifications.activate()
        runtime.restart_notifications.pump(admitted=True)
        assert "could not read" in runtime.health()["restart_recovery"]["blocker"]
        assert runtime.accepting_requests() is True
    finally:
        runtime.shutdown()


@pytest.mark.parametrize("status", [None, "blocked", "closed"])
def test_idle_wait_without_usable_adapter_does_not_spin_or_enter_mutation(
    tmp_path, monkeypatch, status,
):
    runtime = build_runtime(tmp_path)
    if status is not None:
        runtime._adapter = SimpleNamespace(status={"status": status})
    sleeps = []
    monkeypatch.setattr(restart_notification_runtime.time, "sleep", sleeps.append)

    def forbidden():
        pytest.fail("inactive native wait entered admission/mutation")

    runtime.wait(1.0, admitted=forbidden, mutation=forbidden)
    assert len(sleeps) == 1 and 0 < sleeps[0] <= 1.0


def test_idle_wait_rechecks_admission_and_closes_before_leaving_mutation(tmp_path):
    runtime = build_runtime(tmp_path)
    runtime.activate()
    admitted = True
    mutating = False
    events = []
    owner = threading.get_ident()

    @contextmanager
    def mutation():
        nonlocal mutating
        assert not mutating
        mutating = True
        try:
            yield
        finally:
            mutating = False

    class Adapter:
        status = {"status": "watching", "qualified": False}

        def pump(self, *, wait_s):
            nonlocal admitted
            assert mutating and threading.get_ident() == owner
            assert 0 < wait_s <= 0.05
            events.append("pump")
            time.sleep(wait_s)
            admitted = False

        def close(self):
            assert mutating and threading.get_ident() == owner
            events.append("close")

    runtime._adapter = Adapter()
    runtime.wait(0.15, admitted=lambda: admitted, mutation=mutation)
    assert events == ["pump", "close"]
    assert runtime.status["admitted"] is False
    assert runtime._adapter is None and not mutating
