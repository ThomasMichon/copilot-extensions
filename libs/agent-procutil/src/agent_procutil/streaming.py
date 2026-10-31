"""Incremental capture for processes whose lifecycle remains caller-owned."""

from __future__ import annotations

import math
import os
import queue
import select
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncio


class SyncProcessOutput:
    """Drain captured pipes without line buffering or taking process custody.

    Native ``output_fds`` remain live through finite, cancellable byte relays.
    Legacy callbacks run only through ``deliver`` after ``finish``; their
    bytes share the ordinary captured-output storage. After timeout or output
    failure, retire the owned producer before ``finish``. No other reader may
    consume these pipes.
    """

    def __init__(
        self, process: subprocess.Popen, *,
        on_output: Callable[[str, bytes], None] | None = None,
        output_fds: Mapping[str, int] | None = None, capture: bool = True,
    ) -> None:
        from ._output_relay import ByteRelay

        self.process = process
        self.on_output = on_output
        self._notified = False
        self._finished = False
        self._relays = {}
        try:
            for name, descriptor in (output_fds or {}).items():
                if name not in ("stdout", "stderr"):
                    raise ValueError("output relay requires stdout or stderr")
                self._relays[name] = ByteRelay(descriptor)
        except BaseException:
            self._abort_relays()
            raise
        self._capture = capture
        self._relay_failed = False
        self._pending = None
        self._events: queue.Queue = queue.Queue(maxsize=64)
        self._output = {"stdout": bytearray(), "stderr": bytearray()}
        self._last_output = time.monotonic()
        self._lock = threading.Lock()
        self._stop_at_idle = threading.Event()
        self._readers = []
        self._startup_errors = []
        self._done = set()
        self._streams = {}
        for name in ("stdout", "stderr"):
            stream = getattr(process, name)
            if stream is None:
                continue
            self._streams[name] = stream
            thread = threading.Thread(
                target=self._read, args=(name, stream), name=f"process-{name}", daemon=True,
            )
            try:
                thread.start()
            except BaseException as error:
                self._startup_errors.append(error)
                self._done.add(name)
            else:
                self._readers.append(thread)
        if not self._streams:
            self._abort_relays()
            raise ValueError("progress capture requires at least one captured output stream")

    def _read(self, name, stream) -> None:
        try:
            binary = getattr(stream, "buffer", stream)
            read = getattr(binary, "read1", binary.read)
            while True:
                if os.name != "nt":
                    ready, _, _ = select.select(
                        [binary], [], [], 0 if self._stop_at_idle.is_set() else .1,
                    )
                    if not ready:
                        if self._stop_at_idle.is_set():
                            return
                        continue
                chunk = read(4096)
                if not chunk:
                    return
                with self._lock:
                    self._last_output = time.monotonic()
                self._events.put((name, chunk))
        except BaseException as error:
            self._events.put((name, error))
        finally:
            self._events.put((name, None))

    def _deliver(self, name, chunk) -> bool:
        if chunk is None:
            self._done.add(name)
        elif isinstance(chunk, BaseException):
            raise chunk
        else:
            relay = self._relays.get(name)
            if not self._relay_failed and relay is not None and not relay.offer(chunk):
                return False
            if self._capture or self.on_output is not None:
                self._output[name].extend(chunk)
        return True

    def _abort_relays(self) -> None:
        failure = None
        for relay in self._relays.values():
            try:
                relay.abort(10)
            except BaseException as error:
                if failure is None:
                    failure = error
                else:
                    failure.add_note(str(error))
        if failure is not None:
            raise failure

    def wait(
        self, timeout: float | None, *, deadline: float | None = None,
        on_wait: Callable[[], None] | None = None, wait_for_eof: bool = True,
        inactivity_start: float | None = None,
    ) -> None:
        """Renew only from newly read bytes; an explicit absolute cap stays absolute."""
        try:
            if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
                raise ValueError("process inactivity timeout must be finite and positive")
            if deadline is not None and not math.isfinite(deadline):
                raise ValueError("process absolute deadline must be finite")
            if inactivity_start is not None and not math.isfinite(inactivity_start):
                raise ValueError("process inactivity phase start must be finite")
            if self._startup_errors:
                raise self._startup_errors.pop(0)
            while True:
                for _ in range(64):
                    try:
                        event = self._pending or self._events.get_nowait()
                    except queue.Empty:
                        break
                    if not self._deliver(*event):
                        self._pending = event
                        break
                    self._pending = None
                if not self._relay_failed:
                    for relay in self._relays.values():
                        relay.check()
                if on_wait is not None:
                    on_wait()
                if self.process.poll() is not None and (
                    not wait_for_eof or len(self._done) == len(self._streams)
                ):
                    if not wait_for_eof or self._relay_failed:
                        return
                    ended = [relay.end() for relay in self._relays.values()]
                    if all(ended):
                        for relay in self._relays.values():
                            relay.close()
                        return
                with self._lock:
                    last_progress = self._last_output
                if inactivity_start is not None:
                    last_progress = max(last_progress, inactivity_start)
                if not self._relay_failed:
                    last_progress = max(
                        last_progress,
                        *(relay.last_progress for relay in self._relays.values()),
                    ) if self._relays else last_progress
                expires = None if timeout is None else last_progress + timeout
                if deadline is not None:
                    expires = deadline if expires is None else min(expires, deadline)
                remaining = None if expires is None else expires - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise subprocess.TimeoutExpired(self.process.args, timeout, *self.output())
                if self._pending is not None:
                    time.sleep(.01 if remaining is None else min(.01, remaining))
                    continue
                try:
                    event = self._events.get(timeout=.1 if remaining is None else min(.1, remaining))
                except queue.Empty:
                    continue
                if not self._deliver(*event):
                    self._pending = event
        except BaseException as error:
            self._relay_failed = True
            try:
                self._abort_relays()
            except BaseException as cleanup_error:
                cleanup_error.add_note(f"relay cleanup failed after {type(error).__name__}: {error}")
                raise cleanup_error from error
            raise

    def output(self) -> tuple[str | bytes | None, str | bytes | None]:
        values = []
        for name in ("stdout", "stderr"):
            stream = self._streams.get(name)
            value = bytes(self._output[name]) if stream is not None else None
            if value is not None and self.process.text_mode:
                value = value.decode(stream.encoding, stream.errors).replace("\r\n", "\n").replace("\r", "\n")
            values.append(value)
        return tuple(values)

    def finish(
        self, timeout: float, *, stop_at_idle: bool = False,
    ) -> tuple[str | bytes | None, str | bytes | None]:
        if stop_at_idle:
            if os.name == "nt":
                raise ValueError("idle pipe retirement requires POSIX selectable pipes")
            self._stop_at_idle.set()
        failure = None
        deadline = time.monotonic() + timeout
        try:
            if timeout > 0:
                self.wait(timeout, inactivity_start=time.monotonic())
                deadline = time.monotonic() + timeout
            else:
                self.wait(None, deadline=deadline)
        except BaseException as error:
            failure = error
            # The caller has retired the producer. Finish its bounded reader
            # queue even when the sink failed or its delivery deadline expired.
            self.wait(None, deadline=time.monotonic() + 10)
            deadline = time.monotonic() + 10
        for reader in self._readers:
            reader.join(max(0, deadline - time.monotonic()))
            if reader.is_alive():
                raise TimeoutError("captured output reader did not settle")
        for stream in self._streams.values():
            stream.close()
        self._finished = True
        if failure is not None:
            raise failure
        return self.output()

    def deliver(self) -> None:
        """Invoke legacy observers only after the caller has settled its producer."""
        if not self._finished:
            raise RuntimeError("legacy output delivery requires settled capture")
        if self.on_output is not None and not self._notified:
            self._notified = True
            for name, data in self._output.items():
                for offset in range(0, len(data), 4096):
                    self.on_output(name, bytes(data[offset:offset + 4096]))


async def communicate_progress(
    process: asyncio.subprocess.Process,
    *,
    input_bytes: bytes | None = None,
    on_output: Callable[[str, bytes], None],
) -> tuple[bytes, bytes]:
    import asyncio

    if process.stdout is None or process.stderr is None:
        raise ValueError("progress capture requires both captured output streams")
    if input_bytes is not None and process.stdin is None:
        raise ValueError("progress capture input requires a stdin pipe")

    async def collect(name: str, stream: asyncio.StreamReader) -> bytes:
        chunks: list[bytes] = []
        while chunk := await stream.read(4096):
            on_output(name, chunk)
            chunks.append(chunk)
        return b"".join(chunks)

    async def write_input() -> None:
        writer = process.stdin
        if writer is None:
            return
        try:
            if input_bytes:
                writer.write(input_bytes)
                await writer.drain()
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            writer.close()

    tasks = [
        asyncio.create_task(collect("stdout", process.stdout)),
        asyncio.create_task(collect("stderr", process.stderr)),
        asyncio.create_task(write_input()),
    ]
    try:
        stdout, stderr, _ = await asyncio.gather(*tasks)
        await process.wait()
        return stdout, stderr
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
