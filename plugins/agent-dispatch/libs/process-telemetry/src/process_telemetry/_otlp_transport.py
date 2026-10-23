"""Finite HTTP transport, owned synchronously by the existing telemetry writer."""

from __future__ import annotations

import http.client
import io
import re
import socket
import time
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Reply:
    error: str | None = None
    status: int | None = None
    body: bytes = b""
    attempted: bool = False


def remaining(deadline):
    value = deadline - time.monotonic()
    if value <= 0:
        raise TimeoutError("signal deadline exhausted")
    return value


class _Reader(io.RawIOBase):
    def __init__(self, sock, deadline):
        super().__init__()
        self.sock, self.deadline, self.received = sock, deadline, 0

    def readable(self):
        return True

    def readinto(self, buffer):
        self.sock.settimeout(remaining(self.deadline))
        size = self.sock.recv_into(buffer)
        self.received += size
        if self.received > 73728:
            raise ValueError("signal response exceeds wire limit")
        return size

    def close(self):
        try:
            self.sock.close()
        finally:
            super().close()


class _Socket:
    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline

    def makefile(self, mode):
        # Like socket.makefile, the response must outlive HTTPConnection.close
        # when HTTP/1.0 marks the connection non-reusable after its headers.
        return io.BufferedReader(_Reader(self.sock.dup(), self.deadline))

    def sendall(self, data):
        self.sock.settimeout(remaining(self.deadline))
        self.sock.sendall(data)

    def close(self):
        self.sock.close()


class _Connection(http.client.HTTPConnection):
    def __init__(self, destination, deadline):
        super().__init__(destination.hostname, destination.port)
        self.deadline = deadline

    def connect(self):
        # Admission already proved a numeric loopback address; never resolve DNS.
        sock = socket.socket(socket.AF_INET6 if ":" in self.host else socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.settimeout(remaining(self.deadline))
            sock.connect((self.host, self.port))
            self.sock = _Socket(sock, self.deadline)
        except BaseException:
            sock.close()
            raise


def post(endpoint: str, body: bytes, deadline: float, *, strict: bool = True) -> Reply:
    if len(body) > 262144:
        return Reply(error="invalid_ack")
    if deadline <= time.monotonic():
        return Reply(error="deadline_exhausted")
    destination = urlsplit(endpoint)
    connection = _Connection(destination, deadline)
    response = None
    try:
        connection.request("POST", destination.path, body, {
            "Content-Type": "application/x-protobuf", "Content-Length": str(len(body)),
        })
        response = connection.getresponse()
        if response.status in (404, 405, 501):
            return Reply(error="unsupported", status=response.status, attempted=True)
        if (response.status != 200 if strict else not 200 <= response.status < 300):
            return Reply(error="transport_unknown", status=response.status, attempted=True)
        if strict and response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/x-protobuf":
            return Reply(error="invalid_ack", attempted=True)
        declared = response.getheader("Content-Length")
        expected = None
        if declared is not None:
            if response.chunked or not re.fullmatch(r"[0-9]{1,10}", declared.strip()):
                return Reply(error="invalid_ack", attempted=True)
            expected = int(declared)
            if expected > 65536:
                return Reply(error="invalid_ack", attempted=True)
        chunks, size = [], 0
        while True:
            remaining(deadline)
            chunk = response.read1(min(8192, 65537 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
            if size > 65536:
                return Reply(error="invalid_ack", attempted=True)
        if expected is not None and size != expected:
            return Reply(error="invalid_ack", attempted=True)
        return Reply(status=response.status, body=b"".join(chunks), attempted=True)
    except (ValueError, http.client.HTTPException):
        return Reply(error="invalid_ack", attempted=True)
    except OSError:
        return Reply(error="transport_unknown", attempted=True)
    finally:
        if response is not None:
            response.close()
        connection.close()
