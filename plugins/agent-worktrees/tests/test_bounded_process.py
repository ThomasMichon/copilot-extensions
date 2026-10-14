"""Captured-child launch and ownership contracts; no real process in guards."""

from __future__ import annotations

import subprocess
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import push_timeout


@pytest.mark.guard
@pytest.mark.parametrize("windows", [True, False])
@pytest.mark.parametrize("outcome", ["success", "timeout", "error"])
@pytest.mark.parametrize("binary", [False, True])
def test_captured_tree_launch_and_cleanup(monkeypatch, windows, outcome, binary):
    events = []
    launch = {}
    job = SimpleNamespace(close=lambda: events.append("job-close"))
    proc = SimpleNamespace(returncode=7)

    def communicate(*, timeout, input=None):
        assert input == (b"\0\xff" if binary and timeout == 0.5 else None)
        events.append(("communicate", timeout))
        if outcome == "timeout" and timeout == 0.5:
            raise subprocess.TimeoutExpired(["fixture"], timeout, "partial", "diagnostic")
        if outcome == "error":
            raise OSError("capture failed")
        return (b"output", b"error output") if binary else ("output", "error output")

    proc.communicate = communicate

    def spawn(argv, **kwargs):
        launch.update(argv=argv, **kwargs)
        return proc, job if windows else None

    monkeypatch.setattr(push_timeout.platform, "system", lambda: "Windows" if windows else "Linux")
    monkeypatch.setattr(push_timeout, "spawn_sync_in_kill_on_close_job", spawn)
    monkeypatch.setattr(push_timeout, "no_window_kwargs", lambda: {"headless": True})
    monkeypatch.setattr(push_timeout, "detached_kwargs", lambda: {"own_group": True})
    monkeypatch.setattr(push_timeout, "_kill_tree", lambda child: events.append("group-kill"))
    env = {"fixture": "value"}
    if outcome == "success":
        result = push_timeout.run_bounded(
            ["fixture"], cwd="workspace", env=env, timeout=0.5,
            binary=binary, input=b"\0\xff" if binary else None,
        )
        assert (result.returncode, result.stdout, result.stderr) == (
            (7, b"output", b"error output") if binary else (7, "output", "error output")
        )
    elif outcome == "timeout":
        with pytest.raises(subprocess.TimeoutExpired) as raised:
            push_timeout.run_bounded(
                ["fixture"], cwd="workspace", env=env, timeout=0.5,
                binary=binary, input=b"\0\xff" if binary else None,
            )
        assert (raised.value.stdout, raised.value.stderr) == (
            (b"output", b"error output") if binary else ("output", "error output")
        )
        assert events[1] == ("job-close" if windows else "group-kill")
        assert events[2] == ("communicate", 5)
    else:
        with pytest.raises(OSError, match="capture failed"):
            push_timeout.run_bounded(
                ["fixture"], cwd="workspace", env=env, timeout=0.5,
                binary=binary, input=b"\0\xff" if binary else None,
            )
    assert launch["require_job"] is windows
    assert launch["allow_breakaway"] is False
    assert launch.get("headless", False) is windows
    assert launch.get("own_group", False) is not windows
    assert launch["env"] is env and launch["cwd"] == "workspace"
    assert launch["stdout"] == subprocess.PIPE and launch["stderr"] == subprocess.PIPE
    assert launch["text"] is not binary
    if binary:
        assert "encoding" not in launch and launch["stdin"] == subprocess.PIPE
    else:
        assert launch["encoding"] == "utf-8"
    if windows:
        assert events[-1] == "job-close"
        assert "group-kill" not in events
    else:
        assert "job-close" not in events


@pytest.mark.guard
def test_required_job_admission_failure_is_not_retried(monkeypatch):
    calls = []

    def refuse(argv, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("Windows descendant Job assignment failed before child resume")

    monkeypatch.setattr(push_timeout.platform, "system", lambda: "Windows")
    monkeypatch.setattr(push_timeout, "spawn_sync_in_kill_on_close_job", refuse)
    with pytest.raises(RuntimeError, match="before child resume"):
        push_timeout.run_bounded(["fixture"], cwd=None, env={}, timeout=0.5)
    assert len(calls) == 1 and calls[0]["require_job"] is True


@pytest.mark.guard
@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired("renderer", 1), OSError("capture failed"), KeyboardInterrupt(),
])
def test_inherited_process_group_never_signals_its_caller(monkeypatch, failure):
    events = []
    launch = {}
    proc = SimpleNamespace(pid=123, returncode=None, poll=lambda: None)

    def communicate(*, timeout):
        if timeout != 5:
            raise failure
        return "partial", "diagnostic"

    def spawn(argv, **kwargs):
        launch.update(kwargs)
        return proc, None

    proc.communicate = communicate
    proc.kill = lambda: events.append("child-kill")
    monkeypatch.setattr(push_timeout.platform, "system", lambda: "Linux")
    monkeypatch.setattr(push_timeout, "contained_test_mode", lambda: False)
    monkeypatch.setattr(push_timeout, "spawn_sync_in_kill_on_close_job", spawn)
    monkeypatch.setattr(push_timeout.os, "getpgid", lambda pid: 42, raising=False)
    monkeypatch.setattr(push_timeout.os, "getpgrp", lambda: 42, raising=False)
    monkeypatch.setattr(
        push_timeout.os, "killpg", lambda *_args: pytest.fail("signaled the caller's group"),
        raising=False,
    )
    with pytest.raises(type(failure)):
        push_timeout.run_bounded(
            ["renderer"], cwd=None, env={}, timeout=None, inherit_process_group=True,
        )
    assert events and set(events) == {"child-kill"}
    assert "start_new_session" not in launch


@pytest.mark.portfolio_tier("T2")
@pytest.mark.effect("process")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("AGENT_WORKTREES_BOUNDED_NATIVE") != "1",
    reason="explicit Windows consoleless-parent integration",
)
def test_native_consoleless_capture_and_descendant_timeout(tmp_path):
    from agent_procutil import (
        no_window_kwargs,
        spawn_sync_in_kill_on_close_job,
        windowless_python,
        windowless_python_env,
    )

    native = Path(__file__).with_name("native_bounded_process.py")
    report_path = tmp_path / "report.json"
    env = dict(os.environ)
    env.update(windowless_python_env())
    proc, job = spawn_sync_in_kill_on_close_job(
        [
            windowless_python(), str(native),
            str(Path(__file__).resolve().parents[1] / "src"),
            sys.executable, str(tmp_path), str(report_path),
        ],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        require_job=True, **no_window_kwargs(),
    )
    try:
        assert proc.wait(timeout=25) == 0, (
            report_path.read_text() if report_path.exists() else "native driver failed before reporting"
        )
        report = json.loads(report_path.read_text())
        assert report == {
            "cycles": 2, "timeout": True, "failure": "",
            "breakaway_contained": True,
            "new_visible_windows": 0, "foreground_transitions": 0, "observer_errors": [],
        }
    finally:
        if job is not None:
            job.close()
