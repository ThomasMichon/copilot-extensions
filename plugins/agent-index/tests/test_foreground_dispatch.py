"""agent-index-server-venv-split: cmd_start/cmd_cell_start FOREGROUND
dispatch retarget to the sibling SERVER venv (Plan item 2).

Unlike ``spawn_passive``'s existing detached background spawn, a foreground
``start``/``serve``/``__cell-start`` invocation must inherit this process's
own stdio and let the terminal's own Ctrl+C/Ctrl+Break reach the dispatched
child directly (no new process group/session), while still explicitly
relaying a SIGTERM sent only to this process. These tests pin that contract
without spawning a real child process.
"""

from __future__ import annotations

import os
import signal
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_index import __main__ as cli
from agent_index import config


def _make_executable(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    if os.name != "nt":
        path.chmod(path.stat().st_mode | stat.S_IEXEC)


class _FakeProcess:
    def __init__(self, returncode: int = 0, raise_first_wait: bool = False):
        self.pid = 4242
        self.terminated = False
        self.returncode = returncode
        self._raise_first_wait = raise_first_wait
        self.wait_calls = 0

    def wait(self):
        self.wait_calls += 1
        if self._raise_first_wait and self.wait_calls == 1:
            raise KeyboardInterrupt
        return self.returncode

    def terminate(self):
        self.terminated = True


@pytest.fixture(autouse=True)
def _clean_server_venv_env(monkeypatch):
    monkeypatch.delenv(config.SERVER_VENV_PYTHON_ENV, raising=False)


def _args(**overrides):
    base = {"host": None, "port": None, "passive": False, "command": "start"}
    base.update(overrides)
    return SimpleNamespace(**base)


def test_cmd_start_runs_in_process_unchanged_with_no_server_venv(monkeypatch):
    """No sibling venv provisioned -- byte-identical to pre-item-2 behavior:
    serve() runs in-process, nothing is spawned."""
    calls = []
    monkeypatch.setattr(
        cli,
        "serve",
        lambda cfg, *, passive=False: calls.append((cfg.host, cfg.port, passive)),
    )
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("must not spawn when no server venv exists"),
    )

    assert cli.cmd_start(_args()) == 0
    assert calls == [("127.0.0.1", 0, False)]


def test_cmd_start_dispatches_to_the_server_venv_when_provisioned(monkeypatch, tmp_path):
    server_python = tmp_path / "server-venv" / "python3"
    _make_executable(server_python)
    monkeypatch.setenv(config.SERVER_VENV_PYTHON_ENV, str(server_python))
    monkeypatch.setattr(
        cli, "serve", lambda *a, **k: pytest.fail("must not run in-process")
    )

    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProcess(returncode=7)

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)

    assert cli.cmd_start(_args()) == 7
    cmd = captured["cmd"]
    assert cmd[0] == str(server_python)
    assert cmd[1:6] == ["-I", "-X", "utf8", "-m", "agent_index"]
    assert cmd[6] == "start"
    assert cmd[7:] == ["--host", "127.0.0.1", "--port", "0"]

    # Foreground dispatch inherits stdio and the parent's own process
    # group/session (unlike spawn_passive's detached background spawn) so
    # the terminal's own Ctrl+C/Ctrl+Break fan-out reaches the child
    # directly -- no redirection or creation-flag kwargs at all.
    kwargs = captured["kwargs"]
    assert "stdin" not in kwargs
    assert "stdout" not in kwargs
    assert "stderr" not in kwargs
    assert "creationflags" not in kwargs
    assert "start_new_session" not in kwargs


def test_cmd_start_uses_the_actually_invoked_subcommand_and_passive_flag(
    monkeypatch, tmp_path
):
    server_python = tmp_path / "server-venv" / "python3"
    _make_executable(server_python)
    monkeypatch.setenv(config.SERVER_VENV_PYTHON_ENV, str(server_python))

    captured: dict = {}

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess(0)

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)

    cli.cmd_start(_args(command="__cell-start", passive=True))
    assert "__cell-start" in captured["cmd"]
    assert captured["cmd"][-1] == "--passive"


def test_cmd_cell_start_dispatches_after_its_own_authority_check_passes(
    monkeypatch, tmp_path
):
    server_python = tmp_path / "server-venv" / "python3"
    _make_executable(server_python)
    monkeypatch.setenv(config.SERVER_VENV_PYTHON_ENV, str(server_python))
    captured: dict = {}
    monkeypatch.setattr(cli, "_validate_cell_start_authority", lambda **_k: None)

    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        return _FakeProcess(3)

    monkeypatch.setattr(cli.subprocess, "Popen", fake_popen)

    assert cli.cmd_cell_start(_args(command="__cell-start")) == 3
    assert "__cell-start" in captured["cmd"]


def test_cmd_cell_start_never_dispatches_when_authority_check_fails(
    monkeypatch, tmp_path
):
    server_python = tmp_path / "server-venv" / "python3"
    _make_executable(server_python)
    monkeypatch.setenv(config.SERVER_VENV_PYTHON_ENV, str(server_python))

    def _deny(**_kwargs):
        raise cli.ServiceOwnershipError("denied")

    monkeypatch.setattr(cli, "_validate_cell_start_authority", _deny)
    monkeypatch.setattr(
        cli.subprocess,
        "Popen",
        lambda *a, **k: pytest.fail("must never spawn when authority check fails"),
    )

    assert cli.cmd_cell_start(_args(command="__cell-start")) == 2


def test_cmd_start_swallows_keyboard_interrupt_and_keeps_waiting_for_the_child(
    monkeypatch, tmp_path
):
    """A terminal Ctrl+C raises KeyboardInterrupt in this process too (same
    inherited console/process group as the child) -- it must not race the
    child's own graceful shutdown with a second signal or an early exit."""
    server_python = tmp_path / "server-venv" / "python3"
    _make_executable(server_python)
    monkeypatch.setenv(config.SERVER_VENV_PYTHON_ENV, str(server_python))
    fake = _FakeProcess(returncode=0, raise_first_wait=True)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: fake)

    assert cli.cmd_start(_args()) == 0
    assert fake.wait_calls == 2


def test_cmd_start_forwards_sigterm_to_the_dispatched_child(monkeypatch, tmp_path):
    """Unlike a terminal signal, a supervisor-issued SIGTERM targets only
    this process -- it must be explicitly relayed to the child."""
    server_python = tmp_path / "server-venv" / "python3"
    _make_executable(server_python)
    monkeypatch.setenv(config.SERVER_VENV_PYTHON_ENV, str(server_python))
    fake = _FakeProcess(returncode=0)
    monkeypatch.setattr(cli.subprocess, "Popen", lambda *a, **k: fake)

    registered: dict = {}

    def fake_signal(signum, handler):
        if signum == signal.SIGTERM and "handler" not in registered:
            registered["handler"] = handler
        return signal.SIG_DFL

    monkeypatch.setattr(cli.signal, "signal", fake_signal)

    assert cli.cmd_start(_args()) == 0
    assert "handler" in registered
    registered["handler"](signal.SIGTERM, None)
    assert fake.terminated is True
