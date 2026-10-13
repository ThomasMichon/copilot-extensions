"""Group lifetime remains owned after engine success and inner capture failure."""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace
from pathlib import Path

import pytest

from worktree_manager import engine_posix_process as pp


@pytest.mark.parametrize("outcome", ["success", "timeout", "interrupt", "invalid", "closed", "launch-failed"])
def test_group_owner_is_not_reaped_before_descendant_sweep(monkeypatch, outcome):
    events = []
    owner = SimpleNamespace(
        pid=123,
        stdout=io.StringIO(
            '{"error":"engine executable missing"}\n' if outcome == "launch-failed" else
            '{"returncode":0}\n' if outcome not in ("invalid", "closed")
            else '{"returncode":true}\n' if outcome == "invalid" else ""
        ),
        poll=lambda: pytest.fail("reaped the group owner before cleanup"),
    )
    owner.communicate = lambda **kwargs: events.append(("reap", kwargs["timeout"]))

    def spawn(argv, **kwargs):
        events.append(("spawn", kwargs))
        os.write(int(argv[-1]), b'{"action":"exec"}')
        return owner

    def ready(*args):
        if outcome == "interrupt":
            raise KeyboardInterrupt()
        return ([] if outcome == "timeout" else [owner.stdout]), [], []

    monkeypatch.setattr(pp.subprocess, "Popen", spawn)
    monkeypatch.setattr(pp.select, "select", ready)
    monkeypatch.setattr(pp.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(pp.os, "killpg", lambda pid, sig: events.append(("sweep", pid, sig)), raising=False)
    if outcome == "success":
        result = pp.run(["engine"], timeout=1, text=True, env={}, stderr=subprocess.DEVNULL)
        assert result.returncode == 0
        assert result.stdout == '{"action":"exec"}'
    else:
        error = (
            subprocess.TimeoutExpired if outcome == "timeout" else
            KeyboardInterrupt if outcome == "interrupt" else OSError
        )
        with pytest.raises(error):
            pp.run(["engine"], timeout=1, text=True, env={}, stderr=subprocess.DEVNULL)
    assert events[0][1]["start_new_session"] is True
    assert events[0][1]["stdin"] is subprocess.PIPE
    assert events[-2:] == [("sweep", 123, 9), ("reap", 5)]


@pytest.mark.skipif(os.name == "nt", reason="native POSIX group lifetime")
def test_real_spawn_failure_preserves_the_engine_error(tmp_path):
    with pytest.raises(OSError, match="No such file"):
        pp.run(
            [str(tmp_path / "missing-engine")], timeout=10, text=True,
            env=dict(os.environ), stderr=subprocess.DEVNULL,
        )


@pytest.mark.skipif(os.name == "nt", reason="native POSIX group lifetime")
def test_real_degraded_capture_leaves_no_live_grandchild(tmp_path):
    ready = tmp_path / "ready"
    evidence = tmp_path / "alive"
    grandchild = (
        "import time\nfrom pathlib import Path\n"
        f"p=Path({str(evidence)!r})\n"
        "while True:\n    p.write_text(str(time.monotonic()))\n    time.sleep(.05)\n"
    )
    renderer = (
        "import subprocess,sys,time\n"
        f"subprocess.Popen([sys.executable,'-c',{grandchild!r}])\n"
        "time.sleep(60)\n"
    )
    root = Path(__file__).resolve().parents[2]
    engine = (
        "import importlib.util,json,os,sys,time\nfrom pathlib import Path\n"
        f"sys.path.insert(0,{str(root / 'libs' / 'agent-procutil' / 'src')!r})\n"
        f"s=importlib.util.spec_from_file_location('push_timeout',"
        f"{str(root / 'plugins' / 'agent-worktrees' / 'src' / 'agent_worktrees' / 'push_timeout.py')!r})\n"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
        "spawn=m.spawn_sync_in_kill_on_close_job\n"
        "def failing_spawn(*args,**kwargs):\n"
        "    child,job=spawn(*args,**kwargs)\n"
        "    def failed_capture(**kwargs):\n"
        "        deadline=time.monotonic()+10\n"
        f"        while not Path({str(evidence)!r}).exists():\n"
        "            if time.monotonic()>deadline: raise RuntimeError('grandchild never started')\n"
        "            time.sleep(.05)\n"
        "        raise OSError('injected renderer capture failure')\n"
        "    child.communicate=failed_capture\n"
        "    return child,job\n"
        "m.spawn_sync_in_kill_on_close_job=failing_spawn\n"
        "try:\n"
        f"    m.run_bounded([sys.executable,'-c',{renderer!r}],cwd=None,env=dict(os.environ),"
        "timeout=None,inherit_process_group=True)\n"
        "except OSError:\n"
        f"    Path({str(ready)!r}).write_text('degraded')\n"
        "    print(json.dumps({'action':'exec'}))\n"
    )
    result = pp.run(
        [sys.executable, "-c", engine], timeout=20, text=True,
        env=dict(os.environ), stderr=subprocess.DEVNULL,
    )
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"action": "exec"}
    assert ready.read_text() == "degraded"
    assert evidence.exists()
    stopped = evidence.read_text()
    time.sleep(0.3)
    assert evidence.read_text() == stopped
