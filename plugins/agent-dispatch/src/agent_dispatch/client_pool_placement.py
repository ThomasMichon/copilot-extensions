"""Explicit pool claim and acceptance callers; never migrate a legacy lane."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .client_errors import DispatchError, DispatchUpgradeRequired
from .queue_pool_placement import POOL_PLACEMENT_PROTOCOL, POOL_PLACEMENT_VERSION

if TYPE_CHECKING:
    import httpx


def _positive_integer(value: object) -> bool:
    return type(value) is int and value > 0


def _validate_contract(result: object, *, explicit_start: bool = False) -> dict:
    if not isinstance(result, dict):
        raise DispatchUpgradeRequired("running coordinator returned an invalid pool placement contract")
    versions = result.get("supported_versions")
    drivers = result.get("supported_drivers")
    if (
        result.get("protocol") != POOL_PLACEMENT_PROTOCOL
        or not isinstance(versions, list)
        or not any(type(version) is int and version == POOL_PLACEMENT_VERSION for version in versions)
        or not isinstance(drivers, list) or "cli" not in drivers
        or result.get("atomic_claim_before_spawn") is not True
        or result.get("affirmative_worker_start") is not True
    ):
        raise DispatchUpgradeRequired(
            "running coordinator lacks explicit cli pool claim/start support"
        )
    if explicit_start:
        versions = result.get("explicit_start_versions")
        if not isinstance(versions, list) or not any(type(version) is int and version == 1 for version in versions):
            raise DispatchUpgradeRequired("running coordinator lacks required pool-assignment start enforcement")
    return result


class PoolPlacementClientMixin:
    """Negotiate with the executing coordinator before explicit pool effects."""

    if TYPE_CHECKING:
        _http: httpx.Client
        _unwrap: Callable[[httpx.Response], Any]
        _control_headers: Callable[[], dict[str, str]]
        health: Callable[[], dict]

    def pool_placement_contract(self) -> dict:
        """Read this running peer; a payload version cannot qualify placement."""
        try:
            result = self._unwrap(self._http.get(
                "/pool-placements/contract", headers=self._control_headers(),
            ))
        except DispatchError as exc:
            if exc.status_code in {404, 405, 501}:
                raise DispatchUpgradeRequired(
                    "running coordinator lacks explicit pool claim/start support"
                ) from exc
            raise
        except ValueError as exc:
            raise DispatchUpgradeRequired(
                "running coordinator returned an invalid pool placement contract"
            ) from exc
        return _validate_contract(result)

    def place_pool_task(
        self, task_id: str, *, reserved_by: str, catalog_revision: int,
        driver: str = "cli",
    ) -> dict:
        """Claim before startup; only a validated fresh result permits a spawn.

        The coordinator rechecks its complete catalog in the write transaction.
        No preferred pool, capacity count or subset is supplied by this caller.
        Unknown POST outcomes remain unknown; this method never tries a legacy
        reservation or automatically retries with another catalog revision.
        """
        if (
            not isinstance(task_id, str) or not task_id.strip()
            or not isinstance(reserved_by, str) or not reserved_by.strip()
            or type(catalog_revision) is not int or catalog_revision < 0
            or driver != "cli"
        ):
            raise DispatchError(400, "pool placement requires task, allocator, catalog revision and cli driver")
        self.pool_placement_contract()
        try:
            result = self._unwrap(self._http.post(
                "/pool-placements", headers=self._control_headers(),
                json={
                    "version": POOL_PLACEMENT_VERSION, "task_id": task_id,
                    "reserved_by": reserved_by, "catalog_revision": catalog_revision,
                    "driver": driver,
                },
            ))
        except ValueError as exc:
            raise DispatchError(502, "pool placement outcome is unknown: invalid response") from exc
        if not isinstance(result, dict):
            raise DispatchError(502, "pool placement outcome is unknown: invalid response")
        assignment = result.get("assignment")
        reservation = result.get("reservation")
        if (
            result.get("protocol") != POOL_PLACEMENT_PROTOCOL
            or type(result.get("version")) is not int or result["version"] != POOL_PLACEMENT_VERSION
            or type(result.get("reserved")) is not bool
            or not isinstance(assignment, dict) or not isinstance(reservation, dict)
        ):
            raise DispatchError(502, "pool placement outcome is unknown: invalid claim envelope")
        if (
            assignment.get("task_id") != task_id or reservation.get("task_id") != task_id
            or any(
                not isinstance(assignment.get(name), str) or not assignment[name].strip()
                for name in ("registration_id", "machine", "env", "reservation_key")
            )
            or reservation.get("key") != assignment["reservation_key"]
            or not _positive_integer(assignment.get("task_generation"))
            or type(assignment.get("catalog_revision")) is not int or assignment["catalog_revision"] < 0
            or result["reserved"] and (
                assignment["catalog_revision"] != catalog_revision
                or reservation.get("reserved_by") != reserved_by
                or reservation.get("state") != "reserving"
                or reservation.get("driver") != driver
                or reservation.get("release_requested") is not False
                or reservation.get("startup_fenced") is not False
                or not _positive_integer(reservation.get("startup_revision"))
            )
        ):
            raise DispatchError(502, "pool placement outcome is unknown: inconsistent claim binding")
        return result

    def start_pool_task(
        self, task_id: str, worker_id: str, *, owner_session_id: str,
        expected_generation: int,
    ) -> dict:
        """Affirmatively accept the current placed claim under real worker facts."""
        if (
            not isinstance(task_id, str) or not task_id.strip()
            or not isinstance(worker_id, str) or not worker_id.strip()
            or not isinstance(owner_session_id, str) or not owner_session_id.strip()
            or not _positive_integer(expected_generation)
        ):
            raise DispatchError(400, "pool acceptance requires task, worker, native identity and current generation")
        try:
            health = self.health()
        except ValueError as exc:
            raise DispatchUpgradeRequired("running coordinator returned an invalid health contract") from exc
        _validate_contract(
            health.get("pool_placement") if isinstance(health, dict) else None, explicit_start=True,
        )
        try:
            result = self._unwrap(self._http.post(f"/tasks/{task_id}/start", json={
                "worker_id": worker_id, "owner_session_id": owner_session_id,
                "expected_generation": expected_generation, "expected_owner_session_id": None,
                "pool_placement_version": 1,
            }))
        except ValueError as exc:
            raise DispatchError(502, "pool acceptance outcome is unknown: invalid response") from exc
        if not isinstance(result, dict) or (
            result.get("id") != task_id or result.get("status") != "started"
            or result.get("owner") != worker_id or result.get("owner_session_id") != owner_session_id
            or type(result.get("generation")) is not int or result["generation"] != expected_generation
        ):
            raise DispatchError(502, "pool acceptance outcome is unknown: inconsistent current worker")
        return result
