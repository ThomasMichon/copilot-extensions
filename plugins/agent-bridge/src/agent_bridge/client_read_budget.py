"""One optional command deadline for sequential read-only HTTP observations."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import http.client
import io
import socket
from ssl import SSLContext
import time
from typing import Any, Iterator, Literal, cast
import urllib.request

_deadline: ContextVar[float | None] = ContextVar("bridge_read_deadline", default=None)


class ReadDeadlineExpired(Exception):
    """The observer stopped waiting; the target's state is not confirmed."""


def remaining() -> float | None:
    deadline = _deadline.get()
    if deadline is None:
        return None
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise ReadDeadlineExpired("bridge observation deadline expired")
    return seconds


@contextmanager
def command_deadline(timeout: float | None) -> Iterator[None]:
    parent = _deadline.get()
    deadline = time.monotonic() + timeout if timeout else None
    if parent is not None:
        deadline = min(parent, deadline) if deadline is not None else parent
    token = _deadline.set(deadline)
    try:
        yield
    finally:
        _deadline.reset(token)


def pause(seconds: float) -> None:
    left = remaining()
    time.sleep(min(seconds, left) if left is not None else seconds)
    remaining()


def _bounded_timeout(timeout: float | None) -> float | None:
    left = remaining()
    return min(timeout, left) if left is not None and timeout is not None else left or timeout


class _ResponseReader(io.RawIOBase):
    def __init__(self, sock: socket.socket, mode: Literal["rb"]) -> None:
        self._socket = sock
        self._timeout = sock.gettimeout()
        self._raw = sock.makefile(mode, buffering=0)

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int | None:
        self._socket.settimeout(_bounded_timeout(self._timeout))
        try:
            count: int | None = self._raw.readinto(buffer)
        except OSError:
            remaining()
            raise
        remaining()
        return count

    def close(self) -> None:
        try:
            self._raw.close()
        finally:
            super().close()


class _ResponseSocket:
    def __init__(self, sock: socket.socket) -> None:
        self._socket = sock

    def makefile(self, mode: Literal["rb"]) -> io.BufferedReader:
        return io.BufferedReader(_ResponseReader(self._socket, mode))


class _Response(http.client.HTTPResponse):
    def __init__(self, sock: Any, *args: Any, **kwargs: Any) -> None:
        super().__init__(cast(Any, _ResponseSocket(sock)), *args, **kwargs)


class _HTTPConnection(http.client.HTTPConnection):
    response_class = _Response


class _HTTPSConnection(http.client.HTTPSConnection):
    response_class = _Response


class _HTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, request: Any) -> Any:
        request.timeout = _bounded_timeout(request.timeout)
        return self.do_open(_HTTPConnection, request)


class _HTTPSHandler(urllib.request.HTTPSHandler):
    _context: SSLContext | None

    def https_open(self, request: Any) -> Any:
        request.timeout = _bounded_timeout(request.timeout)
        return self.do_open(_HTTPSConnection, request, context=self._context)


def open_response(request: urllib.request.Request, *, timeout: float) -> Any:
    """Bound each raw header/body read, not only one socket operation."""
    if remaining() is None:
        return urllib.request.urlopen(request, timeout=timeout)
    return urllib.request.build_opener(_HTTPHandler(), _HTTPSHandler()).open(
        request,
        timeout=timeout,
    )
