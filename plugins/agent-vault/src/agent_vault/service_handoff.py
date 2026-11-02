"""Best-effort auth-state transfer over the shared private native channels."""

from __future__ import annotations

import asyncio
import contextlib
import os
from pathlib import Path
import sys
import threading
import uuid

from zdd.diagnostics import process_start_time
from zdd.private_protocol import (
    Deadline, PeerIdentity, PrivateIPCError, Refusal, read_frame, write_frame,
)

from .cutover import AUTH_STATE_CONTRACT, AUTH_STATE_VERSIONS, negotiate_auth_state
from .service_connections import settle_owned


def peer_identity(pid: int) -> PeerIdentity:
    if os.name == "nt":
        from zdd.private_windows_native import native

        api = native()
        handle = api.k.OpenProcess(0x1000 | 0x100000, False, pid)
        if not handle:
            raise PrivateIPCError(Refusal.IDENTITY)
        try:
            peer = api.identity(handle, pid)
            api.alive(handle)
            if peer.principal != api.current_sid():
                raise PrivateIPCError(Refusal.PRINCIPAL)
            return peer
        finally:
            api.close(handle)
    if sys.platform != "linux":
        raise PrivateIPCError(Refusal.UNAVAILABLE)
    return PeerIdentity(pid, process_start_time(pid), str(os.geteuid()))


def supported_versions() -> list[int]:
    return list(AUTH_STATE_VERSIONS) if os.name == "nt" or sys.platform == "linux" else []


class AuthReceiver:
    """One bounded, exact-predecessor receive; plaintext exists only in memory."""

    def __init__(self, root: Path, predecessor_pid: int, generation: str) -> None:
        self.peer = peer_identity(predecessor_pid)
        self.generation = generation
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._payload: dict | None = None
        self.task: asyncio.Task | None = None
        self.closed = False
        key = uuid.uuid4().hex
        if os.name == "nt":
            from zdd.private_windows import WindowsListener

            self.address = rf"\\.\pipe\agent-vault-auth-{key}"
            self.listener = WindowsListener(self.address)
        else:
            from zdd.private_unix import UnixListener

            directory = root / "auth-handoff"
            directory.mkdir(mode=0o700, exist_ok=True)
            self.address = str(directory / (key[:12] + ".sock"))
            self.listener = UnixListener(Path(self.address))

    def start(self) -> None:
        self.task = asyncio.create_task(asyncio.to_thread(self._receive))

    def _receive(self) -> None:
        deadline = Deadline(60, cancelled=self.cancelled)
        try:
            with contextlib.closing(self.listener.accept(deadline, expected=self.peer)) as channel:
                frame = read_frame(channel, deadline)
                if (
                    frame.get("contract") != AUTH_STATE_CONTRACT
                    or type(frame.get("version")) is not int
                    or frame["version"] not in AUTH_STATE_VERSIONS
                    or frame.get("target_generation") != self.generation
                    or not isinstance(frame.get("auth"), dict)
                    or frame["auth"].get("contract") != AUTH_STATE_CONTRACT
                    or type(frame["auth"].get("version")) is not int
                    or frame["auth"]["version"] != frame["version"]
                ):
                    return
                with self._lock:
                    self._payload = frame["auth"]
                write_frame(channel, {"received": True, "version": frame["version"]}, deadline)
        except Exception:
            # Private transport diagnostics intentionally do not contain frames.
            return

    def advertisement(self) -> dict | None:
        if self.closed or self.task is None or self.task.done():
            return None
        return {"address": self.address, "versions": supported_versions()}

    async def close(self) -> None:
        self.cancelled.set()
        try:
            if self.task is not None:
                await settle_owned(self.task)
        finally:
            self.listener.close()
            self.closed = True

    def take(self) -> dict | None:
        with self._lock:
            payload, self._payload = self._payload, None
            return payload


def send_auth_state(status: dict, payload: dict) -> bool:
    """Send only to the independently verified selected successor's native peer."""
    receiver = status.get("auth_handoff_receiver")
    if (
        not isinstance(receiver, dict)
        or negotiate_auth_state(status.get("auth_handoff_versions")) is None
        or negotiate_auth_state(receiver.get("versions")) is None
        or not isinstance(receiver.get("address"), str)
        or not isinstance(status.get("runtime_generation"), str)
    ):
        return False
    try:
        peer = peer_identity(status["pid"])
        deadline = Deadline(1)
        if os.name == "nt":
            from zdd.private_windows import connect

            channel = connect(receiver["address"], peer, deadline)
        else:
            from zdd.private_unix import connect

            channel = connect(Path(receiver["address"]), peer, deadline)
        with contextlib.closing(channel):
            write_frame(channel, {
                "contract": AUTH_STATE_CONTRACT, "version": 1,
                "target_generation": status["runtime_generation"], "auth": payload,
            }, deadline)
            response = read_frame(channel, deadline)
            return response.get("received") is True and response.get("version") == 1
    except Exception:
        return False
