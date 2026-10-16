"""Real execution custody, including death, threads and inherited references."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from agent_procutil import direct_python, no_window_kwargs

from single_instance_lease import OperationLock, OperationLockedError, OperationOwnershipError


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
        with pytest.raises(OperationOwnershipError, match="already acquired"):
            custody.acquire()
    custody.release()
    with custody:
        custody.assert_owned()


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
