"""Transient dev-tunnel failures are retried with backoff in the shared layer
(vendored ``ssh_manager``) and at the agent-codespaces call sites that use it."""

from __future__ import annotations

import asyncio
import subprocess
import types

import pytest
import ssh_manager
from ssh_manager import codespace_source, manager as sm


def _result(code, stderr="", timed_out=False, stdout=""):
    return sm.CommandResult(exit_code=code, stdout=stdout, stderr=stderr, timed_out=timed_out)


@pytest.mark.parametrize("result, transient", [
    (_result(255, "ssh: connect to host ... port 22"), True),
    (_result(1, "read tcp ...: wsarecv: An existing connection was forcibly closed by the remote host."), True),
    (_result(1, 'rpc error: code = Unavailable desc = connection error: desc = "error reading server preface"'), True),
    (_result(1, "error getting tunnel client"), True),
    (_result(0, "", timed_out=True), True),
    (_result(1, "fatal: not a git repository"), False),
    (_result(3, "STILL_RUNNING"), False),
    (_result(0, ""), False),
])
def test_transient_classifier(result, transient):
    assert sm.is_transient_ssh_failure(result) is transient


class _FakeManager:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def exec_command(self, host, command, **kwargs):
        self.calls.append((host, command, kwargs))
        return self.results.pop(0)


@pytest.fixture
def no_sleep(monkeypatch):
    slept = []

    async def _sleep(s):
        slept.append(s)

    monkeypatch.setattr(sm.asyncio, "sleep", _sleep)
    return slept


def test_exec_with_retry_rides_out_a_reset_with_backoff(no_sleep):
    mgr = _FakeManager([_result(255, "reset"), _result(1, "forcibly closed"), _result(0, stdout="ok")])
    got = asyncio.run(ssh_manager.exec_with_retry(mgr, "cs-1", "true", input_bytes=b"x"))
    assert got.exit_code == 0 and got.stdout == "ok"
    assert no_sleep == [2.0, 4.0]
    assert all(call[2]["input_bytes"] == b"x" for call in mgr.calls)


def test_exec_with_retry_never_reruns_a_genuine_failure(no_sleep):
    mgr = _FakeManager([_result(2, "no such file")])
    got = asyncio.run(ssh_manager.exec_with_retry(mgr, "cs-1", "cat x"))
    assert got.exit_code == 2 and len(mgr.calls) == 1 and no_sleep == []


def test_exec_with_retry_gives_up_after_its_attempts(no_sleep):
    mgr = _FakeManager([_result(255)] * 3)
    got = asyncio.run(ssh_manager.exec_with_retry(mgr, "cs-1", "true", attempts=3))
    assert got.exit_code == 255 and len(mgr.calls) == 3


def test_config_fetch_retries_a_tunnel_reset_but_not_a_real_gh_error(monkeypatch):
    runs = []

    def fake_run(args, **kw):
        runs.append(args)
        if len(runs) == 1:
            return subprocess.CompletedProcess(args, 1, "", "wsarecv: An existing connection was forcibly closed")
        return subprocess.CompletedProcess(args, 0, "Host cs-1\n  User codespace\n", "")

    monkeypatch.setattr(codespace_source.subprocess, "run", fake_run)
    monkeypatch.setattr(codespace_source.time, "sleep", lambda s: None)
    src = codespace_source.CodespaceConfigSource("cs-1")
    assert "Host cs-1" in src._fetch_gh_config() and len(runs) == 2

    runs.clear()
    monkeypatch.setattr(
        codespace_source.subprocess, "run",
        lambda args, **kw: runs.append(args) or subprocess.CompletedProcess(args, 1, "", "HTTP 404: codespace not found"),
    )
    with pytest.raises(RuntimeError, match="404"):
        src._fetch_gh_config()
    assert len(runs) == 1


def test_control_master_connect_retries_a_transient_connection_error(no_sleep):
    cm = sm.ConnectionManager.__new__(sm.ConnectionManager)
    attempts = []

    async def start(config, socket, forwards, *, env=None):
        attempts.append(env)
        if len(attempts) < 2:
            raise ConnectionError("ControlMaster failed to establish: forcibly closed")
        return "proc"

    cm._start_control_master = start
    config = types.SimpleNamespace(ssh_target="cs-1")
    got = asyncio.run(cm._connect_with_retry(config, "sock", [], env={"GH_TOKEN": "t"}))
    assert got == "proc" and attempts == [{"GH_TOKEN": "t"}] * 2 and no_sleep == [2.0]


def test_ssh_keepalive_tolerates_a_brief_stall():
    cm = sm.ConnectionManager.__new__(sm.ConnectionManager)
    args = cm._base_ssh_args(sm.SSHConfig(host_alias="cs-1"))
    assert "ServerAliveCountMax=6" in args and "TCPKeepAlive=yes" in args
