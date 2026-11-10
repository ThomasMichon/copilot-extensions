"""Bounded client input with independent control progress during backpressure."""

from __future__ import annotations

import asyncio
import json

from .custody_contract import MAX_APPLICATION, MAX_CONTROL
from .custody_state import CustodyState, MAX_BUFFERED_BYTES, MAX_CONTROL_BYTES
from .custody_wire import MAX_FRAME


class IntakeOverflow(ConnectionError):
    """Input exceeded a bounded window; this client stream cannot continue."""


class ClientIntake:
    """Read without awaiting dispatch; preserve application admission order.

    Overflow is terminal, including for notifications: it is never a silent
    drop or permission to replay. Ending intake does not cancel accepted work
    or close the custodian's retained upstream. Cancellation waits for its
    queued target's admission without blocking unrelated server responses.
    """

    def __init__(self, owner, reader):
        self.owner, self.reader = owner, reader
        self._queues = {False: asyncio.Queue(), True: asyncio.Queue()}
        self._counts = {False: 0, True: 0}
        self._bytes = {False: 0, True: 0}
        self._pending = {}
        self._started = False

    def _enqueue(self, message):
        message, size = CustodyState._message(message)
        control = "method" not in message or message["method"] == "notifications/cancelled"
        count_limit = MAX_CONTROL if control else MAX_APPLICATION
        byte_limit = MAX_CONTROL_BYTES if control else MAX_BUFFERED_BYTES
        if self._counts[control] >= count_limit or self._bytes[control] + size > byte_limit:
            raise IntakeOverflow("client input window overflow; session intake stopped")
        dependency = admitted = None
        if not control and "id" in message:
            if message["id"] in self._pending:
                raise ValueError("client request identity is already queued")
            admitted = asyncio.get_running_loop().create_future()
            admitted.add_done_callback(lambda future: None if future.cancelled() else future.exception())
            self._pending[message["id"]] = admitted
        elif message.get("method") == "notifications/cancelled":
            params = message.get("params")
            target = params.get("requestId") if isinstance(params, dict) else None
            if type(target) in (str, int):
                dependency = self._pending.get(target)
        self._counts[control] += 1
        self._bytes[control] += size
        self._queues[control].put_nowait((message, size, admitted, dependency))

    async def _read(self):
        while line := await self.reader.readline():
            if len(line) > MAX_FRAME or not line.endswith(b"\n"):
                raise ValueError("invalid or oversized client frame")
            self._enqueue(json.loads(line))
        self._queues[False].put_nowait(None)
        for _ in range(MAX_CONTROL):
            self._queues[True].put_nowait(None)

    async def _dispatch(self, control):
        queue = self._queues[control]
        while (item := await queue.get()) is not None:
            message, size, admitted, dependency = item
            try:
                if dependency is not None:
                    await asyncio.shield(dependency)
                await self.owner.submit(message, wait=True)
                if admitted is not None:
                    admitted.set_result(None)
            finally:
                if admitted is not None:
                    self._pending.pop(message["id"])
                    if not admitted.done():
                        admitted.set_exception(ConnectionError("queued input admission did not settle"))
                self._counts[control] -= 1
                self._bytes[control] -= size

    async def run(self):
        """Join input publication at EOF; never restart an interrupted intake."""
        if self._started:
            raise ValueError("client intake cannot be restarted or replayed")
        self._started = True
        tasks = [
            asyncio.create_task(self._dispatch(False)),
            *(asyncio.create_task(self._dispatch(True)) for _ in range(MAX_CONTROL)),
            asyncio.create_task(self._read()),
        ]
        try:
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
