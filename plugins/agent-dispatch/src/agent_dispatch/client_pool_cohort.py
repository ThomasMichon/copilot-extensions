"""Negotiated whole-cohort projection through the existing catalog authority."""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any

from .client_errors import DispatchError, DispatchUpgradeRequired
from .pool_publication_custody import PoolPublicationError
from .pool_publication_response import read_pool_configured_cohort
from .queue_pool_cohort import PoolConfiguredCohort, _encode_observation
from .registrar_configuration import ConfiguredSupervisorCohort

if TYPE_CHECKING:
    import httpx


class PoolCohortClientMixin:
    """Explicit projection only; no enrollment, allocation or automatic caller."""

    if TYPE_CHECKING:
        _http: httpx.Client
        _unwrap: Callable[[httpx.Response], Any]
        _control_headers: Callable[[], dict[str, str]]
        health: Callable[[], dict]

    def _require_pool_cohort_projection(self) -> None:
        health = self.health()
        contract = health.get("pool_catalog_transport") if isinstance(health, dict) else None
        if not isinstance(contract, dict) or (
            contract.get("protocol") != "pool_catalog_transport/v1"
            or contract.get("control_authority_configured") is not True
            or any(
                not isinstance(contract.get(key), list)
                or not any(type(version) is int and version == 1 for version in contract[key])
                for key in ("supported_versions", "configured_cohort_projection_versions")
            )
        ):
            raise DispatchUpgradeRequired("running coordinator lacks whole configured-cohort projection v1")

    def _read_pool_cohort_result(self, value: object, *, missing: bool) -> PoolConfiguredCohort | None:
        if not isinstance(value, dict) or (
            value.get("protocol") != "pool_catalog_transport/v1"
            or type(value.get("version")) is not int or value["version"] != 1
            or "cohort" not in value
        ):
            raise PoolPublicationError("pool cohort result is malformed or unsupported")
        if missing and value["cohort"] is None:
            return None
        return read_pool_configured_cohort(value["cohort"])

    def pool_configured_cohort(self) -> PoolConfiguredCohort:
        """Inspect the retained whole projection without changing its history."""
        self._require_pool_cohort_projection()
        try:
            result = self._unwrap(self._http.get(
                "/pool-catalog/cohort", headers=self._control_headers(),
            ))
        except DispatchError as exc:
            if exc.status_code in {404, 405, 501}:
                raise DispatchUpgradeRequired(
                    "running coordinator no longer supports configured-cohort projection",
                ) from exc
            raise
        cohort = self._read_pool_cohort_result(result, missing=False)
        assert cohort is not None
        return cohort

    def _pool_cohort_request(
        self, action: str, observation: ConfiguredSupervisorCohort, *, expected_revision: int,
        boundary: Callable[[], AbstractContextManager[None]],
    ) -> PoolConfiguredCohort | None:
        encoded = _encode_observation(observation)
        if type(expected_revision) is not int or expected_revision < 0:
            raise PoolPublicationError("pool cohort expected revision must be a nonnegative integer")
        body = {
            "version": 1, "expected_revision": expected_revision,
            "observation": json.loads(encoded),
        }
        self._require_pool_cohort_projection()
        headers = self._control_headers()
        with boundary():
            try:
                response = self._unwrap(self._http.post(
                    f"/pool-catalog/cohort/{action}", json=body, headers=headers,
                ))
            except DispatchError as exc:
                if exc.status_code in {404, 405, 501}:
                    raise DispatchUpgradeRequired(
                        "running coordinator no longer supports configured-cohort projection",
                    ) from exc
                raise
        result = self._read_pool_cohort_result(response, missing=action == "recover")
        if result is not None and (
            result.observation is None or _encode_observation(result.observation) != encoded
            or result.revision not in (expected_revision, expected_revision + 1)
        ):
            raise PoolPublicationError("pool cohort result changed the original observation or CAS")
        return result

    def observe_pool_configured_cohort(
        self, observation: ConfiguredSupervisorCohort, *, expected_revision: int,
        boundary: Callable[[], AbstractContextManager[None]],
    ) -> PoolConfiguredCohort:
        """Project original bytes only after final owner admission, never downgrade."""
        result = self._pool_cohort_request(
            "project", observation, expected_revision=expected_revision, boundary=boundary,
        )
        assert result is not None
        return result

    def recover_pool_configured_cohort(
        self, observation: ConfiguredSupervisorCohort, *, expected_revision: int,
    ) -> PoolConfiguredCohort | None:
        """Read exact history only, including after a newer configured revision."""
        from contextlib import nullcontext

        return self._pool_cohort_request(
            "recover", observation, expected_revision=expected_revision, boundary=nullcontext,
        )
