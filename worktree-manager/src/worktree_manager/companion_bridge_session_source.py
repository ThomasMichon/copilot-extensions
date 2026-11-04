"""One selected semantic session subscription and cancellable demand history reads."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncGenerator, Generator
import json
from threading import Event
from typing import Any

from .companion_bridge_session_codec import SessionCodec
from .companion_bridge_source import MembershipCommands
from .companion_timeline_contract import (
    HistoryPage, LiveDelta, SourcePosition, SourceToken, TimelineContractError,
    TimelineGap, TimelineSnapshot,
)
from .companion_timeline_controller import Observation
from .companion_worktree_codec import OwnerReadError
from .companion_worktree_source import owned, settled


class SessionCommands(MembershipCommands):
    def __init__(self, codec: SessionCodec, *, cwd: str, project_root: str) -> None:
        super().__init__(codec.owner, cwd=cwd, project_root=project_root)
        self.selection = codec.selection

    def frames(
        self, cancelled: Event, *, follow: bool = False,
        after: str | None = None, before: str | None = None, history: bool = False,
    ) -> Generator[dict, None, None]:
        selection = self.selection
        yield from self.command([
            *(["--project", selection["project"]] if selection["project"] is not None else []),
            "session-observation-history" if history else "observe-session", selection["session_id"],
            "--worktree-id", selection["worktree_id"], "--contract-version", "1", "--json", "--limit", "64",
            *(["--follow"] if follow else []), *(["--after", after] if after is not None else []),
            *(["--before", before] if before is not None else []),
        ], cancelled)


class BridgeSessionSource:
    preparing_message = "loading selected session; an empty log waits for its first real event (history unknown)"
    status_message = (
        "selected session: partial retained events; current requests and answerability unknown; "
        "membership discovery paused (return to membership to resume)"
    )

    def __init__(
        self, project: str, worktree: str, _head: str, *, owner_ref: str,
        member: dict[str, Any], hosting_generation: str, cwd: str, project_root: str,
    ) -> None:
        self.codec = SessionCodec(project, worktree, owner_ref, member, hosting_generation)
        self.commands = SessionCommands(self.codec, cwd=cwd, project_root=project_root)
        self.initial: TimelineSnapshot | None = None
        self._cursor: str | None = None

    async def prepare(self, epoch: int, *, stop: asyncio.Event) -> TimelineSnapshot:
        cancelled = Event()

        def read() -> TimelineSnapshot:
            stream = self.commands.frames(cancelled)
            try:
                raw = next(stream, None)
                if raw is None:
                    raise InterruptedError("Bridge session read stopped")
                frame = self.codec.decode(raw)
                if frame["kind"] != "snapshot":
                    raise TimelineContractError("Bridge session bootstrap is not a snapshot")
                if frame["position"] is None:
                    stream.close()
                    stream = self.commands.frames(cancelled, follow=True)
                    for raw in stream:
                        frame = self.codec.decode(raw)
                        if frame["kind"] == "snapshot" and frame["position"] is not None:
                            break
                    else:
                        raise InterruptedError("Bridge session first-event read stopped")
                token = self.codec.token(frame, epoch)
                self.initial = self.codec.snapshot(frame, token)
                self._cursor = frame["position"]
                return self.initial
            finally:
                stream.close()

        return await owned(read, stop, cancelled)

    async def observe(self, token: SourceToken, *, stop: asyncio.Event) -> AsyncGenerator[Observation, None]:
        initial = self.initial
        if initial is None or initial.token != token or initial.position is None or self._cursor is None:
            raise TimelineContractError("Bridge session selection has not been prepared")
        cancelled = Event()
        stream = self.commands.frames(cancelled, follow=True, after=self._cursor)
        cursor, published = self._cursor, initial.position
        try:
            yield initial
            while not stop.is_set():
                raw = await owned(lambda: next(stream, None), stop, cancelled)
                if stop.is_set():
                    return
                if raw is None:
                    raise OwnerReadError("unavailable", "Bridge session subscription ended")
                frame = self.codec.decode(raw)
                if frame["kind"] == "heartbeat":
                    if frame["position"] != cursor:
                        raise TimelineContractError("Bridge heartbeat changed its delivered position")
                    continue
                if frame["kind"] != "delta" or frame["base_position"] != cursor:
                    raise TimelineContractError("Bridge session lost its delivered boundary")
                position = self.codec.position(token, frame["position"])
                if position != published:
                    yield LiveDelta(token, published, position, self.codec.events(frame, token))
                    published = position
                elif frame["entries"]:
                    raise TimelineContractError("Bridge session repeated events without advancing")
                cursor = frame["position"]
                self._cursor = cursor
        finally:
            cancelled.set()
            await settled(asyncio.create_task(asyncio.to_thread(stream.close)))

    def _wire_position(self, token: SourceToken, before: SourcePosition) -> str:
        if before.matches(token.identity) and before.cursor.startswith("event:"):
            sequence = before.cursor[6:]
            if sequence.isascii() and sequence.isdecimal() and str(int(sequence)) == sequence:
                raw = json.dumps({
                    "version": 1, "source": self.codec.source, "event_id": int(sequence),
                }, ensure_ascii=True, separators=(",", ":")).encode("ascii")
                return "abso1." + base64.urlsafe_b64encode(raw).decode().rstrip("=")
        raise TimelineContractError("Bridge session history requires an admitted event position")

    async def history(
        self, token: SourceToken, before: SourcePosition, *, stop: asyncio.Event,
    ) -> HistoryPage | TimelineGap:
        if self.initial is None or self.initial.token != token or self.codec.source is None:
            raise TimelineContractError("Bridge session history selection is not prepared")
        boundary = self._wire_position(token, before)
        cancelled = Event()

        def read() -> HistoryPage:
            stream = self.commands.frames(cancelled, history=True, before=boundary)
            try:
                raw = next(stream, None)
                if raw is None:
                    raise InterruptedError("Bridge session history stopped")
                frame = self.codec.decode(raw, history=True)
                if frame["kind"] != "history-page" or frame["before"] != boundary:
                    raise TimelineContractError("Bridge history changed its requested boundary")
                return self.codec.page(frame, token, before)
            finally:
                stream.close()

        return await owned(read, stop, cancelled)
