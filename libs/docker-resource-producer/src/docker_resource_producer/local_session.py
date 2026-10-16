"""Bounded HTTP over one explicitly admitted direct local daemon connection."""
from __future__ import annotations

import http.client
import io
import math
import os
import socket
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .client import _json
from .models import Endpoint, VerificationError

_MAX_BODY = 8 * 1024 * 1024
_MAX_WIRE = _MAX_BODY + 128 * 1024
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

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self.remaining <= 0:
            raise VerificationError("Daemon session deadline or response budget exhausted")
        self.connection.settimeout(remaining)
        count = self.connection.recv_into(buffer, min(len(buffer), self.remaining))
        self.remaining -= count
        return count


class _ResponseSocket:
    def __init__(self, connection: socket.socket, deadline: float) -> None:
        self.connection = connection
        self.deadline = deadline

    def makefile(self, _mode: str) -> io.BufferedReader:
        return io.BufferedReader(_DeadlineReader(self.connection, self.deadline))


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
        self.used = False

    def __enter__(self) -> LocalDaemonSession:
        if self.used:
            raise VerificationError("Daemon sessions cannot reconnect or be reused")
        self.used = True
        if not _DIRECT_LOCAL_SUPPORTED:
            raise VerificationError("Direct local daemon admission is unavailable on this platform")
        try:
            before = self._socket_identity()
            connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.connection = connection
            self._set_deadline()
            connection.connect(str(self.path))
            if self._socket_identity() != before:
                raise VerificationError("Daemon socket changed during connection admission")
            info = self.request("GET", "/info")
            if not isinstance(info, dict) or info.get("ID") != self.endpoint.daemon_id:
                raise VerificationError("Connected daemon identity does not match the claim")
            return self
        except (OSError, ValueError, http.client.HTTPException, VerificationError) as error:
            self.close()
            raise VerificationError("Cannot admit the pinned local daemon session") from error

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
        if method not in {"GET", "DELETE"} or not target.startswith("/") or any(
            ord(character) <= 32 or ord(character) >= 127 for character in target
        ):
            raise VerificationError("Invalid daemon request")
        try:
            self._set_deadline()
            assert self.connection is not None
            wire = f"{method} {target} HTTP/1.1\r\nHost: docker\r\nContent-Length: 0\r\nConnection: keep-alive\r\n\r\n"
            self.connection.sendall(wire.encode("ascii"))
            response = http.client.HTTPResponse(_ResponseSocket(self.connection, self.deadline))
            response.begin()
            headers = response.getheaders()
            lengths = [value for name, value in headers if name.lower() == "content-length"]
            transfers = [value for name, value in headers if name.lower() == "transfer-encoding"]
            if (response.version != 11 or response.will_close or len(lengths) > 1
                    or len(transfers) > 1 or (lengths and transfers)
                    or (transfers and transfers[0].lower() != "chunked")
                    or (not lengths and not transfers and response.status != 204)
                    or (lengths and (not lengths[0].isdigit() or int(lengths[0]) > _MAX_BODY))):
                raise VerificationError("Daemon response framing is not reusable and bounded")
            body = response.read(_MAX_BODY + 1)
            response.close()
            if len(body) > _MAX_BODY or (lengths and len(body) != int(lengths[0])):
                raise VerificationError("Daemon response is oversized or truncated")
            if response.status not in {200, 204}:
                raise VerificationError(f"Daemon request refused (HTTP {response.status})")
            if response.status == 204:
                if body:
                    raise VerificationError("Unexpected daemon deletion response body")
                return None
            return _json(body.decode("utf-8", errors="strict"))
        except (OSError, ValueError, UnicodeError, http.client.HTTPException, VerificationError) as error:
            self.close()
            raise VerificationError("Daemon session failed; no reconnect or absence verdict") from error

    def close(self) -> None:
        connection, self.connection = self.connection, None
        if connection is not None:
            connection.close()

    def __exit__(self, *_exc: object) -> None:
        self.close()
