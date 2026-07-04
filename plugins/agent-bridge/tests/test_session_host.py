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
# Phase 3: cursor-stable event identity across a front cycle / resync
# --------------------------------------------------------------------------
def _mk_session(db, sid="s1"):
    import time as _t
    db.create_session(session_id=sid, name="n", agent_name=None, caller_id=None,
                      target_dir=".", target_type="local", status="idle",
                      now=_t.time(), target_json="{}")


def test_reattach_preserves_event_ids_and_cursor(tmp_path):
    """A frontend restart (rehydrate) must NOT renumber events or orphan the
    delivery cursor -- the identity is append-only and stable."""
    import time as _t

    from agent_bridge.db import Database
    from agent_bridge.events import EventLog

    db = Database(tmp_path / "e.db")
    try:
        _mk_session(db)
        log1 = EventLog(db=db, session_id="s1")
        for i in range(5):
            log1.append("agent_message", {"text": f"m{i}"})
        db.flush()
        ids_before = [e.id for e in log1.get_events(0)]
        assert ids_before == [1, 2, 3, 4, 5]
        db.set_cursor("nf", "s1", 3, _t.time())  # consumer acked up to id 3

        # simulate a frontend restart: EventLog is rebuilt from the DB
        log2 = EventLog.from_db(db, "s1")
        assert [e.id for e in log2.get_events(0)] == ids_before   # no renumbering
        cur = db.get_cursor("nf", "s1")
        assert cur == 3                                           # cursor intact
        assert [e.id for e in log2.get_events(after=cur)] == [4, 5]  # no gap/dup
        assert log2.append("x", {}).id == 6                       # append-only continues
    finally:
        db.close()


def test_rebuild_resets_delivery_cursors(tmp_path):
    """resync's rebuild renumbers from 1; the monotonic cursor must be reset so
    a consumer re-reads the rebuilt log instead of stalling past its end."""
    import time as _t

    from agent_bridge.db import Database
    from agent_bridge.events import EventLog

    db = Database(tmp_path / "e.db")
    try:
        _mk_session(db)
        log = EventLog(db=db, session_id="s1")
        for i in range(5):
            log.append("agent_message", {"text": f"m{i}"})
        db.flush()
        db.set_cursor("nf", "s1", 5, _t.time())   # consumer fully caught up
        assert db.get_cursor("nf", "s1") == 5

        # resync rebuilds to a shorter authoritative log (ids renumber 1..N)
        n = log.rebuild([("agent_message", {"text": "only-one"})])
        db.flush()
        assert n == 1
        # cursor reset -> consumer re-reads the rebuilt log rather than seeing
        # nothing (its old ack id 5 is now past the 1-event log).
        assert db.get_cursor("nf", "s1") == 0
        assert [e.id for e in log.get_events(after=0)] == [1]
    finally:
        db.close()


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
async def test_reattach_session_hosts_on_restart(tmp_path, monkeypatch):
    import os
    import sys

    from agent_bridge.db import Database
    from agent_bridge.models import SessionStatus
    from agent_bridge.session_manager import SessionManager
    from agent_bridge.transport import SpawnTarget

    agent_script = tmp_path / "fake_agent.py"
    agent_script.write_text(_FAKE_AGENT_SRC)
    fake_argv = [sys.executable, str(agent_script)]

    async def _fake_resolve(target, *, tracker=None, session_id=""):
        return fake_argv, str(tmp_path), dict(os.environ)

    monkeypatch.setattr("agent_bridge.transport.resolve_local_launch", _fake_resolve)

    dbpath = tmp_path / "s.db"
    statedir = str(tmp_path / "hosts")
    host_pid = None
    child_pid = None
    try:
        # --- frontend generation 1: start a host-backed session ---
        db1 = Database(dbpath)
        mgr1 = SessionManager(db1, session_host_enabled=True, session_host_state_dir=statedir)
        target = SpawnTarget(type="local", cwd=str(tmp_path))
        session = await asyncio.wait_for(mgr1.start_session(target), timeout=30)
        sid = session.session_id
        rec = mgr1._host_index.all()[0]
        host_pid, child_pid = rec.host_pid, rec.child_pid
        assert osutil_pid_alive(host_pid)

        # simulate a frontend restart: detach (host survives) + drop generation 1
        await session.client.shutdown()
        db1.close()
        assert osutil_pid_alive(host_pid)  # host untouched by the front going away

        # --- frontend generation 2: reattach to the surviving host ---
        db2 = Database(dbpath)
        mgr2 = SessionManager(db2, session_host_enabled=True, session_host_state_dir=statedir)
        assert mgr2.get_session(sid) is not None  # rehydrated (STOPPED)

        n = await asyncio.wait_for(mgr2.reattach_session_hosts(), timeout=30)
        assert n == 1
        s2 = mgr2.get_session(sid)
        assert s2.status == SessionStatus.IDLE
        assert s2.client is not None
        assert s2.acp_session_id == "host-mode-sess"      # session adopted, not re-created
        assert s2.client.pid == child_pid                 # same surviving child
        await s2.client.shutdown()
        db2.close()
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


# --------------------------------------------------------------------------
# Phase 4 -- host version-mux (protocol-version routing + stranded hosts)
# --------------------------------------------------------------------------
def test_version_mux_is_compatible():
    from agent_bridge.session_host import version_mux as vm

    assert vm.is_compatible(proto.PROTOCOL_VERSION) is True
    assert vm.is_compatible(proto.PROTOCOL_VERSION + 1) is False
    assert proto.PROTOCOL_VERSION in vm.SUPPORTED_PROTOCOL_VERSIONS


def test_plan_host_dispositions():
    from agent_bridge.session_host.version_mux import (
        HostDisposition as D,
        plan_host,
    )

    cur = proto.PROTOCOL_VERSION
    future = cur + 1

    # Compatible -> always reattach (drives a live child, drains a dead one).
    assert plan_host(protocol_version=cur, child_alive=True).disposition is D.REATTACH
    assert plan_host(protocol_version=cur, child_alive=False).disposition is D.REATTACH

    # Incompatible + child still running, no bound -> strand (goal 1).
    assert plan_host(protocol_version=future, child_alive=True).disposition is D.STRAND

    # Incompatible + child already stopped -> reap (frees the pinned install).
    assert (plan_host(protocol_version=future, child_alive=False).disposition
            is D.REAP_STOPPED)

    # Incompatible + child alive + past the sprawl bound -> force-reap.
    assert (plan_host(protocol_version=future, child_alive=True,
                      age_seconds=120.0, stale_reap_seconds=60.0).disposition
            is D.FORCE_REAP)
    # ...but under the bound it still strands.
    assert (plan_host(protocol_version=future, child_alive=True,
                      age_seconds=30.0, stale_reap_seconds=60.0).disposition
            is D.STRAND)
    # A zero/None bound never force-reaps.
    assert (plan_host(protocol_version=future, child_alive=True,
                      age_seconds=1e9, stale_reap_seconds=0).disposition
            is D.STRAND)


def test_host_record_protocol_version_persists(tmp_path):
    from agent_bridge.session_host.host_index import HostIndex, HostRecord

    path = tmp_path / "hosts.json"
    idx = HostIndex(path)
    idx.register(HostRecord(session_id="s1", port=9000, host_pid=1, child_pid=2,
                            protocol_version=7))
    # Legacy record with no explicit protocol_version defaults to the baseline 1.
    idx.register(HostRecord(session_id="s2", port=9001, host_pid=3, child_pid=4))
    idx2 = HostIndex(path)
    assert idx2.get("s1").protocol_version == 7
    assert idx2.get("s2").protocol_version == 1


def test_host_record_from_state_file_protocol_version(tmp_path):
    from agent_bridge.session_host.host_index import HostRecord

    with_pv = tmp_path / "with.json"
    with_pv.write_text('{"pid": 1, "child_pid": 2, "port": 9000, '
                       '"protocol_version": 5}')
    assert HostRecord.from_state_file("s", with_pv).protocol_version == 5

    # A state file written before protocol_version existed -> baseline 1.
    legacy = tmp_path / "legacy.json"
    legacy.write_text('{"pid": 1, "child_pid": 2, "port": 9000}')
    assert HostRecord.from_state_file("s", legacy).protocol_version == 1


@pytest.mark.asyncio
async def test_reattach_strands_then_reaps_incompatible_host(tmp_path, monkeypatch):
    """An incompatible-protocol host is left running while its child lives
    (goal 1), then reaped once the child stops -- the Phase-4 version-mux."""
    import os
    import sys

    from agent_bridge.db import Database
    from agent_bridge.models import SessionStatus
    from agent_bridge.session_manager import SessionManager
    from agent_bridge.transport import SpawnTarget

    agent_script = tmp_path / "fake_agent.py"
    agent_script.write_text(_FAKE_AGENT_SRC)
    fake_argv = [sys.executable, str(agent_script)]

    async def _fake_resolve(target, *, tracker=None, session_id=""):
        return fake_argv, str(tmp_path), dict(os.environ)

    monkeypatch.setattr("agent_bridge.transport.resolve_local_launch", _fake_resolve)

    dbpath = tmp_path / "s.db"
    statedir = str(tmp_path / "hosts")
    host_pid = None
    child_pid = None
    try:
        # gen 1: start a host-backed session, then simulate a frontend restart.
        db1 = Database(dbpath)
        mgr1 = SessionManager(db1, session_host_enabled=True,
                              session_host_state_dir=statedir)
        target = SpawnTarget(type="local", cwd=str(tmp_path))
        session = await asyncio.wait_for(mgr1.start_session(target), timeout=30)
        sid = session.session_id
        rec = mgr1._host_index.all()[0]
        host_pid, child_pid = rec.host_pid, rec.child_pid
        assert rec.protocol_version == proto.PROTOCOL_VERSION  # recorded on launch

        # Rewrite the record as an incompatible future protocol generation.
        rec.protocol_version = proto.PROTOCOL_VERSION + 1
        mgr1._host_index.register(rec)
        await session.client.shutdown()  # detach; host + child survive
        db1.close()
        assert osutil_pid_alive(host_pid)

        # gen 2: reattach must STRAND the incompatible host, not drive it.
        db2 = Database(dbpath)
        mgr2 = SessionManager(db2, session_host_enabled=True,
                              session_host_state_dir=statedir)
        n = await asyncio.wait_for(mgr2.reattach_session_hosts(), timeout=30)
        assert n == 0                                   # not reattached
        assert osutil_pid_alive(host_pid)               # left running (goal 1)
        assert sid in mgr2._host_index                  # record kept
        stranded = mgr2.stranded_host_records()
        assert [r.session_id for r in stranded] == [sid]
        assert mgr2.get_session(sid).status == SessionStatus.STOPPED
        db2.close()

        # The child reaches its own stop -> the stranded host is now reapable.
        from agent_bridge.session_host.osutil import kill_pid, pid_alive
        kill_pid(child_pid)
        for _ in range(100):
            if not pid_alive(child_pid):
                break
            await asyncio.sleep(0.05)
        assert not pid_alive(child_pid)

        db3 = Database(dbpath)
        mgr3 = SessionManager(db3, session_host_enabled=True,
                              session_host_state_dir=statedir)
        n3 = await asyncio.wait_for(mgr3.reattach_session_hosts(), timeout=30)
        assert n3 == 0
        assert sid not in mgr3._host_index               # record dropped
        for _ in range(100):
            if not osutil_pid_alive(host_pid):
                break
            await asyncio.sleep(0.05)
        assert not osutil_pid_alive(host_pid)            # host reaped
        db3.close()
    finally:
        for pid in (host_pid, child_pid):
            if pid:
                with contextlib.suppress(Exception):
                    if sys.platform == "win32":
                        import subprocess
                        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
                    else:
                        import os as _os
                        import signal
                        _os.kill(pid, signal.SIGKILL)


def test_config_stale_reap_default_is_disabled():
    from agent_bridge.models import ServiceConfig

    cfg = ServiceConfig()
    assert cfg.session_host_stale_reap_seconds == 0  # age bound off by default


@pytest.mark.asyncio
async def test_sweep_strands_then_force_reaps_over_bound(tmp_path, monkeypatch):
    """The periodic sweep leaves an incompatible host with a live child alone
    under the bound (goal 1), then force-reaps it once it outlives the bound."""
    import os
    import sys
    import time as _time

    from agent_bridge.db import Database
    from agent_bridge.session_manager import SessionManager
    from agent_bridge.transport import SpawnTarget

    agent_script = tmp_path / "fake_agent.py"
    agent_script.write_text(_FAKE_AGENT_SRC)
    fake_argv = [sys.executable, str(agent_script)]

    async def _fake_resolve(target, *, tracker=None, session_id=""):
        return fake_argv, str(tmp_path), dict(os.environ)

    monkeypatch.setattr("agent_bridge.transport.resolve_local_launch", _fake_resolve)

    dbpath = tmp_path / "s.db"
    statedir = str(tmp_path / "hosts")
    host_pid = None
    child_pid = None
    try:
        db = Database(dbpath)
        mgr = SessionManager(db, session_host_enabled=True,
                             session_host_state_dir=statedir,
                             session_host_stale_reap_seconds=0)  # bound off
        target = SpawnTarget(type="local", cwd=str(tmp_path))
        session = await asyncio.wait_for(mgr.start_session(target), timeout=30)
        rec = mgr._host_index.all()[0]
        host_pid, child_pid = rec.host_pid, rec.child_pid
        await session.client.shutdown()  # detach; host + child survive

        # Make the host an incompatible generation with a live child.
        rec.protocol_version = proto.PROTOCOL_VERSION + 1
        mgr._host_index.register(rec)

        # Bound disabled -> a live-child stranded host is left alone (goal 1).
        assert mgr.sweep_stranded_hosts() == 0
        assert osutil_pid_alive(host_pid)
        assert rec.session_id in mgr._host_index

        # Arm a small bound and backdate the host past it -> force-reaped.
        mgr._session_host_stale_reap_seconds = 5.0
        rec.created_at = _time.time() - 1000.0
        mgr._host_index.register(rec)
        assert mgr.sweep_stranded_hosts() == 1
        assert rec.session_id not in mgr._host_index
        from agent_bridge.session_host.osutil import pid_alive
        for _ in range(100):
            if not pid_alive(host_pid):
                break
            await asyncio.sleep(0.05)
        assert not pid_alive(host_pid)  # host reaped by the sprawl bound
        db.close()
    finally:
        for pid in (host_pid, child_pid):
            if pid:
                with contextlib.suppress(Exception):
                    if sys.platform == "win32":
                        import subprocess
                        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                       stdout=subprocess.DEVNULL,
                                       stderr=subprocess.DEVNULL)
                    else:
                        import os as _os
                        import signal
                        _os.kill(pid, signal.SIGKILL)


# --------------------------------------------------------------------------
# Graceful cancel + resume-on-reattach (redeploy protocol)
# --------------------------------------------------------------------------
def test_host_record_resume_flag_persists(tmp_path):
    from agent_bridge.session_host.host_index import HostIndex, HostRecord

    path = tmp_path / "hosts.json"
    idx = HostIndex(path)
    idx.register(HostRecord(session_id="s1", port=1, host_pid=1, child_pid=1))
    assert idx.get("s1").resume_on_reattach is False
    assert idx.set_resume_flag("s1", True) is True
    assert idx.set_resume_flag("s1", True) is False        # idempotent no-op
    assert idx.set_resume_flag("missing", True) is False
    # persists across reload
    assert HostIndex(path).get("s1").resume_on_reattach is True
    assert idx.set_resume_flag("s1", False) is True
    assert HostIndex(path).get("s1").resume_on_reattach is False


@pytest.mark.asyncio
async def test_graceful_cancel_for_redeploy(tmp_path):
    """Cancels only in-flight (RUNNING) turns, spares the excluded caller,
    flags host-backed mid-turn sessions for resume, and waits for settle."""
    from agent_bridge.db import Database
    from agent_bridge.models import SessionStatus
    from agent_bridge.session_host.host_index import HostRecord
    from agent_bridge.session_manager import Session, SessionManager
    from agent_bridge.transport import SpawnTarget

    db = Database(tmp_path / "s.db")
    try:
        mgr = SessionManager(db, session_host_enabled=True,
                             session_host_state_dir=str(tmp_path / "hosts"),
                             graceful_cancel_settle_seconds=5)
        cancels: list[str] = []

        class _FakeClient:
            def __init__(self, sess):
                self._sess = sess

            async def cancel_prompt(self):
                cancels.append(self._sess.session_id)
                self._sess.status = SessionStatus.IDLE  # turn settles at once

        target = SpawnTarget(type="local", cwd=str(tmp_path))
        for sid, status in [("run-a", SessionStatus.RUNNING),
                            ("run-self", SessionStatus.RUNNING),
                            ("idle-c", SessionStatus.IDLE)]:
            s = Session(sid, sid, target)
            s.status = status
            s.client = _FakeClient(s)
            mgr._sessions[sid] = s
            mgr._host_index.register(
                HostRecord(session_id=sid, port=1, host_pid=1, child_pid=1))

        res = await mgr.graceful_cancel_for_redeploy(exclude_session_id="run-self")

        assert res["enabled"] is True
        assert res["settled"] is True
        assert set(res["cancelled"]) == {"run-a"}     # only RUNNING, not excluded/idle
        assert cancels == ["run-a"]
        assert mgr._host_index.get("run-a").resume_on_reattach is True   # flagged
        assert mgr._host_index.get("run-self").resume_on_reattach is False  # spared
        assert mgr._host_index.get("idle-c").resume_on_reattach is False   # not mid-turn
    finally:
        db.close()


@pytest.mark.asyncio
async def test_graceful_cancel_noop_when_flag_off(tmp_path):
    from agent_bridge.db import Database
    from agent_bridge.session_manager import SessionManager

    db = Database(tmp_path / "s.db")
    try:
        mgr = SessionManager(db, session_host_enabled=False)
        res = await mgr.graceful_cancel_for_redeploy()
        assert res == {"cancelled": [], "settled": True, "enabled": False}
    finally:
        db.close()


def test_cli_drain_excludes_self_from_env(monkeypatch):
    """The CLI drain passes AGENT_BRIDGE_SESSION_ID as exclude_session_id so an
    agent updating its own bridge doesn't cancel the turn driving the update."""
    from agent_bridge.client import BridgeClient

    captured = {}

    class _C(BridgeClient):
        def __init__(self):
            pass

        def _request(self, method, path, *, body=None, request_timeout=None):
            captured["body"] = body
            return {}

    monkeypatch.setenv("AGENT_BRIDGE_SESSION_ID", "my-own-sess")
    _C().drain(timeout=10)
    assert captured["body"]["exclude_session_id"] == "my-own-sess"

    captured.clear()
    monkeypatch.delenv("AGENT_BRIDGE_SESSION_ID", raising=False)
    _C().drain(timeout=10)
    assert "exclude_session_id" not in captured["body"]


# --------------------------------------------------------------------------
# SSE stream closes promptly on shutdown / disconnect (#1789)
# --------------------------------------------------------------------------
class _QuietLog:
    """Fake EventLog whose wait always times out empty (a quiet stream)."""

    async def wait_for_events(self, after, timeout=2.0):
        await asyncio.sleep(min(timeout, 0.02))
        return []

    def active_tool_call(self):
        return None


class _FakeSession:
    def __init__(self):
        self.event_log = _QuietLog()


class _FakeServer:
    should_exit = False


async def _drain_stream(gen):
    async for _chunk in gen:
        pass


@pytest.mark.asyncio
async def test_sse_stream_closes_on_shutdown():
    """The SSE generator must return once uvicorn's should_exit flips, so it
    never pins the daemon's graceful shutdown open (#1789)."""
    from agent_bridge.routes.sessions import _sse_event_stream

    server = _FakeServer()

    async def _connected():
        return False

    gen = _sse_event_stream(_FakeSession(), 0, server=server,
                            is_disconnected=_connected)

    async def _flip():
        await asyncio.sleep(0.1)
        server.should_exit = True

    asyncio.ensure_future(_flip())
    # Must terminate (not hang) shortly after should_exit -- fail loud on hang.
    await asyncio.wait_for(_drain_stream(gen), timeout=3.0)


@pytest.mark.asyncio
async def test_sse_stream_closes_on_client_disconnect():
    from agent_bridge.routes.sessions import _sse_event_stream

    state = {"disconnected": False}

    async def _is_disc():
        return state["disconnected"]

    gen = _sse_event_stream(_FakeSession(), 0, server=_FakeServer(),
                            is_disconnected=_is_disc)

    async def _flip():
        await asyncio.sleep(0.1)
        state["disconnected"] = True

    asyncio.ensure_future(_flip())
    await asyncio.wait_for(_drain_stream(gen), timeout=3.0)


@pytest.mark.asyncio
async def test_sse_stream_yields_events_before_shutdown():
    """A stream still delivers queued events, then closes on shutdown."""
    from agent_bridge.routes.sessions import _sse_event_stream

    class _OneShotLog:
        def __init__(self):
            self._sent = False

        async def wait_for_events(self, after, timeout=2.0):
            if not self._sent:
                self._sent = True

                class _E:
                    id = 1
                    event = "agent_message"
                    data = {"text": "hi"}
                    timestamp = 123.0
                return [_E()]
            await asyncio.sleep(min(timeout, 0.02))
            return []

        def active_tool_call(self):
            return None

    class _S:
        def __init__(self):
            self.event_log = _OneShotLog()

    server = _FakeServer()

    async def _connected():
        return False

    chunks = []

    async def _collect():
        async for c in _sse_event_stream(_S(), 0, server=server,
                                         is_disconnected=_connected):
            chunks.append(c)

    async def _flip():
        await asyncio.sleep(0.1)
        server.should_exit = True

    asyncio.ensure_future(_flip())
    await asyncio.wait_for(_collect(), timeout=3.0)
    assert any("agent_message" in c and "id: 1" in c for c in chunks)

# --------------------------------------------------------------------------
# Explicit end/destroy reaps the host-backed child (#1786)
# --------------------------------------------------------------------------
def _kill_pids(pids):
    import os as _os
    import signal
    import subprocess
    import sys
    for pid in pids:
        if not pid:
            continue
        with contextlib.suppress(Exception):
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            else:
                _os.kill(pid, signal.SIGKILL)


async def _wait_dead(pid, timeout=5.0):
    for _ in range(int(timeout / 0.05)):
        if not osutil_pid_alive(pid):
            return True
        await asyncio.sleep(0.05)
    return not osutil_pid_alive(pid)


@pytest.mark.asyncio
async def test_end_session_reaps_host(tmp_path, monkeypatch):
    """end_session (the CLI `end` / DELETE) must REAP the host-backed child and
    drop the index record -- not detach-and-orphan like stop (#1786)."""
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
    host_pid = child_pid = None
    try:
        mgr = SessionManager(db, session_host_enabled=True,
                             session_host_state_dir=str(tmp_path / "hosts"))
        session = await asyncio.wait_for(
            mgr.start_session(SpawnTarget(type="local", cwd=str(tmp_path))),
            timeout=30)
        rec = mgr._host_index.all()[0]
        host_pid, child_pid = rec.host_pid, rec.child_pid
        assert osutil_pid_alive(host_pid)

        await asyncio.wait_for(mgr.end_session(session.session_id), timeout=30)

        # Index record dropped, host + child reaped.
        assert mgr._host_index.get(session.session_id) is None
        assert len(mgr._host_index) == 0
        assert await _wait_dead(host_pid), "host survived end_session"
        assert await _wait_dead(child_pid), "child survived end_session"
        db.close()
    finally:
        _kill_pids((host_pid, child_pid))


@pytest.mark.asyncio
async def test_reattach_reaps_orphaned_host(tmp_path, monkeypatch):
    """A live host whose session is gone (row deleted / pre-#1786 orphan) is
    reaped on reattach instead of leaking forever."""
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

    dbpath = tmp_path / "s.db"
    statedir = str(tmp_path / "hosts")
    host_pid = child_pid = None
    try:
        db1 = Database(dbpath)
        mgr1 = SessionManager(db1, session_host_enabled=True,
                              session_host_state_dir=statedir)
        session = await asyncio.wait_for(
            mgr1.start_session(SpawnTarget(type="local", cwd=str(tmp_path))),
            timeout=30)
        sid = session.session_id
        rec = mgr1._host_index.all()[0]
        host_pid, child_pid = rec.host_pid, rec.child_pid
        # Simulate the pre-fix orphan: drop the session row + detach, but leave
        # the host running and its index record in place.
        await session.client.shutdown()
        db1.delete_session(sid)
        db1.close()
        assert osutil_pid_alive(host_pid)

        # Fresh frontend: rehydrate won't see the deleted session, so reattach
        # finds a live host with no adoptable session -> reap it.
        db2 = Database(dbpath)
        mgr2 = SessionManager(db2, session_host_enabled=True,
                              session_host_state_dir=statedir)
        assert sid in mgr2._host_index          # orphan record present pre-reattach
        n = await asyncio.wait_for(mgr2.reattach_session_hosts(), timeout=30)
        assert n == 0                           # nothing adoptable
        assert sid not in mgr2._host_index      # orphan record dropped
        assert await _wait_dead(host_pid), "orphaned host not reaped"
        db2.close()
    finally:
        _kill_pids((host_pid, child_pid))
