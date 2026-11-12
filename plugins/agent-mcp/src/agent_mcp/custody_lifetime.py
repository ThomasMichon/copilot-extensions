"""Explicit native session ownership for opt-in adopted stdio conduits."""

from __future__ import annotations

import logging
import os
import select
import sys
import threading

log = logging.getLogger("agent-mcp.custody-lifetime")


def _pin(pid, birth):
    from agent_procutil._daemon_instance_identity import _windows_birth, birth_identity

    if os.name == "nt":
        import ctypes

        from agent_procutil.daemon_broker import WindowsDaemonProcess, _kernel32

        handle = _kernel32().OpenProcess(0x100000 | 0x1000, False, pid)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        process = WindowsDaemonProcess.from_handle(handle, pid, ["mcp-session-owner"])
        try:
            if _windows_birth(handle) != birth or process.poll() is not None:
                raise ProcessLookupError("MCP session owner identity changed or exited")
        except BaseException:
            process.close()
            raise
        return lambda: process.poll() is None, process.close
    if sys.platform.startswith("linux"):
        from agent_procutil._linux_pidfd import open_pidfd

        descriptor = open_pidfd(pid)
        try:
            if birth_identity(pid) != birth:
                raise ProcessLookupError("MCP session owner identity changed")
            observer = select.poll()
            observer.register(descriptor, select.POLLIN)
            def alive():
                events = observer.poll(0)
                if any(flags & (select.POLLERR | select.POLLNVAL) for _, flags in events):
                    raise OSError("MCP session owner pidfd observation failed")
                return not any(flags & (select.POLLIN | select.POLLHUP) for _, flags in events)

            if not alive():
                raise ProcessLookupError("MCP session owner already exited")
        except BaseException:
            os.close(descriptor)
            raise
        return alive, lambda: os.close(descriptor)
    raise OSError("Explicit MCP session ownership requires Windows or Linux")


class SessionOwner:
    """Pin an owner supplied by its launcher, never infer it from ancestry.

    The originating session captures its own PID and native birth before an
    interposed launcher can exit. This is a local lifetime observation, not an
    authentication grant or permission to signal that process.
    """

    def __init__(self, pid: int, birth: str):
        maximum_pid = (1 << (32 if os.name == "nt" else 31)) - 1
        if (
            type(pid) is not int or not 0 < pid <= maximum_pid or not isinstance(birth, str)
            or not birth or len(birth) > 256 or any(ord(char) < 33 for char in birth)
        ):
            raise ValueError("MCP session ownership requires an explicit PID and native birth")
        self.pid, self.birth = pid, birth
        self._alive, self._release = _pin(pid, birth)
        self._stop = threading.Event()
        self._thread = None
        self._closed = False

    def require_alive(self):
        """Recheck the retained native pin before accepting a new execution path."""
        if self._closed:
            raise RuntimeError("MCP session owner observation is closed")
        if not self._alive():
            raise ProcessLookupError("MCP session owner exited before execution")

    def start(self, on_death):
        if self._closed or self._thread is not None:
            raise RuntimeError("MCP session owner observation is closed or already started")
        unavailable = False
        try:
            alive = self._alive()
        except OSError:
            log.warning("MCP session owner observation unavailable; lifetime retained")
            unavailable = True
        else:
            if not alive:
                raise ProcessLookupError("MCP session owner exited before conduit admission")

        def watch():
            nonlocal unavailable
            while not self._stop.wait(.1):
                try:
                    alive = self._alive()
                except OSError:
                    if not unavailable:
                        log.warning("MCP session owner observation unavailable; lifetime retained")
                    unavailable = True
                    continue
                unavailable = False
                if not alive:
                    on_death()
                    return

        self._thread = threading.Thread(target=watch, name="mcp-session-owner", daemon=True)
        try:
            self._thread.start()
        except BaseException:
            self._stop.set()
            raise

    def close(self):
        if self._closed:
            return
        self._stop.set()
        if self._thread is not None and self._thread.ident is not None:
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                raise RuntimeError("MCP session owner observation has not settled")
        self._release()
        self._closed = True

    def __enter__(self):
        if self._closed:
            raise RuntimeError("MCP session owner observation is closed")
        return self

    def __exit__(self, *_):
        self.close()
