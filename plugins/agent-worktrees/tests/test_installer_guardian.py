"""The stdlib guardian observes exit without reaping on older macOS Python."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import venv

import pytest
import agent_procutil
from agent_procutil import no_window_kwargs


@pytest.fixture
def guardian():
    source = Path(__file__).resolve().parents[3] / "libs" / "installer-engine" / "installer_engine_process.py"
    spec = importlib.util.spec_from_file_location("guardian_fixture", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("state,exited", [("S\n", False), ("Z\n", True), ("Z+\n", True)])
def test_missing_waitid_uses_nonreaping_process_state(guardian, monkeypatch, state, exited):
    monkeypatch.setattr(guardian, "os", SimpleNamespace())
    calls = []

    def observe(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(stdout=state)

    monkeypatch.setattr(guardian.subprocess, "run", observe)
    assert guardian._child_exited_unreaped(123) is exited
    assert calls[0][0] == ["ps", "-o", "stat=", "-p", "123"]
    assert calls[0][1]["check"] is True and calls[0][1]["timeout"] == 5


@pytest.mark.parametrize("state", ["", "Z\nS\n"])
def test_ambiguous_process_observation_is_not_exit(guardian, monkeypatch, state):
    monkeypatch.setattr(guardian, "os", SimpleNamespace())
    monkeypatch.setattr(guardian.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(stdout=state))
    with pytest.raises(RuntimeError, match="Cannot observe"):
        guardian._child_exited_unreaped(123)


@pytest.mark.parametrize("code,expected", [(23, 23), (-15, 143)])
def test_guardian_reaps_only_after_group_settlement(guardian, monkeypatch, code, expected):
    process = SimpleNamespace(pid=123, returncode=None)
    events = []
    observations = iter([False, True])
    monkeypatch.setattr(guardian, "os", SimpleNamespace(
        close=lambda fd: events.append(("closed", fd)),
    ))
    monkeypatch.setattr(guardian, "subprocess", SimpleNamespace(
        Popen=lambda *a, **k: process, DEVNULL=subprocess.DEVNULL,
    ))
    monkeypatch.setattr(guardian.select, "select", lambda *args: ([], [], []))

    def observe(pid):
        assert pid == 123 and process.returncode is None
        return next(observations)

    def settle(child):
        assert child is process and child.returncode is None
        events.append("settled")
        child.returncode = code

    monkeypatch.setattr(guardian, "_child_exited_unreaped", observe)
    monkeypatch.setattr(guardian, "_terminate_group", settle)
    assert guardian._supervise(17, ["fixture"]) == expected
    assert events == ["settled", ("closed", 17)]


@pytest.mark.skipif(os.name == "nt", reason="native POSIX ps/process-group guardian")
def test_real_guardian_without_waitid_preserves_child_exit(guardian, tmp_path):
    script = (
        "import importlib.util,os,sys;"
        "s=importlib.util.spec_from_file_location('guardian',sys.argv[1]);"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "setattr(m.os,'waitid',None);r,w=os.pipe();"
        "result=m._supervise(r,[sys.executable,'-c','raise SystemExit(23)']);"
        "os.close(w);raise SystemExit(result)"
    )
    result = subprocess.run(
        [sys.executable, "-I", "-B", "-c", script, guardian.__file__],
        cwd=tmp_path, capture_output=True, text=True, timeout=15,
        **no_window_kwargs(),
    )
    assert result.returncode == 23, result.stdout + result.stderr


@pytest.mark.skipif(os.name != "nt", reason="native Windows retained Job guardian")
@pytest.mark.parametrize("outcome", ["success", "exit", "timeout", "launch"])
@pytest.mark.timeout(30)
def test_retained_windows_child_preserves_outcome_and_releases_custody(
    guardian, tmp_path, outcome,
):
    runtime_path = (
        Path(__file__).resolve().parents[3]
        / "libs" / "versioned-runtime" / "versioned_runtime.py"
    )
    runtime = guardian._load_resource("retained_guardian_test_runtime", runtime_path)
    root = tmp_path / "runtime"
    root.mkdir()
    slot = runtime.slot(root, "probe")
    commands = {
        "success": [sys.executable, "-I", "-c", "print('owned')"],
        "exit": [sys.executable, "-I", "-c", "raise SystemExit(23)"],
        "timeout": [sys.executable, "-I", "-c", "import time; time.sleep(60)"],
        "launch": [str(tmp_path / "missing-child.exe")],
    }
    errors = {
        "exit": subprocess.CalledProcessError,
        "timeout": guardian.ChildTimeoutExpired,
        "launch": guardian.ChildLaunchError,
    }
    with runtime.construction_lease(root, "probe") as descriptor:
        def run():
            return guardian.run_owned(
                commands[outcome], cwd=slot, env=dict(os.environ),
                timeout=3, procutil=agent_procutil, construction_fd=descriptor,
                runtime_root=root, runtime_version="probe", runtime_path=runtime_path,
            )

        if outcome == "success":
            assert run().stdout == "owned\n"
        else:
            with pytest.raises(errors[outcome]) as caught:
                run()
            if outcome == "exit":
                assert caught.value.returncode == 23
        with runtime._cleanup_lease(root, "probe", allow_construction_owner=True) as admitted:
            assert admitted


@pytest.mark.skipif(os.name != "nt", reason="native Windows executable-token limit")
@pytest.mark.timeout(30)
def test_retained_windows_guardian_accepts_a_long_owning_interpreter(guardian, tmp_path):
    environment = tmp_path / "owner"
    while len(str(environment / "Scripts" / "python.exe")) <= 270:
        environment /= "interpreter-" + "x" * 30
    venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
    python = environment / "Scripts" / "python.exe"
    repo = Path(__file__).resolve().parents[3]
    code = """
import importlib.util,json,sys
from pathlib import Path
s=importlib.util.spec_from_file_location("long_owner_guardian",sys.argv[1])
guardian=importlib.util.module_from_spec(s);s.loader.exec_module(guardian)
runtime=guardian._load_resource("long_owner_runtime",sys.argv[2])
procutil=guardian._load_resource("long_owner_procutil",sys.argv[3])
root=Path(sys.argv[4]);root.mkdir();slot=runtime.slot(root,"probe")
assert len(sys.executable)>260
with runtime.construction_lease(root,"probe") as descriptor:
    result=guardian.run_owned(
        [sys._base_executable,"-I","-c","print('owned')"],
        cwd=slot,env=dict(__import__("os").environ),timeout=3,
        procutil=procutil,construction_fd=descriptor,
        runtime_root=root,runtime_version="probe",runtime_path=Path(sys.argv[2]),
    )
    with runtime._cleanup_lease(root,"probe",allow_construction_owner=True) as admitted:
        assert admitted
print(json.dumps({"owner":sys.executable,"output":result.stdout}))
"""
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", code, guardian.__file__,
         str(repo / "libs" / "versioned-runtime" / "versioned_runtime.py"),
         str(repo / "libs" / "agent-procutil" / "src" / "agent_procutil" / "__init__.py"),
         str(tmp_path / "runtime")],
        executable=str(python), cwd=tmp_path, capture_output=True, text=True, timeout=20,
        **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert Path(report["owner"]) == python and report["output"] == "owned\n"
