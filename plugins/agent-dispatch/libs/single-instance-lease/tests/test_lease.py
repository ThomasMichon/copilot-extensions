"""Tests for the single-instance lease."""

from __future__ import annotations

import multiprocessing as mp
import os
import sys
import time

import pytest

from single_instance_lease import (
    AlreadyRunningError,
    SingleInstance,
    read_owner_pid,
)


def test_acquire_and_release_roundtrip(tmp_path):
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


def test_second_acquire_same_process_conflicts_posix(tmp_path):
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


def test_port_key_lets_active_and_passive_coexist(tmp_path):
    active = SingleInstance(tmp_path, service="svc", port=9000)
    passive = SingleInstance(tmp_path, service="svc", port=9001)
    active.acquire()
    passive.acquire()  # different lock file -- no conflict
    assert active.lock_path != passive.lock_path
    assert active.lock_path.name == "svc.9000.lock"
    assert passive.lock_path.name == "svc.9001.lock"
    active.release()
    passive.release()


def test_explicit_lock_name_overrides(tmp_path):
    lease = SingleInstance(tmp_path, service="svc", port=1, lock_name="custom.lock")
    lease.acquire()
    assert lease.lock_path.name == "custom.lock"
    lease.release()


def test_service_name_sanitized_into_filename(tmp_path):
    lease = SingleInstance(tmp_path, service="Weird Name/../x")
    lease.acquire()
    # no path separators or spaces leak into the filename
    assert "/" not in lease.lock_path.name
    assert " " not in lease.lock_path.name
    assert lease.lock_path.name.endswith(".lock")
    lease.release()


def test_read_owner_pid_records_holder(tmp_path):
    lease = SingleInstance(tmp_path, service="svc")
    lease.acquire()
    assert read_owner_pid(lease.lock_path) == os.getpid()
    lease.release()


def test_read_owner_pid_missing_file_is_none(tmp_path):
    assert read_owner_pid(tmp_path / "nope.lock") is None


def test_context_manager(tmp_path):
    with SingleInstance(tmp_path, service="svc") as lease:
        assert lease.held
    assert not lease.held


def _hold_lock(lock_dir: str, ready, release) -> None:
    lease = SingleInstance(lock_dir, service="svc")
    lease.acquire()
    ready.set()
    release.wait(10)
    lease.release()


def test_cross_process_exclusion_and_reclaim(tmp_path):
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
