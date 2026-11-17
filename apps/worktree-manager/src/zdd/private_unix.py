"""Linux private channels with socket-derived native peer process custody."""

from __future__ import annotations

import errno
import os
import select
import socket
import stat
import struct
import sys
import threading
from pathlib import Path

from .diagnostics import process_start_time
from .private_peer import PeerProcess, _pidfd_exited
from .private_protocol import Deadline, PeerIdentity, PrivateIPCError, Refusal, deadline_lock

# Linux 6.5 added SO_PEERPIDFD. Numeric PID sampling cannot replace it:
# an accepted socket may outlive its original peer after descriptor transfer.
_SO_PEERPIDFD = 77
_CREDENTIALS = struct.Struct("3i")


def _poll_ready(
    connection: socket.socket, *, write: bool, deadline: Deadline, peer: int = -1,
) -> bool:
    if connection.fileno() < 0:
        raise PrivateIPCError(Refusal.CLOSED)
    poller = select.poll()
    poller.register(connection.fileno(), select.POLLOUT if write else select.POLLIN)
    if peer >= 0:
        poller.register(peer, select.POLLIN)
    events = poller.poll(max(1, int(deadline.wait_slice() * 1000)))
    if any(descriptor == peer for descriptor, _flags in events):
        raise PrivateIPCError(Refusal.IDENTITY)
    if any(flags & select.POLLNVAL for _descriptor, flags in events):
        raise PrivateIPCError(Refusal.CLOSED)
    return bool(events)


class UnixChannel:
    """Owned connected socket plus its authenticated peer's original pidfd."""

    def __init__(
        self, connection: socket.socket, deadline: Deadline,
        *, expected: PeerIdentity | None = None,
    ) -> None:
        self._socket = connection
        self._pidfd = -1
        self._closed = False
        self._lock = threading.RLock()
        try:
            if (
                sys.platform != "linux" or connection.family != socket.AF_UNIX
                or connection.getsockopt(socket.SOL_SOCKET, socket.SO_TYPE) != socket.SOCK_STREAM
            ):
                raise PrivateIPCError(Refusal.UNAVAILABLE)
            connection.setblocking(False)
            pid, uid, _gid = _CREDENTIALS.unpack(
                connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, _CREDENTIALS.size),
            )
            if uid != os.geteuid():
                raise PrivateIPCError(Refusal.PRINCIPAL)
            self._pidfd = connection.getsockopt(socket.SOL_SOCKET, _SO_PEERPIDFD)
            os.set_inheritable(self._pidfd, False)
            self._check_alive(deadline)
            token = process_start_time(pid)
            if token is None:
                raise PrivateIPCError(Refusal.IDENTITY)
            self.peer = PeerIdentity(pid, token, str(uid))
            self._check_alive(deadline)
            if expected is not None and self.peer != expected:
                raise PrivateIPCError(Refusal.IDENTITY)
        except PrivateIPCError:
            self.close()
            raise
        except OSError:
            self.close()
            raise PrivateIPCError(Refusal.UNAVAILABLE) from None

    def _check_alive(self, deadline: Deadline) -> None:
        deadline.remaining()
        if self._closed or self._pidfd < 0:
            raise PrivateIPCError(Refusal.CLOSED)
        if _pidfd_exited(self._pidfd, 0):
            raise PrivateIPCError(Refusal.IDENTITY)

    def _wait(self, *, write: bool, deadline: Deadline) -> None:
        while True:
            self._check_alive(deadline)
            if _poll_ready(
                self._socket, write=write, deadline=deadline, peer=self._pidfd,
            ):
                self._check_alive(deadline)
                return

    def read_exact(self, count: int, deadline: Deadline) -> bytes:
        """Read exactly count bytes within the original request budget."""
        if type(count) is not int or count < 0:
            raise PrivateIPCError(Refusal.SIZE)
        with deadline_lock(self._lock, deadline):
            result = bytearray()
            try:
                while len(result) < count:
                    self._wait(write=False, deadline=deadline)
                    try:
                        chunk = self._socket.recv(count - len(result))
                    except (BlockingIOError, InterruptedError):
                        continue
                    if not chunk:
                        raise PrivateIPCError(Refusal.CLOSED)
                    result.extend(chunk)
                self._check_alive(deadline)
                return bytes(result)
            except PrivateIPCError:
                self.close()
                raise
            except OSError:
                self.close()
                raise PrivateIPCError(Refusal.IO) from None

    def write_all(self, data: bytes, deadline: Deadline) -> None:
        """Write all bytes without unbounded sendall or a fresh phase timeout."""
        if not isinstance(data, bytes):
            raise PrivateIPCError(Refusal.PROTOCOL)
        with deadline_lock(self._lock, deadline):
            remaining = memoryview(data)
            try:
                while remaining:
                    self._wait(write=True, deadline=deadline)
                    try:
                        written = self._socket.send(remaining)
                    except (BlockingIOError, InterruptedError):
                        continue
                    if not written:
                        raise PrivateIPCError(Refusal.CLOSED)
                    remaining = remaining[written:]
                self._check_alive(deadline)
            except PrivateIPCError:
                self.close()
                raise
            except OSError:
                self.close()
                raise PrivateIPCError(Refusal.IO) from None

    def check_peer(self, deadline: Deadline) -> None:
        """Recheck original native peer custody immediately before a control."""
        with deadline_lock(self._lock, deadline):
            self._check_alive(deadline)

    def retain_peer(self, deadline: Deadline) -> PeerProcess:
        """Duplicate the authenticated original pidfd before the peer exits."""
        with deadline_lock(self._lock, deadline):
            self._check_alive(deadline)
            reference = -1
            try:
                reference = os.dup(self._pidfd)
                os.set_inheritable(reference, False)
                self._check_alive(deadline)
                return PeerProcess(reference, self.peer)
            except BaseException as error:
                if reference >= 0:
                    os.close(reference)
                if isinstance(error, OSError):
                    raise PrivateIPCError(Refusal.IDENTITY) from None
                raise

    def close(self) -> None:
        """Release only this channel's descriptors, never signal the peer."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            try:
                self._socket.close()
            finally:
                if self._pidfd >= 0:
                    os.close(self._pidfd)
                    self._pidfd = -1


def connect(path: Path, expected: PeerIdentity, deadline: Deadline) -> UnixChannel:
    """Connect and authenticate the exact serving PID/start/principal."""
    if not isinstance(expected, PeerIdentity):
        raise PrivateIPCError(Refusal.IDENTITY)
    if sys.platform != "linux":
        raise PrivateIPCError(Refusal.UNAVAILABLE)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.setblocking(False)
    try:
        deadline.remaining()
        result = connection.connect_ex(str(path))
        if result not in (0, errno.EINPROGRESS, errno.EAGAIN, errno.EWOULDBLOCK):
            raise PrivateIPCError(Refusal.IO)
        while result:
            if _poll_ready(connection, write=True, deadline=deadline):
                result = connection.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
                if result:
                    raise PrivateIPCError(Refusal.IO)
                break
        return UnixChannel(connection, deadline, expected=expected)
    except PrivateIPCError:
        connection.close()
        raise
    except OSError:
        connection.close()
        raise PrivateIPCError(Refusal.IO) from None


class UnixListener:
    """Generation-owned socket; never replace an existing endpoint on bind."""

    def __init__(self, path: Path) -> None:
        if sys.platform != "linux":
            raise PrivateIPCError(Refusal.UNAVAILABLE)
        self.path = path
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._closed = False
        self._inode: tuple[int, int] | None = None
        try:
            parent = path.parent.lstat()
            if (
                not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
                or stat.S_IMODE(parent.st_mode) & 0o077
            ):
                raise PrivateIPCError(Refusal.PRINCIPAL)
            self._socket.bind(str(path))
            info = path.lstat()
            self._inode = (info.st_dev, info.st_ino)
            path.chmod(0o600)
            self._socket.listen(8)
            self._socket.setblocking(False)
        except PrivateIPCError:
            self.close()
            raise
        except OSError as exc:
            self.close()
            reason = Refusal.UNAVAILABLE if exc.errno == errno.EADDRINUSE else Refusal.IO
            raise PrivateIPCError(reason) from None

    def accept(
        self, deadline: Deadline, *, expected: PeerIdentity | None = None,
    ) -> UnixChannel:
        """Accept only the local principal, optionally one exact native peer."""
        if self._closed:
            raise PrivateIPCError(Refusal.CLOSED)
        try:
            while True:
                if _poll_ready(self._socket, write=False, deadline=deadline):
                    try:
                        connection, _address = self._socket.accept()
                    except (BlockingIOError, InterruptedError):
                        continue
                    return UnixChannel(connection, deadline, expected=expected)
        except OSError:
            raise PrivateIPCError(Refusal.IO) from None

    def close(self) -> None:
        """Remove only the socket inode created by this listener."""
        if self._closed:
            return
        self._closed = True
        self._socket.close()
        if self._inode is not None:
            try:
                info = self.path.lstat()
            except FileNotFoundError:
                return
            if stat.S_ISSOCK(info.st_mode) and (info.st_dev, info.st_ino) == self._inode:
                self.path.unlink()
