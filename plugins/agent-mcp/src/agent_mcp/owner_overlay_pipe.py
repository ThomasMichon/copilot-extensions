"""One-use, native-client-bound transfer of an in-memory Windows MCP overlay."""

from __future__ import annotations

import json
import secrets
import threading

from zdd.private_protocol import Deadline, PrivateIPCError, Refusal
from zdd.private_windows import WindowsChannel
from zdd.private_windows_native import Native


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
        self._pipe = self._api.listener(self.path)
        self._process = None
        self._identity = None
        self._cancelled = threading.Event()
        self._used = False

    def bind(self, process_handle: int, pid: int) -> None:
        if self._process is not None or self._used:
            raise ValueError("Native MCP overlay already has a reader")
        handle = self._api.duplicate_process(process_handle, pid)
        try:
            identity = self._api.identity(handle, pid)
            if identity.principal != self._api.current_sid():
                raise PrivateIPCError(Refusal.PRINCIPAL)
            self._identity, self._process = identity, handle
        except BaseException:
            self._api.close(handle)
            raise

    def send(self) -> None:
        if self._identity is None or self._process is None or self._used:
            raise ValueError("Native MCP overlay requires its original reader")
        self._used = True
        channel = WindowsChannel(
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

    def close(self) -> None:
        self.cancel()
        try:
            if self._pipe is not None:
                self._api.close(self._pipe)
                self._pipe = None
        finally:
            if self._process is not None:
                self._api.close(self._process)
                self._process = None
            self._data = b""
