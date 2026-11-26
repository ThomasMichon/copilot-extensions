"""Re-establish exact member ownership for a bound runtime proxy."""

from __future__ import annotations

from . import runtime_drivers as routing
from .config import ContainersConfig
from .external_driver import _ID_RE, _MEMBER_RE


def _proxy_fleets(config: ContainersConfig, name: str) -> list[str]:
    return [
        key for key, fleet in config.fleets.items()
        if fleet.driver and routing._owns(key, fleet, name)
    ]


def proxy_binding(
    config: ContainersConfig, name: str, fleet: str, driver: routing.RuntimeDriver,
) -> dict[str, int | str] | None:
    """Advertise exact proxy support only for uniquely owned namespaces."""
    if _proxy_fleets(config, name) != [fleet]:
        return None
    return {"version": 1, "execution_runtime": routing._driver_identity(driver)}


def bound_proxy_route(
    config: ContainersConfig, fleet: str, name: str, instance_id: str, runtime: str,
) -> routing.Route:
    """Re-establish ownership in a fresh proxy process before any driver effect."""
    if not _MEMBER_RE.fullmatch(name) or not _ID_RE.fullmatch(instance_id):
        raise RuntimeError("Bound runtime proxy requires an exact member name and instance ID")
    if _proxy_fleets(config, name) != [fleet]:
        raise RuntimeError(f"'{name}' no longer has a unique configured runtime-driver fleet")
    driver = routing.driver_for_fleet(config, fleet, config.fleets[fleet])
    if routing._driver_identity(driver) != runtime:
        raise routing.RetiredIncarnation(f"Runtime registration changed for '{name}'; resolve it again")
    docker_id = routing._docker_id_for_name(name)
    if docker_id is not None and docker_id != instance_id:
        raise RuntimeError(f"'{name}' also identifies a distinct Docker container")
    return routing.Route(driver, name, instance_id)
