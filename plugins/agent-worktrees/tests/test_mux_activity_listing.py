"""Mux occupancy comes from the selected provider's exact session listing."""

from __future__ import annotations

from typing import Any
from pathlib import Path


import subprocess
import platform

import pytest

from agent_worktrees import sessions
from agent_worktrees.mux_list_result import MuxObservationUnavailable


@pytest.fixture(autouse=True)
def qualified_client(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_remote_driver import windows_mux_version

    monkeypatch.setattr(windows_mux_version, "require_creation_binary", lambda binary: binary)


def test_windows_mux_listing_does_not_require_global_server_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_remote_driver import windows_mux_processes

    calls = []

    def run(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 0, "wt-owned:0\nwt-owned-sibling:2\n", "")

    monkeypatch.setattr(platform, "system", lambda: "Windows")
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
    subprocess.CompletedProcess(["psmux"], 0, "\n", ""),
    subprocess.CompletedProcess(["psmux"], 0, "wt-other:0\n\n", ""),
])
def test_failed_or_malformed_windows_listing_is_not_confirmed_absence(monkeypatch: pytest.MonkeyPatch, reply: subprocess.CompletedProcess[str]) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: reply)
    with pytest.raises((MuxObservationUnavailable, ValueError)):
        sessions.mux_status_many(["owned"], strict=True)
    with pytest.raises((MuxObservationUnavailable, ValueError)):
        sessions.has_mux_session("owned")


def test_unqualified_windows_client_is_not_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_remote_driver import windows_mux_version

    def refuse(binary: str) -> str:
        raise RuntimeError("Unknown or legacy executable")

    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(windows_mux_version, "require_creation_binary", refuse)
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: pytest.fail("unqualified client executed"))
    with pytest.raises(MuxObservationUnavailable):
        sessions.mux_status_many(["owned"], strict=True)


def test_qualified_windows_no_server_response_confirms_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(subprocess, "run", lambda argv, **_kw: subprocess.CompletedProcess(
        argv, 1, "", "no server running on fixture-data\n",
    ))
    assert not sessions.mux_status_many(["owned"], strict=True)["owned"].exists


def test_actual_listing_subprocess_matches_exact_worktree_names(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
