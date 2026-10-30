"""Retained native custody for the original PowerShell pipe server."""

from __future__ import annotations

import ctypes
import os
from contextlib import contextmanager
from ctypes import wintypes
from typing import Iterator

from . import JobHandle, _build_job_extended_limit_info, _is_windows, _job_scopes
from ._daemon_instance_identity import _windows_birth
from ._daemon_instance_records import ProcessIdentity
from ._windows_parent_job import _api, _check, _parent_live
from .daemon_broker import WindowsDaemonProcess


class ShellProcessOwner:
    """Authenticate one pipe server and retain that process, not its PID."""

    def __init__(self, identity: ProcessIdentity, pipe_handle: int) -> None:
        if not _is_windows():
            raise OSError("PowerShell instance custody requires native Windows")
        if identity.pid == os.getpid():
            raise ValueError("the instance helper cannot be its own shell")
        if type(pipe_handle) is not int or pipe_handle <= 0:
            raise ValueError("original shell requires an inherited input pipe")
        self.identity = identity
        self.handle: int | None = None
        self.job: JobHandle | None = None
        self.scopes: str | None = None
        self.kernel = _api()
        try:
            self.handle = self.kernel.OpenProcess(
                0x1000 | 0x100000 | 0x40 | 0x100 | 1, False, identity.pid,
            )
            _check(self.handle)
            if _windows_birth(self.handle) != identity.birth:
                raise ProcessLookupError("original shell birth identity changed")
            server = wintypes.ULONG()
            _check(self.kernel.GetNamedPipeServerProcessId(pipe_handle, ctypes.byref(server)))
            if server.value != identity.pid:
                raise ProcessLookupError("instance pipe does not belong to the original shell")
            self.require_live()
        except BaseException:
            self.close()
            raise

    def require_live(self) -> None:
        if not self.handle:
            raise RuntimeError("original shell process custody was released")
        _parent_live(self.kernel, self.handle)

    @contextmanager
    def child(
        self, identity: ProcessIdentity, source_handle: int,
    ) -> Iterator[WindowsDaemonProcess]:
        """Duplicate the caller's retained Process.Handle, never reopen a PID."""
        self.require_live()
        if type(source_handle) is not int or not 0 < source_handle < 2**64:
            raise ValueError("child observation requires the original process handle")
        duplicate = wintypes.HANDLE()
        current = self.kernel.GetCurrentProcess()
        _check(self.kernel.DuplicateHandle(
            self.handle, source_handle, current, ctypes.byref(duplicate), 0, False, 2,
        ))
        if not duplicate.value:
            raise OSError("original child duplication returned an empty handle")
        process = WindowsDaemonProcess.from_handle(
            duplicate.value, identity.pid, ["shell-instance-child"],
        )
        try:
            self.kernel.GetProcessId.argtypes = [wintypes.HANDLE]
            self.kernel.GetProcessId.restype = wintypes.DWORD
            if (
                self.kernel.GetProcessId(process._handle) != identity.pid
                or _windows_birth(process._handle) != identity.birth
            ):
                raise ProcessLookupError("original child process identity changed")
            yield process
        finally:
            process.close()

    def contain(self, environment: dict[str, str]) -> str:
        """Assign the already-running shell before disposable admission returns.

        This handle stays in the helper through the shell's entire lifetime,
        including after its last admission guard closes. Helper failure closes
        the Job and retires the original shell and its descendants. Conversely,
        the existing caller-owned helper Job retires this helper on shell loss.
        """
        self.require_live()
        if self.job is not None:
            if self.scopes is None:
                raise RuntimeError("disposable shell Job scope publication is unconfirmed")
            return self.scopes
        name = _job_scopes.new_name()
        scopes = ";".join((*_job_scopes.scope_names(environment), name))
        _job_scopes.scope_names({_job_scopes.ENVIRONMENT_KEY: scopes})
        handle = self.kernel.CreateJobObjectW(None, name)
        _check(handle)
        owned = JobHandle(handle, name=name)
        try:
            if ctypes.get_last_error() == 183:
                raise OSError("disposable shell Job identity already exists")
            limits = _build_job_extended_limit_info()()
            limits.BasicLimitInformation.LimitFlags = 0x2000
            _check(self.kernel.SetInformationJobObject(
                handle, 9, ctypes.byref(limits), ctypes.sizeof(limits),
            ))
            _check(self.kernel.AssignProcessToJobObject(handle, self.handle))
            self.job, self.scopes = owned, scopes
        except BaseException:
            self._close_job(owned)
            raise
        return scopes

    def retire(self) -> None:
        if self.job is None:
            raise RuntimeError("disposable shell has no retained descendant Job")
        from ._windows_caller_owned import _retire_job

        _retire_job(self.job, 5)

    def _close_job(self, job: JobHandle) -> None:
        handle, job._handle = job.handle, None
        if handle and not self.kernel.CloseHandle(handle):
            # Do not keep serving requests after losing native handle custody.
            raise RuntimeError("disposable shell Job closure is unconfirmed")

    def close(self) -> None:
        try:
            if self.job is not None:
                self._close_job(self.job)
        finally:
            if self.handle:
                handle, self.handle = self.handle, None
                _check(self.kernel.CloseHandle(handle))
