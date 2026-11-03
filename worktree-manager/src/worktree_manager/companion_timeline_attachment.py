"""Demand-driven selected worktree attachment; no provider or pane authority."""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual.widgets import Static

from .agenda_contract import AgendaUnavailable
from .companion_timeline_contract import (
    HistoryPage, LiveDelta, Provider, SourceToken,
    TimelineContractError, TimelineGap, TimelineSnapshot,
)
from .companion_timeline_controller import TimelineController, TimelineSourceError
from .companion_timeline_model import TimelineWindow
from .companion_timeline_widget import CompanionTimelinePanel, SubwayTimeline
from .companion_worktree_source import WorktreeTimelineSource


class TimelineAttachment:
    def __init__(self, panel: CompanionTimelinePanel, *, cwd: str) -> None:
        self.panel, self.cwd = panel, cwd
        self.controller: TimelineController | None = None
        self._stop = asyncio.Event()
        self._revision = 0
        self._lock = asyncio.Lock()
        self._closed = False
        self._cleanup_error: str | None = None

    def stop_now(self) -> None:
        self._revision += 1
        self._stop.set()

    def begin_close(self) -> None:
        self._closed = True
        self.stop_now()

    def _renderable(self) -> bool:
        return not self._closed and self.panel.is_attached and bool(self.panel.query(SubwayTimeline))

    def _error(self, token: SourceToken | None, message: str) -> None:
        if not self._renderable():
            return
        tokens = (token,) if token is not None else tuple(
            state.token for state in self.panel.window.sources.values()
            if state.token.identity.provider is Provider.WORKTREES
        )
        for selected in tokens:
            self.panel.source_error(selected, message)
        if not tokens:
            self.panel.query_one(".timeline-state", Static).update(Text(
                "Timeline unavailable; history unknown. " + message,
            ))

    @property
    def closed(self) -> bool:
        return self._closed

    async def set_selection(self, selection: tuple[str, str, str] | None) -> None:
        self.stop_now()
        revision = self._revision
        async with self._lock:
            if self.controller is not None:
                try:
                    await self.controller.close()
                except (TimelineSourceError, OSError) as exc:
                    self._cleanup_error = str(exc)
                    self._error(None, "Owned timeline cleanup failed: " + self._cleanup_error)
                    return
                self.controller = None
            if self._cleanup_error is not None:
                self._error(None, "Owned timeline cleanup remains unresolved: " + self._cleanup_error)
                return
            if revision != self._revision or self._closed or selection is None:
                return
            stop = asyncio.Event()
            self._stop = stop
            project, worktree, head = selection
            source = WorktreeTimelineSource(project, worktree, head, cwd=self.cwd)
            self.panel.query_one(".timeline-state", Static).update(Text("Loading selected owner observations."))
            try:
                initial = await source.prepare(revision, stop=stop)
                if revision != self._revision or stop.is_set() or not self._renderable():
                    return
                if any(
                    (state.token.identity.project, state.token.identity.worktree_id) != (project, worktree)
                    for state in self.panel.window.sources.values()
                ):
                    self.panel.replace_window(TimelineWindow())
                result = self.panel.bind_source(initial.token)
                if not result.applied:
                    raise TimelineSourceError(result.reason)

                def current() -> bool:
                    return revision == self._revision and self._renderable()

                def observation(value: TimelineSnapshot | LiveDelta | TimelineGap) -> None:
                    if not current():
                        return
                    result = (
                        self.panel.apply_snapshot(value) if isinstance(value, TimelineSnapshot)
                        else self.panel.apply_delta(value) if isinstance(value, LiveDelta)
                        else self.panel.apply_gap(value)
                    )
                    if not result.applied:
                        raise TimelineSourceError(result.reason)

                def page(value: HistoryPage) -> None:
                    if current():
                        result = self.panel.prepend_page(value)
                        if not result.applied:
                            raise TimelineSourceError(result.reason)

                def gap(value: TimelineGap) -> None:
                    if current():
                        self.panel.apply_gap(value)

                def error(token: SourceToken, message: str) -> None:
                    if current():
                        self._error(token, message)

                self.controller = TimelineController(
                    {Provider.WORKTREES: source}, observation=observation,
                    page=page, gap=gap, error=error,
                )
                await self.controller.select((initial.token,))
            except asyncio.CancelledError:
                if not stop.is_set():
                    raise
            except (TimelineSourceError, TimelineContractError, AgendaUnavailable, OSError, ValueError) as exc:
                if revision == self._revision:
                    self._error(None, str(exc))

    def history(self, message: SubwayTimeline.HistoryRequested) -> None:
        if self.controller is None:
            self._error(message.token, "No selected owner reader; Refresh to reconcile.")
            return
        try:
            self.controller.request_history(message.token, message.before)
        except (TimelineSourceError, TimelineContractError) as exc:
            self._error(message.token, str(exc))

    def unavailable(self, reason: str) -> None:
        self._error(None, reason)

    async def close(self) -> None:
        self.begin_close()
        async with self._lock:
            if self.controller is not None:
                await self.controller.close()
                self.controller = None
            if self._cleanup_error is not None:
                raise TimelineSourceError("Owned timeline cleanup failed: " + self._cleanup_error)
