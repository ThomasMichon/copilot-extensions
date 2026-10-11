"""Process-lifetime exclusion for active and recovering acquisition executors."""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from collections.abc import Iterator

from single_instance_lease import AlreadyRunningError, SingleInstance

from .acquisition_contract import AcquisitionRefused
from .db import Database


@contextmanager
def executor(db: Database, worktree_id: str) -> Iterator[None]:
    key = hashlib.sha256(worktree_id.encode()).hexdigest()
    lease = SingleInstance(
        db.db_path.parent / "acquisition-locks",
        service="acquisition", lock_name=f"{key}.lock",
    )
    try:
        lease.acquire()
    except AlreadyRunningError as exc:
        raise AcquisitionRefused("executor_live", "another acquisition executor still owns this target") from exc
    try:
        yield
    finally:
        lease.release()
