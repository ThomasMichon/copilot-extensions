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
    try:
        _wait_for(
            lambda: lock_path.exists() and "hook_endpoint" in _read_json(lock_path),
            message="status-monitor never published its hook endpoint",
        )
        old_pid = _wait_for(
            lambda: _active_pid(routing_path),
            message="status-monitor never published its routed endpoint",
        )
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
        assert hook_thread.is_alive(), "delayed hook finished before the route flipped"
        cutover_thread.join(timeout=20)
        assert not cutover_thread.is_alive(), "cutover thread did not finish"
        assert result["action"] == "cutover", result
        assert result["result"]["ok"] is True
        hook_thread.join(timeout=20)
        assert not hook_thread.is_alive(), "delayed hook did not drain to completion"
        assert hook_response.get("version") == 1
        assert locks.pid_alive(new_pid)
    finally:
        try:
            os.kill(monitor.pid, signal.SIGTERM)
        except OSError:
            pass
