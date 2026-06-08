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
from typing import Any, Callable

from . import __version__
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

log = logging.getLogger("agent-bridge")


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
    async def write_text_file(self, content: str, path: str, session_id: str, **kw: Any) -> WriteTextFileResponse | None:
        raise RequestError.method_not_found("fs/write_text_file")

    async def read_text_file(self, path: str, session_id: str, **kw: Any) -> ReadTextFileResponse:
        raise RequestError.method_not_found("fs/read_text_file")

    async def create_terminal(self, command: str, session_id: str, **kw: Any) -> CreateTerminalResponse:
        raise RequestError.method_not_found("terminal/create")

    async def terminal_output(self, session_id: str, terminal_id: str, **kw: Any) -> TerminalOutputResponse:
        raise RequestError.method_not_found("terminal/output")

    async def release_terminal(self, session_id: str, terminal_id: str, **kw: Any) -> ReleaseTerminalResponse | None:
        raise RequestError.method_not_found("terminal/release")

    async def wait_for_terminal_exit(self, session_id: str, terminal_id: str, **kw: Any) -> WaitForTerminalExitResponse:
        raise RequestError.method_not_found("terminal/wait_for_exit")

    async def kill_terminal(self, session_id: str, terminal_id: str, **kw: Any) -> KillTerminalResponse | None:
        raise RequestError.method_not_found("terminal/kill")

    async def ext_method(self, method: str, params: dict) -> dict:
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict) -> None:
        pass

    def on_connect(self, conn: Any) -> None:
        pass


class ToolCallRecord:
    """Tracks a single tool call during a turn."""

    __slots__ = ("tool_call_id", "title", "kind", "status", "content")

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
        self._prompt_error: str | None = None
        self._stop_reason: str | None = None
        self._pending_permission_future: asyncio.Future[RequestPermissionResponse] | None = None

        # Completion event -- set when prompt completes or permission requested
        self._completion_event = asyncio.Event()

        # One prompt at a time
        self._prompt_lock = asyncio.Lock()

        # Stderr capture
        self._stderr_buffer: list[str] = []

    @property
    def pid(self) -> int | None:
        return self._process.pid if self._process else None

    @property
    def acp_session_id(self) -> str | None:
        return self._acp_session_id

    @property
    def is_running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    # -- Lifecycle -----------------------------------------------------------

    async def start(self, process: asyncio.subprocess.Process) -> None:
        """Initialize ACP protocol on an already-spawned subprocess."""
        self._process = process

        if not process.stdin or not process.stdout:
            raise RuntimeError("Process must have piped stdin and stdout")

        # Start stderr reader
        if process.stderr:
            asyncio.create_task(self._read_stderr())

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

    async def load_session(self, cwd: str, session_id: str) -> None:
        """Reload a previously persisted ACP session (for resume)."""
        if not self._connection:
            raise RuntimeError("ACP connection not initialized")
        await self._connection.load_session(
            cwd=cwd, session_id=session_id, mcp_servers=[],
        )
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
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except TimeoutError:
                proc.kill()
                await proc.wait()
        self._process = None

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
        if isinstance(update, AgentMessageChunk):
            content = update.content
            if isinstance(content, TextContentBlock):
                self._response_chunks.append(content.text)
                self._emit("agent_message", {"text": content.text})

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
            })

        elif isinstance(update, ToolCallProgress):
            existing = self._tool_calls.get(update.tool_call_id)
            if existing:
                status = getattr(update, "status", None)
                if status:
                    existing.status = status
                content = getattr(update, "content", None)
                if content:
                    for c in content:
                        text = getattr(getattr(c, "content", None), "text", None)
                        if text:
                            existing.content.append(text)
            self._emit("tool_call_update", {
                "tool_call_id": update.tool_call_id,
                "status": getattr(update, "status", None),
            })

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
        assert self._process and self._process.stderr
        try:
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
        except Exception:
            pass

        # Process exited -- capture as error if prompt was in-flight
        if not self._prompt_complete and not self._prompt_error:
            rc = self._process.returncode if self._process else None
            stderr_tail = "\n".join(self._stderr_buffer[-10:]) if self._stderr_buffer else ""
            self._prompt_error = (
                f"Child process exited unexpectedly (code={rc})"
                + (f"\n{stderr_tail}" if stderr_tail else "")
            )
            self._prompt_complete = True
            self._completion_event.set()
            self._emit("error", {"message": self._prompt_error})
