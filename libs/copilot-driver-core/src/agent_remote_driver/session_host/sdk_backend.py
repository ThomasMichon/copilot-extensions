"""Optional public-SDK edge for the existing Session Host, not a second broker."""

from __future__ import annotations

import asyncio
from collections import deque
import json
import re
import secrets
import sys
from typing import Any


MAX_RECORD_BYTES = 1_000_000
MAX_EVENTS = 2_000
MAX_REQUESTS = 64
OUTPUT_ONLY_EVENTS = {
    "assistant.message_delta", "assistant.reasoning_delta",
    "tool.execution_partial_result", "tool.execution_progress", "tool.shell_output",
}


def require_sdk() -> tuple[Any, Any]:
    if sys.version_info < (3, 11):
        raise RuntimeError("The optional SDK backend requires Python 3.11 or newer")
    try:
        from copilot import CopilotClient, RuntimeConnection
    except ImportError as error:
        raise RuntimeError("The selected application does not include the optional SDK backend") from error
    return CopilotClient, RuntimeConnection


def available() -> bool:
    if sys.version_info < (3, 11):
        return False
    from importlib.util import find_spec

    return find_spec("copilot") is not None


def validate_child_argv(argv: list[str]) -> None:
    from ..native_arguments import VALUE_OPTIONS

    forbidden = {
        "--acp", "--stdio", "--resume", "--continue", "--session-id",
        "--prompt", "-p", "--interactive", "-i", "--session-idle-timeout",
    }
    headless = False
    index = 1
    while index < len(argv):
        token = argv[index]
        option = token.split("=", 1)[0]
        if option in forbidden or token == "--" or not token.startswith("-"):
            raise ValueError("SDK native execution requires an unseeded headless runtime")
        if token == "--headless":
            headless = True
        index += 2 if token in VALUE_OPTIONS else 1
    if not argv or not headless:
        raise ValueError("The SDK backend requires an explicit native --headless command")


async def connect_owner(owner, *, token: str, cwd: str, session_id: str = ""):
    client_type, connection_type = require_sdk()
    async def announced_port():
        while line := await owner.stdout.readline():
            match = re.search(rb"listening on port (\d+)", line, re.IGNORECASE)
            if match:
                port = int(match.group(1))
                if 0 < port <= 65535:
                    return port
        raise RuntimeError("The owned native SDK runtime exited before connection readiness")

    port = await asyncio.wait_for(announced_port(), 15)
    client = client_type(connection=connection_type.for_uri(
        f"127.0.0.1:{port}", connection_token=token,
    ))
    try:
        await asyncio.wait_for(client.start(), 15)
        backend = SdkBackend(owner, client, cwd=cwd, session_id=session_id)
        backend._output_drain = asyncio.create_task(backend._drain_native_output())
        return backend
    except BaseException:
        await asyncio.wait_for(client.stop(), 5)
        raise


class AcpError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


class SdkBackend:
    """Translate supported ACP operations on one owned native SDK connection.

    The launcher supplies the native owner and a started public SDK client
    connected to that process. Neither this adapter nor the SDK selects or
    launches another runtime.
    """

    def __init__(self, owner, client, *, cwd: str, session_id: str = "") -> None:
        self._owner = owner
        self._client = client
        self._cwd = cwd
        self._selected_id = session_id
        self._session = None
        self._attempted = False
        self._controlled = True
        self._closed = False
        self._input = bytearray()
        self._requests: set[asyncio.Task] = set()
        self._request_ids: set[object] = set()
        self._replies: dict[str, asyncio.Future] = {}
        self._events: deque = deque()
        self._event_task: asyncio.Task | None = None
        self._inventory_task: asyncio.Task | None = None
        self._bound = asyncio.Event()
        self._epoch = 0
        self._seen: set[str] = set()
        self._streamed: set[str] = set()
        self._prompt: asyncio.Task | None = None
        self._cancelled = False
        self._aborted = False
        self._started = asyncio.Event()
        self._expected_source: str | None = None
        self._foreground = {"known": False, "state": "unknown"}
        self._tasks = {"known": False, "items": []}
        self._schedules = {"known": False, "items": []}
        self.stdout = asyncio.StreamReader(limit=MAX_RECORD_BYTES)
        self.stdin = self
        self._monitor = asyncio.create_task(self._observe_exit())
        self._output_drain: asyncio.Task | None = None

    @property
    def pid(self) -> int | None:
        return self._owner.pid

    @property
    def returncode(self) -> int | None:
        return self._owner.returncode

    async def wait(self) -> int:
        return await self._owner.wait()

    def kill(self) -> None:
        self._owner.kill()

    def _emit(self, value: dict) -> None:
        if not self._closed:
            record = json.dumps(value, ensure_ascii=True).encode() + b"\n"
            if len(record) > MAX_RECORD_BYTES:
                raise ValueError("Native SDK observation exceeds the supported ACP record size")
            self.stdout.feed_data(record)

    def write(self, data: bytes) -> None:
        if self._closed:
            raise ConnectionError("The native SDK backend is closed")
        self._input.extend(data)
        while b"\n" in self._input:
            record, _, remaining = self._input.partition(b"\n")
            self._input = bytearray(remaining)
            if len(record) > MAX_RECORD_BYTES:
                raise ValueError("ACP request exceeds the supported record size")
            request = json.loads(record)
            if not isinstance(request, dict) or request.get("jsonrpc") != "2.0":
                raise ValueError("An ACP JSON-RPC object is required")
            if "method" not in request:
                reply = self._replies.get(request.get("id"))
                if reply is not None and not reply.done():
                    reply.set_result(request)
                continue
            identifier = request.get("id")
            if "id" in request and (
                type(identifier) not in (str, int) or identifier in self._request_ids
            ):
                raise ValueError("A unique ACP request id is required")
            if len(self._requests) >= MAX_REQUESTS:
                self._error(identifier, AcpError(-32001, "SDK request capacity is exhausted"))
                continue
            if "id" in request:
                self._request_ids.add(identifier)
            task = asyncio.create_task(self._dispatch(request))
            self._requests.add(task)
            task.add_done_callback(self._requests.discard)
        if len(self._input) > MAX_RECORD_BYTES:
            raise ValueError("ACP request exceeds the supported record size")

    async def drain(self) -> None:
        await asyncio.sleep(0)

    def _error(self, identifier, error: AcpError) -> None:
        if identifier is not None:
            self._emit({"jsonrpc": "2.0", "id": identifier, "error": {
                "code": error.code, "message": str(error),
            }})

    async def _dispatch(self, request: dict) -> None:
        identifier = request.get("id")
        try:
            result = await self._call(request["method"], request.get("params", {}))
            if "id" in request:
                self._emit({"jsonrpc": "2.0", "id": identifier, "result": result})
        except AcpError as error:
            self._error(identifier, error)
        except Exception:
            self._invalidate(control=True)
            self._error(identifier, AcpError(
                -32001, "Native SDK outcome is unavailable; the operation will not be replayed",
            ))
        finally:
            self._request_ids.discard(identifier)

    def _require_session(self, params: dict) -> None:
        if (
            not self._bound.is_set() or self._session is None
            or params.get("sessionId") != self._session.session_id
        ):
            raise AcpError(-32602, "The bound native session is required")

    async def _call(self, method: str, params: dict) -> dict:
        if not isinstance(params, dict):
            raise AcpError(-32602, "ACP parameters must be an object")
        if method == "initialize":
            return {
                "protocolVersion": 1,
                "agentCapabilities": {"loadSession": True, "promptCapabilities": {}},
                "agentInfo": {"name": "copilot-driver-sdk", "version": "1"},
                "_meta": {
                    "backend": "public-python-sdk",
                    "activity": "copilot-driver/activity",
                    "automaticIdleRetirement": "unavailable",
                    "userInput": "unavailable",
                },
            }
        if not self._controlled:
            raise AcpError(-32001, "Native SDK control is unavailable; owner reconciliation is required")
        if method in {"session/new", "session/load"}:
            return await self._bind(method, params)
        if method == "notifications/initialized":
            return {}
        self._require_session(params)
        if method == "session/prompt":
            return await self._send(params)
        if method == "session/cancel":
            prompt = self._prompt
            if prompt is not None:
                # Abort cannot overtake the original native message admission.
                started = asyncio.create_task(self._started.wait())
                try:
                    await asyncio.wait(
                        (started, prompt), return_when=asyncio.FIRST_COMPLETED,
                    )
                finally:
                    started.cancel()
                    await asyncio.gather(started, return_exceptions=True)
                if self._event_task is not None:
                    await asyncio.shield(self._event_task)
                if not self._controlled:
                    raise AcpError(-32001, "Native SDK control was lost before cancellation")
                if self._prompt is not prompt or not self._started.is_set():
                    return {}
                self._cancelled = True
                await self._session.abort()
            return {}
        if method == "copilot-driver/activity":
            await self._refresh_inventory()
            return self.activity()
        raise AcpError(-32601, "This operation is unsupported by the SDK backend")

    async def _bind(self, method: str, params: dict) -> dict:
        if self._attempted:
            raise AcpError(-32001, "This backend already attempted its one native session binding")
        if params.get("cwd") != self._cwd or params.get("mcpServers", []) != []:
            raise AcpError(-32602, "The launch workspace and native MCP configuration must be preserved")
        native_id = params.get("sessionId")
        if method == "session/load" and (
            not isinstance(native_id, str) or not native_id
            or self._selected_id and native_id != self._selected_id
        ):
            raise AcpError(-32602, "The selected native session is required")
        self._attempted = True
        options = {
            "working_directory": self._cwd, "streaming": True,
            "on_permission_request": self._permission, "on_event": self._observe,
        }
        if method == "session/new":
            if self._selected_id:
                options["session_id"] = self._selected_id
            session = await self._client.create_session(**options)
        else:
            session = await self._client.resume_session(native_id, **options)
        self._session = session
        if not session.session_id or (
            self._selected_id and session.session_id != self._selected_id
        ) or (method == "session/load" and session.session_id != native_id):
            self._invalidate(control=True)
            raise AcpError(-32001, "The native session binding did not match the selected identity")
        if method == "session/load":
            history = [event.to_dict() for event in await session.get_events()]
            if any(
                item.get("type") in {"user.message", "assistant.message"}
                and (not isinstance(item.get("data", {}).get("content"), str)
                     or item["data"].get("attachments"))
                for item in history
            ):
                raise RuntimeError("The selected history cannot be restored by this text-only adapter")
            for event in history:
                self._render(event, replay=True)
        if not self._controlled:
            raise AcpError(-32001, "Native restoration observations are incomplete")
        self._bound.set()
        await self._refresh_inventory()
        return {"sessionId": session.session_id} if method == "session/new" else {}

    async def _send(self, params: dict) -> dict:
        from copilot.session import AgentMessageSource

        parts = params.get("prompt")
        if (
            not isinstance(parts, list) or not parts
            or any(not isinstance(part, dict) or part.get("type") != "text"
                   or not isinstance(part.get("text"), str) for part in parts)
        ):
            raise AcpError(-32602, "The SDK backend currently supports text prompts only")
        if self._prompt is not None:
            raise AcpError(-32001, "A native prompt is already in flight")
        text = "".join(part["text"] for part in parts)
        if not text:
            raise AcpError(-32602, "A nonempty prompt is required")
        self._cancelled = False
        self._aborted = False
        self._started.clear()
        source = AgentMessageSource(agent_id="copilot-driver-" + secrets.token_hex(16))
        self._expected_source = "agent-" + source.agent_id
        self._foreground = {"known": True, "state": "active"}
        self._prompt = asyncio.current_task()
        try:
            # Timeout only stops observation; it never permits replay or another send.
            error = None
            try:
                await self._session.send_and_wait(text, source=source, timeout=24 * 60 * 60)
            except Exception as failure:
                error = failure
            if self._event_task is not None:
                await asyncio.shield(self._event_task)
            if not self._controlled:
                raise AcpError(-32001, "Native observation is incomplete; the prompt outcome is unknown")
            if error is not None and not (self._cancelled and self._aborted):
                raise error
            return {"stopReason": "cancelled" if self._cancelled and self._aborted else "end_turn"}
        finally:
            self._prompt = None
            self._expected_source = None

    async def _permission(self, request, invocation):
        from copilot.generated.rpc import PermissionDecisionApproveOnce, PermissionDecisionReject
        from copilot.session import PermissionNoResult

        if (
            not self._bound.is_set() or self._session is None
            or invocation.get("session_id") != self._session.session_id
            or not self._controlled or self._closed
            or invocation.get("managed_settings_enabled", False)
            or getattr(request, "managed_approval_required", False)
        ):
            return PermissionNoResult()
        identifier = "permission-" + secrets.token_hex(16)
        reply = asyncio.get_running_loop().create_future()
        self._replies[identifier] = reply
        self._emit({
            "jsonrpc": "2.0", "id": identifier, "method": "session/request_permission",
            "params": {
                "sessionId": self._session.session_id,
                "toolCall": {
                    "toolCallId": identifier, "title": f"Native {request.kind} permission",
                    "status": "pending", "rawInput": request.to_dict(),
                },
                "options": [
                    {"optionId": "allow-once", "name": "Allow once", "kind": "allow_once"},
                    {"optionId": "reject-once", "name": "Reject", "kind": "reject_once"},
                ],
            },
        })
        try:
            response = await reply
            outcome = response.get("result", {}).get("outcome", {})
            if (
                self._controlled and not self._closed and outcome.get("outcome") == "selected"
                and outcome.get("optionId") == "allow-once"
            ):
                return PermissionDecisionApproveOnce()
            return PermissionDecisionReject()
        finally:
            self._replies.pop(identifier, None)

    def _invalidate(self, *, control: bool = False) -> None:
        self._epoch += 1
        self._tasks = {"known": False, "items": []}
        self._schedules = {"known": False, "items": []}
        if control:
            self._controlled = False
            self._foreground = {"known": False, "state": "unknown"}
            for future in tuple(self._replies.values()):
                if not future.done():
                    future.set_result({})

    def _observe(self, event) -> None:
        if self._closed:
            return
        self._invalidate()
        if len(self._events) >= MAX_EVENTS:
            self._invalidate(control=True)
            return
        self._events.append(event)
        if self._event_task is None or self._event_task.done():
            self._event_task = asyncio.create_task(self._flush_events())

    async def _flush_events(self) -> None:
        await self._bound.wait()
        try:
            refresh = False
            while self._events:
                value = self._events.popleft().to_dict()
                refresh = refresh or value.get("type") not in OUTPUT_ONLY_EVENTS
                self._render(value)
            if refresh and (self._inventory_task is None or self._inventory_task.done()):
                self._inventory_task = asyncio.create_task(self._refresh_inventory())
        except Exception:
            self._invalidate(control=True)

    def _update(self, update: dict) -> None:
        self._emit({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": self._session.session_id, "update": update,
        }})

    def _render(self, event: dict, *, replay: bool = False) -> None:
        if not self._controlled:
            return
        identifier = event.get("id")
        if not isinstance(identifier, str) or not identifier:
            self._invalidate(control=True)
            return
        if identifier in self._seen:
            return
        if len(self._seen) >= 100_000:
            self._invalidate(control=True)
            return
        self._seen.add(identifier)
        kind, data = event.get("type"), event.get("data", {})
        if kind in {"session.error", "session.shutdown", "session.truncation"}:
            if not replay:
                self._invalidate(control=True)
            return
        if event.get("agentId") or data.get("parentToolCallId"):
            return
        if kind == "session.idle":
            self._foreground = {"known": True, "state": "idle"}
            self._aborted = data.get("aborted") is True
        elif kind in {"user.message", "assistant.turn_start"} and not replay:
            self._foreground = {"known": True, "state": "active"}
        if kind == "user.message" and not replay:
            if self._expected_source is not None and data.get("source") == self._expected_source:
                self._started.set()
            else:
                self._invalidate(control=True)
        if kind == "assistant.message_delta" and not replay:
            self._streamed.add(data.get("messageId", ""))
            self._update({"sessionUpdate": "agent_message_chunk", "content": {
                "type": "text", "text": data.get("deltaContent", ""),
            }})
        elif kind in {"assistant.message", "user.message"}:
            if kind == "user.message" and not replay:
                return
            if kind == "assistant.message" and not replay and data.get("messageId", "") in self._streamed:
                self._streamed.discard(data.get("messageId", ""))
                return
            self._update({
                "sessionUpdate": "user_message_chunk" if kind == "user.message" else "agent_message_chunk",
                "content": {"type": "text", "text": data.get("content", "")},
            })
        elif kind == "tool.execution_start":
            self._update({
                "sessionUpdate": "tool_call", "toolCallId": data["toolCallId"],
                "title": data.get("toolName", "Native tool"), "status": "in_progress",
                "rawInput": data.get("arguments"),
            })
        elif kind == "tool.execution_complete":
            self._update({
                "sessionUpdate": "tool_call_update", "toolCallId": data["toolCallId"],
                "status": "completed" if data.get("success") is True else "failed",
                "rawOutput": data.get("result", data.get("error")),
            })

    async def _refresh_inventory(self) -> None:
        if self._session is None or self._closed or not self._controlled:
            return
        epoch = self._epoch
        try:
            tasks, schedules = await asyncio.gather(
                self._session.rpc.tasks.list(timeout=5),
                self._session.rpc.schedule.list(timeout=5),
            )
            task_items, schedule_items = tasks.to_dict()["tasks"], schedules.to_dict()["entries"]
            if not isinstance(task_items, list) or not isinstance(schedule_items, list):
                raise ValueError("Native inventory must contain lists")
            if epoch == self._epoch and self._controlled and not self._closed:
                self._tasks = {"known": True, "items": task_items}
                self._schedules = {"known": True, "items": schedule_items}
        except Exception:
            self._invalidate()

    def activity(self) -> dict:
        return {
            "sessionId": self._session.session_id if self._session is not None else None,
            "foreground": self._foreground,
            "tasks": self._tasks, "schedules": self._schedules,
            "automaticIdleRetirement": "unavailable",
        }

    async def _observe_exit(self) -> None:
        try:
            await self._owner.wait()
        finally:
            self._invalidate(control=True)
            self.stdout.feed_eof()

    async def _drain_native_output(self) -> None:
        while await self._owner.stdout.read(65536):
            pass

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._invalidate(control=True)
        tasks = set(self._requests)
        tasks.update(task for task in (
            self._event_task, self._inventory_task, self._monitor, self._output_drain,
        ) if task is not None)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            if self.returncode is not None:
                # The explicit native owner may have retired the process before
                # the SDK's detach RPC. Closing this exact transport is not an
                # observer-drain or whole-session-settlement receipt.
                await asyncio.wait_for(self._client.force_stop(), 5)
            else:
                try:
                    if self._session is not None:
                        await asyncio.wait_for(self._session.disconnect(), 5)
                finally:
                    await asyncio.wait_for(self._client.stop(), 5)
        finally:
            self.stdout.feed_eof()
