"""Adopt an actual unreaped Linux session child after a native parent's exec."""

from __future__ import annotations

import os
import subprocess
import sys
import threading

from ._caller_owned import _confirm_group, _pidfd_pid
from ._linux_pidfd import open_pidfd
from .owned_process import _observe_unreaped, _retire_posix_group

_registry_lock = threading.Lock()
_adopted: dict[int, NativeChildCustody] = {}


def _after_fork_child() -> None:
    try:
        for custody in _adopted.values():
            os.close(custody._descriptor)
            custody._descriptor = -1
        _adopted.clear()
    finally:
        _registry_lock.release()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(
        before=_registry_lock.acquire, after_in_parent=_registry_lock.release,
        after_in_child=_after_fork_child,
    )


class NativeChildCleanupError(subprocess.SubprocessError):
    """Settlement is unconfirmed; retain custody rather than reporting an outcome."""

    def __init__(self, custody: NativeChildCustody) -> None:
        self.custody = custody
        super().__init__("native child group settlement is unconfirmed")


class NativeChildCustody:
    """Own a direct child, not a persisted PID or a reconstructed Popen object."""

    def __init__(self, pid: int) -> None:
        if sys.platform != "linux":
            raise RuntimeError("native child adoption requires Linux kernel custody")
        if type(pid) is not int or pid <= 0:
            raise ValueError("native child identity must be a positive integer")
        self._pid = pid
        self._returncode: int | None = None
        self._owner = os.getpid()
        self._thread = threading.current_thread()
        with _registry_lock:
            if pid in _adopted:
                raise RuntimeError("native child custody is already adopted")
            self._descriptor = open_pidfd(pid)
            try:
                self.assert_owned()
            except BaseException:
                os.close(self._descriptor)
                self._descriptor = -1
                raise
            _adopted[pid] = self

    @property
    def pid(self) -> int:
        return self._pid

    @property
    def returncode(self) -> int | None:
        return self._returncode

    def _assert_owner(self) -> None:
        if os.getpid() != self._owner or threading.current_thread() is not self._thread:
            raise RuntimeError("native child custody belongs to its adopting process and thread")

    def assert_owned(self) -> None:
        self._assert_owner()
        if self._returncode is not None or self._descriptor < 0:
            raise RuntimeError("native child custody has already been released")
        if _pidfd_pid(self._descriptor) != self._pid:
            raise RuntimeError("native child kernel identity is unavailable")
        _observe_unreaped(self)
        if os.getpgid(self._pid) != self._pid or os.getsid(self._pid) != self._pid:
            raise RuntimeError("native child must own its original session and process group")
        _observe_unreaped(self)

    def retire(self) -> int:
        """Confirm the whole pinned group is dead before reaping its original child."""
        self._assert_owner()
        if self._returncode is not None:
            return self._returncode
        try:
            self.assert_owned()
            _retire_posix_group(self)
            _confirm_group(self)
            self.assert_owned()
            child, status = os.waitpid(self._pid, os.WNOHANG)
            if child != self._pid:
                raise RuntimeError("native child exit is unconfirmed")
            self._returncode = os.waitstatus_to_exitcode(status)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            raise NativeChildCleanupError(self) from error
        with _registry_lock:
            os.close(self._descriptor)
            self._descriptor = -1
            del _adopted[self._pid]
        return self._returncode
