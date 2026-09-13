#!/usr/bin/env python3
"""Portable, stdlib-only process-topology probe for the agent-mcp multiplexer.

Follow-up from the ``plugin-process-hygiene`` effort's #744 multiplexer work
(#866): the RAM win is already proven by ``examples/multiplexer_ab.py`` (a
Linux ``/proc`` A/B) and the multiplexer is default-on, but the effort's
validation plan also calls for a clean-room scenario asserting the process
**topology** deterministically -- not just a footprint delta.

Reuses the same echo-upstream/spawn/drive/``/proc``-census techniques as
``examples/multiplexer_ab.py`` (same repo, same conventions), extended with
three checks that print ``PROBE: <name> PASS|FAIL <detail>`` (the same
convention ``agent-bridge-cutover``'s ``cutover_probe.py`` uses):

  topology-collapse   N ``agent-mcp forward`` sessions against one shared
                       upstream config collapse onto **exactly one** resident
                       ``serve`` host plus N thin forwarders (never N heavy
                       bridges) -- and each session still answers
                       ``initialize``/``tools/list`` correctly through it.
  direct-fallback      With ``AGENT_MCP_NO_MULTIPLEX=1``, N sessions run as N
                       independent direct bridges with **no** resident host --
                       the always-optional inline fallback, unconditionally.
  idle-self-eviction   A ``serve`` host started with a short ``--idle-timeout``
                       evicts itself once its last attached session detaches
                       (never lingers as an orphaned daemon).

Linux-only (``/proc``), like ``multiplexer_ab.py``. Run with a Python that can
``import agent_mcp`` (the plugin's installed venv)::

    python multiplexer_topology_probe.py --python <agent-mcp-venv-python> \\
        --checks topology-collapse,direct-fallback,idle-self-eviction

Exit 0 iff every selected check PASSes.
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
from pathlib import Path

ALL_CHECKS = ["topology-collapse", "direct-fallback", "idle-self-eviction"]
_SESSIONS = 4

# A minimal stdio MCP upstream: answers initialize / tools/list / tools/call --
# identical to examples/multiplexer_ab.py's echo child, so both probes exercise
# the same real upstream shape.
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

_INIT = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}) + "\n"
_LIST = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}) + "\n"


class Result:
    def __init__(self, name: str):
        self.name = name
        self.ok = True
        self.detail: list[str] = []

    def check(self, cond, msg) -> bool:
        if not cond:
            self.ok = False
            self.detail.append("FAILED: " + msg)
        else:
            self.detail.append("ok: " + msg)
        return cond

    def emit(self) -> bool:
        status = "PASS" if self.ok else "FAIL"
        summary = "; ".join(
            self.detail[-3:] if self.ok else [d for d in self.detail if d.startswith("FAILED")]
        )
        print(f"PROBE: {self.name} {status} {summary}")
        return self.ok


def _write_bridge_config(dirpath: Path, python: str) -> Path:
    cfg = dirpath / "echo.mcp.yaml"
    cfg.write_text(
        "server:\n  type: stdio\n  command:\n"
        f"    - {python}\n    - '-c'\n    - |\n"
        + "".join("      " + ln + "\n" for ln in _ECHO_CHILD.splitlines())
        + "auth:\n  kind: none\n",
        encoding="utf-8",
    )
    return cfg


def _read_procs() -> dict[int, tuple[int, str]]:
    """Map pid -> (ppid, cmdline) for every readable process."""
    out: dict[int, tuple[int, str]] = {}
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        try:
            cmd = (
                (entry / "cmdline")
                .read_bytes()
                .replace(b"\0", b" ")
                .decode("utf-8", "replace")
                .strip()
            )
            status = (entry / "status").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ppid = 0
        for line in status.splitlines():
            if line.startswith("PPid:"):
                ppid = int(line.split()[1])
                break
        out[pid] = (ppid, cmd)
    return out


def _find_serve_host(socket_path: str) -> int | None:
    """The pid of *our* serve host, identified by our socket path in its argv."""
    for pid, (_ppid, cmd) in _read_procs().items():
        if "agent_mcp" in cmd and "serve" in cmd and socket_path in cmd:
            return pid
    return None


def _stray_agent_mcp_count(exclude_pids: set[int], *, settle_s: float = 3.0) -> tuple[int, list[str]]:
    """Poll for stray ``agent_mcp`` processes to settle, tolerating teardown lag.

    Returns ``(count, cmdlines)`` from the LAST poll -- a transient process mid
    self-exit (e.g. a just-``shutdown``-requested host still unwinding
    ``asyncio.run``) should not fail the check if it is gone moments later.
    """
    deadline = time.monotonic() + settle_s
    count, cmdlines = 0, []
    while True:
        strays = [
            cmd
            for pid, (_ppid, cmd) in _read_procs().items()
            if "agent_mcp" in cmd and pid not in exclude_pids
        ]
        count, cmdlines = len(strays), strays
        if count == 0 or time.monotonic() >= deadline:
            return count, cmdlines
        time.sleep(0.25)


def _drive(proc: subprocess.Popen) -> tuple[dict | None, dict | None]:
    """Send initialize + tools/list; return the two parsed replies (or None)."""
    if not (proc.stdin and proc.stdout):
        return None, None
    try:
        proc.stdin.write(_INIT + _LIST)
        proc.stdin.flush()
    except (BrokenPipeError, OSError):
        return None, None
    replies: list[dict | None] = []
    for _ in range(2):
        line = proc.stdout.readline()
        if not line:
            replies.append(None)
            continue
        try:
            replies.append(json.loads(line))
        except ValueError:
            replies.append(None)
    while len(replies) < 2:
        replies.append(None)
    return replies[0], replies[1]


def _spawn(cmd: list[str], env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        text=True, env=env,
    )


def _stop(proc: subprocess.Popen) -> None:
    with contextlib.suppress(Exception):
        if proc.stdin:
            proc.stdin.close()
    with contextlib.suppress(Exception):
        proc.terminate()
    with contextlib.suppress(Exception):
        proc.wait(timeout=3)


def _shutdown_serve(python: str, socket_path: str) -> None:
    """Best-effort: ask a host we spawned to shut down, so runs don't accumulate."""
    code = (
        "import asyncio, sys\n"
        "from agent_mcp import ipc\n"
        f"sock = ipc.serve_socket_if_available({socket_path!r})\n"
        "if sock is not None:\n"
        "    asyncio.run(ipc.request_via_socket(sock, {'op': 'shutdown'}))\n"
    )
    with contextlib.suppress(Exception):
        subprocess.run([python, "-c", code], timeout=5, check=False)


def check_topology_collapse(python: str) -> Result:
    res = Result("topology-collapse")
    with tempfile.TemporaryDirectory(prefix="amtp-tc-") as td:
        root = Path(td)
        cfg = _write_bridge_config(root, python)
        home = root / "home"
        socket_path = str(home / "serve.sock")
        env = dict(os.environ)
        env["AGENT_MCP_HOME"] = str(home)
        env["AGENT_MCP_SERVE_SOCKET"] = socket_path
        env["AGENT_MCP_PARENT_WATCHDOG"] = "0"
        env.pop("AGENT_MCP_NO_MULTIPLEX", None)
        env.pop("AGENT_MCP_NO_SERVE", None)
        env.pop("AGENT_MCP_NO_ENSURE_SERVE", None)
        procs = []
        try:
            replies_ok = True
            for _ in range(_SESSIONS):
                p = _spawn([python, "-m", "agent_mcp", "forward", str(cfg)], env)
                procs.append(p)
                init, listing = _drive(p)
                ok_init = bool(
                    isinstance(init, dict)
                    and init.get("result", {}).get("serverInfo", {}).get("name") == "echo"
                )
                ok_list = bool(
                    isinstance(listing, dict)
                    and any(
                        t.get("name") == "echo"
                        for t in listing.get("result", {}).get("tools", [])
                    )
                )
                replies_ok = replies_ok and ok_init and ok_list
            res.check(replies_ok, "every session's initialize+tools/list answered correctly")

            time.sleep(1.0)  # let the serve host settle before the census
            host_pid = _find_serve_host(socket_path)
            res.check(host_pid is not None, "exactly one resident serve host is discoverable")

            forwarder_pids = {p.pid for p in procs}
            exclude = forwarder_pids | ({host_pid} if host_pid else set())
            stray, stray_cmds = _stray_agent_mcp_count(exclude)
            detail = f"found {stray} extra" + (f": {stray_cmds[:2]}" if stray_cmds else "")
            res.check(
                stray == 0,
                f"no stray agent_mcp processes beyond the host + {_SESSIONS} forwarders ({detail})",
            )
        finally:
            for p in procs:
                _stop(p)
            _shutdown_serve(python, socket_path)
            time.sleep(0.3)
    return res


def check_direct_fallback(python: str) -> Result:
    res = Result("direct-fallback")
    with tempfile.TemporaryDirectory(prefix="amtp-df-") as td:
        root = Path(td)
        cfg = _write_bridge_config(root, python)
        home = root / "home"
        socket_path = str(home / "serve.sock")
        env = dict(os.environ)
        env["AGENT_MCP_HOME"] = str(home)
        env["AGENT_MCP_SERVE_SOCKET"] = socket_path
        env["AGENT_MCP_PARENT_WATCHDOG"] = "0"
        env["AGENT_MCP_NO_MULTIPLEX"] = "1"
        procs = []
        try:
            replies_ok = True
            for _ in range(_SESSIONS):
                p = _spawn([python, "-m", "agent_mcp", "bridge", str(cfg)], env)
                procs.append(p)
                init, listing = _drive(p)
                ok_init = bool(
                    isinstance(init, dict)
                    and init.get("result", {}).get("serverInfo", {}).get("name") == "echo"
                )
                ok_list = bool(
                    isinstance(listing, dict)
                    and any(
                        t.get("name") == "echo"
                        for t in listing.get("result", {}).get("tools", [])
                    )
                )
                replies_ok = replies_ok and ok_init and ok_list
            res.check(
                replies_ok,
                "every direct-bridge session's initialize+tools/list answered correctly",
            )

            time.sleep(0.5)
            host_pid = _find_serve_host(socket_path)
            res.check(host_pid is None, "AGENT_MCP_NO_MULTIPLEX spawned NO resident serve host")
        finally:
            for p in procs:
                _stop(p)
    return res


def check_idle_self_eviction(python: str) -> Result:
    res = Result("idle-self-eviction")
    with tempfile.TemporaryDirectory(prefix="amtp-ie-") as td:
        root = Path(td)
        cfg = _write_bridge_config(root, python)
        home = root / "home"
        home.mkdir(parents=True, exist_ok=True)
        socket_path = str(home / "serve.sock")
        env = dict(os.environ)
        env["AGENT_MCP_HOME"] = str(home)
        env["AGENT_MCP_PARENT_WATCHDOG"] = "0"
        host = subprocess.Popen(
            [python, "-m", "agent_mcp", "serve", "--socket", socket_path, "--idle-timeout", "2"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env,
        )
        fwd: subprocess.Popen | None = None
        try:
            deadline = time.monotonic() + 5.0
            host_pid = None
            while time.monotonic() < deadline:
                host_pid = _find_serve_host(socket_path)
                if host_pid is not None:
                    break
                time.sleep(0.1)
            if not res.check(host_pid is not None, "host bound its socket and is discoverable"):
                return res

            fwd_env = dict(env)
            fwd_env["AGENT_MCP_SERVE_SOCKET"] = socket_path
            fwd = _spawn([python, "-m", "agent_mcp", "forward", str(cfg)], fwd_env)
            init, listing = _drive(fwd)
            res.check(
                isinstance(init, dict) and isinstance(listing, dict),
                "the forwarder attached and exchanged a full session",
            )
            _stop(fwd)  # detach -- starts the host's idle-eviction clock
            fwd = None

            deadline = time.monotonic() + 10.0
            evicted = False
            while time.monotonic() < deadline:
                if _find_serve_host(socket_path) is None:
                    evicted = True
                    break
                time.sleep(0.2)
            res.check(evicted, "host self-evicted after its idle-timeout with no attached session")
        finally:
            if fwd is not None:
                _stop(fwd)
            with contextlib.suppress(Exception):
                host.terminate()
                host.wait(timeout=3)
    return res


CHECKS = {
    "topology-collapse": check_topology_collapse,
    "direct-fallback": check_direct_fallback,
    "idle-self-eviction": check_idle_self_eviction,
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--python", required=True, help="the installed agent-mcp venv python")
    ap.add_argument(
        "--checks", default=",".join(ALL_CHECKS),
        help="comma-separated subset of: " + ",".join(ALL_CHECKS),
    )
    args = ap.parse_args()
    if not Path("/proc").is_dir():
        print("PROBE: environment FAIL Linux/proc required for this probe")
        print("PROBE-SUMMARY: 0/0 passed")
        sys.exit(2)
    selected = [x.strip() for x in args.checks.split(",") if x.strip()]
    failed = 0
    for name in selected:
        fn = CHECKS.get(name)
        if not fn:
            print(f"PROBE: {name} FAIL unknown check")
            failed += 1
            continue
        try:
            if not fn(args.python).emit():
                failed += 1
        except Exception as e:  # a probe crash is a FAIL, not a wedge
            print(f"PROBE: {name} FAIL probe-exception {type(e).__name__}: {e}")
            failed += 1
    print(f"PROBE-SUMMARY: {len(selected) - failed}/{len(selected)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
