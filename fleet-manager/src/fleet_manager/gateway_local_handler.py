"""Code-registered local owners inside the existing authenticated service Gateway."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from .gateway_manifest import ServiceSlot
from .gateway_service_channel import check
from .gateway_service_handler import ServiceRequest, ServiceResponse
from .gateway_service_policy import CallerGrant, ServiceAuthority
from .gateway_controller_services import ConnectedService, ControllerSourceBinding
from .operations import OperationIntent
from .runtime_admission import AdmissionAuthority, Permit


async def settled(function):
    """Join a finite owned native worker, including across caller cancellation."""
    task = asyncio.create_task(asyncio.to_thread(function))
    cancelled = None
    while True:
        try:
            result = await asyncio.shield(task)
            break
        except asyncio.CancelledError as error:
            cancelled = error
            if task.done():
                result = task.result()
                break
    if cancelled is not None:
        raise cancelled
    return result


@dataclass(frozen=True)
class LocalServiceRequest:
    """Process-local request custody, not a serialized identity or backend grant."""

    request: ServiceRequest
    _authority: ServiceAuthority = field(repr=False)
    _grant: CallerGrant = field(repr=False)
    _checkpoint: Callable[[], None] = field(repr=False)
    _offers: Callable[[str], tuple[ConnectedService, ...]] | None = field(
        default=None, repr=False, kw_only=True)
    _dispatch: Callable[..., Awaitable[ServiceResponse]] | None = field(
        default=None, repr=False, kw_only=True)

    def checkpoint(self) -> None:
        self.request.lifetime.check()
        self._checkpoint()

    def authorize_operation(
        self, intent: OperationIntent, admission: AdmissionAuthority, *,
        authority: str, role: str, installation: str,
    ) -> Permit:
        self.checkpoint()
        return self._authority.authorize_operation(
            self._grant, intent, admission, role=role, installation=installation,
            authority=authority, checkpoint=self.checkpoint)

    def service_offers(self, service: str) -> tuple[ConnectedService, ...]:
        self.checkpoint()
        check(self._offers is not None, "controller-source-unavailable", 503)
        return self._offers(service)

    async def call_service(
        self, offer: ConnectedService, *, target: str, body: bytes = b"", timeout: float = 30,
    ) -> ServiceResponse:
        self.checkpoint()
        check(self._dispatch is not None, "controller-source-unavailable", 503)
        response = await self._dispatch(offer, target=target, body=body, timeout=timeout)
        self.checkpoint()
        return response


@dataclass(frozen=True)
class LocalHandlerBinding:
    """Trusted deployment input for one exact desired slot; no runtime discovery.

    Registration grants neither caller authority nor operational readiness.
    The handler owns cancellation/native settlement and uses separate operation
    admission before any effect. It cannot be selected by a policy module path.
    """

    slot: ServiceSlot
    handle: Callable[[LocalServiceRequest], Awaitable[ServiceResponse]]
    sources: tuple[ControllerSourceBinding, ...] = ()

    def __post_init__(self) -> None:
        check(type(self.slot) is ServiceSlot and callable(self.handle), "invalid-local-handler")
        check(all(route.capabilities == ("http",) for route in self.slot.routes),
              "unsupported-local-capability")
        check(type(self.sources) is tuple and len(self.sources) <= 16
              and all(type(item) is ControllerSourceBinding for item in self.sources)
              and len({item.service for item in self.sources}) == len(self.sources),
              "invalid-controller-source")
