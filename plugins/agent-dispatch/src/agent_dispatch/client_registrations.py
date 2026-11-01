"""Supervisor-registration methods for :class:`DispatchClient`.

Split out of ``client.py`` to keep that module under its module-size cap; a
pure mechanical extraction with no behavior change. Mixed into
``DispatchClient`` alongside its other method groups.
"""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx

from .client_errors import DispatchUpgradeRequired
from .pool_publication_custody import PoolCatalogRead, PoolMetadataRequest
from .pool_publication_response import read_pool_metadata_result
from .queue_pool_catalog import PoolPublicationScope


class RegistrationClientMixin:
    """Supervisor-registration HTTP calls, mixed into ``DispatchClient``.

    Relies on ``self._unwrap`` and ``self._http`` from the composing class.
    """

    if TYPE_CHECKING:
        _http: httpx.Client
        _unwrap: Callable[[httpx.Response], Any]
        _control_headers: Callable[[], dict[str, str]]
        health: Callable[[], dict]

    def register_registration(
        self,
        kind: str,
        spec: dict,
        *,
        reg_id: str | None = None,
        machine: str | None = None,
        env: str = "default",
    ) -> dict:
        body = {
            "kind": kind,
            "spec": spec,
            "id": reg_id,
            "machine": machine,
            "env": env,
        }
        headers = self._control_headers() if kind == "evaluator" else None
        return self._unwrap(self._http.post("/registrations", json=body, headers=headers))

    def list_registrations(
        self,
        *,
        kind: str | None = None,
        machine: str | None = None,
        env: str | None = None,
        include_paused: bool = True,
    ) -> list[dict]:
        params: dict[str, str | bool] = {"include_paused": include_paused}
        if kind is not None:
            params["kind"] = kind
        if machine is not None:
            params["machine"] = machine
        if env is not None:
            params["env"] = env
        return self._unwrap(self._http.get("/registrations", params=params))

    def _require_pool_metadata_transport(self, *, configured_cohort: bool = False) -> None:
        health = self.health()
        contract = health.get("pool_catalog_transport") if isinstance(health, dict) else None
        if not isinstance(contract, dict) or (
            contract.get("protocol") != "pool_catalog_transport/v1"
            or not isinstance(contract.get("supported_versions"), list)
            or not any(type(version) is int and version == 1 for version in contract["supported_versions"])
            or not isinstance(contract.get("definition_versions"), list)
            or not any(type(version) is int and version == 1 for version in contract["definition_versions"])
            or contract.get("control_authority_configured") is not True
        ):
            raise DispatchUpgradeRequired("running coordinator lacks control-authorized pool catalog metadata")
        if configured_cohort and (
            not isinstance(contract.get("configured_cohort_fencing_versions"), list)
            or not any(
                type(version) is int and version == 1
                for version in contract["configured_cohort_fencing_versions"]
            )
        ):
            raise DispatchUpgradeRequired("running coordinator lacks original configured-cohort request fences")

    def pool_catalog_metadata(self) -> dict:
        """Explicit preparation read; ordinary reconciliation never calls it."""
        self._require_pool_metadata_transport()
        result = self._unwrap(self._http.get("/pool-catalog", headers=self._control_headers()))
        PoolCatalogRead.from_dict(result)
        return result

    def send_pool_metadata(
        self, request: PoolMetadataRequest, *,
        boundary: Callable[[], AbstractContextManager[None]],
    ) -> PoolPublicationScope:
        """Send the retained body only after running-peer and local admission."""
        self._require_pool_metadata_transport(
            configured_cohort=request.expected_cohort_revision is not None,
        )
        body = request.to_dict()
        headers = self._control_headers()
        with boundary():
            result = self._unwrap(self._http.post(
                f"/pool-catalog/scopes/{request.operation}", json=body, headers=headers,
            ))
        return read_pool_metadata_result(result)

    def get_registration(self, rid: str) -> dict:
        return self._unwrap(self._http.get(f"/registrations/{rid}"))

    def remove_registration(self, rid: str) -> dict:
        return self._unwrap(self._http.delete(f"/registrations/{rid}"))

    def set_registration_status(self, rid: str, status: str) -> dict:
        return self._unwrap(
            self._http.post(f"/registrations/{rid}/status", json={"status": status})
        )
