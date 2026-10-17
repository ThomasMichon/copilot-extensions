"""Monitor launcher/runtime PID identity without starting resident monitors."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
import agent_procutil as interpreter

from agent_worktrees import status_monitor_cutover as cutover
from agent_worktrees import status_monitor_python
from agent_worktrees import status_monitor_runtime as runtime


def _windows_venv(tmp_path, monkeypatch):
    scripts = tmp_path / "venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts.parent / "pyvenv.cfg").write_text("home = synthetic-base\n", encoding="utf-8")
    monkeypatch.setattr(interpreter, "_is_windows", lambda: True)
    return str(scripts / "python.exe")


def test_monitor_direct_interpreter_preserves_venv_and_console_root(tmp_path, monkeypatch):
    executable = _windows_venv(tmp_path, monkeypatch)
    base = tmp_path / "base"
    base.mkdir()
    console = base / "python.exe"
    console.touch()
    calls = []

    def select(source):
        calls.append(source)
        return str(base / "pythonw.exe")

    def environment(source):
        calls.append(source)
        return {"__PYVENV_LAUNCHER__": source}

    monkeypatch.setattr(interpreter, "windowless_python", select)
    monkeypatch.setattr(interpreter, "windowless_python_env", environment)
    assert interpreter.direct_python(executable) == (
        str(console), {"__PYVENV_LAUNCHER__": executable},
    )
    assert calls == [executable, executable]


@pytest.mark.parametrize("missing", ["windowless", "console"])
def test_monitor_direct_interpreter_rejects_unavailable_bypass(tmp_path, monkeypatch, missing):
    executable = _windows_venv(tmp_path, monkeypatch)
    monkeypatch.setattr(
        interpreter, "windowless_python", lambda _: str(tmp_path / "pythonw.exe"),
    )
    monkeypatch.setattr(
        interpreter, "windowless_python_env",
        lambda _: {} if missing == "windowless" else {"__PYVENV_LAUNCHER__": executable},
    )
    with pytest.raises(RuntimeError, match="unavailable"):
        interpreter.direct_python(executable)


def test_monitor_direct_interpreter_preserves_nonvenv_windows_console(tmp_path, monkeypatch):
    monkeypatch.setattr(interpreter, "_is_windows", lambda: True)
    executable = str(tmp_path / "python.exe")
    monkeypatch.setattr(interpreter, "windowless_python", lambda source: source)
    monkeypatch.setattr(interpreter, "windowless_python_env", lambda _: {})
    assert interpreter.direct_python(executable) == (executable, {})


def test_monitor_direct_interpreter_preserves_posix(monkeypatch):
    monkeypatch.setattr(interpreter, "_is_windows", lambda: False)
    monkeypatch.setattr(
        interpreter, "windowless_python",
        lambda _: pytest.fail("ordinary POSIX selection must not change"),
    )
    assert interpreter.direct_python("synthetic-python") == ("synthetic-python", {})


def test_monitor_uses_the_shared_direct_interpreter():
    assert status_monitor_python.direct_monitor_python is interpreter.direct_python


@pytest.mark.parametrize("passive", [False, True])
def test_monitor_spawn_preserves_direct_interpreter_environment(tmp_path, monkeypatch, passive):
    captured = {}
    monkeypatch.setattr(cutover, "os", SimpleNamespace(name="nt", path=os.path))
    monkeypatch.setattr(runtime, "_daemon_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(
        runtime, "_daemon_environment", lambda: {"KEEP": "yes", "__PYVENV_LAUNCHER__": "old"},
    )
    monkeypatch.setattr(cutover, "routing_dir", lambda: tmp_path / "routing")
    monkeypatch.setattr(
        cutover if passive else runtime, "direct_monitor_python",
        lambda source: ("base-python", {"__PYVENV_LAUNCHER__": source}),
    )

    def spawn_daemon(argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)
        return SimpleNamespace(pid=77)

    monkeypatch.setattr(cutover, "spawn_daemon", spawn_daemon)
    if passive:
        assert cutover.spawn_passive("venv-python", port=54321).pid == 77
        assert captured["argv"][4:] == ["--passive", "--control-port", "54321"]
    else:
        assert runtime._spawn_detached(["venv-python", "-m", "agent_worktrees", "status-monitor"])
    assert captured["argv"][0] == "base-python"
    kwargs = captured["kwargs"]
    assert kwargs["env"]["KEEP"] == "yes"
    assert kwargs["env"]["__PYVENV_LAUNCHER__"] == "venv-python"
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["log_path"] == tmp_path / "logs" / "status-monitor.log"
    # Console children share one hidden console; breakaway from the caller's
    # Job is required (never a contained fallback).
    assert kwargs["console_children"] is True
    assert kwargs.get("breakaway", True) is True
    assert not kwargs.get("contained_fallback", False)


def test_posix_passive_monitor_is_a_detached_daemon(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr(cutover, "os", SimpleNamespace(name="posix", path=os.path))
    monkeypatch.setattr(runtime, "_daemon_cwd", lambda: str(tmp_path))
    monkeypatch.setattr(runtime, "_daemon_environment", lambda: {"KEEP": "yes"})
    monkeypatch.setattr(cutover, "routing_dir", lambda: tmp_path / "routing")
    monkeypatch.setattr(
        cutover, "spawn_daemon", lambda argv, **kwargs: captured.update(kwargs) or "proc",
    )
    assert cutover.spawn_passive(sys.executable, port=54321) == "proc"
    assert captured["console_children"] is False
    assert captured["log_path"] == tmp_path / "logs" / "status-monitor.log"


def test_monitor_log_never_lands_in_a_shared_temp_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(cutover.os.path, "expanduser", lambda _p: str(tmp_path))
    path = cutover.monitor_log_path(str(tmp_path))
    assert path.parent == tmp_path
    assert path.name == ".agent-worktrees-status-monitor.log"


@pytest.mark.parametrize("passive", [False, True])
def test_monitor_spawn_rejects_unsupported_interpreter(monkeypatch, caplog, passive):
    monkeypatch.setattr(cutover, "os", SimpleNamespace(name="nt"))

    def reject(_):
        raise RuntimeError("unsupported direct interpreter")

    monkeypatch.setattr(cutover if passive else runtime, "direct_monitor_python", reject)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *_a, **_k: pytest.fail("must not spawn a redirector"),
    )
    if passive:
        with pytest.raises(RuntimeError, match="unsupported direct interpreter"):
            cutover.spawn_passive("venv-python", port=54321)
    else:
        assert not runtime._spawn_detached(["venv-python", "-m", "agent_worktrees", "status-monitor"])
        assert "unsupported direct interpreter" in caplog.text


@pytest.mark.skipif(
    os.name != "nt" or sys.prefix == sys.base_prefix,
    reason="requires an owner-contained Windows test venv",
)
@pytest.mark.parametrize("passive", [False, True])
def test_monitor_real_windows_venv_pid_prefix_imports_and_generation(tmp_path, monkeypatch, passive):
    """Exercise each real spawn boundary with a bounded harmless control probe."""
    result = tmp_path / "runtime.json"
    stop = tmp_path / "stop"
    route = tmp_path / "routing"
    monkeypatch.setattr(cutover, "routing_dir", lambda: route)
    monkeypatch.setattr(runtime, "_daemon_cwd", lambda: str(tmp_path))
    env = runtime._daemon_environment()
    env["MONITOR_PROBE_RESULT"] = str(result)
    env["MONITOR_PROBE_STOP"] = str(stop)
    monkeypatch.setattr(runtime, "_daemon_environment", lambda: dict(env))
    real_popen = subprocess.Popen
    owned = []
    code = """
import json, os, sys, time
from pathlib import Path
from agent_worktrees.status_monitor_cutover import ControlServer
server = ControlServer(lambda action, payload: {'pid': os.getpid()}, token='probe-token')
server.start()
try:
    Path(os.environ['MONITOR_PROBE_RESULT']).write_text(
        json.dumps({'pid': os.getpid(), 'prefix': sys.prefix, 'port': server.port}),
        encoding='utf-8')
    deadline = time.monotonic() + 10
    while not Path(os.environ['MONITOR_PROBE_STOP']).exists() and time.monotonic() < deadline:
        time.sleep(0.02)
finally:
    server.close()
"""

    def probe(argv, **kwargs):
        process = real_popen([argv[0], "-c", code], **kwargs)
        owned.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", probe)
    try:
        if passive:
            cutover.spawn_passive(sys.executable, port=54321)
        else:
            assert runtime._spawn_detached([sys.executable, "-m", "agent_worktrees", "status-monitor"])
        process = owned[0]
        deadline = time.monotonic() + 8
        while not result.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        log_path = tmp_path / "logs" / "status-monitor.log"
        assert result.exists(), (
            f"bounded probe failed: exit={process.poll()}; "
            + (log_path.read_text(encoding="utf-8") if log_path.exists() else "no log")
        )
        data = json.loads(result.read_text(encoding="utf-8"))
        assert data["pid"] == process.pid
        assert os.path.normcase(data["prefix"]) == os.path.normcase(sys.prefix)
        assert cutover.ControlClient(
            f"http://127.0.0.1:{data['port']}", token="probe-token",
        ).health()["pid"] == process.pid
        endpoint = cutover.publish_route(data["port"], pid=process.pid, version="probe")
        assert cutover.active_generation_for_pid(data["pid"]) == endpoint.generation
    finally:
        stop.touch()
        for process in owned:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
