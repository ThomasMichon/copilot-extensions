"""One-use, native-client-bound transfer of an in-memory Windows MCP overlay."""

from __future__ import annotations

import ctypes as c
import json
import secrets
import threading

from zdd.private_protocol import Deadline, PrivateIPCError, Refusal
from zdd.private_windows import WindowsChannel
from zdd.private_windows_native import HANDLE, INVALID_HANDLE, Native


class OverlayPipe:
    """Reuse private overlapped I/O, without its incompatible framed handshake.

    The native CLI reads a JSON file, not a private-channel protocol. The
    suspended creation owner pins that exact reader before any bytes pass.
    No listener, worker or configuration survives this launch scope.
    """

    def __init__(self, configuration: dict) -> None:
        self._data = json.dumps(configuration, ensure_ascii=True).encode("utf-8")
        if len(self._data) > 1024 * 1024:
            raise ValueError("Native MCP overlay exceeds the transfer limit")
        self.path = "\\\\.\\pipe\\agent-mcp-overlay-" + secrets.token_hex(32)
        self._api = Native()
        self._pipe = None
        self._process = None
        self._identity = None
        self._channel = None
        self._fence = self._dummy = self._inherited = None
        self._cancelled = threading.Event()
        self._used = False
        try:
            self._fence = self._api.listener(self.path, max_instances=2)
            self._dummy = self._api.k.CreateFileW(
                self.path, 0xC0000000, 0, None, 3, 0x40000000, None,
            )
            if self._dummy in (None, 0, INVALID_HANDLE):
                self._dummy = None
                raise PrivateIPCError(Refusal.IO)
            self._pipe = self._api.listener(
                self.path, max_instances=2, first_instance=False,
            )
            result = HANDLE()
            current = self._api.k.GetCurrentProcess()
            # The native owner only keeps the occupied instance alive. It has
            # no data, configuration, connect or write authority through this handle.
            if not self._api.k.DuplicateHandle(
                current, self._fence, current, c.byref(result), 0x100000, True, 0,
            ):
                raise PrivateIPCError(Refusal.IO)
            self._inherited = result.value
        except BaseException:
            try:
                self.close()
            except BaseException as cleanup:
                from agent_procutil.owned_process import OwnedCleanupError

                raise OwnedCleanupError(
                    None, cleanup_resources=(("configuration-transfer", self),),
                    cleanup_retry=lambda _deadline: self.close(),
                ) from cleanup
            raise

    @property
    def inherited_handle(self) -> int:
        if self._inherited is None:
            raise ValueError("Native MCP overlay reservation is closed")
        return self._inherited

    def bind(self, process_handle: int, pid: int) -> None:
        if self._process is not None or self._used:
            raise ValueError("Native MCP overlay already has a reader")
        self._process = self._api.duplicate_process(process_handle, pid)
        identity = self._api.identity(self._process, pid)
        if identity.principal != self._api.current_sid():
            raise PrivateIPCError(Refusal.PRINCIPAL)
        self._identity = identity
        # The existing suspended-creation owner has now inherited the observation
        # handle explicitly. Its kernel reference, not a parent waiter, reserves
        # the name through successful transfer, timeout and parent cleanup.
        self._close_handles(("_fence", "_dummy", "_inherited"))

    def _close_handle(self, name: str) -> None:
        handle = getattr(self, name)
        if handle is not None:
            self._api.close(handle)
            setattr(self, name, None)

    def send(self) -> None:
        if self._identity is None or self._process is None or self._used:
            raise ValueError("Native MCP overlay requires its original reader")
        self._used = True
        self._channel = channel = WindowsChannel(
            self._pipe, self._identity, self._process, api=self._api,
        )
        self._pipe = self._process = None
        deadline = Deadline(15, cancelled=self._cancelled)
        try:
            channel._io("connect", None, 0, deadline)
            # The retained creation handle prevents reuse of this numeric identity.
            if self._api.peer_pid(channel._pipe, server=False) != self._identity.pid:
                raise PrivateIPCError(Refusal.IDENTITY)
            channel.check_peer(deadline)
            channel.write_all(self._data, deadline)
        finally:
            try:
                channel.close()
            finally:
                self._data = b""

    def cancel(self) -> None:
        self._cancelled.set()

    def _close_handles(self, names) -> None:
        errors = []
        for name in names:
            try:
                self._close_handle(name)
            except BaseException as error:
                errors.append(error)
        if errors:
            raise errors[0]

    def close(self) -> None:
        self.cancel()
        self._data = b""
        errors = []
        try:
            if self._channel is not None:
                self._channel.close()
                self._channel = None
        except BaseException as error:
            errors.append(error)
        try:
            self._close_handles(("_pipe", "_process", "_fence", "_dummy", "_inherited"))
        except BaseException as error:
            errors.append(error)
        if errors:
            raise errors[0]
