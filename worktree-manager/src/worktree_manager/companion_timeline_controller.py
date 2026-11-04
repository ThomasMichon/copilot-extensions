"""Caller-owned normalized observations; no provider discovery or actor control."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator, Callable, Mapping
from functools import partial
from typing import Protocol

from .companion_timeline_contract import (
    HistoryPage, LiveDelta, Provider, SourceIdentity, SourcePosition, SourceToken,
    TimelineContractError, TimelineGap, TimelineSnapshot,
)

Observation = TimelineSnapshot | LiveDelta | TimelineGap
logger = logging.getLogger(__name__)


class TimelineSourceError(RuntimeError):
    """An admitted source failed without changing another source's authority."""


class TimelineCleanupError(TimelineSourceError):
    """An adapter could not retire an owned resource; replacement must stay fenced."""


class TimelineSource(Protocol):
    """Adapters report read failures as source errors, retirement failures as cleanup errors."""

    def observe(
        self, token: SourceToken, *, stop: asyncio.Event,
    ) -> AsyncGenerator[Observation, None]: ...

    async def history(
        self, token: SourceToken, before: SourcePosition, *, stop: asyncio.Event,
    ) -> HistoryPage | TimelineGap: ...


class TimelineController:
    """At most three held source readers and one demand page per source.

    Selection replacement waits for the old adapters' cancellation cleanup.
    The internal revision fences callbacks even if a caller reuses a source token.
    Neither cancellation nor a received record settles an actor or resource.
    """

    def __init__(
        self,
        sources: Mapping[Provider, TimelineSource],
        *,
        observation: Callable[[Observation], None],
        page: Callable[[HistoryPage], None],
        error: Callable[[SourceToken, str], None],
        gap: Callable[[TimelineGap], None] | None = None,
    ) -> None:
        if any(not isinstance(provider, Provider) for provider in sources):
            raise TimelineContractError("timeline adapters require exact provider keys")
        self._sources = dict(sources)
        self._observation = observation
        self._page = page
        self._error = error
        self._gap = gap
        self._selected: dict[Provider, SourceToken] = {}
        self._readers: dict[Provider, asyncio.Task[None]] = {}
        self._pages: dict[Provider, asyncio.Task[None]] = {}
        self._reader_stops: dict[Provider, asyncio.Event] = {}
        self._page_stops: dict[Provider, asyncio.Event] = {}
        self._revision = 0
        self._closed = False
        self._selection_lock = asyncio.Lock()
        self._teardown: asyncio.Future[list[None | BaseException]] | None = None
        self._terminal_failures: tuple[tuple[SourceIdentity | None, TimelineCleanupError], ...] = ()

    @property
    def reader_count(self) -> int:
        return sum(not task.done() for task in self._readers.values())

    @property
    def selected_tokens(self) -> tuple[SourceToken, ...]:
        """Current bindings only, distinct from retained historical source scopes."""
        return tuple(self._selected.values())

    @property
    def page_count(self) -> int:
        return sum(not task.done() for task in self._pages.values())

    def _current(self, token: SourceToken, revision: int) -> bool:
        return (
            not self._closed and revision == self._revision
            and self._selected.get(token.identity.provider) == token
        )

    def _record_terminal(self, token: SourceToken | None, failure: TimelineCleanupError) -> None:
        if not any(known is failure for _identity, known in self._terminal_failures):
            identity = token.identity if token is not None else None
            self._terminal_failures += ((identity, failure),)
        if token is not None:
            selected = self._selected.get(token.identity.provider)
            if selected is not None and selected.identity == token.identity:
                for stops in (self._reader_stops, self._page_stops):
                    stop = stops.get(token.identity.provider)
                    if stop is not None:
                        stop.set()

    def _source_failure(self, token: SourceToken) -> TimelineCleanupError | None:
        return next((
            failure for identity, failure in self._terminal_failures
            if identity is None or identity == token.identity
        ), None)

    def _harvest_terminal(self, token: SourceToken) -> None:
        for tasks in (self._readers, self._pages):
            task = tasks.get(token.identity.provider)
            if task is not None and task.done() and not task.cancelled():
                failure = task.exception()
                if isinstance(failure, TimelineCleanupError):
                    self._record_terminal(token, failure)

    async def select(self, tokens: tuple[SourceToken, ...]) -> None:
        async with self._selection_lock:
            await self._select(tokens)

    async def _select(self, tokens: tuple[SourceToken, ...]) -> None:
        if self._closed:
            raise TimelineSourceError("timeline controller is closed")
        if (
            not isinstance(tokens, tuple) or len(tokens) > len(Provider)
            or any(not isinstance(token, SourceToken) for token in tokens)
        ):
            raise TimelineContractError("timeline selection exceeds its typed source bound")
        selected = {token.identity.provider: token for token in tokens}
        if len(selected) != len(tokens):
            raise TimelineContractError("timeline selection repeats a provider")
        await self._stop()
        if self._closed:
            raise TimelineSourceError("timeline controller closed during selection replacement")
        self._selected = selected
        revision = self._revision
        for provider, token in selected.items():
            source = self._sources.get(provider)
            if source is None:
                self._error(token, "Unsupported: no admitted timeline adapter for this provider.")
            else:
                stop = asyncio.Event()
                self._reader_stops[provider] = stop
                task = asyncio.create_task(
                    self._observe(source, token, revision, stop),
                    name=f"sidecar-observer:{provider.value}",
                )
                self._readers[provider] = task
                task.add_done_callback(
                    partial(self._report_failure, token=token, revision=revision)
                )

    def _report_failure(
        self, task: asyncio.Task[None], token: SourceToken, revision: int,
    ) -> None:
        if task.cancelled():
            return
        failure = task.exception()
        if isinstance(failure, TimelineCleanupError):
            self._record_terminal(token, failure)
        if failure is not None and self._current(token, revision):
            self._error(token, f"Timeline adapter failed: {failure}")

    async def _observe(
        self, source: TimelineSource, token: SourceToken, revision: int, stop: asyncio.Event,
    ) -> None:
        stream = None
        try:
            stream = source.observe(token, stop=stop)
            async for value in stream:
                if not self._current(token, revision) or self._source_failure(token) is not None:
                    return
                if not isinstance(value, (TimelineSnapshot, LiveDelta, TimelineGap)) or value.token != token:
                    raise TimelineContractError("source returned a wrong-selection observation")
                if isinstance(value, TimelineGap):
                    self._deliver_gap(value)
                else:
                    self._observation(value)
            if self._current(token, revision) and self._source_failure(token) is None:
                self._error(token, "Unavailable: timeline source ended; explicit reconciliation required.")
        except TimelineCleanupError as exc:
            self._record_terminal(token, exc)
            raise
        except (TimelineSourceError, TimelineContractError, OSError, TimeoutError) as exc:
            if self._current(token, revision):
                self._error(token, f"Timeline source error: {exc}")
        finally:
            if stream is not None:
                try:
                    await stream.aclose()
                except TimelineCleanupError as exc:
                    self._record_terminal(token, exc)
                    raise
                except (OSError, ValueError, RuntimeError) as exc:
                    failure = TimelineCleanupError(f"timeline stream retirement failed: {exc}")
                    self._record_terminal(token, failure)
                    raise failure from exc

    def request_history(self, token: SourceToken, before: SourcePosition) -> bool:
        """Refuse repeated demand while its same-source page is still running."""
        if not isinstance(token, SourceToken) or not isinstance(before, SourcePosition):
            raise TimelineContractError("history demand requires typed source and cursor")
        if not before.matches(token.identity):
            raise TimelineContractError("history demand cursor belongs to another source")
        if not self._current(token, self._revision):
            raise TimelineSourceError("history demand belongs to an inactive selection")
        provider = token.identity.provider
        self._harvest_terminal(token)
        terminal = self._source_failure(token)
        if terminal is not None:
            raise TimelineCleanupError("source read admission is fenced by terminal retirement failure") from terminal
        previous = self._pages.get(provider)
        if previous is not None and not previous.done():
            return False
        source = self._sources.get(provider)
        if source is None:
            self._error(token, "Unsupported: no admitted timeline history adapter.")
            return False
        stop = asyncio.Event()
        self._page_stops[provider] = stop
        task = asyncio.create_task(
            self._history(source, token, before, self._revision, stop),
            name=f"sidecar-history:{provider.value}",
        )
        self._pages[provider] = task
        revision = self._revision
        task.add_done_callback(
            partial(self._report_failure, token=token, revision=revision)
        )
        return True

    def _deliver_gap(self, value: TimelineGap) -> None:
        if self._gap is None:
            self._error(value.token, f"History gap: {value.reason}: {value.details}")
        else:
            self._gap(value)

    async def _history(
        self, source: TimelineSource, token: SourceToken,
        before: SourcePosition, revision: int, stop: asyncio.Event,
    ) -> None:
        try:
            value = await source.history(token, before, stop=stop)
            if not self._current(token, revision) or self._source_failure(token) is not None:
                return
            if not isinstance(value, (HistoryPage, TimelineGap)) or value.token != token or value.before != before:
                raise TimelineContractError("history result does not match the requested source boundary")
            if isinstance(value, TimelineGap):
                self._deliver_gap(value)
            else:
                self._page(value)
        except TimelineCleanupError as exc:
            self._record_terminal(token, exc)
            raise
        except (TimelineSourceError, TimelineContractError, OSError, TimeoutError) as exc:
            if self._current(token, revision):
                self._error(token, f"Timeline history error: {exc}")

    async def _stop(self) -> None:
        if self._teardown is None:
            for token in tuple(self._selected.values()):
                self._harvest_terminal(token)
            self._revision += 1
            self._selected.clear()
            tasks = (*self._pages.values(), *self._readers.values())
            for stop in (*self._page_stops.values(), *self._reader_stops.values()):
                stop.set()
            self._teardown = asyncio.gather(*tasks, return_exceptions=True)
        teardown = self._teardown
        cancelled: asyncio.CancelledError | None = None
        while True:
            try:
                outcomes = await asyncio.shield(teardown)
                break
            except asyncio.CancelledError as exc:
                if teardown.cancelled():
                    failure = next((
                        known for identity, known in self._terminal_failures if identity is None
                    ), None)
                    if failure is None:
                        failure = TimelineCleanupError("timeline adapter teardown was interrupted")
                        self._record_terminal(None, failure)
                    raise failure from exc
                cancelled = exc
        self._pages.clear()
        self._readers.clear()
        self._page_stops.clear()
        self._reader_stops.clear()
        self._teardown = None
        for outcome in outcomes:
            if isinstance(outcome, TimelineCleanupError):
                self._record_terminal(None, outcome)
            elif isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                logger.error("Retired timeline adapter failed outside resource cleanup: %s", outcome)
        if self._terminal_failures:
            failure = self._terminal_failures[0][1]
            raise TimelineCleanupError(f"timeline adapter cleanup failed: {failure}") from failure
        if cancelled is not None:
            raise cancelled

    async def close(self) -> None:
        self._closed = True
        async with self._selection_lock:
            await self._stop()
