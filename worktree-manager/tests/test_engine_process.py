"""Preparation cancellation retains ownership across the engine boundary."""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from worktree_manager import engine_process as ep


@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("engine", 1), KeyboardInterrupt()])
@pytest.mark.parametrize("windows", [False, True])
def test_cancel_closes_job_or_owned_group(monkeypatch, failure, windows):
    calls = []
    process = SimpleNamespace(pid=123, returncode=None, poll=lambda: None)

    def communicate(timeout):
        calls.append(("communicate", timeout))
        if timeout == 1:
            raise failure
        return "", ""

    process.communicate = communicate
    process.kill = lambda: calls.append(("kill",))
    job = SimpleNamespace(close=lambda: calls.append(("close",)))

    def spawn(argv, **kwargs):
        calls.append(("spawn", kwargs))
        return process, job if windows else None

    monkeypatch.setattr(ep, "_WINDOWS", windows)
    monkeypatch.setattr(ep, "contained_test_mode", lambda: False)
    monkeypatch.setattr(ep, "detached_kwargs", lambda: {"start_new_session": True})
    monkeypatch.setattr(ep, "spawn_sync_in_kill_on_close_job", spawn)
    if not windows:
        def run_posix(argv, **kwargs):
            calls.append(("posix", kwargs))
            raise failure

        monkeypatch.setattr(ep.engine_posix_process, "run", run_posix)
    with pytest.raises(type(failure)):
        ep.run(["engine"], timeout=1, env={}, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if windows:
        spawned = calls[0][1]
        assert spawned["require_job"] is True
        assert spawned["allow_breakaway"] is False
        assert ep.OWNED_GROUP_ENV not in spawned["env"]
        assert ("close",) in calls
        assert calls[-1] == ("communicate", 5)
    else:
        assert calls[0][0] == "posix"
        assert calls[0][1]["env"][ep.OWNED_GROUP_ENV] == "1"
        assert calls[0][1]["timeout"] == 1


@pytest.mark.skipif(os.name != "nt", reason="Windows descendant Job integration")
def test_real_plan_timeout_kills_renderer_and_grandchild(tmp_path, monkeypatch):
    import sys
    import time

    from worktree_manager import engine_client as ec

    evidence = tmp_path / "alive"
    ready = tmp_path / "ready"
    grandchild = (
        "import time\nfrom pathlib import Path\n"
        f"p=Path({str(evidence)!r})\n"
        "while True:\n    p.write_text(str(time.monotonic()))\n    time.sleep(0.05)\n"
    )
    renderer = (
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable,'-c',{grandchild!r}])\n"
        "time.sleep(60)\n"
    )
    engine = (
        "import subprocess,sys,time\nfrom pathlib import Path\n"
        f"subprocess.Popen([sys.executable,'-c',{renderer!r}])\n"
        f"Path({str(ready)!r}).write_text('ready')\n"
        "time.sleep(60)\n"
    )
    spawn = ep.spawn_sync_in_kill_on_close_job

    def spawn_ready(*args, **kwargs):
        process, job = spawn(*args, **kwargs)
        try:
            # Start the cleanup deadline only after the descendant is observable.
            deadline = time.monotonic() + 10
            while not evidence.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            assert evidence.exists(), "fixture grandchild did not become ready"
        except BaseException:
            if job is not None:
                job.close()
            else:
                process.kill()
            process.communicate(timeout=5)
            raise
        return process, job

    monkeypatch.setattr(ep, "spawn_sync_in_kill_on_close_job", spawn_ready)
    monkeypatch.setattr(ec, "engine_base_command", lambda: [sys.executable, "-c", engine])
    with pytest.raises(ec.EngineError, match="timed out"):
        ec.resolve_launch_plan("demo", new=True, timeout=3)
    assert ready.exists()
    assert evidence.exists(), "grandchild never started; descendant cleanup was not exercised"
    stopped = evidence.read_text()
    time.sleep(0.3)
    assert evidence.read_text() == stopped
