"""Accepted Vault connections and their native requests share server custody."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from functools import partial
from collections.abc import Awaitable, Callable, Coroutine, Sequence
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

if TYPE_CHECKING:
    from .service import VaultService

T = TypeVar("T")
Message = dict[str, Any]


class Closable(Protocol):
    def close(self) -> None: ...


class ClosingServer(Closable, Protocol):
    async def wait_closed(self) -> None: ...


class ClientHandler(Protocol):
    def __call__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        service: VaultService, transport: str = "unknown", *,
        _connections: VaultConnections | None = None,
    ) -> Coroutine[Any, Any, None]: ...


log = logging.getLogger(__name__)


async def settle_owned(awaitable: Awaitable[T]) -> T:
    """Defer cancellation until owned work has actually settled."""
    future = asyncio.ensure_future(awaitable)
    cancelled = False
    while True:
        try:
            result = await asyncio.shield(future)
            break
        except asyncio.CancelledError:
            if future.cancelled():
                raise
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
    return result


class VaultConnections:
    SERVER_CLOSE_GRACE = 2.0

    def __init__(self, service: VaultService, handler: ClientHandler) -> None:
        self.service = service
        self.handler = handler
        self.closing = False
        self.clients: dict[asyncio.Task[None], asyncio.StreamWriter] = {}
        self.busy: set[asyncio.StreamWriter] = set()
        self.workers: set[asyncio.Future[Any]] = set()
        self.work: set[asyncio.Future[Any]] = set()

    def callback(
        self, transport: str,
    ) -> Callable[[asyncio.StreamReader, asyncio.StreamWriter], None]:
        return partial(self.accept, transport=transport)

    def accept(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, *, transport: str,
    ) -> None:
        task = asyncio.create_task(
            self.handler(reader, writer, self.service, transport, _connections=self)
        )
        self.clients[task] = writer
        task.add_done_callback(self._finished)
        if self.closing or self.service._shutdown:
            writer.close()

    def _finished(self, task: asyncio.Task[None]) -> None:
        writer = self.clients.pop(task)
        self.busy.discard(writer)

    def request(
        self, writer: asyncio.StreamWriter, request: Message, peer: str, transport: str,
    ) -> asyncio.Future[Message] | None:
        if self.closing or self.service._shutdown:
            return None
        credential_work = request.get("action") not in {"ping", "stop", "handoff-export"}
        if credential_work and not self.service._runtime_can_work:
            response: asyncio.Future[Message] = asyncio.get_running_loop().create_future()
            response.set_result({"ok": False, "error": "service_transition", "retryable": True})
            return response
        self.busy.add(writer)
        worker = asyncio.get_running_loop().run_in_executor(
            None, self.service._handle_admitted_request, request, peer, transport
        )
        self.workers.add(worker)
        worker.add_done_callback(self.workers.discard)
        if credential_work:
            self.work.add(worker)
            worker.add_done_callback(self.work.discard)
        return worker

    async def close(
        self, servers: Sequence[ClosingServer], pipe_servers: Sequence[Closable],
        cleanup: Callable[[], None],
    ) -> None:
        self.closing = True
        first_error: BaseException | None = None

        def remember(error: BaseException) -> None:
            nonlocal first_error
            if first_error is None:
                first_error = error
                with contextlib.suppress(BaseException):
                    log.warning(
                        "Vault closure failed (%s); retaining admission until settled",
                        type(error).__name__,
                    )

        pending: list[Closable] = [
            *servers, *pipe_servers,
            *(writer for writer in self.clients.values() if writer not in self.busy),
        ]
        while pending:
            failed: list[Closable] = []
            for target in pending:
                try:
                    target.close()
                except BaseException as error:
                    remember(error)
                    failed.append(target)
            pending = failed
            if pending:
                # A failed intake close is unknown, not proof intake has stopped.
                await asyncio.sleep(0.05)

        while self.clients or self.workers:
            await asyncio.gather(*self.clients, *self.workers, return_exceptions=True)

        pending_servers = list(servers)
        while pending_servers:
            waits = {asyncio.ensure_future(server.wait_closed()): server for server in pending_servers}
            done, stalled = await asyncio.wait(waits, timeout=self.SERVER_CLOSE_GRACE)
            for future in stalled:
                future.cancel()
            if stalled:
                # Every tracked client and worker has settled, so a server still
                # waiting here is holding only asyncio's untracked connection
                # count (e.g. after a peer reset). It must not block cleanup.
                with contextlib.suppress(BaseException):
                    log.warning(
                        "Vault server close did not settle within %ss; continuing cleanup",
                        self.SERVER_CLOSE_GRACE,
                    )
            failed_servers = []
            for future in done:
                failure = None if future.cancelled() else future.exception()
                if future.cancelled() or failure is not None:
                    remember(failure or asyncio.CancelledError())
                    failed_servers.append(waits[future])
            pending_servers = failed_servers
            if pending_servers:
                await asyncio.sleep(0.05)

        while True:
            try:
                cleanup()
                break
            except BaseException as error:
                remember(error)
                await asyncio.sleep(0.05)
        if first_error is not None:
            raise first_error
