"""The watch daemon boots through the shared resident-daemon launch."""

from __future__ import annotations

import sys

import agent_procutil

import agent_pull_requests.__main__ as cli


def test_watch_boot_launches_through_spawn_daemon(monkeypatch, tmp_path):
    home = tmp_path / "prs-home"
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", str(home))
    seen: dict = {}

    def _fake_spawn_daemon(argv, **kwargs):
        seen.update(argv=list(argv), **kwargs)

    monkeypatch.setattr(agent_procutil, "spawn_daemon", _fake_spawn_daemon)
    cli._watch_boot()
    root = home.resolve()
    assert root.is_dir()
    assert seen["argv"] == [sys.executable, "-m", "agent_pull_requests", "serve"]
    assert seen["cwd"] == root
    assert seen["log_path"] == root / "watch-daemon.log"
    # It always ran inside its caller's Job before: keep it running there
    # (logged) when that Job forbids breakaway.
    assert seen["contained_fallback"] is True
    assert seen.get("breakaway", True) is True
