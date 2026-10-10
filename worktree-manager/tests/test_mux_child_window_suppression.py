"""Recurring mux children use the shared captured-child launch contract."""

from __future__ import annotations

import subprocess
import ctypes
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import mux_daemon
from worktree_manager import mux_daemon_process
from worktree_manager import mux_child_process


@pytest.mark.parametrize("timeout", [False, True])
def test_windows_captured_child_closes_job(
    monkeypatch: pytest.MonkeyPatch, timeout: bool,
) -> None:
    closed: list[bool] = []
    argv = ["fixture-mux", "has-session"]

    class Process:
        returncode = 0
        calls = 0

        def communicate(self, timeout: float) -> tuple[str, str]:
            self.calls += 1
            if self.calls == 1 and timeout == .1:
                raise subprocess.TimeoutExpired(argv, timeout)
            return "stdout", "stderr"

    process = Process()
    monkeypatch.setattr(mux_child_process, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(
        mux_child_process, "spawn_sync_in_kill_on_close_job",
        lambda *args, **kwargs: (process, SimpleNamespace(close=lambda: closed.append(True))),
    )
    if timeout:
        with pytest.raises(subprocess.TimeoutExpired):
            mux_child_process.run(argv, timeout=.1)
    else:
        result = mux_child_process.run(argv, timeout=1)
        assert (result.returncode, result.stdout, result.stderr) == (0, "stdout", "stderr")
    assert closed == [True]
    assert process.calls == 2


def test_repeated_mux_calls_preserve_suppression_and_capture(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], dict]] = []
    sentinel = {"creationflags": 0x08000000}
    monkeypatch.setattr(mux_daemon, "no_window_kwargs", lambda: sentinel)

    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout="captured", stderr="")

    monkeypatch.setattr(mux_daemon, "run_mux_child", run)
    entry = {"mux_bin": "psmux", "mux_session": "example-session"}
    for _ in range(2):
        assert mux_daemon._mux_session_alive("psmux", "example-session")
        assert mux_daemon.apply_status_options(entry, {"@aw_ctx": "context", "@aw_seg": "status"})
    assert len(calls) == 6
    for argv, kwargs in calls:
        assert kwargs["creationflags"] == sentinel["creationflags"]
        assert kwargs["capture_output"] is True
        assert kwargs["timeout"] == (5 if argv[1] == "has-session" else 15)


@pytest.mark.parametrize("probe", [True, False])
@pytest.mark.parametrize("failure", ["exit", "timeout", "launch"])
def test_mux_failure_behavior_preserved(
    monkeypatch: pytest.MonkeyPatch, probe: bool, failure: str,
) -> None:
    def run(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if failure == "timeout":
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if failure == "launch":
            raise OSError("synthetic launch failure")
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(mux_daemon, "run_mux_child", run)
    if probe:
        assert not mux_daemon._mux_session_alive("psmux", "example-session")
    else:
        assert not mux_daemon.apply_status_options(
            {"mux_bin": "psmux", "mux_session": "example-session"}, {"@aw_ctx": "context"},
        )


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("MUX_CHILD_WINDOWS_E2E") != "1",
    reason="opt-in interactive Windows PSMux observation",
)
def test_real_mux_children_two_cycles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise actual PSMux clients from the production daemon launch shape."""
    api = ctypes.WinDLL("kernel32", use_last_error=True)
    session = ctypes.c_ulong()
    assert api.ProcessIdToSessionId(os.getpid(), ctypes.byref(session))
    assert session.value != 0, "desktop observation cannot run in Windows session zero"
    psmux = os.environ["PSMUX_TEST_BIN"]
    from mux_windows_observer import descendants, processes, windows
    output = tmp_path / "result.json"
    script = """
import json, os, pathlib, subprocess, sys, time
from agent_procutil import no_window_kwargs
from worktree_manager import mux_daemon
root, psmux = pathlib.Path(sys.argv[1]), sys.argv[2]
os.environ['PSMUX_DATA_DIR'] = str(root / 'mux-data')
os.environ['PSMUX_NO_WARM'] = '1'
for key in ('TMUX', 'TMUX_PANE', 'PSMUX_SESSION', 'PSMUX_TARGET_SESSION'):
    os.environ.pop(key, None)
entry = {'mux_bin': psmux, 'mux_session': 'mux-window-fixture'}
created = subprocess.run(
    [psmux, 'new-session', '-d', '-s', entry['mux_session'],
     '--', sys.executable, '-c', 'import time; time.sleep(15)'],
    capture_output=True, timeout=10, **no_window_kwargs(),
)
assert created.returncode == 0, created.stderr
results = []
try:
    for cycle in range(2):
        results.append([
            mux_daemon._mux_session_alive(psmux, entry['mux_session']),
            mux_daemon.apply_status_options(entry, {'@fixture_ctx': 'context', '@fixture_seg': 'status'}),
        ])
        time.sleep(.5)
finally:
    stopped = subprocess.run(
        [psmux, 'kill-session', '-t', entry['mux_session']],
        capture_output=True, timeout=10, **no_window_kwargs(),
    )
    assert stopped.returncode == 0, stopped.stderr
(root / 'result.json').write_text(json.dumps(results), encoding='utf-8')
"""
    roots: set[int] = set()
    owned: set[int] = set()
    visible: set[int] = set()
    focused: set[int] = set()
    stop = threading.Event()
    baseline_windows, baseline_foreground = windows()
    baseline_pids = set(processes())
    launched: list[subprocess.Popen] = []
    real_popen = subprocess.Popen

    def popen(argv: list[str], **kwargs: object) -> subprocess.Popen:
        process = real_popen(argv, **kwargs)
        roots.add(process.pid)
        launched.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", popen)

    def observe() -> None:
        while not stop.is_set():
            table = processes()
            for pid in roots.copy():
                owned.update(descendants(pid, table))
            visible_windows, foreground_pid = windows()
            for hwnd, pid in visible_windows.items():
                name = table.get(pid, ("", 0))[0].lower()
                if pid in owned or (
                    hwnd not in baseline_windows
                    and name in {"windowsterminal.exe", "openconsole.exe", "conhost.exe"}
                ):
                    visible.add(hwnd)
            if foreground_pid in owned:
                focused.add(foreground_pid)
            if foreground_pid != baseline_foreground and table.get(
                foreground_pid, ("", 0),
            )[0].lower() in {"windowsterminal.exe", "openconsole.exe", "conhost.exe"}:
                focused.add(foreground_pid)
            for pid, (name, _) in table.items():
                if pid not in baseline_pids and name.lower() == "openconsole.exe":
                    visible.add(pid)
            stop.wait(.01)

    with ThreadPoolExecutor(max_workers=1) as executor:
        monitor = executor.submit(observe)
        try:
            assert mux_daemon_process.spawn_detached(
                [sys.executable, "-c", script, str(tmp_path), psmux],
            )
            deadline = time.monotonic() + 15
            while not output.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            assert output.exists(), "PSMux fixture failed to produce its two-cycle result"
            assert json.loads(output.read_text(encoding="utf-8")) == [[True, True], [True, True]]
            assert launched[0].wait(timeout=5) == 0
            assert roots and owned, "observer never identified its fixture processes"
            deadline = time.monotonic() + 3
            while owned & processes().keys() and time.monotonic() < deadline:
                time.sleep(.02)
            assert not (owned & processes().keys()), "fixture descendants leaked"
        finally:
            stop.set()
            monitor.result(timeout=3)
    assert not visible, "owned mux child surfaced a visible window"
    assert not focused, "owned mux child stole focus"


@pytest.mark.skipif(
    os.name != "nt" or os.environ.get("MUX_CHILD_WINDOWS_E2E") != "1",
    reason="opt-in real PSMux timeout containment",
)
@pytest.mark.parametrize("probe", [False, True])
def test_real_psmux_timeout_reaps_descendants(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, probe: bool,
) -> None:
    from mux_windows_observer import descendants, processes

    env = dict(os.environ)
    for key in ("TMUX", "TMUX_PANE", "PSMUX_SESSION", "PSMUX_TARGET_SESSION"):
        env.pop(key, None)
    env["PSMUX_DATA_DIR"] = str(tmp_path / "timeout-mux-data")
    env["PSMUX_NO_WARM"] = "1"
    payload = tmp_path / "slow-child.py"
    payload.write_text(
        "import subprocess,sys,time\n"
        "from agent_procutil import no_window_kwargs\n"
        "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],"
        "**no_window_kwargs())\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    env["PYTHONPATH"] = os.pathsep.join(sys.path)
    from worktree_manager import mux_child_process
    real_spawn = mux_child_process.spawn_sync_in_kill_on_close_job
    launched: list[subprocess.Popen] = []
    owned: set[int] = set()

    def spawn(argv: list[str], **kwargs: object) -> tuple[subprocess.Popen, object]:
        process, job = real_spawn(
            [os.environ["PSMUX_TEST_BIN"], "run-shell",
             subprocess.list2cmdline([sys.executable, str(payload)])],
            env=env, **kwargs,
        )
        launched.append(process)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            table = processes()
            owned.update(descendants(process.pid, table))
            if sum(table.get(pid, ("", 0))[0].lower() == "python.exe" for pid in owned) >= 2:
                break
            time.sleep(.02)
        else:
            pytest.fail("PSMux timeout fixture did not launch both Python descendants")
        return process, job

    monkeypatch.setattr(mux_child_process, "spawn_sync_in_kill_on_close_job", spawn)
    real_run = mux_daemon.run_mux_child
    monkeypatch.setattr(
        mux_daemon, "run_mux_child",
        lambda argv, **kwargs: real_run(argv, **{**kwargs, "timeout": .1}),
    )
    if probe:
        assert not mux_daemon._mux_session_alive("fixture-mux", "fixture")
    else:
        assert not mux_daemon.apply_status_options(
            {"mux_bin": "fixture-mux", "mux_session": "fixture"}, {"@fixture": "value"},
        )
    assert launched and launched[0].poll() is not None
    deadline = time.monotonic() + 3
    while owned & processes().keys() and time.monotonic() < deadline:
        time.sleep(.02)
    assert not (owned & processes().keys()), "timed-out PSMux descendants leaked"
