"""Explicit guest operation exclusion; not case admission or crash recovery."""

from __future__ import annotations

import os
import stat
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from guest_mounts import MAX_SECONDS, read_mount_table
from mounts import StateMount, inspect_state_mount

if TYPE_CHECKING:
    from single_instance_lease import OperationLock

STATE = Path("/lab/state")
LOCK = STATE / "reproducer-driver.lock"


@dataclass(frozen=True)
class GuestState:
    device: int
    inode: int
    mount: StateMount


def observe_state() -> GuestState:
    """Read only the existing fixed Linux state root with bounded table input."""
    if sys.platform != "linux" or (os.geteuid(), os.getegid()) != (1000, 1000):
        raise ValueError("guest operation requires Linux identity 1000:1000")
    started = time.monotonic()
    before = read_mount_table()
    if STATE.resolve(strict=True) != STATE:
        raise ValueError("guest state path is linked or redirected")
    descriptor = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        filesystem = os.fstatvfs(descriptor)
        if (
            not stat.S_ISDIR(info.st_mode)
            or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (1000, 1000, 0o700)
            or filesystem.f_flag & os.ST_RDONLY
            or not os.path.samestat(info, STATE.lstat())
            or STATE.resolve(strict=True) != STATE
        ):
            raise ValueError("guest state directory identity or posture differs")
        mount = inspect_state_mount(
            before, f"{os.major(info.st_dev)}:{os.minor(info.st_dev)}",
        )
        if before != read_mount_table():
            raise ValueError("guest state observation changed or expired")
        current = STATE.lstat()
        if (
            not os.path.samestat(info, current) or STATE.resolve(strict=True) != STATE
            or (current.st_mode, current.st_uid, current.st_gid)
            != (info.st_mode, info.st_uid, info.st_gid)
        ):
            raise ValueError("guest state directory changed during observation")
        if time.monotonic() - started >= MAX_SECONDS:
            raise ValueError("guest state observation changed or expired")
        return GuestState(info.st_dev, info.st_ino, mount)
    finally:
        os.close(descriptor)


def _check_lock(state: GuestState, *, required: bool) -> None:
    try:
        info = LOCK.lstat()
    except FileNotFoundError:
        if required:
            raise ValueError("guest operation lock disappeared")
        return
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
        or info.st_dev != state.device
        or (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (1000, 1000, 0o600)
    ):
        raise ValueError("guest operation lock identity or posture differs")


def _new_lock() -> OperationLock:
    source = Path(__file__).resolve().parents[2] / "libs" / "single-instance-lease" / "src"
    if str(source) not in sys.path:
        sys.path.insert(0, str(source))
    from single_instance_lease import OperationLock

    return OperationLock(LOCK)


@dataclass(frozen=True)
class GuestOperation:
    _state: GuestState
    _holder: OperationLock

    def assert_owned(self) -> None:
        """Reobserve the state mount and current lock before a caller's effect."""
        if observe_state() != self._state:
            raise ValueError("guest state changed during operation custody")
        _check_lock(self._state, required=True)
        self._holder.assert_owned()


@contextmanager
def operation() -> Iterator[GuestOperation]:
    """Serialize a separately authorized operation on this physical state root.

    The caller must join existing task/Bridge journals before any new case or
    replay. Acquisition after a crash does not settle accepted descendants.
    This API never provisions state or runs from the read-only host reader.
    """
    before = observe_state()
    _check_lock(before, required=False)
    holder = _new_lock()
    with holder:
        custody = GuestOperation(before, holder)
        custody.assert_owned()
        try:
            yield custody
        finally:
            custody.assert_owned()
