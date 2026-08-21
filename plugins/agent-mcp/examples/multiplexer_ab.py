#!/usr/bin/env python3
"""A/B: process count + resident memory of N MCP sessions -- direct vs multiplexer.

Brings up ``N`` MCP bridge sessions against a trivial stdio-echo upstream two
ways and compares their footprint:

* **direct** -- one full ``agent-mcp bridge`` interpreter per session (today's
  model): each parses config, builds the credential injectors + decorator
  pipeline, and connects its own upstream.
* **multiplexer** -- one shared ``agent-mcp serve`` session-host plus ``N`` thin
  ``agent-mcp forward`` children: the host owns the upstream + pipeline per
  session; each forwarder is a near-empty interpreter that only pumps bytes.

Each session is driven through ``initialize`` + ``tools/list`` (so the upstream
is actually connected), then, with every session held open, the script sums the
resident-set size (``VmRSS``) and counts the live processes in the agent-mcp
process forest for each mode and prints the delta.

Linux-only: reads ``/proc/<pid>/status`` and ``/proc/<pid>/cmdline``. Run with a
Python that can ``import agent_mcp`` (e.g. the plugin's installed venv)::

    python examples/multiplexer_ab.py --sessions 7
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import defaultdict
from pathlib import Path

# A minimal stdio MCP upstream: answers initialize / tools/list / tools/call.
_ECHO_CHILD = (
    "import sys,json\n"
    "for line in sys.stdin:\n"
    "    line=line.strip()\n"
    "    if not line: continue\n"
    "    m=json.loads(line); mid=m.get('id'); method=m.get('method')\n"
    "    if mid is None: continue\n"
    "    if method=='initialize':\n"
    "        r={'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'echo'}}\n"
    "    elif method=='tools/list':\n"
    "        r={'tools':[{'name':'echo','description':'d','inputSchema':{'type':'object'}}]}\n"
    "    else:\n"
    "        r={}\n"
    "    sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,'result':r})+'\\n')\n"
    "    sys.stdout.flush()\n"
)

_INIT = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {}}) + "\n"
_LIST = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                    "params": {}}) + "\n"


def _write_bridge_config(dirpath: Path) -> Path:
    cfg = dirpath / "echo.mcp.yaml"
    cfg.write_text(
        "server:\n  type: stdio\n  command:\n"
        f"    - {sys.executable}\n    - '-c'\n    - |\n"
        + "".join("      " + ln + "\n" for ln in _ECHO_CHILD.splitlines())
        + "auth:\n  kind: none\n",
        encoding="utf-8",
    )
    return cfg


def _read_procs() -> dict[int, tuple[int, str, int]]:
    """Map pid -> (ppid, cmdline, VmRSS_kB) for every readable process."""
    out: dict[int, tuple[int, str, int]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", "replace").strip()
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ppid = 0
        rss = 0
        for line in status.splitlines():
            if line.startswith("PPid:"):
                ppid = int(line.split()[1])
            elif line.startswith("VmRSS:"):
                rss = int(line.split()[1])
        out[pid] = (ppid, cmd, rss)
    return out


def _footprint(roots: set[int]) -> tuple[int, int]:
    """(#processes, total VmRSS kB) of ``roots`` plus all their descendants.

    Scoped strictly to the given root pids (the sessions this run spawned, plus
    its own serve host) so a shared host running *other* agent-mcp sessions can't
    pollute the measurement.
    """
    procs = _read_procs()
    children: dict[int, list[int]] = defaultdict(list)
    for pid, (ppid, _c, _r) in procs.items():
        children[ppid].append(pid)
    seen: set[int] = set()
    stack = [p for p in roots if p in procs]
    while stack:
        p = stack.pop()
        if p in seen:
            continue
        seen.add(p)
        stack.extend(children.get(p, []))
    total = sum(procs[p][2] for p in seen if p in procs)
    return len(seen), total


def _find_serve_host(socket_path: str) -> int | None:
    """The pid of *our* serve host, identified by our socket path in its argv."""
    for pid, (_ppid, cmd, _rss) in _read_procs().items():
        if "agent_mcp" in cmd and "serve" in cmd and socket_path in cmd:
            return pid
    return None


def _drive(proc: subprocess.Popen) -> None:
    """Send initialize + tools/list and read both replies (upstream connects)."""
    if not (proc.stdin and proc.stdout):
        return
    proc.stdin.write(_INIT + _LIST)
    proc.stdin.flush()
    for _ in range(2):
        line = proc.stdout.readline()
        if not line:
            break


def _spawn(cmd: list[str], env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, env=env)


def _run_mode(mode: str, sessions: int, cfg: Path, home: Path) -> tuple[int, int]:
    env = dict(os.environ)
    env["AGENT_MCP_HOME"] = str(home)
    env["AGENT_MCP_SERVE_SOCKET"] = str(home / "serve.sock")
    env["AGENT_MCP_PARENT_WATCHDOG"] = "0"
    if mode == "direct":
        verb = ["bridge", str(cfg)]
        env["AGENT_MCP_NO_MULTIPLEX"] = "1"
    else:
        verb = ["forward", str(cfg)]
        env.pop("AGENT_MCP_NO_MULTIPLEX", None)
        env.pop("AGENT_MCP_NO_SERVE", None)
        env.pop("AGENT_MCP_NO_ENSURE_SERVE", None)
    procs = []
    try:
        for _ in range(sessions):
            p = _spawn([sys.executable, "-m", "agent_mcp", *verb], env)
            _drive(p)
            procs.append(p)
        time.sleep(1.0)  # let the serve host (multiplex) settle + RSS stabilize
        roots = {p.pid for p in procs}
        host = _find_serve_host(env["AGENT_MCP_SERVE_SOCKET"])
        if host is not None:
            roots.add(host)
        return _footprint(roots)
    finally:
        for p in procs:
            _stop(p)
        # Best-effort: stop a spawned serve host so runs don't accumulate.
        _shutdown_serve(env)
        time.sleep(0.3)


def _stop(proc: subprocess.Popen) -> None:
    with contextlib.suppress(Exception):
        if proc.stdin:
            proc.stdin.close()
    with contextlib.suppress(Exception):
        proc.terminate()
    with contextlib.suppress(Exception):
        proc.wait(timeout=3)


def _shutdown_serve(env: dict) -> None:
    with contextlib.suppress(Exception):
        from agent_mcp import ipc
        sock = ipc.serve_socket_if_available(env.get("AGENT_MCP_SERVE_SOCKET"))
        if sock is not None:
            import asyncio
            asyncio.run(ipc.request_via_socket(sock, {"op": "shutdown"}))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sessions", type=int, default=7,
                    help="number of concurrent MCP sessions per mode (default 7)")
    args = ap.parse_args(argv)

    if not Path("/proc").is_dir():
        print("multiplexer_ab: Linux/proc required for the RSS measurement",
              file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cfg = _write_bridge_config(root)
        direct = _run_mode("direct", args.sessions, cfg, root / "home-direct")
        multi = _run_mode("multiplex", args.sessions, cfg, root / "home-multi")

    def _fmt(label: str, count: int, rss_kb: int) -> str:
        return f"  {label:<11} processes={count:<4} RSS={rss_kb / 1024:8.1f} MiB"

    print(f"agent-mcp multiplexer A/B  ({args.sessions} sessions)")
    print(_fmt("direct", *direct))
    print(_fmt("multiplex", *multi))
    d_cnt, d_rss = direct
    m_cnt, m_rss = multi
    if d_rss and m_rss:
        print(f"  delta        processes {d_cnt}->{m_cnt} "
              f"({d_cnt - m_cnt} fewer); "
              f"RSS {(1 - m_rss / d_rss) * 100:.0f}% lower "
              f"({(d_rss - m_rss) / 1024:.1f} MiB saved)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
