from __future__ import annotations

import subprocess

import pytest

from worktree_manager.production_picker import monitor_roots


def test_status_monitor_enabled_env_override_has_no_effect_outside_test_harness(monkeypatch):
    """Mirrors ``agent_worktrees.status_monitor_runtime._status_monitor_
    enabled``'s own regression test exactly: the resident monitor is
    mandatory infrastructure, so a value that would disable it under the
    test harness must have NO effect once ``PYTEST_CURRENT_TEST`` is
    absent -- i.e. in a real operator session running the Picker."""
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "0")
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert monitor_roots.status_monitor_enabled() is True


def test_status_monitor_enabled_env_override_still_works_under_test_harness(monkeypatch):
    """The companion guarantee: the env var still works for test isolation
    as long as ``PYTEST_CURRENT_TEST`` is set -- which pytest sets
    automatically for every test."""
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "0")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "some-test (call)")
    assert monitor_roots.status_monitor_enabled() is False


def test_picker_heartbeat_registers_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor_roots, "_roots_dir", lambda: tmp_path)
    heartbeat = monitor_roots.PickerHeartbeat("project-a", interval=60)

    assert heartbeat.start() is True
    assert monitor_roots.live_picker_projects() == {"project-a"}
    heartbeat.close()
    assert monitor_roots.live_picker_projects() == set()
    assert not heartbeat.path.exists()


def test_stale_picker_heartbeat_is_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor_roots, "_roots_dir", lambda: tmp_path)
    path = monitor_roots._roots_dir() / "picker-stale.json"
    assert monitor_roots._write_lock(path, extra={"kind": "picker", "project": "project-a"})
    data = monitor_roots._read_lock(path)
    assert data is not None

    assert monitor_roots.live_picker_projects(
        now=float(data["created_at"]) + 31, stale_after=30
    ) == set()
    assert not path.exists()


def test_picker_roots_are_project_deduplicated(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor_roots, "_roots_dir", lambda: tmp_path)
    first = monitor_roots.PickerHeartbeat("project-a", interval=60)
    second = monitor_roots.PickerHeartbeat("project-a", interval=60)
    assert first.start() and second.start()
    try:
        assert monitor_roots.live_picker_projects() == {"project-a"}
    finally:
        first.close()
        second.close()


def test_picker_heartbeat_reasserts_monitor(tmp_path, monkeypatch):
    monkeypatch.setattr(monitor_roots, "_roots_dir", lambda: tmp_path)
    ensured: list[bool] = []
    heartbeat = monitor_roots.PickerHeartbeat(
        "project-a",
        interval=0.01,
        ensure_monitor=lambda: ensured.append(True) or True,
    )

    assert heartbeat.start()
    try:
        assert heartbeat._stop.wait(0.03) is False
    finally:
        heartbeat.close()
    assert len(ensured) >= 2


def test_ensure_status_monitor_running_skips_spawn_when_lock_is_live(monkeypatch):
    monkeypatch.setattr(monitor_roots, "status_monitor_enabled", lambda: True)
    monkeypatch.setattr(monitor_roots, "_status_monitor_lock_path", lambda: monitor_roots.Path("C:/status-monitor.lock"))
    monkeypatch.setattr(
        monitor_roots,
        "_read_lock",
        lambda path: {"pid": 123, "start_time": "1", "prefix": "C:/slot", "mux": True},
    )
    monkeypatch.setattr(monitor_roots, "_lock_is_live", lambda data: True)
    monkeypatch.setattr(monitor_roots, "_current_engine_prefix", lambda: "C:/slot")
    monkeypatch.setattr(
        monitor_roots.engine_client,
        "engine_base_command",
        lambda: (_ for _ in ()).throw(AssertionError("should not resolve engine")),
    )

    assert monitor_roots.ensure_status_monitor_running() is True


def test_ensure_status_monitor_running_spawns_status_monitor(monkeypatch):
    calls = []
    monkeypatch.setattr(monitor_roots, "status_monitor_enabled", lambda: True)
    monkeypatch.setattr(monitor_roots, "_status_monitor_lock_path", lambda: monitor_roots.Path("C:/status-monitor.lock"))
    monkeypatch.setattr(monitor_roots, "_read_lock", lambda path: None)
    monkeypatch.setattr(monitor_roots, "_lock_is_live", lambda data: False)
    monkeypatch.setattr(
        monitor_roots.engine_client,
        "engine_base_command",
        lambda: ["C:/aw.ps1"],
    )
    monkeypatch.setattr(
        monitor_roots,
        "_spawn_status_monitor",
        lambda argv: calls.append(list(argv)) or True,
    )

    assert monitor_roots.ensure_status_monitor_running() is True
    assert calls == [["C:/aw.ps1"]]


def test_ensure_status_monitor_running_replaces_incapable_monitor(monkeypatch):
    calls = []
    monkeypatch.setattr(monitor_roots, "status_monitor_enabled", lambda: True)
    monkeypatch.setattr(
        monitor_roots,
        "_status_monitor_lock_path",
        lambda: monitor_roots.Path("C:/status-monitor.lock"),
    )
    monkeypatch.setattr(
        monitor_roots,
        "_read_lock",
        lambda path: {"pid": 123, "start_time": "1", "prefix": "C:/old-slot", "mux": False},
    )
    monkeypatch.setattr(monitor_roots, "_lock_is_live", lambda data: True)
    monkeypatch.setattr(monitor_roots, "_current_engine_prefix", lambda: "C:/new-slot")
    monkeypatch.setattr(monitor_roots, "_caller_has_mux", lambda: True)
    monkeypatch.setattr(
        monitor_roots.engine_client,
        "engine_base_command",
        lambda: ["C:/aw.ps1"],
    )
    monkeypatch.setattr(
        monitor_roots,
        "_spawn_status_monitor",
        lambda argv: calls.append(list(argv)) or True,
    )

    assert monitor_roots.ensure_status_monitor_running() is True
    assert calls == [["C:/aw.ps1"]]


@pytest.mark.parametrize(
    ("data", "caller_has_mux", "prefix", "expected"),
    [
        ({"pid": 1, "prefix": "C:/slot", "mux": True}, True, "C:/slot", True),
        ({"pid": 1, "prefix": "C:/old-slot", "mux": True}, True, "C:/slot", False),
        ({"pid": 1, "prefix": "C:/slot", "mux": False}, True, "C:/slot", False),
        ({"pid": 1, "prefix": "C:/slot", "mux": False}, False, "C:/slot", True),
    ],
)
def test_monitor_lock_satisfies_current_runtime(monkeypatch, data, caller_has_mux, prefix, expected):
    monkeypatch.setattr(monitor_roots, "_lock_is_live", lambda payload: True)
    monkeypatch.setattr(monitor_roots, "_caller_has_mux", lambda: caller_has_mux)
    monkeypatch.setattr(monitor_roots, "_current_engine_prefix", lambda: prefix)

    assert monitor_roots._monitor_lock_satisfies_current_runtime(data) is expected


def test_spawn_status_monitor_uses_clean_runtime_env(monkeypatch):
    popen_calls = []
    monkeypatch.setattr(
        monitor_roots.engine_client,
        "_engine_environment",
        lambda: {
            "KEEP": "1",
            "GH_TOKEN": "secret",
            "COPILOT_AGENT_SESSION_ID": "sid",
            "WORKTREE_PROJECT": "demo",
            monitor_roots.engine_client.ENGINE_ARGV_ENV: '["agent-worktrees"]',
        },
    )
    monkeypatch.setattr(monitor_roots, "_engine_runtime_home", lambda: monitor_roots.Path("C:/runtime"))
    monkeypatch.setattr(
        monitor_roots,
        "windowless_daemon_kwargs",
        lambda breakaway=True: {"creationflags": 123} if breakaway else {},
    )
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda argv, **kwargs: popen_calls.append((list(argv), kwargs)),
    )

    assert monitor_roots._spawn_status_monitor(["C:/aw.ps1"]) is True
    assert popen_calls == [(
        ["C:/aw.ps1", "status-monitor"],
        {
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
            "cwd": str(monitor_roots.Path("C:/runtime")),
            "env": {"KEEP": "1"},
            "creationflags": 123,
        },
    )]


def test_spawn_status_monitor_returns_false_on_failure(monkeypatch):
    monkeypatch.setattr(
        monitor_roots.engine_client,
        "_engine_environment",
        lambda: {},
    )
    monkeypatch.setattr(monitor_roots, "_engine_runtime_home", lambda: monitor_roots.Path("C:/runtime"))
    monkeypatch.setattr(monitor_roots, "windowless_daemon_kwargs", lambda breakaway=True: {})

    def _boom(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(subprocess, "Popen", _boom)

    assert monitor_roots._spawn_status_monitor(["C:/aw.ps1"]) is False
