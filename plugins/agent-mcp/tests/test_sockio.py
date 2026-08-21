from __future__ import annotations

import asyncio
import json
import sys
import threading

import pytest

from agent_mcp import sockio
from agent_mcp.serve import Server, request_via_socket

# sockio is the asyncio-free client the forwarder uses. These tests drive its
# discovery + synchronous attach against a real (async) Server run in a thread.

_CHILD = (
    "import sys,json\n"
    "for line in sys.stdin:\n"
    "    line=line.strip()\n"
    "    if not line: continue\n"
    "    m=json.loads(line); mid=m.get('id'); method=m.get('method')\n"
    "    if mid is None: continue\n"
    "    if method=='initialize':\n"
    "        r={'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'echo'}}\n"
    "    elif method=='tools/list':\n"
    "        r={'tools':[{'name':'echo','inputSchema':{'type':'object'}}]}\n"
    "    elif method=='tools/call':\n"
    "        a=m.get('params',{}).get('arguments',{})\n"
    "        r={'content':[{'type':'text','text':json.dumps(a)}]}\n"
    "    else:\n"
    "        r={}\n"
    "    sys.stdout.write(json.dumps({'jsonrpc':'2.0','id':mid,'result':r})+'\\n')\n"
    "    sys.stdout.flush()\n"
)


def _write_bridge_config(tmp_path):
    bridge = tmp_path / "echo.mcp.yaml"
    bridge.write_text(
        "server:\n  type: stdio\n  command:\n"
        f"    - {sys.executable}\n    - '-c'\n    - |\n"
        + "".join("      " + ln + "\n" for ln in _CHILD.splitlines())
        + "auth:\n  kind: none\n",
        encoding="utf-8",
    )
    return bridge


class _ServerThread:
    def __init__(self, sock):
        self.sock = sock
        self.server = Server(sock)
        self.ready = threading.Event()
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _serve():
            starter = asyncio.create_task(self.server.serve_forever())
            for _ in range(200):
                if sockio.serve_socket_if_available(str(self.sock)):
                    break
                await asyncio.sleep(0.02)
            self.ready.set()
            await starter

        loop.run_until_complete(_serve())
        loop.close()

    def start(self):
        self._t.start()
        assert self.ready.wait(timeout=5)

    def stop(self):
        import contextlib
        with contextlib.suppress(Exception):
            asyncio.run(request_via_socket(self.sock, {"op": "shutdown"}))
        self._t.join(timeout=5)


def test_sockio_no_asyncio_import():
    """Importing sockio must not drag in asyncio -- that footprint is the whole
    reason the forwarder uses this module instead of ipc."""
    # A subprocess with a clean import graph is the honest check.
    import subprocess
    code = "import sys, agent_mcp.sockio; print('asyncio' in sys.modules)"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, check=True).stdout.strip()
    assert out == "False"


def test_serve_socket_detection(tmp_path, monkeypatch):
    monkeypatch.delenv("AGENT_MCP_NO_SERVE", raising=False)
    assert sockio.serve_socket_if_available(str(tmp_path / "absent.sock")) is None
    reg = tmp_path / "regular"
    reg.write_text("x", encoding="utf-8")
    assert sockio.serve_socket_if_available(str(reg)) is None
    monkeypatch.setenv("AGENT_MCP_NO_SERVE", "1")
    assert sockio.serve_socket_if_available(str(reg)) is None


def test_malformed_endpoint_rejected(tmp_path, monkeypatch):
    """An out-of-range / malformed sidecar port is treated as no host, not passed
    through to create_connection (which would raise OverflowError, not OSError)."""
    monkeypatch.delenv("AGENT_MCP_NO_SERVE", raising=False)
    handle = tmp_path / "serve.sock"
    ep = tmp_path / "serve.sock.endpoint"
    for bad in ('{"port": 70000}', '{"port": 0}', '{"port": -1}', '{"port": "x"}', "{}"):
        ep.write_text(bad, encoding="utf-8")
        assert sockio._read_endpoint(handle) is None
        assert sockio.serve_socket_if_available(str(handle)) is None


def test_sockio_attach_roundtrip(tmp_path):
    """sockio.attach connects + handshakes; the raw socket then speaks MCP."""
    sock = tmp_path / "serve.sock"
    bridge = _write_bridge_config(tmp_path)
    srv = _ServerThread(sock)
    srv.start()
    try:
        conn = sockio.attach(sock, str(bridge))
        fh = conn.makefile("rwb")
        for mid, method, params in [
            (1, "initialize", {}),
            (2, "tools/list", {}),
            (3, "tools/call", {"name": "echo", "arguments": {"k": "v"}}),
        ]:
            fh.write((json.dumps({"jsonrpc": "2.0", "id": mid, "method": method,
                                  "params": params}) + "\n").encode())
            fh.flush()
            resp = json.loads(fh.readline())
            assert resp["id"] == mid
        assert json.loads(resp["result"]["content"][0]["text"]) == {"k": "v"}
        conn.close()
    finally:
        srv.stop()


def test_sockio_attach_missing_bridge_refused(tmp_path):
    sock = tmp_path / "serve.sock"
    srv = _ServerThread(sock)
    srv.start()
    try:
        with pytest.raises(OSError):
            sockio.attach(sock, str(tmp_path / "does-not-exist.yaml"))
    finally:
        srv.stop()


def test_sockio_attach_no_host_raises(tmp_path):
    with pytest.raises(OSError):
        sockio.attach(tmp_path / "nope.sock", "echo")


def test_attach_does_not_swallow_bytes_after_ack(tmp_path):
    """The ack must be read without over-buffering: a message the host sends
    immediately after the ack must still be readable off the raw socket (a
    buffered reader would prefetch and drop it)."""
    import socket as _socket
    import threading

    if not hasattr(_socket, "AF_UNIX"):
        pytest.skip("AF_UNIX unavailable (e.g. Windows)")

    sockpath = tmp_path / "fake.sock"
    srv = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
    srv.bind(str(sockpath))
    srv.listen(1)

    def _serve_once():
        conn, _ = srv.accept()
        conn.recv(4096)  # consume the attach request
        # Send the ack AND an unsolicited notification back-to-back, so a
        # read-ahead on the ack line would swallow the notification.
        conn.sendall(b'{"ok":true,"attached":true}\n')
        conn.sendall(b'{"jsonrpc":"2.0","method":"notifications/x"}\n')
        import time
        time.sleep(0.5)
        conn.close()

    t = threading.Thread(target=_serve_once, daemon=True)
    t.start()
    try:
        conn = sockio.attach(sockpath, "echo")
        # The notification queued right after the ack must still arrive.
        data = b""
        conn.settimeout(2)
        while b"\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        assert b"notifications/x" in data
        conn.close()
    finally:
        srv.close()
        t.join(timeout=2)
