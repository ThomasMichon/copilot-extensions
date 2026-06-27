"""ACP client -- wraps a Copilot CLI subprocess running in ACP mode.

Uses the ``agent-client-protocol`` SDK for protocol framing. Implements
the ``Client`` interface to receive streaming session updates (response
chunks, thoughts, tool calls, permissions) and routes them to the
session's EventLog for SSE consumers.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import signal
import sys
from collections.abc import Callable
from typing import Any

from acp import PROTOCOL_VERSION, Client, RequestError, text_block
from acp.client.connection import ClientSideConnection
from acp.schema import (
    AgentMessageChunk,
    AgentPlanUpdate,
    AgentThoughtChunk,
    AvailableCommandsUpdate,
    ClientCapabilities,
    ConfigOptionUpdate,
    CreateTerminalResponse,
    CurrentModeUpdate,
    Implementation,
    KillTerminalResponse,
    PermissionOption,
    ReadTextFileResponse,
    ReleaseTerminalResponse,
    RequestPermissionResponse,
    SessionInfoUpdate,
    TerminalOutputResponse,
    TextContentBlock,
    ToolCallProgress,
    ToolCallStart,
    ToolCallUpdate,
    UsageUpdate,
    UserMessageChunk,
    WaitForTerminalExitResponse,
    WriteTextFileResponse,
)

from . import __version__
from .procgroup import safe_killpg

log = logging.getLogger("agent-bridge")

# Tool-call statuses that mean the call has finished. Mirrors
# events._TERMINAL_TOOL_STATUSES / render._TERMINAL_TOOL_STATUS, duplicated here
# so the ACP client has no dependency on the event-log or display layers.
_TERMINAL_TOOL_STATUSES = frozenset(
    {
        "completed", "complete", "success", "succeeded",
        "failed", "error", "cancelled", "canceled",
    }
)

# -- Background-task (sub-agent) detection --------------------------------------
#
# Copilot's `task` tool can launch a sub-agent in *background* mode. The
# orchestrator turn then returns ``end_turn`` while the sub-agent keeps running
# in the same Copilot process (its bash/tool calls stream in after the turn
# settles, and the orchestrator auto-wakes when it completes). Tearing the
# process down in that window kills in-flight background work -- exactly what a
# conversation "waiting on the PR daemon or another agent session" must not
# suffer. There is no structured ACP field for this, so we parse the `task`
# tool's human-readable output (the only authoritative signal Copilot emits):
#
#   launch     -> "Agent started in background with agent_id: <id>. ..."
#   completion -> a later read_agent / task-wait result naming the same
#                 ``agent_id: <id>`` with ``status: completed|failed|...``
#                 (or "Agent is idle (waiting for messages). ... status: idle").
#
# An agent is "active background work" from launch until the first time we
# observe it in a terminal-or-idle status. Idle counts as not-active: an idle
# sub-agent is parked waiting for messages, not making progress, so it does not
# need the connection held open. The match is deliberately tolerant (the phrase
# is product copy that can drift); a missed completion only over-counts, which
# `force` teardown overrides -- it never silently kills live work.
_BG_TASK_LAUNCH_RE = re.compile(
    r"started in background with agent_id:\s*([A-Za-z0-9][\w-]*)",
    re.IGNORECASE,
)
_BG_TASK_AGENT_ID_RE = re.compile(r"agent_id:\s*([A-Za-z0-9][\w-]*)")
_BG_TASK_STATUS_RE = re.compile(r"status:\s*([A-Za-z_]+)")
# Sub-agent statuses that mean "no longer actively running background work".
_BG_TASK_INACTIVE_STATUSES = frozenset(
    {
        "completed", "complete", "succeeded", "success",
        "failed", "error", "cancelled", "canceled",
        "idle", "stopped",
    }
)


async def _terminate_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Terminate a spawned agent process **and its child tree**.

    ``proc.terminate()`` only signals the direct child. On Windows that child
    is the ``cmd.exe`` batch wrapper, which orphans the ``pwsh -> copilot`` (or
    ``python -> ssh``) tree beneath it -- leaving processes that hold the
    worktree directory open after a session ends. Kill the whole tree.
    """
    pid = proc.pid
    if sys.platform == "win32":
        try:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/PID", str(pid), "/T", "/F",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(killer.wait(), timeout=5.0)
        except (TimeoutError, OSError, ProcessLookupError):
            pass
    else:
        # POSIX: agent spawns use start_new_session, so the child leads its
        # own process group -- signal the whole group, then escalate. Guard
        # against ever signaling the bridge's own group (see procgroup /
        # #1001): if the child unexpectedly shares our group, fall back to
        # the direct child only.
        if not safe_killpg(pid, signal.SIGTERM):
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
    with contextlib.suppress(TimeoutError, ProcessLookupError):
        await asyncio.wait_for(proc.wait(), timeout=5.0)
        return
    # Last resort if still alive.
    with contextlib.suppress(ProcessLookupError):
        if sys.platform != "win32":
            safe_killpg(pid, signal.SIGKILL)
        proc.kill()
    with contextlib.suppress(Exception):
        await asyncio.wait_for(proc.wait(), timeout=3.0)


class _BridgeClientImpl(Client):
    """ACP Client callback implementation.

    Routes session_update notifications to the owning AcpClient, which
    in turn pushes events to the session's EventLog.
    """

    def __init__(self, owner: AcpClient) -> None:
        self._owner = owner

    async def request_permission(
        self,
        options: list[PermissionOption],
        session_id: str,
        tool_call: ToolCallUpdate,
        **kwargs: Any,
    ) -> RequestPermissionResponse:
        return await self._owner._handle_permission_request(options, tool_call)

    async def session_update(
        self,
        session_id: str,
        update: (
            UserMessageChunk
            | AgentMessageChunk
            | AgentThoughtChunk
            | ToolCallStart
            | ToolCallProgress
            | AgentPlanUpdate
            | AvailableCommandsUpdate
            | CurrentModeUpdate
            | ConfigOptionUpdate
            | SessionInfoUpdate
            | UsageUpdate
        ),
        **kwargs: Any,
    ) -> None:
        self._owner._handle_session_update(update)

    # Unsupported server-initiated requests -- reject cleanly
    async def write_text_file(
        self, content: str, path: str, session_id: str, **kw: Any
    ) -> WriteTextFileResponse | None:
        raise RequestError.method_not_found("fs/write_text_file")

    async def read_text_file(
        self, path: str, session_id: str, **kw: Any
    ) -> ReadTextFileResponse:
        raise RequestError.method_not_found("fs/read_text_file")

    async def create_terminal(
        self, command: str, session_id: str, **kw: Any
    ) -> CreateTerminalResponse:
        raise RequestError.method_not_found("terminal/create")

    async def terminal_output(
        self, session_id: str, terminal_id: str, **kw: Any
    ) -> TerminalOutputResponse:
        raise RequestError.method_not_found("terminal/output")

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kw: Any
    ) -> ReleaseTerminalResponse | None:
        raise RequestError.method_not_found("terminal/release")

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kw: Any
    ) -> WaitForTerminalExitResponse:
        raise RequestError.method_not_found("terminal/wait_for_exit")

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kw: Any
    ) -> KillTerminalResponse | None:
        raise RequestError.method_not_found("terminal/kill")

    async def ext_method(self, method: str, params: dict) -> dict:
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict) -> None:
        pass

    def on_connect(self, conn: Any) -> None:
        pass


class ToolCallRecord:
    """Tracks a single tool call during a turn."""

    __slots__ = ("content", "kind", "status", "title", "tool_call_id")

    def __init__(self, tool_call_id: str, title: str, kind: str, status: str) -> None:
        self.tool_call_id = tool_call_id
        self.title = title
        self.kind = kind
        self.status = status
        self.content: list[str] = []

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_call_id": self.tool_call_id,
            "title": self.title,
            "kind": self.kind,
            "status": self.status,
            "content": self.content,
        }


class AcpClient:
    """Wraps a single Copilot CLI subprocess running in ACP mode.

    Handles the ACP protocol (initialize, session/new, session/prompt)
    and pushes streaming events to a callback for the session's EventLog.
    """

    MAX_STDERR_LINES = 50

    def __init__(
        self,
        *,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
        on_permission: (
            Callable[
                [str, list[Any], Any],
                Any,  # Awaitable[RequestPermissionResponse]
            ]
            | None
        ) = None,
    ) -> None:
        self._on_event = on_event
        self._on_permission = on_permission

        self._process: asyncio.subprocess.Process | None = None
        self._connection: ClientSideConnection | None = None
        self._acp_session_id: str | None = None

        # Auto-approve all permission requests (agent-bridge default).
        # Ignored when on_permission is set.
        self.auto_approve = True

        # Streaming output buffers for the current turn
        self._response_chunks: list[str] = []
        self._thought_chunks: list[str] = []
        self._tool_calls: dict[str, ToolCallRecord] = {}
        self._prompt_complete = False

        # Background sub-agent tracking. Keyed by Copilot agent_id; value is the
        # tool_call_id of the launching `task` call (or "" if unknown). An entry
        # is present from the moment we see "started in background with
        # agent_id: <id>" until we observe that id reach an inactive status
        # (completed/failed/idle/...). See _BG_TASK_* above. Used to keep the
        # Copilot process alive while sub-agents are doing real work so a
        # teardown does not kill the PR daemon or another waited-on session.
        self._background_tasks: dict[str, str] = {}
        self._prompt_error: str | None = None
        self._stop_reason: str | None = None
        self._pending_permission_future: asyncio.Future[RequestPermissionResponse] | None = None

        # True only while awaiting a prompt turn result. Distinguishes a
        # real mid-turn crash from an idle/just-resumed process exit.
        self._prompt_in_flight = False
        # True while load_session replays conversation history. The replayed
        # session/update notifications are already persisted, so suppress
        # re-emitting them as fresh events.
        self._loading_session = False
        # When False during a load, the replayed history is emitted as normal
        # events (resync rebuilds the log from the agent's authoritative
        # replay instead of suppressing it).
        self._suppress_replay = True

        # Completion event -- set when prompt completes or permission requested
        self._completion_event = asyncio.Event()

        # One prompt at a time
        self._prompt_lock = asyncio.Lock()

        # Stderr capture
        self._stderr_buffer: list[str] = []
        self._stderr_task: asyncio.Task[None] | None = None

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def acp_session_id(self) -> str | None:
        return self._acp_session_id

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    @property
    def active_background_tasks(self) -> list[str]:
        """Copilot agent_ids of background sub-agents still doing live work.

        An id appears here from launch ("started in background with
        agent_id: <id>") until it is first seen in an inactive status
        (completed/failed/idle/...). Sorted for stable output.
        """
        return sorted(self._background_tasks)

    @property
    def has_active_background_tasks(self) -> bool:
        """True while any background sub-agent is still running.

        Teardown gates on this so the Copilot process -- and the in-process
        sub-agents it hosts -- survives while a conversation waits on the PR
        daemon or another agent session.
        """
        return bool(self._background_tasks)

    # -- Lifecycle -----------------------------------------------------------

    async def start(self, process: asyncio.subprocess.Process) -> None:
        """Initialize ACP protocol on an already-spawned subprocess."""
        self._process = process

        if not process.stdin or not process.stdout:
            raise RuntimeError("Process must have piped stdin and stdout")

        # Start stderr reader (keep a reference so the task is not GC'd)
        if process.stderr:
            self._stderr_task = asyncio.create_task(self._read_stderr())

        # Create ACP client-side connection
        client_impl = _BridgeClientImpl(self)
        self._connection = ClientSideConnection(
            client_impl,
            process.stdin,
            process.stdout,
        )

        # Initialize the ACP connection
        await self._connection.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=ClientCapabilities(),
            client_info=Implementation(
                name="agent-bridge",
                version=__version__,
            ),
        )

    async def new_session(self, cwd: str) -> str:
        """Create a new ACP session. Returns the ACP session ID."""
        if not self._connection:
            raise RuntimeError("ACP connection not initialized")
        result = await self._connection.new_session(cwd=cwd, mcp_servers=[])
        self._acp_session_id = result.session_id
        return result.session_id

    async def load_session(
        self, cwd: str, session_id: str, suppress_replay: bool = True,
    ) -> None:
        """Reload a previously persisted ACP session (for resume).

        Per the ACP spec, the agent streams the entire conversation history
        back as session/update notifications during load. By default those
        events are suppressed (``suppress_replay=True``) because they are
        already persisted in this session's event log -- otherwise resume
        duplicates the last messages.

        Pass ``suppress_replay=False`` to let the replay flow through as
        normal events (the resync flow uses this to rebuild a truncated log
        from the agent's authoritative history).
        """
        if not self._connection:
            raise RuntimeError("ACP connection not initialized")
        self._loading_session = True
        self._suppress_replay = suppress_replay
        try:
            await self._connection.load_session(
                cwd=cwd, session_id=session_id, mcp_servers=[],
            )
        finally:
            self._loading_session = False
            self._suppress_replay = True
        self._acp_session_id = session_id

    async def send_prompt(self, text: str) -> dict[str, Any]:
        """Send a prompt and block until the turn completes.

        Returns a dict with the full turn result (response_text,
        thought_text, tool_calls, stop_reason, error).
        """
        if not self._connection or not self._acp_session_id:
            raise RuntimeError("No active ACP session")

        async with self._prompt_lock:
            self._reset_buffers()
            self._prompt_in_flight = True

            try:
                result = await self._connection.prompt(
                    session_id=self._acp_session_id,
                    prompt=[text_block(text)],
                )
                self._stop_reason = result.stop_reason
                self._prompt_complete = True
                self._emit("turn_complete", {
                    "stop_reason": result.stop_reason,
                })
            except Exception as exc:
                self._prompt_error = str(exc)
                self._prompt_complete = True
                self._emit("error", {"message": str(exc)})
                raise
            finally:
                self._prompt_in_flight = False

            return self._build_turn_result()

    async def cancel_prompt(self) -> None:
        """Cancel the current prompt via ACP session/cancel."""
        if self._connection and self._acp_session_id and not self._prompt_complete:
            await self._connection.cancel(session_id=self._acp_session_id)

    async def shutdown(self) -> None:
        """Shut down the ACP connection and process."""
        # Cancel any pending permission
        if self._pending_permission_future and not self._pending_permission_future.done():
            self._pending_permission_future.set_result(
                RequestPermissionResponse(outcome={"outcome": "cancelled"})
            )
            self._pending_permission_future = None

        if self._connection:
            with contextlib.suppress(Exception):
                await self._connection.close()
            self._connection = None

        proc = self._process
        if proc and proc.returncode is None:
            await _terminate_process_tree(proc)
        self._process = None

        # The process (and the in-process sub-agents it hosted) is gone; drop
        # any background-task tracking so a discarded client never reports
        # stale active tasks.
        self._background_tasks.clear()

    # -- Event emission ------------------------------------------------------

    def _emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Push event to the session's callback."""
        if self._on_event:
            try:
                self._on_event(event_type, data)
            except Exception:
                log.warning("Event callback error for %s", event_type, exc_info=True)

    # -- Session update handling ---------------------------------------------

    def _handle_session_update(self, update: Any) -> None:
        """Process ACP session update notifications."""
        # During load_session the agent replays the full conversation as
        # session/update notifications. By default those events are already
        # persisted, so ignore them to avoid duplicating prior messages on
        # resume. The resync flow clears suppression so the replay rebuilds
        # a truncated log from the agent's authoritative history.
        if self._loading_session and self._suppress_replay:
            return

        if isinstance(update, AgentMessageChunk):
            content = update.content
            if isinstance(content, TextContentBlock):
                self._response_chunks.append(content.text)
                self._emit("agent_message", {"text": content.text})

        elif isinstance(update, UserMessageChunk):
            # User prompts are normally tracked by the client (the bridge
            # sends them via prompt()), so they are NOT emitted during a live
            # turn -- that would duplicate the consumer's own record. But on a
            # load replay (resync), the agent re-streams the user's turns as
            # the only source of them, so capture them there to preserve the
            # user's messages in the rebuilt log. ``content`` is the v2 user
            # message field the chat UX renders.
            if self._loading_session:
                content = update.content
                if isinstance(content, TextContentBlock):
                    self._emit("user_message", {"content": content.text})

        elif isinstance(update, AgentThoughtChunk):
            content = update.content
            if isinstance(content, TextContentBlock):
                self._thought_chunks.append(content.text)
                self._emit("agent_thought", {"text": content.text})

        elif isinstance(update, AgentPlanUpdate):
            entries = getattr(update, "entries", None)
            if entries and isinstance(entries, list) and len(entries) > 0:
                active = next(
                    (e for e in entries if getattr(e, "status", None) == "in_progress"),
                    entries[-1],
                )
                title = getattr(active, "title", None)
                if title:
                    self._emit("plan_update", {"title": title})

        elif isinstance(update, ToolCallStart):
            tc = ToolCallRecord(
                tool_call_id=update.tool_call_id,
                title=update.title or "",
                kind=getattr(update, "kind", "other") or "other",
                status=getattr(update, "status", "pending") or "pending",
            )
            self._tool_calls[update.tool_call_id] = tc
            self._emit("tool_call_start", {
                "tool_call_id": tc.tool_call_id,
                "title": tc.title,
                "kind": tc.kind,
                "raw_input": getattr(update, "raw_input", None),
            })

        elif isinstance(update, ToolCallProgress):
            status = getattr(update, "status", None)
            existing = self._tool_calls.get(update.tool_call_id)
            if existing:
                if status:
                    existing.status = status
                content = getattr(update, "content", None)
                if content:
                    for c in content:
                        text = getattr(getattr(c, "content", None), "text", None)
                        if text:
                            existing.content.append(text)
            # Only the TERMINAL update carries the accumulated content + raw_output.
            # They are consumed solely at completion (render._render_tool_update
            # emits content only on a terminal status; the ACP-WS re-emit and
            # active_tool_call ignore content entirely). Sending the growing
            # accumulation on every in-progress update is O(n^2) in storage, CPU
            # (json.dumps), and SSE fan-out, and the per-event commit backpressures
            # the ACP read loop -- stalling a remote agent over SSH (dotfiles #99).
            terminal = bool(status) and str(status).lower() in _TERMINAL_TOOL_STATUSES
            raw_output = getattr(update, "raw_output", None) if terminal else None
            self._emit("tool_call_update", {
                "tool_call_id": update.tool_call_id,
                "status": status,
                "content": list(existing.content) if (terminal and existing) else [],
                "raw_output": raw_output,
            })
            # A `task` tool's launch/completion is only legible in its terminal
            # text output, so scan it once the call has settled.
            if terminal:
                self._scan_background_tasks(
                    update.tool_call_id,
                    existing.content if existing else None,
                    raw_output,
                )

        elif isinstance(update, UsageUpdate):
            self._emit("usage_update", {
                "input_tokens": getattr(update, "input_tokens", None),
                "output_tokens": getattr(update, "output_tokens", None),
                "model": getattr(update, "model", None),
                "context_size": update.size,
                "context_used": update.used,
            })

        elif isinstance(update, SessionInfoUpdate):
            self._emit("session_info", {
                "session_id": getattr(update, "session_id", None),
            })

    def _scan_background_tasks(
        self,
        tool_call_id: str,
        content: list[str] | None,
        raw_output: Any,
    ) -> None:
        """Track background sub-agents from a settled `task` tool's output.

        Copilot exposes no structured background-task signal, so the launch and
        completion of a background sub-agent are recovered from the `task`
        tool's human-readable result text (see _BG_TASK_* above):

          * launch     -> "...started in background with agent_id: <id>..."
          * completion -> a later read_agent/task-wait result naming the same
                          ``agent_id: <id>`` with an inactive ``status:`` (or an
                          "Agent is idle" line).

        Launch wins ties: a single tool result never both starts and finishes
        the same id, and the launch phrase has no ``status:`` field, so the two
        branches are mutually exclusive in practice.
        """
        parts: list[str] = []
        if content:
            parts.extend(content)
        if raw_output is not None:
            parts.append(raw_output if isinstance(raw_output, str) else repr(raw_output))
        if not parts:
            return
        text = "\n".join(parts)

        launched = False
        for match in _BG_TASK_LAUNCH_RE.finditer(text):
            agent_id = match.group(1)
            if agent_id not in self._background_tasks:
                self._background_tasks[agent_id] = tool_call_id
                launched = True
                log.info("background sub-agent started: %s", agent_id)
                self._emit("background_task_started", {
                    "agent_id": agent_id,
                    "tool_call_id": tool_call_id,
                    "active_background_tasks": self.active_background_tasks,
                })
        if launched:
            return

        # Completion: a status line for an already-tracked agent_id reaching an
        # inactive state. A `task` result can mention an id without a status
        # (e.g. a launch confirmation handled above); only act on a status.
        status_match = _BG_TASK_STATUS_RE.search(text)
        if not status_match:
            return
        status = status_match.group(1).lower()
        if status not in _BG_TASK_INACTIVE_STATUSES:
            return
        for id_match in _BG_TASK_AGENT_ID_RE.finditer(text):
            agent_id = id_match.group(1)
            if self._background_tasks.pop(agent_id, None) is not None:
                log.info("background sub-agent finished (%s): %s", status, agent_id)
                self._emit("background_task_finished", {
                    "agent_id": agent_id,
                    "status": status,
                    "active_background_tasks": self.active_background_tasks,
                })

    async def _handle_permission_request(
        self,
        options: list[PermissionOption],
        tool_call: ToolCallUpdate,
    ) -> RequestPermissionResponse:
        """Handle a permission request from the agent."""
        option_dicts = [
            {"optionId": o.option_id, "name": o.name, "kind": o.kind}
            for o in options
        ]
        title = getattr(tool_call, "title", None) or "Unknown tool call"

        # Delegate to external callback if set (e.g., upstream ACP forwarding)
        if self._on_permission:
            session_id = self._acp_session_id or ""
            self._emit("permission_forwarding", {
                "title": title,
                "options": option_dicts,
            })
            return await self._on_permission(session_id, options, tool_call)

        if self.auto_approve:
            allow_option = next(
                (o for o in option_dicts if o.get("kind") == "allow_always"),
                next(
                    (o for o in option_dicts if o.get("kind") == "allow_once"),
                    option_dicts[0] if option_dicts else None,
                ),
            )
            option_id = allow_option["optionId"] if allow_option else "allow_always"

            self._emit("permission_resolved", {
                "title": title,
                "outcome": option_id,
                "auto": True,
            })

            return RequestPermissionResponse(
                outcome={"outcome": "selected", "optionId": option_id}
            )

        # Manual mode -- emit event and block
        self._emit("permission_request", {
            "title": title,
            "options": option_dicts,
        })
        loop = asyncio.get_running_loop()
        self._pending_permission_future = loop.create_future()
        self._completion_event.set()
        return await self._pending_permission_future

    # -- Buffer management ---------------------------------------------------

    def _reset_buffers(self) -> None:
        """Reset buffers for a new turn."""
        self._response_chunks = []
        self._thought_chunks = []
        self._tool_calls = {}
        self._prompt_complete = False
        self._prompt_error = None
        self._stop_reason = None
        self._pending_permission_future = None
        self._completion_event = asyncio.Event()

    def _build_turn_result(self) -> dict[str, Any]:
        """Build the structured result for a completed turn."""
        return {
            "response_text": "".join(self._response_chunks),
            "thought_text": "".join(self._thought_chunks),
            "tool_calls": [tc.to_dict() for tc in self._tool_calls.values()],
            "stop_reason": self._stop_reason,
            "error": self._prompt_error,
        }

    # -- Stderr reader -------------------------------------------------------

    async def _read_stderr(self) -> None:
        """Background task to capture child stderr."""
        if not self._process or not self._process.stderr:
            return
        with contextlib.suppress(Exception):
            while True:
                line = await self._process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", errors="replace").rstrip()
                if os.environ.get("AGENT_BRIDGE_DEBUG"):
                    log.info("[child stderr] %s", text)
                self._stderr_buffer.append(text)
                if len(self._stderr_buffer) > self.MAX_STDERR_LINES:
                    self._stderr_buffer = self._stderr_buffer[-self.MAX_STDERR_LINES:]

        self._handle_child_exit()

    def _handle_child_exit(self) -> None:
        """Handle the child process exiting.

        Only a real error if a prompt turn was actually in flight. An idle
        or just-resumed process exiting (e.g. after a stop) is not an
        "unexpected" crash and must not emit an error.
        """
        if self._prompt_in_flight and not self._prompt_error:
            rc = self._process.returncode if self._process else None
            stderr_tail = "\n".join(self._stderr_buffer[-10:]) if self._stderr_buffer else ""
            self._prompt_error = (
                f"Child process exited unexpectedly (code={rc})"
                + (f"\n{stderr_tail}" if stderr_tail else "")
            )
            self._prompt_complete = True
            self._completion_event.set()
            self._emit("error", {"message": self._prompt_error})
