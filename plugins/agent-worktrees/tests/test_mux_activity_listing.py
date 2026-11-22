"""Mux occupancy comes from the selected provider's exact session listing."""

from __future__ import annotations

import subprocess

import pytest

from agent_worktrees import sessions
from agent_worktrees.mux_list_result import MuxObservationUnavailable


def test_windows_mux_listing_does_not_require_global_server_coverage(monkeypatch):
    from agent_remote_driver import windows_mux_processes

    calls = []

    def run(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "wt-owned:0\nwt-owned-sibling:2\n", "")

    monkeypatch.setattr(sessions.platform, "system", lambda: "Windows")
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(windows_mux_processes, "servers", lambda: pytest.fail("unrelated process census"))
    observed = sessions.mux_status_many(["owned", "own", "owned-sibling"], strict=True)
    assert observed["owned"].exists and observed["owned"].clients == 0
    assert observed["owned-sibling"].exists
    assert not observed["own"].exists
    assert calls == [["psmux", "list-sessions", "-F", "#{session_name}:#{session_attached}"]]
    assert sessions.has_mux_session("owned")
    assert not sessions.has_mux_session("absent")
    assert sessions.has_mux_session("owned", strict=True)
    assert not sessions.has_mux_session("absent", strict=True)


@pytest.mark.parametrize("reply", [
    subprocess.CompletedProcess(["psmux"], 1, "", "provider unavailable"),
    subprocess.CompletedProcess(["psmux"], 0, "not-a-session-count\n", ""),
])
def test_failed_or_malformed_windows_listing_is_not_confirmed_absence(monkeypatch, reply):
    monkeypatch.setattr(sessions.platform, "system", lambda: "Windows")
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: reply)
    with pytest.raises((MuxObservationUnavailable, ValueError)):
        sessions.mux_status_many(["owned"], strict=True)
    with pytest.raises((MuxObservationUnavailable, ValueError)):
        sessions.has_mux_session("owned")


def test_actual_listing_subprocess_matches_exact_worktree_names(tmp_path, monkeypatch):
    import sys

    (tmp_path / "list-sessions").write_text(
        'print("wt-owned:0\\nwt-owned-sibling:1")\n', encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sessions, "_mux_bin", lambda: sys.executable)
    observed = sessions.mux_status_many(["owned", "own", "owned-sibling"], strict=True)
    assert observed["owned"].exists
    assert not observed["own"].exists
    assert observed["owned-sibling"].exists
