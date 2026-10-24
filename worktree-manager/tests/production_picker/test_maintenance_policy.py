"""Automatic admission is separate from read and manual-action authority."""

from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from worktree_manager import manager_config, mux_daemon_live
from worktree_manager.production_picker import housekeeping, monitor_roots, runner


@pytest.fixture
def policy(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", str(tmp_path))
    return tmp_path / "config.toml"


def _forbidden(*_args, **_kwargs):
    pytest.fail("automatic effect was admitted")


@pytest.mark.parametrize("body", [
    "[maintenance]\nautomatic = false",
    "[maintenance]\nautomatic = 'false'",
    "[maintenance]\nautomtaic = false",
])
def test_policy_vetoes_every_picker_admission(policy, monkeypatch, body):
    policy.write_text(body, encoding="utf-8")
    monkeypatch.setattr(runner.threading, "Thread", _forbidden)
    monkeypatch.setattr(housekeeping, "sweep_managed_worktrees", _forbidden)
    monkeypatch.setattr(housekeeping, "reap_orphan_launcher_shells", _forbidden)
    monkeypatch.setattr(housekeeping, "sweep_finished_session_worktrees", _forbidden)
    monkeypatch.setattr(monitor_roots, "_write_lock", _forbidden)
    monkeypatch.setattr(monitor_roots.engine_client, "engine_base_command", _forbidden)
    monkeypatch.setattr(monitor_roots, "spawn_daemon", _forbidden)

    runner._start_anchor_heal_check("fixture")
    runner._start_housekeeping()
    housekeeping.sweep_managed_on_exit()
    housekeeping.sweep_launcher_shells_on_exit()
    housekeeping.sweep_finished_sessions_on_cadence()
    assert housekeeping.start_picker_monitor_root("fixture") is None
    assert monitor_roots.start_picker_monitor_root("fixture") is None
    assert monitor_roots.ensure_status_monitor_running() is False
    assert monitor_roots._spawn_status_monitor(["forbidden"]) is False
    assert mux_daemon_live.ensure_status_monitor_running() is False
    heartbeat = monitor_roots.PickerHeartbeat("fixture", ensure_monitor=_forbidden)
    assert heartbeat.start() is False
    assert heartbeat._write() is False
    heartbeat._ensure()


def test_disabled_policy_vetoes_optional_warm_ui_spawn(policy):
    from worktree_manager.warm_picker_pool import WarmPickerPool

    policy.write_text("[maintenance]\nautomatic = false", encoding="utf-8")
    # Refuse before touching a pool's runtime, mux or directory authority.
    pool = object.__new__(WarmPickerPool)
    pool._spawn()


def test_queued_workers_recheck_policy_before_effects(policy, monkeypatch):
    queued = []

    class QueuedThread:
        def __init__(self, *, target, **_kwargs):
            queued.append(target)

        def start(self):
            pass

    monkeypatch.setattr(runner.threading, "Thread", QueuedThread)
    monkeypatch.setattr(runner.engine_group_b, "repair_stale_anchor", _forbidden)
    monkeypatch.setattr(housekeeping, "reap_orphan_mux_sessions", _forbidden)
    runner._start_anchor_heal_check("fixture")
    runner._start_housekeeping()
    assert len(queued) == 2
    policy.write_text("[maintenance]\nautomatic = false", encoding="utf-8")
    for callback in queued:
        callback()


def test_successive_housekeeping_steps_recheck_live_policy(policy, monkeypatch):
    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(runner.threading, "Thread", InlineThread)
    monkeypatch.setattr(
        housekeeping, "reap_orphan_mux_sessions",
        lambda **_kwargs: policy.write_text("[maintenance]\nautomatic = false"),
    )
    monkeypatch.setattr(housekeeping, "sweep_managed_worktrees", _forbidden)
    monkeypatch.setattr(housekeeping, "reap_orphan_launcher_shells", _forbidden)
    monkeypatch.setattr(housekeeping, "sweep_finished_session_worktrees", _forbidden)
    runner._start_housekeeping()


def test_disabled_policy_preserves_manual_engine_adapter(policy, monkeypatch):
    policy.write_text("[maintenance]\nautomatic = false", encoding="utf-8")
    called = []
    monkeypatch.setattr(housekeeping.context, "project", lambda: "fixture")
    monkeypatch.setattr(
        housekeeping.engine_group_d, "sweep_managed_worktrees",
        lambda project: called.append(project) or {"removed": []},
    )
    assert housekeeping.sweep_managed_worktrees() == {"removed": []}
    assert called == ["fixture"]


def test_existing_heartbeat_stops_renewal_and_ensure_after_policy_change(policy, monkeypatch):
    calls = []
    monkeypatch.setattr(monitor_roots, "_write_lock", lambda *a, **kw: calls.append("write") or True)
    heartbeat = monitor_roots.PickerHeartbeat(
        "fixture", ensure_monitor=lambda: calls.append("ensure") or True,
    )
    assert heartbeat._write() is True
    heartbeat._ensure()
    assert calls == ["write", "ensure"]
    policy.write_text("[maintenance]\nautomatic = false", encoding="utf-8")
    assert heartbeat._write() is False
    heartbeat._ensure()
    assert calls == ["write", "ensure"]


@pytest.mark.parametrize("body", [
    "[maintenance]\nautomatic = false",
    "[maintenance]\nautomatic = 'false'",
])
def test_runner_still_binds_and_returns_browsing_result(policy, monkeypatch, body):
    policy.write_text(body, encoding="utf-8")
    monkeypatch.setattr(runner, "_select_project", lambda project: project)
    monkeypatch.setattr(
        runner.engine_group_b, "picker_bootstrap",
        lambda project: dict(version=1, project=project, default_live=False),
    )
    monkeypatch.setattr(runner, "_start_anchor_heal_check", _forbidden)
    monkeypatch.setattr(runner, "_start_housekeeping", _forbidden)
    monkeypatch.setattr(housekeeping, "start_picker_monitor_root", _forbidden)

    def picker(*, prepare, after_first_refresh, **_kwargs):
        binding = prepare()
        binding.activate()
        if "'false'" in body:
            with pytest.raises(manager_config.ManagerConfigError):
                after_first_refresh()
        else:
            assert "Automatic maintenance disabled" in after_first_refresh()
        return {"action": "cancel"}

    monkeypatch.setattr(runner, "run_tui_picker", picker)
    try:
        assert runner.run("fixture") == {"action": "cancel"}
    finally:
        runner.context.reset()


@pytest.mark.parametrize("fails", [False, True])
def test_startup_policy_result_and_errors_are_delivered_through_ui_inbox(monkeypatch, fails):
    from worktree_manager.production_picker.picker_tui import engine

    class InlineThread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            self.target()

    monkeypatch.setattr(threading, "Thread", InlineThread)
    monkeypatch.delenv("AGENT_WORKTREES_PICKER_FRAME_HEALTH", raising=False)
    monkeypatch.delenv("AGENT_WORKTREES_LAUNCH_TRACE", raising=False)
    notices = []

    def callback():
        if fails:
            raise manager_config.ManagerConfigError("maintenance.automatic must be a boolean")
        return "Automatic maintenance disabled"

    screen = engine.PickerScreen(
        SimpleNamespace(LOCAL=("fixture", "Win")),
        live=False, after_first_refresh=callback,
    )
    screen.inbox._home_thread_id = -1
    monkeypatch.setattr(screen, "post_message", lambda _message: True)
    monkeypatch.setattr(screen, "notify", lambda message, **_kw: notices.append(message))
    screen._after_first_refresh()
    assert notices == []
    screen.inbox.drain_apply()
    if fails:
        assert "Automatic maintenance failed" in screen.debug
        assert "maintenance.automatic" in screen.debug
    else:
        assert notices == ["Automatic maintenance disabled"]
