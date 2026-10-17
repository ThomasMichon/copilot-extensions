"""Real execution custody, including death, threads and inherited references."""

from __future__ import annotations

import errno
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from agent_procutil import direct_python, no_window_kwargs

from single_instance_lease import OperationLock, OperationLockedError, OperationOwnershipError
from single_instance_lease import operation


_HOLDER = """
import os, sys
from pathlib import Path
from single_instance_lease import OperationLock
with OperationLock(Path(sys.argv[1])) as custody:
    custody.assert_owned()
    ready = Path(sys.argv[2])
    pending = ready.with_suffix(".pending")
    pending.write_text(str(os.getpid()), encoding="ascii")
    pending.replace(ready)
    sys.stdin.read(1)
"""

_PROBE = """
import sys
from pathlib import Path
from single_instance_lease import OperationLock, OperationLockedError
try:
    with OperationLock(Path(sys.argv[1])):
        sys.exit(7)
except OperationLockedError:
    pass
"""


def _assert_kernel_held(path):
    python, extra_env = direct_python(sys.executable)
    result = subprocess.run(
        [python, "-c", _PROBE, str(path)],
        env={**os.environ, **extra_env, "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    assert result.returncode == 0, (result.stdout, result.stderr)


@pytest.mark.parametrize("crash", [False, True])
def test_native_process_exclusion_and_crash_release(tmp_path, crash):
    path = tmp_path / "operation.lock"
    ready = tmp_path / "ready"
    python, extra_env = direct_python(sys.executable)
    child = subprocess.Popen(
        [python, "-c", _HOLDER, str(path), str(ready)],
        env={**os.environ, **extra_env, "PYTHONPATH": os.pathsep.join(sys.path)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, **no_window_kwargs(),
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        assert child.pid == int(ready.read_text(encoding="ascii"))
        with pytest.raises(OperationLockedError):
            OperationLock(path).acquire()
        if crash:
            child.kill()
        else:
            child.stdin.write("x")
            child.stdin.flush()
        stdout, stderr = child.communicate(timeout=5)
        assert not stdout and not stderr, (stdout, stderr)
        with OperationLock(path) as recovered:
            recovered.assert_owned()
        assert path.is_file()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def test_same_process_handles_and_threads_cannot_overlap(tmp_path):
    path = tmp_path / "operation.lock"
    with OperationLock(path) as custody:
        with pytest.raises(OperationLockedError):
            OperationLock(path).acquire()
        errors = []

        def contend():
            for action in (custody.assert_owned, custody.release):
                try:
                    action()
                except OperationOwnershipError as error:
                    errors.append(error)
            with pytest.raises(OperationLockedError):
                OperationLock(path).acquire()

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(contend).result(timeout=5)
        assert len(errors) == 2
        custody.assert_owned()
        _assert_kernel_held(path)
        with pytest.raises(OperationOwnershipError, match="already acquired"):
            custody.acquire()
    custody.release()
    with custody:
        custody.assert_owned()


def test_concurrent_acquire_on_one_instance_cannot_clear_the_winner(tmp_path, monkeypatch):
    path = tmp_path / "operation.lock"
    custody = OperationLock(path)
    owner_at_native, contender_at_guard = threading.Event(), threading.Event()
    owner_acquired, contender_done, release_owner = (
        threading.Event(), threading.Event(), threading.Event()
    )
    role = threading.local()
    native_guard = threading.Lock()

    class Gate:
        def acquire(self):
            return native_guard.acquire()

        def release(self):
            native_guard.release()

        def __enter__(self):
            if getattr(role, "name", None) == "contender" and not getattr(role, "seen", False):
                role.seen = True
                contender_at_guard.set()
                assert owner_acquired.wait(5)
            self.acquire()

        def __exit__(self, *_):
            self.release()

    original = operation._acquire_os_lock

    def native(stream):
        if getattr(role, "name", None) == "owner":
            owner_at_native.set()
            assert contender_at_guard.wait(5)
        original(stream)

    monkeypatch.setattr(operation, "_guard", Gate())
    monkeypatch.setattr(operation, "_acquire_os_lock", native)

    def own():
        role.name = "owner"
        with custody:
            owner_acquired.set()
            assert contender_done.wait(5)
            custody.assert_owned()
            assert release_owner.wait(15)

    def contend():
        role.name = "contender"
        try:
            with pytest.raises((OperationOwnershipError, OperationLockedError)):
                custody.acquire()
        finally:
            contender_done.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner = executor.submit(own)
        try:
            assert owner_at_native.wait(5)
            contender = executor.submit(contend)
            contender.result(timeout=10)
            _assert_kernel_held(path)
        finally:
            release_owner.set()
        owner.result(timeout=10)
    with OperationLock(path) as next_operation:
        next_operation.assert_owned()


def test_linked_or_nonregular_lock_is_refused(tmp_path):
    target = tmp_path / "target"
    target.write_text("foreign", encoding="ascii")
    path = tmp_path / "operation.lock"
    try:
        path.symlink_to(target)
    except OSError as error:
        pytest.skip(f"native symlinks unavailable: {error}")
    with pytest.raises(OperationOwnershipError, match="plain file"):
        OperationLock(path).acquire()
    assert target.read_text(encoding="ascii") == "foreign"
    path.unlink()
    path.mkdir()
    with pytest.raises(OperationOwnershipError, match="plain file"):
        OperationLock(path).acquire()


@pytest.mark.skipif(os.name == "nt", reason="POSIX unlink and fork contracts")
def test_replaced_inode_is_not_ownership(tmp_path):
    path = tmp_path / "operation.lock"
    with OperationLock(path) as custody:
        path.unlink()
        path.touch()
        with pytest.raises(OperationOwnershipError, match="replaced"):
            custody.assert_owned()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="native fork unavailable")
def test_inherited_reference_cannot_release_parent(tmp_path):
    path = tmp_path / "operation.lock"
    with OperationLock(path) as custody:
        pid = os.fork()
        if pid == 0:
            try:
                try:
                    custody.release()
                except OperationOwnershipError:
                    pass
                else:
                    os._exit(2)
                try:
                    OperationLock(path).acquire()
                except OperationLockedError:
                    os._exit(0)
                os._exit(3)
            except BaseException:
                os._exit(4)
        _, status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        custody.assert_owned()
        with pytest.raises(OperationLockedError):
            OperationLock(path).acquire()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="native fork unavailable")
@pytest.mark.parametrize("close_error", [False, True])
def test_live_forked_child_does_not_retain_released_parent_custody(tmp_path, close_error):
    import select
    import signal

    path = tmp_path / "operation.lock"
    ready_read, ready_write = os.pipe()
    stop_read, stop_write = os.pipe()
    custody = OperationLock(path)
    custody.acquire()
    peer_path = tmp_path / "peer.lock"
    peer = OperationLock(peer_path)
    peer.acquire()
    original_file = custody._file
    if close_error:
        class CloseFailure:
            def close(self):
                original_file.close()
                raise OSError(errno.EBADF, "injected close failure")
        custody._file = CloseFailure()
    pid = os.fork()
    if pid == 0:
        os.close(ready_read)
        os.close(stop_write)
        try:
            os.write(ready_write, b"ready")
            if not select.select([stop_read], [], [], 10)[0]:
                os._exit(2)
            os.read(stop_read, 1)
            os._exit(0)
        except BaseException:
            os._exit(3)
    custody._file = original_file
    os.close(ready_write)
    os.close(stop_read)
    child_status = None
    try:
        assert select.select([ready_read], [], [], 5)[0]
        assert os.read(ready_read, 5) == b"ready"
        custody.release()
        peer.release()
        with OperationLock(path) as successor:
            successor.assert_owned()
        with OperationLock(peer_path) as next_peer:
            next_peer.assert_owned()
        reaped, status = os.waitpid(pid, os.WNOHANG)
        if reaped:
            child_status = status
        assert reaped == 0, "forked child must still be alive"
    finally:
        custody.release()
        peer.release()
        try:
            os.write(stop_write, b"x")
        except BrokenPipeError:
            pass
        os.close(stop_write)
        os.close(ready_read)
        deadline = time.monotonic() + 5
        while child_status is None and time.monotonic() < deadline:
            reaped, status = os.waitpid(pid, os.WNOHANG)
            if reaped:
                child_status = status
            else:
                time.sleep(.01)
        if child_status is None:
            os.kill(pid, signal.SIGKILL)
            _, child_status = os.waitpid(pid, 0)
        assert os.waitstatus_to_exitcode(child_status) == 0


def test_fork_cleanup_closes_all_handles_before_reporting_error(tmp_path, monkeypatch):
    failure = OSError(errno.EBADF, "injected close failure")
    with (tmp_path / "first").open("w+b") as first, (tmp_path / "second").open("w+b") as second:
        class CloseFailure:
            def close(self):
                first.close()
                raise failure
        guard = threading.Lock()
        guard.acquire()
        monkeypatch.setattr(operation, "_guard", guard)
        monkeypatch.setattr(operation, "_holders", {
            (0, 0, 1): SimpleNamespace(_file=CloseFailure()),
            (0, 0, 2): SimpleNamespace(_file=second),
        })
        with pytest.raises(OSError) as caught:
            operation._after_fork()
        assert caught.value is failure
        assert first.closed and second.closed
        assert operation._holders == {}
        assert operation._guard.acquire(blocking=False)
        operation._guard.release()
