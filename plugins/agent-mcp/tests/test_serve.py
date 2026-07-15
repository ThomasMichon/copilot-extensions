from __future__ import annotations

import asyncio
import json
import sys

from agent_mcp.config import parse_config
from agent_mcp.serve import (
    Server,
    WarmPool,
    call_via_socket,
    serve_socket_if_available,
)

# A minimal stdio MCP child: answers initialize, tools/list, and tools/call.
# tools/call echoes its arguments back as text so we can assert round-trips.
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


def _cfg():
    return parse_config({
        "server": {"type": "stdio", "command": [sys.executable, "-c", _CHILD]},
        "auth": {"kind": "none"},
    })


async def test_warmpool_reuses_one_session():
    pool = WarmPool()
    cfg = _cfg()
    try:
        r1 = await pool.call("k", cfg, "echo", {"n": 1})
        assert pool.size == 1
        # Same session object is reused across calls (no reopen).
        entry = pool._entries["k"]
        sess1 = entry.session
        r2 = await pool.call("k", cfg, "echo", {"n": 2})
        assert pool._entries["k"].session is sess1
        assert pool.size == 1
        assert json.loads(r1["content"][0]["text"]) == {"n": 1}
        assert json.loads(r2["content"][0]["text"]) == {"n": 2}
    finally:
        await pool.close_all()
    assert pool.size == 0


async def test_warmpool_list():
    pool = WarmPool()
    try:
        tools = await pool.list("k", _cfg())
        assert [t["name"] for t in tools] == ["echo"]
    finally:
        await pool.close_all()


async def test_server_roundtrip_over_socket(tmp_path):
    sock = tmp_path / "serve.sock"
    # Write a bridge config to a file so the server can load_config(bridge).
    bridge = tmp_path / "echo.mcp.yaml"
    bridge.write_text(
        "server:\n  type: stdio\n  command:\n"
        f"    - {sys.executable}\n    - '-c'\n    - |\n"
        + "".join("      " + ln + "\n" for ln in _CHILD.splitlines())
        + "auth:\n  kind: none\n",
        encoding="utf-8",
    )
    server = Server(sock)
    task = asyncio.create_task(server.serve_forever())
    try:
        # Wait for the socket to appear.
        for _ in range(50):
            if serve_socket_if_available(str(sock)):
                break
            await asyncio.sleep(0.05)
        assert serve_socket_if_available(str(sock)) == sock

        # ping
        reader, writer = await asyncio.open_unix_connection(str(sock))
        writer.write(b'{"op":"ping"}\n')
        await writer.drain()
        pong = json.loads(await reader.readline())
        assert pong["ok"] and pong["pong"]
        writer.close()

        # call via helper
        resp = await call_via_socket(sock, str(bridge), "echo", {"hello": "world"})
        assert resp["ok"]
        assert json.loads(resp["content"]) == {"hello": "world"}
        assert resp["isError"] is False
    finally:
        # shutdown op stops the server
        reader, writer = await asyncio.open_unix_connection(str(sock))
        writer.write(b'{"op":"shutdown"}\n')
        await writer.drain()
        await reader.readline()
        writer.close()
        await asyncio.wait_for(task, timeout=5)
    # socket cleaned up on shutdown
    assert not sock.exists()


async def test_server_reports_config_error(tmp_path):
    sock = tmp_path / "serve.sock"
    server = Server(sock)
    task = asyncio.create_task(server.serve_forever())
    try:
        for _ in range(50):
            if serve_socket_if_available(str(sock)):
                break
            await asyncio.sleep(0.05)
        resp = await call_via_socket(sock, str(tmp_path / "nope.yaml"), "echo", {})
        assert resp["ok"] is False
        assert "config" in resp["error"]
    finally:
        r, w = await asyncio.open_unix_connection(str(sock))
        w.write(b'{"op":"shutdown"}\n')
        await w.drain()
        await r.readline()
        w.close()
        await asyncio.wait_for(task, timeout=5)


def test_socket_detection_negatives(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_MCP_NO_SERVE", raising=False)
    # Missing path -> None
    assert serve_socket_if_available(str(tmp_path / "absent.sock")) is None
    # A regular file (not a socket) -> None
    reg = tmp_path / "regular"
    reg.write_text("x", encoding="utf-8")
    assert serve_socket_if_available(str(reg)) is None
    # AGENT_MCP_NO_SERVE forces None even if a socket exists
    monkeypatch.setenv("AGENT_MCP_NO_SERVE", "1")
    assert serve_socket_if_available(str(reg)) is None


def _bridge_file(tmp_path):
    bridge = tmp_path / "echo.mcp.yaml"
    bridge.write_text(
        "server:\n  type: stdio\n  command:\n"
        f"    - {sys.executable}\n    - '-c'\n    - |\n"
        + "".join("      " + ln + "\n" for ln in _CHILD.splitlines())
        + "auth:\n  kind: none\n",
        encoding="utf-8",
    )
    return bridge


def test_call_verb_uses_serve_daemon(tmp_path, monkeypatch, capsys):
    """The `call` verb routes through a running daemon (fast-path integration)."""
    import threading

    from agent_mcp.__main__ import main

    sock = tmp_path / "serve.sock"
    bridge = _bridge_file(tmp_path)

    server = Server(sock)
    ready = threading.Event()

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _serve():
            starter = asyncio.create_task(server.serve_forever())
            for _ in range(100):
                if sock.exists():
                    break
                await asyncio.sleep(0.02)
            ready.set()
            await starter

        loop.run_until_complete(_serve())
        loop.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert ready.wait(timeout=5)

    monkeypatch.setenv("AGENT_MCP_SERVE_SOCKET", str(sock))
    monkeypatch.delenv("AGENT_MCP_NO_SERVE", raising=False)
    try:
        rc = main(["call", str(bridge), "echo", '{"served": true}'])
        assert rc == 0
        out = capsys.readouterr().out
        assert json.loads(out.strip()) == {"served": True}
        assert server.pool.size == 1  # the daemon held a warm session
    finally:
        loop2 = asyncio.new_event_loop()

        async def _shutdown():
            r, w = await asyncio.open_unix_connection(str(sock))
            w.write(b'{"op":"shutdown"}\n')
            await w.drain()
            await r.readline()
            w.close()

        loop2.run_until_complete(_shutdown())
        loop2.close()
        thread.join(timeout=5)

