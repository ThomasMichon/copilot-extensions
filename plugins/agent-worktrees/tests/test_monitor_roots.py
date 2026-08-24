"""Tests for Picker liveness roots owned by the resident monitor."""

from __future__ import annotations

from agent_worktrees import config as cfg
from agent_worktrees import locks
from agent_worktrees import monitor_roots


def test_picker_heartbeat_registers_and_cleans_up(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path)
    heartbeat = monitor_roots.PickerHeartbeat("project-a", interval=60)

    assert heartbeat.start() is True
    assert monitor_roots.live_picker_projects() == {"project-a"}
    heartbeat.close()
    assert monitor_roots.live_picker_projects() == set()
    assert not heartbeat.path.exists()


def test_stale_picker_heartbeat_is_pruned(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path)
    path = monitor_roots._roots_dir() / "picker-stale.json"
    assert locks.write_lock(
        path, extra={"kind": "picker", "project": "project-a"})
    data = locks.read_lock(path)
    assert data is not None

    assert monitor_roots.live_picker_projects(
        now=float(data["created_at"]) + 31, stale_after=30) == set()
    assert not path.exists()


def test_picker_roots_are_project_deduplicated(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path)
    first = monitor_roots.PickerHeartbeat("project-a", interval=60)
    second = monitor_roots.PickerHeartbeat("project-a", interval=60)
    assert first.start() and second.start()
    try:
        assert monitor_roots.live_picker_projects() == {"project-a"}
    finally:
        first.close()
        second.close()


def test_picker_heartbeat_reasserts_monitor(tmp_path, monkeypatch):
    monkeypatch.setattr(cfg, "install_dir", lambda: tmp_path)
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
