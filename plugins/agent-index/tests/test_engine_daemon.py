"""Tests for the durable engine daemon manager (engine/daemon.py) + CLI."""

from __future__ import annotations

import os

import pytest

from agent_index.engine import daemon


class FakeProc:
    def __init__(self, alive: bool = True, returncode: int | None = None):
        self.pid = 5150
        self._alive = alive
        self.returncode = returncode

    def poll(self):
        return None if self._alive else self.returncode


# -- path / config resolution -----------------------------------------------


def test_engine_home_default_and_override(monkeypatch, tmp_path):
    monkeypatch.delenv("AGENT_INDEX_ENGINE_HOME", raising=False)
    assert daemon.engine_home().name == "engine"
    monkeypatch.setenv("AGENT_INDEX_ENGINE_HOME", str(tmp_path))
    assert daemon.engine_home() == tmp_path


def test_engine_venv_python_layout(tmp_path):
    py = daemon.engine_venv_python(tmp_path)
    assert py.parent.parent == tmp_path / ".venv"
    assert py.name in ("python", "python.exe")


def test_engine_endpoint_env(monkeypatch):
    monkeypatch.setenv("AGENT_INDEX_ENGINE_HOST", "10.1.2.3")
    monkeypatch.setenv("AGENT_INDEX_ENGINE_PORT", "9001")
    assert daemon.engine_endpoint() == ("10.1.2.3", 9001)


def test_engine_command_targets_durable_venv(tmp_path):
    cmd = daemon.engine_command(tmp_path)
    assert cmd[1:] == ["-m", "agent_index_engine.app", "--host",
                        daemon.engine_endpoint()[0], "--port", str(daemon.engine_endpoint()[1])]
    assert str(tmp_path) in cmd[0]


def test_engine_command_prefers_pythonw_to_avoid_console_reexec_window(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(daemon.os, "name", "nt")
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"")
    (scripts / "pythonw.exe").write_bytes(b"")

    cmd = daemon.engine_command(tmp_path)

    # The console python.exe launcher re-execs the base interpreter as a
    # visible-console child even under CREATE_NO_WINDOW; pythonw.exe never
    # allocates a console at all, and this single long-lived server has no
    # recurring console descendants that would need a console interpreter.
    assert cmd[0] == str(scripts / "pythonw.exe")


def test_engine_command_falls_back_to_console_python_without_pythonw_sibling(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(daemon.os, "name", "nt")
    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "python.exe").write_bytes(b"")

    cmd = daemon.engine_command(tmp_path)

    assert cmd[0] == str(scripts / "python.exe")


# -- health ------------------------------------------------------------------


def test_is_healthy_true(monkeypatch):
    class R:
        status_code = 200

    monkeypatch.setattr("httpx.get", lambda *a, **k: R())
    assert daemon.is_healthy("127.0.0.1", 8421) is True


def test_is_healthy_false_on_error(monkeypatch):
    import httpx

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("httpx.get", boom)
    assert daemon.is_healthy("127.0.0.1", 8421) is False


def test_health_reports_unreachable_details(monkeypatch):
    import httpx

    def boom(*_a, **_k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("httpx.get", boom)
    assert daemon.health("127.0.0.1", 8421) == {
        "status": "unreachable",
        "generation": None,
        "gpu_deps_installed": False,
        "model_loaded": False,
        "model_name": None,
        "device": None,
        "cuda_available": None,
        "python_executable": None,
        "detail": "Engine not reachable at http://127.0.0.1:8421",
    }


# -- start / stop / status ---------------------------------------------------


def test_start_noop_when_already_healthy(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "is_healthy", lambda *a, **k: True)
    out = daemon.start(tmp_path)
    assert "already running" in out


def test_start_raises_when_venv_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(daemon, "is_healthy", lambda *a, **k: False)
    with pytest.raises(FileNotFoundError, match="durable engine venv"):
        daemon.start(tmp_path)


def _prepare_venv(tmp_path):
    py = daemon.engine_venv_python(tmp_path)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="ascii")
    return py


def test_start_default_wait_timeout_is_300_seconds(monkeypatch, tmp_path):
    """A cold torch + embedding-model import (before uvicorn even starts
    listening) has been observed to take upwards of two minutes -- the
    default wait must be generous enough to tolerate that, not the old 90s
    that gave up while the engine was still loading."""
    monkeypatch.delenv("AGENT_INDEX_ENGINE_START_TIMEOUT", raising=False)
    _prepare_venv(tmp_path)
    monkeypatch.setattr(daemon, "is_healthy", lambda *a, **k: False)
    monkeypatch.setattr(daemon, "_spawn", lambda cmd, *, python, home: FakeProc(alive=True))
    monkeypatch.setattr(daemon, "_write_pid", lambda *a, **k: None)

    clock = {"t": 0.0}
    monkeypatch.setattr(daemon.time, "monotonic", lambda: clock["t"])

    def advancing_sleep(_seconds):
        clock["t"] += 1.0

    monkeypatch.setattr(daemon.time, "sleep", advancing_sleep)

    with pytest.raises(TimeoutError, match="within 300s"):
        daemon.start(tmp_path)
    assert clock["t"] >= 300.0


def test_start_wait_timeout_is_overridable_via_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENT_INDEX_ENGINE_START_TIMEOUT", "5")
    _prepare_venv(tmp_path)
    monkeypatch.setattr(daemon, "is_healthy", lambda *a, **k: False)
    monkeypatch.setattr(daemon, "_spawn", lambda cmd, *, python, home: FakeProc(alive=True))
    monkeypatch.setattr(daemon, "_write_pid", lambda *a, **k: None)

    clock = {"t": 0.0}
    monkeypatch.setattr(daemon.time, "monotonic", lambda: clock["t"])

    def advancing_sleep(_seconds):
        clock["t"] += 1.0

    monkeypatch.setattr(daemon.time, "sleep", advancing_sleep)

    with pytest.raises(TimeoutError, match="within 5s"):
        daemon.start(tmp_path)
    assert 5.0 <= clock["t"] < 300.0


def test_start_spawns_and_waits_for_health(monkeypatch, tmp_path):
    # Make the durable venv python "exist".
    py = daemon.engine_venv_python(tmp_path)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="ascii")

    calls = {"n": 0}

    def health(*_a, **_k):
        calls["n"] += 1
        return calls["n"] >= 2  # unhealthy at the guard, healthy while waiting

    monkeypatch.setattr(daemon, "is_healthy", health)
    monkeypatch.setattr(daemon, "_spawn", lambda cmd, *, python, home: FakeProc())
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: None)

    out = daemon.start(tmp_path)
    assert "started" in out and "5150" in out
    assert daemon._read_pid(tmp_path) == 5150


def test_spawn_launches_through_spawn_daemon(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.delenv(daemon.ENGINE_LOG_LEVEL_ENV, raising=False)
    monkeypatch.setattr(
        daemon,
        "spawn_daemon",
        lambda cmd, **kwargs: captured.update(cmd=cmd, kwargs=kwargs) or FakeProc(),
    )

    daemon._spawn(
        ["pythonw.exe", "-m", "agent_index_engine.app"], python="python.exe", home=tmp_path
    )

    kwargs = captured["kwargs"]
    # Launched through the log-level bootstrap, not `-m` directly.
    assert captured["cmd"][:2] == ["pythonw.exe", "-c"]
    # Never the caller's ambient cwd -- this is a long-lived, detached
    # daemon rooted at its own durable engine home instead.
    assert kwargs["cwd"] == tmp_path
    assert kwargs["log_path"] == tmp_path / "logs" / "engine-stdio.log"
    assert kwargs["console_children"] is True
    # It used to start inside a caller's Job; that stays the fallback.
    assert kwargs["contained_fallback"] is True
    # One stdio log line per embedding request would grow without bound.
    assert kwargs["env"][daemon.ENGINE_LOG_LEVEL_ENV] == "warning"


def test_spawn_keeps_an_explicit_engine_log_level(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setenv(daemon.ENGINE_LOG_LEVEL_ENV, "debug")
    monkeypatch.setattr(
        daemon, "spawn_daemon", lambda cmd, **kwargs: captured.update(kwargs) or FakeProc()
    )
    daemon._spawn(["pythonw.exe"], python="python.exe", home=tmp_path)
    assert captured["env"][daemon.ENGINE_LOG_LEVEL_ENV] == "debug"


_OLD_ENGINE_APP = """\
import sys

def main():
    import uvicorn
    # An engine app from before AGENT_INDEX_ENGINE_LOG_LEVEL: hard-coded info.
    uvicorn.run("app", host=sys.argv[sys.argv.index("--host") + 1], log_level="info")

if __name__ == "__main__":
    main()
"""
_FAKE_UVICORN = """\
import sys

def run(app, **kwargs):
    print("LEVEL=" + kwargs["log_level"])
    print("ARGS=" + " ".join(sys.argv[1:]))
"""


@pytest.mark.parametrize(("level", "expected"), [("warning", "warning"), (None, "info")])
def test_bootstrap_applies_the_log_level_to_a_preserved_older_engine(
    monkeypatch, tmp_path, level, expected
):
    """The durable engine venv survives service updates, so its app may hard-code
    ``info``; the launch bootstrap still applies the level without a rebuild."""
    import subprocess
    import sys

    pkg = tmp_path / "agent_index_engine"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "app.py").write_text(_OLD_ENGINE_APP, encoding="utf-8")
    (tmp_path / "uvicorn.py").write_text(_FAKE_UVICORN, encoding="utf-8")
    cmd = daemon._with_log_level_bootstrap(
        [sys.executable, "-m", "agent_index_engine.app", "--host", "127.0.0.1", "--port", "1"]
    )
    assert cmd[1] == "-c"
    env = {**os.environ, "PYTHONPATH": str(tmp_path)}
    env.pop(daemon.ENGINE_LOG_LEVEL_ENV, None)
    if level:
        env[daemon.ENGINE_LOG_LEVEL_ENV] = level
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=60)
    assert result.returncode == 0, result.stderr
    assert f"LEVEL={expected}" in result.stdout
    assert "ARGS=--host 127.0.0.1 --port 1" in result.stdout


def test_start_raises_on_early_exit(monkeypatch, tmp_path):
    py = daemon.engine_venv_python(tmp_path)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="ascii")
    monkeypatch.setattr(daemon, "is_healthy", lambda *a, **k: False)
    monkeypatch.setattr(
        daemon, "_spawn", lambda cmd, *, python, home: FakeProc(alive=False, returncode=1)
    )
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: None)
    with pytest.raises(RuntimeError, match="exited early"):
        daemon.start(tmp_path)


def test_stop_not_running(monkeypatch, tmp_path):
    assert daemon.stop(tmp_path) == "not running"


def test_stop_terminates(monkeypatch, tmp_path):
    daemon._write_pid(4242, tmp_path)
    monkeypatch.setattr(daemon, "_pid_alive", lambda _p: True)
    killed = {}
    monkeypatch.setattr(daemon.subprocess, "run",
                        lambda *a, **k: killed.update({"ran": True}))
    monkeypatch.setattr(daemon.os, "kill", lambda _pid, _sig: killed.update({"killed": True}))
    out = daemon.stop(tmp_path)
    assert "stopped" in out and "4242" in out
    assert not daemon._pid_file(tmp_path).exists()


def test_status_shape(monkeypatch, tmp_path):
    monkeypatch.setattr(
        daemon,
        "health",
        lambda *a, **k: {
            "status": "ok",
            "generation": "engine-v1",
            "gpu_deps_installed": True,
            "model_loaded": False,
            "model_name": "example-model",
            "device": "cpu",
            "cuda_available": False,
            "python_executable": "python",
            "detail": None,
        },
    )
    st = daemon.status(tmp_path)
    assert set(st) == {
        "healthy", "host", "port", "pid", "pid_alive",
        "engine_home", "venv_python", "provisioned", "generation",
        "observed_generation", "gpu_deps_installed", "model_loaded",
        "cuda_available", "python_executable", "detail",
    }
    assert st["provisioned"] is False
    assert st["healthy"] is True
    assert st["observed_generation"] == "engine-v1"


# -- CLI ---------------------------------------------------------------------


def test_cli_engine_status(monkeypatch, capsys):
    from agent_index.__main__ import main

    monkeypatch.setattr(daemon, "is_healthy", lambda *a, **k: False)
    rc = main(["engine", "status"])
    assert rc == 0
    assert "provisioned" in capsys.readouterr().out


def test_cli_engine_start_failure(monkeypatch, capsys):
    from agent_index.__main__ import main

    def boom(*_a, **_k):
        raise FileNotFoundError("durable engine venv not found")

    monkeypatch.setattr(daemon, "start", boom)
    rc = main(["engine", "start"])
    assert rc == 1
    assert "FAIL" in capsys.readouterr().err
