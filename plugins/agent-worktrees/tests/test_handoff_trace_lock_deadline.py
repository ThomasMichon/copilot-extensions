"""Canonical trace-lock admission, preserving the existing blocking callers."""

import errno
import json
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from agent_worktrees import handoff_trace


@pytest.mark.parametrize("deadline", [True, float("nan"), float("inf"), "later"])
def test_invalid_deadline_has_no_file_effect(tmp_path, deadline):
    path = tmp_path / "new" / "trace.lock"
    with pytest.raises(ValueError):
        with handoff_trace._append_lock(path, deadline=deadline):
            pytest.fail("invalid admission entered body")
    assert not path.parent.exists()


def test_expired_deadline_does_not_enter_or_create(tmp_path):
    path = tmp_path / "trace.lock"
    with pytest.raises(TimeoutError):
        with handoff_trace._append_lock(path, deadline=time.monotonic() - 1):
            pytest.fail("expired admission entered body")
    assert not path.exists()


def test_windows_bounded_acquire_uses_nonblocking_and_releases(tmp_path, monkeypatch):
    calls = []
    clock = [10.0]
    sleeps = []

    def locking(fd, mode, _count):
        calls.append((mode, os.fstat(fd).st_size))
        if mode == 2 and sum(item[0] == 2 for item in calls) < 3:
            raise OSError(errno.EACCES, "controlled contention")

    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_LOCK=1, LK_NBLCK=2, LK_UNLCK=3, locking=locking))
    monkeypatch.setattr(handoff_trace, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(handoff_trace, "time", SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep))
    with handoff_trace._append_lock(tmp_path / "trace.lock", deadline=10.1):
        assert calls[-1] == (2, 0)
    assert calls[-1] == (3, 1)
    assert len(sleeps) == 2 and max(sleeps) <= 0.01


def test_windows_default_call_retains_native_blocking_mode(tmp_path, monkeypatch):
    calls = []
    fake = SimpleNamespace(LK_LOCK=1, LK_UNLCK=3, locking=lambda _fd, mode, _n: calls.append(mode))
    monkeypatch.setitem(sys.modules, "msvcrt", fake)
    monkeypatch.setattr(handoff_trace, "os", SimpleNamespace(name="nt"))
    with handoff_trace._append_lock(tmp_path / "trace.lock"):
        pass
    assert calls == [1, 3]


def test_unexpected_lock_error_is_not_retried(tmp_path, monkeypatch):
    calls = []

    def locking(_fd, mode, _n):
        calls.append(mode)
        raise OSError(errno.EIO, "controlled failure")

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=2, LK_UNLCK=3, locking=locking))
    monkeypatch.setattr(handoff_trace, "os", SimpleNamespace(name="nt"))
    with pytest.raises(OSError) as error:
        with handoff_trace._append_lock(tmp_path / "trace.lock", deadline=time.monotonic() + 1):
            pytest.fail("failed admission entered body")
    assert error.value.errno == errno.EIO
    assert calls == [2]


def test_post_acquisition_expiry_releases_without_body(tmp_path, monkeypatch):
    clock = [10.0]
    calls = []

    def locking(_fd, mode, _n):
        calls.append(mode)
        clock[0] = 11.0

    monkeypatch.setitem(sys.modules, "msvcrt", SimpleNamespace(LK_NBLCK=2, LK_UNLCK=3, locking=locking))
    monkeypatch.setattr(handoff_trace, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(handoff_trace, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    with pytest.raises(TimeoutError):
        with handoff_trace._append_lock(tmp_path / "trace.lock", deadline=10.1):
            pytest.fail("late admission entered body")
    assert calls == [2, 3]
    assert (tmp_path / "trace.lock").read_bytes() == b""


def test_real_process_contention_times_out_then_same_lock_is_reusable(tmp_path):
    lock = tmp_path / "trace.lock"
    ready = tmp_path / "ready"
    script = """
import json,pathlib,sys
sys.path[:0]=json.loads(sys.argv[1])
from agent_worktrees.handoff_trace import _append_lock
with _append_lock(pathlib.Path(sys.argv[2])):
    pathlib.Path(sys.argv[3]).write_text('ready')
    sys.stdin.readline()
"""
    child = subprocess.Popen(
        [sys.executable, "-c", script, json.dumps(sys.path), str(lock), str(ready)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        startup = time.monotonic() + 10
        while not ready.exists():
            assert child.poll() is None and time.monotonic() < startup
            time.sleep(0.01)
        before = lock.stat()
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            with handoff_trace._append_lock(lock, deadline=started + 0.1):
                pytest.fail("contended admission entered body")
        assert time.monotonic() - started < 0.5
        child.communicate(b"\n", timeout=5)
        assert child.returncode == 0
        with handoff_trace._append_lock(lock, deadline=time.monotonic() + 0.1):
            assert lock.stat().st_ino == before.st_ino
        with pytest.raises(RuntimeError):
            with handoff_trace._append_lock(lock, deadline=time.monotonic() + 0.1):
                raise RuntimeError("body failure")
        with handoff_trace._append_lock(lock):
            pass
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
