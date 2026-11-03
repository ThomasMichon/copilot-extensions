"""Rich Agenda demand driven by the existing coarse subscription, never another feed."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from copy import deepcopy
import json
from threading import Event
from typing import Any

from . import engine_client as ec
from .companion_agenda_codec import AgendaCodec
from .companion_agenda_commands import AgendaCommands
from .companion_timeline_contract import (
    Availability, Completeness, LiveDelta, Provider, SourceToken,
    TimelineContractError, TimelineGap, TimelineSnapshot,
)
from .companion_timeline_controller import TimelineCleanupError, TimelineSourceError
from .companion_timeline_widget import CompanionTimelinePanel, SubwayTimeline
from .companion_worktree_codec import OwnerReadError
from .companion_worktree_source import owned, settled
from .engine_process import EngineCleanupError

MAX_CATCHUP_PAGES = 4
Selection = tuple[str, str, str]


def _raw(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":")).encode("utf-8")


class AgendaReader:
    def __init__(self, selection: Selection, store: str, *, cwd: str) -> None:
        self.selection, self.store = selection, store
        project, worktree, session = selection
        self.commands = AgendaCommands(project, worktree, store, cwd=cwd)
        self.codec = AgendaCodec(project, worktree, session, store)
        self.revision = 0

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
        if revision < max(self.revision, publication["revision"]):
            raise OwnerReadError("source-replaced", "agenda store is behind accepted publication or retained evidence")
        if revision == publication["revision"] and page["committed_at"] != publication["committed_at"]:
            raise TimelineContractError("agenda reader and publication disagree at the same revision")
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
                if self.codec.revision(earlier["cursor"]) < revision:
                    raise OwnerReadError("source-replaced", "agenda head regressed during catch-up")
                pages.append(earlier)
            oldest = pages[-1]
            if not oldest["entries"] or self.codec.revision(oldest["entries"][0]["cursor"]) > previous_revision + 1:
                raise OwnerReadError(
                    "cursor-unavailable",
                    "agenda catch-up budget exhausted; retained rows were not discarded. "
                    "Close and reopen Companion for a fresh partial viewer window; canonical history is unchanged.",
                )
        self.revision = revision
        return list(reversed(pages))

    def history(self, cancelled: Event, cursor: str) -> dict[str, Any]:
        self.commands.discover(cancelled)
        frame = self.codec.decode(_raw(self.commands.read(cancelled, history=True, before=cursor)))
        if self.codec.revision(frame["cursor"]) < self.revision:
            raise OwnerReadError("source-replaced", "agenda history returned a rewound store")
        return frame


class AgendaTimelineAttachment:
    def __init__(
        self, panel: CompanionTimelinePanel, *, cwd: str, status: Callable[[str], None],
    ) -> None:
        self.panel, self.cwd, self.status = panel, cwd, status
        self.selection: Selection | None = None
        self.reader: AgendaReader | None = None
        self.token: SourceToken | None = None
        self._stop = asyncio.Event()
        self._changed = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._page: asyncio.Task[None] | None = None
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
            for token in self._tokens():
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
        if self._renderable():
            self.status("Rich Agenda: " + message)
            for token in self._tokens():
                self.panel.source_error(token, message)

    def _tokens(self) -> tuple[SourceToken, ...]:
        return tuple(
            state.token for state in self.panel.window.sources.values()
            if state.token.identity.provider is Provider.AGENDA
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
        error: str | None = None,
    ) -> None:
        async with self._lock:
            if selection is not None and selection == self.selection and self._task is not None and not self._task.done():
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
                if self.reader is None or self.reader.selection != self.selection:
                    self.reader = AgendaReader(self.selection, store, cwd=self.cwd)
                elif self.reader.store != store:
                    raise OwnerReadError("source-replaced", "selected head changed its agenda store")
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
                result = self.panel.bind_source(token)
                if not result.applied:
                    raise TimelineSourceError(result.reason)
                self.token = token
                state = self.panel.window.sources[token.identity.source_key]
                replay: list[LiveDelta] = []
                snapshot = reader.codec.snapshot(pages[-1], token)
                if state.position is not None:
                    base = state.position
                    for page in pages:
                        events = reader.codec.events(page, token)
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
                    f"Rich Agenda: accepted operations through revision {reader.revision}; "
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
        if self._page is not None and not self._page.done():
            return

        async def read() -> None:
            reader, token, stop = self.reader, message.token, self._stop
            cancelled = Event()
            try:
                frame = await owned(lambda: reader.history(cancelled, message.before.cursor), stop, cancelled)
                if stop.is_set() or self.token != token or not self._renderable():
                    return
                value = reader.codec.page(frame, token, message.before)
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
                if not stop.is_set():
                    self._failure(str(error))

        self._page = asyncio.create_task(read(), name="sidecar-agenda-page")

    async def close(self) -> None:
        self.begin_close()
        async with self._lock:
            await self._retire()
