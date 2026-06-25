"""Tests for the daemon single-instance guard (agent_bridge.singleton)."""

from __future__ import annotations

import os

import pytest

from agent_bridge.singleton import (
    AlreadyRunningError,
    SingleInstance,
    _read_holder_pid,
)


def test_acquire_creates_lock_and_records_pid(tmp_path):
    guard = SingleInstance(tmp_path)
    guard.acquire()
    try:
        assert guard.lock_path.exists()
        assert _read_holder_pid(guard.lock_path) == os.getpid()
    finally:
        guard.release()


def test_second_instance_same_dir_is_refused(tmp_path):
    first = SingleInstance(tmp_path)
    first.acquire()
    try:
        second = SingleInstance(tmp_path)
        with pytest.raises(AlreadyRunningError) as ei:
            second.acquire()
        # The error names the live holder (this process) for diagnostics.
        assert ei.value.holder_pid == os.getpid()
        assert str(tmp_path) in str(ei.value.lock_path)
    finally:
        first.release()


def test_lock_is_reusable_after_release(tmp_path):
    first = SingleInstance(tmp_path)
    first.acquire()
    first.release()
    # Once released, a new instance for the same dir must acquire cleanly.
    second = SingleInstance(tmp_path)
    second.acquire()
    try:
        assert _read_holder_pid(second.lock_path) == os.getpid()
    finally:
        second.release()


def test_distinct_dirs_do_not_conflict(tmp_path):
    primary = SingleInstance(tmp_path / "primary")
    elevated = SingleInstance(tmp_path / "elevated")
    primary.acquire()
    elevated.acquire()
    try:
        # Distinct config dirs (primary vs elevated sub-daemon) each get their
        # own singleton -- they must not block one another.
        assert primary.lock_path != elevated.lock_path
    finally:
        primary.release()
        elevated.release()


def test_context_manager_releases(tmp_path):
    with SingleInstance(tmp_path):
        contender = SingleInstance(tmp_path)
        with pytest.raises(AlreadyRunningError):
            contender.acquire()
    # After the `with` block exits, the lock is free again.
    again = SingleInstance(tmp_path)
    again.acquire()
    again.release()


def test_release_is_idempotent(tmp_path):
    guard = SingleInstance(tmp_path)
    guard.acquire()
    guard.release()
    guard.release()  # second release must be a no-op, not an error


def test_read_holder_pid_missing_returns_none(tmp_path):
    assert _read_holder_pid(tmp_path / "does-not-exist.lock") is None
