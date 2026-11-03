"""Passive owner commands, bounded thread bridging and selected timeline adapter."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
from dataclasses import replace
from pathlib import Path
import subprocess
from threading import Event
from typing import Any, TypeVar

from . import agenda_transport as transport
from . import engine_client as ec
from .companion_client import cancellable_read_runner
from .companion_timeline_contract import (
    HistoryPage, LiveDelta, SourcePosition, SourceToken, TimelineContractError,
    TimelineGap, TimelineSnapshot, decode_wire_frame,
)
from .companion_timeline_controller import Observation
from .companion_worktree_codec import (
    FEED_CAPABILITY, HISTORY_CAPABILITY, OwnerReadError, WorktreeCodec, encoded, generation,
)

_PROBE = r"""
import json,sys
try:
    from agent_worktrees.worktrees_client import read_health
    health = read_health()
    caps = health.get("capabilities") if isinstance(health,dict) else None
    if not isinstance(caps,list) or "worktree-observations-feed-v1" not in caps:
        result = {"ok":False,"code":"unsupported","message":"resident lacks worktree observations"}
    else:
        from agent_worktrees.owner_observation_cli import COMMANDS
        from agent_worktrees.owner_observation_contract import VERSION
        from agent_worktrees.__main__ import build_parser
        if VERSION != 1 or not {"worktree-observations","worktree-observation-history"} <= COMMANDS:
            raise ValueError("selected executable does not declare the observation CLI")
        parser = build_parser()
        parser.parse_args(["worktree-observations",
            "--worktree",sys.argv[2],"--session",sys.argv[3],"--follow",
            "--contract-version","1","--json"])
        if "worktree-observations-history-v1" in caps:
            parser.parse_args(["worktree-observation-history",
                "--worktree",sys.argv[2],"--session",sys.argv[3],
                "--source-generation","0"*32,"--limit","64","--contract-version","1","--json"])
        result = {"ok":True,"health":health}
except ImportError:
    result = {"ok":False,"code":"unsupported","message":"selected executable lacks passive observation support"}
except (OSError,ValueError,RuntimeError,AttributeError,TypeError,SystemExit) as exc:
    result = {"ok":False,"code":"unavailable","message":str(exc)[:1024] or type(exc).__name__}
raw = json.dumps(result,ensure_ascii=False,allow_nan=False,separators=(",",":"))
if len(raw.encode())+1 > 35*1024:
    raw = json.dumps({"ok":False,"code":"invalid-response","message":"owner health exceeds its bound"})
print(raw,flush=True)
"""
T = TypeVar("T")


async def settled(task: asyncio.Task[T]) -> T:
    """Keep cleanup custody through repeated caller cancellation."""
    cancelled: asyncio.CancelledError | None = None
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError as exc:
            if task.cancelled():
                raise
            cancelled = exc
    if cancelled is not None:
        raise cancelled
    return result


async def owned(
    operation: Callable[[], T], stop: asyncio.Event, cancelled: Event,
) -> T:
    task = asyncio.create_task(asyncio.to_thread(operation))
    stopping = asyncio.create_task(stop.wait())
    try:
        done, _ = await asyncio.wait((task, stopping), return_when=asyncio.FIRST_COMPLETED)
        if stopping in done:
            cancelled.set()
        return await settled(task)
    except InterruptedError as exc:
        if stop.is_set():
            raise asyncio.CancelledError from exc
        raise
    finally:
        if stop.is_set() or not task.done():
            cancelled.set()
        stopping.cancel()

        async def cleanup() -> None:
            await asyncio.gather(stopping, return_exceptions=True)
            if not task.done():
                try:
                    await task
                except InterruptedError:
                    if not cancelled.is_set():
                        raise

        await settled(asyncio.create_task(cleanup()))


class OwnerCommands:
    def __init__(self, project: str, worktree: str, session: str, *, cwd: str) -> None:
        self.project, self.worktree, self.session, self.cwd = project, worktree, session, cwd
        self.base: list[str] | None = None
        self.capabilities: frozenset[str] = frozenset()

    def probe(self, cancelled: Event) -> str:
        try:
            base = ec.engine_base_command()
        except ec.EngineError as exc:
            raise OwnerReadError("unavailable", str(exc)) from exc
        if not base or not Path(base[0]).is_absolute():
            raise OwnerReadError("unavailable", "no attributed absolute engine command")
        transport.validate_agenda_entry(base)
        if len(base) < 3 or base[-2:] not in (
            ["-m", "agent_worktrees"], ["-m", "agent_worktrees.__main__"],
        ):
            raise OwnerReadError("unsupported", "owned executable has no passive health probe")
        argv = [*base[:-2], "-I", "-B", "-c", _PROBE, self.project, self.worktree, self.session]
        try:
            result = cancellable_read_runner(cancelled, cwd=self.cwd)(argv, transport.STARTUP_TIMEOUT)
        except (TimeoutError, subprocess.TimeoutExpired) as exc:
            raise OwnerReadError("timeout", "passive observation probe exceeded its deadline") from exc
        if cancelled.is_set():
            raise InterruptedError("selected owner probe stopped")
        if result.returncode or len(result.stderr.encode("utf-8")) > 8192:
            raise OwnerReadError("unavailable", "passive observation probe failed")
        value = decode_wire_frame(result.stdout.encode("utf-8"))
        if value.get("ok") is False and set(value) == {"ok", "code", "message"}:
            if not isinstance(value["code"], str) or not isinstance(value["message"], str):
                raise TimelineContractError("invalid passive probe error")
            raise OwnerReadError(value["code"], value["message"])
        if set(value) != {"ok", "health"} or value["ok"] is not True:
            raise TimelineContractError("invalid passive owner probe")
        health = value["health"]
        if not isinstance(health, dict) or not isinstance(health.get("capabilities"), list):
            raise TimelineContractError("owner health omitted capabilities")
        if any(not isinstance(item, str) for item in health["capabilities"]):
            raise TimelineContractError("invalid owner capability")
        self.capabilities = frozenset(health["capabilities"])
        if FEED_CAPABILITY not in self.capabilities:
            raise OwnerReadError("unsupported", "resident lacks selected observation support")
        metadata, roster = health.get("worktree_observations"), health.get("worktrees")
        if not isinstance(metadata, dict) or not isinstance(roster, dict):
            raise TimelineContractError("owner health omitted observation readiness")
        if (
            health.get("published") is not True or health.get("status") != "ready"
            or metadata.get("status") != "ready" or roster.get("status") != "ready"
        ):
            raise OwnerReadError("unavailable", "existing observation receiver is not ready")
        resident = generation(metadata.get("generation"))
        if resident != roster.get("generation"):
            raise OwnerReadError("source-replaced", "roster and observation owners disagree")
        self.base = base
        return resident

    def argv(
        self, *, follow: bool = False, after: str | None = None,
        history: bool = False, source_generation: str | None = None, before: str | None = None,
    ) -> list[str]:
        if self.base is None:
            raise OwnerReadError("unsupported", "owner support has not been admitted")
        if history and HISTORY_CAPABILITY not in self.capabilities:
            raise OwnerReadError("unsupported", "resident lacks selected history support")
        args = [
            *self.base, "--project", self.project,
            "worktree-observation-history" if history else "worktree-observations",
            "--worktree", self.worktree, "--session", self.session, "--contract-version", "1", "--json",
        ]
        if history:
            if source_generation is None:
                raise TimelineContractError("history requires its admitted journal generation")
            args += ["--source-generation", source_generation, "--limit", "64"]
            if before is not None:
                args += ["--before-cursor", before]
        else:
            if follow:
                args.append("--follow")
            if after is not None:
                args += ["--after-cursor", after]
        return args

    def frames(self, cancelled: Event, **options: Any) -> Generator[dict, None, None]:
        return transport.command_frames(self.argv(**options), cancelled=cancelled, cwd=self.cwd)

    def point(self, cancelled: Event, **options: Any) -> dict:
        stream = self.frames(cancelled, **options)
        try:
            value = next(stream, None)
            if value is None:
                raise InterruptedError("selected owner point read stopped")
            return value
        finally:
            stream.close()


class WorktreeTimelineSource:
    """One selected journal; replacement requires a new owner-qualified preparation."""

    def __init__(self, project: str, worktree: str, session: str, *, cwd: str) -> None:
        self.commands = OwnerCommands(project, worktree, session, cwd=cwd)
        self.codec: WorktreeCodec | None = None
        self.initial: TimelineSnapshot | None = None
        self._cursor: SourcePosition | None = None

    async def prepare(self, epoch: int, *, stop: asyncio.Event) -> TimelineSnapshot:
        cancelled = Event()

        def read() -> TimelineSnapshot:
            resident = self.commands.probe(cancelled)
            codec = WorktreeCodec(
                self.commands.project, self.commands.worktree, self.commands.session, resident,
            )
            frame = codec.decode(encoded(self.commands.point(cancelled)).encode("utf-8"))
            if frame["type"] != "snapshot" or frame["base_cursor"] is not None:
                raise TimelineContractError("owner point read did not return the requested snapshot")
            token = codec.token(frame, epoch)
            seed = ()
            # A point snapshot intentionally has no initial replay. Seed from a
            # bounded owner page, then obtain atomic current facts/replay after
            # its oldest real cursor. Never manufacture a cursor or creation event.
            if frame["cursor"] != frame["history_floor"] and HISTORY_CAPABILITY in self.commands.capabilities:
                page = codec.decode(encoded(self.commands.point(
                    cancelled, history=True, source_generation=token.identity.generation,
                )).encode("utf-8"))
                codec.check_source(page, token)
                if page["type"] != "history-page" or page["before_cursor"] is not None:
                    raise TimelineContractError("initial owner history did not return the latest page")
                if page["entries"]:
                    base = page["entries"][0]["cursor"]
                    frame = codec.decode(encoded(self.commands.point(cancelled, after=base)).encode("utf-8"))
                    if frame["type"] != "snapshot" or frame["base_cursor"] != base:
                        raise TimelineContractError("owner initial replay changed its base")
                    codec.check_source(frame, token)
                    if not frame["replay_complete"]:
                        raise OwnerReadError("cursor-unavailable", "initial history requires fresh reconciliation")
                    seed = codec.events(page, token)[:1]
            self.codec = codec
            result = codec.snapshot(frame, token)
            if seed and result.availability.value == "observed":
                events = (*seed, *result.events)[-64:]
                result = replace(result, events=events, before=events[0].position)
            if HISTORY_CAPABILITY not in self.commands.capabilities:
                result = replace(result, before=None)
            self.initial, self._cursor = result, result.position
            return result

        return await owned(read, stop, cancelled)

    async def observe(
        self, token: SourceToken, *, stop: asyncio.Event,
    ) -> AsyncGenerator[Observation, None]:
        codec, initial = self.codec, self.initial
        if codec is None or initial is None or initial.token != token or initial.position is None:
            raise OwnerReadError("unavailable", "selected journal has not been prepared")
        cancelled = Event()
        stream = self.commands.frames(cancelled, follow=True, after=initial.position.cursor)
        cursor = initial.position.cursor
        try:
            yield initial
            first = True
            while not stop.is_set():
                raw = await owned(lambda: next(stream, None), stop, cancelled)
                if stop.is_set():
                    return
                if raw is None:
                    raise OwnerReadError("unavailable", "owner subscription ended")
                value = codec.decode(encoded(raw).encode("utf-8"))
                codec.check_source(value, token)
                kind = value["type"]
                if first and (kind != "snapshot" or value["base_cursor"] != cursor):
                    raise TimelineContractError("owner feed omitted its selected initial snapshot")
                first = False
                if kind in {"heartbeat", "going-away"}:
                    if value["cursor"] != cursor:
                        raise TimelineContractError("owner notice changed the accepted live cursor")
                    if kind == "going-away":
                        raise OwnerReadError(value["reason"], "owner observation stream retired")
                    continue
                if kind == "gap":
                    yield codec.gap(value, token)
                    continue
                if kind not in {"snapshot", "delta"} or value["base_cursor"] != cursor:
                    raise TimelineContractError("owner update lost its exact live boundary")
                if kind == "delta" and value["availability"] == "observed":
                    tip = codec.position(token, value["cursor"])
                    yield LiveDelta(token, codec.position(token, cursor), tip, codec.events(value, token))
                    value = {**value, "entries": []}
                yield codec.snapshot(value, token)
                cursor = value["cursor"]
                self._cursor = codec.position(token, cursor)
                if value["availability"] == "head-changed":
                    return
        finally:
            cancelled.set()
            await settled(asyncio.create_task(asyncio.to_thread(stream.close)))

    async def history(
        self, token: SourceToken, before: SourcePosition, *, stop: asyncio.Event,
    ) -> HistoryPage | TimelineGap:
        codec, initial = self.codec, self.initial
        if codec is None or initial is None or initial.token != token or not before.matches(token.identity):
            raise TimelineContractError("history belongs to another selected journal")
        cancelled = Event()

        def read() -> HistoryPage | TimelineGap:
            raw = self.commands.point(
                cancelled, history=True, source_generation=token.identity.generation, before=before.cursor,
            )
            value = codec.decode(encoded(raw).encode("utf-8"))
            return codec.page(value, token, before)

        return await owned(read, stop, cancelled)
