"""Explicit disposition of one recorded launch failure that never entered execution."""

from __future__ import annotations

import re
import time
from pathlib import Path

from single_instance_lease.operation import OperationLock, OperationLockedError

from .core import (
    DriverError, InvalidRequestError, Request, RequestNotFoundError,
    _assert_database_identity, _database, _request, _root, _text,
)

FAILED_UNSTARTED_DISPOSITION_CAPABILITY = "update-driver.failed-unstarted-disposition/v1"


class DispositionError(DriverError):
    """The selected request cannot be resolved as a failed unstarted launch."""


def resolve_failed_unstarted(
    request_id: str, *, reason: str, authorized: bool = False, root: Path | None = None,
) -> Request:
    """Retain one failed launch as terminal non-success, without replay or cleanup."""
    if authorized is not True:
        raise DispositionError("explicit operator repair authorization is required")
    if not isinstance(request_id, str) or re.fullmatch(r"[0-9a-f]{32}", request_id) is None:
        raise InvalidRequestError("an exact request identifier is required")
    reason = _text(reason).strip()
    if not reason or len(reason) > 1024:
        raise InvalidRequestError("a bounded nonempty disposition reason is required")
    root = _root(root)
    path = root / "queue.sqlite3"
    try:
        identity = path.lstat()
    except FileNotFoundError as error:
        raise DispositionError("queue missing; disposition refused") from error
    lock = OperationLock(root / "execution.lock")
    try:
        lock.acquire()
    except OperationLockedError as error:
        raise DispositionError("installation execution is active; disposition refused") from error
    try:
        _assert_database_identity(path, identity)
        with _database(root, writable=True, expected_identity=identity) as db, db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM requests WHERE id=?", (request_id,)).fetchone()
            if row is None:
                raise RequestNotFoundError(request_id)
            request = _request(row, root)
            marker = f"\nFailed-unstarted disposition: {reason}"
            if (
                request.state == "completed" and request.exit_code == 125
                and request.started_at is None and request.boot_id is None
                and request.finished_at is not None
                and request.error is not None and request.error.endswith(marker)
            ):
                return request
            if (
                request.state != "pending" or request.started_at is not None
                or request.finished_at is not None or request.boot_id is not None
                or request.exit_code is not None or request.error is None
                or not request.error.startswith("worker launch failed:")
            ):
                raise DispositionError("request is not a recorded failed-unstarted launch")
            detail = _text(request.error + marker)
            changed = db.execute(
                """UPDATE requests SET state='completed',exit_code=125,error=?,finished_at=?
                WHERE id=? AND state='pending' AND started_at IS NULL
                AND finished_at IS NULL AND boot_id IS NULL AND exit_code IS NULL AND error=?""",
                (detail, time.time(), request.id, request.error),
            )
            if changed.rowcount != 1:
                raise DispositionError("request changed; disposition refused")
            lock.assert_owned()
            _assert_database_identity(path, identity)
            result = _request(
                db.execute("SELECT * FROM requests WHERE id=?", (request.id,)).fetchone(), root,
            )
            db.commit()
            return result
    finally:
        lock.release()
