"""Caller-owned normalized observations; no provider discovery or actor control."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Mapping
from functools import partial
from typing import Protocol

from .companion_timeline_contract import (
    HistoryPage, LiveDelta, Provider, SourcePosition, SourceToken,
    TimelineContractError, TimelineSnapshot,
)

Observation = TimelineSnapshot | LiveDelta


class TimelineSourceError(RuntimeError):
    """An admitted source failed without changing another source's authority."""


class TimelineSource(Protocol):
    """Adapters stop reads cooperatively and await their owned bounded cleanup."""

    def observe(
        self, token: SourceToken, *, stop: asyncio.Event,
    ) -> AsyncGenerator[Observation, None]: ...

    async def history(
        self, token: SourceToken, before: SourcePosition, *, stop: asyncio.Event,
    ) -> HistoryPage: ...


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
    ) -> None:
        if any(not isinstance(provider, Provider) for provider in sources):
            raise TimelineContractError("timeline adapters require exact provider keys")
        self._sources = dict(sources)
        self._observation = observation
        self._page = page
        self._error = error
        self._selected: dict[Provider, SourceToken] = {}
        self._readers: dict[Provider, asyncio.Task[None]] = {}
        self._pages: dict[Provider, asyncio.Task[None]] = {}
        self._reader_stops: dict[Provider, asyncio.Event] = {}
        self._page_stops: dict[Provider, asyncio.Event] = {}
        self._revision = 0
        self._closed = False
        self._selection_lock = asyncio.Lock()
        self._teardown: asyncio.Future[list[None | BaseException]] | None = None

    @property
    def reader_count(self) -> int:
        return sum(not task.done() for task in self._readers.values())

    @property
    def page_count(self) -> int:
        return sum(not task.done() for task in self._pages.values())

    def _current(self, token: SourceToken, revision: int) -> bool:
        return (
            not self._closed and revision == self._revision
            and self._selected.get(token.identity.provider) == token
        )

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
        if failure is not None and self._current(token, revision):
            self._error(token, f"Timeline adapter failed: {failure}")

    async def _observe(
        self, source: TimelineSource, token: SourceToken, revision: int, stop: asyncio.Event,
    ) -> None:
        stream = source.observe(token, stop=stop)
        try:
            async for value in stream:
                if not self._current(token, revision):
                    return
                if not isinstance(value, (TimelineSnapshot, LiveDelta)) or value.token != token:
                    raise TimelineContractError("source returned a wrong-selection observation")
                self._observation(value)
            if self._current(token, revision):
                self._error(token, "Unavailable: timeline source ended; explicit reconciliation required.")
        except (TimelineSourceError, TimelineContractError, OSError, TimeoutError) as exc:
            if self._current(token, revision):
                self._error(token, f"Timeline source error: {exc}")
            else:
                raise
        finally:
            await stream.aclose()

    def request_history(self, token: SourceToken, before: SourcePosition) -> bool:
        """Refuse repeated demand while its same-source page is still running."""
        if not isinstance(token, SourceToken) or not isinstance(before, SourcePosition):
            raise TimelineContractError("history demand requires typed source and cursor")
        if not before.matches(token.identity):
            raise TimelineContractError("history demand cursor belongs to another source")
        if not self._current(token, self._revision):
            raise TimelineSourceError("history demand belongs to an inactive selection")
        provider = token.identity.provider
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

    async def _history(
        self, source: TimelineSource, token: SourceToken,
        before: SourcePosition, revision: int, stop: asyncio.Event,
    ) -> None:
        try:
            value = await source.history(token, before, stop=stop)
            if not self._current(token, revision):
                return
            if not isinstance(value, HistoryPage) or value.token != token or value.before != before:
                raise TimelineContractError("history result does not match the requested source boundary")
            self._page(value)
        except (TimelineSourceError, TimelineContractError, OSError, TimeoutError) as exc:
            if self._current(token, revision):
                self._error(token, f"Timeline history error: {exc}")
            else:
                raise

    async def _stop(self) -> None:
        if self._teardown is None:
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
                    raise TimelineSourceError("timeline adapter teardown was interrupted") from exc
                cancelled = exc
        self._pages.clear()
        self._readers.clear()
        self._page_stops.clear()
        self._reader_stops.clear()
        self._teardown = None
        for outcome in outcomes:
            if isinstance(outcome, BaseException) and not isinstance(outcome, asyncio.CancelledError):
                raise TimelineSourceError(f"timeline adapter cleanup failed: {outcome}") from outcome
        if cancelled is not None:
            raise cancelled

    async def close(self) -> None:
        self._closed = True
        async with self._selection_lock:
            await self._stop()
