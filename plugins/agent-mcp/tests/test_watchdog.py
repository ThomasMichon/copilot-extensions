"""Tests for the parent-death watchdog + descendant reaping (agent_mcp.watchdog).

The unit tests inject a fake ``probe`` so the watchdog logic can be exercised
without actually killing the test's own parent. A POSIX-only integration test
reproduces the real leak shape end to end: a bridge whose launcher dies while its
stdin stays open (so stdin EOF never fires) must still exit via the watchdog.
"""

from __future__ import annotations

import os
import select
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path
from typing import BinaryIO

import pytest

from agent_mcp import watchdog

# --- env helpers ----------------------------------------------------------

def test_env_flag_defaults_and_falsey(monkeypatch):
    monkeypatch.delenv("X_FLAG", raising=False)
    assert watchdog._env_flag("X_FLAG", True) is True
    assert watchdog._env_flag("X_FLAG", False) is False
    for falsey in ("0", "false", "off", "no", "", "  OFF  "):
        monkeypatch.setenv("X_FLAG", falsey)
        assert watchdog._env_flag("X_FLAG", True) is False
    for truthy in ("1", "true", "on", "yes"):
        monkeypatch.setenv("X_FLAG", truthy)
        assert watchdog._env_flag("X_FLAG", False) is True


def test_env_float(monkeypatch):
    monkeypatch.delenv("X_NUM", raising=False)
    assert watchdog._env_float("X_NUM", 5.0) == 5.0
    monkeypatch.setenv("X_NUM", "0.5")
    assert watchdog._env_float("X_NUM", 5.0) == 0.5
    monkeypatch.setenv("X_NUM", "not-a-number")
    assert watchdog._env_float("X_NUM", 5.0) == 5.0  # falls back on parse error


# --- watchdog firing behaviour (injected probe) ---------------------------

def test_watchdog_fires_when_parent_gone(monkeypatch):
    monkeypatch.delenv("AGENT_MCP_PARENT_WATCHDOG", raising=False)
    fired = threading.Event()
    t = watchdog.install_parent_death_watchdog(
        fired.set, probe=lambda: False, interval=0.02, grace=0,
    )
    assert t is not None
    assert fired.wait(2.0), "watchdog did not fire when parent reported gone"
    t.join(timeout=2)
    assert not t.is_alive()


def test_watchdog_quiet_while_parent_alive(monkeypatch):
    monkeypatch.delenv("AGENT_MCP_PARENT_WATCHDOG", raising=False)
    fired = threading.Event()
    alive = threading.Event()
    alive.set()
    t = watchdog.install_parent_death_watchdog(
        fired.set, probe=alive.is_set, interval=0.02, grace=0,
    )
    assert t is not None
    try:
        assert not fired.wait(0.3), "watchdog fired while parent was still alive"
    finally:
        alive.clear()
        t.join(timeout=2)
    assert not t.is_alive()


def test_watchdog_disabled_by_env(monkeypatch):
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")
    fired = threading.Event()
    t = watchdog.install_parent_death_watchdog(
        fired.set, probe=lambda: False, interval=0.02, grace=0,
    )
    assert t is None
    assert not fired.wait(0.2)


def test_watchdog_disabled_by_nonpositive_interval(monkeypatch):
    monkeypatch.delenv("AGENT_MCP_PARENT_WATCHDOG", raising=False)
    fired = threading.Event()
    t = watchdog.install_parent_death_watchdog(
        fired.set, probe=lambda: False, interval=0, grace=0,
    )
    assert t is None
    assert not fired.wait(0.2)


def test_watchdog_probe_exception_is_survived(monkeypatch):
    """A transient probe failure must not trip a false-positive shutdown."""
    monkeypatch.delenv("AGENT_MCP_PARENT_WATCHDOG", raising=False)
    fired = threading.Event()
    stop = threading.Event()
    state = {"calls": 0}

    def flaky() -> bool:
        if stop.is_set():
            return False
        state["calls"] += 1
        raise OSError("transient")

    t = watchdog.install_parent_death_watchdog(
        fired.set, probe=flaky, interval=0.02, grace=0,
    )
    assert t is not None
    try:
        assert not fired.wait(0.3)
        assert state["calls"] > 1, "probe should have been retried after failing"
    finally:
        stop.set()
        t.join(timeout=2)
    assert not t.is_alive()


# --- real platform probe smoke -------------------------------------------

def test_default_probe_reports_live_parent():
    """The real probe must see this test's own (live) parent as alive."""
    probe = watchdog._default_probe()
    assert probe is not None
    assert probe() is True


def test_reap_descendants_on_exit_is_safe():
    # No-op on POSIX (returns False); on Windows arms a kill-on-close job (or
    # gracefully declines). Either way it must not raise.
    result = watchdog.reap_descendants_on_exit()
    assert isinstance(result, bool)
    if sys.platform != "win32":
        assert result is False


# --- end-to-end: launcher dies, stdin stays open, bridge must exit --------

_LAUNCHER = textwrap.dedent(
    """
    import os, subprocess, sys
    stdin_fd = int(os.environ["BRIDGE_STDIN_FD"])
    cfg = os.environ["BRIDGE_CFG"]
    command = [sys.executable, "-m", "agent_mcp", "--log-level", "error",
               "bridge", "--config", cfg]
    delay = float(os.environ["BRIDGE_STARTUP_DELAY"])
    if delay:
        bootstrap = (
            f"import runpy, time; time.sleep({delay}); "
            "runpy.run_module('agent_mcp', run_name='__main__', alter_sys=True)"
        )
        command = [sys.executable, "-c", bootstrap, *command[3:]]
    p = subprocess.Popen(command, stdin=stdin_fd)
    os.close(stdin_fd)
    sys.stdout.write(str(p.pid) + "\\n")
    sys.stdout.flush()
    # Wait for the test's go-ahead, then exit WITHOUT reaping the bridge so it is
    # orphaned with its stdin (held by the test) still open.
    sys.stdin.readline()
    """
)

_UPSTREAM = textwrap.dedent(
    """
    import sys, json
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        m = json.loads(line)
        if m.get("id") is not None:
            sys.stdout.write(json.dumps({"jsonrpc":"2.0","id":m["id"],"result":{}}) + "\\n")
            sys.stdout.flush()
    """
)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_launcher_line(stream: BinaryIO, *, timeout: float = 10.0) -> bytes:
    deadline = time.monotonic() + timeout
    line = bytearray()
    while len(line) < 4096:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([stream], [], [], remaining)[0]:
            pytest.fail("launcher stdout did not provide a complete line before the deadline")
        byte = os.read(stream.fileno(), 1)
        if not byte:
            pytest.fail("launcher stdout ended before a complete line")
        line.extend(byte)
        if byte == b"\n":
            return bytes(line)
    pytest.fail("launcher stdout exceeded the bounded line length")


@pytest.mark.skipif(sys.platform == "win32", reason="select on pipe descriptors is POSIX-only")
@pytest.mark.parametrize("case", ["ready", "eof", "timeout", "too-long"])
def test_launcher_line_read_is_bounded(case: str) -> None:
    reader, writer = os.pipe()
    try:
        with os.fdopen(reader, "rb", buffering=0) as stream:
            if case == "ready":
                os.write(writer, b"ready\n")
                assert _read_launcher_line(stream) == b"ready\n"
            elif case == "eof":
                os.close(writer)
                writer = -1
                with pytest.raises(pytest.fail.Exception, match="ended before"):
                    _read_launcher_line(stream)
            elif case == "timeout":
                with pytest.raises(pytest.fail.Exception, match="deadline"):
                    _read_launcher_line(stream, timeout=0.01)
            else:
                os.write(writer, b"x" * 4096)
                with pytest.raises(pytest.fail.Exception, match="bounded line length"):
                    _read_launcher_line(stream)
    finally:
        if writer >= 0:
            os.close(writer)


@pytest.mark.skipif(sys.platform == "win32", reason="uses POSIX fd passing / getppid reparenting")
@pytest.mark.parametrize("startup_delay", [0.0, 1.0])
def test_bridge_exits_when_orphaned_with_open_stdin(tmp_path: Path, startup_delay: float) -> None:
    upstream = tmp_path / "upstream.py"
    upstream.write_text(_UPSTREAM, encoding="utf-8")
    cfg = tmp_path / "bridge.yaml"
    import json
    cfg.write_text(json.dumps({
        "server": {"type": "stdio", "command": [sys.executable, str(upstream)]},
    }), encoding="utf-8")

    launcher = tmp_path / "launcher.py"
    launcher.write_text(_LAUNCHER, encoding="utf-8")

    # The test holds the write end of the bridge's stdin pipe, so the bridge never
    # sees stdin EOF even after its launcher dies -- only the watchdog can reap it.
    r, w = os.pipe()
    env = dict(os.environ)
    env["BRIDGE_STDIN_FD"] = str(r)
    env["BRIDGE_CFG"] = str(cfg)
    env["BRIDGE_STARTUP_DELAY"] = str(startup_delay)
    env["AGENT_MCP_PARENT_WATCHDOG_INTERVAL"] = "0.2"
    env["AGENT_MCP_PARENT_WATCHDOG_GRACE"] = "3"

    launcher_proc = subprocess.Popen(
        [sys.executable, str(launcher)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, bufsize=0,
        pass_fds=(r,), env=env,
    )
    os.close(r)  # the launcher (and, via it, the bridge) own the read end now
    try:
        assert launcher_proc.stdout is not None and launcher_proc.stdin is not None
        bridge_pid = int(_read_launcher_line(launcher_proc.stdout).strip())
        # Both real bridge paths install the watchdog before processing client input.
        os.write(w, b'{"jsonrpc":"2.0","id":1,"method":"ping"}\n')
        response = json.loads(_read_launcher_line(launcher_proc.stdout))
        assert response == {"jsonrpc": "2.0", "id": 1, "result": {}}
        assert _pid_alive(bridge_pid)

        # Tell the launcher to exit -> the bridge is orphaned, stdin still open.
        launcher_proc.stdin.write(b"go\n")
        launcher_proc.stdin.flush()
        launcher_proc.wait(timeout=10)

        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            if not _pid_alive(bridge_pid):
                break
            time.sleep(0.1)
        assert not _pid_alive(bridge_pid), (
            "orphaned bridge did not exit via the parent-death watchdog"
        )
    finally:
        os.close(w)
        if launcher_proc.poll() is None:
            launcher_proc.kill()
