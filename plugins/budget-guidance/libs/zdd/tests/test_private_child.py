"""Real owned process references, with cooperative fixture exit only."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pytest

from zdd.private_child import OwnedChild
from zdd.private_protocol import Deadline, PrivateIPCError, Refusal

pytestmark = pytest.mark.skipif(
    sys.platform not in ("linux", "win32"), reason="native child custody",
)


@contextmanager
def child() -> Iterator[subprocess.Popen]:
    process = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.buffer.read(1)"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        close_fds=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        yield process
    finally:
        finish = b"x" if process.stdin is not None and not process.stdin.closed else None
        _out, error = process.communicate(finish, timeout=5)
        assert process.returncode == 0, error


def test_real_child_birth_principal_and_executable() -> None:
    with child() as process:
        custody = OwnedChild(process, Path(sys.executable).resolve(), Deadline(2))
        try:
            assert custody.peer.pid == process.pid
            assert custody.peer.start_time.isdigit()
            assert custody.peer.principal
            assert custody.executable == Path(sys.executable).resolve()
            custody.check(Deadline(1))
            assert process.returncode is None
            if os.name != "nt":
                assert not os.get_inheritable(custody._reference)
        finally:
            custody.close()
        custody.close()
        # Releasing custody is not termination or reaping.
        assert process.poll() is None


def test_wrong_selected_executable_refuses_without_killing(tmp_path: Path) -> None:
    other = tmp_path / "not-the-child-image"
    other.write_bytes(b"synthetic")
    with child() as process:
        with pytest.raises(PrivateIPCError) as caught:
            OwnedChild(process, other, Deadline(1))
        assert caught.value.reason is Refusal.IDENTITY
        assert process.poll() is None


def test_expired_deadline_refuses_before_acquisition() -> None:
    with child() as process:
        cancelled = threading.Event()
        cancelled.set()
        with pytest.raises(PrivateIPCError) as caught:
            OwnedChild(process, Path(sys.executable).resolve(), Deadline(1, cancelled=cancelled))
        assert caught.value.reason is Refusal.CANCELLED
        assert process.poll() is None


def test_exit_and_closed_custody_refuse() -> None:
    with child() as process:
        custody = OwnedChild(process, Path(sys.executable).resolve(), Deadline(1))
        try:
            process.communicate(b"x", timeout=5)
            with pytest.raises(PrivateIPCError) as caught:
                custody.check(Deadline(1))
            assert caught.value.reason is Refusal.IDENTITY
        finally:
            custody.close()
        with pytest.raises(PrivateIPCError) as caught:
            custody.check(Deadline(1))
        assert caught.value.reason is Refusal.IDENTITY


def test_already_reaped_child_cannot_acquire() -> None:
    with child() as process:
        process.communicate(b"x", timeout=5)
        with pytest.raises(PrivateIPCError) as caught:
            OwnedChild(process, Path(sys.executable).resolve(), Deadline(1))
        assert caught.value.reason is Refusal.IDENTITY


def test_other_thread_cannot_use_owner_custody() -> None:
    with child() as process:
        custody = OwnedChild(process, Path(sys.executable).resolve(), Deadline(1))
        reasons = []

        def attempt() -> None:
            try:
                custody.check(Deadline(1))
            except PrivateIPCError as error:
                reasons.append(error.reason)

        try:
            worker = threading.Thread(target=attempt)
            worker.start()
            worker.join(2)
            assert not worker.is_alive()
            assert reasons == [Refusal.IDENTITY]
            custody.check(Deadline(1))
        finally:
            custody.close()


@pytest.mark.skipif(sys.platform != "linux", reason="Linux direct-child proof")
def test_live_nonchild_reference_refuses_even_with_correct_numeric_pid() -> None:
    with child() as process:
        # A forged numeric PID cannot turn the parent itself into a direct child.
        original = process.pid
        process.pid = os.getpid()
        try:
            with pytest.raises(PrivateIPCError) as caught:
                OwnedChild(process, Path(sys.executable).resolve(), Deadline(1))
            assert caught.value.reason is Refusal.IDENTITY
        finally:
            process.pid = original


@pytest.mark.skipif(sys.platform != "linux", reason="Linux high-numbered pidfds")
def test_high_numbered_pidfds_preserve_acquisition_and_exact_exit(monkeypatch) -> None:
    import fcntl
    import resource
    from zdd import singleton_linux
    from zdd.private_peer import PeerProcess

    if resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= 1200:
        pytest.skip("test needs an existing descriptor limit above 1200")
    original_open = singleton_linux._open_pidfd

    def open_high(pid):
        original = original_open(pid)
        try:
            return fcntl.fcntl(original, fcntl.F_DUPFD_CLOEXEC, 1100)
        finally:
            os.close(original)

    monkeypatch.setattr(singleton_linux, "_open_pidfd", open_high)
    with child() as process:
        custody = OwnedChild(process, Path(sys.executable).resolve(), Deadline(2))
        peer = None
        try:
            assert custody._reference >= 1100
            custody.check(Deadline(1))
            descriptor = fcntl.fcntl(custody._reference, fcntl.F_DUPFD_CLOEXEC, 1200)
            peer = PeerProcess(descriptor, custody.peer)
            assert peer.exited(Deadline(1)) is False
            with pytest.raises(PrivateIPCError) as caught:
                peer.wait_exit(Deadline(0.01))
            assert caught.value.reason is Refusal.DEADLINE
            process.communicate(b"x", timeout=5)
            assert peer.exited(Deadline(1)) is True
            peer.wait_exit(Deadline(1))
            with pytest.raises(PrivateIPCError) as caught:
                custody.check(Deadline(1))
            assert caught.value.reason is Refusal.IDENTITY
        finally:
            if peer is not None:
                peer.close()
            custody.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows creation-handle proof")
def test_windows_never_reopens_numeric_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    with child() as process:
        def forbidden(*args, **kwargs):
            raise AssertionError("native custody reopened a numeric PID")

        monkeypatch.setattr("zdd.private_windows_native.Native.pin_peer", forbidden)
        custody = OwnedChild(process, Path(sys.executable).resolve(), Deadline(1))
        try:
            assert custody._reference != int(process._handle)
            custody.check(Deadline(1))
        finally:
            custody.close()


@pytest.mark.skipif(os.name != "nt", reason="Windows creation-handle proof")
def test_windows_creation_handle_pid_disagreement_refuses() -> None:
    with child() as process:
        original = process.pid
        process.pid += 1
        try:
            with pytest.raises(PrivateIPCError) as caught:
                OwnedChild(process, Path(sys.executable).resolve(), Deadline(1))
            assert caught.value.reason is Refusal.IDENTITY
        finally:
            process.pid = original
