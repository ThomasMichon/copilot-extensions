"""Remote lifecycle and live, explicit approval handling for the existing client."""

from __future__ import annotations

import asyncio
import copy
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from acp import connect_to_agent
from acp.schema import (
    AcceptElicitationResponse, CancelElicitationResponse, DeclineElicitationResponse,
    Implementation, RequestPermissionResponse,
)
from credential_relay.http_auth import HttpAuthContext, HttpAuthInjector, SourceHttpAuth
from credential_relay.sources.git_credential import GitCredentialSource

from . import __version__
from .remote_acp_config import (
    NAMESPACE, REMOTE_ACP_VERSION, RemoteAcpTarget, capability_metadata, validate_capabilities,
)
from .remote_acp_transport import RemoteTransport, RemoteTransportError
from .remote_acp_logging import remote_sdk_log_scope


class RemoteAcpClientMixin:
    _remote_transport: RemoteTransport | None = None

    async def _lifecycle_rpc(
        self, method: Callable[..., Awaitable[Any]], **kwargs: Any,
    ) -> Any:
        if self._remote_transport is not None:
            return await self._remote_sdk_rpc(method, **kwargs)
        return await self._subagent_attr.call_with_meta(method, **kwargs)

    async def _remote_sdk_rpc(
        self, method: Callable[..., Awaitable[Any]], **kwargs: Any,
    ) -> Any:
        try:
            with remote_sdk_log_scope():
                return await method(**kwargs)
        except Exception as exc:
            reason = f"Remote ACP SDK request failed ({type(exc).__name__}); outcome unknown"
            self._remote_transport.fail(reason)
            raise RemoteTransportError(reason) from None

    async def _adopt_remote_session(self) -> None:
        if self._remote_transport is not None:
            await self._remote_transport.adopt_session(self._acp_session_id)

    async def _prompt_rpc(self, **kwargs: Any) -> Any:
        if self._remote_transport is not None:
            return await self._remote_sdk_rpc(self._connection.prompt, **kwargs)
        return await self._connection.prompt(**kwargs)

    async def start_remote(
        self, target: RemoteAcpTarget, *, auth: HttpAuthInjector | None = None,
        transport: RemoteTransport | None = None, cookies: Any = None,
        auth_factory: Callable[[HttpAuthContext], HttpAuthInjector] | None = None,
    ) -> None:
        from .acp_callbacks import _BridgeClientImpl
        from .acp_subagents import client_capabilities
        from .remote_acp_http import HttpAcpTransport
        from .remote_acp_ws import WsAcpTransport

        if transport is not None and transport.target != target:
            raise ValueError("Remote transport target mismatch")
        self._remote_answers = {}
        self._remote_negotiated = False
        self._remote_transport = transport or (
            HttpAcpTransport(target, auth, cookies=cookies, on_lost=self._remote_lost)
            if target.transport == "streamable-http"
            else WsAcpTransport(target, auth, cookies=cookies, on_lost=self._remote_lost)
        )
        remote = self._remote_transport
        remote.on_lost = self._remote_lost
        remote.on_request_completed = self._remote_rpc_completed
        if auth_factory is not None:
            if auth is not None or remote.auth is not None:
                raise ValueError("Remote credential binding is ambiguous")
            auth = auth_factory(remote.context)
        if auth is not None:
            remote.auth = auth
        if auth is None and remote.auth is None and target.auth.source == "git-credential":
            remote.auth = SourceHttpAuth(
                GitCredentialSource(cache_ttl=0), remote.context, target.endpoint,
                scheme=target.auth.scheme,
            )
        self._host_mode = True
        self._host_transport_alive = True
        self._host_closer = remote.close
        self.auto_approve = False
        try:
            if target.transport == "websocket":
                await remote.open()
            with remote_sdk_log_scope():
                self._connection = connect_to_agent(
                    _BridgeClientImpl(self), remote, use_unstable_protocol=True,
                )
            result = await asyncio.wait_for(
                self._remote_sdk_rpc(self._connection.initialize,
                    protocol_version=REMOTE_ACP_VERSION,
                    client_capabilities=client_capabilities(capability_metadata()),
                    client_info=Implementation(name="agent-bridge", version=__version__),
                ), target.timeout_seconds,
            )
            if result.protocol_version != REMOTE_ACP_VERSION:
                raise PermissionError("Remote ACP protocol version mismatch")
            validate_capabilities(result.agent_capabilities)
            self._remote_can_load = result.agent_capabilities.load_session is True
            self._remote_negotiated = True
        except BaseException:
            await self.shutdown()
            raise

    def _remote_lost(self):
        self._remote_negotiated = False
        self._connection_loss_error = "Remote ACP connection lost; outcome unknown"
        self.mark_transport_lost()
        self._remote_cancel_answers()

    def _remote_cancel_answers(self, *, deliver_cancel: bool = False):
        for entry in getattr(self, "_remote_answers", {}).values():
            if not entry["future"].done():
                if deliver_cancel:
                    response = (
                        RequestPermissionResponse(
                            outcome={"outcome": "cancelled"}, field_meta=entry["meta"],
                        ) if entry["kind"] == "permission"
                        else CancelElicitationResponse(action="cancel", field_meta=entry["meta"])
                    )
                    entry["future"].set_result(response)
                else:
                    entry["future"].cancel()
        getattr(self, "_remote_answers", {}).clear()

    def _remote_metadata(self, kwargs, tool_call_id):
        extension = kwargs.get(NAMESPACE)
        if extension is None:
            extension = (kwargs.get("_meta") or kwargs.get("field_meta") or {}).get(NAMESPACE)
        approval = (extension or {}).get("applicationApproval")
        if approval is None:
            return None, None
        if (
            not isinstance(approval, dict) or type(approval.get("version")) is not int
            or approval.get("version") != 1
            or ("decisionId" in approval and (
                not isinstance(approval["decisionId"], str) or not approval["decisionId"]
            ))
        ):
            raise ValueError("Invalid application approval identity")
        if "decisionId" not in approval and not tool_call_id:
            raise ValueError("Application approval requires durable server identity")
        return approval.get("decisionId", tool_call_id), {NAMESPACE: {
            "applicationApproval": copy.deepcopy(approval),
        }}

    async def _remote_request(self, kind, session_id, tool_call_id, data, kwargs):
        if (
            not self.is_running or not self._remote_negotiated or self._loading_session
            or session_id != self._acp_session_id
            or not isinstance(tool_call_id, str)
            or (not tool_call_id and data.get("native_request_id") is None)
        ):
            raise ValueError("No live remote approval authority")
        decision_id, meta = self._remote_metadata(kwargs, tool_call_id)
        token = uuid.uuid4().hex
        future = asyncio.get_running_loop().create_future()
        if len(self._remote_answers) >= self._remote_transport.target.max_queue_messages:
            raise RemoteTransportError("Remote approval limit exceeded")
        self._remote_answers[token] = {
            "kind": kind, "tool_call_id": tool_call_id, "decision_id": decision_id,
            "future": future, "data": copy.deepcopy(data), "meta": meta,
        }
        self._emit("permission_request" if kind == "permission" else "ask_user_request", {
            **data, "request_id": token, "tool_call_id": tool_call_id,
            "decision_id": decision_id, "remote_session_id": session_id,
            "connection_generation": self._remote_transport.generation,
        })
        self._completion_event.set()
        try:
            try:
                return await asyncio.wait_for(
                    future, self._remote_transport.target.rpc_timeout_seconds,
                )
            except asyncio.TimeoutError:
                reason = "Remote ACP approval deadline exceeded; answer authority expired"
                self._remote_transport.fail(reason)
                raise RemoteTransportError(reason) from None
        finally:
            self._remote_answers.pop(token, None)

    async def _remote_permission(self, options, session_id, tool_call, kwargs):
        data = {
            "title": getattr(tool_call, "title", None),
            "tool_call": tool_call.model_dump(by_alias=True, exclude_none=True),
            "options": [o.model_dump(by_alias=True, exclude_none=True) for o in options],
        }
        return await self._remote_request(
            "permission", session_id, tool_call.tool_call_id, data, kwargs,
        )

    async def _remote_elicitation(self, message, mode, kwargs):
        schema = getattr(mode, "requested_schema", None)
        if schema is None:
            return DeclineElicitationResponse(action="decline")
        if hasattr(schema, "model_dump"):
            schema = schema.model_dump(by_alias=True, exclude_none=True)
        session_id = getattr(mode, "session_id", None)
        native_request_id = getattr(mode, "request_id", None)
        if session_id is None:
            if not self._remote_transport.has_request(native_request_id):
                raise ValueError("Unknown remote elicitation request scope")
            session_id = self._remote_transport.request_session(native_request_id)
        return await self._remote_request(
            "form", session_id, getattr(mode, "tool_call_id", ""),
            {"message": message, "requested_schema": schema,
             "native_request_id": native_request_id}, kwargs,
        )

    def _remote_answer_permission(self, request_id, option_id):
        entry = self._remote_answers.get(request_id)
        if not self.is_running or not entry or entry["kind"] != "permission":
            return False
        if entry["future"].done():
            return False
        if option_id not in {o["optionId"] for o in entry["data"]["options"]}:
            raise ValueError("Unknown permission option")
        entry["future"].set_result(RequestPermissionResponse(
            outcome={"outcome": "selected", "optionId": option_id},
            field_meta=entry["meta"],
        ))
        self._emit("permission_resolved", {
            "request_id": request_id, "tool_call_id": entry["tool_call_id"],
            "decision_id": entry["decision_id"], "auto": False,
            "outcome": {"outcome": "selected", "optionId": option_id},
        })
        return True

    def _remote_answer_form(self, tool_call_id, content, action, request_id):
        entry = self._remote_answers.get(request_id)
        if (
            not self.is_running or not entry or entry["kind"] != "form"
            or entry["tool_call_id"] != tool_call_id or entry["future"].done()
        ):
            return False
        native_request_id = entry["data"].get("native_request_id")
        if native_request_id is not None and not self._remote_transport.has_request(native_request_id):
            return False
        if action not in {"accept", "decline", "cancel"}:
            raise ValueError("Invalid elicitation action")
        cls = {
            "accept": AcceptElicitationResponse, "decline": DeclineElicitationResponse,
            "cancel": CancelElicitationResponse,
        }[action]
        values = {"action": action, "field_meta": entry["meta"]}
        if action == "accept":
            values["content"] = copy.deepcopy(content or {})
        try:
            response = cls.model_validate(values, strict=True)
        except ValueError:
            raise ValueError("Invalid structured elicitation content") from None
        entry["future"].set_result(response)
        self._emit("ask_user_resolved", {
            "request_id": request_id, "tool_call_id": tool_call_id,
            "decision_id": entry["decision_id"], "action": action,
        })
        return True

    def has_pending_elicitation_request(self, request_id: str) -> bool:
        entry = getattr(self, "_remote_answers", {}).get(request_id)
        return bool(
            self._remote_transport is not None and self.is_running and entry
            and entry["kind"] == "form" and not entry["future"].done()
        )

    def _remote_rpc_completed(self, request_id: object) -> None:
        for token, entry in list(getattr(self, "_remote_answers", {}).items()):
            native = entry["data"].get("native_request_id")
            if (
                entry["kind"] != "form" or type(native) is not type(request_id)
                or native != request_id or entry["future"].done()
            ):
                continue
            entry["future"].set_result(CancelElicitationResponse(
                action="cancel", field_meta=entry["meta"],
            ))
            self._emit("ask_user_withdrawn", {
                "request_id": token, "tool_call_id": entry["tool_call_id"],
                "decision_id": entry["decision_id"], "native_request_id": native,
            })

    def _remote_withdraw(self, elicitation_id, kwargs):
        # Native elicitation IDs, tool-call IDs and application decision IDs
        # are different domains. Never guess equality or cancel the sole form.
        try:
            decision_id, _ = self._remote_metadata(kwargs, "")
        except ValueError:
            return
        if decision_id is None:
            return
        matches = [
            (token, entry) for token, entry in self._remote_answers.items()
            if entry["kind"] == "form" and entry["decision_id"] == decision_id
            and not entry["future"].done()
        ]
        if len(matches) != 1:
            return
        token, entry = matches[0]
        entry["future"].set_result(CancelElicitationResponse(
            action="cancel", field_meta=entry["meta"],
        ))
        self._emit("ask_user_withdrawn", {
            "request_id": token, "tool_call_id": entry["tool_call_id"],
            "decision_id": decision_id, "native_elicitation_id": elicitation_id,
        })
