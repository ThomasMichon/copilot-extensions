"""Adapt a :class:`SessionHostClient` into the ``asyncio`` stream pair that the
ACP ``ClientSideConnection`` requires -- the Phase-2 bridge that lets ACP flow
*through* the Session Host transparently.

``acp_client.AcpClient.start(process)`` hands ``process.stdin``/``process.stdout``
to the ACP ``ClientSideConnection``, which insists on genuine
``asyncio.StreamReader``/``StreamWriter`` (it ``isinstance``-checks them). We
cannot hand it the raw host socket, because that socket carries the *multiplexed*
wire protocol (FRAME/ACK/control), not raw ACP.

The bridge uses a local ``socket.socketpair`` so ClientSideConnection gets real
asyncio streams while two relay tasks shuttle bytes to/from the host:

* host -> client: iterate ``client.frames()``, feed each frame's raw bytes into
  the pair (ClientSideConnection reads them as agent->client ACP), and **ack**
  the frame (advancing the durable delivery cursor).
* client -> host: read ClientSideConnection's outbound ACP bytes from the pair
  and relay them via ``client.write()`` into the child's stdin.

The ACP payloads are relayed **byte-for-byte** (the 1:1 invariant); the host
socket only adds the seq/ack envelope, which this adapter consumes.
"""

from __future__ import annotations

import asyncio
import logging
import socket
from collections.abc import Callable
from dataclasses import dataclass, field

from .client import SessionHostClient
from . import protocol as proto

log = logging.getLogger("agent-bridge.session-host")

_RELAY_CHUNK = 64 * 1024


@dataclass
class AcpStreams:
    """The asyncio stream pair to hand to the ACP ClientSideConnection."""

    reader: asyncio.StreamReader   # agent -> client ACP frames
    writer: asyncio.StreamWriter   # client -> agent ACP frames
    _pair_reader: asyncio.StreamReader
    _pair_writer: asyncio.StreamWriter
    _tasks: list[asyncio.Task]
    # Fired at most once when the host->front relay ends while the child is
    # still alive -- i.e. the loopback/forwarded transport dropped but the
    # Session Host + its child survive. This is the in-session ``disconnected``
    # signal the frontend's liveness-driven reattach driver keys on (P1). Set by
    # the caller AFTER the AcpClient exists (typically ``client.mark_transport_lost``).
    on_transport_lost: Callable[[], None] | None = field(default=None)

    async def aclose(self) -> None:
        for t in self._tasks:
            t.cancel()
        for t in self._tasks:
            try:
                await t
            except asyncio.CancelledError:
                pass
        for w in (self.writer, self._pair_writer):
            try:
                w.close()
            except OSError:
                pass


async def open_acp_streams(
    client: SessionHostClient, *, start_from: int = 0, auto_ack: bool = True,
) -> AcpStreams:
    """Bridge ``client`` into an ACP-ready ``(reader, writer)`` pair.

    ``start_from`` is the last durably-acked frame seq (the caller must have
    already ``attach``-ed the client at this cursor). Returns :class:`AcpStreams`;
    hand ``.reader``/``.writer`` to the ACP connection and ``await .aclose()``
    when done.
    """
    a, b = socket.socketpair()
    a.setblocking(False)
    b.setblocking(False)
    # End A: handed to the ACP connection. A single ACP frame can exceed
    # asyncio's default 64 KiB StreamReader line limit (e.g. a large PR diff),
    # so size the relay readers to the protocol's max message -- matching the
    # child-stdout reader -- or ``readline`` raises LimitOverrunError and the
    # ACP receive loop dies ("Connection closed").
    acp_reader, acp_writer = await asyncio.open_connection(
        sock=a, limit=proto.MAX_MESSAGE_BYTES)
    # End B: our relay side.
    relay_reader, relay_writer = await asyncio.open_connection(
        sock=b, limit=proto.MAX_MESSAGE_BYTES)

    streams = AcpStreams(
        reader=acp_reader, writer=acp_writer,
        _pair_reader=relay_reader, _pair_writer=relay_writer, _tasks=[],
    )

    async def _host_to_acp() -> None:
        try:
            async for seq, data in client.frames():
                relay_writer.write(data)
                await relay_writer.drain()
                if auto_ack:
                    await client.ack(seq)
        except (OSError, ConnectionError):
            pass
        finally:
            try:
                relay_writer.write_eof()
            except (OSError, RuntimeError):
                pass
            # ``frames()`` ended. If the child is still alive, the host->front
            # transport dropped underneath us (loopback/forwarded socket
            # EOF/reset) rather than the child exiting -- that is the in-session
            # ``disconnected`` condition. Notify the frontend so its
            # liveness-driven driver can reattach by cursor (P1). A genuine
            # child exit (LIVENESS(dead), which clears ``child_alive``) is NOT a
            # transport loss and must not trigger a reattach.
            if client.child_alive and streams.on_transport_lost is not None:
                try:
                    streams.on_transport_lost()
                except Exception:
                    log.debug("on_transport_lost callback raised", exc_info=True)

    async def _acp_to_host() -> None:
        try:
            while True:
                data = await relay_reader.read(_RELAY_CHUNK)
                if not data:
                    break
                await client.write(data)
        except (OSError, ConnectionError):
            pass

    streams._tasks = [
        asyncio.create_task(_host_to_acp(), name="session-host-to-acp"),
        asyncio.create_task(_acp_to_host(), name="acp-to-session-host"),
    ]
    return streams
