"""Bounded synchronous CLI transport for authenticated JSON-line owner reads."""

from __future__ import annotations

import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

JsonObject = dict[str, Any]


class Endpoint(Protocol):
    host: str
    port: int
    token: str
    timeout: float


class Receiver(Protocol):
    closed: bool

    def receive(self, frame: JsonObject) -> None: ...


class StreamError(OSError):
    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class Codec:
    frame_bytes: int
    encode: Callable[[Any], str]
    decode: Callable[[bytes], Any]
    label: str


class _Reader:
    def __init__(
        self,
        sock: socket.socket,
        codec: Codec,
        clock: Callable[[], float],
    ) -> None:
        self.sock, self.codec, self.clock = sock, codec, clock
        self.pending = bytearray()

    def read(self, deadline: float) -> JsonObject:
        while True:
            if b"\n" in self.pending:
                line, _, rest = self.pending.partition(b"\n")
                self.pending = bytearray(rest)
                if len(line) + 1 > self.codec.frame_bytes:
                    raise StreamError(
                        f"{self.codec.label} frame exceeds byte bound", "invalid-response"
                    )
                frame = self.codec.decode(bytes(line))
                if not isinstance(frame, dict):
                    raise StreamError(
                        f"{self.codec.label} returned a non-object frame", "invalid-response"
                    )
                return frame
            if len(self.pending) >= self.codec.frame_bytes:
                raise StreamError(
                    f"{self.codec.label} frame exceeds byte bound", "invalid-response"
                )
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise StreamError(f"{self.codec.label} deadline expired", "unavailable")
            self.sock.settimeout(remaining)
            chunk = self.sock.recv(self.codec.frame_bytes)
            if not chunk:
                raise StreamError(
                    f"{self.codec.label} disconnected without terminal frame", "disconnected"
                )
            self.pending.extend(chunk)


def _request_bytes(
    client: Endpoint,
    action: str,
    payload: JsonObject,
    codec: Codec,
) -> bytes:
    return (
        codec.encode(
            {"version": 1, "token": client.token, "action": action, "payload": payload}
        ).encode("utf-8")
        + b"\n"
    )


def request(
    client: Endpoint,
    action: str,
    payload: JsonObject,
    codec: Codec,
    *,
    clock: Callable[[], float] = time.monotonic,
) -> JsonObject:
    encoded = _request_bytes(client, action, payload, codec)
    with socket.create_connection((client.host, client.port), timeout=client.timeout) as sock:
        deadline = clock() + client.timeout
        sock.sendall(encoded)
        reply = _Reader(sock, codec, clock).read(deadline)
    if type(reply.get("version")) is not int or reply["version"] != 1:
        raise StreamError(f"{codec.label} returned an invalid control version", "invalid-response")
    if set(reply) == {"version", "error"}:
        raise StreamError(f"{codec.label} control request was rejected", "unavailable")
    if set(reply) != {"version", "result"} or not isinstance(reply["result"], dict):
        raise StreamError(f"{codec.label} returned an invalid control reply", "invalid-response")
    return reply["result"]


def relay(
    client: Endpoint,
    action: str,
    payload: JsonObject,
    codec: Codec,
    receiver: Receiver,
    emit: Callable[[JsonObject], None],
    *,
    idle_timeout: float = 5.0,
    connect: Callable[..., Any] = socket.create_connection,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    encoded = _request_bytes(client, action, payload, codec)
    with connect((client.host, client.port), timeout=client.timeout) as sock:
        sock.sendall(encoded)
        reader = _Reader(sock, codec, clock)
        deadline = clock() + client.timeout
        while not receiver.closed:
            frame = reader.read(deadline)
            receiver.receive(frame)
            emit(frame)
            deadline = clock() + idle_timeout
            if receiver.closed:
                return 1 if frame.get("type") == "error" else 0
    return 0
