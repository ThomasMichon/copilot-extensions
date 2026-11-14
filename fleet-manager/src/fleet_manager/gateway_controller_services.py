"""Current Controller service observations, never service or credential grants."""

from __future__ import annotations

from dataclasses import dataclass

from .gateway_manifest import ServiceSlot
from .gateway_service_channel import check, shape, target
from .gateway_service_context import ServicePolicyError, identifier

PROFILE = "controller-services-v1"
MAX_SERVICES = 16


@dataclass(frozen=True)
class ControllerService:
    service: str
    prefix: str

    def __post_init__(self) -> None:
        try:
            identifier(self.service)
        except ServicePolicyError:
            check(False, "invalid-controller-service")
        path, query = target(self.prefix)
        check(not query and path != "/" and not path.endswith("/"),
              "invalid-controller-service")


def services(value: object) -> tuple[ControllerService, ...]:
    check(type(value) is list and len(value) <= MAX_SERVICES, "invalid-controller-services")
    result = []
    for item in value:
        check(type(item) is dict, "invalid-controller-services")
        shape(item, {"service", "prefix"})
        result.append(ControllerService(item["service"], item["prefix"]))
    check(len({item.service for item in result}) == len(result)
          and len({item.prefix for item in result}) == len(result),
          "duplicate-controller-service")
    return tuple(sorted(result, key=lambda item: item.service))


def service_values(value: tuple[ControllerService, ...]) -> list[dict[str, str]]:
    check(type(value) is tuple
          and all(type(item) is ControllerService for item in value),
          "invalid-controller-services")
    return [{"service": item.service, "prefix": item.prefix} for item in value]


def admitted_services(value: tuple[ControllerService, ...], slot: ServiceSlot) -> None:
    check(all(any(route.prefix == item.prefix for route in slot.routes) for item in value),
          "controller-route-denied", 403)


@dataclass(frozen=True)
class ConnectedService:
    """A bounded observation of an exact connection, not permission to dispatch."""

    controller: str
    connection: str
    generation: str
    principal: str
    service: str
    prefix: str


@dataclass(frozen=True)
class ControllerSourceBinding:
    """Trusted local-owner selection, not an incoming route or caller grant."""

    service: str
    controllers: tuple[str, ...]

    def __post_init__(self) -> None:
        try:
            identifier(self.service)
            check(type(self.controllers) is tuple and 0 < len(self.controllers) <= 32
                  and len(set(self.controllers)) == len(self.controllers),
                  "invalid-controller-source")
            for name in self.controllers:
                identifier(name)
        except ServicePolicyError:
            check(False, "invalid-controller-source")
