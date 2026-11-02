"""Real asyncio reader lifetimes without starting provider hosts or actors."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from dataclasses import replace

import pytest

from worktree_manager.companion_timeline_contract import (
    Availability, Completeness, HistoryPage, LiveDelta, Provider,
    SourceIdentity, SourcePosition, SourceToken, TimelineContractError, TimelineSnapshot,
)
from worktree_manager.companion_timeline_controller import (
    TimelineController, TimelineSourceError,
)


def token(provider: Provider = Provider.WORKTREES, epoch: int = 0) -> SourceToken:
    return SourceToken(SourceIdentity(provider, provider.value, "project", "worktree", "head", "generation"), epoch)


def position(selected: SourceToken) -> SourcePosition:
    return SourcePosition(selected.identity.source_key, selected.identity.generation, "current")


def snapshot(selected: SourceToken) -> TimelineSnapshot:
    return TimelineSnapshot(selected, position(selected), Availability.OBSERVED, Completeness.PARTIAL)


class Source:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[TimelineSnapshot | LiveDelta] = asyncio.Queue()
        self.closed = asyncio.Event()
        self.page_started = asyncio.Event()
        self.page_release = asyncio.Event()
        self.page_cancelled = asyncio.Event()
        self.wrong_page = False

    async def observe(self, selected: SourceToken) -> AsyncGenerator[TimelineSnapshot | LiveDelta, None]:
        try:
            yield snapshot(selected)
            while True:
                yield await self.queue.get()
        finally:
            self.closed.set()

    async def history(self, selected: SourceToken, before: SourcePosition) -> HistoryPage:
        self.page_started.set()
        try:
            await self.page_release.wait()
        except asyncio.CancelledError:
            self.page_cancelled.set()
            raise
        if self.wrong_page:
            selected = replace(selected, request_epoch=selected.request_epoch + 1)
        return HistoryPage(selected, before, None, (), Completeness.PARTIAL)


class BrokenSource(Source):
    async def observe(self, selected: SourceToken) -> AsyncGenerator[TimelineSnapshot | LiveDelta, None]:
        yield snapshot(selected)
        raise RuntimeError("broken provider contract")


def test_unexpected_adapter_failure_is_visible_before_close() -> None:
    async def run() -> None:
        errors: list[str] = []
        controller = TimelineController(
            {Provider.WORKTREES: BrokenSource()}, observation=lambda value: None,
            page=lambda value: None, error=lambda selected, message: errors.append(message),
        )
        await controller.select((token(),))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert errors == ["Timeline adapter failed: broken provider contract"]
        with pytest.raises(TimelineSourceError, match="cleanup failed"):
            await controller.close()
        assert controller.reader_count == 0
    asyncio.run(run())


def test_three_sources_are_independent_and_close_only_readers() -> None:
    async def run() -> None:
        sources = {provider: Source() for provider in Provider}
        observations: list[TimelineSnapshot | LiveDelta] = []
        errors: list[tuple[SourceToken, str]] = []
        controller = TimelineController(
            sources, observation=observations.append, page=lambda value: None,
            error=lambda selected, message: errors.append((selected, message)),
        )
        await controller.select(tuple(token(provider) for provider in Provider))
        await asyncio.sleep(0)
        assert controller.reader_count == 3 and len(observations) == 3
        await sources[Provider.WORKTREES].queue.put(snapshot(token(epoch=1)))
        await asyncio.sleep(0)
        assert len(errors) == 1 and "wrong-selection" in errors[0][1]
        assert controller.reader_count == 2
        await controller.close()
        assert controller.reader_count == 0 and all(source.closed.is_set() for source in sources.values())
    asyncio.run(run())


def test_replacement_waits_for_readers_and_demand_page_cleanup() -> None:
    async def run() -> None:
        source = Source()
        pages: list[HistoryPage] = []
        controller = TimelineController(
            {Provider.WORKTREES: source}, observation=lambda value: None, page=pages.append,
            error=lambda selected, message: pytest.fail(message),
        )
        first = token()
        await controller.select((first,))
        await asyncio.sleep(0)
        assert controller.request_history(first, position(first))
        assert not controller.request_history(first, position(first))
        await source.page_started.wait()
        await controller.select((token(epoch=1),))
        assert source.closed.is_set() and source.page_cancelled.is_set() and not pages
        with pytest.raises(TimelineSourceError, match="inactive selection"):
            controller.request_history(first, position(first))
        await controller.close()
        with pytest.raises(TimelineSourceError, match="closed"):
            await controller.select((first,))
    asyncio.run(run())


def test_history_boundary_errors_remain_visible_and_can_retry() -> None:
    async def run() -> None:
        source = Source()
        source.wrong_page = True
        source.page_release.set()
        pages: list[HistoryPage] = []
        errors: list[str] = []
        controller = TimelineController(
            {Provider.WORKTREES: source}, observation=lambda value: None,
            page=pages.append, error=lambda selected, message: errors.append(message),
        )
        selected = token()
        await controller.select((selected,))
        assert controller.request_history(selected, position(selected))
        await asyncio.sleep(0)
        assert not pages and "requested source boundary" in errors[-1]
        source.wrong_page = False
        assert controller.request_history(selected, position(selected))
        await asyncio.sleep(0)
        assert len(pages) == 1
        await controller.close()
    asyncio.run(run())


def test_unsupported_and_invalid_selections_are_not_empty_success() -> None:
    async def run() -> None:
        errors: list[str] = []
        controller = TimelineController(
            {}, observation=lambda value: None, page=lambda value: None,
            error=lambda selected, message: errors.append(message),
        )
        selected = token()
        await controller.select((selected,))
        assert errors and "Unsupported" in errors[0] and controller.reader_count == 0
        assert not controller.request_history(selected, position(selected))
        with pytest.raises(TimelineContractError, match="repeats"):
            await controller.select((selected, selected))
        with pytest.raises(TimelineContractError, match="another source"):
            controller.request_history(selected, SourcePosition("other", "generation", "cursor"))
        await controller.close()
    asyncio.run(run())
