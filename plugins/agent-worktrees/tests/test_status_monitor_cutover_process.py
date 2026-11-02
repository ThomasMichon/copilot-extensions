"""Process-level rehearsal for the real status-monitor drain/cutover path."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest
from agent_worktrees import locks
from agent_worktrees import status_monitor_cutover as smc

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes


def _write_fake_mux(fake_bin: Path) -> None:
    script = """#!/usr/bin/env bash
set -eu
case "${1:-}" in
  list-sessions) printf 'wt-test:0:@1:1\\n' ;;
  has-session|set-option) exit 0 ;;
  *) exit 0 ;;
esac
"""
    for name in ("tmux", "psmux"):
        path = fake_bin / name
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)


def _wait_for(predicate, *, timeout: float = 20.0, interval: float = 0.05, message: str):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError(message)


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _active_pid(routing_path: Path) -> int | None:
    if not routing_path.exists():
        return None
    active = _read_json(routing_path).get("active")
    if not isinstance(active, dict):
        return None
    pid = active.get("pid")
    return int(pid) if isinstance(pid, int) else None


def _send_delayed_hook(lock_path: Path, *, delay_s: float) -> dict:
    data = _read_json(lock_path)
    host, port_s = str(data["hook_endpoint"]).split(":", 1)
    body = json.dumps(
        {
            "version": 1,
            "token": data["hook_token"],
            "kind": "preToolUse",
            "payload": {"__test_delay_s": delay_s},
            "deadline": time.time() + 30.0,
        },
        separators=(",", ":"),
    ).encode("utf-8") + b"\n"
    with socket.create_connection((host, int(port_s)), timeout=10) as sock:
        sock.settimeout(10)
        sock.sendall(body)
        sock.shutdown(socket.SHUT_WR)
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = sock.recv(65536)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode("utf-8"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX process rehearsal")
@pytest.mark.timeout(180)
def test_real_status_monitor_cutover_drains_live_classify_request(monkeypatch) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="aw-cutover-process-"))
    home = tmp / "home"
    fake_bin = tmp / "fake-bin"
    fake_bin.mkdir()
    _write_fake_mux(fake_bin)
    tracked = tmp / "tracked"
    tracked.mkdir()
    subprocess.run(["git", "init", "-q", str(tracked)], check=True)

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["AGENT_HOME"] = str(home)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["AGENT_WORKTREES_STATUS_MONITOR"] = "1"
    env["PYTEST_CURRENT_TEST"] = "status-monitor-cutover-process"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("AGENT_HOME", str(home))
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setenv("AGENT_WORKTREES_STATUS_MONITOR", "1")
    monkeypatch.setenv("PYTEST_CURRENT_TEST", env["PYTEST_CURRENT_TEST"])
    # Never spawn a REAL, 120s-delayed detached subprocess during this test
    # (copilot-extensions#5453 async backstop) -- it has its own dedicated
    # unit tests, and this test's tmp HOME is torn down long before any
    # such process would fire.
    monkeypatch.setattr(
        "agent_worktrees.status_monitor_reap_stale.schedule_delayed_daemon_health_reap",
        lambda *a, **k: None,
    )

    install_dir = home / ".agent-worktrees"
    registry = install_dir / "status-monitor.d"
    registry.mkdir(parents=True, exist_ok=True)
    (registry / "wt-test").write_text(str(tracked), encoding="utf-8")
    lock_path = install_dir / "status-monitor.lock"
    routing_path = install_dir / "status-monitor-routing" / "active.json"

    monitor = subprocess.Popen(  # noqa: S603
        [sys.executable, "-m", "agent_worktrees", "status-monitor", "--interval", "1"],
        cwd=tmp,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pids_to_kill = {monitor.pid}
    try:
        _wait_for(
            lambda: lock_path.exists() and "hook_endpoint" in _read_json(lock_path),
            message="status-monitor never published its hook endpoint",
        )
        old_pid = _wait_for(
            lambda: _active_pid(routing_path),
            message="status-monitor never published its routed endpoint",
        )
        pids_to_kill.add(old_pid)
        hook_response: dict[str, object] = {}
        hook_thread = threading.Thread(
            target=lambda: hook_response.update(_send_delayed_hook(lock_path, delay_s=3.0)),
            daemon=True,
        )
        hook_thread.start()
        time.sleep(0.3)

        result: dict[str, object] = {}
        cutover_thread = threading.Thread(
            target=lambda: result.update(smc.activate_after_update(runtime_python=sys.executable)),
            daemon=True,
        )
        cutover_thread.start()

        new_pid = _wait_for(
            lambda: (pid := _active_pid(routing_path)) and pid != old_pid and pid,
            message="cutover never published a successor route",
        )
        pids_to_kill.add(new_pid)
        assert hook_thread.is_alive(), "delayed hook finished before the route flipped"
        cutover_thread.join(timeout=20)
        assert not cutover_thread.is_alive(), "cutover thread did not finish"
        assert result["action"] == "cutover", result
        assert result["result"]["ok"] is True
        hook_thread.join(timeout=20)
        assert not hook_thread.is_alive(), "delayed hook did not drain to completion"
        assert hook_response.get("version") == 1
        assert locks.pid_alive(new_pid)
        new_lock = _read_json(lock_path)
        assert "hook_endpoint" in new_lock and "classify_endpoint" in new_lock
        drain_events = [
            json.loads(line)
            for line in (routing_path.parent / "lifecycle.log").read_text().splitlines()
            if json.loads(line).get("action") == "monitor-drain"
        ]
        assert any(
            event["detail"]["control_action"] == "promote"
            and event["detail"]["phase"] == "complete"
            for event in drain_events
        )
        assert any(
            event["detail"]["role"] == "daemon"
            and event["detail"]["control_action"] == "drain"
            and event["detail"]["phase"] == "complete"
            for event in drain_events
        )
    finally:
        for pid in pids_to_kill:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


@pytest.mark.timeout(180)
def test_install_process_returns_before_resident_finishes_accepted_work(tmp_path):
    from agent_procutil import no_window_kwargs
    from agent_worktrees import classify_daemon
    from agent_worktrees.status_monitor_cli import _SELF_RETIRE_MAX_GRACE_S
    from zdd import diagnostics, routing

    home = tmp_path / "home"
    home.mkdir()
    root = home / ".agent-worktrees"
    started, release = tmp_path / "started", tmp_path / "release"
    observed = tmp_path / "resident-observed-generation"
    output = tmp_path / "result.json"
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), AGENT_HOME=str(home),
               AGENT_WORKTREES_STATUS_MONITOR="1")
    for key in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
                "AGENT_WORKTREES_PAYLOAD_ROOT", "AGENT_WORKTREES_STATUS_MONITOR_ROUTING_DIR"):
        env.pop(key, None)
    baseline = """
import json, runpy, sys, threading
from pathlib import Path
from agent_worktrees import classify_daemon, self_retire
started, release, observed = map(Path, sys.argv[1:])
is_superseded = self_retire.is_superseded
def observe(*args, **kwargs):
    result = is_superseded(*args, **kwargs)
    observed.write_text(json.dumps({"superseded": result, "generation": args[2]}))
    return result
self_retire.is_superseded = observe
original = classify_daemon.start_server
def start_server(compute, **kwargs):
    def held(kind, payload):
        if payload.get("async_install_probe"):
            started.touch()
            for _ in range(1200):
                if release.exists():
                    return {"completed": True}
                threading.Event().wait(0.1)
            raise TimeoutError("probe was not released")
        return compute(kind, payload)
    return original(held, **kwargs)
classify_daemon.start_server = start_server
sys.argv = ["agent_worktrees", "status-monitor", "--interval", "1"]
runpy.run_module("agent_worktrees", run_name="__main__", alter_sys=True)
"""
    installation = """
import json, os, sys
from contextlib import ExitStack
from pathlib import Path
from agent_worktrees import status_monitor_cutover as cutover, status_monitor_runtime as runtime
# Preserve this source packet's import closure for the actual resident child.
runtime._daemon_environment = lambda: dict(os.environ)
with ExitStack() as custody:
    guard = cutover._acquire_cutover_lock(cutover.routing_dir())
    custody.callback(guard.release)
    result = cutover.activate_after_update(
        runtime_python=sys.executable, cutover_guard=guard,
        spawned_handles=[], transition_custody=custody, asynchronous_retirement=True,
    )
    Path(sys.argv[1]).write_text(json.dumps(result), encoding="utf-8")
"""
    identities = {}
    request_result = {}
    requester = None
    demand = None
    demand_thread = None
    demand_stop = threading.Event()
    with (tmp_path / "process.log").open("w", encoding="utf-8") as log:
        old = subprocess.Popen(
            [sys.executable, "-B", "-c", baseline, str(started), str(release), str(observed)],
            env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, **no_window_kwargs(),
        )
        try:
            record = _wait_for(
                lambda: (
                    value if "classify_endpoint" in (
                        value := locks.read_lock(root / "status-monitor.lock") or {}
                    ) else None
                ), message="baseline did not publish its request endpoint",
            )
            old_pid = record["pid"]
            identities[old_pid] = diagnostics.process_start_time(old_pid)
            _wait_for(observed.exists, message="baseline did not observe its routed generation")
            demand_host, demand_port = record["worktree_status_endpoint"].split(":")
            demand = (demand_host, int(demand_port), record["worktree_status_token"])
            classify_daemon.wcs_client.subscribe(*demand, "async-install-demand")

            def keep_demand():
                while not demand_stop.wait(1):
                    try:
                        classify_daemon.wcs_client.subscribe(*demand, "async-install-demand", timeout=2)
                    except classify_daemon.wcs_client.DaemonUnavailable:
                        return

            demand_thread = threading.Thread(target=keep_demand, daemon=True)
            demand_thread.start()
            host, port, token = classify_daemon.endpoint_from_rendezvous(record)

            def request():
                try:
                    request_result.update(classify_daemon.wcs_client.request(
                        host, port, token, kind=classify_daemon.KIND, key="async-install-probe",
                        payload={"async_install_probe": True}, request_deadline_s=120,
                    ))
                except classify_daemon.wcs_client.DaemonUnavailable as error:
                    request_result["error"] = str(error)

            requester = threading.Thread(target=request, daemon=True)
            requester.start()
            _wait_for(started.exists, message="baseline did not accept the held request")
            installed = subprocess.run(
                [sys.executable, "-B", "-c", installation, str(output)],
                env=env, cwd=tmp_path, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, timeout=60, **no_window_kwargs(),
            )
            assert installed.returncode == 0, (tmp_path / "process.log").read_text()
            result = _read_json(output)["result"]
            assert result["ok"] and result["committed"] and result["retirement_pending"]
            assert requester.is_alive() and old.poll() is None, request_result
            successor = routing.read_active_endpoint(root / "status-monitor-routing")
            assert successor is not None and successor.pid != old_pid
            identities[successor.pid] = diagnostics.process_start_time(successor.pid)
            health = smc.ControlClient(
                successor.base_url, token=(root / "status-monitor-control.token").read_text().strip(),
            ).health()
            assert health["published"] is True and health["status"] == "ready"
            current = locks.read_lock(root / "status-monitor.lock")
            assert current["pid"] == successor.pid
            new_host, new_port = current["worktree_status_endpoint"].split(":")
            new_demand = (new_host, int(new_port), current["worktree_status_token"])
            classify_daemon.wcs_client.subscribe(*new_demand, "async-install-demand")
            previous_demand, demand = demand, new_demand
            classify_daemon.wcs_client.release(*previous_demand, "async-install-demand")
            release.touch()
            requester.join(timeout=15)
            assert not requester.is_alive() and request_result.get("completed") is True
            assert old.wait(timeout=_SELF_RETIRE_MAX_GRACE_S + 15) == 0
        except (AssertionError, subprocess.TimeoutExpired) as error:
            log.flush()
            observation = observed.read_text() if observed.exists() else "unobserved"
            table = routing.read_table(root / "status-monitor-routing")
            health = {}
            for label in ("active", "previous"):
                endpoint = (table or {}).get(label)
                if endpoint:
                    try:
                        health[label] = smc.ControlClient(
                            routing.Endpoint.from_dict(endpoint).base_url,
                            token=(root / "status-monitor-control.token").read_text().strip(),
                        ).health()
                    except (OSError, ValueError) as unavailable:
                        health[label] = str(unavailable)
            pytest.fail(
                f"{error}\nretirement={observation}\nhealth={health}\n"
                f"{(tmp_path / 'process.log').read_text(encoding='utf-8')}"
            )
        finally:
            release.touch()
            demand_stop.set()
            if demand_thread is not None:
                demand_thread.join(timeout=5)
            if demand is not None:
                classify_daemon.wcs_client.release(*demand, "async-install-demand")
            if requester is not None:
                requester.join(timeout=15)
            for pid, identity in identities.items():
                if identity is not None:
                    diagnostics.terminate_pid_if_identity(pid, identity, exit_timeout=10)
            if old.poll() is None:
                old.terminate()
            old.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-only windowless-parent contract")
def test_spawn_passive_is_windowless_from_pythonw_parent(tmp_path: Path) -> None:
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    python = Path(sys.executable).with_name("python.exe")
    if not pythonw.is_file():
        pytest.skip("pythonw.exe is unavailable")
    if not python.is_file():
        pytest.skip("python.exe is unavailable")
    parent = tmp_path / "windowless_parent.py"
    output = tmp_path / "result.json"
    parent.write_text(
        "import json, os, sys, time\n"
        "from pathlib import Path\n"
        "from agent_worktrees import status_monitor_cutover as smc\n"
        "from agent_worktrees import status_monitor_runtime as smr\n"
        "root = Path(sys.argv[1])\n"
        "runtime_python = str(Path(sys.argv[3]))\n"
        "smr._daemon_environment = lambda: dict(os.environ)\n"
        "smr._daemon_cwd = lambda: str(root)\n"
        "pids = []\n"
        "for port in (49151, 49152):\n"
        " handle = smc.spawn_passive(runtime_python, port=port)\n"
        " pids.append(getattr(handle, 'pid', None))\n"
        " time.sleep(2.5)\n"
        " handle.terminate()\n"
        " time.sleep(0.5)\n"
        "Path(sys.argv[2]).write_text(json.dumps(pids), encoding='utf-8')\n",
        encoding="utf-8",
    )
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = wintypes.HWND
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]

    def visible_windows():
        windows = set()

        @callback_type
        def visit(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                windows.add(hwnd)
            return True

        user32.EnumWindows(visit, 0)
        return windows

    def process_name(hwnd):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        handle = kernel32.OpenProcess(0x1000, False, pid.value)
        if not handle:
            return ""
        try:
            length = wintypes.DWORD(32768)
            name = ctypes.create_unicode_buffer(length.value)
            if kernel32.QueryFullProcessImageNameW(handle, 0, name, ctypes.byref(length)):
                return Path(name.value).name.lower()
            return ""
        finally:
            kernel32.CloseHandle(handle)

    baseline = visible_windows()
    foreground = user32.GetForegroundWindow()
    process_names = {
        "python.exe", "pythonw.exe", "powershell.exe", "conhost.exe",
        "openconsole.exe", "windowsterminal.exe",
    }
    surfaced = set()
    focus_changes = set()
    parent_log = tmp_path / "windowless-parent.log"
    with parent_log.open("w", encoding="utf-8") as stream, subprocess.Popen(
        [str(pythonw), "-I", str(parent), str(tmp_path), str(output), str(python)],
        stdout=stream, stderr=stream,
    ) as proc:
        deadline = time.monotonic() + 25
        while proc.poll() is None and time.monotonic() < deadline:
            surfaced.update(
                hwnd for hwnd in visible_windows() - baseline
                if process_name(hwnd) in process_names
            )
            current = user32.GetForegroundWindow()
            if current != foreground and current not in baseline and process_name(current) in process_names:
                focus_changes.add(current)
            time.sleep(0.02)
        if proc.poll() is None:
            proc.terminate()
            pytest.fail("windowless passive-monitor probe exceeded its deadline")
        assert proc.returncode == 0, parent_log.read_text(encoding="utf-8", errors="replace")
    pids = json.loads(output.read_text(encoding="utf-8"))
    assert len(pids) == 2
    assert all(isinstance(pid, int) and pid > 0 for pid in pids)
    assert surfaced == focus_changes == set()
