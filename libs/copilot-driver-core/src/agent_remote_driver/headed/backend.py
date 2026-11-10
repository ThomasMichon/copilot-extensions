"""A launch-bound SDK stream, not another native conversation or launch journal."""

from __future__ import annotations

import asyncio
import json
import secrets
from typing import Protocol

from agent_procutil.daemon_instances import admit_daemon_instance


class NativeOwner(Protocol):
    """The existing launch owner retains exact native process/mux custody."""

    @property
    def pid(self) -> int | None: ...

    @property
    def returncode(self) -> int | None: ...

    async def wait(self) -> int: ...

    async def terminate(self) -> None: ...


class HeadedBackend:
    def __init__(
        self, owner: NativeOwner, *, session_id: str, nonce: str,
        startup_prompt: str | None = None,
        auth_wait=None,
    ) -> None:
        if (
            not isinstance(session_id, str) or not 1 <= len(session_id) <= 256
            or not isinstance(nonce, str) or not 32 <= len(nonce) <= 256
        ):
            raise ValueError("A headed backend requires its native session and private launch nonce")
        self._owner = owner
        self._session_id = session_id
        self._nonce = nonce
        if startup_prompt is not None and not isinstance(startup_prompt, str):
            raise ValueError("The original native startup prompt must be text")
        self._startup_prompt = startup_prompt
        self._auth_wait = auth_wait
        self.auth_snapshot: dict | None = None
        self._auth_frozen = asyncio.Event()
        self._stream_closed = asyncio.Event()
        self.auth_stopping = False
        self.stdout = asyncio.StreamReader(limit=1_000_000)
        self.stdin = self
        self.bound = asyncio.Event()
        self.lost = asyncio.Event()
        self.extension_pid: int | None = None
        self.native_pid: int | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._server: asyncio.Server | None = None
        self._connections: dict[asyncio.Task, asyncio.StreamWriter] = {}
        self._monitor: asyncio.Task | None = None
        self._guard = None
        self._closed = False
        self._admitted = False

    @property
    def pid(self) -> int | None:
        return self._owner.pid

    @property
    def returncode(self) -> int | None:
        return self._owner.returncode

    async def wait(self) -> int:
        return await self._owner.wait()

    async def terminate(self) -> None:
        await self._owner.terminate()

    def write(self, data: bytes) -> None:
        if self._writer is None or self._writer.is_closing():
            raise ConnectionError("The launch-bound native SDK backend is unavailable")
        self._writer.write(data)

    async def drain(self) -> None:
        writer = self._writer
        if writer is None:
            raise ConnectionError("The launch-bound native SDK backend is unavailable")
        await writer.drain()

    def declaration(self) -> dict:
        if self._server is None:
            raise RuntimeError("The headed backend is not listening")
        return {
            "version": 1, "port": self._server.sockets[0].getsockname()[1],
            "sessionId": self._session_id, "nonce": self._nonce,
            **({"startupPrompt": self._startup_prompt} if self._startup_prompt is not None else {}),
            **({"authWait": {"incarnation": self._auth_wait.incarnation}}
               if self._auth_wait is not None else {}),
        }

    async def freeze_auth(self) -> None:
        """Stop controlled sends before retiring the exact native process."""
        self.auth_stopping = True
        if self._auth_wait is None or self._writer is None:
            return
        self.write(json.dumps({
            "contract": "copilot-driver.headed-auth-freeze", "version": 1,
            "incarnation": self._auth_wait.incarnation,
        }).encode() + b"\n")
        try:
            await asyncio.wait_for(self.drain(), .25)
            await asyncio.wait_for(self._auth_frozen.wait(), .25)
        except (OSError, asyncio.TimeoutError):
            self._auth_wait.preserve_hold("native-freeze-unconfirmed")

    async def finish_auth_stop(self) -> None:
        """Drain the old incarnation before making its recovery decision."""
        if self._admitted:
            try:
                await asyncio.wait_for(self._stream_closed.wait(), 1)
            except asyncio.TimeoutError:
                self._auth_wait.preserve_hold("native-observation-incomplete")
        self._auth_wait.stopped(self.auth_snapshot)

    async def serve(self) -> None:
        guard = admit_daemon_instance(
            "agent-driver-headed-backend", source=__file__, purpose="production",
        )
        try:
            if self._server is not None or self._closed:
                raise RuntimeError("The headed backend is already started or closed")
            self._server = await asyncio.start_server(
                self._accept, "127.0.0.1", 0, limit=1_000_000,
            )
            self._guard = guard
            self._monitor = asyncio.create_task(self._observe_exit())
        except BaseException:
            guard.close()
            raise

    def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        if self._closed or len(self._connections) >= 4:
            writer.close()
            return
        task = asyncio.create_task(self._receive(reader, writer))
        self._connections[task] = writer
        task.add_done_callback(lambda done: self._connections.pop(done, None))

    async def _receive(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            hello = json.loads(await asyncio.wait_for(reader.readline(), 3))
            if (
                not isinstance(hello, dict)
                or set(hello) != {
                    "contract", "version", "sessionId", "nonce", "extensionPid", "nativePid",
                }
                or hello["contract"] != "copilot-driver.headed-sdk"
                or type(hello["version"]) is not int or hello["version"] != 1
                or hello["sessionId"] != self._session_id
                or not isinstance(hello["nonce"], str)
                or not secrets.compare_digest(hello["nonce"].encode(), self._nonce.encode())
                or type(hello["extensionPid"]) is not int or hello["extensionPid"] <= 0
                or type(hello["nativePid"]) is not int or hello["nativePid"] <= 0
                or self.pid is not None and hello["nativePid"] != self.pid
                or self._admitted or self.returncode is not None
            ):
                return
            self._admitted = True
            self._writer = writer
            self.extension_pid = hello["extensionPid"]
            self.native_pid = hello["nativePid"]
            self.bound.set()
            while line := await reader.readline():
                if self._auth_wait is not None:
                    value = json.loads(line)
                    if isinstance(value, dict) and value.get("contract") == "copilot-driver.headed-auth-state":
                        snapshot = value.get("state")
                        if (
                            type(value.get("version")) is not int or value.get("version") != 1
                            or value.get("incarnation") != self._auth_wait.incarnation
                            or not isinstance(snapshot, dict)
                            or set(snapshot) != {
                                "controlled", "closed", "pending", "sending", "outstanding", "waiting",
                            }
                            or any(type(item) is not bool for item in snapshot.values())
                        ):
                            self._auth_wait.preserve_hold("native-state-invalid")
                            return
                        self.auth_snapshot = snapshot
                        if not snapshot["controlled"] or snapshot["closed"]:
                            self._auth_wait.preserve_hold("native-control-lost")
                        if snapshot["waiting"]:
                            self._auth_frozen.set()
                            if any(snapshot[key] for key in ("pending", "sending", "outstanding")):
                                self._auth_wait.preserve_hold("native-operation-unsettled")
                        continue
                self.stdout.feed_data(line)
        except (OSError, ValueError, asyncio.TimeoutError):
            pass
        finally:
            if self._writer is writer:
                self._writer = None
                self.bound.clear()
                self.lost.set()
                if self._auth_wait is not None and not self.auth_stopping:
                    self._auth_wait.preserve_hold("native-connection-lost")
                self._stream_closed.set()
            writer.close()
            try:
                await asyncio.wait_for(writer.wait_closed(), 1)
            except (OSError, asyncio.TimeoutError):
                pass

    async def _observe_exit(self) -> None:
        await self._owner.wait()
        self.stdout.feed_eof()

    async def close(self) -> None:
        self._closed = True
        if self._server is not None:
            self._server.close()
        tasks = set(self._connections)
        if self._monitor is not None:
            tasks.add(self._monitor)
        for writer in tuple(self._connections.values()):
            writer.close()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self._server is not None:
            await self._server.wait_closed()
        if self._guard is not None:
            self._guard.close()
            self._guard = None
