"""ACP JSON-RPC text messages over Bridge's existing ``acp.v1`` WS contract."""

from __future__ import annotations

import asyncio
import contextlib
from http import HTTPStatus
import hmac
import json

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .client import SessionHostClient
from .protocol import MAX_MESSAGE_BYTES, ProtocolError


class WebSocketTransport:
    """Expose one existing Host, without allocating an ACP conversation."""

    def __init__(self, tcp_port: int, tcp_nonce: str) -> None:
        if not tcp_nonce:
            raise ValueError("ACP WebSocket requires its authenticated Host")
        self.nonce = tcp_nonce
        self.port = 0
        self._tcp_port = tcp_port
        self._tcp_nonce = tcp_nonce
        self._server = None
        self._handlers: set[asyncio.Task] = set()

    async def serve(self, port: int = 0) -> int:
        self._server = await serve(
            self._handle, "127.0.0.1", port,
            subprotocols=["acp.v1"], process_request=self._authorize,
            compression=None, max_size=MAX_MESSAGE_BYTES - 2,
            max_queue=4, write_limit=64 * 1024, close_timeout=2,
        )
        self.port = self._server.sockets[0].getsockname()[1]
        return self.port

    def _authorize(self, connection, request):
        headers = request.headers
        offered = [
            item.strip() for value in headers.get_all("Sec-WebSocket-Protocol")
            for item in value.split(",")
        ]
        tokens = [item[7:] for item in offered if item.startswith("bearer.")]
        if (
            request.path != "/acp"
            or headers.get_all("Host") != [f"127.0.0.1:{self.port}"]
            or headers.get_all("Origin")
            or len(tokens) != 1
            or not hmac.compare_digest(tokens[0].encode("utf-8"), self.nonce.encode("ascii"))
        ):
            return connection.respond(HTTPStatus.FORBIDDEN, "ACP endpoint refused\n")
        return None

    async def close(self, *, drain: bool = False) -> None:
        if self._server is not None:
            self._server.close(close_connections=not drain)
            waiter = asyncio.create_task(self._server.wait_closed())
            try:
                await asyncio.wait_for(asyncio.shield(waiter), 10)
            except asyncio.TimeoutError:
                await asyncio.gather(*(
                    connection.close(1001, "Host closing")
                    for connection in tuple(self._server.connections)
                ), return_exceptions=True)
                await asyncio.wait_for(waiter, 3)

    async def _handle(self, websocket: ServerConnection) -> None:
        handler = asyncio.current_task()
        self._handlers.add(handler)
        client = None
        tasks = []
        try:
            try:
                client = await asyncio.wait_for(
                    SessionHostClient.connect(port=self._tcp_port), 10,
                )
                await asyncio.wait_for(client.attach(nonce=self._tcp_nonce.encode()), 10)
            except (OSError, ConnectionError, ProtocolError, asyncio.TimeoutError):
                await websocket.close(1013, "Host controller unavailable")
                return

            async def incoming():
                async for text in websocket:
                    if not isinstance(text, str):
                        await websocket.close(1003, "ACP requires JSON-RPC text messages")
                        return
                    try:
                        message = json.loads(text)
                        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
                            raise ValueError("not a JSON-RPC object")
                        payload = text.rstrip("\n").encode("utf-8")
                        if b"\n" in payload or b"\r" in payload:
                            # Pretty-printed JSON is valid WS input, but the
                            # child consumes exactly one NDJSON frame per RPC.
                            payload = json.dumps(message, separators=(",", ":")).encode()
                        if len(payload) + 1 > MAX_MESSAGE_BYTES:
                            raise ValueError("frame too large")
                    except (ValueError, UnicodeError):
                        await websocket.close(1007, "Invalid ACP JSON-RPC message")
                        return
                    await client.write(payload + b"\n")

            async def outgoing():
                async for sequence, frame in client.frames():
                    await websocket.send(frame.decode("utf-8").rstrip("\n"))
                    # Plain ACP WS has no application receipt or resumable
                    # cursor; ACK means handed to this transport, not consumed.
                    await client.ack(sequence)
                await websocket.close(1000, "ACP process exited")

            tasks = [
                asyncio.create_task(incoming()), asyncio.create_task(outgoing()),
                asyncio.create_task(websocket.wait_closed()),
            ]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        except (ConnectionClosed, OSError, ConnectionError, ProtocolError, UnicodeError):
            with contextlib.suppress(ConnectionClosed):
                await websocket.close(1011, "ACP connection ended")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            try:
                if client is not None:
                    await client.close()
            finally:
                self._handlers.discard(handler)
