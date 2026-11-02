"""Finite native-pin custody that rolls back if the retirement caller dies.

Requires the ``retirement-custody`` extra. Callers still own target selection,
ancestry inspection and authority; the worker never discovers processes.
"""

from __future__ import annotations

import os
import secrets
import socket
import subprocess
import sys
from pathlib import Path

import zdd
from zdd.private_protocol import Deadline, PrivateIPCError, read_frame, write_frame

from . import contained_test_mode
from .daemon_instances import (
    DaemonInstanceSpec,
    cleanup_instance,
    spawn_finite_worker,
    spawn_instance,
)
from .process_retirement import RetirementRefused

_ENTRY = (
    "import sys;sys.path.insert(0,sys.argv.pop(1));"
    "from agent_procutil._retirement_custodian import main;main()"
)


class RetirementCustody:
    """One finite worker, outside the caller's production Job/process group.

    Closing the private lifeline resumes all remaining original pins. Failed
    cleanup retains worker custody; this class never kills a worker holding pins.
    """

    def __init__(self, runtime_root: Path):
        self._root = Path(runtime_root).absolute()
        self._channel = None
        self._process = None

    def __enter__(self):
        if self._process is not None:
            raise RetirementRefused("Retirement custody is already active")
        instance = DaemonInstanceSpec(
            "runtime-retirement-custodian", self._root, Path(__file__).absolute(),
            lifecycle="finite-worker",
        )
        command = [
            sys.executable, "-I", "-c", _ENTRY,
            str(Path(zdd.__file__).resolve().parent.parent), str(os.getpid()),
        ]
        try:
            if os.name == "nt":
                self._windows(command, instance)
            elif sys.platform.startswith("linux"):
                self._linux(command, instance)
            else:
                raise RetirementRefused("Retirement custody requires Windows or Linux")
            if read_frame(self._channel, Deadline(15)) != {"ready": True}:
                raise RetirementRefused("Retirement custodian did not become ready")
            return self
        except BaseException:
            self.close()
            raise

    def _windows(self, command, instance) -> None:
        from zdd.private_windows import WindowsListener
        from zdd.private_windows_native import native

        api = native()
        parent = api.identity(api.k.GetCurrentProcess(), os.getpid())
        path = rf"\\.\pipe\runtime-retirement-{secrets.token_hex(24)}"
        listener = WindowsListener(path)
        try:
            self._process = spawn_finite_worker(
                [*command, "pipe", path, parent.start_time, parent.principal],
                instance=instance, cwd=self._root,
                log_path=self._root / "retirement-custodian.log", console_children=True,
            )
            child = api.identity(self._process._handle, self._process.pid)
            self._channel = listener.accept(Deadline(15), child)
        finally:
            listener.close()

    def _linux(self, command, instance) -> None:
        from ._linux_pidfd import open_pidfd
        from ._retirement_channel import InheritedChannel

        parent, child = socket.socketpair()
        owner_fd = None
        try:
            owner_fd = open_pidfd(os.getpid())
            self._process = spawn_instance(
                [*command, "socket", str(child.fileno()), str(owner_fd)],
                instance=instance, cwd=self._root, stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True,
                pass_fds=(child.fileno(), owner_fd),
                start_new_session=not contained_test_mode(),
            )
            self._channel = InheritedChannel(parent, open_pidfd(self._process.pid))
        except BaseException:
            parent.close()
            raise
        finally:
            child.close()
            if owner_fd is not None:
                os.close(owner_fd)

    def _request(self, operation: str, **fields):
        if self._channel is None:
            raise RetirementRefused("Retirement custodian connection is closed")
        try:
            deadline = Deadline(10)
            self._channel.check_peer(deadline)
            write_frame(self._channel, {"operation": operation, **fields}, deadline)
            response = read_frame(self._channel, deadline)
        except (OSError, PrivateIPCError):
            raise RetirementRefused("Retirement custodian connection failed") from None
        if response.get("error") == "lookup":
            raise ProcessLookupError(fields.get("pid"))
        if response.get("error"):
            raise RetirementRefused("Retirement custodian refused native process control")
        if set(response) != {"result"}:
            raise RetirementRefused("Retirement custodian response is invalid")
        return response["result"]

    def pin(self, pid: int, birth: str):
        """Retain the original native identity before returning a proxy."""
        if pid == os.getpid():
            raise RetirementRefused("External retirement cannot target its caller")
        self._request("pin", pid=pid, birth=birth)
        return _Pin(self, pid, birth)

    def __exit__(self, *_):
        self.close()

    def close(self) -> None:
        """Drop the lifeline and confirm kernel exit, never force a rollback."""
        close_failed = False
        try:
            if self._channel is not None:
                self._channel.close()
        except (OSError, PrivateIPCError):
            close_failed = True
        finally:
            self._channel = None
        if self._process is None:
            return
        process = self._process
        try:
            result = process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            error = RetirementRefused("Retirement custodian retains unsettled native custody")
            error.process = process
            raise error from None
        cleanup_instance(process.instance_receipt, terminate=False)
        if closer := getattr(process, "close", None):
            closer()
        self._process = None
        if result != 0 or close_failed:
            raise RetirementRefused("Retirement custodian did not exit successfully")


class _Pin:
    def __init__(self, custody: RetirementCustody, pid: int, birth: str):
        self._custody = custody
        self.pid, self.birth = pid, birth

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self._custody._request("close", pid=self.pid)

    def pause(self):
        self._custody._request("pause", pid=self.pid)

    def exited(self):
        return self._custody._request("exited", pid=self.pid)

    def terminate(self):
        self._custody._request("terminate", pid=self.pid)
