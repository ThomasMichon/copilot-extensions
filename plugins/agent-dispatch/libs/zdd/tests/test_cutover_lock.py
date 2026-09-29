"""Tests for the process-wide cutover lock (zdd.cutover_lock)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from zdd.cutover_lock import CutoverLock, CutoverLockedError, lock_path, read_holder_pid


def test_acquire_and_release(tmp_path: Path):
    lock = CutoverLock(tmp_path)
    lock.acquire()
    assert lock.held
    lock.release()
    assert not lock.held
    # Idempotent release.
    lock.release()


def test_context_manager(tmp_path: Path):
    with CutoverLock(tmp_path) as lock:
        assert lock.held
        assert lock_path(tmp_path).exists()
    assert not lock.held


def test_records_own_pid(tmp_path: Path):
    with CutoverLock(tmp_path):
        assert read_holder_pid(lock_path(tmp_path)) == os.getpid()


def test_second_acquire_same_process_conflicts_posix(tmp_path: Path):
    # On POSIX, flock is per-open-file-description; a second CutoverLock opens
    # its own descriptor and must be refused (mirrors single_instance_lease's
    # own same-process test -- msvcrt on Windows is per-process for the same
    # file, so this same-process assertion is POSIX-only).
    if sys.platform == "win32":
        pytest.skip("flock semantics -- POSIX only")
    first = CutoverLock(tmp_path)
    first.acquire()
    try:
        second = CutoverLock(tmp_path)
        with pytest.raises(CutoverLockedError) as exc_info:
            second.acquire()
        assert exc_info.value.holder_pid == os.getpid()
        assert not second.held
    finally:
        first.release()


def test_released_lock_is_reacquirable(tmp_path: Path):
    first = CutoverLock(tmp_path)
    first.acquire()
    first.release()

    second = CutoverLock(tmp_path)
    second.acquire()
    try:
        assert second.held
    finally:
        second.release()


def test_error_names_lock_path(tmp_path: Path):
    first = CutoverLock(tmp_path)
    first.acquire()
    try:
        second = CutoverLock(tmp_path)
        with pytest.raises(CutoverLockedError) as exc_info:
            second.acquire()
        assert str(lock_path(tmp_path)) in str(exc_info.value)
    finally:
        first.release()
