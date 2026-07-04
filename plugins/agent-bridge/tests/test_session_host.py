"""Tests for the Session Host layer (effort agent-bridge-version-mux, #1762).

Covers the 1:1-ACP wire protocol, the host's reattach/seq/ack/buffer semantics
(no gap, no re-stream), child liveness, WRITE relay, explicit terminate, and the
Windows job-breakaway flag plumbing. The host is exercised in-process against a
fake child (no real subprocess) so the tests are fast and deterministic on every
platform.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from agent_bridge import winjob
from agent_bridge.session_host import launcher
from agent_bridge.session_host import protocol as proto
from agent_bridge.session_host.acp_adapter import open_acp_streams
from agent_bridge.session_host.client import SessionHostClient
from agent_bridge.session_host.host import SessionHost


# --------------------------------------------------------------------------
# protocol
# --------------------------------------------------------------------------
def test_pack_unpack_u64():
    assert proto.unpack_u64(proto.pack_u64(0)) == 0
    assert proto.unpack_u64(proto.pack_u64(2**63)) == 2**63


def test_pack_unpack_frame():
    seq, data = proto.unpack_frame(proto.pack_frame(42, b'{"x":1}\n'))
    assert seq == 42
    assert data == b'{"x":1}\n'


def test_pack_unpack_liveness():
    assert proto.unpack_liveness(proto.pack_liveness(True)) == (True, 0)
    assert proto.unpack_liveness(proto.pack_liveness(False, 7)) == (False, 7)


@pytest.mark.asyncio
async def test_message_roundtrip():
    r = asyncio.StreamReader()
    r.feed_data(proto.encode(proto.MsgType.FRAME, proto.pack_frame(3, b"hi\n")))
    r.feed_eof()
    msg = await proto.read_message(r)
    assert msg is not None
    mtype, payload = msg
    assert mtype == proto.MsgType.FRAME
    assert proto.unpack_frame(payload) == (3, b"hi\n")
    # EOF -> None
    assert await proto.read_message(r) is None


@pytest.mark.asyncio
async def test_message_partial_eof_is_clean():
    r = asyncio.StreamReader()
    r.feed_data(b"\x00\x00")  # truncated header
    r.feed_eof()
    assert await proto.read_message(r) is None


@pytest.mark.asyncio
async def test_oversized_message_raises():
    r = asyncio.StreamReader()
    import struct
    r.feed_data(struct.pack(">I", proto.MAX_MESSAGE_BYTES + 1))
    r.feed_eof()
    with pytest.raises(proto.ProtocolError):
        await proto.read_message(r)


@pytest.mark.asyncio
async def test_unknown_type_raises():
    r = asyncio.StreamReader()
    r.feed_data(proto._U32.pack(1) + b"Z")
    r.feed_eof()
    with pytest.raises(proto.ProtocolError):
        await proto.read_message(r)


# --------------------------------------------------------------------------
# fake child
# --------------------------------------------------------------------------
class _FakeStdin:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None


class _FakeChild:
    """Duck-typed ChildProcess: a feedable stdout + a captured stdin."""

    def __init__(self, pid: int = 4242) -> None:
        self.stdout = asyncio.StreamReader()
        self.stdin = _FakeStdin()
        self._pid = pid
        self._returncode: int | None = None
        self._exited = asyncio.Event()
        self.killed = False

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def feed_frame(self, obj: bytes) -> None:
        self.stdout.feed_data(obj if obj.endswith(b"\n") else obj + b"\n")

    def finish(self, code: int = 0) -> None:
        self._returncode = code
        self.stdout.feed_eof()
        self._exited.set()

    def kill(self) -> None:
        self.killed = True
        if self._returncode is None:
            self.finish(-9)

    async def wait(self) -> int:
        await self._exited.wait()
        return self._returncode or 0


async def _serve(child: _FakeChild) -> tuple[SessionHost, int]:
    host = SessionHost(child)
    port = await host.serve(port=0)
    return host, port


async def _read_n(gen, n: int, client: SessionHostClient) -> list[int]:
    seqs: list[int] = []
    for _ in range(n):
        seq, _data = await asyncio.wait_for(gen.__anext__(), timeout=5)
        seqs.append(seq)
        await client.ack(seq)
    return seqs


# --------------------------------------------------------------------------
# host reattach semantics
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_reattach_no_gap_no_restream():
    child = _FakeChild(pid=1234)
    host, port = await _serve(child)
    try:
        # front 1 attaches fresh, consumes 3 frames, acks them, detaches.
        c1 = await SessionHostClient.connect(port=port)
        hello = await c1.attach(0)
        assert hello.child_pid == 1234
        for i in range(1, 4):
            child.feed_frame(f'{{"n":{i}}}'.encode())
        acked = await _read_n(c1.frames(), 3, c1)
        assert acked == [1, 2, 3]
        await c1.close()
        await asyncio.sleep(0.02)

        # frames stream while NO front is attached -> buffered by the host.
        for i in range(4, 7):
            child.feed_frame(f'{{"n":{i}}}'.encode())
        await asyncio.sleep(0.02)

        # front 2 reattaches from last-acked seq 3.
        c2 = await SessionHostClient.connect(port=port)
        await c2.attach(3)
        got = await _read_n(c2.frames(), 3, c2)
        assert got == [4, 5, 6]              # contiguous
        assert min(got) > 3                  # no re-stream of acked frames
        await c2.close()
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_ack_trims_buffer():
    child = _FakeChild()
    host, port = await _serve(child)
    try:
        c1 = await SessionHostClient.connect(port=port)
        await c1.attach(0)
        for i in range(1, 5):
            child.feed_frame(f'{{"n":{i}}}'.encode())
        await _read_n(c1.frames(), 4, c1)
        await asyncio.sleep(0.05)
        # everything acked -> buffer trimmed to empty.
        assert host.buffered_seqs == []
        assert host.ack_cursor == 4
        await c1.close()
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_write_relays_to_child_stdin():
    child = _FakeChild()
    host, port = await _serve(child)
    try:
        c1 = await SessionHostClient.connect(port=port)
        await c1.attach(0)
        await c1.write(b'{"initialize":1}\n')
        await asyncio.sleep(0.05)
        assert bytes(child.stdin.buffer) == b'{"initialize":1}\n'
        await c1.close()
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_liveness_on_child_exit():
    child = _FakeChild()
    host, port = await _serve(child)
    try:
        c1 = await SessionHostClient.connect(port=port)
        await c1.attach(0)
        child.feed_frame(b'{"n":1}')
        gen = c1.frames()
        seq, _ = await asyncio.wait_for(gen.__anext__(), timeout=5)
        assert seq == 1
        await c1.ack(1)
        child.finish(0)
        # generator ends once the dead-liveness arrives.
        with pytest.raises(StopAsyncIteration):
            await asyncio.wait_for(gen.__anext__(), timeout=5)
        assert c1.child_alive is False
        await c1.close()
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_terminate_reaps_child():
    child = _FakeChild()
    host, port = await _serve(child)
    try:
        c1 = await SessionHostClient.connect(port=port)
        await c1.attach(0)
        await c1.terminate()
        await asyncio.sleep(0.05)
        assert child.killed is True
        await c1.close()
    finally:
        await host.close()


@pytest.mark.asyncio
async def test_host_survives_front_reset():
    """A front crashing (abrupt close) must not take the host down."""
    child = _FakeChild()
    host, port = await _serve(child)
    try:
        c1 = await SessionHostClient.connect(port=port)
        await c1.attach(0)
        child.feed_frame(b'{"n":1}')
        await _read_n(c1.frames(), 1, c1)
        # abrupt transport close (no graceful shutdown)
        c1._writer.transport.abort()
        await asyncio.sleep(0.05)
        # host is still serving: a new front can attach and reach the child.
        c2 = await SessionHostClient.connect(port=port)
        hello = await c2.attach(1)
        assert hello.child_pid == child.pid
        await c2.close()
    finally:
        await host.close()


# --------------------------------------------------------------------------
# winjob breakaway flags
# --------------------------------------------------------------------------
def test_breakaway_flag_composition():
    with_ba = winjob._kill_on_close_limit_flags(True)
    without = winjob._kill_on_close_limit_flags(False)
    kill = winjob._JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    breakaway = winjob._JOB_OBJECT_LIMIT_BREAKAWAY_OK
    # both keep kill-on-close
    assert with_ba & kill and without & kill
    # only the breakaway-ok variant sets the escape flag
    assert with_ba & breakaway
    assert not (without & breakaway)


def test_create_breakaway_flag_value():
    # documented Win32 constant
    assert winjob.CREATE_BREAKAWAY_FROM_JOB == 0x01000000


# --------------------------------------------------------------------------
# host index (durable session -> host endpoint map)
# --------------------------------------------------------------------------
def test_host_index_register_get_remove(tmp_path):
    from agent_bridge.session_host.host_index import HostIndex, HostRecord

    idx = HostIndex(tmp_path / "hosts.json")
    rec = HostRecord(session_id="s1", port=9000, host_pid=111, child_pid=222)
    idx.register(rec)
    assert "s1" in idx
    assert idx.get("s1").child_pid == 222
    assert len(idx) == 1
    assert idx.remove("s1") is True
    assert idx.remove("s1") is False
    assert len(idx) == 0


def test_host_index_persists_across_reload(tmp_path):
    from agent_bridge.session_host.host_index import HostIndex, HostRecord

    path = tmp_path / "hosts.json"
    idx = HostIndex(path)
    idx.register(HostRecord(session_id="s1", port=9000, host_pid=111, child_pid=222,
                            host_version="0.4.0-dev78"))
    idx.register(HostRecord(session_id="s2", port=9001, host_pid=333, child_pid=444))
    # fresh instance reads the same file
    idx2 = HostIndex(path)
    assert len(idx2) == 2
    assert idx2.get("s1").host_version == "0.4.0-dev78"
    assert idx2.get("s2").port == 9001


def test_host_index_prune_and_live(tmp_path):
    from agent_bridge.session_host.host_index import HostIndex, HostRecord

    idx = HostIndex(tmp_path / "hosts.json")
    idx.register(HostRecord(session_id="alive", port=1, host_pid=10, child_pid=20))
    idx.register(HostRecord(session_id="dead", port=2, host_pid=99, child_pid=30))

    def is_alive(pid: int) -> bool:
        return pid == 10

    live = idx.live_records(is_alive)
    assert [r.session_id for r in live] == ["alive"]
    pruned = idx.prune_dead(is_alive)
    assert [r.session_id for r in pruned] == ["dead"]
    assert "dead" not in idx and "alive" in idx


def test_host_index_from_state_file(tmp_path):
    from agent_bridge.session_host.host_index import HostRecord

    state = tmp_path / "host.json"
    state.write_text('{"pid": 111, "child_pid": 222, "port": 9000}')
    rec = HostRecord.from_state_file("s1", state, host_version="v1")
    assert rec.session_id == "s1"
    assert rec.host_pid == 111
    assert rec.child_pid == 222
    assert rec.port == 9000
    assert rec.host_version == "v1"
    assert rec.state_file == str(state)


def test_host_index_corrupt_file_is_ignored(tmp_path):
    from agent_bridge.session_host.host_index import HostIndex

    path = tmp_path / "hosts.json"
    path.write_text("{ not json")
    idx = HostIndex(path)  # must not raise
    assert len(idx) == 0


# --------------------------------------------------------------------------
# flag-gated start_session -> Session-Host mode, end to end (real subprocesses)
# --------------------------------------------------------------------------
_FAKE_AGENT_SRC = (
    "import asyncio, acp\n"
    "from acp.schema import InitializeResponse, NewSessionResponse, AgentCapabilities\n"
    "class Agent:\n"
    "    async def initialize(self, protocol_version, **kw):\n"
    "        return InitializeResponse(protocol_version=protocol_version, agent_capabilities=AgentCapabilities())\n"
    "    async def new_session(self, cwd, **kw):\n"
    "        return NewSessionResponse(session_id='host-mode-sess')\n"
    "    def __getattr__(self, name):\n"
    "        if name.startswith('_') or name == 'on_connect':\n"
    "            raise AttributeError(name)\n"
    "        async def _noop(*a, **k):\n"
    "            return None\n"
    "        return _noop\n"
    "asyncio.run(acp.run_agent(Agent()))\n"
)


@pytest.mark.asyncio
async def test_start_session_host_mode_end_to_end(tmp_path, monkeypatch):
    import os
    import sys

    from agent_bridge.db import Database
    from agent_bridge.session_manager import SessionManager
    from agent_bridge.transport import SpawnTarget

    agent_script = tmp_path / "fake_agent.py"
    agent_script.write_text(_FAKE_AGENT_SRC)
    fake_argv = [sys.executable, str(agent_script)]

    async def _fake_resolve(target, *, tracker=None, session_id=""):
        return fake_argv, str(tmp_path), dict(os.environ)

    monkeypatch.setattr("agent_bridge.transport.resolve_local_launch", _fake_resolve)

    db = Database(tmp_path / "s.db")
    mgr = SessionManager(
        db, session_host_enabled=True,
        session_host_state_dir=str(tmp_path / "hosts"),
    )
    host_pid = None
    child_pid = None
    try:
        target = SpawnTarget(type="local", cwd=str(tmp_path))
        session = await asyncio.wait_for(mgr.start_session(target), timeout=30)

        # ACP session created THROUGH the Session Host, host index registered.
        assert session.acp_session_id == "host-mode-sess"
        assert session.pid  # child pid surfaced via host-mode AcpClient
        assert mgr._host_index is not None and len(mgr._host_index) == 1
        rec = mgr._host_index.all()[0]
        host_pid, child_pid = rec.host_pid, rec.child_pid
        assert osutil_pid_alive(host_pid)

        # host-mode teardown DETACHES -- the child survives (goal 1).
        await session.client.shutdown()
        await asyncio.sleep(0.2)
        assert osutil_pid_alive(host_pid)  # host + child untouched by detach
    finally:
        for pid in (host_pid, child_pid):
            if pid:
                with contextlib.suppress(Exception):
                    if sys.platform == "win32":
                        import subprocess
                        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        import os as _os
                        import signal
                        _os.kill(pid, signal.SIGKILL)
        db.close()


@pytest.mark.asyncio
async def test_launch_session_host_process_owns_child(tmp_path):
    import signal
    import sys

    from agent_bridge.session_host.launcher import launch_session_host

    handle = await asyncio.to_thread(
        launch_session_host, [sys.executable, "-c", _STREAMER],
        state_dir=str(tmp_path),
    )
    try:
        assert handle.child_pid > 0 and handle.port > 0
        assert osutil_pid_alive(handle.host_pid)

        c = await SessionHostClient.connect(port=handle.port)
        hello = await c.attach(0)
        assert hello.child_pid == handle.child_pid
        await c.write(b'{"prompt":"go"}\n')
        seqs = []
        async for seq, data in c.frames():
            seqs.append(seq)
            await c.ack(seq)
            if b"turn_complete" in data:
                break
        assert len(seqs) >= 5
        await c.close()
    finally:
        with contextlib.suppress(Exception):
            handle.proc.terminate()
        with contextlib.suppress(Exception):
            handle.proc.wait(timeout=5)
        if sys.platform != "win32":
            import os as _os
            with contextlib.suppress(Exception):
                _os.kill(handle.child_pid, signal.SIGKILL)


def osutil_pid_alive(pid: int) -> bool:
    import sys
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        k.OpenProcess.restype = wintypes.HANDLE
        k.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        h = k.OpenProcess(0x1000, False, pid)
        if not h:
            return False
        code = wintypes.DWORD()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259
    import os as _os
    try:
        _os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


# --------------------------------------------------------------------------
# full-stack: a real AcpClient completes an ACP handshake THROUGH the host
# --------------------------------------------------------------------------
class _FakeAcpAgent:
    """Minimal ACP agent: answers initialize + new_session; no-ops the rest."""

    def __init__(self) -> None:
        self.initialized = False
        self.new_session_calls = 0

    async def initialize(self, protocol_version, **kwargs):
        from acp.schema import AgentCapabilities, InitializeResponse
        self.initialized = True
        return InitializeResponse(
            protocol_version=protocol_version,
            agent_capabilities=AgentCapabilities(),
        )

    async def new_session(self, cwd, **kwargs):
        from acp.schema import NewSessionResponse
        self.new_session_calls += 1
        return NewSessionResponse(session_id="fake-sess-1")

    def __getattr__(self, name):
        # route_request binds getattr(agent, <method>) for every ACP method at
        # build time; provide async no-ops for the ones this test never calls.
        # Raise for dunders / on_connect so the library's optional-hook probe
        # (getattr(agent, "on_connect", None)) resolves to None.
        if name.startswith("_") or name == "on_connect":
            raise AttributeError(name)

        async def _noop(*a, **k):
            return None

        return _noop


class _SockChild:
    """A child whose stdio is one end of a socketpair (the agent is the other)."""

    def __init__(self, reader, writer, pid) -> None:
        self.stdout = reader
        self.stdin = writer
        self._pid = pid

    @property
    def pid(self):
        return self._pid

    @property
    def returncode(self):
        return None  # stays alive for the test

    async def wait(self):
        await asyncio.sleep(3600)
        return 0


@pytest.mark.asyncio
async def test_full_stack_acp_handshake_through_host():
    import socket as _socket

    from acp.agent.connection import AgentSideConnection

    from agent_bridge.acp_client import AcpClient
    from agent_bridge.session_host.acp_adapter import open_acp_streams

    # socketpair: one end is the child's stdio (host side), the other the agent.
    host_sock, agent_sock = _socket.socketpair()
    host_reader, host_writer = await asyncio.open_connection(sock=host_sock)
    agent_reader, agent_writer = await asyncio.open_connection(sock=agent_sock)

    fake_agent = _FakeAcpAgent()
    # AgentSideConnection(input_stream=writer, output_stream=reader)
    _agent_conn = AgentSideConnection(fake_agent, agent_writer, agent_reader)

    child = _SockChild(host_reader, host_writer, pid=54321)
    host = SessionHost(child)
    port = await host.serve(port=0)

    client = await SessionHostClient.connect(port=port)
    await client.attach(0)
    streams = await open_acp_streams(client, start_from=0)

    acp = AcpClient()
    try:
        # initialize + new_session flow all the way through:
        # AcpClient -> adapter -> host -> child.stdin -> agent, and back.
        await asyncio.wait_for(
            acp.start_streams(streams.reader, streams.writer,
                              child_pid=child.pid, closer=streams.aclose),
            timeout=10,
        )
        assert acp.is_running is True
        assert acp.pid == 54321                       # informational child pid
        assert fake_agent.initialized is True

        sid = await asyncio.wait_for(acp.new_session(cwd="/tmp"), timeout=10)
        assert sid == "fake-sess-1"
        assert fake_agent.new_session_calls == 1

        # host-mode shutdown DETACHES (no child kill) -- intentional-only reaping.
        await acp.shutdown()
        assert child.returncode is None               # child untouched
    finally:
        with contextlib.suppress(Exception):
            await streams.aclose()
        await client.close()
        await host.close()
        for w in (agent_writer, host_writer):
            with contextlib.suppress(Exception):
                w.close()


# --------------------------------------------------------------------------
# ACP stream adapter (Phase 2 bridge): host <-> asyncio streams, byte-exact
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_acp_adapter_relays_both_directions_byte_exact():
    child = _FakeChild()
    host, port = await _serve(child)
    c1 = await SessionHostClient.connect(port=port)
    await c1.attach(0)
    streams = await open_acp_streams(c1, start_from=0)
    try:
        # agent -> client: a child ACP line must arrive byte-for-byte on reader.
        frame = b'{"jsonrpc":"2.0","method":"session/update","params":{"x":1}}\n'
        child.feed_frame(frame.rstrip(b"\n"))
        got = await asyncio.wait_for(streams.reader.readline(), timeout=5)
        assert got == frame

        # client -> agent: bytes written to writer must reach the child stdin.
        outbound = b'{"jsonrpc":"2.0","id":1,"method":"session/prompt"}\n'
        streams.writer.write(outbound)
        await streams.writer.drain()
        await asyncio.sleep(0.05)
        assert bytes(child.stdin.buffer) == outbound
    finally:
        await streams.aclose()
        await c1.close()
        await host.close()


@pytest.mark.asyncio
async def test_acp_adapter_auto_acks_frames():
    child = _FakeChild()
    host, port = await _serve(child)
    c1 = await SessionHostClient.connect(port=port)
    await c1.attach(0)
    streams = await open_acp_streams(c1, start_from=0, auto_ack=True)
    try:
        for i in range(1, 4):
            child.feed_frame(f'{{"n":{i}}}'.encode())
        # drain three lines through the adapter
        for _ in range(3):
            await asyncio.wait_for(streams.reader.readline(), timeout=5)
        await asyncio.sleep(0.05)
        # auto-ack advanced the host's durable cursor and trimmed the buffer.
        assert host.ack_cursor == 3
        assert host.buffered_seqs == []
    finally:
        await streams.aclose()
        await c1.close()
        await host.close()


# --------------------------------------------------------------------------
# launcher: survival adapter selection + real end-to-end via run_host
# --------------------------------------------------------------------------
def test_host_spawn_kwargs_per_os():
    kw = launcher.host_spawn_kwargs()
    import sys
    if sys.platform == "win32":
        assert kw["creationflags"] & winjob.CREATE_BREAKAWAY_FROM_JOB
        assert "start_new_session" not in kw
    else:
        assert kw["start_new_session"] is True
        assert "creationflags" not in kw


_STREAMER = (
    "import sys,time,json\n"
    "sys.stdout.write(json.dumps({'type':'ready'})+'\\n'); sys.stdout.flush()\n"
    "line=sys.stdin.readline()\n"
    "for i in range(1,6):\n"
    "    sys.stdout.write(json.dumps({'type':'update','chunk':i})+'\\n'); sys.stdout.flush(); time.sleep(0.05)\n"
    "sys.stdout.write(json.dumps({'type':'turn_complete'})+'\\n'); sys.stdout.flush()\n"
    "time.sleep(1.0)\n"
)


@pytest.mark.asyncio
async def test_run_host_end_to_end_reattach(tmp_path):
    """run_host spawns a real child process; a front reattaches mid-stream."""
    import sys

    state = tmp_path / "host.json"
    ready = asyncio.Event()
    task = asyncio.create_task(
        launcher.run_host(
            [sys.executable, "-c", _STREAMER],
            port=0, state_file=str(state), ready=ready,
        )
    )
    try:
        await asyncio.wait_for(ready.wait(), timeout=10)
        import json as _json
        meta = _json.loads(state.read_text())
        port = meta["port"]

        # front 1: attach fresh, drive the stream, read 2 frames, ack, detach.
        c1 = await SessionHostClient.connect(port=port)
        hello = await c1.attach(0)
        assert hello.child_pid == meta["child_pid"]
        await c1.write(b'{"prompt":"go"}\n')
        gen1 = c1.frames()
        first_seqs = []
        for _ in range(2):
            seq, _d = await asyncio.wait_for(gen1.__anext__(), timeout=10)
            first_seqs.append(seq)
            await c1.ack(seq)
        await c1.close()

        # front 2: reattach from the last-acked seq; drain to completion.
        c2 = await SessionHostClient.connect(port=port)
        await c2.attach(first_seqs[-1])
        saw_complete = False
        seqs2 = []
        async for seq, data in c2.frames():
            seqs2.append(seq)
            await c2.ack(seq)
            if b"turn_complete" in data:
                saw_complete = True
                break
        assert saw_complete
        assert seqs2[0] == first_seqs[-1] + 1        # no gap
        assert min(seqs2) > first_seqs[-1]           # no re-stream
        await c2.close()
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
