"""Upstream ACP agent interface -- presents agent-bridge as an ACP agent.

Implements the ``Agent`` interface from the ``agent-client-protocol`` SDK
so that any ACP client (chat UIs, other Copilot CLI instances) can connect
to agent-bridge over stdio. The bridge routes prompts to downstream agents
and streams session updates back to the upstream client.

Usage::

    agent-bridge agent --agent lambda-core-wsl

This spawns agent-bridge in ACP agent mode on stdio. The upstream client
connects via ``copilot --acp --stdio`` or equivalent.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from . import __version__
from acp import (
    PROTOCOL_VERSION,
    Agent,
    RequestError,
    start_tool_call,
    update_agent_message_text,
    update_agent_thought_text,
    update_tool_call,
)
from acp.agent.connection import AgentSideConnection
from acp.schema import (
    AgentCapabilities,
    AllowedOutcome,
    AudioContentBlock,
    ClientCapabilities,
    CloseSessionResponse,
    EmbeddedResourceContentBlock,
    ImageContentBlock,
    Implementation,
    InitializeResponse,
    ListSessionsResponse,
    LoadSessionResponse,
    NewSessionResponse,
    PermissionOption,
    PromptResponse,
    RequestPermissionResponse,
    ResourceContentBlock,
    ResumeSessionResponse,
    SessionCapabilities,
    SessionCloseCapabilities,
    SessionInfo,
    SessionListCapabilities,
    SessionResumeCapabilities,
    TextContentBlock,
    ToolCallUpdate,
    UsageUpdate,
)

from .agent_registry import AgentResolver
from .events import SseEvent
from .models import SessionStatus
from .session_manager import Session, SessionManager
from .transport import SpawnTarget

log = logging.getLogger("agent-bridge")

# Valid ACP stop reasons
_VALID_STOP_REASONS = frozenset({
    "end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled",
})


def _normalize_stop_reason(reason: str | None) -> str:
    """Clamp stop reason to ACP-valid values."""
    if reason and reason in _VALID_STOP_REASONS:
        return reason
    return "end_turn"


def _extract_text(
    prompt: list[
        TextContentBlock
        | ImageContentBlock
        | AudioContentBlock
        | ResourceContentBlock
        | EmbeddedResourceContentBlock
    ],
) -> str:
    """Concatenate text content blocks from an ACP prompt."""
    parts = []
    for block in prompt:
        if isinstance(block, TextContentBlock):
            parts.append(block.text)
    if not parts:
        raise RequestError.invalid_params("Prompt must contain at least one text block")
    return "".join(parts)


def _event_to_acp_update(event: SseEvent) -> Any | None:
    """Convert an EventLog event to an ACP session update object.

    Returns None for events that have no ACP equivalent (e.g.,
    session_state_changed, permission events).
    """
    data = event.data
    etype = event.event

    if etype == "agent_message":
        text = data.get("text", "")
        if text:
            return update_agent_message_text(text)

    elif etype == "agent_thought":
        text = data.get("text", "")
        if text:
            return update_agent_thought_text(text)

    elif etype == "tool_call_start":
        return start_tool_call(
            tool_call_id=data.get("tool_call_id", ""),
            title=data.get("title", ""),
            kind=data.get("kind"),
        )

    elif etype == "tool_call_update":
        return update_tool_call(
            tool_call_id=data.get("tool_call_id", ""),
            status=data.get("status"),
        )

    elif etype == "usage_update":
        # Map bridge usage data to ACP UsageUpdate (size/used model)
        input_t = data.get("input_tokens") or 0
        output_t = data.get("output_tokens") or 0
        return UsageUpdate(
            session_update="usage_update",
            size=input_t,
            used=output_t,
        )

    return None


class BridgeAgent(Agent):
    """ACP Agent that routes to downstream agents via SessionManager.

    Each BridgeAgent instance targets a single agent name (set via
    ``--agent``). When the upstream client creates a session, BridgeAgent
    resolves the agent to a SpawnTarget and starts a bridge session.
    Prompts are forwarded downstream; streaming events are forwarded
    upstream as ACP session_update notifications.
    """

    def __init__(
        self,
        session_manager: SessionManager,
        *,
        resolver: AgentResolver | None = None,
        default_agent: str | None = None,
    ) -> None:
        self._sm = session_manager
        self._resolver = resolver
        self._default_agent = default_agent
        self._conn: AgentSideConnection | None = None
        self._owned_sessions: set[str] = set()

    def on_connect(self, conn: Any) -> None:
        """Called by the SDK when the upstream connection is established."""
        self._conn = conn

    # -- ACP Protocol Methods ------------------------------------------------

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: ClientCapabilities | None = None,
        client_info: Implementation | None = None,
        **kwargs: Any,
    ) -> InitializeResponse:
        log.info(
            "Upstream ACP initialize (protocol=%d, client=%s)",
            protocol_version,
            client_info.name if client_info else "unknown",
        )
        return InitializeResponse(
            protocol_version=PROTOCOL_VERSION,
            agent_info=Implementation(name="agent-bridge", version=__version__),
            agent_capabilities=AgentCapabilities(
                load_session=True,
                session_capabilities=SessionCapabilities(
                    list=SessionListCapabilities(),
                    close=SessionCloseCapabilities(),
                    resume=SessionResumeCapabilities(),
                ),
            ),
        )

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> NewSessionResponse:
        agent_name = self._default_agent
        target = self._resolve_target(cwd, agent_name)

        # Build permission callback for this session
        permission_cb = self._make_permission_callback()

        session = await self._sm.start_session(
            target,
            agent_name=agent_name,
            permission_callback=permission_cb,
        )

        if session.status == SessionStatus.FAILED:
            raise RequestError.internal_error(
                f"Failed to start session for agent '{agent_name}'"
            )

        self._owned_sessions.add(session.session_id)
        log.info(
            "Upstream new_session -> bridge session %s (agent=%s)",
            session.session_id, agent_name,
        )
        return NewSessionResponse(session_id=session.session_id)

    async def prompt(
        self,
        prompt: list[
            TextContentBlock
            | ImageContentBlock
            | AudioContentBlock
            | ResourceContentBlock
            | EmbeddedResourceContentBlock
        ],
        session_id: str,
        message_id: str | None = None,
        **kwargs: Any,
    ) -> PromptResponse:
        text = _extract_text(prompt)
        session = self._sm.get_session(session_id)
        if not session:
            raise RequestError.invalid_params(f"Session '{session_id}' not found")

        # Capture cursor BEFORE submitting so we don't miss early events
        cursor = session.event_log.latest_id if session.event_log else 0

        # Submit prompt (returns immediately, runs in background)
        await self._sm.submit_prompt(session_id, text)

        # Stream events until turn completes
        stop_reason = await self._forward_events(session, session_id, cursor)

        return PromptResponse(stop_reason=_normalize_stop_reason(stop_reason))

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        session = self._sm.get_session(session_id)
        if session and session.client:
            await session.client.cancel_prompt()

    async def list_sessions(
        self,
        cursor: str | None = None,
        cwd: str | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> ListSessionsResponse:
        # Only list sessions owned by this connection
        sessions = [
            s for s in self._sm.list_sessions()
            if s.session_id in self._owned_sessions
        ]
        return ListSessionsResponse(
            sessions=[
                SessionInfo(
                    session_id=s.session_id,
                    cwd=s.target.cwd,
                    title=s.name,
                )
                for s in sessions
            ],
        )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> LoadSessionResponse | None:
        session = self._sm.get_session(session_id)
        if not session:
            raise RequestError.invalid_params(f"Session '{session_id}' not found")
        if session.status == SessionStatus.STOPPED:
            permission_cb = self._make_permission_callback()
            await self._sm.resume_session(
                session_id, permission_callback=permission_cb,
            )
        self._owned_sessions.add(session_id)
        return LoadSessionResponse()

    async def resume_session(
        self,
        cwd: str,
        session_id: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> ResumeSessionResponse:
        session = self._sm.get_session(session_id)
        if not session:
            raise RequestError.invalid_params(f"Session '{session_id}' not found")
        if session.status == SessionStatus.STOPPED:
            permission_cb = self._make_permission_callback()
            await self._sm.resume_session(
                session_id, permission_callback=permission_cb,
            )
        self._owned_sessions.add(session_id)
        return ResumeSessionResponse()

    async def close_session(self, session_id: str, **kwargs: Any) -> CloseSessionResponse | None:
        await self._sm.end_session(session_id)
        self._owned_sessions.discard(session_id)
        return CloseSessionResponse()

    # -- Unsupported methods -------------------------------------------------

    async def fork_session(self, cwd: str, session_id: str, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("session/fork")

    async def set_config_option(self, config_id: str, session_id: str, value: Any, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("session/setConfigOption")

    async def set_session_mode(self, mode_id: str, session_id: str, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("session/setMode")

    async def set_session_model(self, model_id: str, session_id: str, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("session/setModel")

    async def authenticate(self, method_id: str, **kwargs: Any) -> Any:
        raise RequestError.method_not_found("authenticate")

    async def ext_method(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict[str, Any]) -> None:
        pass

    # -- Internal helpers ----------------------------------------------------

    def _resolve_target(self, cwd: str, agent_name: str | None) -> SpawnTarget:
        """Resolve agent name to a spawn target."""
        if agent_name and self._resolver:
            return self._resolver.resolve(agent_name)
        # Fallback: local agent with upstream cwd
        return SpawnTarget(type="local", cwd=cwd)

    def _make_permission_callback(self):
        """Create a permission callback that forwards to upstream client."""
        conn = self._conn

        async def _forward_permission(
            session_id: str,
            options: list[PermissionOption],
            tool_call: ToolCallUpdate,
        ) -> RequestPermissionResponse:
            if not conn:
                # No upstream connection -- auto-approve
                return RequestPermissionResponse(
                    outcome=AllowedOutcome(
                        outcome="selected", option_id="allow_always",
                    ),
                )
            return await conn.request_permission(
                options=options,
                session_id=session_id,
                tool_call=tool_call,
            )

        return _forward_permission

    async def _forward_events(
        self,
        session: Session,
        session_id: str,
        cursor: int,
    ) -> str | None:
        """Forward EventLog events upstream until turn completes.

        Returns the stop_reason from the turn_complete event.
        """
        if not session.event_log:
            return None

        conn = self._conn
        prompt_task = session._prompt_task

        while True:
            # Wait for events with short timeout so we can check task status
            events = await session.event_log.wait_for_events(cursor, timeout=2.0)

            for event in events:
                cursor = event.id

                # Forward as ACP session update
                acp_update = _event_to_acp_update(event)
                if acp_update and conn:
                    await conn.session_update(session_id, acp_update)

                # Terminal events
                if event.event == "turn_complete":
                    return event.data.get("stop_reason")
                if event.event == "error":
                    return "end_turn"

            # Check if prompt task finished without a terminal event
            if prompt_task and prompt_task.done():
                # Drain any remaining events
                remaining = session.event_log.get_events(cursor)
                for event in remaining:
                    cursor = event.id
                    acp_update = _event_to_acp_update(event)
                    if acp_update and conn:
                        await conn.session_update(session_id, acp_update)
                    if event.event == "turn_complete":
                        return event.data.get("stop_reason")
                # Task done but no turn_complete -- something went wrong
                return "end_turn"

    async def cleanup(self) -> None:
        """Stop all owned sessions on disconnect."""
        for sid in list(self._owned_sessions):
            session = self._sm.get_session(sid)
            if session and session.status in (
                SessionStatus.RUNNING, SessionStatus.IDLE, SessionStatus.STARTING,
            ):
                try:
                    await self._sm.stop_session(sid)
                    log.info("Stopped owned session %s on cleanup", sid)
                except Exception:
                    log.warning("Failed to stop session %s on cleanup", sid, exc_info=True)
        self._owned_sessions.clear()
