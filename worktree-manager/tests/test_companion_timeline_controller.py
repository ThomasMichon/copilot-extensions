"""Real asyncio reader lifetimes without starting provider hosts or actors."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Awaitable
from dataclasses import replace
from typing import TypeVar

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

    async def observe(
        self, selected: SourceToken, *, stop: asyncio.Event,
    ) -> AsyncGenerator[TimelineSnapshot | LiveDelta, None]:
        try:
            yield snapshot(selected)
            while True:
                yield await until_stopped(self.queue.get(), stop)
        finally:
            self.closed.set()

    async def history(
        self, selected: SourceToken, before: SourcePosition, *, stop: asyncio.Event,
    ) -> HistoryPage:
        self.page_started.set()
        try:
            if not self.page_release.is_set():
                await until_stopped(self.page_release.wait(), stop)
        except asyncio.CancelledError:
            self.page_cancelled.set()
            raise
        if self.wrong_page:
            selected = replace(selected, request_epoch=selected.request_epoch + 1)
        return HistoryPage(selected, before, None, (), Completeness.PARTIAL)


class BrokenSource(Source):
    async def observe(
        self, selected: SourceToken, *, stop: asyncio.Event,
    ) -> AsyncGenerator[TimelineSnapshot | LiveDelta, None]:
        yield snapshot(selected)
        raise RuntimeError("broken provider contract")


class SlowCleanupSource(Source):
    def __init__(self) -> None:
        super().__init__()
        self.cleanup_started = asyncio.Event()
        self.page_cleanup_started = asyncio.Event()
        self.cleanup_release = asyncio.Event()
        self.cleanup_interrupted = asyncio.Event()
        self.page_cleanup_finished = asyncio.Event()
        self.observations_started = 0

    async def _cleanup(self) -> None:
        try:
            await self.cleanup_release.wait()
        except asyncio.CancelledError:
            self.cleanup_interrupted.set()
            raise

    async def observe(
        self, selected: SourceToken, *, stop: asyncio.Event,
    ) -> AsyncGenerator[TimelineSnapshot | LiveDelta, None]:
        self.observations_started += 1
        try:
            yield snapshot(selected)
            await until_stopped(asyncio.Event().wait(), stop)
        finally:
            self.cleanup_started.set()
            await self._cleanup()
            self.closed.set()

    async def history(
        self, selected: SourceToken, before: SourcePosition, *, stop: asyncio.Event,
    ) -> HistoryPage:
        self.page_started.set()
        try:
            if not self.page_release.is_set():
                await until_stopped(self.page_release.wait(), stop)
            return HistoryPage(selected, before, None, (), Completeness.PARTIAL)
        finally:
            self.page_cleanup_started.set()
            await self._cleanup()
            self.page_cleanup_finished.set()


T = TypeVar("T")


async def until_stopped(value: Awaitable[T], stop: asyncio.Event) -> T:
    request = asyncio.ensure_future(value)
    stopping = asyncio.create_task(stop.wait())
    try:
            done, _ = await asyncio.wait((request, stopping), return_when=asyncio.FIRST_COMPLETED)
            if request in done:
                return request.result()
            raise asyncio.CancelledError
    finally:
            for task in (request, stopping):
                if not task.done():
                    task.cancel()
            await asyncio.gather(request, stopping, return_exceptions=True)


class FiniteCleanupSource(SlowCleanupSource):
    async def observe(
            self, selected: SourceToken, *, stop: asyncio.Event,
    ) -> AsyncGenerator[TimelineSnapshot | LiveDelta, None]:
            try:
                yield snapshot(selected)
            finally:
                self.cleanup_started.set()
                await self._cleanup()
                self.closed.set()


class FailedCleanupSource(SlowCleanupSource):
    async def history(
            self, selected: SourceToken, before: SourcePosition, *, stop: asyncio.Event,
    ) -> HistoryPage:
            try:
                return await super().history(selected, before, stop=stop)
            finally:
                raise OSError("history cleanup failed")


@pytest.mark.parametrize("operation", ["select", "close"])
@pytest.mark.parametrize("history", [False, True])
def test_caller_cancellation_waits_for_owned_awaited_cleanup(operation: str, history: bool) -> None:
    async def run() -> None:
        source = SlowCleanupSource()
        controller = TimelineController(
            {Provider.WORKTREES: source}, observation=lambda value: None,
            page=lambda value: None, error=lambda selected, message: pytest.fail(message),
        )
        selected = token()
        await controller.select((selected,))
        await asyncio.sleep(0)
        if history:
            assert controller.request_history(selected, position(selected))
            await source.page_started.wait()
        action = controller.close() if operation == "close" else controller.select((token(epoch=1),))
        caller = asyncio.create_task(action)
        try:
            await source.cleanup_started.wait()
            if history:
                await source.page_cleanup_started.wait()
            caller.cancel()
            await asyncio.sleep(0)
            caller.cancel()
            await asyncio.sleep(0)
            assert not caller.done() and not source.cleanup_interrupted.is_set()
            assert controller.reader_count == 1 and controller.page_count == int(history)
        finally:
            source.cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await caller
        assert source.closed.is_set() and not source.cleanup_interrupted.is_set()
        assert not history or source.page_cleanup_finished.is_set()
        assert controller.reader_count == controller.page_count == 0
        await controller.close()
        assert source.observations_started == 1
    asyncio.run(run())


def test_close_during_selection_cleanup_does_not_start_replacement_reader() -> None:
    async def run() -> None:
        source = SlowCleanupSource()
        controller = TimelineController(
            {Provider.WORKTREES: source}, observation=lambda value: None,
            page=lambda value: None, error=lambda selected, message: pytest.fail(message),
        )
        await controller.select((token(),))
        await asyncio.sleep(0)
        replacing = asyncio.create_task(controller.select((token(epoch=1),)))
        await source.cleanup_started.wait()
        closing = asyncio.create_task(controller.close())
        await asyncio.sleep(0)
        source.cleanup_release.set()
        with pytest.raises(TimelineSourceError, match="closed during"):
            await replacing
        await closing
        assert source.closed.is_set() and source.observations_started == 1
        assert controller.reader_count == controller.page_count == 0
    asyncio.run(run())


@pytest.mark.parametrize("operation", ["select", "close"])
def test_teardown_does_not_interrupt_history_cleanup_already_in_progress(operation: str) -> None:
    async def run() -> None:
        source = SlowCleanupSource()
        controller = TimelineController(
            {Provider.WORKTREES: source}, observation=lambda value: None,
            page=lambda value: pytest.fail("retired page was delivered"),
            error=lambda selected, message: pytest.fail(message),
        )
        selected = token()
        await controller.select((selected,))
        await asyncio.sleep(0)
        assert controller.request_history(selected, position(selected))
        await source.page_started.wait()
        source.page_release.set()
        await source.page_cleanup_started.wait()
        action = controller.close() if operation == "close" else controller.select((token(epoch=1),))
        caller = asyncio.create_task(action)
        try:
            await source.cleanup_started.wait()
            assert not source.cleanup_interrupted.is_set()
            assert not source.page_cleanup_finished.is_set() and not caller.done()
            assert controller.reader_count == controller.page_count == 1
        finally:
            source.cleanup_release.set()
            await caller
            await controller.close()
        assert source.page_cleanup_finished.is_set() and not source.cleanup_interrupted.is_set()
        assert controller.reader_count == controller.page_count == 0
    asyncio.run(run())


def test_teardown_waits_for_naturally_ending_observer_cleanup() -> None:
    async def run() -> None:
        source = FiniteCleanupSource()
        controller = TimelineController(
            {Provider.WORKTREES: source}, observation=lambda value: None,
            page=lambda value: None, error=lambda selected, message: pytest.fail(message),
        )
        await controller.select((token(),))
        await source.cleanup_started.wait()
        caller = asyncio.create_task(controller.close())
        try:
            await asyncio.sleep(0)
            assert not caller.done() and controller.reader_count == 1
            assert not source.cleanup_interrupted.is_set()
        finally:
            source.cleanup_release.set()
            await caller
        assert source.closed.is_set() and not source.cleanup_interrupted.is_set()
        assert controller.reader_count == 0
    asyncio.run(run())


def test_cleanup_failure_remains_visible_after_selection_is_retired() -> None:
    async def run() -> None:
        source = FailedCleanupSource()
        controller = TimelineController(
            {Provider.WORKTREES: source}, observation=lambda value: None,
            page=lambda value: None, error=lambda selected, message: pytest.fail(message),
        )
        selected = token()
        await controller.select((selected,))
        await asyncio.sleep(0)
        assert controller.request_history(selected, position(selected))
        await source.page_started.wait()
        caller = asyncio.create_task(controller.close())
        await source.page_cleanup_started.wait()
        source.cleanup_release.set()
        with pytest.raises(TimelineSourceError, match="history cleanup failed"):
            await caller
        assert source.closed.is_set() and source.page_cleanup_finished.is_set()
        assert controller.reader_count == controller.page_count == 0
    asyncio.run(run())


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
        error_received = asyncio.Event()

        def receive_error(selected: SourceToken, message: str) -> None:
            errors.append((selected, message))
            error_received.set()

        controller = TimelineController(
            sources, observation=observations.append, page=lambda value: None,
            error=receive_error,
        )
        await controller.select(tuple(token(provider) for provider in Provider))
        await asyncio.sleep(0)
        assert controller.reader_count == 3 and len(observations) == 3
        await sources[Provider.WORKTREES].queue.put(snapshot(token(epoch=1)))
        await error_received.wait()
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
