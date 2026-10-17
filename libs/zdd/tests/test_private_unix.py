"""Real Linux peer custody and bounded I/O, without a network listener."""

from __future__ import annotations

import multiprocessing
from multiprocessing.reduction import recv_handle, send_handle
import os
import socket
import struct
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Iterator

import pytest

from zdd.diagnostics import process_start_time
from zdd.private_protocol import (
    Deadline,
    PeerIdentity,
    PrivateIPCError,
    Refusal,
    read_frame,
    write_frame,
)
from zdd.private_unix import UnixChannel, UnixListener, _poll_ready, connect

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux native peer custody")


def _identity() -> PeerIdentity:
    token = process_start_time(os.getpid())
    assert token is not None
    return PeerIdentity(os.getpid(), token, str(os.geteuid()))


def _server(control: Connection, path: str, mode: str, expected: PeerIdentity) -> None:
    listener = UnixListener(Path(path))
    channel = None
    try:
        control.send(_identity())
        channel = listener.accept(Deadline(3), expected=expected)
        control.send(channel.peer)
        if mode == "echo":
            value = read_frame(channel, Deadline(2))
            write_frame(channel, value, Deadline(2))
        elif mode == "partial":
            channel.write_all(struct.pack("!BI", 1, 8) + b"{", Deadline(1))
        elif mode == "transfer":
            send_handle(control, channel._socket.fileno(), expected.pid)
        if control.poll(3):
            control.recv()
    except PrivateIPCError as error:
        control.send(error.reason.value)
    finally:
        if channel is not None:
            channel.close()
        listener.close()
        control.close()


@contextmanager
def _process(path: Path, mode: str, *, expected: PeerIdentity | None = None) -> Iterator[
    tuple[Connection, PeerIdentity]
]:
    parent, child = multiprocessing.get_context("spawn").Pipe()
    process = multiprocessing.get_context("spawn").Process(
        target=_server, args=(child, str(path), mode, expected or _identity()),
    )
    process.start()
    child.close()
    try:
        assert parent.poll(5), "bounded server startup"
        identity = parent.recv()
        assert isinstance(identity, PeerIdentity)
        yield parent, identity
    finally:
        if process.is_alive():
            try:
                parent.send("finish")
            except (BrokenPipeError, ConnectionResetError):
                # A refusing peer closes control before its exit is observable.
                pass
        process.join(5)
        parent.close()
        assert not process.is_alive(), "owned fixture failed its bounded exit"
        assert process.exitcode == 0


def test_real_process_framing_and_both_peer_identities(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    path = tmp_path / "capture.sock"
    with _process(path, "echo") as (control, expected):
        channel = connect(path, expected, Deadline(1))
        try:
            assert channel.peer == expected
            assert control.poll(1)
            assert control.recv() == _identity()
            value = {"config": {"memory_override": 17}, "sensitive": "fixture-only"}
            deadline = Deadline(1)
            write_frame(channel, value, deadline)
            assert read_frame(channel, deadline) == value
        finally:
            channel.close()
    assert not path.exists()
    assert sorted(item.name for item in tmp_path.iterdir()) == []


def test_closed_socket_poll_refuses_explicitly() -> None:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.close()
    with pytest.raises(PrivateIPCError) as caught:
        _poll_ready(connection, write=False, deadline=Deadline(1))
    assert caught.value.reason is Refusal.CLOSED


def test_native_pollnval_refuses_as_closed_descriptor() -> None:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    os.close(connection.fileno())
    try:
        with pytest.raises(PrivateIPCError) as caught:
            _poll_ready(connection, write=False, deadline=Deadline(1))
        assert caught.value.reason is Refusal.CLOSED
    finally:
        connection.detach()


@pytest.mark.parametrize("high_descriptor", ["socket", "pidfd", "both"])
def test_real_channel_io_and_peer_exit_above_fd_setsize(
    tmp_path: Path, high_descriptor: str,
) -> None:
    import fcntl
    import resource

    if resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= 1200:
        pytest.skip("test needs an existing descriptor limit above 1200")
    tmp_path.chmod(0o700)
    with _process(tmp_path / "capture.sock", "echo") as (control, expected):
        channel = connect(tmp_path / "capture.sock", expected, Deadline(1))
        reference = None
        try:
            assert control.poll(1)
            assert control.recv() == _identity()
            if high_descriptor in ("socket", "both"):
                original = channel._socket
                channel._socket = socket.socket(
                    fileno=fcntl.fcntl(original.fileno(), fcntl.F_DUPFD_CLOEXEC, 1100),
                )
                original.close()
                channel._socket.setblocking(False)
                assert channel._socket.fileno() >= 1100
            if high_descriptor in ("pidfd", "both"):
                original_pidfd = channel._pidfd
                channel._pidfd = fcntl.fcntl(original_pidfd, fcntl.F_DUPFD_CLOEXEC, 1200)
                os.close(original_pidfd)
                assert channel._pidfd >= 1200
            reference = channel.retain_peer(Deadline(1))
            value = {"count": 17}
            write_frame(channel, value, Deadline(1))
            assert read_frame(channel, Deadline(1)) == value
            control.send("finish")
            reference.wait_exit(Deadline(2))
            with pytest.raises(PrivateIPCError) as caught:
                channel.read_exact(1, Deadline(1))
            assert caught.value.reason is Refusal.IDENTITY
        finally:
            if reference is not None:
                reference.close()
            channel.close()


@pytest.mark.parametrize("field", ["pid", "start_time", "principal"])
def test_wrong_server_native_identity_refused(tmp_path: Path, field: str) -> None:
    tmp_path.chmod(0o700)
    with _process(tmp_path / "capture.sock", "idle") as (_control, expected):
        changes = {"pid": expected.pid + 1, "start_time": "0", "principal": "foreign"}
        with pytest.raises(PrivateIPCError) as caught:
            connect(tmp_path / "capture.sock", replace(expected, **{field: changes[field]}), Deadline(1))
        assert caught.value.reason is Refusal.IDENTITY


def test_wrong_client_start_refused_before_request(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    with _process(
        tmp_path / "capture.sock", "idle", expected=replace(_identity(), start_time="0"),
    ) as (control, expected):
        channel = connect(tmp_path / "capture.sock", expected, Deadline(1))
        try:
            assert control.poll(1)
            assert control.recv() == Refusal.IDENTITY.value
        finally:
            channel.close()


def test_partial_read_uses_original_deadline(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    with _process(tmp_path / "capture.sock", "partial") as (control, expected):
        channel = connect(tmp_path / "capture.sock", expected, Deadline(1))
        try:
            assert control.poll(1)
            control.recv()
            started = time.monotonic()
            with pytest.raises(PrivateIPCError) as caught:
                read_frame(channel, Deadline(0.15))
            assert caught.value.reason is Refusal.DEADLINE
            assert time.monotonic() - started < 0.6
        finally:
            channel.close()


def test_blocked_writer_and_cancel_are_bounded(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    with _process(tmp_path / "capture.sock", "idle") as (control, expected):
        channel = connect(tmp_path / "capture.sock", expected, Deadline(1))
        try:
            assert control.poll(1)
            control.recv()
            channel._socket.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 4096)
            started = time.monotonic()
            with pytest.raises(PrivateIPCError) as caught:
                channel.write_all(b"x" * (1024 * 1024), Deadline(0.1))
            assert caught.value.reason is Refusal.DEADLINE
            assert time.monotonic() - started < 0.6
            cancelled = threading.Event()
            cancelled.set()
            with pytest.raises(PrivateIPCError) as caught:
                channel.read_exact(1, Deadline(1, cancelled=cancelled))
            assert caught.value.reason is Refusal.CANCELLED
        finally:
            channel.close()


def test_unknown_peer_pidfd_capability_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    left, right = socket.socketpair()
    try:
        monkeypatch.setattr("zdd.private_unix._SO_PEERPIDFD", 65535)
        with pytest.raises(PrivateIPCError) as caught:
            UnixChannel(left, Deadline(1))
        assert caught.value.reason is Refusal.UNAVAILABLE
        assert left.fileno() == -1
    finally:
        right.close()


def test_dead_peer_refuses_even_if_socket_descriptor_survives(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    with _process(tmp_path / "capture.sock", "transfer") as (control, expected):
        channel = connect(tmp_path / "capture.sock", expected, Deadline(1))
        assert control.poll(1)
        control.recv()
        borrowed = socket.socket(fileno=recv_handle(control))
    try:
        assert borrowed.fileno() >= 0
        with pytest.raises(PrivateIPCError) as caught:
            channel.read_exact(1, Deadline(0.1))
        assert caught.value.reason is Refusal.IDENTITY
    finally:
        channel.close()
        borrowed.close()


def test_foreign_principal_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    left, right = socket.socketpair()
    try:
        monkeypatch.setattr("zdd.private_unix.os.geteuid", lambda: os.getuid() + 1)
        with pytest.raises(PrivateIPCError) as caught:
            UnixChannel(left, Deadline(1))
        assert caught.value.reason is Refusal.PRINCIPAL
    finally:
        right.close()


def test_retained_original_peer_exit_survives_channel_close_and_reaping(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    reference = None
    try:
        with _process(tmp_path / "capture.sock", "idle") as (control, expected):
            channel = connect(tmp_path / "capture.sock", expected, Deadline(1))
            try:
                assert control.poll(1)
                control.recv()
                reference = channel.retain_peer(Deadline(1))
                assert reference.peer == expected
                assert os.get_inheritable(reference._reference) is False
            finally:
                channel.close()
            assert reference.exited(Deadline(1)) is False
            with pytest.raises(PrivateIPCError) as caught:
                reference.wait_exit(Deadline(0.03))
            assert caught.value.reason is Refusal.DEADLINE
        reference.wait_exit(Deadline(1))
        assert reference.exited(Deadline(1)) is True
    finally:
        if reference is not None:
            reference.close()


def test_cannot_acquire_peer_exit_custody_after_exit(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    with _process(tmp_path / "capture.sock", "idle") as (control, expected):
        channel = connect(tmp_path / "capture.sock", expected, Deadline(1))
        assert control.poll(1)
        control.recv()
    try:
        with pytest.raises(PrivateIPCError) as caught:
            channel.retain_peer(Deadline(1))
        assert caught.value.reason is Refusal.IDENTITY
    finally:
        channel.close()


def test_listener_never_replaces_occupied_endpoint(tmp_path: Path) -> None:
    tmp_path.chmod(0o700)
    path = tmp_path / "capture.sock"
    first = UnixListener(path)
    try:
        inode = path.stat().st_ino
        with pytest.raises(PrivateIPCError) as caught:
            UnixListener(path)
        assert caught.value.reason is Refusal.UNAVAILABLE
        assert path.stat().st_ino == inode
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        first.close()


def test_listener_rejects_public_directory(tmp_path: Path) -> None:
    tmp_path.chmod(0o755)
    with pytest.raises(PrivateIPCError) as caught:
        UnixListener(tmp_path / "capture.sock")
    assert caught.value.reason is Refusal.PRINCIPAL
    assert not (tmp_path / "capture.sock").exists()
