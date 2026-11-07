"""Crash-releasing exclusion for one local CodeSpace claim operation."""
from __future__ import annotations

import hashlib
import os
from contextlib import contextmanager
from functools import wraps


@contextmanager
def claim_guard(name: str):
    from . import lease

    root = lease.LEASE_FILE.parent / "claim-locks"
    root.mkdir(parents=True, exist_ok=True)
    path = root / (hashlib.sha256(name.encode("utf-8")).hexdigest() + ".lock")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    locked = False
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"\0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError(f"CodeSpace '{name}' claim/return is busy") from None
        locked = True
        yield
    finally:
        try:
            if locked:
                os.lseek(descriptor, 0, os.SEEK_SET)
                if os.name == "nt":
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def serialize_claim(position: int = 0):
    def decorate(function):
        @wraps(function)
        def guarded(*args, **kwargs):
            from .claim_return import pending_returns

            name = kwargs.get("codespace", args[position] if len(args) > position else "")
            with claim_guard(name):
                if name in pending_returns():
                    raise RuntimeError(f"CodeSpace '{name}' has an unsettled return; local claim is retained")
                return function(*args, **kwargs)

        return guarded

    return decorate
