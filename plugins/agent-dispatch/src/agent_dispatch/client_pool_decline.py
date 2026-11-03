"""Negotiate decline preparation without pretending the workspace is finalized."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

from .client_errors import DispatchError, DispatchUpgradeRequired
from .client_pool_placement import _validate_contract

if TYPE_CHECKING:
    import httpx


class PoolDeclineClientMixin:
    if TYPE_CHECKING:
        _http: httpx.Client
        _unwrap: Callable[[httpx.Response], Any]
        health: Callable[[], dict]

    def _pool_decline_request(self, task_id: str, body: dict, *, blocker: bool = False) -> dict:
        binding = {
            key: body.get(key) for key in
            ("worker_id", "native_id", "generation", "startup_revision", "reservation_key")
        }
        if (
            not isinstance(task_id, str) or not task_id.strip()
            or any(not isinstance(binding[name], str) or not binding[name].strip()
                   for name in ("worker_id", "native_id", "reservation_key"))
            or any(type(binding[name]) is not int or binding[name] < 1
                   for name in ("generation", "startup_revision"))
        ):
            raise DispatchError(400, "pool decline requires its exact task, worker and startup incarnation")
        try:
            health = self.health()
        except ValueError as exc:
            raise DispatchUpgradeRequired("running coordinator returned an invalid health contract") from exc
        contract = _validate_contract(
            health.get("pool_placement") if isinstance(health, dict) else None,
        )
        versions = contract.get("worker_decline_preparation_versions")
        if not isinstance(versions, list) or not any(type(v) is int and v == 1 for v in versions):
            raise DispatchUpgradeRequired("running coordinator lacks worker decline preparation")
        try:
            result = self._unwrap(self._http.post(
                f"/tasks/{task_id}/pool-decline" + ("/blocker" if blocker else ""),
                json={"version": 1, **body},
            ))
        except ValueError as exc:
            raise DispatchError(502, "pool decline outcome is unknown: invalid response") from exc
        decline = result.get("decline") if isinstance(result, dict) else None
        if (
            not isinstance(result, dict) or result.get("protocol") != "pool_worker_decline/v1"
            or type(result.get("version")) is not int or result["version"] != 1
            or not isinstance(decline, dict) or decline.get("task_id") != task_id
            or any(decline.get(key) != value or type(decline.get(key)) is not type(value)
                   for key, value in binding.items())
            or any(type(decline.get(key)) not in (int, float) or not math.isfinite(decline[key])
                   for key in ("created_at", "updated_at"))
            or (decline.get("blocker") != body["detail"] if blocker else (
                decline.get("exclusions") != sorted(set(body["exclusions"]))
                or decline.get("note") != body["note"]
            ))
        ):
            raise DispatchError(502, "pool decline outcome is unknown: inconsistent current worker decision")
        return decline

    def prepare_pool_decline(
        self, task_id: str, worker_id: str, *, native_id: str, generation: int,
        startup_revision: int, reservation_key: str, exclusions: list[str], note: str,
    ) -> dict:
        if (
            not isinstance(note, str) or not note.strip() or len(note) > 4096
            or not isinstance(exclusions, list) or not 1 <= len(exclusions) <= 3
            or any(not isinstance(value, str) for value in exclusions)
        ):
            raise DispatchError(400, "pool decline requires scoped exclusions and a meaningful explanation")
        return self._pool_decline_request(task_id, {
            "worker_id": worker_id, "native_id": native_id, "generation": generation,
            "startup_revision": startup_revision, "reservation_key": reservation_key,
            "exclusions": exclusions, "note": note.strip(),
        })

    def record_pool_decline_blocker(
        self, task_id: str, worker_id: str, *, native_id: str, generation: int,
        startup_revision: int, reservation_key: str, detail: str,
    ) -> dict:
        if not isinstance(detail, str) or not detail.strip() or len(detail) > 4096:
            raise DispatchError(400, "pool decline requires a meaningful closeout blocker")
        return self._pool_decline_request(task_id, {
            "worker_id": worker_id, "native_id": native_id, "generation": generation,
            "startup_revision": startup_revision, "reservation_key": reservation_key,
            "detail": detail.strip(),
        }, blocker=True)
