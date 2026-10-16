"""Private idle frontend staging, never running RPC transfer or activation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from functools import wraps
from typing import TYPE_CHECKING, Any, Literal, ParamSpec, TypeVar

from pydantic import TypeAdapter
from zdd.private_protocol import MAX_FRAME_BYTES, Deadline, PrivateIPCError, Refusal

from .resolver_runtime import _encode
from .session_host.acp_boundary import AcpWireBoundary, AcpWireStamp
from .session_host.private_receipt import HostReceipt, VerifiedHostReceipt, decode_host_receipt
from .session_host.protocol import MAX_MESSAGE_BYTES

if TYPE_CHECKING:
    from .acp_client import AcpClient
    from .events import EventLog

_P = ParamSpec("_P")
_T = TypeVar("_T")


def idle_operation(method: Callable[_P, Awaitable[_T]]) -> Callable[_P, Awaitable[_T]]:
    """Fence outer client operations, including their post-RPC reducer work."""
    @wraps(method)
    async def run(*args: _P.args, **kwargs: _P.kwargs) -> _T:
        owner = args[0]
        owner._idle_operations += 1
        try:
            return await method(*args, **kwargs)
        finally:
            owner._idle_operations -= 1
    return run


class IdleAcpTransport:
    """Opt-in byte owner for the SDK's public message-transport seam.

    Normal clients keep the SDK transport. This owner observes the exact bytes,
    including whitespace, so host ACK cannot substitute for parser completion.
    """

    def __init__(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter,
        *, baseline: AcpWireStamp | None = None, inert: bool = False,
    ) -> None:
        self.reader, self.writer = reader, writer
        self.boundary = (
            AcpWireBoundary.from_idle_stamp(baseline) if baseline is not None
            else AcpWireBoundary()
        )
        self.inert = inert
        self.closed = False
        self.sending = 0
        self.loop = asyncio.get_running_loop()

    async def send(self, message: dict[str, Any]) -> None:
        if self.closed or self.inert:
            raise ConnectionError("Private idle ACP transport unavailable")
        raw = (json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8")
        if len(raw) > MAX_MESSAGE_BYTES:
            self.boundary.invalidate()
            raise ValueError("Private idle ACP message exceeds limit")
        self.sending += 1
        try:
            self.writer.write(raw)
            await self.writer.drain()
            self.boundary.observe(raw, to_child=True)
        except BaseException:
            self.boundary.invalidate()
            raise
        finally:
            self.sending -= 1

    async def receive(self) -> dict[str, Any] | None:
        if self.closed or self.inert:
            raise ConnectionError("Private idle ACP transport unavailable")
        try:
            raw = await self.reader.readuntil(b"\n")
        except asyncio.IncompleteReadError as exc:
            if exc.partial:
                self.boundary.invalidate()
            return None
        except asyncio.CancelledError:
            if not self.closed:
                self.boundary.invalidate()
            raise
        except BaseException:
            self.boundary.invalidate()
            raise
        self.boundary.observe(raw, to_child=False)
        if self.boundary._fault:
            raise ValueError("Unsupported private idle ACP wire data")
        return json.loads(raw)

    async def close(self) -> None:
        self.closed = True
        self.writer.close()

    def require_stamp(self, stamp: AcpWireStamp) -> None:
        if (
            asyncio.get_running_loop() is not self.loop or self.closed or self.sending
            or self.reader.at_eof() or self.writer.is_closing()
            or self.reader._buffer or self.writer.transport.get_write_buffer_size()
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        self.boundary.require_unchanged(stamp, stamp.cursor, stamp.cursor)


@dataclass(frozen=True)
class IdlePreferences:
    source: str
    model_override: str | None
    effort_override: str | None
    context_override: str | None
    target: dict[str, Any] | None = field(repr=False)
    launch: dict[str, str] = field(repr=False)
    confirmed: dict[str, str] = field(repr=False)
    provider_intent: bool
    verified_options: list[dict[str, Any]] | None = field(repr=False)


@dataclass(frozen=True)
class IdleTool:
    tool_call_id: str
    title: str
    kind: str
    status: str
    content: list[str] = field(repr=False)


@dataclass(frozen=True)
class IdleReducer:
    response: list[str] = field(repr=False)
    thought: list[str] = field(repr=False)
    tools: list[IdleTool] = field(repr=False)
    background: dict[str, str]
    subagents: dict[str, str | None]
    tool_agents: dict[str, tuple[str, str | None]]
    runs: dict[str, list[str] | None] = field(repr=False)
    prompt_complete: bool
    completion_set: bool
    prompt_error: str | None = field(repr=False)
    stop_reason: str | None
    auto_approve: bool


@dataclass(frozen=True)
class IdleContinuation:
    schema: Literal[1]
    coverage: Literal["idle-sdk-reducer-event-observation-v1"]
    acp_session_id: str
    host: HostReceipt = field(repr=False)
    preferences: IdlePreferences = field(repr=False)
    reducer: IdleReducer = field(repr=False)
    next_event_id: int


_ADAPTER = TypeAdapter(IdleContinuation)


def decode_idle_continuation(payload: object) -> IdleContinuation:
    """Strict bounded private data; event cursor is not independent DB proof."""
    from .acp_preferences import AcpPreferencesMixin
    from .session_preferences import SOURCES

    try:
        wire = _encode(payload)
        if len(wire) > MAX_FRAME_BYTES - 4096:
            raise PrivateIPCError(Refusal.SIZE)
        state = _ADAPTER.validate_json(wire, strict=True)
        if (
            type(state.schema) is not int
            or _encode(json.loads(_ADAPTER.dump_json(state))) != wire
            or not state.acp_session_id or "\0" in state.acp_session_id
            or type(state.next_event_id) is not int or state.next_event_id <= 0
            or state.preferences.source not in SOURCES
            or state.preferences.verified_options is None
            or not state.preferences.confirmed.get("model")
            or AcpPreferencesMixin._selected_preferences(state.preferences.verified_options)
            != state.preferences.confirmed
            or set(state.reducer.runs) != {"message", "thought"}
            or len(state.reducer.tools) != len({tool.tool_call_id for tool in state.reducer.tools})
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
        host = decode_host_receipt(asdict(state.host))
        if host.acp_wire is None:
            raise PrivateIPCError(Refusal.AUTHORITY)
        return state
    except PrivateIPCError:
        raise
    except (TypeError, ValueError, AttributeError, KeyError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


def _sdk_idle(client: AcpClient, *, listening: bool) -> None:
    """Explicit compatibility fence; unknown SDK internals refuse continuation."""
    from acp.client.connection import ClientSideConnection
    from acp.connection import Connection
    from acp.task import TaskSupervisor

    connection = client._connection
    transport = client._idle_transport
    if type(connection) is not ClientSideConnection or type(transport) is not IdleAcpTransport:
        raise PrivateIPCError(Refusal.AUTHORITY)
    try:
        conn = connection._conn
        receive = conn._recv_task
        tasks = conn._tasks
        if (
            type(conn) is not Connection or type(tasks) is not TaskSupervisor
            or conn._transport is not transport or conn._closed or conn._disconnected
            or type(conn._pending) is not dict or conn._pending
            or type(conn._next_request_id) is not int
            or not 0 <= conn._next_request_id < (1 << 63)
            or type(tasks._tasks) is not set or tasks._closed
            or (listening and (type(receive) is not asyncio.Task or receive.done()))
            or (not listening and receive is not None)
            or any(task is not receive for task in tasks._tasks)
            or type(connection._session_updates._pending) is not dict
            or connection._session_updates._pending
        ):
            raise PrivateIPCError(Refusal.AUTHORITY)
    except (AttributeError, TypeError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None


def capture_idle_continuation(
    client: AcpClient, receipt: VerifiedHostReceipt, event_log: EventLog,
    deadline: Deadline,
) -> dict[str, object]:
    """Capture only a processed idle client, not Tasks/Futures or event durability."""
    from .acp_client import AcpClient
    from .events import EventLog

    if (
        type(client) is not AcpClient or type(receipt) is not VerifiedHostReceipt
        or type(event_log) is not EventLog or type(deadline) is not Deadline
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    host = decode_host_receipt(receipt.private_payload(deadline))
    _sdk_idle(client, listening=True)
    if (
        not client._host_mode or not client.is_running or client._host_child_exit_code is not None
        or not client._acp_session_id or not client._preferences_ready
        or client._verified_options is None
        or not client.confirmed_preferences.get("model")
        or client._selected_preferences(client._verified_options) != client.confirmed_preferences
        or client._idle_operations or client._prompt_in_flight or client._prompt_lock.locked()
        or client._loading_session or client._out_of_turn_open
        or client._out_of_turn_settle_handle is not None
        or client._pending_permission_future is not None or client._pending_permission_id is not None
        or client._pending_permission_options or client._pending_elicitations
        or client._pending_ask_user_meta or client._stderr_task is not None
        or client._idle_event_log is not event_log
        or event_log._session_id != host.session_id
        or event_log._telemetry._identity.get("acp_session_id") != client._acp_session_id
        or event_log.merged_into is not None or host.acp_wire is None
        or client._connection._conn._next_request_id != host.acp_wire.next_client_request_id
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    client._idle_transport.require_stamp(host.acp_wire)
    with event_log._lock:
        cursor = event_log._next_id
    payload = _client_snapshot(client, host, cursor)
    decode_idle_continuation(payload)
    deadline.remaining()
    return payload


def _client_snapshot(client: AcpClient, host: HostReceipt, cursor: int) -> dict[str, object]:
    options = client._verified_options
    if options is not None:
        options = [
            option.model_dump(mode="json", by_alias=True, exclude_none=True)
            if hasattr(option, "model_dump") else option
            for option in options
        ]
    state = IdleContinuation(
        1, "idle-sdk-reducer-event-observation-v1", client._acp_session_id, host,
        IdlePreferences(
            client.preference_source, client.model_override, client.effort_override,
            client.context_override, client.target_preferences, client.launch_preferences,
            client.confirmed_preferences, client.provider_intent, options,
        ),
        IdleReducer(
            client._response_chunks, client._thought_chunks,
            [IdleTool(**tool.to_dict()) for tool in client._tool_calls.values()],
            client._background_tasks, client._subagent_attr.subagents,
            client._subagent_attr.tool_call_agents,
            client._subagent_attr.runs, client._prompt_complete,
            client._completion_event.is_set(), client._prompt_error, client._stop_reason,
            client.auto_approve,
        ),
        cursor,
    )
    try:
        payload = json.loads(_encode(asdict(state)))
    except (TypeError, ValueError, AttributeError, RecursionError):
        raise PrivateIPCError(Refusal.AUTHORITY) from None
    return payload


def require_prepared_continuation(
    client: AcpClient, payload: object, log: EventLog,
) -> None:
    """Recheck actual inert reducer, preferences, stream and SDK state."""
    from .acp_client import AcpClient

    state = decode_idle_continuation(payload)
    if type(client) is not AcpClient:
        raise PrivateIPCError(Refusal.AUTHORITY)
    _sdk_idle(client, listening=False)
    with log._lock:
        cursor = log._next_id
    if (
        client._idle_event_log is not log
        or client.is_running or not client._host_mode or client._host_child_exit_code is not None
        or client._host_child_pid != state.host.child_pid or not client._preferences_ready
        or not client._idle_transport.inert or client._idle_operations
        or client._prompt_in_flight or client._prompt_lock.locked()
        or client._loading_session or client._out_of_turn_open
        or client._out_of_turn_settle_handle is not None
        or client._pending_permission_future is not None or client._pending_permission_id is not None
        or client._pending_permission_options or client._pending_elicitations
        or client._pending_ask_user_meta or client._stderr_task is not None
        or client._connection._conn._next_request_id != state.host.acp_wire.next_client_request_id
        or _encode(_client_snapshot(client, state.host, cursor)) != _encode(payload)
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    client._idle_transport.require_stamp(state.host.acp_wire)


def prepare_idle_continuation(
    payload: object, receipt: VerifiedHostReceipt, event_log: EventLog,
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, deadline: Deadline,
    *, on_event: Callable[[str, dict[str, Any]], None] | None = None,
    on_permission: Callable[..., Any] | None = None, closer: Any = None,
) -> AcpClient:
    """Restore an INERT SDK client: no read loop, initialize, replay or adoption.

    An activation owner must later prove the native commit and independent event
    persistence. Until that owner exists this client cannot report itself alive.
    """
    from acp.client.connection import ClientSideConnection
    from .acp_client import AcpClient, ToolCallRecord, _BridgeClientImpl
    from .events import EventLog

    state = decode_idle_continuation(payload)
    if type(receipt) is not VerifiedHostReceipt or type(event_log) is not EventLog:
        raise PrivateIPCError(Refusal.AUTHORITY)
    host = decode_host_receipt(receipt.private_payload(deadline))
    with event_log._lock:
        cursor = event_log._next_id
    if (
        host != state.host or host.acp_wire is None
        or event_log._session_id != host.session_id
        or event_log._telemetry._identity.get("acp_session_id") != state.acp_session_id
        or event_log.merged_into is not None or cursor != state.next_event_id
        or not isinstance(reader, asyncio.StreamReader)
        or not isinstance(writer, asyncio.StreamWriter)
        or reader._buffer or reader.at_eof() or writer.is_closing()
        or writer.transport.get_write_buffer_size()
    ):
        raise PrivateIPCError(Refusal.AUTHORITY)
    pref, reducer = state.preferences, state.reducer
    client = AcpClient(
        on_event=on_event, on_permission=on_permission,
        model_override=pref.model_override, effort_override=pref.effort_override,
        preference_source=pref.source, context_override=pref.context_override,
        target_preferences=pref.target, launch_preferences=pref.launch,
        confirmed_preferences=pref.confirmed, provider_intent=pref.provider_intent,
    )
    client._acp_session_id = state.acp_session_id
    client._verified_options = pref.verified_options
    client._preferences_ready = True
    client._response_chunks, client._thought_chunks = reducer.response, reducer.thought
    for tool in reducer.tools:
        record = ToolCallRecord(tool.tool_call_id, tool.title, tool.kind, tool.status)
        record.content = tool.content
        client._tool_calls[record.tool_call_id] = record
    client._background_tasks = reducer.background
    client._subagent_attr.subagents = reducer.subagents
    client._subagent_attr.tool_call_agents = reducer.tool_agents
    client._subagent_attr.runs = reducer.runs
    client._prompt_complete, client._prompt_error = reducer.prompt_complete, reducer.prompt_error
    client._stop_reason, client.auto_approve = reducer.stop_reason, reducer.auto_approve
    if reducer.completion_set:
        client._completion_event.set()
    client._host_mode, client._host_transport_alive = True, False
    client._host_child_pid, client._host_closer = host.child_pid, closer
    client._idle_event_log = event_log
    client._idle_transport = IdleAcpTransport(reader, writer, baseline=host.acp_wire, inert=True)
    client._connection = ClientSideConnection(
        _BridgeClientImpl(client), client._idle_transport, listening=False,
    )
    client._connection._conn._next_request_id = host.acp_wire.next_client_request_id
    client._subagent_attr.install_route(client._connection)
    try:
        _sdk_idle(client, listening=False)
        deadline.remaining()
    except BaseException:
        client._idle_transport.closed = True
        writer.close()
        raise
    return client
