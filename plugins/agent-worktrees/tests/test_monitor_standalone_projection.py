"""Cold standalone updater handoff retains a separate content-computation owner."""

import argparse
from pathlib import Path
from typing import Any

import pytest

from agent_worktrees import __main__ as core
from agent_worktrees import config as cfg
from agent_worktrees import git_ops, push_timeout, status_bar_cli, status_monitor_discovery
from agent_worktrees import status_monitor_runtime, status_updater_cli, tracking
from agent_worktrees.status_monitor_content import refresh_monitor_content
from agent_worktrees.status_monitor_presentation import run_monitor_sweep
from agent_worktrees.status_segment_cache import _StatusSegmentCache


class StandaloneContent:
    completed: str | None = None
    phase = "projection"

    def get_cached(self, path: str) -> str | None:
        return self.completed

    def get(self, path: str) -> str:
        assert self.phase == "producer"
        assert cfg.active_project() is None
        self.completed = "standalone WIP"
        return self.completed

    def observation_blocked(self, project: str, path: str | None = None) -> bool:
        return False

    def pending_segment_for_path(self, path: str) -> str:
        return "pending"

    def retain_unclaimed_cleanup(
        self, project: str, path: str | None, error: push_timeout.ObservationCleanupError,
    ) -> None:
        pytest.fail("unexpected standalone cleanup failure")


def test_cold_standalone_updater_handoff_gets_scheduled_content_without_tracking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    checkout = tmp_path / "standalone"
    checkout.mkdir()
    registry = tmp_path / "registry"
    session = "wt-standalone"
    monkeypatch.setattr(core, "_monitor_registry_dir", lambda: registry)
    monkeypatch.setattr(status_updater_cli, "_status_monitor_enabled", lambda: True)
    monkeypatch.setattr(status_updater_cli, "_ensure_status_monitor", lambda: True)
    assert status_updater_cli.cmd_status_updater(argparse.Namespace(
        session=session, mux="tmux", path=str(checkout), interval=15,
    )) == 0
    assert (registry / session).read_text(encoding="utf-8") == str(checkout)
    monkeypatch.setattr(core, "_monitor_list_sessions", lambda *a: {session: (1, "$7:100")})
    monkeypatch.setattr(core, "_manager_owned_mapping_for_session", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(status_monitor_runtime, "_manager_owned_mapping_for_session", lambda *a, **k: None)
    monkeypatch.setattr(status_monitor_discovery, "discover_connected_worktrees", lambda *a, **k: {})
    monkeypatch.setattr(core, "_status_monitor_recheck", lambda *a: None)
    monkeypatch.setattr(core, "_wait_for_lifecycle_priority", lambda *a: None)
    monkeypatch.setattr(core, "_monitor_maybe_trigger_handoff_cutover", lambda *a, **k: None)
    monkeypatch.setattr(core, "_warm_list_cache_for_active_project", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(core, "_activate_project_for_path", lambda *a, **k: cfg.set_active_project(None), raising=False)
    monkeypatch.setattr(cfg, "project_name", lambda: "unadopted")
    monkeypatch.setattr(git_ops, "_windows_observation", lambda: False)
    monkeypatch.setattr(tracking, "list_records", lambda *a: [])
    monkeypatch.setattr(status_bar_cli, "_render_status_context", lambda *a, **k: "standalone context")
    monkeypatch.setattr(core, "_render_status_context", lambda *a, **k: "standalone context", raising=False)
    writes: list[tuple[str, str]] = []

    def publish(mux_bin: str, target: str, option: str, value: str) -> bool:
        assert target == session
        writes.append((option, value))
        return True

    monkeypatch.setattr(core, "_monitor_mux_set", publish, raising=False)
    owner = StandaloneContent()
    contexts: set[str] = set()
    published: dict[tuple[str, str], str] = {}
    for index in range(2):
        paths: dict[str, str | None] = {}
        assert run_monitor_sweep(
            core._monitor_sweep, "tmux", "monitor", "fixture", contexts,
            segment_cache=owner, content_paths=paths, published=published,
        ) == 1
        assert paths == {str(checkout): None}
        if index == 0:
            previous = cfg.active_project()
            owner.phase = "producer"
            refresh_monitor_content(
                paths, owner, recheck=lambda *a: None, governance_deferred=RuntimeError,
            )
            assert cfg.active_project() == previous
            owner.phase = "projection"
    segments = [value for option, value in writes if option == "@aw_seg"]
    assert len(segments) == 2 and "STATE(?)" in segments[0]
    assert segments[1] == "standalone WIP"


def test_warm_posix_multi_session_projection_avoids_path_scans_and_keeps_cleanup_fences(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(git_ops, "_windows_observation", lambda: False)
    monkeypatch.setattr(cfg, "active_project", lambda: "project")
    monkeypatch.setattr(core, "_find_record_for_path", lambda path: None, raising=False)
    rendered: list[str] = []

    def render(path: str, **options: object) -> str:
        rendered.append(path)
        return "computed:" + Path(path).name

    monkeypatch.setattr(core, "_render_status_segment", render, raising=False)
    owner = _StatusSegmentCache()
    paths = [str(tmp_path / name) for name in ("one", "two", "three", "four")]
    for path in paths:
        assert owner.get(path) == "computed:" + Path(path).name
    scanned: list[str] = []
    original = owner.project_for_observation_path

    def project_scan(path: str) -> str | None:
        scanned.append(path)
        return original(path)

    monkeypatch.setattr(owner, "project_for_observation_path", project_scan)

    def sweep(*args: Any, **options: Any) -> int:
        view = options["segment_cache"]
        for path in paths:
            assert view.get(path) == "computed:" + Path(path).name
        return len(paths)

    assert run_monitor_sweep(
        sweep, "tmux", "monitor", "fixture", set(), segment_cache=owner,
        state_snapshot=lambda project: {
            "ok": True, "projection": {"quality": "current"}, "worktrees": [
                {"id": Path(path).name, "path": path, "state": "active"} for path in paths
            ],
        },
    ) == len(paths)
    assert rendered == paths and scanned == []
    ticket = owner.begin_observation("cleanup-project", None, paths[0])
    failure = push_timeout.ObservationCleanupError("held")
    monkeypatch.setattr(failure, "try_settle", lambda: pytest.fail("not the cleanup owner"))
    owner.retain_cleanup(ticket, failure)
    assert owner.get_cached(paths[0]) is None
    assert owner.get_cached(paths[1]) == "computed:two"
    assert scanned == []
