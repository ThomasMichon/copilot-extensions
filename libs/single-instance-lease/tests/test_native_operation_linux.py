"""Real Linux native-to-Python custody, never a cold foundation installation."""

import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import pytest

from single_instance_lease.operation import (
    OperationLock, OperationLockedError, OperationOwnershipError,
)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux per-descriptor flock evidence")
SOURCE = Path(__file__).parents[1] / "src" / "single_instance_lease" / "operation.sh"


def await_file(path, process, log):
    deadline = time.monotonic() + 10
    while not path.exists():
        assert process.poll() is None, log.read_text()
        assert time.monotonic() < deadline, log.read_text()
        time.sleep(.01)


@pytest.mark.parametrize("mode", ["native", "handoff", "death"])
def test_bash_custody_excludes_python_and_transfers_without_unlock(tmp_path, mode):
    lock = tmp_path / "execution.lock"
    log = tmp_path / "native.log"
    fixture = Path(__file__).with_name("fixture_linux_native_owner.sh")
    successor = Path(__file__).with_name("fixture_linux_inherited_owner.py")
    with log.open("w") as output:
        owner = subprocess.Popen(
            ["bash", str(fixture), str(SOURCE), str(lock),
             "handoff" if mode == "handoff" else "native", sys.executable, str(successor)],
            stdin=subprocess.PIPE, stdout=output, stderr=output, text=True,
            env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)},
        )
        try:
            await_file(tmp_path / "native-ready", owner, log)
            identity = lock.stat()
            with pytest.raises(OperationLockedError):
                OperationLock(lock).acquire()
            if mode == "handoff":
                await_file(tmp_path / "python-ready", owner, log)
                assert os.path.samestat(identity, lock.stat())
                with pytest.raises(OperationLockedError):
                    OperationLock(lock).acquire()
            if mode == "death":
                owner.kill()
                owner.wait(timeout=5)
            else:
                owner.communicate("release\n", timeout=10)
                assert owner.returncode == 0, log.read_text()
            with OperationLock(lock) as next_owner:
                next_owner.assert_owned()
        finally:
            if owner.poll() is None:
                owner.kill()
            owner.wait(timeout=5)
            owner.stdin.close()


@pytest.mark.parametrize("kind", ["unlocked", "shared", "wrong-path", "closed"])
def test_inherited_admission_requires_actual_kernel_custody(tmp_path, kind):
    import fcntl

    lock = tmp_path / "execution.lock"
    lock.touch()
    fd = os.open(lock, os.O_RDWR)
    try:
        if kind in {"shared", "wrong-path"}:
            fcntl.flock(fd, fcntl.LOCK_SH if kind == "shared" else fcntl.LOCK_EX)
        path = lock
        if kind == "wrong-path":
            path = tmp_path / "other.lock"
            path.touch()
        if kind == "closed":
            os.close(fd)
        with pytest.raises((OperationOwnershipError, OSError)):
            OperationLock.from_inherited(path, fd)
        if kind != "closed":
            assert os.fstat(fd).st_ino == lock.stat().st_ino
    finally:
        if kind != "closed":
            os.close(fd)


def test_inherited_owner_rechecks_kernel_custody_identity_thread_and_fork(tmp_path):
    import fcntl

    lock = tmp_path / "execution.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    owned = OperationLock.from_inherited(lock, fd)
    try:
        errors = []

        def foreign_thread():
            try:
                owned.assert_owned()
            except OperationOwnershipError as error:
                errors.append(error)

        thread = threading.Thread(target=foreign_thread)
        thread.start()
        thread.join(timeout=5)
        assert len(errors) == 1
        child = os.fork()
        if child == 0:
            try:
                try:
                    owned.assert_owned()
                except OperationOwnershipError:
                    pass
                else:
                    os._exit(2)
                try:
                    OperationLock(lock).acquire()
                except OperationLockedError:
                    os._exit(0)
                os._exit(3)
            except BaseException:
                os._exit(4)
        _pid, code = os.waitpid(child, 0)
        assert code == 0
        owned.assert_owned()
        fcntl.flock(owned._file.fileno(), fcntl.LOCK_UN)
        with pytest.raises(OperationOwnershipError, match="exclusive whole-file"):
            owned.assert_owned()
    finally:
        owned.release()
    with OperationLock(lock):
        pass


def test_inherited_owner_refuses_replaced_lock_file(tmp_path):
    import fcntl

    lock = tmp_path / "execution.lock"
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    owned = OperationLock.from_inherited(lock, fd)
    try:
        lock.rename(tmp_path / "old.lock")
        lock.touch()
        with pytest.raises(OperationOwnershipError, match="replaced"):
            owned.assert_owned()
    finally:
        owned.release()


def test_inherited_directory_refusal_does_not_leak_a_duplicated_lock(tmp_path):
    import fcntl

    directory = tmp_path / "directory"
    directory.mkdir()
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        before = len(os.listdir("/proc/self/fd"))
        with pytest.raises(IsADirectoryError):
            OperationLock.from_inherited(directory, fd)
        assert len(os.listdir("/proc/self/fd")) == before
        assert os.fstat(fd).st_ino == directory.stat().st_ino
    finally:
        os.close(fd)
    probe = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(probe)


@pytest.mark.parametrize("mode", ["cleanup", "foreign"])
def test_native_cleanup_closes_only_its_owned_reference_after_path_invalidation(tmp_path, mode):
    lock = tmp_path / "execution.lock"
    fixture = Path(__file__).with_name("fixture_linux_native_owner.sh")
    result = subprocess.run(
        ["bash", str(fixture), str(SOURCE), str(lock), mode],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    for path in (lock, lock.with_name(lock.name + ".old")):
        if path.exists():
            with OperationLock(path) as owned:
                owned.assert_owned()
