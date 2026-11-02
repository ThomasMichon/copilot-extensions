"""Human operation framing; no durable events, subprocesses or wall-time timers."""

from __future__ import annotations

import codecs
import contextvars
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from types import TracebackType
from typing import Protocol, cast

from .redact import redact

_CURRENT: contextvars.ContextVar[HumanOperation | None] = contextvars.ContextVar(
    "update_human_operation", default=None,
)
_WRITE_LOCK = threading.Lock()
_FRAMED = re.compile(
    r"^(\[\d{2}:\d{2}:\d{2}\.\d{3}Z\] )(.+)"
    r"( \[(?:elapsed|duration) \d+\.\d+s\])$"
)
_STATES = frozenset({
    "starting", "running", "done", "current", "skipped", "disabled", "warning", "failed",
})
_LEGACY_PROGRESS = re.compile(r"^[ \t]*(?:\[(OK|SKIP|WARN|FAIL)\]|(\.\.\.))[ \t]+(.*)$")
_LEGACY_STATES = {
    "OK": "done", "SKIP": "skipped", "WARN": "warning", "FAIL": "failed", "...": "running",
}


def _available(fd: int) -> int:
    """Bytes already written, without waiting for detached descendants' EOF."""
    if sys.platform == "win32":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        peek = kernel.PeekNamedPipe
        peek.argtypes = [
            wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
            wintypes.LPVOID, ctypes.POINTER(wintypes.DWORD), wintypes.LPVOID,
        ]
        peek.restype = wintypes.BOOL
        available = wintypes.DWORD()
        if not peek(msvcrt.get_osfhandle(fd), None, 0, None, ctypes.byref(available), None):
            error = ctypes.get_last_error()
            if error == 109:  # ERROR_BROKEN_PIPE
                return 0
            raise ctypes.WinError(error)
        return available.value
    import array
    import fcntl
    import termios

    count = array.array("i", [0])
    fcntl.ioctl(fd, termios.FIONREAD, count, True)
    return count[0]


def current_operation() -> HumanOperation | None:
    """Return the explicit human scope, never infer one from protocol output."""
    return _CURRENT.get()


class HumanStream(Protocol):
    def write(self, text: str) -> object: ...

    def flush(self) -> object: ...


class HumanOperation:
    """Frame lines with UTC correlation time and a containing monotonic scope.

    Nesting inherits the sink and clocks. Only an operation's completion has a
    duration; arbitrary progress/child lines report elapsed time in that scope.
    """

    path: str
    started: float

    def __init__(
        self, label: str, *, stream: HumanStream | None = None,
        wall: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.label = re.sub(r"\s+", "-", redact(label).strip())
        self.stream = stream
        self.wall = wall
        self.monotonic = monotonic
        self._token: contextvars.Token[HumanOperation | None] | None = None
        self._completed = False

    def __enter__(self) -> HumanOperation:
        parent = current_operation()
        self.path = f"{parent.path}/{self.label}" if parent else self.label
        if self.stream is None:
            self.stream = parent.stream if parent else sys.stderr
        if self.wall is None:
            self.wall = parent.wall if parent else lambda: datetime.now(timezone.utc)
        if self.monotonic is None:
            self.monotonic = parent.monotonic if parent else time.monotonic
        self.started = cast(Callable[[], float], self.monotonic)()
        self._token = _CURRENT.set(self)
        try:
            self.progress("starting")
        except BaseException:
            _CURRENT.reset(self._token)
            self._token = None
            raise
        return self

    def _write(
        self, text: str, *, completed: bool = False, elapsed: float | None = None,
        state: str | None = None,
    ) -> None:
        # Entering the scope resolves the inherited sink and clocks.
        message = str(text)
        safe = redact(message)
        originals = message.splitlines() or [""]
        lines = safe.splitlines() or [""]
        # Reuse envelopes only when redaction retained line alignment. Unusual
        # separators consumed by a redaction rule must never recover raw text.
        if len(originals) != len(lines):
            originals = lines
        for original, line in zip(originals, lines, strict=True):
            framed = _FRAMED.fullmatch(original)
            if framed is not None and line.startswith(framed[1]):
                body = line[len(framed[1]):]
                if body.endswith(framed[3]):
                    body = body[:-len(framed[3])]
                rendered = framed[1] + body + framed[3]
            else:
                line_state = state
                if not completed and line_state is None:
                    legacy = _LEGACY_PROGRESS.fullmatch(line)
                    if legacy is not None:
                        line_state = _LEGACY_STATES[legacy[1] or legacy[2]]
                        line = legacy[3]
                if line_state is not None:
                    line = f"{line_state} {line}".rstrip()
                now = cast(Callable[[], datetime], self.wall)().astimezone(timezone.utc)
                stamp = now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
                seconds = (
                    cast(Callable[[], float], self.monotonic)() - self.started
                    if elapsed is None else elapsed
                )
                kind = "duration" if completed else "elapsed"
                body = f"{self.path} {line}" if line else self.path
                rendered = f"[{stamp}] {body} [{kind} {seconds:.1f}s]"
            with _WRITE_LOCK:
                cast(HumanStream, self.stream).write(rendered + "\n")
                cast(HumanStream, self.stream).flush()

    def progress(self, message: str, *, state: str | None = None) -> None:
        if state is not None and state not in _STATES:
            raise ValueError(f"unknown human output state: {state}")
        self._write(message, state=state)

    def complete(self, state: str = "done", message: str = "", *, elapsed: float | None = None) -> None:
        if state not in _STATES - {"starting", "running"}:
            raise ValueError(f"invalid completion state: {state}")
        if not self._completed:
            elapsed = (
                cast(Callable[[], float], self.monotonic)() - self.started
                if elapsed is None else elapsed
            )
            self._write(f"{state} {message}".rstrip(), completed=True, elapsed=elapsed)
            self._completed = True

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        try:
            if exc is not None:
                self.complete("failed", f"{type(exc).__name__}: {exc}")
            else:
                self.complete()
        finally:
            if self._token is not None:
                _CURRENT.reset(self._token)
                self._token = None


class HumanLineBuffer:
    """Retain only credential prefixes needing a following physical line."""

    def __init__(self, emit: Callable[[str], None]) -> None:
        self.emit = emit
        self.pending: str | None = None

    def feed(self, line: str) -> None:
        message = line if self.pending is None else self.pending + "\n" + line
        probe = "human_output_continuation_probe"
        suffixes = ("\n" + probe, "\n: " + probe, "\n= " + probe)
        if any(not redact(message + suffix).endswith(probe) for suffix in suffixes):
            self.pending = message
        else:
            self.emit(message)
            self.pending = None

    def flush(self) -> None:
        if self.pending is not None:
            self.emit(self.pending)
            self.pending = None


@contextmanager
def native_output() -> Iterator[int | None]:
    """Relay a known-human child's merged output live in the existing Python.

    The caller still owns execution, timeout and exit handling. No interpreter
    is started or pinned for rendering. With no human scope the inherited
    native streams are unchanged.
    """
    operation = current_operation()
    if operation is None:
        yield None
        return
    reader_fd, writer_fd = os.pipe()
    stopped = threading.Event()
    errors: list[BaseException] = []

    def relay() -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        lines = HumanLineBuffer(operation.progress)

        def consume(data: bytes, *, final: bool = False) -> None:
            nonlocal pending
            pending += decoder.decode(data, final=final)
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                lines.feed(line.removesuffix("\r"))
            if final and pending:
                lines.feed(pending)
            if final:
                lines.flush()

        try:
            while not stopped.is_set():
                size = _available(reader_fd)
                if size:
                    consume(os.read(reader_fd, min(size, 65536)))
                else:
                    stopped.wait(0.05)
            # The direct child has returned. Drain its already-written bytes,
            # not an unbounded stream from a detached service inheriting stdout.
            remaining = _available(reader_fd)
            while remaining:
                data = os.read(reader_fd, min(remaining, 65536))
                if not data:
                    break
                consume(data)
                remaining -= len(data)
            consume(b"", final=True)
        except BaseException as exc:
            errors.append(exc)
        finally:
            os.close(reader_fd)

    thread = threading.Thread(target=relay, name="update-human-output", daemon=True)
    try:
        thread.start()
    except BaseException:
        os.close(reader_fd)
        os.close(writer_fd)
        raise
    failed = False
    try:
        yield writer_fd
    except BaseException:
        failed = True
        raise
    finally:
        stopped.set()
        os.close(writer_fd)
        thread.join()
        if errors and not failed:
            raise errors[0]
