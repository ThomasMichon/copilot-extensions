"""Ordered private custody IPC; cancellation never authorizes request replay."""

from __future__ import annotations

import asyncio
import contextlib
import json

from .custody_contract import MAX_SEQUENCE

MAX_FRAME = 8 * 1024 * 1024
MAX_PENDING = 128


class Peer:
    """Exchange ordered operations and retain receipts after caller cancellation."""

    def __init__(self, reader, writer, handler) -> None:
        self.reader, self.writer, self.handler = reader, writer, handler
        self._counter = 0
        self._received = 0
        self._pending = {}
        self._tasks = set()
        self._receiving = set()
        self._write_lock = asyncio.Lock()
        self._credits = asyncio.Semaphore(MAX_PENDING)
        self._closed = False

    @staticmethod
    def _encode(packet: dict) -> bytes:
        wire = (json.dumps(packet, allow_nan=False, separators=(",", ":")) + "\n").encode()
        if len(wire) > MAX_FRAME:
            raise ValueError("custody frame exceeds its byte budget")
        return wire

    def _check_open(self) -> None:
        if self._closed:
            raise ConnectionError("custody attachment closed; effects may be unknown")

    async def write(self, packet: dict) -> None:
        """Publish a receipt without interleaving another frame."""
        wire = self._encode(packet)
        async with self._write_lock:
            self._check_open()
            self.writer.write(wire)
            if "receipt" in packet:
                # The peer can reuse this slot before drain or task callbacks run.
                self._receiving.discard(asyncio.current_task())
            await self.writer.drain()

    def _settled(self, future) -> None:
        self._credits.release()
        if not future.cancelled():
            future.exception()

    async def request(self, operation: str, **data):
        """Keep sequence allocation and publication atomic; never resend an effect."""
        if not isinstance(operation, str):
            raise ValueError("custody operation must be a string")
        await self._credits.acquire()
        published = False
        try:
            async with self._write_lock:
                self._check_open()
                sequence = self._counter + 1
                if sequence > MAX_SEQUENCE:
                    raise ValueError("custody sequence exhausted; operation not published")
                wire = self._encode(
                    {"sequence": sequence, "operation": operation, "data": data},
                )
                future = asyncio.get_running_loop().create_future()
                future.add_done_callback(self._settled)
                self._pending[sequence] = future
                self._counter = sequence
                published = True
                try:
                    self.writer.write(wire)
                    await self.writer.drain()
                except asyncio.CancelledError:
                    # Bytes may already be accepted. Retain the receipt and credit
                    # until it settles, rather than opening an unbounded orphan window.
                    raise
                except BaseException:
                    self._closed = True
                    self.writer.close()
                    raise
            return await asyncio.shield(future)
        finally:
            if not published:
                self._credits.release()

    async def _dispatch(self, packet: dict) -> None:
        sequence = packet["sequence"]
        try:
            result = await self.handler(packet["operation"], packet["data"])
            reply = {"receipt": sequence, "result": result}
            self._encode(reply)
        except Exception as exc:
            reply = {"receipt": sequence, "error": str(exc)}
        await self.write(reply)

    def _finished(self, task) -> None:
        self._tasks.discard(task)
        self._receiving.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self._closed = True
            self.writer.close()

    def _receipt(self, packet: dict) -> None:
        sequence = packet["receipt"]
        if (
            type(sequence) is not int
            or not 0 < sequence <= MAX_SEQUENCE
            or set(packet) not in ({"receipt", "result"}, {"receipt", "error"})
            or ("error" in packet and not isinstance(packet["error"], str))
        ):
            raise ValueError("invalid custody receipt")
        future = self._pending.pop(sequence, None)
        if future is None or future.done():
            raise ValueError("unexpected or duplicate custody receipt")
        if "error" in packet:
            future.set_exception(RuntimeError(packet["error"]))
        else:
            future.set_result(packet["result"])

    async def run(self) -> None:
        """Read receipts while handlers await operations in the other direction."""
        try:
            while line := await self.reader.readline():
                if self._closed:
                    break
                if len(line) > MAX_FRAME or not line.endswith(b"\n"):
                    raise ValueError("invalid or incomplete custody frame")
                packet = json.loads(line)
                if not isinstance(packet, dict):
                    raise ValueError("custody frame must be an object")
                if "receipt" in packet:
                    self._receipt(packet)
                    continue
                if (
                    set(packet) != {"sequence", "operation", "data"}
                    or type(packet["sequence"]) is not int
                    or not 0 < packet["sequence"] <= MAX_SEQUENCE
                    or packet["sequence"] != self._received + 1
                    or not isinstance(packet["operation"], str)
                    or not isinstance(packet["data"], dict)
                ):
                    raise ValueError("invalid or out-of-order custody frame")
                if len(self._receiving) >= MAX_PENDING:
                    raise ValueError("custody peer exceeded its outstanding operation budget")
                self._received = packet["sequence"]
                task = asyncio.create_task(self._dispatch(packet))
                self._tasks.add(task)
                self._receiving.add(task)
                task.add_done_callback(self._finished)
        finally:
            self._closed = True
            self.writer.close()
            for future in self._pending.values():
                if not future.done():
                    future.set_exception(
                        ConnectionError(
                            "custody attachment lost; accepted effects are unknown; no replay",
                        ),
                    )
            self._pending.clear()
            tasks = tuple(self._tasks)
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self) -> None:
        """Close this attachment, not the retained upstream session it addresses."""
        self._closed = True
        self.writer.close()
        with contextlib.suppress(Exception):
            await self.writer.wait_closed()
