"""Rich Agenda demand driven by the existing coarse subscription, never another feed."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
import json
from pathlib import Path
from threading import Event, Lock
from typing import Any

from . import engine_client as ec
from .companion_agenda_codec import AgendaCodec
from .companion_agenda_commands import AgendaCommands
from .companion_timeline_contract import (
    Availability, Completeness, LiveDelta, Provider, SourcePosition, SourceToken,
    TimelineContractError, TimelineEvent, TimelineGap, TimelineSnapshot,
)
from .companion_timeline_controller import TimelineCleanupError, TimelineSourceError
from .companion_timeline_model import TimelineWindow
from .companion_timeline_widget import CompanionTimelinePanel, SubwayTimeline
from .companion_worktree_codec import OwnerReadError
from .companion_worktree_source import owned, settled
from .engine_process import EngineCleanupError

MAX_CATCHUP_PAGES = 4
Selection = tuple[str, str, str]


def _raw(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


class AgendaReader:
    def __init__(self, selection: Selection, store: str, *, cwd: str, project_root: str) -> None:
        self.selection, self.store = selection, store
        project, worktree, session = selection
        self.commands = AgendaCommands(project, worktree, store, cwd=cwd, project_root=project_root)
        self.codec = AgendaCodec(project, worktree, session, store)
        self.revision = 0
        self._revision_lock = Lock()

    def _retain_revision(self, revision: int, minimum: int, *, historical: bool = False) -> None:
        with self._revision_lock:
            floor = minimum if historical else max(minimum, self.revision)
            if revision < floor:
                raise OwnerReadError("source-replaced", "agenda store is behind accepted publication or retained evidence")
            self.revision = max(self.revision, revision)

    def latest(
        self, publication: dict[str, Any], cancelled: Event, previous: str | None,
    ) -> list[dict[str, Any]]:
        self.commands.discover(cancelled)
        if self.revision == 0:
            self.codec.info(_raw(self.commands.read(cancelled)))
        page = self.codec.decode(_raw(self.commands.read(cancelled, history=True)))
        if page["type"] != "history-page" or page["before_cursor"] is not None:
            raise TimelineContractError("latest agenda read did not return a latest history page")
        revision = self.codec.revision(page["cursor"])
        if revision == publication["revision"] and page["committed_at"] != publication["committed_at"]:
            raise TimelineContractError("agenda reader and publication disagree at the same revision")
        self._retain_revision(revision, publication["revision"])
        pages = [page]
        if previous is not None:
            previous_revision = self.codec.revision(previous)
            for _ in range(MAX_CATCHUP_PAGES - 1):
                oldest = pages[-1]
                if not oldest["entries"] or self.codec.revision(oldest["entries"][0]["cursor"]) <= previous_revision + 1:
                    break
                before = oldest["next_before"]
                if before is None:
                    raise OwnerReadError("cursor-unavailable", "agenda catch-up lost its prior accepted boundary")
                earlier = self.codec.decode(_raw(self.commands.read(cancelled, history=True, before=before)))
                if earlier["type"] != "history-page" or earlier["before_cursor"] != before:
                    raise TimelineContractError("agenda catch-up changed its requested history boundary")
                self._retain_revision(self.codec.revision(earlier["cursor"]), revision)
                pages.append(earlier)
            oldest = pages[-1]
            if not oldest["entries"] or self.codec.revision(oldest["entries"][0]["cursor"]) > previous_revision + 1:
                raise OwnerReadError(
                    "cursor-unavailable",
                    "agenda catch-up budget exhausted; retained rows were not discarded. "
                    "Close and reopen Companion for a fresh partial viewer window; canonical history is unchanged.",
                )
        return list(reversed(pages))

    def history(self, cancelled: Event, cursor: str) -> dict[str, Any]:
        minimum_revision = self.revision
        self.commands.discover(cancelled)
        frame = self.codec.decode(_raw(self.commands.read(cancelled, history=True, before=cursor)))
        self._retain_revision(self.codec.revision(frame["cursor"]), minimum_revision, historical=True)
        return frame


class AgendaTimelineAttachment:
    def __init__(
        self, panel: CompanionTimelinePanel, *, cwd: str, status: Callable[[str], None],
        validate_events: Callable[[SourceToken, tuple[TimelineEvent, ...]], None],
    ) -> None:
        self.panel, self.cwd, self.status = panel, cwd, status
        self._validate_events = validate_events
        self.selection: Selection | None = None
        self._last_selection: Selection | None = None
        self._invalidated_selection: Selection | None = None
        self.project_root: str | None = None
        self.reader: AgendaReader | None = None
        self.token: SourceToken | None = None
        self._stop = asyncio.Event()
        self._changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._page: asyncio.Task[None] | None = None
        self._history_epoch = 0
        self._lock = asyncio.Lock()
        self._epoch = 0
        self._version = 0
        self._publication: dict[str, Any] | None = None
        self._error: str | None = None
        self._closed = False
        self._cleanup_failure: TimelineCleanupError | None = None

    def begin_close(self) -> None:
        self._closed = True
        self._stop.set()
        self._changed.set()

    def publication(self, frame: dict[str, Any] | None, error: str | None) -> None:
        self._publication, self._error = deepcopy(frame), error
        self._version += 1
        self._changed.set()
        if self.selection is None or not self._renderable():
            return
        if error or frame is None:
            self._failure(error or "awaiting provider snapshot")
        elif (
            (frame.get("project"), frame.get("worktree_id"), frame.get("session_id")) == self.selection
            and frame.get("availability") == "head-changed"
        ):
            self._invalidated_selection = self.selection
            for token in self._tokens(all_providers=True):
                self.panel.apply_snapshot(TimelineSnapshot(
                    token, None, Availability.HEAD_CHANGED, Completeness.PARTIAL,
                ))
            self.status("Rich Agenda: head changed; Refresh required")
            self._stop.set()
        elif frame.get("availability") != "observed":
            self._failure("selected head has no current publication")

    def _renderable(self) -> bool:
        return not self._closed and self.panel.is_attached and bool(self.panel.query(SubwayTimeline))

    def _failure(self, message: str) -> None:
        if self._renderable() and (self.selection is None or self._head_current()):
            self._history_epoch += 1
            self.status("Rich Agenda: " + message)
            for token in self._tokens():
                self.panel.source_error(token, message)

    def _head_current(self) -> bool:
        if self.selection is None:
            return False
        project, worktree, head = self.selection
        invalidated = self._invalidated_selection == self.selection or any(
            (state.token.identity.project, state.token.identity.worktree_id) == (project, worktree)
            and (
                state.availability is Availability.HEAD_CHANGED
                or state.token.identity.head_session_id not in (None, head)
            )
            for state in self.panel.window.sources.values()
        )
        if invalidated:
            self._invalidated_selection = self.selection
            self._stop.set()
            self._changed.set()
            self.status("Rich Agenda: head changed; Refresh required")
        return not invalidated

    def _tokens(self, *, all_providers: bool = False) -> tuple[SourceToken, ...]:
        return tuple(
            state.token for state in self.panel.window.sources.values()
            if (all_providers or state.token.identity.provider is Provider.AGENDA)
            and (state.token.identity.project, state.token.identity.worktree_id,
                 state.token.identity.head_session_id) == self.selection
        )

    async def _retire(self) -> None:
        self._stop.set()
        self._changed.set()
        tasks = [task for task in (self._task, self._page) if task is not None]
        if not tasks:
            if self._cleanup_failure is not None:
                raise self._cleanup_failure
            return

        async def join() -> None:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, TimelineCleanupError):
                    self._cleanup_failure = result
            if self._cleanup_failure is not None:
                raise self._cleanup_failure

        await settled(asyncio.create_task(join()))
        self._task = self._page = None

    async def set_selection(
        self, selection: Selection | None, frame: dict[str, Any] | None = None,
        error: str | None = None, *, project_root: str | None = None,
    ) -> None:
        async with self._lock:
            if self.selection is not None and self._renderable():
                self._head_current()
            if (
                selection is not None and selection == self.selection
                and project_root == self.project_root and self._task is not None and not self._task.done()
            ):
                return
            self.selection = None
            try:
                await self._retire()
            except TimelineCleanupError:
                self._failure("resource retirement unresolved; no replacement read")
                return
            self._epoch += 1
            self.token = None
            if selection is None or self._closed:
                return
            self.selection = selection
            if not isinstance(project_root, str) or not Path(project_root).is_absolute():
                self._failure("selected worktree root is unavailable")
                return
            if self._last_selection is not None and selection != self._last_selection:
                self._invalidated_selection = None
                if self._renderable() and not any(
                    (state.token.identity.project, state.token.identity.worktree_id,
                     state.token.identity.head_session_id) == selection
                    for state in self.panel.window.sources.values()
                ):
                    self.panel.replace_window(TimelineWindow())
            self._last_selection = selection
            self.project_root = project_root
            self._stop = asyncio.Event()
            self.publication(frame, error)
            self._task = asyncio.create_task(self._observe(), name="sidecar-agenda-rich")

    async def _observe(self) -> None:
        while not self._stop.is_set():
            await self._changed.wait()
            self._changed.clear()
            if self._stop.is_set():
                return
            frame, error, version = self._publication, self._error, self._version
            try:
                if not self._head_current():
                    return
                if error or frame is None:
                    self._failure(error or "awaiting provider snapshot")
                    continue
                if self.selection is None or (
                    frame["project"], frame["worktree_id"], frame["session_id"],
                ) != self.selection:
                    raise TimelineContractError("rich agenda publication changed the selected head")
                if frame["availability"] != "observed":
                    if self.token is not None and frame["availability"] == "head-changed":
                        self.panel.apply_snapshot(TimelineSnapshot(
                            self.token, None, Availability.HEAD_CHANGED, Completeness.PARTIAL,
                        ))
                    else:
                        self._failure("selected head has no current publication")
                    continue
                publication = frame["observation"]
                store = publication["source"]["store_id"]
                if self.project_root is None:
                    raise OwnerReadError("unavailable", "selected worktree root is unavailable")
                if self.reader is None or self.reader.selection != self.selection:
                    self.reader = AgendaReader(self.selection, store, cwd=self.cwd, project_root=self.project_root)
                elif self.reader.store != store:
                    raise OwnerReadError("source-replaced", "selected head changed its agenda store")
                elif self.reader.commands.project_root != self.project_root:
                    raise OwnerReadError("source-replaced", "selected worktree root changed")
                reader = self.reader
                token = reader.codec.token(self._epoch)
                state = self.panel.window.sources.get(token.identity.source_key)
                previous = state.position.cursor if state is not None and state.position is not None else None
                self.status("Rich Agenda: reading accepted operations")
                cancelled = Event()
                pages = await owned(
                    lambda: reader.latest(publication, cancelled, previous),
                    self._stop, cancelled,
                )
                if self._stop.is_set() or version != self._version or not self._renderable():
                    continue
                if not self._head_current():
                    return
                page_events = tuple(reader.codec.events(page, token) for page in pages)
                for events in page_events:
                    self._validate_events(token, events)
                state = self.panel.window.sources.get(token.identity.source_key)
                if state is None or state.token != token:
                    result = self.panel.bind_source(token)
                    if not result.applied:
                        raise TimelineSourceError(result.reason)
                self.token = token
                state = self.panel.window.sources[token.identity.source_key]
                replay: list[LiveDelta] = []
                snapshot = reader.codec.snapshot(pages[-1], token)
                if state.position is not None:
                    base = state.position
                    for events in page_events:
                        newer = tuple(event for event in events if reader.codec.revision(event.position.cursor) > reader.codec.revision(base.cursor))
                        if not newer:
                            continue
                        replay.append(LiveDelta(token, base, newer[-1].position, newer))
                        base = newer[-1].position
                if replay and state.availability is Availability.UNAVAILABLE:
                    result = self.panel.recover_snapshot(snapshot, tuple(replay))
                else:
                    for delta in replay:
                        result = self.panel.apply_delta(delta)
                        if not result.applied:
                            raise TimelineSourceError(result.reason)
                    result = self.panel.apply_snapshot(snapshot)
                if not result.applied:
                    raise TimelineSourceError(result.reason)
                self.status(
                    f"Rich Agenda: accepted operations through revision {reader.codec.revision(pages[-1]['cursor'])}; "
                    "partial history, caller-reported actors; not execution authority"
                )
            except asyncio.CancelledError:
                if not self._stop.is_set():
                    raise
                return
            except (TimelineCleanupError, EngineCleanupError) as error:
                self._cleanup_failure = (
                    error if isinstance(error, TimelineCleanupError) else TimelineCleanupError(str(error))
                )
                self._stop.set()
                self._changed.set()
                self._failure("resource retirement unresolved; no replacement read")
                raise self._cleanup_failure
            except (TimelineSourceError, TimelineContractError, OSError, ValueError, ec.EngineError) as error:
                if not self._stop.is_set() and version == self._version:
                    self._failure(str(error))

    def history(self, message: SubwayTimeline.HistoryRequested) -> None:
        if message.token.identity.provider is not Provider.AGENDA:
            return
        if self.token != message.token or self.reader is None or self._stop.is_set() or self._cleanup_failure is not None:
            self._failure("history request belongs to an inactive agenda")
            return
        if not self._head_current():
            return
        if self._page is not None and not self._page.done():
            return

        async def read() -> None:
            reader, token, stop = self.reader, message.token, self._stop
            epoch = self._history_epoch
            cancelled = Event()
            try:
                if not self._history_current(token, message.before, stop, epoch):
                    return
                frame = await owned(lambda: reader.history(cancelled, message.before.cursor), stop, cancelled)
                if not self._history_current(token, message.before, stop, epoch):
                    return
                value = reader.codec.page(frame, token, message.before)
                if not isinstance(value, TimelineGap):
                    self._validate_events(token, value.events)
                result = self.panel.apply_gap(value) if isinstance(value, TimelineGap) else self.panel.prepend_page(value)
                if not result.applied and not isinstance(value, TimelineGap):
                    raise TimelineSourceError(result.reason)
            except asyncio.CancelledError:
                if not stop.is_set():
                    raise
            except (TimelineCleanupError, EngineCleanupError) as error:
                self._cleanup_failure = (
                    error if isinstance(error, TimelineCleanupError) else TimelineCleanupError(str(error))
                )
                self._failure("history retirement unresolved; no replacement read")
                self._stop.set()
                self._changed.set()
                raise self._cleanup_failure
            except (TimelineSourceError, TimelineContractError, OSError, ValueError) as error:
                if self._history_current(token, message.before, stop, epoch):
                    self._failure(str(error))

        self._page = asyncio.create_task(read(), name="sidecar-agenda-page")
        self._page.add_done_callback(self._history_finished)

    def _history_finished(self, task: asyncio.Task[None]) -> None:
        if (
            task is not self._page or task.cancelled() or task.exception() is not None
            or self.token is None or self._stop.is_set() or not self._renderable()
        ):
            return
        state = self.panel.window.sources.get(self.token.identity.source_key)
        if state is not None and state.requested_before is not None:
            # A recovered viewport may reissue demand while its retired child settles.
            if self._history_current(state.token, state.requested_before, self._stop, self._history_epoch):
                self.history(SubwayTimeline.HistoryRequested(state.token, state.requested_before))

    def _history_current(
        self, token: SourceToken, before: SourcePosition, stop: asyncio.Event, epoch: int,
    ) -> bool:
        if (
            stop.is_set() or self.token != token or epoch != self._history_epoch
            or not self._renderable() or not self._head_current()
        ):
            return False
        state = self.panel.window.sources.get(token.identity.source_key)
        return (
            state is not None and state.token == token
            and state.before == before and state.requested_before == before
        )

    async def close(self) -> None:
        self.begin_close()
        async with self._lock:
            await self._retire()
