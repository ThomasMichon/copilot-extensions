"""Application-owned custody spanning complete authentication operations."""

from __future__ import annotations

import threading
import inspect
from contextlib import contextmanager, nullcontext
from functools import wraps

from starlette.types import ASGIApp, Receive, Scope, Send
from zdd.private_protocol import PrivateIPCError, Refusal


class AuthCustody:
    """Count whole requests, including body reads and WebSocket lifetime."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._inflight = 0
        self._thread = threading.current_thread()

    @contextmanager
    def operation(self):
        with self._lock:
            self._inflight += 1
        try:
            yield
        finally:
            with self._lock:
                self._inflight -= 1

    @contextmanager
    def quiescent(self):
        if (
            threading.current_thread() is not self._thread
            or not self._lock.acquire(blocking=False)
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        try:
            if self._inflight != 0:
                raise PrivateIPCError(Refusal.AUTHORITY)
            yield
        finally:
            self._lock.release()


class AuthCustodyMiddleware:
    """Keep HTTP responses and WebSocket connections under one owner."""

    def __init__(self, app: ASGIApp, *, custody: AuthCustody) -> None:
        self.app = app
        self.custody = custody

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            with self.custody.operation():
                await self.app(scope, receive, send)
        else:
            await self.app(scope, receive, send)


def owned_auth_operation(function):
    """Cover direct endpoint execution as well as its ordinary ASGI wrapper."""
    @wraps(function)
    async def owned(request, *args, **kwargs):
        custody = getattr(request.app.state, "auth_custody", None)
        if custody is not None and type(custody) is not AuthCustody:
            raise PrivateIPCError(Refusal.AUTHORITY)
        with custody.operation() if custody is not None else nullcontext():
            return await function(request, *args, **kwargs)
    owned.__signature__ = inspect.signature(function, eval_str=True)
    return owned
