"""One passive membership subscription through the independently selected Bridge CLI."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Generator
import json
import os
from pathlib import Path
import shutil
from threading import Event
from typing import Any

from plugin_activation import resolve_active_plugins

from . import agenda_transport as transport
from .companion_bridge_codec import MembershipCodec, offset, selected_owner
from .companion_timeline_contract import (
    HistoryPage, LiveDelta, SourcePosition, SourceToken, TimelineContractError,
    TimelineGap, TimelineSnapshot,
)
from .companion_timeline_controller import Observation, TimelineCleanupError
from .companion_worktree_codec import OwnerReadError, encoded
from .companion_worktree_source import owned, settled
from .engine_process import EngineCleanupError

BRIDGE_STARTUP_TIMEOUT = 35.0
BRIDGE_IDLE_TIMEOUT = 15.0


def bridge_command(project_root: str) -> list[str]:
    """Resolve enabled payload attribution without importing its runtime."""
    if not isinstance(project_root, str):
        raise OwnerReadError("unavailable", "selected worktree root must be an absolute path")
    root = Path(project_root)
    if not root.is_absolute() or not root.is_dir():
        raise OwnerReadError("unavailable", "selected worktree root is unavailable")
    report = resolve_active_plugins(plugin_names=("agent-bridge",), project_root=root)
    if report.authority.value != "complete" or len(report.decisions) != 1 or len(report.active) != 1:
        raise OwnerReadError("unsupported", "one unambiguous enabled Bridge payload is required")
    plugin = next(iter(report.active.values()))
    payload = plugin.root_for_scope("project:current") or plugin.root_for_scope("global")
    if payload is None:
        raise OwnerReadError("unsupported", "Bridge payload has no selected activation scope")
    spec = payload / "payload-invocation.json"
    command = payload / "bin" / ("agent-bridge.ps1" if os.name == "nt" else "agent-bridge")
    for path in (payload, spec, command.parent, command):
        if path.is_symlink() or getattr(path.lstat(), "st_reparse_tag", 0):
            raise OwnerReadError("unsupported", "Bridge payload entry is indirect")
    with spec.open("rb") as stream:
        raw = stream.read(32 * 1024 + 1)
    if len(raw) > 32 * 1024:
        raise TimelineContractError("Bridge command declaration exceeded its bound")
    declaration = json.loads(raw)
    if (
        declaration.get("schema") != "copilot-extensions.payload-invocation"
        or declaration.get("command") != "agent-bridge"
        or declaration.get("module") != "agent_bridge"
        or declaration.get("noSelfProvisionEnv") != "AGENT_BRIDGE_NO_SELFPROVISION"
        or not command.is_file()
    ):
        raise OwnerReadError("unsupported", "selected Bridge has no non-provisioning command declaration")
    if os.name == "nt":
        shell = shutil.which("pwsh") or shutil.which("powershell")
        if shell is None:
            raise OwnerReadError("unsupported", "Bridge payload requires an available PowerShell host")
        return [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-File", str(command)]
    return [str(command)]


class MembershipCommands:
    def __init__(self, owner: str, *, cwd: str, project_root: str) -> None:
        self.owner, self.cwd, self.project_root = owner, cwd, project_root
        self.base: list[str] | None = None

    def frames(self, cancelled: Event, *, follow: bool = False, after: str | None = None) -> Generator[dict, None, None]:
        if self.base is None:
            self.base = bridge_command(self.project_root)
        argv = [
            *self.base, "observe-owner", "--owner-ref", self.owner, "--contract-version", "1", "--json",
            *(["--follow"] if follow else []), *(["--after", after] if after is not None else []),
        ]
        try:
            # The owning CLI negotiates the running capability before opening
            # its read. The overlay also fences older payload first-use paths.
            yield from transport.command_frames(
                argv, cancelled=cancelled, cwd=self.cwd,
                environment={"AGENT_BRIDGE_NO_SELFPROVISION": "1"},
                startup_timeout=BRIDGE_STARTUP_TIMEOUT, idle_timeout=BRIDGE_IDLE_TIMEOUT,
            )
        except EngineCleanupError as error:
            raise TimelineCleanupError(str(error)) from error
        except transport.AgendaUnavailable as error:
            raise OwnerReadError(error.code or "unavailable", str(error)) from error
        except ValueError as error:
            raise OwnerReadError("invalid-response", str(error)) from error


class BridgeMembershipSource:
    def __init__(
        self, project: str, worktree: str, _head: str, *, owner_ref: Any,
        cwd: str, project_root: str,
    ) -> None:
        owner = selected_owner(owner_ref, project, worktree)
        self.commands = MembershipCommands(owner, cwd=cwd, project_root=project_root)
        self.codec = MembershipCodec(project, worktree, owner)
        self.initial: TimelineSnapshot | None = None
        self._cursor: str | None = None

    async def prepare(self, epoch: int, *, stop: asyncio.Event) -> TimelineSnapshot:
        cancelled = Event()

        def read() -> TimelineSnapshot:
            stream = self.commands.frames(cancelled)
            try:
                raw = next(stream, None)
                if raw is None:
                    raise InterruptedError("Bridge membership read stopped")
                frame = self.codec.decode(encoded(raw).encode())
                if frame["kind"] != "snapshot":
                    raise TimelineContractError("Bridge membership bootstrap is not a snapshot")
                token = self.codec.token(frame, epoch)
                self.initial = self.codec.apply(frame, token)
                self._cursor = frame["position"]
                return self.initial
            finally:
                stream.close()

        return await owned(read, stop, cancelled)

    async def observe(self, token: SourceToken, *, stop: asyncio.Event) -> AsyncGenerator[Observation, None]:
        initial = self.initial
        if initial is None or initial.token != token or self._cursor is None:
            raise TimelineContractError("Bridge membership selection has not been prepared")
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
                    raise OwnerReadError("unavailable", "Bridge membership subscription ended")
                frame = self.codec.decode(encoded(raw).encode())
                source = frame["source"]
                if offset(frame["position"], source) < offset(cursor, source):
                    raise TimelineContractError("Bridge membership live position regressed")
                if frame["kind"] == "heartbeat":
                    # The owner may scan past unrelated owners on a heartbeat.
                    # Retain transport continuity, not a productive timeline move.
                    cursor = frame["position"]
                    continue
                if frame["kind"] != "delta" or frame["base_position"] != cursor:
                    raise TimelineContractError("Bridge membership lost its exact delivered boundary")
                cursor = frame["position"]
                if not frame["changes"]:
                    continue
                snapshot = self.codec.apply(frame, token)
                delta = self.codec.delta(frame, token)
                if published is None:
                    raise TimelineContractError("Bridge membership lost its accepted baseline")
                yield LiveDelta(token, published, delta.position, delta.events)
                yield snapshot
                published = delta.position
                self._cursor = cursor
        finally:
            cancelled.set()
            await settled(asyncio.create_task(asyncio.to_thread(stream.close)))

    async def history(
        self, token: SourceToken, before: SourcePosition, *, stop: asyncio.Event,
    ) -> HistoryPage | TimelineGap:
        raise OwnerReadError("unsupported", "Bridge membership has bounded live retention, not a history-page operation")
