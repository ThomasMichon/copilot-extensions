"""Frontend-side connector for a Session Host.

Dials a host, performs the reattach handshake, and exposes a clean frame-level
API: iterate ACP frames (with their stable ``seq``), ``ack`` them to advance the
durable delivery cursor, ``write`` client->agent ACP bytes, and ``terminate``.

Phase 1 exposes this frame-level surface (fully testable). Phase 2 adapts it
into the ``asyncio.StreamReader``/``StreamWriter`` pair that
``acp_client.AcpClient`` feeds to the ACP ``ClientSideConnection``, so ACP flows
through the host transparently.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from . import protocol as proto


@dataclass
class Hello:
    max_seq: int
    child_pid: int
    min_seq: int = 0


class SessionHostClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._child_alive = True
        self._child_exit = 0
        self._closed = False

    @classmethod
    async def connect(cls, host: str = "127.0.0.1", *, port: int) -> SessionHostClient:
        # Size the reader to the protocol's max message: a relayed ACP frame can
        # far exceed asyncio's default 64 KiB line limit (e.g. a large PR diff).
        reader, writer = await asyncio.open_connection(
            host, port, limit=proto.MAX_MESSAGE_BYTES)
        return cls(reader, writer)

    @property
    def child_alive(self) -> bool:
        return self._child_alive

    @property
    def child_exit_code(self) -> int:
        return self._child_exit

    async def attach(self, last_acked: int = 0, *, nonce: bytes = b"") -> Hello:
        """Send the reattach handshake; return the host's HELLO.

        ``last_acked`` is the last frame ``seq`` this frontend durably recorded.
        The host replays everything after it -- no gap, no re-stream. ``nonce``
        is the optional connect-auth token: a host launched with a nonce closes
        the connection unless it matches (defense-in-depth so a stray same-user
        process cannot drive the child by dialing the port); an unsecured host
        ignores it.
        """
        await proto.write_message(self._writer, proto.MsgType.ATTACH,
                                  proto.pack_attach(last_acked, nonce))
        msg = await proto.read_message(self._reader)
        if msg is None or msg[0] != proto.MsgType.HELLO:
            raise ConnectionError("session host did not send HELLO")
        payload = msg[1]
        return Hello(
            max_seq=proto.unpack_u64(payload[:8]),
            child_pid=proto.unpack_u64(payload[8:16]),
            min_seq=proto.unpack_u64(payload[16:24]) if len(payload) >= 24 else 0,
        )

    async def probe(self, *, nonce: bytes) -> tuple[Hello, bool, int]:
        """Read identity/liveness without taking the terminal's frontend lease."""
        await proto.write_message(self._writer, proto.MsgType.PROBE, proto.pack_attach(0, nonce))
        hello = await proto.read_message(self._reader)
        status = await proto.read_message(self._reader)
        if hello is None or hello[0] != proto.MsgType.HELLO or status is None or status[0] != proto.MsgType.LIVENESS:
            raise ConnectionError("execution host did not authenticate its liveness response")
        payload = hello[1]
        alive, code = proto.unpack_liveness(status[1])
        return Hello(proto.unpack_u64(payload[:8]), proto.unpack_u64(payload[8:16])), alive, code

    async def resize(self, rows: int, columns: int) -> None:
        await proto.write_message(self._writer, proto.MsgType.RESIZE, proto.pack_resize(rows, columns))

    async def activate(self, *, child_pid: int, nonce: bytes) -> None:
        await proto.write_message(self._writer, proto.MsgType.START, proto.pack_attach(child_pid, nonce))
        answer = await proto.read_message(self._reader)
        if answer is None or answer[0] != proto.MsgType.HELLO:
            raise ConnectionError("native execution activation was not acknowledged")

    async def retire(self, *, child_pid: int, nonce: bytes) -> int:
        await proto.write_message(self._writer, proto.MsgType.TERMINATE, proto.pack_attach(child_pid, nonce))
        answer = await proto.read_message(self._reader)
        if answer is None or answer[0] != proto.MsgType.LIVENESS:
            raise ConnectionError("native execution retirement was not acknowledged")
        alive, code = proto.unpack_liveness(answer[1])
        if alive:
            raise ConnectionError("native child is still alive")
        return code

    async def frames(self):
        """Async-iterate ``(seq, frame_bytes)`` until the connection ends.

        A ``LIVENESS(dead)`` control message updates ``child_alive`` and ends
        iteration once the buffered frames are drained.
        """
        while True:
            msg = await proto.read_message(self._reader)
            if msg is None:
                return
            mtype, payload = msg
            if mtype == proto.MsgType.FRAME:
                yield proto.unpack_frame(payload)
            elif mtype == proto.MsgType.LIVENESS:
                self._child_alive, self._child_exit = proto.unpack_liveness(payload)
                if not self._child_alive:
                    return

    async def ack(self, seq: int) -> None:
        await proto.write_message(self._writer, proto.MsgType.ACK, proto.pack_u64(seq))

    async def write(self, data: bytes) -> None:
        """Relay client->agent ACP bytes into the child's stdin."""
        await proto.write_message(self._writer, proto.MsgType.WRITE, data)

    async def send_status(self, reapable: bool) -> None:
        """Tell the host whether the child is REAPABLE (idle, no active
        background tasks). Best-effort: a failed send must never disturb the
        ACP turn flow sharing this socket (#51)."""
        if self._closed:
            return
        try:
            await proto.write_message(
                self._writer, proto.MsgType.STATUS, proto.pack_flag(reapable))
        except (OSError, ConnectionError):
            pass

    async def detach(self, reapable: bool) -> None:
        """Signal a GRACEFUL disconnect (vs a hard drop) and the child's current
        reapable state, so the host reaps a reapable child promptly instead of
        after the unexpected-disconnect grace window. Best-effort (#51)."""
        if self._closed:
            return
        try:
            await proto.write_message(
                self._writer, proto.MsgType.DETACH, proto.pack_flag(reapable))
        except (OSError, ConnectionError):
            pass

    async def terminate(self) -> None:
        """Request the host reap the child (explicit, sanctioned termination)."""
        await proto.write_message(self._writer, proto.MsgType.TERMINATE)

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._writer.close()
            await self._writer.wait_closed()
        except (OSError, ConnectionError):
            pass
