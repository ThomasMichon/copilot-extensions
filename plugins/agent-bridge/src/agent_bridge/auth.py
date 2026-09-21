"""Bearer token authentication middleware."""

from __future__ import annotations

import logging
import os

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

log = logging.getLogger("agent-bridge")

# Paths that skip auth
_PUBLIC_PATHS = frozenset({"/health", "/ui", "/ui/console", "/docs", "/openapi.json", "/redoc"})

_LOOPBACK_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})


def host_is_loopback(host_header: str | None) -> bool:
    """Whether an HTTP ``Host`` header names a loopback literal.

    Accepts ``127.0.0.1[:port]``, ``localhost[:port]``, ``[::1][:port]`` and the
    ``127.0.0.0/8`` range. Checks the *literal Host the client sent*, not the
    socket address, so a DNS-rebinding page (whose ``Host`` is ``evil.com`` even
    though it resolved to 127.0.0.1) is NOT treated as loopback.
    """
    if not host_header:
        return False
    host = host_header.strip()
    if host.startswith("["):  # bracketed IPv6, e.g. [::1]:6355
        end = host.find("]")
        host = host[1:end] if end != -1 else host[1:]
    elif host.count(":") == 1:  # host:port (IPv4 / hostname)
        host = host.split(":", 1)[0]
    host = host.lower()
    return host in _LOOPBACK_NAMES or host.startswith("127.")


def loopback_is_trusted() -> bool:
    """Whether loopback requests skip the token (default: yes, single-user local)."""
    return os.environ.get("AGENT_BRIDGE_REQUIRE_LOOPBACK_AUTH", "").strip().lower() not in {
        "1", "true", "yes", "on",
    }


def request_is_trusted_local(headers) -> bool:  # noqa: ANN001
    """Whether this request is trusted without a token (a loopback Host)."""
    try:
        host = headers.get("host", "")
    except Exception:
        host = ""
    return loopback_is_trusted() and host_is_loopback(host)


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """Validate Authorization: Bearer <token> on all API routes."""

    def __init__(self, app, *, token: str) -> None:  # noqa: ANN001
        super().__init__(app)
        self._token = token

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)

        # Single-user localhost tool: trust loopback, no token needed.
        if request_is_trusted_local(request.headers):
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return Response(
                content='{"detail":"Missing Authorization header"}',
                status_code=401,
                media_type="application/json",
            )

        provided = auth[7:]  # strip "Bearer "
        if provided != self._token:
            return Response(
                content='{"detail":"Invalid token"}',
                status_code=403,
                media_type="application/json",
            )

        return await call_next(request)
