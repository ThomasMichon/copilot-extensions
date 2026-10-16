"""Native custody of an explicitly owned direct child, without lifecycle policy."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from pathlib import Path

from .diagnostics import process_start_time
from .private_protocol import Deadline, PeerIdentity, PrivateIPCError, Refusal
from .private_peer import _pidfd_exited
from .private_windows_native import Native

# Linux 5.4 waitid idtype; some Python builds expose pidfd_open but omit it.
_P_PIDFD = 3


class OwnedChild:
    """Pin a sole-owner, unreaped Popen before granting private transfer.

    The caller must retain exclusive Popen ownership through acquisition; do
    not concurrently poll, wait, communicate or transfer it. Linux additionally
    proves direct-child custody through pidfd waitid without reaping. Windows
    duplicates the Popen's original creation handle, never OpenProcess(pid).
    This object does not spawn, kill, reap, detach or grant adoption authority.
    """

    def __init__(
        self, process: subprocess.Popen, executable: Path, deadline: Deadline,
    ) -> None:
        self._reference = -1
        self._native: Native | None = None
        self._owner_pid = os.getpid()
        self._thread = threading.current_thread()
        self._closed = False
        try:
            deadline.remaining()
            if (
                type(process) is not subprocess.Popen
                or process.returncode is not None
                or type(process.pid) is not int or process.pid <= 0
                or not isinstance(executable, Path) or not executable.is_absolute()
            ):
                raise PrivateIPCError(Refusal.IDENTITY)
            self.executable = executable.resolve(strict=True)
            self._pid = process.pid
            if sys.platform == "linux":
                from .singleton_linux import _open_pidfd

                self._reference = _open_pidfd(process.pid)
                os.set_inheritable(self._reference, False)
                self._linux_alive(deadline)
                self.peer = self._linux_identity()
            elif os.name == "nt":
                self._native = Native()
                self._reference = self._native.duplicate_process(int(process._handle), process.pid)
                self.peer = self._native.identity(self._reference, process.pid)
                if self.peer.principal != self._native.current_sid():
                    raise PrivateIPCError(Refusal.PRINCIPAL)
            else:
                raise PrivateIPCError(Refusal.UNAVAILABLE)
            self.check(deadline)
        except PrivateIPCError:
            self.close()
            raise
        except NotImplementedError:
            self.close()
            raise PrivateIPCError(Refusal.UNAVAILABLE) from None
        except (OSError, ValueError, AttributeError):
            self.close()
            raise PrivateIPCError(Refusal.IDENTITY) from None

    def _linux_alive(self, deadline: Deadline) -> None:
        deadline.remaining()
        if _pidfd_exited(self._reference, 0):
            raise PrivateIPCError(Refusal.IDENTITY)
        # ECHILD refuses a PID that no longer belongs to this process. WNOWAIT
        # preserves Popen's exclusive ownership of eventual exit/reaping.
        exited = os.waitid(
            _P_PIDFD, self._reference, os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
        if exited is not None:
            raise PrivateIPCError(Refusal.IDENTITY)

    def _linux_identity(self) -> PeerIdentity:
        token = process_start_time(self._pid)
        status = Path(f"/proc/{self._pid}/status").read_text()
        uid_rows = [row.split()[1:] for row in status.splitlines() if row.startswith("Uid:")]
        if len(uid_rows) != 1 or len(uid_rows[0]) != 4:
            raise PrivateIPCError(Refusal.PRINCIPAL)
        if any(uid != str(os.geteuid()) for uid in uid_rows[0]):
            raise PrivateIPCError(Refusal.PRINCIPAL)
        if token is None:
            raise PrivateIPCError(Refusal.IDENTITY)
        return PeerIdentity(self._pid, token, str(os.geteuid()))

    def check(self, deadline: Deadline) -> None:
        """Recheck held birth, principal and actual executable, without signalling."""
        deadline.remaining()
        if (
            self._closed or self._reference < 0
            or os.getpid() != self._owner_pid
            or threading.current_thread() is not self._thread
        ):
            raise PrivateIPCError(Refusal.IDENTITY)
        try:
            if self._native is None:
                self._linux_alive(deadline)
                identity = self._linux_identity()
                image = Path(os.readlink(f"/proc/{self._pid}/exe")).resolve(strict=True)
                self._linux_alive(deadline)
            else:
                self._native.alive(self._reference)
                identity = self._native.identity(self._reference, self._pid)
                image = Path(self._native.process_image(self._reference)).resolve(strict=True)
                self._native.alive(self._reference)
            if identity != self.peer or image != self.executable:
                raise PrivateIPCError(Refusal.IDENTITY)
            deadline.remaining()
        except (OSError, ValueError, AttributeError):
            raise PrivateIPCError(Refusal.IDENTITY) from None

    def close(self) -> None:
        """Release only owned native references; child lifetime remains unchanged."""
        if self._closed:
            return
        self._closed = True
        if self._reference >= 0:
            if self._native is None:
                os.close(self._reference)
            else:
                self._native.close(self._reference)
            self._reference = -1
