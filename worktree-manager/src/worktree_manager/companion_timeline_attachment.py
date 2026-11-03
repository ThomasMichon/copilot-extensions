"""Demand-driven selected worktree attachment; no provider or pane authority."""

from __future__ import annotations

import asyncio

from rich.text import Text
from textual.widgets import Static

from .agenda_contract import AgendaUnavailable
from .companion_timeline_contract import (
    Availability, HistoryPage, LiveDelta, MAX_WINDOW_BYTES, Provider, SourceToken,
    TimelineContractError, TimelineEvent, TimelineGap, TimelineSnapshot,
)
from .companion_timeline_controller import TimelineCleanupError, TimelineController, TimelineSourceError
from .companion_timeline_model import MAX_SOURCE_SCOPES, TimelineWindow
from .companion_timeline_widget import CompanionTimelinePanel, SubwayTimeline
from .companion_worktree_source import WorktreeTimelineSource

MAX_RETAINED_EVIDENCE_BYTES = 2 * MAX_WINDOW_BYTES
MAX_RETAINED_WINDOWS = MAX_SOURCE_SCOPES


class TimelineAttachment:
    def __init__(self, panel: CompanionTimelinePanel, *, cwd: str) -> None:
        self.panel, self.cwd = panel, cwd
        self.controller: TimelineController | None = None
        self._stop = asyncio.Event()
        self._revision = 0
        self._lock = asyncio.Lock()
        self._closed = False
        self._cleanup_error: str | None = None
        self._cleanup_failure: TimelineCleanupError | None = None
        self._retained_windows: tuple[TimelineWindow, ...] = ()
        self._retained_bytes = 0

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

    @property
    def retained_windows(self) -> tuple[TimelineWindow, ...]:
        """Exhausted viewer evidence retained separately, never used as active facts."""
        return self._retained_windows

    def _retain_exhausted(self, window: TimelineWindow) -> None:
        size = window.encoded_size + window._integrity_size()
        if (
            len(self._retained_windows) >= MAX_RETAINED_WINDOWS
            or self._retained_bytes + size > MAX_RETAINED_EVIDENCE_BYTES
        ):
            raise TimelineSourceError("Retained evidence budget is full; no evidence or baseline was discarded.")
        self._retained_windows += (window,)
        self._retained_bytes += size

    def validate_events(self, token: SourceToken, events: tuple[TimelineEvent, ...]) -> None:
        """Check shared active and retained proofs before any provider publishes."""
        windows = self._retained_windows + ((self.panel.window,) if self._renderable() else ())
        for event in events:
            digest = None
            for window in windows:
                state = window.sources.get(token.identity.source_key)
                if state is None or state.token.identity != token.identity:
                    continue
                known = window._proofs.get(event.ref)
                if known is not None:
                    digest = window._digest(event) if digest is None else digest
                    if known != digest:
                        raise TimelineSourceError("An immutable event conflicts with active or retained window evidence.")

    async def set_selection(
        self, selection: tuple[str, str, str] | None, *, reconcile: bool = False,
    ) -> None:
        self.stop_now()
        revision = self._revision
        previous_window = self.panel.window
        viewport = self.panel.capture_viewport() if selection is not None and self._renderable() else None
        async with self._lock:
            if self.controller is not None:
                try:
                    await self.controller.close()
                except TimelineCleanupError as exc:
                    self._cleanup_error = str(exc)
                    self._cleanup_failure = exc
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
                if (
                    not isinstance(initial, TimelineSnapshot)
                    or initial.token.request_epoch != revision
                    or initial.token.identity.provider is not Provider.WORKTREES
                    or (initial.token.identity.project, initial.token.identity.worktree_id,
                        initial.token.identity.head_session_id) != selection
                ):
                    raise TimelineContractError("prepared snapshot changed the active selection or request epoch")
                window = self.panel.window
                self.validate_events(initial.token, initial.events)
                key = initial.token.identity.source_key
                if reconcile and window.reconciliation_required and initial.availability is not Availability.OBSERVED:
                    raise TimelineContractError("reconciliation requires fresh observed owner facts")
                if (
                    reconcile and key in window.reconciliation_required
                    and window.sources[key].token.identity == initial.token.identity
                ):
                    candidate = window.reconciliation_window(initial)
                    self._retain_exhausted(window)
                    self.panel.replace_window(candidate, viewport=viewport)
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
                    if not isinstance(value, TimelineGap):
                        self.validate_events(value.token, value.events)
                    result = (
                        self.panel.apply_snapshot(value) if isinstance(value, TimelineSnapshot)
                        else self.panel.apply_delta(value) if isinstance(value, LiveDelta)
                        else self.panel.apply_gap(value)
                    )
                    if not result.applied:
                        raise TimelineSourceError(result.reason)

                def page(value: HistoryPage) -> None:
                    if current():
                        self.validate_events(value.token, value.events)
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
            except TimelineCleanupError as exc:
                self._cleanup_error, self._cleanup_failure = str(exc), exc
                if revision == self._revision:
                    self._error(None, "Owned timeline cleanup failed: " + self._cleanup_error)
            except (TimelineSourceError, TimelineContractError, AgendaUnavailable, OSError, ValueError) as exc:
                if revision == self._revision:
                    self._error(None, str(exc))
                    if viewport is not None:
                        saved_viewport = viewport

                        def restore() -> None:
                            if (
                                revision == self._revision and self._renderable()
                                and self.panel.window is previous_window
                            ):
                                self.panel.restore_viewport(saved_viewport)

                        self.panel.call_after_refresh(restore)

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
                raise TimelineCleanupError("Owned timeline cleanup failed: " + self._cleanup_error) from self._cleanup_failure
