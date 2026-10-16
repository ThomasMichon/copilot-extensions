"""Exact-port relay staging; transport preparation never admits credentials."""

from __future__ import annotations

import asyncio
import os
import socket
import threading
import time

from credential_relay.server import CredentialRelayServer
from zdd.private_protocol import Deadline, PrivateIPCError, Refusal

from .relay_runtime import capture_relay
from .resolver_runtime import _encode


class StagedRelay:
    """Own one reserved socket and its closed-admission async transport.

    Reserve and activate are short receiver-fenced actions. Prepare runs on the
    same event loop outside that fence, within the original reservation budget.
    No ordinary relay startup, port reclamation or fallback is permitted.
    """

    def __init__(self, relay: CredentialRelayServer, deadline: Deadline) -> None:
        deadline.remaining()
        if (
            type(relay) is not CredentialRelayServer or relay._server is not None
            or type(relay.port) is not int or not 0 < relay.port <= 65535
            or relay.stats.active_connections
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._loop = asyncio.get_running_loop()
        self._thread = threading.current_thread()
        self._pid = os.getpid()
        self._relay = relay
        self._wire = _encode(capture_relay(relay))
        self._handler = relay._handle_client
        self._port = relay.port
        self._deadline = deadline
        self._transport: asyncio.Server | None = None
        self._socket: socket.socket | None = None
        self._clients: dict[asyncio.Task[None], asyncio.StreamWriter] = {}
        self._attempted = False
        self._prepared = False
        self._active = False
        self._closed = False
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == "nt":
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            else:
                # Reuse departed connections, never a simultaneously listening owner.
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.setblocking(False)
            sock.bind(("127.0.0.1", self._port))
            sock.listen(128)
            deadline.remaining()
        except BaseException as error:
            sock.close()
            if isinstance(error, OSError):
                raise PrivateIPCError(Refusal.AUTHORITY) from None
            raise
        self._socket = sock

    @property
    def active(self) -> bool:
        return self._active and not self._closed

    def _owner(self) -> None:
        if (
            os.getpid() != self._pid or threading.current_thread() is not self._thread
            or asyncio.get_running_loop() is not self._loop
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)

    def verify(self) -> None:
        """Check retained resources and captured objects without renewing time."""
        self._owner()
        if self._closed:
            raise PrivateIPCError(Refusal.CLOSED)
        try:
            sockets = (
                self._transport.sockets if self._transport is not None
                else (self._socket,)
            )
            if (
                len(sockets) != 1 or sockets[0] is None
                or sockets[0].getsockname() != ("127.0.0.1", self._port)
                or sockets[0].getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) != 1
                or self._relay.port != self._port
                or self._relay._handle_client != self._handler
                or self._relay._server is not (self._transport if self._active else None)
                or (self._prepared and (
                    self._transport is None or not self._transport.is_serving()
                ))
                or _encode(capture_relay(self._relay)) != self._wire
            ):
                raise PrivateIPCError(Refusal.AUTHORITY)
        except (OSError, AttributeError, TypeError, ValueError):
            raise PrivateIPCError(Refusal.AUTHORITY) from None

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # Decide at accept time, not when a scheduled coroutine eventually runs.
        if not self.active:
            writer.close()
            return
        task = self._loop.create_task(self._handler(reader, writer))
        self._clients[task] = writer
        task.add_done_callback(self._finished)

    def _finished(self, task: asyncio.Task[None]) -> None:
        self._clients.pop(task, None)
        if not task.cancelled() and task.exception() is not None:
            self._loop.call_exception_handler({
                "message": "staged credential relay client failed", "task": task,
            })

    async def prepare(self) -> None:
        """Prepare async accepts outside routing authority, with admission shut."""
        self.verify()
        if self._attempted or self._active:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._attempted = True
        try:
            remaining = self._deadline.remaining()
            self._transport = await asyncio.wait_for(
                asyncio.start_server(self._accept, sock=self._socket),
                timeout=remaining,
            )
            # asyncio now owns the socket; retain only its public server handle.
            self._socket = None
            self._deadline.remaining()
            self.verify()
            self._prepared = True
        except BaseException as error:
            self.close()
            if isinstance(error, (TimeoutError, asyncio.TimeoutError)):
                raise PrivateIPCError(Refusal.DEADLINE) from None
            if isinstance(error, (OSError, ValueError)):
                raise PrivateIPCError(Refusal.AUTHORITY) from None
            raise

    def activate(self, deadline: Deadline) -> None:
        """Open admission only under a later independently checked native grant."""
        self.verify()
        deadline.remaining()
        self._deadline.remaining()
        if not self._prepared or self._active:
            raise PrivateIPCError(Refusal.AUTHORITY)
        self._relay._server = self._transport
        self._relay.stats.start_time = time.time()
        self._active = True

    def close(self) -> None:
        """Close only owned transport/clients; never stop a foreign relay owner."""
        self._owner()
        if self._closed:
            return
        self._closed = True
        self._active = False
        if self._socket is not None:
            self._socket.close()
        if self._transport is not None:
            self._transport.close()
            if self._relay._server is self._transport:
                self._relay._server = None
        for task, writer in tuple(self._clients.items()):
            writer.close()
            task.cancel()

    async def wait_closed(self, deadline: Deadline) -> None:
        """Bound owned async cleanup outside any routing fence."""
        self.close()
        try:
            if self._transport is not None:
                await asyncio.wait_for(
                    self._transport.wait_closed(), timeout=deadline.remaining(),
                )
            if self._clients:
                await asyncio.wait_for(
                    asyncio.gather(*tuple(self._clients), return_exceptions=True),
                    timeout=deadline.remaining(),
                )
        except (TimeoutError, asyncio.TimeoutError):
            raise PrivateIPCError(Refusal.DEADLINE) from None
