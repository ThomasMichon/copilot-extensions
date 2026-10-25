"""Accepted Vault connections and their native requests share server custody."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from functools import partial


log = logging.getLogger(__name__)


async def settle_owned(awaitable):
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
    def __init__(self, service, handler):
        self.service = service
        self.handler = handler
        self.closing = False
        self.clients: dict[asyncio.Task, asyncio.StreamWriter] = {}
        self.busy: set[asyncio.StreamWriter] = set()
        self.workers: set[asyncio.Future] = set()

    def callback(self, transport):
        return partial(self.accept, transport=transport)

    def accept(self, reader, writer, *, transport):
        task = asyncio.create_task(
            self.handler(reader, writer, self.service, transport, _connections=self)
        )
        self.clients[task] = writer
        task.add_done_callback(self._finished)
        if self.closing or self.service._shutdown:
            writer.close()

    def _finished(self, task):
        writer = self.clients.pop(task)
        self.busy.discard(writer)

    def request(self, writer, request, peer, transport):
        if self.closing or self.service._shutdown:
            return None
        self.busy.add(writer)
        worker = asyncio.get_running_loop().run_in_executor(
            None, self.service.handle_request, request, peer, transport
        )
        self.workers.add(worker)
        worker.add_done_callback(self.workers.discard)
        return worker

    async def close(self, servers, pipe_servers, cleanup):
        self.closing = True
        first_error = None

        def remember(error):
            nonlocal first_error
            if first_error is None:
                first_error = error
                with contextlib.suppress(BaseException):
                    log.warning(
                        "Vault closure failed (%s); retaining admission until settled",
                        type(error).__name__,
                    )

        pending = [
            *servers, *pipe_servers,
            *(writer for writer in self.clients.values() if writer not in self.busy),
        ]
        while pending:
            failed = []
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

        pending = list(servers)
        while pending:
            results = await asyncio.gather(
                *(server.wait_closed() for server in pending), return_exceptions=True
            )
            failed = []
            for server, result in zip(pending, results):
                if isinstance(result, BaseException):
                    remember(result)
                    failed.append(server)
            pending = failed
            if pending:
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
