"""Private Linux Unix HTTP ingress within the existing WatchService lifetime."""

import asyncio
import os
from pathlib import Path
import socket
import stat
import struct
import sys
import time

from fleet_manager.gateway_service_channel import BODY_BYTES
from fleet_manager.gateway_service_handler import RequestLifetime
from fleet_manager.local_service_handler import LocalCallerContext, LocalServiceRequest

from .repository_subscriptions import RESPONSE_BYTES


class LocalIngress:
    """OS peer admission, never caller-selected identity from HTTP headers."""

    connected: asyncio.Event

    def __init__(self, *, path, namespace, peers, handler, operation_seconds):
        if sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED"):
            raise ValueError("local repository ingress requires Linux peer credentials")
        self.path, self.namespace = Path(path), namespace
        self.peers = dict(peers)
        if not self.path.is_absolute() or len(str(self.path).encode()) > 100 or not self.peers:
            raise ValueError("invalid private local ingress selection")
        self.handler, self.seconds = handler, operation_seconds
        self.connected = asyncio.Event()
        self._tasks = set()
        self._requests = {}
        self._closing = False

    def _stale(self):
        if not self.path.exists() and not self.path.is_symlink():
            return
        info = self.path.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
            raise ValueError("existing local endpoint is not an owned socket")
        probe = socket.socket(socket.AF_UNIX)
        probe.settimeout(.1)
        try:
            probe.connect(str(self.path))
        except ConnectionRefusedError:
            if self.path.lstat().st_ino != info.st_ino:
                raise ValueError("local endpoint changed during adoption")
            self.path.unlink()
        else:
            raise ValueError("local endpoint already has a serving owner")
        finally:
            probe.close()

    async def run(self, stop):
        self._stale()
        server = await asyncio.start_unix_server(self._accept, path=str(self.path), limit=8192)
        os.chmod(self.path, 0o600)
        owned = self.path.lstat()
        self.connected.set()
        try:
            await stop.wait()
        finally:
            self._closing = True
            self.connected.clear()
            server.close()
            for writer, lifetime in tuple(self._requests.values()):
                lifetime.close()
                writer.close()
                writer.transport.abort()
            if self._tasks:
                await asyncio.gather(*tuple(self._tasks), return_exceptions=True)
            await server.wait_closed()
            if self.path.exists() and self.path.lstat().st_ino == owned.st_ino:
                self.path.unlink()

    def _accept(self, reader, writer):
        if self._closing or len(self._tasks) >= 8:
            writer.close()
            return
        task = asyncio.create_task(self._connection(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _connection(self, reader, writer):
        deadline = time.monotonic() + self.seconds
        expires_at = int(time.time()) + self.seconds
        lifetime = RequestLifetime()
        task = asyncio.current_task()
        self._requests[task] = (writer, lifetime)
        monitor = pending = None
        try:
            raw_peer = writer.get_extra_info("socket").getsockopt(
                socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII"),
            )
            _pid, uid, _gid = struct.unpack("iII", raw_peer)
            principal = self.peers.get(uid)
            if principal is None:
                await asyncio.wait_for(
                    self._write(writer, 403, b'{"error":"local_peer_refused"}'), self.seconds,
                )
                return
            async def receive():
                raw = await reader.readuntil(b"\r\n\r\n")
                if len(raw) > 8192:
                    raise ValueError
                lines = raw.decode("ascii").split("\r\n")
                method, target, version = lines[0].split(" ")
                if method not in ("GET", "POST") or version != "HTTP/1.1" or "?" in target:
                    raise ValueError
                headers = {}
                for line in lines[1:-2]:
                    name, value = line.split(":", 1)
                    name = name.lower().strip()
                    if name in headers:
                        raise ValueError
                    headers[name] = value.strip()
                if "transfer-encoding" in headers:
                    raise ValueError
                size = int(headers.get("content-length", "0"))
                if not 0 <= size <= BODY_BYTES:
                    raise ValueError
                body = await reader.readexactly(size)
                return LocalServiceRequest(
                    LocalCallerContext(self.namespace, principal, expires_at),
                    method, target, tuple(headers.items()), body, lifetime,
                    response_limit=RESPONSE_BYTES,
                )
            request = await asyncio.wait_for(receive(), self.seconds)
            monitor = asyncio.create_task(reader.read(1))
            pending = asyncio.create_task(self.handler(request))
            done, _ = await asyncio.wait({monitor, pending}, return_when=asyncio.FIRST_COMPLETED)
            if monitor in done:
                lifetime.close()
            result = await asyncio.shield(pending)
            lifetime.check()
            if len(result.body) > RESPONSE_BYTES:
                raise ValueError
            await asyncio.wait_for(
                self._write(writer, result.status, result.body),
                max(.001, deadline - time.monotonic()),
            )
        except (Exception, asyncio.CancelledError):
            lifetime.close()
        finally:
            if monitor is not None:
                monitor.cancel()
                await asyncio.gather(monitor, return_exceptions=True)
            if pending is not None and not pending.done():
                lifetime.close()
                await asyncio.gather(pending, return_exceptions=True)
            self._requests.pop(task, None)
            writer.close()
            try:
                await asyncio.wait_for(
                    writer.wait_closed(), max(.001, deadline - time.monotonic()),
                )
            except (OSError, asyncio.TimeoutError):
                writer.transport.abort()

    @staticmethod
    async def _write(writer, status, body):
        writer.write(
            f"HTTP/1.1 {status} Result\r\nContent-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode() + body,
        )
        await writer.drain()
