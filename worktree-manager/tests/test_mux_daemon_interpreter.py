"""Direct-interpreter contracts for mux daemon process ownership."""

from __future__ import annotations

import os
import json
import subprocess
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import agent_procutil as interpreter

from worktree_manager import mux_daemon_cutover, mux_daemon_process
from _manager_process_fixture import stub_runtime, synthetic_runtime


def _venv_python(tmp_path, home="synthetic-base"):
    root = tmp_path / "venv"
    scripts = root / "Scripts"
    scripts.mkdir(parents=True)
    (root / "pyvenv.cfg").write_text(f"home = {home}\n", encoding="utf-8")
    return str(scripts / "python.exe")


def test_direct_interpreter_preserves_windows_venv_environment(tmp_path, monkeypatch):
    executable = _venv_python(tmp_path)
    calls = []

    def select(source):
        calls.append(source)
        return "base-console", {"__PYVENV_LAUNCHER__": source}

    monkeypatch.setattr(mux_daemon_process, "direct_python", select)
    assert mux_daemon_process.direct_daemon_python(executable) == (
        "base-console", {"__PYVENV_LAUNCHER__": executable},
    )
    assert calls == [executable]


def test_windows_venv_without_direct_interpreter_is_rejected(tmp_path, monkeypatch):
    executable = _venv_python(tmp_path)
    monkeypatch.setattr(interpreter, "_is_windows", lambda: True)
    with pytest.raises(RuntimeError, match="base pythonw.exe is unavailable"):
        mux_daemon_process.direct_daemon_python(executable)


def test_direct_windows_interpreter_uses_console_base(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    executable = _venv_python(tmp_path, base)
    console = base / "python.exe"
    console.touch()
    (base / "pythonw.exe").touch()
    monkeypatch.setattr(interpreter, "_is_windows", lambda: True)
    assert mux_daemon_process.direct_daemon_python(executable) == (
        str(console), {"__PYVENV_LAUNCHER__": executable},
    )


def test_direct_windows_interpreter_rejects_missing_console_base(tmp_path, monkeypatch):
    base = tmp_path / "base"
    base.mkdir()
    (base / "pythonw.exe").touch()
    executable = _venv_python(tmp_path, base)
    monkeypatch.setattr(interpreter, "_is_windows", lambda: True)
    with pytest.raises(RuntimeError, match="base python.exe is unavailable"):
        mux_daemon_process.direct_daemon_python(executable)


def test_posix_interpreter_selection_stays_unchanged(monkeypatch):
    monkeypatch.setattr(interpreter, "_is_windows", lambda: False)
    assert mux_daemon_process.direct_daemon_python("/synthetic/bin/python") == (
        "/synthetic/bin/python", {},
    )


@pytest.mark.parametrize("explicit", [False, True])
@pytest.mark.parametrize("relative_root", ["relative-root", "../relative-root"])
def test_selected_root_anchors_relative_paths_without_resolving_links(
    tmp_path, monkeypatch, explicit, relative_root,
):
    cwd = tmp_path / "caller" if relative_root.startswith("..") else tmp_path
    cwd.mkdir(exist_ok=True)
    monkeypatch.chdir(cwd)
    monkeypatch.setenv("WORKTREE_MANAGER_ROOT", relative_root)
    with monkeypatch.context() as scoped:
        scoped.setattr(Path, "resolve", lambda *_a, **_k: pytest.fail("must retain path indirection"))
        selected = mux_daemon_process.selected_root(Path(relative_root) if explicit else None)
    assert selected == tmp_path / "relative-root"


def test_ordinary_start_anchors_relative_cwd_before_instance_spec(tmp_path, monkeypatch):
    (tmp_path / "relative-root").mkdir()
    monkeypatch.chdir(tmp_path)
    captured = {}
    monkeypatch.setattr(mux_daemon_process, "direct_daemon_python", lambda source: (source, {}))
    monkeypatch.setattr(
        mux_daemon_process, "spawn_daemon",
        lambda argv, **kwargs: captured.update(kwargs),
    )
    assert mux_daemon_process.spawn_detached(["python"], cwd=Path("relative-root"))
    assert captured["cwd"] == str(tmp_path / "relative-root")
    assert captured["instance"].runtime_root == tmp_path / "relative-root"


@pytest.mark.parametrize("gui_name", ["pythonw.exe", "gui-host.exe"], ids=["relocated", "renamed"])
def test_linked_gui_keeps_console_and_venv_from_configured_home(tmp_path, monkeypatch, gui_name):
    base = tmp_path / "base"
    base.mkdir()
    console = base / "python.exe"
    console.touch()
    executable = _venv_python(tmp_path, base)
    gui_dir = tmp_path / "gui-only"
    gui_dir.mkdir()
    gui = gui_dir / gui_name
    gui.touch()
    alias = str(base / "pythonw.exe")
    lstat = os.lstat
    readlink = os.readlink
    monkeypatch.setattr(interpreter, "_is_windows", lambda: True)
    monkeypatch.setattr(os, "lstat", lambda path, *a, **k: (
        SimpleNamespace(st_mode=stat.S_IFLNK) if os.fspath(path) == alias else lstat(path, *a, **k)
    ))
    monkeypatch.setattr(os, "readlink", lambda path: (
        str(gui) if os.fspath(path) == alias else readlink(path)
    ))
    assert mux_daemon_process.direct_daemon_python(executable) == (
        str(console), {"__PYVENV_LAUNCHER__": executable},
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

    monkeypatch.setattr(mux_daemon_process, "spawn_daemon", popen)
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


@pytest.mark.parametrize("relative", [False, True])
def test_passive_start_preserves_direct_venv_and_payload_environment(tmp_path, monkeypatch, relative):
    root = tmp_path / "stable"
    root.mkdir()
    slot = root / "versions" / "candidate"
    captured = {}
    target_python = stub_runtime(slot)
    monkeypatch.setattr(
        mux_daemon_process, "direct_daemon_python",
        lambda source: ("base-pythonw", {"__PYVENV_LAUNCHER__": source}),
    )
    monkeypatch.setenv("GH_TOKEN", "synthetic")

    def popen(argv, **kwargs):
        captured.update(argv=argv, kwargs=kwargs)
        return SimpleNamespace(pid=77)

    monkeypatch.setattr(mux_daemon_cutover, "spawn_daemon", popen)
    monkeypatch.chdir(tmp_path)
    proc = mux_daemon_cutover.spawn_passive(
        slot, root=Path("stable") if relative else root, port=54321,
    )
    assert proc.pid == 77
    assert captured["argv"][0] == "base-pythonw"
    assert captured["kwargs"]["cwd"] == root
    assert captured["kwargs"]["instance"].runtime_root == root
    assert f"--root={root}" in captured["argv"]
    env = captured["kwargs"]["env"]
    assert env["__PYVENV_LAUNCHER__"] == str(target_python)
    assert "PYTHONPATH" not in env
    assert "GH_TOKEN" not in env


@pytest.mark.skipif(
    os.name != "nt" or sys.prefix == sys.base_prefix,
    reason="real Windows venv launch contract",
)
def test_real_windows_venv_runtime_pid_matches_spawn_and_routing(tmp_path):
    from zdd import routing

    root = tmp_path / "stable"
    root.mkdir()
    slot = synthetic_runtime(root, "probe", program="""import json, os, pathlib, sys, time
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
""")
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
        assert os.path.normcase(data["prefix"]) == os.path.normcase(str(slot))
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
