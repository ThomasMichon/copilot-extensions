"""One selected semantic session subscription and cancellable demand history reads."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import AsyncGenerator, Callable, Generator
import json
from threading import Event
from typing import Any

from .companion_bridge_session_codec import SessionCodec
from .companion_bridge_current_requests import CAPABILITY, CurrentRequestProjection, CurrentRequestState
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
        self.current_requests: bool | None = None

    def _negotiate(self, cancelled: Event) -> bool:
        stream = self.command(["version", "--contracts-json"], cancelled, json_document=True)
        try:
            declaration = next(stream, None)
            if (
                not isinstance(declaration, dict)
                or declaration.get("schema") != "agent-bridge.cli-contracts"
                or type(declaration.get("version")) is not int or declaration["version"] != 1
                or not isinstance(declaration.get("contracts"), dict)
            ):
                return False
            contract = declaration["contracts"].get(CAPABILITY)
            return (
                isinstance(contract, dict) and set(contract) == {"version", "capabilities"}
                and type(contract["version"]) is int and contract["version"] == 1
                and isinstance(contract["capabilities"], list)
                and all(isinstance(item, str) for item in contract["capabilities"])
                and "observe-session-current-requests" in contract["capabilities"]
            )
        except OwnerReadError:
            return False
        finally:
            stream.close()

    def frames(
        self, cancelled: Event, *, follow: bool = False,
        after: str | None = None, before: str | None = None, history: bool = False,
    ) -> Generator[dict, None, None]:
        selection = self.selection
        if not history and self.current_requests is None:
            self.current_requests = self._negotiate(cancelled)
        arguments = [
            *(["--project", selection["project"]] if selection["project"] is not None else []),
            "session-observation-history" if history else "observe-session", selection["session_id"],
            "--worktree-id", selection["worktree_id"], "--contract-version", "1", "--json", "--limit", "64",
            *(["--follow"] if follow else []), *(["--after", after] if after is not None else []),
            *(["--before", before] if before is not None else []),
        ]
        optional = not history and self.current_requests is True
        stream = self.command([*arguments, *(["--current-requests"] if optional else [])], cancelled)
        try:
            first = next(stream, None)
            if optional and first == {
                "schema": "bridge-session-observations-v1", "version": 1, "kind": "error",
                "code": "unsupported",
                "message": f"selected daemon does not support {CAPABILITY}",
            }:
                # The owning CLI refused during health negotiation, before any
                # observation query. Unknown parser/transport failures never retry.
                stream.close()
                self.current_requests = False
                yield from self.command(arguments, cancelled)
                return
            if first is not None:
                yield first
                yield from stream
        finally:
            stream.close()


class BridgeSessionSource:
    preparing_message = "loading selected session; an empty log waits for its first real event (history unknown)"
    status_message = (
        "selected session: partial retained events; historical answerability unknown; live input qualified separately; "
        "membership discovery paused (return to membership to resume)"
    )

    def __init__(
        self, project: str, worktree: str, _head: str, *, owner_ref: str,
        member: dict[str, Any], hosting_generation: str, cwd: str, project_root: str,
        current: Callable[[CurrentRequestState | None], None] | None = None,
    ) -> None:
        self.codec = SessionCodec(project, worktree, owner_ref, member, hosting_generation)
        self.commands = SessionCommands(self.codec, cwd=cwd, project_root=project_root)
        self.initial: TimelineSnapshot | None = None
        self._cursor: str | None = None
        self._current = current
        self.current_requests = CurrentRequestProjection()

    def _publish_current(self) -> None:
        state = self.codec.current_state
        if state is None:
            self._invalidate_current()
        elif self.current_requests.accept(state) and self._current is not None:
            self._current(state)

    def _invalidate_current(self) -> None:
        self.current_requests.invalidate()
        if self._current is not None:
            self._current(None)

    def _decode(self, raw: dict[str, Any]) -> dict[str, Any]:
        return self.codec.decode(raw, current_requests=self.commands.current_requests is True)

    async def prepare(self, epoch: int, *, stop: asyncio.Event) -> TimelineSnapshot:
        cancelled = Event()

        def read() -> TimelineSnapshot:
            stream = self.commands.frames(cancelled)
            try:
                raw = next(stream, None)
                if raw is None:
                    raise InterruptedError("Bridge session read stopped")
                frame = self._decode(raw)
                if frame["kind"] != "snapshot":
                    raise TimelineContractError("Bridge session bootstrap is not a snapshot")
                self._publish_current()
                if frame["position"] is None:
                    stream.close()
                    self._invalidate_current()
                    stream = self.commands.frames(cancelled, follow=True)
                    for raw in stream:
                        frame = self._decode(raw)
                        if frame["kind"] != "heartbeat":
                            self._publish_current()
                        if frame["kind"] == "snapshot" and frame["position"] is not None:
                            break
                    else:
                        raise InterruptedError("Bridge session first-event read stopped")
                token = self.codec.token(frame, epoch)
                self.initial = self.codec.snapshot(frame, token)
                self._cursor = frame["position"]
                return self.initial
            finally:
                try:
                    stream.close()
                finally:
                    self._invalidate_current()

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
                frame = self._decode(raw)
                if frame["kind"] == "heartbeat":
                    if frame["position"] != cursor:
                        raise TimelineContractError("Bridge heartbeat changed its delivered position")
                    continue
                if frame["kind"] != "delta" or frame["base_position"] != cursor:
                    raise TimelineContractError("Bridge session lost its delivered boundary")
                self._publish_current()
                position = self.codec.position(token, frame["position"])
                if position != published:
                    yield LiveDelta(token, published, position, self.codec.events(frame, token))
                    published = position
                elif frame["entries"]:
                    raise TimelineContractError("Bridge session repeated events without advancing")
                cursor = frame["position"]
                self._cursor = cursor
        finally:
            self._invalidate_current()
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
