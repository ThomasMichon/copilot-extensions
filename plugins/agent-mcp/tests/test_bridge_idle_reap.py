"""End-to-end: the stdio ``agent-mcp bridge`` self-reaps when idle (#3876).

Nothing today closes a leaked bridge's stdin or kills its (still-live) parent
once the sub-agent that spawned it finishes, so the existing stdin-EOF and
parent-death defenses in :meth:`agent_mcp.bridge.Bridge.run` never fire and
the process leaks for the life of the top-level session. These tests drive
the real subprocess with a short ``idle_timeout`` and verify: (1) it exits
itself once idle, without stdin ever closing; (2) it is never reaped while a
request is still in flight, however long that request takes; (3) a
non-positive ``idle_timeout`` disables self-reap entirely (today's behavior).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
import time

UPSTREAM = textwrap.dedent(
    """
    import sys, json, time
    def reply(o):
        sys.stdout.write(json.dumps(o) + "\\n"); sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        m = json.loads(line); mid = m.get("id"); method = m.get("method")
        if method == "tools/call" and m["params"]["name"] == "sleep":
            time.sleep(float(m["params"]["arguments"]["seconds"]))
            reply({"jsonrpc":"2.0","id":mid,"result":{"content":[],"isError":False}})
        elif mid is not None:
            reply({"jsonrpc":"2.0","id":mid,"result":{}})
    """
)


def _write_cfg(tmp_path, *, idle_timeout):
    upstream = tmp_path / "upstream.py"
    upstream.write_text(UPSTREAM, encoding="utf-8")
    cfg = tmp_path / "bridge.yaml"
    cfg.write_text(json.dumps({
        "server": {"type": "stdio", "command": [sys.executable, str(upstream)]},
        "idle_timeout": idle_timeout,
    }), encoding="utf-8")
    return cfg


def _spawn(cfg_path):
    env = dict(os.environ, AGENT_MCP_NO_MULTIPLEX="1")
    return subprocess.Popen(
        [sys.executable, "-m", "agent_mcp", "--log-level", "info",
         "bridge", "--config", str(cfg_path)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env,
    )


def test_idle_bridge_self_reaps_without_stdin_closing(tmp_path):
    cfg = _write_cfg(tmp_path, idle_timeout=0.3)
    proc = _spawn(cfg)
    try:
        proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n")
        proc.stdin.flush()
        proc.stdout.readline()  # the ping response

        # stdin is deliberately kept open (never closed): the process must
        # exit on its own from the idle timer, not from EOF.
        returncode = proc.wait(timeout=10)
        assert returncode == 0
        assert "self-reaping" in proc.stderr.read()
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_in_flight_request_is_never_reaped_mid_dispatch(tmp_path):
    # idle_timeout is much shorter than the upstream call's sleep: if the
    # self-reap ignored in-flight dispatch, the bridge would exit and the
    # slow reply would never come back.
    cfg = _write_cfg(tmp_path, idle_timeout=0.2)
    proc = _spawn(cfg)
    try:
        proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "sleep", "arguments": {"seconds": 1.5}}}) + "\n")
        proc.stdin.flush()
        start = time.monotonic()
        line = proc.stdout.readline()
        elapsed = time.monotonic() - start
        assert elapsed >= 1.4, "reply arrived suspiciously early"
        resp = json.loads(line)
        assert resp["result"]["isError"] is False

        proc.stdin.close()
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)


def test_zero_idle_timeout_disables_self_reap(tmp_path):
    cfg = _write_cfg(tmp_path, idle_timeout=0)
    proc = _spawn(cfg)
    try:
        proc.stdin.write(json.dumps(
            {"jsonrpc": "2.0", "id": 1, "method": "ping"}) + "\n")
        proc.stdin.flush()
        proc.stdout.readline()

        time.sleep(1.0)  # would have been well past any short idle window
        assert proc.poll() is None, "bridge exited despite idle_timeout=0"

        proc.stdin.close()
        assert proc.wait(timeout=10) == 0
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=5)
