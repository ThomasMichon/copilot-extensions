"""Optional Gateway SDK adapter; no listener, forge client or daemon activation."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING

from fleet_manager.gateway_service_channel import BODY_BYTES
from fleet_manager.gateway_service_context import identifier
from fleet_manager.gateway_service_handler import (
    HandlerBinding, RequestDisconnected, ServiceRequest, ServiceResponse,
)
from single_instance_lease import OperationLockedError, OperationOwnershipError

if TYPE_CHECKING:
    from fleet_manager.gateway_service_handler import BoundedServiceResponse

from .watch_replay import MAX_BATCH, ConsumerScope
from .watch_notification import positive_seconds
from .watch_replay_binding import SCOPED_PROTOCOL, ReplayBinding, ReplayBindingMismatch
from .watch_replay_store import (
    ReplayCallerExpired, ReplayServiceIdentity, ReplayStore, ReplayStoreInvalid, json_object,
)

_TARGET = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_PATH = re.compile(r"/(v[12])/watch/([a-z0-9][a-z0-9-]{0,63})/([a-z]+)")
_LOG = logging.getLogger(__name__)
_FIELDS = {
    "register": (
        {"request_id", "issued_at", "number", "until"},
        {"deadline_at"},
    ),
    "inspect": ({"request_id", "issued_at"}, set()),
    "cancel": ({"request_id", "issued_at", "registration_id"}, set()),
    "acknowledge": ({"request_id", "issued_at", "registration_id", "event_id"}, set()),
    "pending": (set(), {"limit"}),
    "capabilities": (set(), set()),
}


@dataclass(frozen=True)
class WatchScopeBinding:
    """Trusted deployment input, never reconstructed from request labels."""

    principal: str
    target: str
    scope: ConsumerScope

    def __post_init__(self) -> None:
        identifier(self.principal)
        if not isinstance(self.target, str) or not _TARGET.fullmatch(self.target):
            raise ValueError("invalid watch target")
        if type(self.scope) is not ConsumerScope:
            raise ValueError("invalid watch scope")


def _response(status: int, value: dict) -> ServiceResponse:
    return ServiceResponse(
        status, (("content-type", "application/json"), ("cache-control", "no-store")),
        json.dumps(value, allow_nan=False).encode("utf-8"),
    )


def _payload(operation: str, value: dict) -> dict:
    required, optional = _FIELDS[operation]
    if not required <= value.keys() or value.keys() - required - optional:
        raise ValueError("invalid watch request fields")
    if operation == "register":
        until = value["until"]
        if not isinstance(until, list) or any(not isinstance(item, str) for item in until):
            raise ValueError("invalid watch transitions")
        value["until"] = tuple(until)
    if operation == "pending" and (
        type(value.get("limit", MAX_BATCH)) is not int
        or not 1 <= value.get("limit", MAX_BATCH) <= MAX_BATCH
    ):
        raise ValueError("invalid watch batch limit")
    return value


class WatchGateway:
    """Bind admitted callers to configured scopes and bounded replay operations.

    Construct only beside the trusted connector. This handler is not standalone
    authentication. Gateway policy still owns channel/caller admission; no
    body/header/query label can supply the principal or account/repository.
    """

    def __init__(
        self, store: ReplayStore, *, fleet: str | None, gateway: str | None, slot: str | None,
        bindings: tuple[WatchScopeBinding, ...],
        checkpoint: Callable[[], None] | None = None,
        workspaces=None,
        attention=None,
        reports=None,
        local: bool = False,
        repositories=None,
    ) -> None:
        if type(local) is not bool:
            raise ValueError("explicit local profile selection required")
        self.local, self.repositories = local, repositories
        if repositories is not None and repositories.store is not store:
            raise ValueError("repository projection must use the existing storage owner")
        if local:
            if (fleet, gateway, slot) != (None, None, None) or store.service_identity is not None:
                raise ValueError("local ingress cannot synthesize Gateway context")
            if repositories is None or not store.repository_continuing:
                raise ValueError("local ingress requires continuing repository storage")
        else:
            for name in (fleet, gateway, slot):
                identifier(name)
        if not local and store.service_identity is not None and store.service_identity != ReplayServiceIdentity(
            fleet, gateway, slot,
        ):
            raise ValueError("service identity differs from replay storage binding")
        selected = {}
        if not isinstance(bindings, tuple):
            raise ValueError("watch scope bindings must be immutable")
        for binding in bindings:
            if type(binding) is not WatchScopeBinding:
                raise ValueError("invalid watch scope binding")
            key = (binding.principal, binding.target)
            if key in selected:
                raise ValueError("duplicate watch scope binding")
            selected[key] = binding.scope
        self.store = store
        if checkpoint is not None and not callable(checkpoint):
            raise ValueError("invalid service checkpoint")
        self._checkpoint = checkpoint
        self.fleet, self.gateway, self.slot = fleet, gateway, slot
        self.bindings = MappingProxyType(selected)
        from .workspace_operations import WorkspaceOperations
        workspaces = {} if workspaces is None else dict(workspaces)
        if any(
            key not in selected or not isinstance(value, WorkspaceOperations) or value.store is not store
            for key, value in workspaces.items()
        ):
            raise ValueError("workspace operations must use an existing admitted scope/storage owner")
        self.workspaces = MappingProxyType(workspaces)
        from .attention_read import AttentionReader
        attention = {} if attention is None else dict(attention)
        if any(
            key not in workspaces or not isinstance(value, AttentionReader)
            or value.workspace is not workspaces[key]
            or value.consumer != selected[key].consumer
            for key, value in attention.items()
        ):
            raise ValueError("attention must use the admitted consumer and existing workspace owners")
        self.attention = MappingProxyType(attention)
        from .report_read import ReportReader
        reports = {} if reports is None else dict(reports)
        if any(
            key not in selected or not isinstance(value, ReportReader)
            or value.consumer_scope != selected[key]
            for key, value in reports.items()
        ):
            raise ValueError("reports must use the existing admitted scope owner")
        self.reports = MappingProxyType(reports)
        self._tasks: set[asyncio.Task[ServiceResponse | BoundedServiceResponse]] = set()
        self._closing = False

    def handler_binding(self, *, response_limit: int = BODY_BYTES) -> HandlerBinding:
        if self.local:
            raise ValueError("local ingress has no authenticated Gateway channel")
        if type(response_limit) is int and response_limit == BODY_BYTES:
            return HandlerBinding(self.fleet, self.gateway, self.handle)
        return HandlerBinding(self.fleet, self.gateway, self.handle, response_limit=response_limit)

    async def handle(self, request: ServiceRequest) -> ServiceResponse | BoundedServiceResponse:
        if self.local:
            return _response(403, {"error": "gateway_channel_not_selected"})
        if type(request) is not ServiceRequest:
            raise TypeError("expected trusted service request")
        try:
            self._check(request)
        except RequestDisconnected:
            return _response(503, {"status": "uncertain", "reason": "watch_request_disconnected"})
        caller = request.context
        if (
            (caller.fleet, caller.gateway, caller.slot) != (self.fleet, self.gateway, self.slot)
            or caller.expires_at <= self.store.clock()
        ):
            return _response(403, {"error": "watch_caller_refused"})
        if request.target.startswith(("/v2/review/", "/v1/attention/")):
            return await self._handle_workspace(request)
        if request.target.startswith(("/v1/read/", "/v2/read/")):
            from .report_gateway import handle
            return await handle(self, request)
        matched = _PATH.fullmatch(request.target)
        if matched is None:
            return _response(404, {"error": "watch_route_not_found"})
        version, target, operation = matched.groups()
        scope = self.bindings.get((caller.principal, target))
        if scope is None:
            return _response(403, {"error": "watch_scope_refused"})
        if operation not in _FIELDS:
            return _response(404, {"error": "watch_route_not_found"})
        if request.method != ("GET" if operation == "capabilities" else "POST"):
            return _response(405, {"error": "watch_method_refused"})
        if len(request.body) > BODY_BYTES:
            return _response(413, {"error": "watch_request_limit"})
        if operation == "capabilities" and request.body:
            return _response(400, {"error": "invalid_watch_request"})
        if request.body and dict(request.headers).get("content-type", "").split(";")[0] != (
            "application/json"
        ):
            return _response(415, {"error": "watch_json_required"})
        try:
            payload = json_object(request.body) if request.body else {}
            expected = None
            if version == "v2" and operation != "capabilities":
                if set(payload) != {"binding", "request"} or not isinstance(payload["request"], dict):
                    raise ValueError("invalid scoped replay request")
                expected = ReplayBinding.from_document(payload["binding"])
                payload = payload["request"]
            payload = _payload(operation, payload)
        except (ValueError, UnicodeError, RecursionError):
            return _response(400, {"error": "invalid_watch_request"})
        try:
            if version == "v2":
                selected = self._binding(request, target)
                if expected is not None and expected != selected:
                    raise ReplayBindingMismatch
                expected = selected
        except ReplayBindingMismatch:
            return _response(409, {"error": "watch_binding_mismatch"})
        if self._closing:
            return _response(503, {"error": "watch_service_draining"})
        if len(self._tasks) >= 8:
            return _response(503, {"error": "watch_service_busy"})
        task = asyncio.create_task(self._execute(
            scope, operation, payload, request, target=target, expected=expected,
        ))
        self._tasks.add(task)
        task.add_done_callback(self._completed)
        # Transport cancellation cannot release an in-progress disk transaction.
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            request.lifetime.close()
            raise

    async def handle_local(self, request):
        from fleet_manager.local_service_handler import LocalServiceRequest
        from .repository_gateway import handle, response
        if type(request) is not LocalServiceRequest or not self.local:
            return response(403, {"error": "local_profile_not_selected"})
        self._check(request)
        if request.context.namespace != self.store.namespace or (
            request.context.expires_at <= self.store.clock()
        ):
            return response(403, {"error": "local_caller_refused"})
        return await handle(self, request)

    def _check(self, request: ServiceRequest) -> None:
        request.lifetime.check()
        if self._checkpoint is not None:
            self._checkpoint()

    async def _handle_workspace(self, request):
        from .workspace_operations import OPERATIONS
        is_attention = request.target.startswith("/v1/attention/")
        prefix = "/v1/attention/" if is_attention else "/v2/review/"
        operations = ("capabilities", "read") if is_attention else OPERATIONS
        matched = re.fullmatch(prefix + r"([a-z0-9][a-z0-9-]{0,63})/([a-z]+(?:-[a-z]+)*)", request.target)
        if matched is None or matched[2] not in operations:
            return _response(404, {"error": "workspace_route_not_found"})
        target, operation = matched.groups()
        owners = self.attention if is_attention else self.workspaces
        workspace = owners.get((request.context.principal, target))
        if workspace is None:
            return _response(404, {"error": "workspace_unsupported"})
        if request.method != "POST":
            return _response(405, {"error": "workspace_method_refused"})
        if len(request.body) > BODY_BYTES:
            return _response(413, {"error": "workspace_request_limit"})
        if dict(request.headers).get("content-type", "").split(";")[0] != "application/json":
            return _response(415, {"error": "workspace_json_required"})
        try:
            payload = json_object(request.body)
            if (
                not {"binding", "request"} <= payload.keys()
                or payload.keys() - {"binding", "request"} - (
                    set() if is_attention else {"workspace_contract"}
                )
                or not isinstance(payload["request"], dict)
            ):
                raise ValueError
            expected = ReplayBinding.from_document(payload["binding"])
            if self._binding(request, target) != expected:
                raise ReplayBindingMismatch
        except ReplayBindingMismatch:
            return _response(409, {"error": "workspace_binding_mismatch"})
        except (ValueError, UnicodeError, RecursionError):
            return _response(400, {"error": "invalid_workspace_request"})
        if self._closing or len(self._tasks) >= 8:
            return _response(503, {"error": "workspace_service_unavailable"})
        task = asyncio.create_task(self._execute_workspace(
            workspace, operation, payload["request"], request, target, expected,
            selection=payload.get("workspace_contract"),
        ))
        self._tasks.add(task)
        task.add_done_callback(self._completed)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            request.lifetime.close()
            raise

    async def _execute_workspace(self, workspace, operation, payload, request, target, expected,
                                 *, selection=None):
        from .attention_read import AttentionUnavailable, MAX_BATCH_BYTES
        from .generation_cache import CacheInvalid
        from .owner_read import OwnerReadResponseTooLarge
        from .workspace_operations import WorkspaceUnavailable

        def checkpoint():
            self._check(request)
            if self._binding(request, target) != expected:
                raise ReplayBindingMismatch

        def validate_selection(intent: dict) -> None:
            from .workspace_operations import PROTOCOL, encoded
            from .workspace_selection import selection_preview

            preview = {
                "protocol": PROTOCOL, "binding": expected.document(), "scope": intent["scope"],
                "result": selection_preview(intent),
            }
            submission = {
                "binding": expected.document(), "request": {
                    "number": payload["number"], "intent": intent,
                    "source_attachment": payload["source_attachment"],
                    "budget_seconds": payload["budget_seconds"],
                },
            }
            if (
                len(json.dumps(preview, allow_nan=False).encode("utf-8")) > BODY_BYTES
                or len(encoded(submission)) > BODY_BYTES
            ):
                raise OwnerReadResponseTooLarge("prepared preview or submission exceeds transport limit")

        try:
            from .workspace_terminal_read import serve
            attention = request.target.startswith("/v1/attention/")
            value = await asyncio.to_thread(
                workspace.execute if attention else serve,
                *((operation, payload) if attention else (workspace, operation, payload)),
                expires_at=request.context.expires_at, checkpoint=checkpoint,
                **({} if attention else {"selection": selection}),
                **({"selection_validator": validate_selection} if operation == "allocate" else {}),
            )
            checkpoint()
            value = dict(value, binding=expected.document())
            limit = MAX_BATCH_BYTES if request.target.startswith("/v1/attention/") else BODY_BYTES
            if len(json.dumps(value, allow_nan=False).encode("utf-8")) > limit:
                return _response(503, {"error": "workspace_response_limit"})
            return _response(200, value)
        except RequestDisconnected:
            return _response(503, {"error": "workspace_request_disconnected"})
        except ReplayBindingMismatch:
            return _response(409, {"error": "workspace_binding_mismatch"})
        except PermissionError:
            return _response(403, {"error": "workspace_owner_refused"})
        except OwnerReadResponseTooLarge:
            return _response(503, {"error": "workspace_response_limit"})
        except (CacheInvalid, ReplayStoreInvalid, OperationOwnershipError):
            return _response(503, {"error": "workspace_state_unavailable"})
        except (WorkspaceUnavailable, AttentionUnavailable, OSError, RuntimeError):
            return _response(503, {"error": "workspace_owner_unavailable"})
        except (ValueError, OverflowError):
            return _response(400, {"error": "invalid_workspace_operation"})

    def _binding(self, request: ServiceRequest, target: str) -> ReplayBinding:
        caller = request.context
        scope = self.bindings.get((caller.principal, target))
        identity = ReplayServiceIdentity(caller.fleet, caller.gateway, caller.slot)
        if (scope is None or self.store.service_identity != identity
                or (caller.fleet, caller.gateway, caller.slot) != (
                    self.fleet, self.gateway, self.slot,
                )):
            raise ReplayBindingMismatch
        return ReplayBinding(
            caller.fleet, caller.gateway, caller.slot, self.store.namespace,
            caller.principal, target, scope.consumer, scope.account, scope.repo,
        )

    def stop_admission(self) -> None:
        """Withdraw readiness before joining already-owned operations."""
        self._closing = True

    def _completed(self, task: asyncio.Task[ServiceResponse | BoundedServiceResponse]) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception() is not None:
            _LOG.error("watch-service-failed code=unexpected-operation-failure")

    async def _execute(
        self, scope: ConsumerScope, operation: str, payload: dict, request: ServiceRequest,
        *, target: str, expected: ReplayBinding | None,
    ) -> ServiceResponse:
        def checkpoint() -> None:
            self._check(request)
            if expected is not None and self._binding(request, target) != expected:
                raise ReplayBindingMismatch

        try:
            value = await asyncio.to_thread(
                self.store.execute, scope, operation, payload,
                expires_at=request.context.expires_at, checkpoint=checkpoint,
            )
            checkpoint()
            if expected is not None and operation == "capabilities":
                value = dict(value, protocol=SCOPED_PROTOCOL, binding=expected.document())
        except ReplayBindingMismatch:
            return _response(409, {"error": "watch_binding_mismatch"})
        except RequestDisconnected:
            return _response(503, {"status": "uncertain", "reason": "watch_request_disconnected"})
        except ReplayCallerExpired:
            return _response(403, {"error": "watch_caller_expired"})
        except OperationLockedError:
            return _response(503, {"error": "watch_store_busy"})
        except (OSError, OperationOwnershipError, ReplayStoreInvalid):
            _LOG.error("watch-service-failed code=watch-store-unavailable")
            return _response(503, {"status": "uncertain", "reason": "watch_store_unavailable"})
        except (ValueError, OverflowError):
            return _response(400, {"error": "invalid_watch_request"})
        return _response(200, value)

    async def drain(self, *, timeout: float) -> None:
        """Stop admission and join disk work, even after its caller disconnected."""
        timeout = positive_seconds(timeout)
        self.stop_admission()
        if self._tasks:
            _, pending = await asyncio.wait(self._tasks, timeout=timeout)
            if pending:
                raise TimeoutError("watch operations are not drained")
