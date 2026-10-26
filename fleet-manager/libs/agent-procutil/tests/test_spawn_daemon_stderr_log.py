"""spawn_daemon can keep a daemon's stderr in its own log.

Some daemons are diagnosed from a separate stderr log, so the shared launch
primitive must be able to keep that split without giving up any of its
guarantees: the path is validated like ``log_path``, the file is private on
POSIX, the parent never keeps it open, and every launch and breakaway note is
recorded in it.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from unittest.mock import Mock

import pytest

import agent_procutil as pu


class _Popen:
    def __init__(self, failures=()):
        self.calls: list = []
        self.failures = list(failures)

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), dict(kwargs)))
        if self.failures:
            raise self.failures.pop(0)
        proc = Mock()
        proc.pid = 4242
        return proc


@pytest.fixture()
def fake_popen(monkeypatch, declared_mock_daemon):
    from agent_procutil import daemon, daemon_broker

    fake = _Popen()
    monkeypatch.setattr(daemon_broker, "caller_in_job", lambda: False)
    monkeypatch.setattr(pu.subprocess, "Popen", fake)
    monkeypatch.setattr(
        daemon, "spawn_brokered_daemon", Mock(side_effect=pu.DaemonSpawnError(
            "fixture", process_created=False, admission_sent=False,
        )),
    )
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    return fake


def _access_denied() -> OSError:
    exc = OSError(13, "Access is denied")
    exc.winerror = 5
    return exc


def test_stderr_goes_to_its_own_log(tmp_path, fake_popen):
    out_log = tmp_path / "svc.log"
    err_log = tmp_path / "errs" / "svc-err.log"

    pu.spawn_daemon(["py"], cwd=tmp_path, log_path=out_log, stderr_log_path=err_log)

    (_, kwargs), = fake_popen.calls
    assert kwargs["stdout"].name == str(out_log)
    assert kwargs["stderr"] is not subprocess.STDOUT
    assert kwargs["stderr"].name == str(err_log)
    assert kwargs["stdout"].closed and kwargs["stderr"].closed, (
        "the parent must not keep either daemon log open"
    )
    for path in (out_log, err_log):
        assert "spawn_daemon cwd=" in path.read_text(encoding="utf-8")


def test_without_stderr_log_stderr_is_merged(tmp_path, fake_popen):
    pu.spawn_daemon(["py"], cwd=tmp_path, log_path=tmp_path / "svc.log")
    (_, kwargs), = fake_popen.calls
    assert kwargs["stderr"] is subprocess.STDOUT


def test_stderr_log_equal_to_log_path_merges(tmp_path, fake_popen):
    log = tmp_path / "svc.log"
    pu.spawn_daemon(["py"], cwd=tmp_path, log_path=log, stderr_log_path=str(log))
    (_, kwargs), = fake_popen.calls
    assert kwargs["stderr"] is subprocess.STDOUT
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


def test_relative_stderr_log_is_rejected(tmp_path, fake_popen, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="stderr_log_path must be an absolute path"):
        pu.spawn_daemon(
            ["py"], cwd=tmp_path, log_path=tmp_path / "svc.log", stderr_log_path="err.log"
        )
    assert fake_popen.calls == []
    assert not (tmp_path / "err.log").exists()
    assert not (tmp_path / "svc.log").exists(), "nothing is opened before validation"


def test_payload_stderr_log_is_rejected(tmp_path, fake_popen):
    payload = tmp_path / "installed-plugins" / "market" / "plugin"
    payload.mkdir(parents=True)
    with pytest.raises(ValueError, match="stderr_log_path is inside a plugin payload"):
        pu.spawn_daemon(
            ["py"], cwd=tmp_path, log_path=tmp_path / "svc.log",
            stderr_log_path=payload / "err.log",
        )
    assert fake_popen.calls == []


def test_unopenable_stderr_log_closes_the_stdout_log(tmp_path, fake_popen, monkeypatch):
    opened = []
    real_open = pu._open_daemon_log

    def open_log(path):
        if path.endswith("err.log"):
            raise PermissionError(13, "denied")
        fh = real_open(path)
        opened.append(fh)
        return fh

    monkeypatch.setattr(pu, "_open_daemon_log", open_log)
    with pytest.raises(PermissionError):
        pu.spawn_daemon(
            ["py"], cwd=tmp_path, log_path=tmp_path / "svc.log",
            stderr_log_path=tmp_path / "err.log",
        )
    assert fake_popen.calls == []
    assert opened and opened[0].closed


def test_breakaway_denial_is_recorded_in_both_logs(tmp_path, fake_popen, monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    fake_popen.failures.append(_access_denied())
    out_log, err_log = tmp_path / "svc.log", tmp_path / "svc-err.log"

    with pytest.raises(pu.DaemonSpawnError):
        pu.spawn_daemon(["py"], cwd=tmp_path, log_path=out_log, stderr_log_path=err_log)

    for path in (out_log, err_log):
        assert "daemon not started" in path.read_text(encoding="utf-8")


def test_contained_fallback_keeps_the_stderr_log(tmp_path, fake_popen, monkeypatch):
    monkeypatch.setattr(pu, "_is_windows", lambda: True)
    fake_popen.failures.append(_access_denied())
    err_log = tmp_path / "svc-err.log"

    pu.spawn_daemon(
        ["py"], cwd=tmp_path, log_path=tmp_path / "svc.log",
        stderr_log_path=err_log, contained_fallback=True,
    )

    _, (_, retry) = fake_popen.calls
    assert retry["stderr"].name == str(err_log)
    assert "starting the daemon inside it" in err_log.read_text(encoding="utf-8")


@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes do not apply on Windows")
def test_stderr_log_is_private_on_posix(tmp_path, monkeypatch, declared_mock_daemon):
    seen = []

    def popen(argv, **kwargs):
        seen.append(stat.S_IMODE(os.fstat(kwargs["stderr"].fileno()).st_mode))
        return Mock(pid=1)

    monkeypatch.setattr(pu.subprocess, "Popen", popen)
    err_log = tmp_path / "svc-err.log"
    err_log.write_bytes(b"")
    os.chmod(err_log, 0o644)

    pu.spawn_daemon(["py"], cwd=tmp_path, log_path=tmp_path / "svc.log", stderr_log_path=err_log)

    assert seen == [0o600]


def test_real_child_splits_stdout_and_stderr(tmp_path):
    out_log, err_log = tmp_path / "svc.log", tmp_path / "svc-err.log"
    proc = pu.spawn_daemon(
        [sys.executable, "-c", "import sys; print('to-out'); print('to-err', file=sys.stderr)"],
        cwd=tmp_path,
        log_path=out_log,
        stderr_log_path=err_log,
    )
    assert proc.wait(timeout=60) == 0
    out_text = out_log.read_text(encoding="utf-8", errors="replace")
    err_text = err_log.read_text(encoding="utf-8", errors="replace")
    assert "to-out" in out_text and "to-err" not in out_text
    assert "to-err" in err_text and "to-out" not in err_text
