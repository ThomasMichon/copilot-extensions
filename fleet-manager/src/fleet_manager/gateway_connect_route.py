"""Explicit local TCP routing without changing the authenticated HTTPS origin."""

from __future__ import annotations

import socket
from dataclasses import dataclass

import aiohttp
from yarl import URL


@dataclass(frozen=True)
class LoopbackRoute:
    host: str
    port: int

    def __post_init__(self) -> None:
        if (self.host not in ("127.0.0.1", "::1") or type(self.port) is not int
                or not 1 <= self.port <= 65535):
            raise ValueError("invalid-gateway-connect-route")


class GatewayTCPConnector(aiohttp.TCPConnector):
    """Only DNS/TCP destination changes; aiohttp retains URL Host and TLS name."""

    def __init__(self, gateway: str, route: LoopbackRoute, **kwargs) -> None:
        super().__init__(**kwargs)
        uri = URL(gateway)
        self._gateway_address = (uri.raw_host, uri.port)
        self._gateway_route = route

    async def _resolve_host(self, host, port, traces=None):
        if (host, port) != self._gateway_address:
            raise OSError("gateway-connect-route-target-denied")
        route = self._gateway_route
        return [{
            "hostname": host, "host": route.host, "port": route.port,
            "family": socket.AF_INET6 if route.host == "::1" else socket.AF_INET,
            "proto": socket.IPPROTO_TCP, "flags": socket.AI_NUMERICHOST,
        }]
