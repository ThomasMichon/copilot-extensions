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


class SessionHostClient:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self._child_alive = True
        self._child_exit = 0
        self._closed = False

    @classmethod
    async def connect(cls, host: str = "127.0.0.1", *, port: int) -> SessionHostClient:
        reader, writer = await asyncio.open_connection(host, port)
        return cls(reader, writer)

    @property
    def child_alive(self) -> bool:
        return self._child_alive

    @property
    def child_exit_code(self) -> int:
        return self._child_exit

    async def attach(self, last_acked: int = 0) -> Hello:
        """Send the reattach handshake; return the host's HELLO.

        ``last_acked`` is the last frame ``seq`` this frontend durably recorded.
        The host replays everything after it -- no gap, no re-stream.
        """
        await proto.write_message(self._writer, proto.MsgType.ATTACH,
                                  proto.pack_u64(last_acked))
        msg = await proto.read_message(self._reader)
        if msg is None or msg[0] != proto.MsgType.HELLO:
            raise ConnectionError("session host did not send HELLO")
        payload = msg[1]
        return Hello(max_seq=proto.unpack_u64(payload[:8]),
                     child_pid=proto.unpack_u64(payload[8:16]))

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
