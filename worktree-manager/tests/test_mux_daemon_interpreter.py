"""Direct-interpreter contracts for mux daemon process ownership."""

from __future__ import annotations

import os
import json
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from worktree_manager import mux_daemon_cutover, mux_daemon_process


def _venv_python(tmp_path):
    root = tmp_path / "venv"
    scripts = root / "Scripts"
    scripts.mkdir(parents=True)
    (root / "pyvenv.cfg").write_text("home = synthetic-base\n", encoding="utf-8")
    return str(scripts / "python.exe")


def test_direct_interpreter_preserves_windows_venv_environment(tmp_path, monkeypatch):
    executable = _venv_python(tmp_path)
    monkeypatch.setattr(mux_daemon_process, "os", SimpleNamespace(name="nt", path=os.path))
    calls = []

    def python(source):
        calls.append(source)
        return "base-pythonw"

    def env(source):
        calls.append(source)
        return {"__PYVENV_LAUNCHER__": source}

    monkeypatch.setattr(mux_daemon_process, "windowless_python", python)
    monkeypatch.setattr(mux_daemon_process, "windowless_python_env", env)
    assert mux_daemon_process.direct_daemon_python(executable) == (
        "base-pythonw", {"__PYVENV_LAUNCHER__": executable},
    )
    assert calls == [executable, executable]


def test_windows_venv_without_direct_interpreter_is_rejected(tmp_path, monkeypatch):
    executable = _venv_python(tmp_path)
    monkeypatch.setattr(mux_daemon_process, "os", SimpleNamespace(name="nt", path=os.path))
    monkeypatch.setattr(mux_daemon_process, "windowless_python", lambda _: "venv-pythonw")
    monkeypatch.setattr(mux_daemon_process, "windowless_python_env", lambda _: {})
    with pytest.raises(RuntimeError, match="base pythonw.exe is unavailable"):
        mux_daemon_process.direct_daemon_python(executable)


def test_posix_interpreter_selection_stays_unchanged(monkeypatch):
    monkeypatch.setattr(mux_daemon_process, "os", SimpleNamespace(name="posix", path=os.path))
    monkeypatch.setattr(mux_daemon_process, "windowless_python", lambda source: source)
    monkeypatch.setattr(mux_daemon_process, "windowless_python_env", lambda _: {})
    assert mux_daemon_process.direct_daemon_python("/synthetic/bin/python") == (
        "/synthetic/bin/python", {},
    )


def test_ordinary_start_uses_direct_interpreter_and_scrubs_credentials(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        mux_daemon_process, "direct_daemon_python",
        lambda source: ("base-pythonw", {"__PYVENV_LAUNCHER__": source}),
    )
    monkeypatch.setenv("GH_TOKEN", "synthetic")
    monkeypatch.setenv("KEEP_FOR_DAEMON", "yes")

    def popen(argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)
        return SimpleNamespace(pid=77)

    monkeypatch.setattr(subprocess, "Popen", popen)
    assert mux_daemon_process.spawn_detached(["venv-python", "-m", "worktree_manager"])
    assert captured["argv"] == ["base-pythonw", "-m", "worktree_manager"]
    env = captured["kwargs"]["env"]
    assert env["__PYVENV_LAUNCHER__"] == "venv-python"
    assert env["KEEP_FOR_DAEMON"] == "yes"
    assert "GH_TOKEN" not in env


def test_ordinary_start_reports_unsupported_interpreter_without_spawning(monkeypatch, caplog):
    def reject(_):
        raise RuntimeError("unsupported direct interpreter")

    monkeypatch.setattr(mux_daemon_process, "direct_daemon_python", reject)

    def popen(*args, **kwargs):
        raise AssertionError("must not launch a redirector")

    monkeypatch.setattr(subprocess, "Popen", popen)
    assert not mux_daemon_process.spawn_detached(["venv-python", "-m", "worktree_manager"])
    assert "unsupported direct interpreter" in caplog.text


def test_passive_start_preserves_direct_venv_and_payload_environment(tmp_path, monkeypatch):
    root = tmp_path / "stable"
    root.mkdir()
    slot = root / "versions" / "candidate"
    captured = {}
    original_python = mux_daemon_cutover.sys.executable
    monkeypatch.setattr(
        mux_daemon_process, "direct_daemon_python",
        lambda source: ("base-pythonw", {"__PYVENV_LAUNCHER__": source}),
    )
    monkeypatch.setenv("GH_TOKEN", "synthetic")

    def popen(argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)
        return SimpleNamespace(pid=77)

    monkeypatch.setattr(subprocess, "Popen", popen)
    proc = mux_daemon_cutover.spawn_passive(slot, root=root, port=54321)
    assert proc.pid == 77
    assert captured["argv"][0] == "base-pythonw"
    assert captured["kwargs"]["cwd"] == str(root)
    env = captured["kwargs"]["env"]
    assert env["__PYVENV_LAUNCHER__"] == original_python
    assert env["PYTHONPATH"].split(os.pathsep)[0] == str(slot / "src")
    assert "GH_TOKEN" not in env


@pytest.mark.skipif(
    os.name != "nt" or sys.prefix == sys.base_prefix,
    reason="real Windows venv launch contract",
)
def test_real_windows_venv_runtime_pid_matches_spawn_and_routing(tmp_path):
    from zdd import routing

    root = tmp_path / "stable"
    root.mkdir()
    slot = root / "versions" / "probe"
    package = slot / "src" / "worktree_manager"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        """import json, os, pathlib, sys, time
from work_coalescing_singleton import CoalescingServer
root = pathlib.Path(next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--root=')))
port = int(next(a.split('=', 1)[1] for a in sys.argv if a.startswith('--listen-port=')))
server = CoalescingServer(lambda kind, payload: {'pid': os.getpid()}, bind_port=port, token='probe-token')
server.start()
try:
    (root / 'runtime.json').write_text(json.dumps({'pid': os.getpid(), 'prefix': sys.prefix}), encoding='utf-8')
    while not (root / 'stop').exists():
        time.sleep(0.02)
finally:
    server.close()
""",
        encoding="utf-8",
    )
    port = mux_daemon_cutover.pick_free_port()
    proc = mux_daemon_cutover.spawn_passive(slot, root=root, port=port)
    result_path = root / "runtime.json"
    try:
        deadline = time.monotonic() + 15
        while not result_path.exists() and proc.poll() is None and time.monotonic() < deadline:
            time.sleep(0.05)
        assert result_path.exists(), f"probe exited or timed out: {proc.poll()}"
        data = json.loads(result_path.read_text(encoding="utf-8"))
        assert data["pid"] == proc.pid
        assert os.path.normcase(data["prefix"]) == os.path.normcase(sys.prefix)
        from work_coalescing_singleton import client
        client_id = client.new_client_id()
        try:
            reply = client.request(
                "127.0.0.1", port, "probe-token",
                kind="probe", key="pid", payload={},
                request_deadline_s=2, client_id=client_id,
            )
            assert reply["pid"] == proc.pid
        finally:
            client.release("127.0.0.1", port, "probe-token", client_id)
        endpoint = routing.publish_active(
            mux_daemon_cutover.routing_dir(root), bind="127.0.0.1",
            port=port, pid=proc.pid, version="probe",
        )
        assert mux_daemon_cutover.active_generation_for_pid(
            data["pid"], root=root
        ) == endpoint.generation
    finally:
        (root / "stop").write_text("stop", encoding="utf-8")
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.terminate()
            proc.wait(timeout=5)
