"""Tests for cross-process target locks (ssh_manager.locks)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from ssh_manager.locks import (
    LockHolder,
    TargetBusyError,
    TargetLock,
    pid_alive,
)


@pytest.fixture
def lock_dir(tmp_path):
    return tmp_path / "locks"


def _make(target, lock_dir, **kw):
    return TargetLock(target, directory=lock_dir, **kw)


class TestPidAlive:
    def test_current_process_alive(self):
        assert pid_alive(os.getpid()) is True

    def test_nonpositive_pid_not_alive(self):
        assert pid_alive(0) is False
        assert pid_alive(-1) is False

    def test_almost_certainly_dead_pid(self):
        # A very high pid is almost never live on a fresh machine.
        assert pid_alive(2**31 - 1) is False


class TestAcquireRelease:
    def test_acquire_writes_holder(self, lock_dir):
        lock = _make("cs-alpha", lock_dir, op="stdio")
        lock.acquire()
        try:
            assert lock.path.exists()
            data = json.loads(lock.path.read_text(encoding="utf-8"))
            assert data["pid"] == os.getpid()
            assert data["op"] == "stdio"
            assert data["target"] == "cs-alpha"
        finally:
            lock.release()
        assert not lock.path.exists()

    def test_context_manager(self, lock_dir):
        lock = _make("cs-beta", lock_dir)
        with lock:
            assert lock.path.exists()
        assert not lock.path.exists()

    def test_reentrant_same_process(self, lock_dir):
        a = _make("cs-gamma", lock_dir)
        b = _make("cs-gamma", lock_dir)
        a.acquire()
        try:
            # Same-process second acquire must not raise.
            b.acquire()
        finally:
            a.release()

    def test_distinct_targets_independent(self, lock_dir):
        a = _make("cs-one", lock_dir).acquire()
        b = _make("cs-two", lock_dir).acquire()
        try:
            assert a.path != b.path
            assert a.path.exists() and b.path.exists()
        finally:
            a.release()
            b.release()


class TestContention:
    def test_busy_when_held_by_live_other(self, lock_dir):
        lock = _make("cs-busy", lock_dir, op="remote-cmd")
        # Simulate another live process holding the lock (use a real live pid).
        lock._dir.mkdir(parents=True, exist_ok=True)
        holder = LockHolder(
            pid=_spawn_sleeper(),
            op="stdio",
            target="cs-busy",
            started_at=time.time(),
        )
        lock.path.write_text(json.dumps(holder.__dict__), encoding="utf-8")
        try:
            with pytest.raises(TargetBusyError) as ei:
                lock.acquire()
            assert ei.value.holder.pid == holder.pid
            assert "BUSY" in ei.value.user_message()
        finally:
            _terminate_pid(holder.pid)

    def test_stale_lock_reclaimed(self, lock_dir):
        lock = _make("cs-stale", lock_dir)
        lock._dir.mkdir(parents=True, exist_ok=True)
        dead = LockHolder(
            pid=2**31 - 1, op="stdio", target="cs-stale", started_at=time.time()
        )
        lock.path.write_text(json.dumps(dead.__dict__), encoding="utf-8")
        # Dead holder -> acquire reclaims silently.
        lock.acquire()
        try:
            data = json.loads(lock.path.read_text(encoding="utf-8"))
            assert data["pid"] == os.getpid()
        finally:
            lock.release()

    def test_unreadable_lock_treated_stale(self, lock_dir):
        lock = _make("cs-garbage", lock_dir)
        lock._dir.mkdir(parents=True, exist_ok=True)
        lock.path.write_text("not json {{{", encoding="utf-8")
        lock.acquire()
        try:
            assert lock.read_holder().pid == os.getpid()
        finally:
            lock.release()

    def test_force_evicts_live_holder(self, lock_dir):
        lock = _make("cs-force", lock_dir)
        lock._dir.mkdir(parents=True, exist_ok=True)
        victim = _spawn_sleeper()
        holder = LockHolder(
            pid=victim, op="stdio", target="cs-force", started_at=time.time()
        )
        lock.path.write_text(json.dumps(holder.__dict__), encoding="utf-8")
        assert pid_alive(victim) is True
        lock.acquire(force=True)
        try:
            # Victim terminated, we own the lock.
            assert pid_alive(victim) is False
            assert lock.read_holder().pid == os.getpid()
        finally:
            lock.release()
            _terminate_pid(victim)


def _spawn_sleeper() -> int:
    """Spawn a short-lived child process and return its pid."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Give it a moment to actually start.
    time.sleep(0.2)
    return proc.pid


def _terminate_pid(pid: int) -> None:
    from ssh_manager.locks import _terminate

    _terminate(pid, grace=2.0)
