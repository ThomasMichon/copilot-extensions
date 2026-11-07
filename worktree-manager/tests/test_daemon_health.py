from __future__ import annotations

from pathlib import Path

from worktree_manager import daemon_health


def test_candidates_are_scoped_to_requested_root(tmp_path: Path, monkeypatch):
    root = tmp_path / "cell-a"
    root.mkdir()
    monkeypatch.setattr(
        daemon_health.mux_daemon_cutover,
        "_iter_mux_daemon_pids",
        lambda: {101, 202},
    )
    monkeypatch.setattr(
        daemon_health.mux_daemon_cutover,
        "_pid_matches_root",
        lambda pid, *, root: pid == 101,
    )
    monkeypatch.setattr(
        daemon_health.diagnostics,
        "process_start_time",
        lambda pid: f"start-{pid}",
    )

    candidates = daemon_health._candidates(root)

    assert [candidate.pid for candidate in candidates] == [101]
    assert candidates[0].start_time == "start-101"
