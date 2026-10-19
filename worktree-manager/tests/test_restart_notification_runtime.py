"""Explicit opt-in and active admission, without a real native bus."""

from __future__ import annotations

import sys
import threading
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
    runtime.restart_notifications.pump(admitted=True)
    assert "must be a boolean" in runtime.health()["restart_recovery"]["blocker"]
    assert runtime.accepting_requests() is True
    runtime.shutdown()
