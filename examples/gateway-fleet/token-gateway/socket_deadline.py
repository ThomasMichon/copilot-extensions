"""Interrupt an owned socket even while a peer drips HTTP headers."""

from contextlib import contextmanager
import http.client
import socket
import threading
import time


@contextmanager
def socket_deadline(sock, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Backend deadline exceeded")
    expired = threading.Event()

    def expire():
        expired.set()
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            # A completed response may already have closed this captured socket.
            pass

    timer = threading.Timer(remaining, expire)
    timer.daemon = True
    timer.start()
    try:
        yield
        if expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("Backend deadline exceeded")
    except (OSError, http.client.HTTPException) as exc:
        if expired.is_set() or time.monotonic() >= deadline:
            raise TimeoutError("Backend deadline exceeded") from exc
        raise
    finally:
        timer.cancel()
        timer.join()
