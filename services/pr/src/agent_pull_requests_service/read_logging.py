"""Keep reviewer diagnostics on the foreground owner's existing stderr sink."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import json
import logging
import os
import socket
import stat
import sys
import threading

LOGGER = "agent_pull_requests.reviewer_reads"
_BYTES = 4096
_lock = threading.Lock()
_users = 0
_handler: logging.Handler | None = None
_previous: tuple[int, bool, tuple[logging.Handler, ...]] = (logging.NOTSET, True, ())


class _Stderr(logging.Handler):
    """One bounded pending line, at most two nonblocking writes per read."""

    def __init__(self) -> None:
        super().__init__()
        self.fd: int | None = None
        self.socket: socket.socket | None = None
        self.pending = b""
        self.dropped = 0
        try:
            if sys.platform != "linux":
                return
            descriptor = sys.stderr.fileno()
            info = os.fstat(descriptor)
            if stat.S_ISFIFO(info.st_mode):
                # dup/set_blocking would also change the caller's open-file flags.
                self.fd = os.open(
                    f"/proc/self/fd/{descriptor}", os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC,
                )
            elif stat.S_ISSOCK(info.st_mode):
                if socket.getdefaulttimeout() is not None:
                    return
                duplicate = os.dup(descriptor)
                try:
                    selected = socket.socket(fileno=duplicate)
                except Exception:
                    os.close(duplicate)
                    raise
                if selected.family == socket.AF_UNIX and selected.type == socket.SOCK_STREAM:
                    self.socket = selected
                else:
                    selected.close()
        except (AttributeError, OSError, ValueError):
            pass

    def _write(self, value: bytes) -> int:
        if sys.platform != "linux":
            return 0
        try:
            if self.socket is not None:
                return self.socket.send(value, socket.MSG_DONTWAIT | socket.MSG_NOSIGNAL)
            if self.fd is not None:
                return os.write(self.fd, value)
        except OSError:
            pass
        return 0

    def emit(self, record: logging.LogRecord) -> None:
        if self.pending:
            self.pending = self.pending[self._write(self.pending):]
            if self.pending:
                self.dropped += 1
                return
        value = json.loads(record.getMessage())
        value["dropped_reads"] = self.dropped
        encoded = (json.dumps(value, separators=(",", ":"), allow_nan=False) + "\n").encode()
        if len(encoded) > _BYTES or self.fd is None and self.socket is None:
            self.dropped += 1
            return
        self.dropped = 0
        self.pending = encoded[self._write(encoded):]

    def close(self) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None
        if self.socket is not None:
            self.socket.close()
            self.socket = None
        self.pending = b""
        super().close()


@contextmanager
def reviewer_reads() -> Iterator[None]:
    """Share one handler across embeddings and restore the caller's logger state."""
    global _users, _handler, _previous
    logger = logging.getLogger(LOGGER)
    with _lock:
        if not _users:
            _previous = logger.level, logger.propagate, tuple(logger.handlers)
            for handler in _previous[2]:
                logger.removeHandler(handler)
            _handler = _Stderr()
            logger.addHandler(_handler)
            logger.setLevel(logging.INFO)
            logger.propagate = False
        _users += 1
    try:
        yield
    finally:
        with _lock:
            _users -= 1
            if not _users:
                assert _handler is not None
                logger.removeHandler(_handler)
                _handler.close()
                _handler = None
                logger.setLevel(_previous[0])
                logger.propagate = _previous[1]
                for handler in _previous[2]:
                    logger.addHandler(handler)
