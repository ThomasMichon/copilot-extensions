"""ACP client-side callbacks, shared by local and remote connection adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from acp import Client, RequestError
from acp.schema import (
    AcceptElicitationResponse, AgentMessageChunk, AgentPlanUpdate, AgentThoughtChunk,
    AvailableCommandsUpdate, CancelElicitationResponse, ConfigOptionUpdate,
    CreateTerminalResponse, CurrentModeUpdate, DeclineElicitationResponse,
    KillTerminalResponse, PermissionOption, ReadTextFileResponse,
    ReleaseTerminalResponse, RequestPermissionResponse, SessionInfoUpdate,
    TerminalOutputResponse, ToolCallProgress, ToolCallStart, ToolCallUpdate,
    UsageUpdate, UserMessageChunk, WaitForTerminalExitResponse, WriteTextFileResponse,
)

if TYPE_CHECKING:
    from .acp_client import AcpClient


class _BridgeClientImpl(Client):
    """Route SDK callbacks into the owning client's existing lifecycle."""

    def __init__(self, owner: AcpClient) -> None:
        self._owner = owner

    async def request_permission(
        self, options: list[PermissionOption], session_id: str,
        tool_call: ToolCallUpdate, **kwargs: Any,
    ) -> RequestPermissionResponse:
        if self._owner._remote_transport is not None:
            return await self._owner._remote_permission(options, session_id, tool_call, kwargs)
        return await self._owner._handle_permission_request(options, tool_call)

    async def create_elicitation(
        self, message: str, mode: Any, **kwargs: Any,
    ) -> AcceptElicitationResponse | DeclineElicitationResponse | CancelElicitationResponse:
        if self._owner._remote_transport is not None:
            return await self._owner._remote_elicitation(message, mode, kwargs)
        return await self._owner._handle_elicitation(message, mode)

    async def complete_elicitation(self, elicitation_id: str, **kwargs: Any) -> None:
        if self._owner._remote_transport is not None:
            self._owner._remote_withdraw(elicitation_id, kwargs)
        else:
            self._owner._withdraw_elicitation(elicitation_id)

    async def session_update(
        self, session_id: str,
        update: (
            UserMessageChunk | AgentMessageChunk | AgentThoughtChunk | ToolCallStart
            | ToolCallProgress | AgentPlanUpdate | AvailableCommandsUpdate
            | CurrentModeUpdate | ConfigOptionUpdate | SessionInfoUpdate | UsageUpdate
        ),
        **kwargs: Any,
    ) -> None:
        if (
            self._owner._remote_transport is not None and not self._owner._loading_session
            and session_id != self._owner._acp_session_id
        ):
            raise ValueError("Remote update session mismatch")
        self._owner._handle_session_update(update)

    async def write_text_file(
        self, content: str, path: str, session_id: str, **kw: Any,
    ) -> WriteTextFileResponse | None:
        raise RequestError.method_not_found("fs/write_text_file")

    async def read_text_file(
        self, path: str, session_id: str, **kw: Any,
    ) -> ReadTextFileResponse:
        raise RequestError.method_not_found("fs/read_text_file")

    async def create_terminal(
        self, command: str, session_id: str, **kw: Any,
    ) -> CreateTerminalResponse:
        raise RequestError.method_not_found("terminal/create")

    async def terminal_output(
        self, session_id: str, terminal_id: str, **kw: Any,
    ) -> TerminalOutputResponse:
        raise RequestError.method_not_found("terminal/output")

    async def release_terminal(
        self, session_id: str, terminal_id: str, **kw: Any,
    ) -> ReleaseTerminalResponse | None:
        raise RequestError.method_not_found("terminal/release")

    async def wait_for_terminal_exit(
        self, session_id: str, terminal_id: str, **kw: Any,
    ) -> WaitForTerminalExitResponse:
        raise RequestError.method_not_found("terminal/wait_for_exit")

    async def kill_terminal(
        self, session_id: str, terminal_id: str, **kw: Any,
    ) -> KillTerminalResponse | None:
        raise RequestError.method_not_found("terminal/kill")

    async def ext_method(self, method: str, params: dict) -> dict:
        raise RequestError.method_not_found(method)

    async def ext_notification(self, method: str, params: dict) -> None:
        pass

    def on_connect(self, conn: Any) -> None:
        pass
