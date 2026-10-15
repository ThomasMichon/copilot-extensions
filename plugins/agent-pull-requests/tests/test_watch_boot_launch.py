"""The watch daemon boots through the shared resident-daemon launch."""

from __future__ import annotations

import sys
from pathlib import Path

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


def test_relative_home_override_is_passed_resolved(monkeypatch, tmp_path):
    # The child runs from the state dir: a relative override inherited as-is
    # would resolve to <state>/<state> there, apart from the clients' copy.
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("AGENT_PULL_REQUESTS_HOME", "state")
    seen: dict = {}
    monkeypatch.setattr(agent_procutil, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    cli._watch_boot()
    root = (tmp_path / "state").resolve()
    assert seen["cwd"] == root
    assert seen["env_overrides"] == {"AGENT_PULL_REQUESTS_HOME": str(root)}


def test_unset_home_override_stays_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_PULL_REQUESTS_HOME", raising=False)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    seen: dict = {}
    monkeypatch.setattr(agent_procutil, "spawn_daemon", lambda argv, **kw: seen.update(kw))
    cli._watch_boot()
    assert not seen.get("env_overrides")
