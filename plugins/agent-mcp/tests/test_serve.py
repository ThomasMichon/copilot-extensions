from __future__ import annotations

import asyncio
import json
import sys

import pytest

from agent_mcp.client import UpstreamError
from agent_mcp.config import parse_config
from agent_mcp.serve import (
    _HAS_AF_UNIX,
    Server,
    WarmPool,
    _read_endpoint,
    call_via_socket,
    request_via_socket,
    serve_socket_if_available,
)

# The serve daemon is cross-platform: an AF_UNIX socket on POSIX, a loopback-TCP
# listener + token on Windows (where asyncio has no AF_UNIX). Every test drives
# it through the transport-agnostic helpers (``serve_socket_if_available``,
# ``request_via_socket``, ``call_via_socket``), so the suite runs on both. The
# token-enforcement test is loopback-TCP specific (no token on AF_UNIX).
tcp_transport_only = pytest.mark.skipif(
    _HAS_AF_UNIX, reason="loopback-TCP transport only (no AF_UNIX, e.g. Windows)")

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


def _cfg(extra: dict | None = None):
    data = {
        "server": {"type": "stdio", "command": [sys.executable, "-c", _CHILD]},
        "auth": {"kind": "none"},
    }
    if extra:
        data.update(extra)
    return parse_config(data)


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


async def test_warmpool_cli_bridge_recovers_from_transient_auth_failure(tmp_path):
    """A transient CLI-bridge auth failure must not permanently poison the
    warm-pooled session.

    Regression test: ``CliTransport`` used to compute its spawn environment
    (including the auth-injected token) once and cache it for the life of the
    transport object. ``WarmPool`` keeps that same transport alive across
    many calls over hours (exactly this test's ``pool.call`` reuse), so a
    one-time hiccup in the auth command (e.g. a cold vault) permanently broke
    every subsequent call for that bridge until the daemon restarted. The
    auth command here fails on its first invocation and succeeds afterward;
    the second ``pool.call`` against the same warm session must receive the
    token, not the poisoned/ambient environment from the first failure.
    """
    counter = tmp_path / "invocations"
    auth_script = tmp_path / "mint_token.py"
    auth_script.write_text(
        "import sys\n"
        "from pathlib import Path\n"
        f"counter = Path({str(counter)!r})\n"
        "n = int(counter.read_text()) + 1 if counter.exists() else 1\n"
        "counter.write_text(str(n))\n"
        "if n == 1:\n"
        "    sys.exit(1)\n"
        "print('sekret-456')\n",
        encoding="utf-8",
    )
    tool_md = tmp_path / "whoami.md"
    mcp = {
        "name": "whoami",
        "description": "echo the injected token",
        "inputSchema": {"type": "object", "properties": {}},
        "invoke": {
            "command": sys.executable,
            "args": ["-c", "import os,sys; "
                     "sys.stdout.write(os.environ.get('MY_TOKEN', '<unset>'))"],
        },
    }
    tool_md.write_text("---\n" + json.dumps({"mcp": mcp}) + "\n---\n", encoding="utf-8")
    cfg = _cfg({
        "server": {"type": "cli", "tools_from": [str(tool_md)]},
        "auth": {"kind": "command", "command": [sys.executable, str(auth_script)],
                 "parse": "raw", "target_env": "MY_TOKEN"},
    })

    pool = WarmPool()
    try:
        r1 = await pool.call("k", cfg, "whoami", {})
        assert pool.size == 1
        entry = pool._entries["k"]
        sess1 = entry.session
        assert r1["content"][0]["text"] == "<unset>"

        r2 = await pool.call("k", cfg, "whoami", {})
        # Same warm session/transport reused, not reopened.
        assert pool._entries["k"].session is sess1
        assert pool.size == 1
        assert r2["content"][0]["text"] == "sekret-456"
    finally:
        await pool.close_all()


async def test_warmpool_list():
    pool = WarmPool()
    try:
        tools = await pool.list("k", _cfg())
        assert [t["name"] for t in tools] == ["echo"]
    finally:
        await pool.close_all()


async def test_warmpool_enforces_fresh_tool_filter():
    pool = WarmPool()
    try:
        initial = _cfg()
        await pool.call("k", initial, "echo", {"value": 1})
        session = pool._entries["k"].session

        denied = _cfg({"tools": {"deny": ["echo"]}})
        with pytest.raises(UpstreamError, match="blocked by bridge tools filter"):
            await pool.call("k", denied, "echo", {})
        assert await pool.list("k", denied) == []
        assert pool._entries["k"].session is not session
    finally:
        await pool.close_all()


async def test_warmpool_reopens_when_config_widens():
    pool = WarmPool()
    try:
        denied = _cfg({"tools": {"deny": ["echo"]}})
        assert await pool.list("k", denied) == []
        old_session = pool._entries["k"].session

        widened = _cfg()
        assert [tool["name"] for tool in await pool.list("k", widened)] == ["echo"]
        assert pool._entries["k"].session is not old_session
        result = await pool.call("k", widened, "echo", {"value": 2})
        assert json.loads(result["content"][0]["text"]) == {"value": 2}
    finally:
        await pool.close_all()


async def test_busy_bridge_does_not_block_other_bridge():
    pool = WarmPool()
    lock = await pool._key_lock("busy")
    await lock.acquire()
    queued = asyncio.create_task(pool.call("busy", _cfg(), "echo", {"value": 1}))
    try:
        result = await asyncio.wait_for(
            pool.call("other", _cfg(), "echo", {"value": 2}),
            timeout=2,
        )
        assert json.loads(result["content"][0]["text"]) == {"value": 2}
    finally:
        lock.release()
        await queued
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
        # Wait for the daemon handle to appear.
        for _ in range(50):
            if serve_socket_if_available(str(sock)):
                break
            await asyncio.sleep(0.05)
        assert serve_socket_if_available(str(sock)) == sock

        # ping
        pong = await request_via_socket(sock, {"op": "ping"})
        assert pong["ok"] and pong["pong"]

        # call via helper
        resp = await call_via_socket(sock, str(bridge), "echo", {"hello": "world"})
        assert resp["ok"]
        assert json.loads(resp["content"]) == {"hello": "world"}
        assert resp["isError"] is False
    finally:
        # shutdown op stops the server
        await request_via_socket(sock, {"op": "shutdown"})
        await asyncio.wait_for(task, timeout=5)
    # handle cleaned up on shutdown (socket file on POSIX / endpoint on Windows)
    assert serve_socket_if_available(str(sock)) is None


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
        await request_via_socket(sock, {"op": "shutdown"})
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
            await request_via_socket(sock, {"op": "shutdown"})

        loop2.run_until_complete(_shutdown())
        loop2.close()
        thread.join(timeout=5)


@tcp_transport_only
async def test_tcp_endpoint_published_and_token_enforced(tmp_path):
    """On the loopback-TCP transport the endpoint sidecar carries port+token, an
    unauthenticated connection is rejected, and the token grants access."""
    sock = tmp_path / "serve.sock"
    server = Server(sock)
    task = asyncio.create_task(server.serve_forever())
    try:
        for _ in range(50):
            if serve_socket_if_available(str(sock)):
                break
            await asyncio.sleep(0.05)
        ep = _read_endpoint(sock)
        assert isinstance(ep["port"], int) and ep["port"] > 0
        assert ep["token"]

        # A raw connection that omits the token is rejected as unauthorized.
        reader, writer = await asyncio.open_connection("127.0.0.1", ep["port"])
        writer.write(b'{"op":"ping"}\n')
        await writer.drain()
        resp = json.loads(await reader.readline())
        assert resp["ok"] is False and "unauthorized" in resp["error"]
        writer.close()

        # request_via_socket presents the token -> authorized.
        pong = await request_via_socket(sock, {"op": "ping"})
        assert pong["ok"] and pong["pong"]
    finally:
        await request_via_socket(sock, {"op": "shutdown"})
        await asyncio.wait_for(task, timeout=5)
    # Endpoint sidecar cleaned up on shutdown.
    assert serve_socket_if_available(str(sock)) is None
    assert _read_endpoint(sock) is None



# -- full-session attach (the #744 multiplexer session-host) ------------------

def _write_bridge_config(tmp_path, name="echo.mcp.yaml"):
    """Write a stdio-echo bridge config file the server can load_config()."""
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


async def _rpc(reader, writer, msg):
    writer.write((json.dumps(msg) + "\n").encode())
    await writer.drain()
    line = await asyncio.wait_for(reader.readline(), timeout=10)
    return json.loads(line)


async def test_attach_full_session_roundtrip(tmp_path):
    from agent_mcp.serve import open_attached_session

    sock = tmp_path / "serve.sock"
    bridge = _write_bridge_config(tmp_path)
    server = Server(sock)
    task = asyncio.create_task(server.serve_forever())
    try:
        await _await_socket(sock)
        reader, writer = await open_attached_session(sock, str(bridge))
        init = await _rpc(reader, writer,
                          {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                           "params": {}})
        assert init["id"] == 1 and "result" in init
        tl = await _rpc(reader, writer,
                        {"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                         "params": {}})
        assert [t["name"] for t in tl["result"]["tools"]] == ["echo"]
        call = await _rpc(reader, writer,
                          {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                           "params": {"name": "echo", "arguments": {"k": "v"}}})
        assert json.loads(call["result"]["content"][0]["text"]) == {"k": "v"}
        writer.close()
    finally:
        await request_via_socket(sock, {"op": "shutdown"})
        await asyncio.wait_for(task, timeout=5)


async def test_two_attached_sessions_are_independent(tmp_path):
    # One serve process hosts two concurrent client sessions, each with its own
    # upstream + pipeline (the multiplexer property: interpreters collapse, but
    # per-client MCP sessions stay separate).
    from agent_mcp.serve import open_attached_session

    sock = tmp_path / "serve.sock"
    bridge = _write_bridge_config(tmp_path)
    server = Server(sock)
    task = asyncio.create_task(server.serve_forever())
    try:
        await _await_socket(sock)
        r1, w1 = await open_attached_session(sock, str(bridge))
        r2, w2 = await open_attached_session(sock, str(bridge))
        # Interleave calls; each session echoes its own arguments back.
        a = await _rpc(r1, w1, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "echo", "arguments": {"s": "one"}}})
        b = await _rpc(r2, w2, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                "params": {"name": "echo", "arguments": {"s": "two"}}})
        assert json.loads(a["result"]["content"][0]["text"]) == {"s": "one"}
        assert json.loads(b["result"]["content"][0]["text"]) == {"s": "two"}
        assert server.pool.size == 0  # attach sessions bypass the WarmPool
        w1.close()
        w2.close()
    finally:
        await request_via_socket(sock, {"op": "shutdown"})
        await asyncio.wait_for(task, timeout=5)


async def test_attach_missing_bridge_is_refused(tmp_path):
    from agent_mcp.serve import open_attached_session

    sock = tmp_path / "serve.sock"
    server = Server(sock)
    task = asyncio.create_task(server.serve_forever())
    try:
        await _await_socket(sock)
        with pytest.raises(OSError):
            await open_attached_session(sock, str(tmp_path / "does-not-exist.yaml"))
    finally:
        await request_via_socket(sock, {"op": "shutdown"})
        await asyncio.wait_for(task, timeout=5)


async def test_attached_session_survives_invalid_json(tmp_path):
    # A garbage line after attach is logged and dropped; the session stays live
    # and still answers the next valid request.
    from agent_mcp.serve import open_attached_session

    sock = tmp_path / "serve.sock"
    bridge = _write_bridge_config(tmp_path)
    server = Server(sock)
    task = asyncio.create_task(server.serve_forever())
    try:
        await _await_socket(sock)
        reader, writer = await open_attached_session(sock, str(bridge))
        writer.write(b"not json at all\n")
        await writer.drain()
        call = await _rpc(reader, writer,
                          {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                           "params": {"name": "echo", "arguments": {"ok": 1}}})
        assert json.loads(call["result"]["content"][0]["text"]) == {"ok": 1}
        writer.close()
    finally:
        await request_via_socket(sock, {"op": "shutdown"})
        await asyncio.wait_for(task, timeout=5)


# -- host lifecycle: single-instance lease + attach refcount + idle-evict (#744) --

async def test_second_host_stands_down_under_lease(tmp_path):
    """A second host on the same home (lock dir) can't take the single-instance
    lease, so it stands down without binding -- the first host keeps the socket."""
    sock = tmp_path / "serve.sock"
    server_a = Server(sock)
    task_a = asyncio.create_task(server_a.serve_forever())
    try:
        await _await_socket(sock)
        # A second host pointed at the same home stands down immediately: its
        # serve_forever returns without ever binding (it never took the lease).
        server_b = Server(sock)
        await asyncio.wait_for(server_b.serve_forever(), timeout=5)
        assert server_b._server is None  # never bound
        # The first host is still live and answering.
        pong = await request_via_socket(sock, {"op": "ping"})
        assert pong["ok"] and pong["pong"]
    finally:
        await request_via_socket(sock, {"op": "shutdown"})
        await asyncio.wait_for(task_a, timeout=5)


async def test_host_idle_evicts_with_no_attached_sessions(tmp_path):
    """With nothing attached and no activity, the host self-evicts after its idle
    window -- serve_forever returns on its own (the losable/refcounted property)."""
    sock = tmp_path / "serve.sock"
    server = Server(sock, idle_timeout=0.2)
    # serve_forever should return by itself (idle self-eviction), no shutdown op.
    await asyncio.wait_for(server.serve_forever(), timeout=5)
    # Handle cleaned up on the self-eviction path.
    assert serve_socket_if_available(str(sock)) is None


async def test_attach_refcount_keeps_host_alive_then_evicts(tmp_path):
    """An attached session holds the host open past the idle window; once it
    detaches and the window elapses, the host evicts itself."""
    from agent_mcp.serve import open_attached_session

    sock = tmp_path / "serve.sock"
    bridge = _write_bridge_config(tmp_path)
    server = Server(sock, idle_timeout=0.3)
    task = asyncio.create_task(server.serve_forever())
    await _await_socket(sock)
    _reader, writer = await open_attached_session(sock, str(bridge))
    # ping reports the live attach count.
    pong = await request_via_socket(sock, {"op": "ping"})
    assert pong["attached"] == 1
    # Hold well past the idle window; the attached session keeps the host alive.
    await asyncio.sleep(0.6)
    assert not task.done()
    # Detach; now the host should evict itself after the idle window elapses.
    writer.close()
    await asyncio.wait_for(task, timeout=5)
    assert serve_socket_if_available(str(sock)) is None


async def test_disabling_lease_allows_two_hosts(tmp_path):
    """enable_lease=False lets a test run two hosts on one home (the lease is the
    only thing preventing it) -- a guard that the stand-down above is lease-driven."""
    sock_a = tmp_path / "a.sock"
    sock_b = tmp_path / "b.sock"
    a = Server(sock_a, enable_lease=False)
    b = Server(sock_b, enable_lease=False, lease_service="agent-mcp-serve")
    ta = asyncio.create_task(a.serve_forever())
    tb = asyncio.create_task(b.serve_forever())
    try:
        await _await_socket(sock_a)
        await _await_socket(sock_b)
        assert (await request_via_socket(sock_a, {"op": "ping"}))["ok"]
        assert (await request_via_socket(sock_b, {"op": "ping"}))["ok"]
    finally:
        await request_via_socket(sock_a, {"op": "shutdown"})
        await request_via_socket(sock_b, {"op": "shutdown"})
        await asyncio.wait_for(ta, timeout=5)
        await asyncio.wait_for(tb, timeout=5)


async def test_detach_decrements_refcount_even_if_aclose_raises(tmp_path, monkeypatch):
    """A teardown error on detach must not wedge the refcount: _attached still
    returns to 0, so idle-eviction is never blocked by a failed aclose."""
    import agent_mcp.serve as serve_mod
    from agent_mcp.serve import open_attached_session

    orig_aclose = serve_mod.BridgeSession.aclose

    async def _boom(self):
        await orig_aclose(self)
        raise RuntimeError("teardown blew up")

    monkeypatch.setattr(serve_mod.BridgeSession, "aclose", _boom)

    sock = tmp_path / "serve.sock"
    bridge = _write_bridge_config(tmp_path)
    server = Server(sock, idle_timeout=0.3)
    task = asyncio.create_task(server.serve_forever())
    try:
        await _await_socket(sock)
        _reader, writer = await open_attached_session(sock, str(bridge))
        assert (await request_via_socket(sock, {"op": "ping"}))["attached"] == 1
        writer.close()
        # The failing aclose still drops the refcount; the host then idle-evicts.
        await asyncio.wait_for(task, timeout=5)
        assert server.attached == 0
    finally:
        if not task.done():
            await request_via_socket(sock, {"op": "shutdown"})
            await asyncio.wait_for(task, timeout=5)
