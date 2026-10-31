"""Optional Gateway SDK adapter; no listener, forge client or daemon activation."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from types import MappingProxyType

from fleet_manager.gateway_service_channel import BODY_BYTES
from fleet_manager.gateway_service_context import identifier
from fleet_manager.gateway_service_handler import (
    HandlerBinding, RequestDisconnected, ServiceRequest, ServiceResponse,
)
from single_instance_lease import OperationLockedError, OperationOwnershipError

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
        self, store: ReplayStore, *, fleet: str, gateway: str, slot: str,
        bindings: tuple[WatchScopeBinding, ...],
        checkpoint: Callable[[], None] | None = None,
    ) -> None:
        for name in (fleet, gateway, slot):
            identifier(name)
        if store.service_identity is not None and store.service_identity != ReplayServiceIdentity(
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
        self._tasks: set[asyncio.Task[ServiceResponse]] = set()
        self._closing = False

    def handler_binding(self) -> HandlerBinding:
        return HandlerBinding(self.fleet, self.gateway, self.handle)

    async def handle(self, request: ServiceRequest) -> ServiceResponse:
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

    def _check(self, request: ServiceRequest) -> None:
        request.lifetime.check()
        if self._checkpoint is not None:
            self._checkpoint()

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

    def _completed(self, task: asyncio.Task[ServiceResponse]) -> None:
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
