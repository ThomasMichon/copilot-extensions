"""Bounded HTTP over one explicitly admitted direct local daemon connection."""
from __future__ import annotations

import io
import math
import os
import select
import socket
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .client import _json
from .http_stream import MAX_BODY, read_response
from .models import Endpoint, VerificationError

_MAX_WIRE = MAX_BODY + 128 * 1024
_DIRECT_LOCAL_SUPPORTED = os.name == "posix" and hasattr(socket, "AF_UNIX")


@dataclass(frozen=True)
class LocalDaemonAdmission:
    """Provider-trusted direct-daemon route, never restored from claim JSON.

    The provider must obtain this from its trusted configuration, which asserts
    that the local socket terminates at one daemon, not a request-routing proxy.
    Socket permissions alone cannot prove that semantic property.
    """

    host: str

    def __post_init__(self) -> None:
        parsed = urlsplit(self.host)
        if (parsed.scheme != "unix" or parsed.netloc or not parsed.path.startswith("/")
                or parsed.query or parsed.fragment or "\0" in self.host):
            raise VerificationError("Only configured direct absolute Unix daemon sockets are admitted")


class _DeadlineReader(io.RawIOBase):
    def __init__(self, connection: socket.socket, deadline: float) -> None:
        self.connection = connection
        self.deadline = deadline
        self.remaining = _MAX_WIRE
        self.received = 0

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self.remaining <= 0:
            raise VerificationError("Daemon session deadline or response budget exhausted")
        self.connection.settimeout(remaining)
        count = self.connection.recv_into(buffer, min(len(buffer), self.remaining))
        self.remaining -= count
        self.received += count
        return count


class _ResponseReader(io.BufferedReader):
    def __init__(self, raw: _DeadlineReader) -> None:
        super().__init__(raw)
        self.consumed = 0

    def read(self, size: int = -1) -> bytes:
        value = super().read(size)
        self.consumed += len(value)
        return value

    def readline(self, size: int = -1) -> bytes:
        value = super().readline(size)
        self.consumed += len(value)
        return value


class LocalDaemonSession:
    """One operation, one socket, fixed deadline, no reconnect or redirects."""

    def __init__(self, endpoint: Endpoint, admission: LocalDaemonAdmission, *, timeout: float = 8) -> None:
        if not isinstance(admission, LocalDaemonAdmission) or endpoint.host != admission.host or endpoint.tls:
            raise VerificationError("Claim endpoint does not match trusted local daemon admission")
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or not 0 < timeout <= 30:
            raise VerificationError("Daemon session deadline must be finite and at most 30 seconds")
        self.endpoint = endpoint
        self.path = Path(urlsplit(admission.host).path)
        self.deadline = time.monotonic() + timeout
        self.connection: socket.socket | None = None
        self.reader: _ResponseReader | None = None
        self.raw_reader: _DeadlineReader | None = None
        self.used = False

    def __enter__(self) -> LocalDaemonSession:
        if self.used:
            raise VerificationError("Daemon sessions cannot reconnect or be reused")
        self.used = True
        if not _DIRECT_LOCAL_SUPPORTED:
            raise VerificationError("Direct local daemon admission is unavailable on this platform")
        admitted = False
        try:
            before = self._socket_identity()
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.connection = connection
            self._set_deadline()
            connection.connect(str(self.path))
            if self._socket_identity() != before:
                raise VerificationError("Daemon socket changed during connection admission")
            self.raw_reader = _DeadlineReader(connection, self.deadline)
            self.reader = _ResponseReader(self.raw_reader)
            info = self.request("GET", "/info")
            if not isinstance(info, dict) or info.get("ID") != self.endpoint.daemon_id:
                raise VerificationError("Connected daemon identity does not match the claim")
            admitted = True
            return self
        except (OSError, ValueError, VerificationError) as error:
            raise VerificationError("Cannot admit the pinned local daemon session") from error
        finally:
            if not admitted:
                self.close()

    def _socket_identity(self) -> tuple[tuple[int, int], ...]:
        identities: list[tuple[int, int]] = []
        for path in [*reversed(self.path.parents), self.path]:
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or metadata.st_uid not in {0, os.getuid()}:
                raise VerificationError("Daemon socket route has a link or foreign owner")
            if path != self.path:
                sticky_root = metadata.st_uid == 0 and metadata.st_mode & stat.S_ISVTX
                if not stat.S_ISDIR(metadata.st_mode) or (metadata.st_mode & 0o022 and not sticky_root):
                    raise VerificationError("Daemon socket ancestor is not privately controlled")
            elif not stat.S_ISSOCK(metadata.st_mode) or metadata.st_mode & 0o022:
                raise VerificationError("Daemon socket is not a protected socket")
            identities.append((metadata.st_dev, metadata.st_ino))
        return tuple(identities)

    def _set_deadline(self) -> None:
        if self.connection is None:
            raise VerificationError("Daemon session is closed")
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise VerificationError("Daemon session deadline exhausted")
        self.connection.settimeout(remaining)

    def request(self, method: str, target: str) -> Any:
        """Read one bounded response; every transport/protocol error closes."""
        if method != "GET" or not target.startswith("/") or any(
            ord(character) <= 32 or ord(character) >= 127 for character in target
        ):
            raise VerificationError("Invalid daemon request")
        try:
            self._check_response_boundary()
            assert self.connection is not None
            wire = f"{method} {target} HTTP/1.1\r\nHost: docker\r\nContent-Length: 0\r\nConnection: keep-alive\r\n\r\n"
            self.connection.sendall(wire.encode("ascii"))
            if self.reader is None or self.raw_reader is None:
                raise VerificationError("Daemon response stream is unavailable")
            self.raw_reader.remaining = _MAX_WIRE
            status, body = read_response(self.reader)
            if status != 200:
                raise VerificationError(f"Daemon request refused (HTTP {status})")
            decoded = _json(body.decode("utf-8", errors="strict"))
            self._check_response_boundary()
            return decoded
        except (OSError, ValueError, UnicodeError, VerificationError) as error:
            self.close()
            raise VerificationError("Daemon session failed; no reconnect or absence verdict") from error

    def _check_response_boundary(self) -> None:
        self._set_deadline()
        if self.reader is None or self.raw_reader is None or self.connection is None:
            raise VerificationError("Daemon response stream is unavailable")
        if self.reader.consumed != self.raw_reader.received:
            raise VerificationError("Unsolicited buffered daemon response bytes")
        if select.select([self.connection], [], [], 0)[0]:
            raise VerificationError("Unsolicited daemon bytes or closed connection between requests")

    def close(self) -> None:
        connection, self.connection = self.connection, None
        reader, self.reader = self.reader, None
        self.raw_reader = None
        if connection is not None:
            connection.close()
        if reader is not None:
            reader.close()

    def __exit__(self, *_exc: object) -> None:
        self.close()
