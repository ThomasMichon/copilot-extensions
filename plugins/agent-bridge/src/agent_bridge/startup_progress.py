"""Target-owned startup phases carried by the existing session event stream."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import re
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any, TypeVar

_active: ContextVar[StartupProgress | None] = ContextVar("startup_progress", default=None)
WAIT_INTERVAL = 5.0
_launch_phase = re.compile(
    r"(launch/[A-Za-z0-9_/-]+)\s+(starting|running|done|failed)\b"
)
_legacy_phase = re.compile(r"^\[launch\] ([^:\r\n]{1,120}):")
log = logging.getLogger("agent-bridge")
_T = TypeVar("_T")


class StartupProgress:
    def __init__(self, emit: Callable[[str, dict[str, Any]], Any]) -> None:
        self.emit = emit
        self.phase = "resume"
        self.started = time.monotonic()
        self._report_failed = False

    def update(self, phase: str | None = None, state: str = "waiting") -> None:
        if phase is not None:
            self.phase = phase
        try:
            self.emit("startup_progress", {
                "phase": self.phase, "state": state,
                "elapsed_s": round(time.monotonic() - self.started, 3),
            })
        except Exception as exc:
            if not self._report_failed:
                log.warning("Startup progress delivery failed: %s", type(exc).__name__)
                self._report_failed = True

    async def waiting(self) -> None:
        while True:
            await asyncio.sleep(WAIT_INTERVAL)
            self.update()


def current() -> StartupProgress | None:
    return _active.get()


def checkpoint(phase: str, state: str) -> None:
    progress = current()
    if progress is not None:
        progress.update(phase, state)


@contextmanager
def phase(label: str) -> Iterator[None]:
    progress = current()
    if progress is None:
        yield
        return
    previous = progress.phase
    progress.update(label, "started")
    try:
        yield
    except BaseException:
        progress.update(label, "failed")
        raise
    else:
        progress.update(label, "finished")
    finally:
        progress.phase = previous

async def step(label: str, operation: Awaitable[_T]) -> _T:
    with phase(label):
        return await operation


def preparation_output(chunk: bytes, *, parse_phase: bool = True) -> None:
    """Forward contact, not opaque child diagnostics or credentials."""
    progress = current()
    if progress is None or not chunk:
        return
    match = _launch_phase.search(chunk.decode("utf-8", errors="replace")) if parse_phase else None
    if match is not None:
        progress.update(match[1], match[2])
        return
    legacy = _legacy_phase.match(chunk.decode("utf-8", errors="replace")) if parse_phase else None
    if legacy is not None:
        progress.update(legacy[1], "preparing")
        return
    progress.update(state="preparation-output")

@asynccontextmanager
async def lifecycle_lock(lock: asyncio.Lock):
    with phase("lifecycle-lock"):
        await lock.acquire()
    try:
        yield
    finally:
        lock.release()


def observed(method: Callable[..., Any]) -> Callable[..., Any]:
    """Add observation only; lifecycle, cancellation and retry remain the owner's."""
    @functools.wraps(method)
    async def run(manager: Any, session_id: str, *args: Any, **kwargs: Any) -> Any:
        if current() is not None:
            return await method(manager, session_id, *args, **kwargs)
        resolved = manager._resolve_ref(session_id) or session_id
        session = manager._sessions.get(resolved)
        event_log = getattr(session, "event_log", None)
        if event_log is None:
            return await method(manager, session_id, *args, **kwargs)
        progress = StartupProgress(event_log.append)
        token = _active.set(progress)
        progress.update(state="started")
        ticker = asyncio.create_task(progress.waiting())
        try:
            result = await method(manager, session_id, *args, **kwargs)
        except BaseException:
            progress.update(state="failed")
            raise
        else:
            progress.update("ready", "finished")
            return result
        finally:
            ticker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await ticker
            _active.reset(token)
    return run
