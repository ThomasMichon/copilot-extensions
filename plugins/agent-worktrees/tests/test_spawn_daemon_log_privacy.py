"""The vendored agent_procutil keeps resident-daemon logs private on POSIX.

agent-worktrees' resident daemons are launched through spawn_daemon, whose
durable log receives raw daemon stdout/stderr (possibly credential-bearing
errors). A umask-derived 0644 log would be readable by other local users.
"""
from __future__ import annotations

import os
import stat
from unittest.mock import Mock

import pytest

import agent_procutil

pytestmark = [
    pytest.mark.guard,
    pytest.mark.skipif(
        os.name != "posix",
        reason="POSIX file modes do not apply on Windows (the log inherits its ACL)",
    ),
]


@pytest.fixture()
def launch_modes(monkeypatch):
    seen: list[int] = []

    def fake_popen(argv, **kwargs):
        seen.append(stat.S_IMODE(os.fstat(kwargs["stdout"].fileno()).st_mode))
        proc = Mock()
        proc.pid = 4242
        return proc

    monkeypatch.setattr(agent_procutil.subprocess, "Popen", fake_popen)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    previous = os.umask(0o022)
    try:
        yield seen
    finally:
        os.umask(previous)


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


def test_spawn_daemon_creates_private_log(tmp_path, launch_modes):
    log_path = tmp_path / "logs" / "daemon.log"

    agent_procutil.spawn_daemon(["py"], cwd=tmp_path, log_path=log_path)

    assert launch_modes == [0o600]
    assert _mode(log_path) == 0o600
    assert _mode(log_path.parent) == 0o700


def test_spawn_daemon_tightens_readable_log_before_launch(tmp_path, launch_modes):
    log_path = tmp_path / "daemon.log"
    log_path.write_bytes(b"earlier run\n")
    os.chmod(log_path, 0o644)

    agent_procutil.spawn_daemon(["py"], cwd=tmp_path, log_path=log_path)

    assert launch_modes == [0o600]
    assert _mode(log_path) == 0o600
    assert log_path.read_bytes().startswith(b"earlier run\n")
