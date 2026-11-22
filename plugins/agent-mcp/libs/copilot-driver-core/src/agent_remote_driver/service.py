"""Small authenticated front door to the existing independently owned Hosts."""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
import json
import secrets
from typing import Any

from websockets.asyncio.server import Server, ServerConnection, serve
from websockets.http11 import Request, Response
from websockets.typing import Subprotocol

from .session_host.protocol import MAX_MESSAGE_BYTES
from .session_host.registered_inventory import HostEndpoint, registered_host_endpoints
from .session_host.ws_transport import WebSocketTransport


class DriverService:
    """Inventory and relay existing sessions without creating or owning them."""

    def __init__(
        self, nonce: str, *,
        inventory: Callable[
            [], tuple[list[HostEndpoint], dict[str, Any]]
        ] = registered_host_endpoints,
        instance_id: str = "",
        generation: str = "",
    ) -> None:
        if not isinstance(nonce, str) or not 32 <= len(nonce) <= 256 or not nonce.isascii():
            raise ValueError("Driver service requires its private capability")
        self._nonce = nonce
        self._inventory = inventory
        self._instance_id = instance_id
        self._generation = generation
        self._server: Server | None = None
        self.port = 0
        self.draining = False

    async def serve(self, port: int = 0) -> int:
        if self._server is not None:
            raise RuntimeError("Driver service is already serving")
        self._server = await serve(
            self._relay, "127.0.0.1", port, subprotocols=[Subprotocol("acp.v1")],
            process_request=self._authorize, compression=None,
            max_size=MAX_MESSAGE_BYTES - 2, max_queue=4,
            write_limit=64 * 1024, close_timeout=2,
        )
        self.port = next(iter(self._server.sockets)).getsockname()[1]
        return self.port

    async def close(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def drain(self) -> None:
        """Stop new frontends while accepted streams retain their original Host."""
        self.draining = True
        if self._server is not None:
            self._server.close(close_connections=False)
            await self._server.wait_closed()

    @staticmethod
    def _json(
        connection: ServerConnection, status: HTTPStatus, body: dict[str, Any],
    ) -> Response:
        response = connection.respond(status, json.dumps(body, separators=(",", ":")))
        response.headers["Content-Type"] = "application/json"
        return response

    def _authorize(self, connection: ServerConnection, request: Request) -> Response | None:
        headers = request.headers
        schemes = [
            item.strip() for value in headers.get_all("Sec-WebSocket-Protocol")
            for item in value.split(",")
        ]
        credentials = [item[7:] for item in schemes if item.startswith("bearer.")]
        protocol_credentials = len(credentials)
        authorization = headers.get_all("Authorization")
        prefix = "Bear" + "er "
        for value in authorization:
            credentials.append(value[len(prefix):] if value.startswith(prefix) else "")
        if (
            headers.get_all("Host") != [f"127.0.0.1:{self.port}"]
            or headers.get_all("Origin") or not credentials
            or protocol_credentials > 1 or len(authorization) > 1
            or any(not secrets.compare_digest(
                credential.encode("utf-8"), self._nonce.encode("ascii"),
            ) for credential in credentials)
        ):
            return self._json(connection, HTTPStatus.FORBIDDEN, {"error": "driver_access_refused"})
        if request.path == "/health":
            return self._json(connection, HTTPStatus.OK, {
                "contract": "copilot-driver.service", "version": 1, "ready": not self.draining,
                "transports": ["acp.v1"],
                "instance_id": self._instance_id, "generation": self._generation,
            })
        if request.path == "/sessions":
            endpoints, source = self._inventory()
            if len(endpoints) > 1024:
                return self._json(
                    connection, HTTPStatus.SERVICE_UNAVAILABLE, {"error": "inventory_limit"},
                )
            return self._json(connection, HTTPStatus.OK, {
                "sessions": [{
                    "session_id": endpoint.session_id, "instance_id": endpoint.instance_id,
                    "host_pid": endpoint.host_pid,
                    "acp_path": "/sessions/" + endpoint.instance_id + "/acp",
                } for endpoint in endpoints],
                "source": source,
            })
        parts = request.path.split("/")
        if (
            len(parts) != 4 or parts[1] != "sessions" or parts[3] != "acp"
            or not parts[2] or len(parts[2]) > 128 or "acp.v1" not in schemes
            or not headers.get_all("Upgrade")
        ):
            return self._json(connection, HTTPStatus.NOT_FOUND, {"error": "driver_target_unavailable"})
        return None

    async def _relay(self, websocket: ServerConnection) -> None:
        # Reobserve after the handshake. A pathname or an earlier list is not a
        # grant to follow a replacement Host or start a second conversation.
        request = websocket.request
        assert request is not None
        instance = request.path.split("/")[2]
        endpoints, _ = self._inventory()
        selected = [endpoint for endpoint in endpoints if endpoint.instance_id == instance]
        if len(selected) != 1:
            await websocket.close(1013, "Original Host is unavailable")
            return
        endpoint = selected[0]
        await WebSocketTransport(endpoint.port, endpoint.nonce).relay(websocket)
