"""spawn_daemon keeps its durable log private to its owner on POSIX.

The log receives the daemon's raw stdout/stderr, which may include
credential-bearing errors, so a umask-derived ``0644`` would let other local
users read it.
"""
from __future__ import annotations

import os
import stat
from unittest.mock import Mock

import pytest

import agent_procutil as pu

posix_only = pytest.mark.skipif(
    os.name != "posix", reason="POSIX file modes do not apply on Windows (the log inherits its ACL)"
)


@pytest.fixture()
def recorded_spawn(monkeypatch, declared_mock_daemon):
    """Fake Popen that records the log file's mode at the moment of launch."""
    seen: list[int] = []

    def fake_popen(argv, **kwargs):
        seen.append(stat.S_IMODE(os.fstat(kwargs["stdout"].fileno()).st_mode))
        proc = Mock()
        proc.pid = 4242
        return proc

    monkeypatch.setattr(pu.subprocess, "Popen", fake_popen)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    return seen


@pytest.fixture()
def permissive_umask():
    # Pin the umask so a newly created file would be 0644 without the fix.
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


def _mode(path) -> int:
    return stat.S_IMODE(os.stat(path).st_mode)


@posix_only
def test_new_daemon_log_and_parent_are_private(tmp_path, permissive_umask, recorded_spawn):
    log_path = tmp_path / "logs" / "daemon.log"

    pu.spawn_daemon(["py"], cwd=tmp_path, log_path=log_path)

    assert recorded_spawn == [0o600], "the child must never see a readable log"
    assert _mode(log_path) == 0o600
    assert _mode(log_path.parent) == 0o700


@posix_only
def test_existing_readable_daemon_log_is_tightened(tmp_path, permissive_umask, recorded_spawn):
    log_path = tmp_path / "daemon.log"
    log_path.write_bytes(b"earlier run\n")
    os.chmod(log_path, 0o644)
    os.chmod(tmp_path, 0o755)

    pu.spawn_daemon(["py"], cwd=tmp_path, log_path=log_path)

    assert recorded_spawn == [0o600], "tightened before the child starts"
    assert _mode(log_path) == 0o600
    assert log_path.read_bytes().startswith(b"earlier run\n"), "still appends"
    assert _mode(tmp_path) == 0o755, "a pre-existing parent directory is left alone"


@posix_only
def test_existing_private_daemon_log_is_not_rechmodded(tmp_path, monkeypatch):
    log_path = tmp_path / "daemon.log"
    log_path.write_bytes(b"")
    os.chmod(log_path, 0o600)
    monkeypatch.setattr(pu.os, "fchmod", Mock(side_effect=AssertionError("unexpected")))

    with pu._open_daemon_log(str(log_path)) as fh:
        fh.write(b"x\n")

    assert _mode(log_path) == 0o600


@posix_only
def test_untightenable_daemon_log_fails_closed(tmp_path, monkeypatch):
    log_path = tmp_path / "daemon.log"
    log_path.write_bytes(b"")
    os.chmod(log_path, 0o644)
    monkeypatch.setattr(pu.os, "fchmod", Mock(side_effect=PermissionError(1, "not owner")))

    with pytest.raises(PermissionError):
        pu._open_daemon_log(str(log_path))


def test_daemon_log_appends_bytes_verbatim(tmp_path):
    """Binary append without newline translation, on every platform."""
    log_path = tmp_path / "nested" / "daemon.log"
    with pu._open_daemon_log(str(log_path)) as fh:
        fh.write(b"one\n")
    with pu._open_daemon_log(str(log_path)) as fh:
        assert fh.name == str(log_path)
        fh.write(b"two\n")
    assert log_path.read_bytes() == b"one\ntwo\n"
