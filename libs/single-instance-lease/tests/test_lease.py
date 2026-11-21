"""Tests for the single-instance lease."""

from __future__ import annotations

import multiprocessing as mp
from multiprocessing.synchronize import Event
import os
import sys
import time
from pathlib import Path

import pytest

from single_instance_lease import (
    AlreadyRunningError,
    SingleInstance,
    read_owner_pid,
)


def test_acquire_and_release_roundtrip(tmp_path: Path) -> None:
    lease = SingleInstance(tmp_path, service="svc")
    assert not lease.held
    lease.acquire()
    assert lease.held
    assert lease.lock_path.name == "svc.lock"
    assert lease.lock_path.exists()
    lease.release()
    assert not lease.held
    # release is idempotent
    lease.release()


def test_second_acquire_same_process_conflicts_posix(tmp_path: Path) -> None:
    # On POSIX, flock is per-open-file-description; a second SingleInstance opens
    # its own descriptor and must be refused. (msvcrt on Windows is per-process
    # for the same file, so this same-process assertion is POSIX-only.)
    if sys.platform == "win32":
        pytest.skip("flock semantics -- POSIX only")
    a = SingleInstance(tmp_path, service="svc")
    a.acquire()
    b = SingleInstance(tmp_path, service="svc")
    with pytest.raises(AlreadyRunningError) as ei:
        b.acquire()
    assert ei.value.holder_pid == os.getpid()
    a.release()
    # once released, a fresh contender may take it
    b.acquire()
    b.release()


def test_port_key_lets_active_and_passive_coexist(tmp_path: Path) -> None:
    active = SingleInstance(tmp_path, service="svc", port=9000)
    passive = SingleInstance(tmp_path, service="svc", port=9001)
    active.acquire()
    passive.acquire()  # different lock file -- no conflict
    assert active.lock_path != passive.lock_path
    assert active.lock_path.name == "svc.9000.lock"
    assert passive.lock_path.name == "svc.9001.lock"
    active.release()
    passive.release()


def test_explicit_lock_name_overrides(tmp_path: Path) -> None:
    lease = SingleInstance(tmp_path, service="svc", port=1, lock_name="custom.lock")
    lease.acquire()
    assert lease.lock_path.name == "custom.lock"
    lease.release()


def test_service_name_sanitized_into_filename(tmp_path: Path) -> None:
    lease = SingleInstance(tmp_path, service="Weird Name/../x")
    lease.acquire()
    # no path separators or spaces leak into the filename
    assert "/" not in lease.lock_path.name
    assert " " not in lease.lock_path.name
    assert lease.lock_path.name.endswith(".lock")
    lease.release()


def test_read_owner_pid_records_holder(tmp_path: Path) -> None:
    lease = SingleInstance(tmp_path, service="svc")
    lease.acquire()
    assert read_owner_pid(lease.lock_path) == os.getpid()
    lease.release()


def test_held_file_identity_requires_current_native_custody(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    lease = SingleInstance(tmp_path, service="svc")
    with pytest.raises(PermissionError):
        lease.held_file_identity()
    lease.acquire()
    try:
        info = lease.lock_path.lstat()
        assert lease.held_file_identity() == (info.st_dev, info.st_ino)
        with monkeypatch.context() as child:
            child.setattr(os, "getpid", lambda: -1)
            with pytest.raises(PermissionError):
                lease.held_file_identity()
    finally:
        lease.release()
    with pytest.raises(PermissionError):
        lease.held_file_identity()


@pytest.mark.skipif(sys.platform == "win32", reason="Windows prevents moving an open lease")
def test_held_file_identity_refuses_replaced_or_linked_path(tmp_path: Path) -> None:
    lease = SingleInstance(tmp_path, service="svc")
    lease.acquire()
    retained = tmp_path / "retained"
    try:
        lease.lock_path.rename(retained)
        lease.lock_path.write_text("replacement", encoding="ascii")
        with pytest.raises(PermissionError):
            lease.held_file_identity()
        lease.lock_path.unlink()
        lease.lock_path.symlink_to(retained)
        with pytest.raises(PermissionError):
            lease.held_file_identity()
    finally:
        lease.release()


def test_read_owner_pid_missing_file_is_none(tmp_path: Path) -> None:
    assert read_owner_pid(tmp_path / "nope.lock") is None


@pytest.mark.parametrize("change", ["missing", "replacement", "link"])
def test_existing_identity_acquisition_never_creates_or_writes_a_changed_file(
    tmp_path: Path, change: str,
) -> None:
    lease = SingleInstance(tmp_path, service="svc")
    lease.lock_path.write_bytes(b"original")
    info = lease.lock_path.stat()
    original = tmp_path / "original.lock"
    lease.lock_path.rename(original)
    if change == "replacement":
        lease.lock_path.write_bytes(b"replacement")
    elif change == "link":
        if sys.platform == "win32":
            pytest.skip("symlink creation requires separate Windows privilege")
        lease.lock_path.symlink_to(original)
    try:
        with pytest.raises((OSError, PermissionError)):
            lease.acquire(expected_file_identity=(info.st_dev, info.st_ino))
        assert not lease.held
        assert original.read_bytes() == b"original"
        if change == "replacement":
            assert lease.lock_path.read_bytes() == b"replacement"
        elif change == "missing":
            assert not lease.lock_path.exists()
    finally:
        lease.release()


def _release_inherited(lease: SingleInstance) -> None:
    lease.release()


def test_existing_identity_acquisition_holds_and_records_only_that_file(tmp_path: Path) -> None:
    lease = SingleInstance(tmp_path, service="svc")
    lease.lock_path.write_bytes(b"original")
    info = lease.lock_path.stat()
    identity = (info.st_dev, info.st_ino)
    lease.acquire(expected_file_identity=identity)
    try:
        assert lease.held_file_identity() == identity
        assert read_owner_pid(lease.lock_path) == os.getpid()
    finally:
        lease.release()


def test_inherited_release_does_not_unlock_the_parent(tmp_path: Path) -> None:
    if sys.platform == "win32":
        pytest.skip("forked flock descriptions are POSIX-only")
    owner = SingleInstance(tmp_path, service="svc")
    owner.acquire()
    identity = owner.held_file_identity()
    child = mp.get_context("fork").Process(target=_release_inherited, args=(owner,))
    contender = SingleInstance(tmp_path, service="svc")
    child.start()
    try:
        child.join(10)
        assert child.exitcode == 0
        assert owner.held_file_identity() == identity
        with pytest.raises(AlreadyRunningError):
            contender.acquire()
    finally:
        if child.is_alive():
            child.terminate()
            child.join(10)
        owner.release()
        contender.release()
    contender.acquire()
    contender.release()


def test_context_manager(tmp_path: Path) -> None:
    with SingleInstance(tmp_path, service="svc") as lease:
        assert lease.held
    assert not lease.held


def _hold_lock(lock_dir: str, ready: Event, release: Event) -> None:
    lease = SingleInstance(lock_dir, service="svc")
    lease.acquire()
    ready.set()
    release.wait(10)
    lease.release()


def test_cross_process_exclusion_and_reclaim(tmp_path: Path) -> None:
    # A lock held by another process blocks us; when that process dies the
    # kernel frees the lock and we can acquire it (liveness-reconciled).
    ctx = mp.get_context("spawn")
    ready = ctx.Event()
    release = ctx.Event()
    proc = ctx.Process(target=_hold_lock, args=(str(tmp_path), ready, release))
    proc.start()
    try:
        assert ready.wait(10)
        contender = SingleInstance(tmp_path, service="svc")
        with pytest.raises(AlreadyRunningError):
            contender.acquire()
    finally:
        release.set()
        proc.join(10)
    # The holder exited -> the lock is now reclaimable.
    for _ in range(50):
        try:
            contender = SingleInstance(tmp_path, service="svc")
            contender.acquire()
            contender.release()
            break
        except AlreadyRunningError:
            time.sleep(0.1)
    else:
        pytest.fail("lock not reclaimed after holder exit")
