"""Opt-in native GUI-parent observation; never part of required unit CI."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("AGENT_SSH_WINDOWS_VISIBILITY") != "1",
    reason="requires explicit native Windows visibility acceptance",
)


@pytest.mark.timeout(120)
def test_windowless_writer_chain_two_cycles_and_timeout(tmp_path):
    import agent_procutil

    plugin = Path(__file__).resolve().parents[1]
    observer_source = (
        plugin.parents[1] / "libs" / "agent-procutil" / "tests"
        / "test_spawn_daemon_windows_integration.py"
    )
    spec = importlib.util.spec_from_file_location("ssh_window_observer", observer_source)
    assert spec and spec.loader
    helpers = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helpers)
    helpers._SURFACED_PROCESS_NAMES.add("pwsh.exe")
    user32, kernel32, callback_type = helpers._configure_win32()
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    assert pythonw.is_file(), "the selected test interpreter must supply its native GUI launcher"
    target = plugin / "tests" / "test_write_session_guidance.py"
    with helpers._WindowObserver(user32, kernel32, callback_type) as observer:
        process, job = agent_procutil.spawn_sync_in_kill_on_close_job(
            [
                str(pythonw), "-m", "pytest", str(target), "-q",
                "-k", "actual_writer_and_cli_replace_snapshot_without_actuation or contributor_timeout_reaps_real_descendant or worktrees_lookup_timeout_reaps_nested_resolver",
                "--basetemp", str(tmp_path / "child-tests"),
            ],
            require_job=True, allow_breakaway=False,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", **agent_procutil.no_window_kwargs(),
        )
        try:
            stdout, stderr = process.communicate(timeout=90)
            assert process.returncode == 0, stdout + stderr
            assert "4 passed" in stdout, stdout
        finally:
            if job is not None and job.handle:
                job.terminate_and_wait(2.0)
            process.communicate(timeout=2.0)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        time.sleep(.1)
    assert observer.surfaced == {}, f"visible test-chain windows: {observer.surfaced}"
    assert observer.focus_changes == {}, f"test-chain foreground transitions: {observer.focus_changes}"
