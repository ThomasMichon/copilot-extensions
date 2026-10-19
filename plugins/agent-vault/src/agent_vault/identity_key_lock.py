"""Coordinate owner initialization across processes sharing one database."""

from __future__ import annotations

import contextlib
import hashlib
import os
import time
from pathlib import Path

from single_instance_lease import AlreadyRunningError, SingleInstance


@contextlib.contextmanager
def database_creation_lock(kpdb: str, deadline: float):
    from .identity_key import IdentityKeyError

    try:
        database = Path(kpdb).resolve(strict=True)
        digest = hashlib.sha256(os.path.normcase(str(database)).encode("utf-8")).hexdigest()
        lease = SingleInstance(
            database.parent,
            service="agent-vault-identity",
            lock_name=f".agent-vault-identity-{digest}.lock",
        )
        wait_until = min(deadline, time.monotonic() + 5.0)
        while True:
            if time.monotonic() >= wait_until:
                raise IdentityKeyError(
                    "service_busy", "Another process is initializing this database; retry setup."
                )
            try:
                lease.acquire()
                break
            except AlreadyRunningError:
                time.sleep(min(0.05, max(0, wait_until - time.monotonic())))
    except OSError as exc:
        raise IdentityKeyError(
            "vault_read_failed",
            "Cannot coordinate owner initialization; check the database directory permissions.",
        ) from exc
    try:
        yield
    finally:
        lease.release()
