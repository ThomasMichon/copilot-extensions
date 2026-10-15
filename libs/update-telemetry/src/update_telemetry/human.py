"""Human operation framing; no durable events, subprocesses or wall-time timers."""

from __future__ import annotations

import contextvars
import codecs
import os
import re
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

from .redact import redact

_CURRENT = contextvars.ContextVar("update_human_operation", default=None)
_WRITE_LOCK = threading.Lock()
_FRAMED = re.compile(
    r"^(\[\d{2}:\d{2}:\d{2}\.\d{3}Z\] )(.+)"
    r"( \[(?:elapsed|duration) \d+\.\d+s\])$"
)
_STATES = frozenset({
    "starting", "running", "done", "current", "skipped", "disabled", "warning", "failed",
})


def _available(fd: int) -> int:
    """Bytes already written, without waiting for detached descendants' EOF."""
    if os.name == "nt":
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
        count = wintypes.DWORD()
        if not peek(msvcrt.get_osfhandle(fd), None, 0, None, ctypes.byref(count), None):
            error = ctypes.get_last_error()
            if error == 109:  # ERROR_BROKEN_PIPE
                return 0
            raise ctypes.WinError(error)
        return count.value
    import array
    import fcntl
    import termios

    count = array.array("i", [0])
    fcntl.ioctl(fd, termios.FIONREAD, count, True)
    return count[0]


def current_operation():
    """Return the explicit human scope, never infer one from protocol output."""
    return _CURRENT.get()


class HumanOperation:
    """Frame lines with UTC correlation time and a containing monotonic scope.

    Nesting inherits the sink and clocks. Only an operation's completion has a
    duration; arbitrary progress/child lines report elapsed time in that scope.
    """

    def __init__(self, label: str, *, stream=None, wall=None, monotonic=None):
        self.label = re.sub(r"\s+", "-", redact(label).strip())
        self.stream = stream
        self.wall = wall
        self.monotonic = monotonic
        self._token = None
        self._completed = False

    def __enter__(self):
        parent = current_operation()
        self.path = f"{parent.path}/{self.label}" if parent else self.label
        if self.stream is None:
            self.stream = parent.stream if parent else sys.stderr
        if self.wall is None:
            self.wall = parent.wall if parent else lambda: datetime.now(timezone.utc)
        if self.monotonic is None:
            self.monotonic = parent.monotonic if parent else time.monotonic
        self.started = self.monotonic()
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
                if state is not None:
                    line = f"{state} {line}".rstrip()
                now = self.wall().astimezone(timezone.utc)
                stamp = now.strftime("%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"
                seconds = self.monotonic() - self.started if elapsed is None else elapsed
                kind = "duration" if completed else "elapsed"
                body = f"{self.path} {line}" if line else self.path
                rendered = f"[{stamp}] {body} [{kind} {seconds:.1f}s]"
            with _WRITE_LOCK:
                self.stream.write(rendered + "\n")
                self.stream.flush()

    def progress(self, message: str, *, state: str | None = None) -> None:
        if state is not None and state not in _STATES:
            raise ValueError(f"unknown human output state: {state}")
        self._write(message, state=state)

    def complete(self, state: str = "done", message: str = "") -> None:
        if state not in _STATES - {"starting", "running"}:
            raise ValueError(f"invalid completion state: {state}")
        if not self._completed:
            elapsed = self.monotonic() - self.started
            self._write(f"{state} {message}".rstrip(), completed=True, elapsed=elapsed)
            self._completed = True

    def __exit__(self, exc_type, exc, traceback):
        try:
            if exc is not None:
                self.complete("failed", f"{type(exc).__name__}: {exc}")
            else:
                self.complete()
        finally:
            if self._token is not None:
                _CURRENT.reset(self._token)
                self._token = None


@contextmanager
def native_output():
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
    errors = []

    def relay():
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = ""
        logical = None

        def emit(line: str):
            nonlocal logical
            message = line if logical is None else logical + "\n" + line
            # Ask the existing redactor whether a following token would be
            # sensitive; retain only prefixes requiring that continuation.
            probe = "human_output_continuation_probe"
            suffixes = ("\n" + probe, "\n: " + probe, "\n= " + probe)
            if any(not redact(message + suffix).endswith(probe) for suffix in suffixes):
                logical = message
            else:
                operation.progress(message)
                logical = None

        def consume(data: bytes, *, final: bool = False):
            nonlocal pending, logical
            pending += decoder.decode(data, final=final)
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                emit(line.removesuffix("\r"))
            if final and pending:
                emit(pending)
            if final and logical is not None:
                operation.progress(logical)
                logical = None

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
