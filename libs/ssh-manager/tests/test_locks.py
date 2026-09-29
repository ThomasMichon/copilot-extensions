"""Tests for cross-process target locks (ssh_manager.locks)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import ctypes
from types import SimpleNamespace

import pytest
import ssh_manager.locks as locks_mod

from ssh_manager.locks import (
    LockHolder,
    TargetBusyError,
    TargetLock,
    process_identity,
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


class TestProcessIdentity:
    def test_windows_creation_time_identity(self, monkeypatch):
        class Fn:
            def __init__(self, func):
                self.func = func
                self.argtypes = None
                self.restype = None

            def __call__(self, *args):
                return self.func(*args)

        def get_process_times(handle, creation, exit_, kernel, user):
            creation.dwHighDateTime = 1
            creation.dwLowDateTime = 2
            exit_.dwHighDateTime = 0
            exit_.dwLowDateTime = 0
            return 1

        kernel32 = SimpleNamespace(
            OpenProcess=Fn(lambda access, inherit, pid: 99),
            CloseHandle=Fn(lambda handle: 1),
            GetProcessTimes=Fn(get_process_times),
        )

        monkeypatch.setattr(locks_mod.sys, "platform", "win32")
        monkeypatch.setattr(locks_mod, "pid_alive", lambda pid: True)
        monkeypatch.setattr(
            ctypes,
            "WinDLL",
            lambda name, use_last_error=True: kernel32,
            raising=False,
        )
        monkeypatch.setattr(ctypes, "byref", lambda value: value)

        assert process_identity(123) == "windows-filetime:4294967298"

    def test_procfs_start_time_identity(self, monkeypatch):
        tokens = ["S"] + ["0"] * 18 + ["12345"]
        monkeypatch.setattr(locks_mod.sys, "platform", "linux")
        monkeypatch.setattr(locks_mod, "pid_alive", lambda pid: True)
        def read_text(self, encoding="ascii"):
            if str(self) == "/proc/sys/kernel/random/boot_id":
                return "boot-123\n"
            return f"123 (python) {' '.join(tokens)}"
        monkeypatch.setattr(locks_mod.Path, "read_text", read_text)
        assert process_identity(123) == "proc-start:boot-123:12345"

    def test_procfs_zombie_has_no_identity(self, monkeypatch):
        tokens = ["Z"] + ["0"] * 18 + ["12345"]
        monkeypatch.setattr(locks_mod.sys, "platform", "linux")
        monkeypatch.setattr(locks_mod, "pid_alive", lambda pid: True)
        def read_text(self, encoding="ascii"):
            if str(self) == "/proc/sys/kernel/random/boot_id":
                return "boot-123\n"
            return f"123 (python) {' '.join(tokens)}"
        monkeypatch.setattr(locks_mod.Path, "read_text", read_text)
        assert process_identity(123) is None

    def test_ps_fallback_identity(self, monkeypatch):
        monkeypatch.setattr(locks_mod.sys, "platform", "linux")
        monkeypatch.setattr(locks_mod, "pid_alive", lambda pid: True)

        def fail_proc(*_args, **_kwargs):
            raise OSError("missing procfs")

        monkeypatch.setattr(locks_mod.Path, "read_text", fail_proc)
        monkeypatch.setattr(
            locks_mod.subprocess,
            "run",
            lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="Mon Sep 29 04:00:00 2026\n"),
        )
        assert process_identity(123) == "ps-start:Mon Sep 29 04:00:00 2026"


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
        victim, reaper = _spawn_sleeper()
        holder = LockHolder(
            pid=victim,
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
            reaper.join(timeout=5)

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
        victim, reaper = _spawn_sleeper()
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
            reaper.join(timeout=5)


def _spawn_sleeper() -> tuple[int, threading.Thread]:
    """Spawn a short-lived child process and return its pid, plus a daemon
    thread that reaps it as soon as it exits.

    ``_terminate()`` (production code) sends a signal, then polls
    ``pid_alive()`` (``os.kill(pid, 0)``) until it sees the process gone --
    but for a process this TEST itself spawned as a direct child, only ITS
    OWN parent (this test process) can reap it; nothing else will. Without
    an active ``waitpid``, a SIGTERM'd child becomes a zombie that
    ``os.kill(pid, 0)`` still reports as "alive" indefinitely, so
    ``_terminate()``'s polling loop -- and this test's own
    ``pid_alive(victim) is False`` assertion -- would never observe real
    death. A background thread blocked in ``Popen.wait()`` reaps the child
    the moment it actually exits, which real production usage never needs
    (a lock holder is typically an unrelated process already reparented to
    init, which reaps it for free)."""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    reaper = threading.Thread(target=proc.wait, daemon=True)
    reaper.start()
    # Give it a moment to actually start.
    time.sleep(0.2)
    return proc.pid, reaper


def _terminate_pid(pid: int) -> None:
    from ssh_manager.locks import _terminate

    _terminate(pid, grace=2.0)
