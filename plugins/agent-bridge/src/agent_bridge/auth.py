"""Bearer token authentication middleware."""

from __future__ import annotations

import logging

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

log = logging.getLogger("agent-bridge")

# Paths that skip auth
_PUBLIC_PATHS = frozenset({"/health", "/docs", "/openapi.json", "/redoc"})


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
