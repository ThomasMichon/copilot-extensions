"""Tests for the detached, update-safe session-end sync launcher (spawn.py)."""

from __future__ import annotations

import contextlib
import os
import shutil
import sys
from pathlib import Path

import pytest

from agent_logger.config import load_config
from agent_logger.sync import spawn


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    home = tmp_path / "agent-logger-home"
    home.mkdir()
    monkeypatch.setenv("AGENT_LOGGER_HOME", str(home))
    return load_config(include_repo=False)


class _FakePopen:
    """Capture the launched command without spawning anything."""

    calls: list[tuple[list[str], dict]] = []

    def __init__(self, cmd, **kwargs):
        type(self).calls.append((cmd, kwargs))


def _staged_dirs_cleanup(*dirs: str) -> None:
    for d in dirs:
        shutil.rmtree(d, ignore_errors=True)


def test_spawn_stages_package_and_launches_detached(cfg, monkeypatch):
    _FakePopen.calls = []
    monkeypatch.setattr(spawn.subprocess, "Popen", _FakePopen)

    rc = spawn.spawn_detached_sync(cfg, prune=False)

    assert rc == 0
    assert len(_FakePopen.calls) == 1
    cmd, kwargs = _FakePopen.calls[0]
    assert cmd == [sys.executable, "-m", "agent_logger.sync.engine", "run"]

    staged = kwargs["cwd"]
    # cwd is a fresh temp staging dir -- never the caller's (worktree) cwd.
    assert Path(staged).name.startswith("agent-logger-sync-")
    assert Path(staged, "agent_logger", "sync", "engine.py").is_file()
    # tests/ are not staged (only the package source is copied).
    assert not Path(staged, "agent_logger", "tests").exists()

    env = kwargs["env"]
    assert env["AGENT_LOGGER_SYNC_STAGED"] == staged
    assert staged in env["PYTHONPATH"].split(os.pathsep)
    # fully detached: no inherited stdio.
    assert kwargs["stdin"] == spawn.subprocess.DEVNULL
    assert kwargs["stdout"] == spawn.subprocess.DEVNULL

    _staged_dirs_cleanup(staged)


def test_spawn_passes_prune_flag(cfg, monkeypatch):
    _FakePopen.calls = []
    monkeypatch.setattr(spawn.subprocess, "Popen", _FakePopen)

    spawn.spawn_detached_sync(cfg, prune=True)

    cmd, kwargs = _FakePopen.calls[0]
    assert cmd[-1] == "--prune"
    _staged_dirs_cleanup(kwargs["cwd"])


def test_spawn_dedupes_when_a_sync_is_running(cfg, monkeypatch):
    _FakePopen.calls = []
    monkeypatch.setattr(spawn.subprocess, "Popen", _FakePopen)

    @contextlib.contextmanager
    def _lock_held(*_a, **_k):
        yield False  # lock not acquired -> a sync is already in flight

    monkeypatch.setattr(spawn, "sync_lock", _lock_held)

    rc = spawn.spawn_detached_sync(cfg, prune=False)

    assert rc == 0
    assert _FakePopen.calls == []  # no staging, no spawn


def test_cleanup_staging_removes_marked_dir(monkeypatch, tmp_path):
    staged = tmp_path / "agent-logger-sync-xyz"
    (staged / "agent_logger").mkdir(parents=True)
    monkeypatch.setenv("AGENT_LOGGER_SYNC_STAGED", str(staged))

    orig = os.getcwd()
    try:
        spawn.cleanup_staging()
    finally:
        os.chdir(orig)

    assert not staged.exists()


def test_cleanup_staging_is_noop_without_marker(monkeypatch):
    monkeypatch.delenv("AGENT_LOGGER_SYNC_STAGED", raising=False)
    orig = os.getcwd()
    try:
        spawn.cleanup_staging()  # must not raise
    finally:
        os.chdir(orig)
