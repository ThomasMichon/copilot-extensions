"""The Session Host: owns one Copilot ``--acp`` child and serves a reattachable
endpoint, relaying ACP frames 1:1 with a seq/ack control channel.

Asyncio-based to match agent-bridge. The host is intentionally child-agnostic:
it accepts any object satisfying :class:`ChildProcess` (a real
``asyncio.subprocess.Process`` does), so it is unit-testable in-process with a
fake child and has no hard dependency on the spawn path.

Frame identity: ACP over stdio is newline-delimited JSON-RPC, so each
newline-terminated line from the child's stdout is one frame. The host assigns a
**monotonic sequence** it never renumbers -- that stable sequence is what lets a
reattaching frontend (or a cold frontend rebuild) preserve delivery-cursor
identity (Phase 3 anchors event IDs to it).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol, runtime_checkable

from . import protocol as proto

log = logging.getLogger("agent-bridge.session-host")


@runtime_checkable
class ChildProcess(Protocol):
    """The subset of ``asyncio.subprocess.Process`` the host relies on."""

    stdin: asyncio.StreamWriter | None
    stdout: asyncio.StreamReader | None

    @property
    def pid(self) -> int | None: ...

    @property
    def returncode(self) -> int | None: ...

    async def wait(self) -> int: ...


class _Front:
    """State for the single currently-attached frontend."""

    __slots__ = ("reader", "writer", "next_seq", "send_lock", "closed")

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
                 next_seq: int) -> None:
        self.reader = reader
        self.writer = writer
        self.next_seq = next_seq          # next frame seq owed to this front
        self.send_lock = asyncio.Lock()
        self.closed = False


class SessionHost:
    """Owns a child + its pipes; serves a reattachable 1:1-ACP endpoint.

    The host keeps reading and buffering child frames whether or not a frontend
    is attached -- child progress is decoupled from frontend presence, which is
    the entire point. Buffered frames past the durable ``ack`` cursor are
    retained so a reattaching frontend resumes with no gap and no re-stream.
    """

    def __init__(self, child: ChildProcess) -> None:
        self._child = child
        self._frames: dict[int, bytes] = {}
        self._max_seq = 0
        self._ack_cursor = 0
        self._front: _Front | None = None
        self._child_exit: int | None = None
        self._child_done = asyncio.Event()
        self._server: asyncio.base_events.Server | None = None
        self._reader_task: asyncio.Task | None = None
        self._closing = False

    # -- introspection (tests / diagnostics) -------------------------------
    @property
    def max_seq(self) -> int:
        return self._max_seq

    @property
    def ack_cursor(self) -> int:
        return self._ack_cursor

    @property
    def buffered_seqs(self) -> list[int]:
        return sorted(self._frames)

    @property
    def child_pid(self) -> int | None:
        return self._child.pid

    @property
    def child_alive(self) -> bool:
        return self._child.returncode is None

    # -- child reader ------------------------------------------------------
    async def _reader_loop(self) -> None:
        stdout = self._child.stdout
        assert stdout is not None
        while True:
            line = await stdout.readline()
            if not line:
                break
            self._max_seq += 1
            self._frames[self._max_seq] = line
            front = self._front
            if front is not None and not front.closed:
                await self._flush_front(front)
        # child stdout closed -> child exiting
        try:
            self._child_exit = await self._child.wait()
        except ProcessLookupError:
            self._child_exit = -1
        self._child_done.set()
        front = self._front
        if front is not None and not front.closed:
            await self._safe_send(front, proto.MsgType.LIVENESS,
                                  proto.pack_liveness(False, self._child_exit or 0))

    # -- frontend serving --------------------------------------------------
    async def _flush_front(self, front: _Front) -> None:
        """Send every buffered frame the front is still owed, in order.

        Idempotent and re-entrant-safe: both the reader loop (new frame) and the
        attach handshake (replay) call it; the per-front lock serializes sends
        and ``next_seq`` guarantees no gap and no duplicate.
        """
        async with front.send_lock:
            while not front.closed:
                seq = front.next_seq
                data = self._frames.get(seq)
                if data is None:
                    if seq > self._max_seq:
                        return  # nothing more buffered yet
                    front.next_seq = seq + 1  # trimmed below ack; skip
                    continue
                try:
                    await proto.write_message(
                        front.writer, proto.MsgType.FRAME,
                        proto.pack_frame(seq, data),
                    )
                except (OSError, ConnectionError):
                    front.closed = True
                    return
                front.next_seq = seq + 1

    async def _safe_send(self, front: _Front, mtype: proto.MsgType,
                         payload: bytes) -> None:
        async with front.send_lock:
            if front.closed:
                return
            try:
                await proto.write_message(front.writer, mtype, payload)
            except (OSError, ConnectionError):
                front.closed = True

    async def _handle_front(self, reader: asyncio.StreamReader,
                            writer: asyncio.StreamWriter) -> None:
        # Displace any stale front (its process almost always already gone).
        old = self._front
        if old is not None and not old.closed:
            old.closed = True
            with _suppress_close(old.writer):
                old.writer.close()

        first = await proto.read_message(reader)
        if first is None or first[0] != proto.MsgType.ATTACH:
            writer.close()
            return
        last_acked = proto.unpack_u64(first[1])
        self._ack_cursor = max(self._ack_cursor, last_acked)
        front = _Front(reader, writer, next_seq=last_acked + 1)
        self._front = front

        await self._safe_send(
            front, proto.MsgType.HELLO,
            proto.pack_u64(self._max_seq) + proto.pack_u64(self._child.pid or 0),
        )
        if not self.child_alive:
            await self._safe_send(front, proto.MsgType.LIVENESS,
                                  proto.pack_liveness(False, self._child_exit or 0))
        await self._flush_front(front)

        try:
            while not self._closing:
                msg = await proto.read_message(reader)
                if msg is None:
                    break  # front detached; child keeps running
                mtype, payload = msg
                if mtype == proto.MsgType.ACK:
                    self._on_ack(proto.unpack_u64(payload))
                elif mtype == proto.MsgType.WRITE:
                    await self._on_write(payload)
                elif mtype == proto.MsgType.TERMINATE:
                    await self._terminate_child()
                    break
        except proto.ProtocolError:
            log.warning("protocol error from frontend; dropping connection")
        finally:
            front.closed = True
            if self._front is front:
                self._front = None
            with _suppress_close(writer):
                writer.close()

    def _on_ack(self, seq: int) -> None:
        self._ack_cursor = max(self._ack_cursor, seq)
        # Trim durably-acked frames from the buffer.
        for s in [s for s in self._frames if s <= self._ack_cursor]:
            del self._frames[s]

    async def _on_write(self, payload: bytes) -> None:
        stdin = self._child.stdin
        if stdin is None:
            return
        try:
            stdin.write(payload)
            await stdin.drain()
        except (OSError, ConnectionError):
            log.warning("failed to relay WRITE to child stdin")

    async def _terminate_child(self) -> None:
        """Explicit, sanctioned reap (goal 1's only allowed termination path)."""
        rc = self._child.returncode
        if rc is not None:
            return
        proc = self._child
        # Best-effort: prefer the real Process tree-kill if available.
        killer = getattr(proc, "kill", None)
        if callable(killer):
            try:
                proc.kill()  # type: ignore[attr-defined]
            except (ProcessLookupError, OSError):
                pass

    # -- lifecycle ---------------------------------------------------------
    async def serve(self, host: str = "127.0.0.1", port: int = 0) -> int:
        """Start serving on loopback. Returns the bound port."""
        self._reader_task = asyncio.create_task(self._reader_loop())
        self._server = await asyncio.start_server(self._handle_front, host, port)
        sock = self._server.sockets[0]
        return sock.getsockname()[1]

    async def serve_forever(self) -> None:
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def close(self) -> None:
        self._closing = True
        if self._server is not None:
            self._server.close()
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass


class _suppress_close:
    def __init__(self, writer: asyncio.StreamWriter) -> None:
        self._writer = writer

    def __enter__(self) -> None:
        return None

    def __exit__(self, *exc: object) -> bool:
        return True  # swallow close-time errors
