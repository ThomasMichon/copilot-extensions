"""Tests for the durable engine daemon manager (engine/daemon.py) + CLI."""

from __future__ import annotations

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
    assert cmd[1:] == ["-m", "agent_index.engine.app", "--host",
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
    monkeypatch.setattr(daemon, "_spawn", lambda cmd: FakeProc())
    monkeypatch.setattr(daemon.time, "sleep", lambda _s: None)

    out = daemon.start(tmp_path)
    assert "started" in out and "5150" in out
    assert daemon._read_pid(tmp_path) == 5150


def test_spawn_uses_windowless_daemon_contract(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        daemon,
        "windowless_daemon_kwargs",
        lambda: {"creationflags": 0x08000000},
    )
    monkeypatch.setattr(
        daemon.subprocess,
        "Popen",
        lambda cmd, **kwargs: captured.update(cmd=cmd, kwargs=kwargs) or FakeProc(),
    )

    daemon._spawn(["pythonw.exe", "-m", "agent_index.engine.app"])

    assert captured["kwargs"]["creationflags"] == 0x08000000
    assert captured["kwargs"]["stdin"] is daemon.subprocess.DEVNULL
    assert captured["kwargs"]["stdout"] is daemon.subprocess.DEVNULL
    assert captured["kwargs"]["stderr"] is daemon.subprocess.DEVNULL


def test_start_raises_on_early_exit(monkeypatch, tmp_path):
    py = daemon.engine_venv_python(tmp_path)
    py.parent.mkdir(parents=True, exist_ok=True)
    py.write_text("", encoding="ascii")
    monkeypatch.setattr(daemon, "is_healthy", lambda *a, **k: False)
    monkeypatch.setattr(daemon, "_spawn", lambda cmd: FakeProc(alive=False, returncode=1))
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
