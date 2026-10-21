"""The stdlib guardian observes exit without reaping on older macOS Python."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest
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
