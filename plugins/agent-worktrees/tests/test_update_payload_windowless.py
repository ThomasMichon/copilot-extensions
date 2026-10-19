"""Opt-in desktop observation of strict payload cleanup from a windowless parent."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

import agent_procutil as pu

pytestmark = [
    pytest.mark.real_installer_processes,
    pytest.mark.skipif(sys.platform != "win32", reason="native Windows desktop observation"),
]


def test_two_windowless_parent_cycles_leave_no_windows_or_children(tmp_path):
    observer_path = (
        Path(__file__).resolve().parents[3] / "libs" / "agent-procutil" / "tests"
        / "test_spawn_daemon_windows_integration.py"
    )
    if not observer_path.is_file():
        pytest.skip("desktop observation requires the source-checkout observer fixture")
    spec = importlib.util.spec_from_file_location("payload_window_observer", observer_path)
    assert spec is not None and spec.loader is not None
    observer_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(observer_module)
    user32, kernel32, callback_type = observer_module._configure_win32()
    if not user32.GetForegroundWindow():
        pytest.skip("desktop observation requires an observable foreground window")
    pythonw = pu.windowless_python(sys.executable)
    if not pythonw.lower().endswith("pythonw.exe"):
        pytest.skip("this interpreter has no windowless parent")
    dependency = observer_path.parents[1] / "src"
    source_paths = json.dumps([str(dependency), *sys.path])

    worker = tmp_path / "worker.py"
    worker.write_text(textwrap.dedent("""
        import json, os, subprocess, sys, time
        from pathlib import Path
        sys.path[:] = json.loads(sys.argv[1])
        from agent_procutil import no_window_kwargs
        child = subprocess.Popen(
            [sys.executable, "-c",
             "import pathlib,sys,time; time.sleep(30); pathlib.Path(sys.argv[1]).touch()",
             sys.argv[3]],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            **no_window_kwargs(),
        )
        Path(sys.argv[2]).write_text(json.dumps([os.getpid(), child.pid]))
        print("ready", flush=True)
        time.sleep(60)
    """), encoding="utf-8")
    parent = tmp_path / "windowless-parent.py"
    parent.write_text(textwrap.dedent("""
        import json, os, sys, time
        from pathlib import Path
        sys.path[:] = json.loads(sys.argv[1])
        from agent_worktrees import installer_run
        root = Path(sys.argv[2])
        original_spawn = installer_run._spawn
        def spawn(*args, **kwargs):
            process, job = original_spawn(*args, **kwargs)
            ready = root / "witness-open"
            deadline = time.monotonic() + 10
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.02)
            if not ready.exists():
                installer_run._kill_tree(process, job)
                raise AssertionError("native observer did not bind child handles before deadline")
            return process, job
        installer_run._spawn = spawn
        result = installer_run.run_installer(
            [sys.argv[3], sys.argv[4], sys.argv[1], str(root / "pids.json"),
             str(root / "late-write")],
            cwd=root, env=os.environ, deadline_s=3, log=lambda line: None,
            require_tree_exit=True,
        )
        (root / "result.json").write_text(json.dumps({"status": result.status, "rc": result.rc}))
    """), encoding="utf-8")

    with observer_module._WindowObserver(user32, kernel32, callback_type) as observer:
        for cycle in range(2):
            root = tmp_path / f"cycle-{cycle}"
            root.mkdir()
            handles = []
            with (root / "parent.log").open("wb") as log:
                process, job = pu.spawn_sync_in_kill_on_close_job(
                    [pythonw, str(parent), source_paths, str(root),
                     sys.executable, str(worker)],
                    require_job=True, allow_breakaway=False,
                    env={**os.environ, **pu.windowless_python_env(sys.executable)},
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                    **pu.no_window_kwargs(),
                )
                try:
                    assert job is not None
                    deadline = time.monotonic() + 15
                    pids = None
                    while time.monotonic() < deadline:
                        pids = observer_module._read_json(root / "pids.json")
                        if pids is not None:
                            break
                        assert process.poll() is None, (root / "parent.log").read_text()
                        time.sleep(0.02)
                    assert pids is not None, (root / "parent.log").read_text()
                    for pid in pids:
                        handle = kernel32.OpenProcess(0x00100000, False, pid)
                        assert handle, "child exited before the native containment witness"
                        handles.append(handle)
                    (root / "witness-open").touch()
                    assert process.wait(timeout=15) == 0, (root / "parent.log").read_text()
                    result = json.loads((root / "result.json").read_text())
                    assert result["status"].startswith("timed out") and result["rc"] is None
                    for handle in handles:
                        assert kernel32.WaitForSingleObject(handle, 3000) == 0
                    assert not (root / "late-write").exists()
                finally:
                    if job is not None:
                        job.terminate_and_wait(10)
                    process.wait(timeout=10)
                    for handle in handles:
                        assert kernel32.CloseHandle(handle)
    assert observer.surfaced == {}, f"visible windows surfaced: {observer.surfaced}"
    assert observer.focus_changes == {}, f"foreground transitions: {observer.focus_changes}"
