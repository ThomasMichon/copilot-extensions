"""Fail-closed cutover from unlinkable legacy locks to a kernel-lock namespace."""

from __future__ import annotations

import stat
import time
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from pathlib import Path

from .private_state import ensure_private_dir, private_file_lock

PROTOCOL = "kernel-namespace-v1"


class LockCutoverError(RuntimeError):
    """Legacy/invalid lock ownership has not reached a safe cutover boundary."""


@contextmanager
def provider_lock(path: Path, *, timeout: float, poll: float) -> Iterator[None]:
    """Let legacy writers release naturally; never steal or unlink their inode."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            path.mkdir(mode=0o700)
            break
        except FileExistsError:
            try:
                mode = path.lstat().st_mode
            except FileNotFoundError:
                if time.monotonic() >= deadline:
                    raise LockCutoverError("Legacy lease lock release raced until the cutover deadline") from None
                continue
            if stat.S_ISDIR(mode):
                break
            if not stat.S_ISREG(mode):
                raise LockCutoverError("Lease lock namespace is invalid; refusing protocol cutover")
            if time.monotonic() >= deadline:
                raise LockCutoverError("Legacy lease lock has not quiesced; refusing protocol cutover")
            time.sleep(poll)
    # Legacy O_EXCL/unlink code cannot create or unlink this directory, even
    # after its age-based stale-lock recovery threshold has elapsed.
    with ExitStack() as stack:
        try:
            ensure_private_dir(path)
            stack.enter_context(private_file_lock(
                path / "kernel.lock", timeout=max(0, deadline - time.monotonic()),
            ))
        except RuntimeError as exc:
            raise LockCutoverError(str(exc)) from exc
        yield


def legacy_writer_quiesced(record) -> bool:
    from . import lease

    if (
        type(record.pid) is not int or record.pid <= 0
        or not isinstance(record.host, str) or not record.host
        or not isinstance(record.environment, str) or not record.environment
    ):
        raise lease.ProviderAdmissionError("Legacy provider writer identity is indeterminate")
    if record.host != lease._this_host() or record.environment != lease._this_environment():
        return False
    return not lease._pid_alive(record.pid)


def require_legacy_writers_quiesced(
    *, clear_expired_corruption: bool, cleanup_only: bool = False,
) -> None:
    from . import lease

    for path, record_type, ttl in (
        (lease._DEPLOY_HOLDS_FILE, lease.DeployHold, lease.DEPLOY_HOLD_TTL),
        (lease._SESSION_ADMISSIONS_FILE, lease.SessionAdmission, lease.SESSION_ADMISSION_TTL),
    ):
        try:
            records = lease._read_records(path, record_type, fail_closed=True)
        except lease.ProviderAdmissionError:
            if not clear_expired_corruption:
                raise
            try:
                old_enough = time.time() - path.stat().st_mtime > ttl
            except OSError:
                old_enough = False
            if not old_enough:
                raise
            continue
        for record in records.values():
            if record.lock_protocol == PROTOCOL:
                continue
            if record.lock_protocol != "legacy":
                raise lease.ProviderAdmissionError("Provider record has an unknown lock protocol")
            if cleanup_only:
                continue
            try:
                quiesced = legacy_writer_quiesced(record)
            except (AttributeError, TypeError, ValueError) as exc:
                raise lease.ProviderAdmissionError("Legacy provider writer liveness is indeterminate") from exc
            if not quiesced:
                raise lease.ProviderAdmissionError(
                    "Legacy provider writers must quiesce before lock protocol cutover"
                )
