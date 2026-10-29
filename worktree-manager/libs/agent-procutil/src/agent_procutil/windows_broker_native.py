"""Native child custody for the explicitly adopted finite Windows broker."""

from __future__ import annotations

import ctypes as c
import subprocess

from zdd.private_protocol import Deadline, PeerIdentity
from zdd.private_windows_native import HANDLE, native

from .daemon_broker import (
    _CREATE_SUSPENDED,
    _PROCESS_ACCESS,
    WindowsDaemonProcess,
    _outside_job,
    _resume,
)
from .windows_broker_protocol import LaunchRequest, _refuse

_DUP_HANDLE = 0x0040
_BREAKAWAY = 0x01000000


def _duplicate(source: int, handle: int) -> int:
    api = native()
    duplicate = HANDLE()
    if not api.k.DuplicateHandle(
        source, handle, api.k.GetCurrentProcess(), c.byref(duplicate),
        _PROCESS_ACCESS, False, 0,
    ):
        raise _refuse("native custody refused", cleanup_confirmed=False)
    return duplicate.value


class NativeChild(WindowsDaemonProcess):
    """Control handle obtained from CreateProcess or authenticated native custody."""

    def offer(self) -> dict[str, object]:
        if self.poll() is not None or not _outside_job(self):
            raise _refuse("child lifetime refused", cleanup_confirmed=False)
        identity = native().identity(self._handle, self.pid)
        return {
            "pid": self.pid, "birth": identity.start_time,
            "principal": identity.principal, "source_handle": self._handle,
        }

    def resume(self) -> None:
        if not _outside_job(self):
            raise _refuse("child lifetime refused", cleanup_confirmed=False)
        _resume(self)

    def retire(self, deadline: Deadline) -> None:
        deadline.remaining()
        self.terminate()
        self.wait(timeout=deadline.remaining())
        deadline.remaining()


class NativeBrokerPeer:
    """Retain one actual scheduler instance before connecting or sending secrets."""

    def __init__(self, expected: PeerIdentity) -> None:
        api = native()
        handle = api.k.OpenProcess(_PROCESS_ACCESS | _DUP_HANDLE, False, expected.pid)
        if not handle:
            raise _refuse("activation identity refused")
        try:
            api.alive(handle)
            if api.identity(handle, expected.pid) != expected:
                raise _refuse("activation identity refused")
            self.process = WindowsDaemonProcess.from_handle(
                handle, expected.pid, ["finite-windows-launch-broker"],
            )
            self.identity = expected
        except BaseException:
            api.close(handle)
            raise

    def adopt(self, offer: dict[str, object], request: LaunchRequest) -> NativeChild:
        if set(offer) != {"pid", "birth", "principal", "source_handle"}:
            raise _refuse("native offer refused", cleanup_confirmed=False)
        pid, source = offer["pid"], offer["source_handle"]
        if (
            type(pid) is not int or not 0 < pid <= 0xFFFFFFFF
            or type(source) is not int or not 0 < source < (1 << (c.sizeof(HANDLE) * 8)) - 1
        ):
            raise _refuse("native offer refused", cleanup_confirmed=False)
        if self.process.poll() is not None:
            raise _refuse("activation identity refused", cleanup_confirmed=False)
        # Duplicate through the original birth-pinned broker handle. The offer's
        # PID is only compared with GetProcessId on that actual native duplicate.
        handle = _duplicate(self.process._handle, source)
        child = NativeChild.from_handle(handle, pid, list(request.argv))
        try:
            api = native()
            if api.k.GetProcessId(handle) != pid or child.poll() is not None:
                raise _refuse("native child refused", cleanup_confirmed=False)
            actual = api.identity(handle, pid)
            if (
                actual.start_time != offer["birth"]
                or actual.principal != offer["principal"]
                or actual.principal != self.identity.principal
                or not _outside_job(child)
            ):
                raise _refuse("native child refused", cleanup_confirmed=False)
            return child
        except BaseException:
            # An unverified offer never authorizes signalling the duplicate.
            child.close()
            raise

    def wait_exit(self, deadline: Deadline) -> int:
        return self.process.wait(timeout=deadline.remaining())

    def close(self) -> None:
        self.process.close()


def stage(request: LaunchRequest, deadline: Deadline) -> NativeChild:
    """Create suspended, then prove freedom from every Job before offering."""
    import agent_procutil as pu

    deadline.remaining()
    cwd = pu._validated_daemon_cwd(request.cwd)
    output = pu._validated_daemon_log_path(request.log_path)
    error = (
        None if request.stderr_log_path is None
        else pu._validated_daemon_log_path(request.stderr_log_path, "stderr_log_path")
    )
    child = None
    with pu._open_daemon_log(output) as stdout:
        stderr = pu._open_daemon_log(error) if error else None
        try:
            # Native capability is proved on this actual resident, never inferred
            # from a scheduler definition, creation flag or immediate caller Job.
            process = subprocess.Popen(  # noqa: S603
                list(request.argv), cwd=cwd, env=request.env,
                stdin=subprocess.DEVNULL, stdout=stdout,
                stderr=stderr if stderr is not None else subprocess.STDOUT,
                close_fds=True,
                creationflags=request.creationflags | _CREATE_SUSPENDED | _BREAKAWAY,
            )
            try:
                handle = _duplicate(native().k.GetCurrentProcess(), int(process._handle))
                child = NativeChild.from_handle(handle, process.pid, list(request.argv))
                child._popen = process
                child.offer()
                deadline.remaining()
                return child
            except BaseException:
                # The spawn-owned Popen handle is also original native custody.
                process.kill()
                process.wait(timeout=5)
                if child is not None:
                    child.close()
                raise
        finally:
            if stderr is not None:
                stderr.close()
