"""Inherited socketpair adapter for one finite retirement worker (Linux)."""

from __future__ import annotations

import os
import select
import socket

from zdd.private_protocol import Deadline, PrivateIPCError, Refusal


class InheritedChannel:
    """Own an anonymous socket and an already-pinned peer's pidfd."""

    def __init__(self, stream: socket.socket, peer_fd: int):
        self._stream = stream
        self._peer_fd = peer_fd
        stream.setblocking(False)

    def check_peer(self, deadline: Deadline) -> None:
        deadline.remaining()
        if self._peer_fd is None:
            raise PrivateIPCError(Refusal.CLOSED)
        if select.select([self._peer_fd], [], [], 0)[0]:
            raise PrivateIPCError(Refusal.CLOSED)

    def _ready(self, deadline: Deadline, *, write: bool = False) -> None:
        while True:
            self.check_peer(deadline)
            readable, writable, _ = select.select(
                [self._peer_fd] if write else [self._peer_fd, self._stream],
                [self._stream] if write else [], [], deadline.wait_slice(),
            )
            self.check_peer(deadline)
            if self._stream in (writable if write else readable):
                return

    def read_exact(self, count: int, deadline: Deadline) -> bytes:
        chunks = []
        while count:
            self._ready(deadline)
            try:
                data = self._stream.recv(min(count, 65536))
            except BlockingIOError:
                continue
            if not data:
                raise PrivateIPCError(Refusal.CLOSED)
            chunks.append(data)
            count -= len(data)
        self.check_peer(deadline)
        return b"".join(chunks)

    def write_all(self, data: bytes, deadline: Deadline) -> None:
        offset = 0
        while offset < len(data):
            self._ready(deadline, write=True)
            try:
                size = self._stream.send(data[offset:offset + 65536])
            except BlockingIOError:
                continue
            if not size:
                raise PrivateIPCError(Refusal.CLOSED)
            offset += size
        self.check_peer(deadline)

    def close(self) -> None:
        self._stream.close()
        if self._peer_fd is not None:
            os.close(self._peer_fd)
            self._peer_fd = None
