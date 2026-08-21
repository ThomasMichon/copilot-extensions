from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
import threading
import time

import pytest

from agent_mcp import forward
from agent_mcp.ipc import serve_socket_if_available
from agent_mcp.serve import Server, request_via_socket

# Reuse the stdio-echo child + bridge-config helpers the serve tests use: a
# minimal upstream that answers initialize / tools/list / tools/call (echoing
# arguments back), so a forwarded round-trip is assertable end to end.
_CHILD = (
    "import sys,json\n"
    "for line in sys.stdin:\n"
    "    line=line.strip()\n"
    "    if not line: continue\n"
    "    m=json.loads(line)\n"
    "    mid=m.get('id'); method=m.get('method')\n"
    "    if mid is None: continue\n"
    "    if method=='initialize':\n"
    "        r={'protocolVersion':'2025-06-18','capabilities':{},"
    "'serverInfo':{'name':'echo'}}\n"
    "    elif method=='tools/list':\n"
    "        r={'tools':[{'name':'echo','description':'d','inputSchema':{'type':'object'}}]}\n"
    "    elif method=='tools/call':\n"
    "        a=m.get('params',{}).get('arguments',{})\n"
    "        r={'content':[{'type':'text','text':json.dumps(a)}]}\n"
    "    else:\n"
    "        r={}\n"
    "    sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,'result':r})+'\\n')\n"
    "    sys.stdout.flush()\n"
)


def _write_bridge_config(tmp_path, name="echo.mcp.yaml"):
    bridge = tmp_path / name
    bridge.write_text(
        "server:\n  type: stdio\n  command:\n"
        f"    - {sys.executable}\n    - '-c'\n    - |\n"
        + "".join("      " + ln + "\n" for ln in _CHILD.splitlines())
        + "auth:\n  kind: none\n",
        encoding="utf-8",
    )
    return bridge


async def _await_socket(sock):
    for _ in range(50):
        if serve_socket_if_available(str(sock)):
            return
        await asyncio.sleep(0.05)
    raise AssertionError("serve socket never appeared")


class _FakeStdin:
    """A blocking-file stand-in the forwarder's stdin reader thread iterates.

    Yields the queued client->server JSON-RPC lines, then blocks on a threading
    event until released, then signals EOF -- so the test controls exactly when
    the pump sees the client close stdin.
    """

    def __init__(self, lines, release):
        self._lines = list(lines)
        self._release = release

    def __iter__(self):
        yield from self._lines
        # Hold the "connection" open until the test releases it (after reading
        # the replies). The generous timeout is only a safety valve against a
        # wedged test -- in the happy path release() fires promptly; it must be
        # long enough that a slow host can't trip an unintended early stdin EOF.
        self._release.wait(timeout=30)


def test_forward_attaches_and_pumps(tmp_path, monkeypatch):
    """With a live serve host, the forwarder attaches and pumps a full session:
    client stdin -> host -> upstream -> host -> forwarder stdout."""
    sock = tmp_path / "serve.sock"
    bridge = _write_bridge_config(tmp_path)

    server = Server(sock)
    ready = threading.Event()
    loop_box = {}

    # Normalize the serve env BEFORE the server thread starts: _await_socket()
    # (which runs inside that thread) goes through serve_socket_if_available(),
    # so a stray AGENT_MCP_NO_SERVE in the outer environment would otherwise make
    # the readiness wait spin forever. Set the socket + clear the opt-outs first.
    monkeypatch.setenv("AGENT_MCP_SERVE_SOCKET", str(sock))
    monkeypatch.delenv("AGENT_MCP_NO_SERVE", raising=False)
    monkeypatch.delenv("AGENT_MCP_NO_MULTIPLEX", raising=False)
    # The forwarder's parent-death watchdog would race the test; disable it.
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")

    def _run_server():
        loop = asyncio.new_event_loop()
        loop_box["loop"] = loop
        asyncio.set_event_loop(loop)

        async def _serve():
            starter = asyncio.create_task(server.serve_forever())
            await _await_socket(sock)
            ready.set()
            await starter

        loop.run_until_complete(_serve())
        loop.close()

    server_thread = threading.Thread(target=_run_server, daemon=True)
    server_thread.start()
    assert ready.wait(timeout=5)

    # Drive the forwarder: three requests, then hold stdin open until we've read
    # all three replies, then release to signal EOF and let the pump finish.
    requests = [
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {}}) + "\n",
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                    "params": {}}) + "\n",
        json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "echo", "arguments": {"k": "v"}}}) + "\n",
    ]
    release = threading.Event()
    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdin", _FakeStdin(requests, release))
    monkeypatch.setattr(sys, "stdout", captured)

    def _drive():
        rc_box["rc"] = forward.run(str(bridge))

    rc_box = {}
    fwd_thread = threading.Thread(target=_drive, daemon=True)
    fwd_thread.start()

    # Wait until all three replies have been written to the forwarder's stdout.
    for _ in range(100):
        if captured.getvalue().count("\n") >= 3:
            break
        time.sleep(0.05)
    release.set()
    fwd_thread.join(timeout=5)

    try:
        lines = [json.loads(x) for x in captured.getvalue().splitlines() if x.strip()]
        by_id = {m["id"]: m for m in lines}
        assert by_id[1]["result"]["serverInfo"]["name"] == "echo"
        assert [t["name"] for t in by_id[2]["result"]["tools"]] == ["echo"]
        assert json.loads(by_id[3]["result"]["content"][0]["text"]) == {"k": "v"}
        assert rc_box.get("rc") == 0
    finally:
        loop = loop_box["loop"]
        fut = asyncio.run_coroutine_threadsafe(
            request_via_socket(sock, {"op": "shutdown"}), loop)
        fut.result(timeout=5)
        server_thread.join(timeout=5)


def test_forward_falls_back_to_direct_when_no_host(tmp_path, monkeypatch, capsys):
    """No serve host advertised and ensure-serve disabled -> the forwarder runs
    the in-process bridge and still answers a request over its own stdio
    (behaviour identical to ``agent-mcp bridge``)."""
    bridge = _write_bridge_config(tmp_path)
    # Point at an absent socket so no host is found; keep multiplex on but disable
    # the on-demand spawn so we exercise the pure direct-fallback path.
    monkeypatch.setenv("AGENT_MCP_SERVE_SOCKET", str(tmp_path / "absent.sock"))
    monkeypatch.delenv("AGENT_MCP_NO_SERVE", raising=False)
    monkeypatch.delenv("AGENT_MCP_NO_MULTIPLEX", raising=False)
    monkeypatch.setenv("AGENT_MCP_NO_ENSURE_SERVE", "1")
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")

    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "echo", "arguments": {"direct": 1}}}) + "\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(req))
    rc = forward.run(str(bridge))
    assert rc == 0
    out = capsys.readouterr().out
    replies = [json.loads(x) for x in out.splitlines() if x.strip()]
    assert json.loads(replies[0]["result"]["content"][0]["text"]) == {"direct": 1}


def test_forward_disabled_uses_direct_even_with_host(tmp_path, monkeypatch, capsys):
    """AGENT_MCP_NO_MULTIPLEX forces the direct in-process path regardless of any
    advertised host (the always-optional escape hatch)."""
    bridge = _write_bridge_config(tmp_path)
    monkeypatch.setenv("AGENT_MCP_NO_MULTIPLEX", "1")
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")

    # Even if a socket were set, disabling short-circuits before any attach.
    called = {"attach": False}

    async def _boom(*a, **k):
        called["attach"] = True
        raise AssertionError("attach must not be attempted when disabled")

    monkeypatch.setattr(forward.ipc, "open_attached_session", _boom)

    req = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                      "params": {"name": "echo", "arguments": {"x": 9}}}) + "\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(req))
    rc = forward.run(str(bridge))
    assert rc == 0
    assert called["attach"] is False
    out = capsys.readouterr().out
    replies = [json.loads(x) for x in out.splitlines() if x.strip()]
    assert json.loads(replies[0]["result"]["content"][0]["text"]) == {"x": 9}


def test_forward_falls_back_when_attach_refused(tmp_path, monkeypatch, capsys):
    """A host is advertised but refuses the attach (e.g. bad bridge on its end)
    before any MCP traffic -> the forwarder falls back to the direct bridge with
    no lost session state."""
    bridge = _write_bridge_config(tmp_path)
    sock = tmp_path / "serve.sock"
    sock.write_bytes(b"")  # a plain file; forwarder's discovery may still probe

    # Force discovery to "find" a socket, but make the attach refuse.
    monkeypatch.setattr(forward.ipc, "serve_socket_if_available",
                        lambda *_a, **_k: sock)

    async def _refuse(*_a, **_k):
        raise OSError("attach refused: nope")

    monkeypatch.setattr(forward.ipc, "open_attached_session", _refuse)
    monkeypatch.delenv("AGENT_MCP_NO_MULTIPLEX", raising=False)
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")

    req = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "echo", "arguments": {"fb": True}}}) + "\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(req))
    rc = forward.run(str(bridge))
    assert rc == 0
    out = capsys.readouterr().out
    replies = [json.loads(x) for x in out.splitlines() if x.strip()]
    assert json.loads(replies[0]["result"]["content"][0]["text"]) == {"fb": True}


def test_forward_bare_name_not_treated_as_cwd_file(tmp_path, monkeypatch):
    """A bare bridge *name* must pass through untouched even if a same-named file
    exists in the CWD -- matching config.resolve_config_path's path heuristic --
    so it is never mis-routed to an absolute CWD path."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "echo").write_text("not a config", encoding="utf-8")  # decoy
    # A bare name (no separator, no extension) stays a bare name.
    assert forward._abs_bridge_ref("echo") == "echo"
    assert forward._looks_like_path("echo") is False
    # A path-looking ref that exists is absolutized for the host's differing CWD.
    cfg = tmp_path / "echo.yaml"
    cfg.write_text("x", encoding="utf-8")
    assert forward._looks_like_path("echo.yaml") is True
    assert forward._abs_bridge_ref("echo.yaml") == str(cfg.resolve())
    # A path-looking ref that does NOT exist passes through unchanged.
    assert forward._abs_bridge_ref("sub/dir/none.yaml") == "sub/dir/none.yaml"


def test_forward_config_error_reports_nonzero(tmp_path, monkeypatch, capsys):
    """A bad bridge ref in the direct path returns a non-zero code + a message."""
    monkeypatch.setenv("AGENT_MCP_NO_MULTIPLEX", "1")
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc = forward.run(str(tmp_path / "does-not-exist.yaml"))
    assert rc == 1
    assert "forward" in capsys.readouterr().err


# -- ensure-serve: race-spawn a host on demand (#744 slice 3) ------------------

def test_ensure_serve_returns_existing_without_spawning(tmp_path, monkeypatch):
    """When a host is already advertised, _ensure_serve returns it and never
    spawns a second one."""
    sock = tmp_path / "serve.sock"

    server = Server(sock)
    ready = threading.Event()
    loop_box = {}

    def _run_server():
        loop = asyncio.new_event_loop()
        loop_box["loop"] = loop
        asyncio.set_event_loop(loop)

        async def _serve():
            starter = asyncio.create_task(server.serve_forever())
            for _ in range(100):
                if serve_socket_if_available(str(sock)):
                    break
                await asyncio.sleep(0.02)
            ready.set()
            await starter

        loop.run_until_complete(_serve())
        loop.close()

    t = threading.Thread(target=_run_server, daemon=True)
    t.start()
    assert ready.wait(timeout=5)

    monkeypatch.delenv("AGENT_MCP_NO_SERVE", raising=False)
    monkeypatch.setattr(forward, "_spawn_serve_host",
                        lambda *_a, **_k: pytest.fail("must not spawn when host is up"))
    try:
        got = forward._ensure_serve(str(sock))
        assert got == sock
    finally:
        with contextlib.suppress(Exception):
            asyncio.run(request_via_socket(sock, {"op": "shutdown"}))
        t.join(timeout=5)


def test_ensure_serve_spawns_a_real_host(tmp_path, monkeypatch):
    """With no host up, _ensure_serve spawns a detached `agent-mcp serve`, waits
    for its socket, and returns it; the spawned host answers a ping."""
    sock = tmp_path / "serve.sock"
    monkeypatch.delenv("AGENT_MCP_NO_SERVE", raising=False)
    monkeypatch.delenv("AGENT_MCP_NO_ENSURE_SERVE", raising=False)
    monkeypatch.setenv("AGENT_MCP_HOME", str(tmp_path))

    got = forward._ensure_serve(str(sock))
    assert got is not None, "ensure-serve did not bring up a host in time"
    try:
        pong = asyncio.run(request_via_socket(got, {"op": "ping"}))
        assert pong["ok"] and pong["pong"]
    finally:
        with contextlib.suppress(Exception):
            asyncio.run(request_via_socket(got, {"op": "shutdown"}))


def test_ensure_serve_disabled_returns_none(tmp_path, monkeypatch):
    """AGENT_MCP_NO_ENSURE_SERVE means run() never spawns -- it falls through to
    the direct bridge instead."""
    monkeypatch.setenv("AGENT_MCP_NO_ENSURE_SERVE", "1")
    assert forward._ensure_serve_enabled() is False
