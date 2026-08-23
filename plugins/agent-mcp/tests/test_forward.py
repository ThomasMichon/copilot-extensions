from __future__ import annotations

import asyncio
import contextlib
import io
import json
import sys
import threading
import time

import pytest

from agent_mcp import forward, sockio
from agent_mcp.serve import Server, request_via_socket

# The forwarder is the asyncio-free multiplexer client: it attaches to a resident
# (async) serve host over a plain socket and pumps bytes on two threads. These
# tests drive it against a real Server (run on its own loop in a thread) plus
# unit-level checks of the direct-fallback / ensure-serve branches.

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


class _BlockingLines:
    """A binary line source: yields the queued lines, then blocks until released
    (so the test controls exactly when the forwarder sees stdin EOF)."""

    def __init__(self, lines: list[bytes], release: threading.Event) -> None:
        self._lines = lines
        self._release = release

    def __iter__(self):
        yield from self._lines
        self._release.wait(timeout=30)


class _CaptureBuffer:
    def __init__(self) -> None:
        self.data = bytearray()
        self._lock = threading.Lock()

    def write(self, b: bytes) -> int:
        with self._lock:
            self.data.extend(b)
        return len(b)

    def flush(self) -> None:
        pass

    def text(self) -> str:
        with self._lock:
            return bytes(self.data).decode("utf-8", "replace")


class _FakeStd:
    """A stdin/stdout stand-in exposing the ``.buffer`` the pump reads/writes."""

    def __init__(self, buffer) -> None:
        self.buffer = buffer


class _ServerThread:
    """Run an (async) Server on its own loop in a background thread."""

    def __init__(self, sock) -> None:
        self.sock = sock
        self.server = Server(sock)
        self.ready = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
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

    def start(self) -> None:
        self._thread.start()
        assert self.ready.wait(timeout=5)

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            asyncio.run(request_via_socket(self.sock, {"op": "shutdown"}))
        self._thread.join(timeout=5)


def test_forward_attaches_and_pumps(tmp_path, monkeypatch):
    """With a live host, the forwarder attaches and pumps a full session:
    stdin -> host -> upstream -> host -> stdout."""
    sock = tmp_path / "serve.sock"
    bridge = _write_bridge_config(tmp_path)

    monkeypatch.setenv("AGENT_MCP_SERVE_SOCKET", str(sock))
    monkeypatch.delenv("AGENT_MCP_NO_SERVE", raising=False)
    monkeypatch.delenv("AGENT_MCP_NO_MULTIPLEX", raising=False)
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")

    srv = _ServerThread(sock)
    srv.start()

    requests = [
        (json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                     "params": {}}) + "\n").encode(),
        (json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                     "params": {}}) + "\n").encode(),
        (json.dumps({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                     "params": {"name": "echo", "arguments": {"k": "v"}}}) + "\n").encode(),
    ]
    release = threading.Event()
    cap = _CaptureBuffer()
    monkeypatch.setattr(sys, "stdin", _FakeStd(_BlockingLines(requests, release)))
    monkeypatch.setattr(sys, "stdout", _FakeStd(cap))

    rc_box = {}
    fwd = threading.Thread(target=lambda: rc_box.__setitem__("rc", forward.run(str(bridge))),
                           daemon=True)
    fwd.start()
    try:
        for _ in range(100):
            if cap.text().count("\n") >= 3:
                break
            time.sleep(0.05)
        release.set()
        fwd.join(timeout=5)

        by_id = {m["id"]: m for m in
                 (json.loads(x) for x in cap.text().splitlines() if x.strip())}
        assert by_id[1]["result"]["serverInfo"]["name"] == "echo"
        assert [t["name"] for t in by_id[2]["result"]["tools"]] == ["echo"]
        assert json.loads(by_id[3]["result"]["content"][0]["text"]) == {"k": "v"}
        assert rc_box.get("rc") == 0
    finally:
        srv.stop()


def test_forward_falls_back_to_direct_when_no_host(tmp_path, monkeypatch, capsys):
    """No host advertised and ensure-serve disabled -> the forwarder runs the
    in-process bridge and answers over its own stdio."""
    bridge = _write_bridge_config(tmp_path)
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
    replies = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert json.loads(replies[0]["result"]["content"][0]["text"]) == {"direct": 1}


def test_forward_disabled_uses_direct_even_with_host(tmp_path, monkeypatch, capsys):
    """AGENT_MCP_NO_MULTIPLEX forces the direct path before any attach."""
    bridge = _write_bridge_config(tmp_path)
    monkeypatch.setenv("AGENT_MCP_NO_MULTIPLEX", "1")
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")

    def _boom(*_a, **_k):
        raise AssertionError("attach must not be attempted when disabled")

    monkeypatch.setattr(forward.sockio, "attach", _boom)
    req = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                      "params": {"name": "echo", "arguments": {"x": 9}}}) + "\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(req))
    rc = forward.run(str(bridge))
    assert rc == 0
    replies = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert json.loads(replies[0]["result"]["content"][0]["text"]) == {"x": 9}


def test_forward_falls_back_when_attach_refused(tmp_path, monkeypatch, capsys):
    """A host is advertised but refuses the attach before any MCP traffic -> the
    forwarder falls back to the direct bridge with no lost session state."""
    bridge = _write_bridge_config(tmp_path)
    sock = tmp_path / "serve.sock"
    monkeypatch.setattr(forward.sockio, "serve_socket_if_available",
                        lambda *_a, **_k: sock)

    def _refuse(*_a, **_k):
        raise OSError("attach refused: nope")

    monkeypatch.setattr(forward.sockio, "attach", _refuse)
    monkeypatch.delenv("AGENT_MCP_NO_MULTIPLEX", raising=False)
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")

    req = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                      "params": {"name": "echo", "arguments": {"fb": True}}}) + "\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(req))
    rc = forward.run(str(bridge))
    assert rc == 0
    replies = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert json.loads(replies[0]["result"]["content"][0]["text"]) == {"fb": True}


def test_forward_resurrects_dead_host_on_unreachable(tmp_path, monkeypatch):
    """A stale handle whose host is GONE is reclaimed and a fresh host respawned,
    instead of degrading to the in-process bridge forever."""
    bridge = _write_bridge_config(tmp_path)
    stale = tmp_path / "serve.sock"
    calls = {"discarded": [], "ensure": 0, "attach": 0, "pumped": 0, "direct": 0}

    monkeypatch.setattr(forward.sockio, "serve_socket_if_available",
                        lambda *_a, **_k: stale)

    def _attach(_handle, _bref):
        calls["attach"] += 1
        if calls["attach"] == 1:
            raise forward.sockio.HostUnreachableError("dead host, stale socket")
        return object()  # a live connection after the respawn

    monkeypatch.setattr(forward.sockio, "attach", _attach)
    monkeypatch.setattr(forward.sockio, "discard_stale_handle",
                        lambda *a, **_k: calls["discarded"].append(a))

    def _ensure(_sp):
        calls["ensure"] += 1
        return tmp_path / "serve.sock"

    monkeypatch.setattr(forward, "_ensure_serve", _ensure)
    monkeypatch.setattr(forward.sockio, "pump",
                        lambda _c: calls.__setitem__("pumped", calls["pumped"] + 1))
    monkeypatch.setattr(forward, "_run_direct",
                        lambda _b: (calls.__setitem__("direct", calls["direct"] + 1) or 0))
    monkeypatch.delenv("AGENT_MCP_NO_MULTIPLEX", raising=False)
    monkeypatch.delenv("AGENT_MCP_NO_ENSURE_SERVE", raising=False)
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")

    rc = forward.run(str(bridge))
    assert rc == 0
    assert calls["discarded"], "the stale handle was reclaimed"
    assert calls["ensure"] == 1, "a fresh host was respawned"
    assert calls["attach"] == 2, "re-attached to the respawned host"
    assert calls["pumped"] == 1, "pumped the live session"
    assert calls["direct"] == 0, "did NOT degrade to the in-process bridge"


def test_forward_falls_back_when_respawn_fails_after_unreachable(tmp_path, monkeypatch):
    """A dead host is reclaimed; if the respawn can't come up, the forwarder
    still falls back to the direct bridge (graceful, not wedged)."""
    bridge = _write_bridge_config(tmp_path)
    stale = tmp_path / "serve.sock"
    calls = {"discarded": 0, "direct": 0}

    monkeypatch.setattr(forward.sockio, "serve_socket_if_available",
                        lambda *_a, **_k: stale)

    def _unreachable(*_a, **_k):
        raise forward.sockio.HostUnreachableError("dead host")

    monkeypatch.setattr(forward.sockio, "attach", _unreachable)
    monkeypatch.setattr(forward.sockio, "discard_stale_handle",
                        lambda *_a, **_k: calls.__setitem__("discarded", calls["discarded"] + 1))
    monkeypatch.setattr(forward, "_ensure_serve", lambda _sp: None)  # respawn fails
    monkeypatch.setattr(forward, "_run_direct",
                        lambda _b: (calls.__setitem__("direct", calls["direct"] + 1) or 0))
    monkeypatch.delenv("AGENT_MCP_NO_MULTIPLEX", raising=False)
    monkeypatch.delenv("AGENT_MCP_NO_ENSURE_SERVE", raising=False)
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")

    rc = forward.run(str(bridge))
    assert rc == 0
    assert calls["discarded"] == 1, "the stale handle was reclaimed"
    assert calls["direct"] == 1, "fell back to the direct bridge after a failed respawn"


def test_forward_config_error_reports_nonzero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("AGENT_MCP_NO_MULTIPLEX", "1")
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    rc = forward.run(str(tmp_path / "does-not-exist.yaml"))
    assert rc == 1
    # Generic "agent-mcp:" prefix (not "forward:") so `bridge` and `forward`
    # report config errors identically.
    err = capsys.readouterr().err
    assert err.startswith("agent-mcp:")
    assert "does-not-exist" in err


def test_looks_like_path_and_abs_ref(tmp_path, monkeypatch):
    """A bare bridge *name* passes through untouched even if a same-named file
    exists in the CWD (matching config.resolve_config_path)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "echo").write_text("decoy", encoding="utf-8")
    assert forward._looks_like_path("echo") is False
    assert forward._abs_bridge_ref("echo") == "echo"
    cfg = tmp_path / "echo.yaml"
    cfg.write_text("x", encoding="utf-8")
    assert forward._looks_like_path("echo.yaml") is True
    assert forward._abs_bridge_ref("echo.yaml") == str(cfg.resolve())
    assert forward._abs_bridge_ref("sub/dir/none.yaml") == "sub/dir/none.yaml"


# -- ensure-serve: race-spawn a host on demand ---------------------------------

def test_ensure_serve_returns_existing_without_spawning(tmp_path, monkeypatch):
    sock = tmp_path / "serve.sock"
    srv = _ServerThread(sock)
    srv.start()
    monkeypatch.delenv("AGENT_MCP_NO_SERVE", raising=False)
    monkeypatch.setattr(forward, "_spawn_serve_host",
                        lambda *_a, **_k: pytest.fail("must not spawn when host is up"))
    try:
        assert forward._ensure_serve(str(sock)) == sock
    finally:
        srv.stop()


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


def test_ensure_serve_disabled(monkeypatch):
    monkeypatch.setenv("AGENT_MCP_NO_ENSURE_SERVE", "1")
    assert forward._ensure_serve_enabled() is False


# -- default-on: `bridge` delegates to the multiplexer forwarder ---------------

def test_bridge_defaults_to_multiplexer(monkeypatch):
    """`agent-mcp bridge` routes through forward.run by default (multiplex-on)."""
    from agent_mcp.__main__ import _cmd_bridge

    called = {}

    def _fake_run(target, **_kw):
        called["target"] = target
        return 0

    monkeypatch.setattr(forward, "run", _fake_run)

    class _NS:
        config = None
        name = "echo"

    rc = _cmd_bridge(_NS())
    assert rc == 0
    assert called["target"] == "echo"


def test_bridge_opt_out_runs_direct(tmp_path, monkeypatch, capsys):
    """AGENT_MCP_NO_MULTIPLEX makes `bridge` (via forward) run the classic
    in-process bridge."""
    from agent_mcp.__main__ import _cmd_bridge

    bridge = _write_bridge_config(tmp_path)
    monkeypatch.setenv("AGENT_MCP_NO_MULTIPLEX", "1")
    monkeypatch.setenv("AGENT_MCP_PARENT_WATCHDOG", "0")
    req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                      "params": {"name": "echo", "arguments": {"d": 1}}}) + "\n"
    monkeypatch.setattr(sys, "stdin", io.StringIO(req))

    class _NS:
        config = str(bridge)
        name = None

    rc = _cmd_bridge(_NS())
    assert rc == 0
    replies = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert json.loads(replies[0]["result"]["content"][0]["text"]) == {"d": 1}
