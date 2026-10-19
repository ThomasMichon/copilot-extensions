"""Bound raw socket admission before asyncio's maintained TLS handshake."""

from __future__ import annotations

import asyncio
import ssl

from fleet_manager.gateway_contract import MAX_WIRE

from . import fleet_http

MAX_CONNECTIONS = 32


class TLSAdmission:
    def __init__(self, context: ssl.SSLContext) -> None:
        self.context = context
        self.loop: asyncio.AbstractEventLoop | None = None
        self.connections: set[FleetTLSProtocol] = set()
        self.closing = False

    def protocol(self, **kwargs) -> FleetTLSProtocol:
        return FleetTLSProtocol(self, kwargs)

    def close(self) -> None:
        """Called on the ingress loop, including connections not yet in Uvicorn."""
        self.closing = True
        for connection in tuple(self.connections):
            connection.abort()


class FleetTLSProtocol(asyncio.Protocol):
    def __init__(self, admission: TLSAdmission, http_kwargs: dict) -> None:
        self.admission = admission
        self.http_kwargs = http_kwargs
        self.transport: asyncio.Transport | None = None
        self.http: fleet_http.FleetHTTPProtocol | None = None
        self.task: asyncio.Task | None = None
        self.deadline: asyncio.TimerHandle | None = None
        self.pending = bytearray()
        self.closed = False
        self.http_lost = False
        self.writing_paused = False

    def connection_made(self, transport: asyncio.Transport) -> None:
        self.transport = transport
        owner = self.admission
        owner.loop = asyncio.get_running_loop()
        if owner.closing or len(owner.connections) >= MAX_CONNECTIONS:
            self.abort()
            return
        owner.connections.add(self)
        self.deadline = owner.loop.call_later(
            fleet_http.INITIAL_REQUEST_TIMEOUT, self.abort,
        )
        transport.pause_reading()
        self.task = owner.loop.create_task(self._upgrade())
        self.task.add_done_callback(self._upgrade_done)

    def _upgrade_done(self, task: asyncio.Task) -> None:
        if not task.cancelled() and task.exception() is not None:
            self.abort()
            asyncio.get_running_loop().call_exception_handler({
                "message": "fleet TLS upgrade failed",
            })

    async def _upgrade(self) -> None:
        loop = asyncio.get_running_loop()
        raw_transport = self.transport
        if raw_transport is None:
            raise RuntimeError("fleet TLS transport unavailable")
        try:
            transport = await loop.start_tls(
                raw_transport, self, self.admission.context, server_side=True,
                ssl_handshake_timeout=fleet_http.INITIAL_REQUEST_TIMEOUT,
            )
            if transport is None or self.closed:
                if transport is not None:
                    transport.abort()
                return
            self.transport = transport
            self.http = fleet_http.FleetHTTPProtocol(**self.http_kwargs)
            self.http.connection_made(transport)
            if self.writing_paused:
                self.http.pause_writing()
            if self.pending:
                pending = bytes(self.pending)
                self.pending.clear()
                self.data_received(pending)
        except (OSError, asyncio.TimeoutError):
            self.abort()
        finally:
            if self.http is None:
                self.abort()

    def data_received(self, data: bytes) -> None:
        if self.closed:
            return
        if self.http is None:
            # TLS can deliver application bytes before start_tls resumes.
            if len(self.pending) + len(data) > MAX_WIRE + 16384:
                self.abort()
            else:
                self.pending.extend(data)
            return
        self.http.data_received(data)
        if self.http.cycle is not None:
            self._cancel_deadline()

    def _cancel_deadline(self) -> None:
        if self.deadline is not None:
            self.deadline.cancel()
            self.deadline = None

    def abort(self) -> None:
        if self.closed:
            return
        self.closed = True
        self._cancel_deadline()
        self.pending.clear()
        self.admission.connections.discard(self)
        if self.transport is not None:
            self.transport.abort()
        if (
            self.task is not None and not self.task.done()
            and self.task is not asyncio.current_task()
        ):
            self.task.cancel()

    def connection_lost(self, exc: Exception | None) -> None:
        self.abort()
        if self.http is not None and not self.http_lost:
            self.http_lost = True
            self.http.connection_lost(exc)

    def eof_received(self) -> None:
        if self.http is not None:
            self.http.eof_received()

    def pause_writing(self) -> None:
        self.writing_paused = True
        if self.http is not None:
            self.http.pause_writing()

    def resume_writing(self) -> None:
        self.writing_paused = False
        if self.http is not None:
            self.http.resume_writing()
