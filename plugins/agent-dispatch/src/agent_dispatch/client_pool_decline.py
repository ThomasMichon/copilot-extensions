"""Negotiate decline preparation without pretending the workspace is finalized."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any
from collections.abc import Callable

import httpx

from .client_errors import DispatchError, DispatchUpgradeRequired
from .client_pool_placement import _validate_contract

# Both routes replay the Worktrees ground owner through serialized local
# subprocesses (readiness, record, inspect and close, each bounded at 30s), so
# a default-length read can expire while the owner is still converging.
OWNER_SETTLEMENT_READ_SECONDS = 300.0


class PoolDeclineClientMixin:
    if TYPE_CHECKING:
        _http: httpx.Client
        _unwrap: Callable[[httpx.Response], Any]
        health: Callable[[], dict]
        _control_headers: Callable[[], dict[str, str]]

    def _owner_settlement_timeout(self) -> httpx.Timeout:
        selected = httpx.Timeout(self._http.timeout)
        if selected.read is not None:
            selected.read = max(selected.read, OWNER_SETTLEMENT_READ_SECONDS)
        return selected

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

    def close_pool_decline(self, task_id: str, decline: dict) -> dict:
        """Request the coordinator's own observation, never send finalizer JSON."""
        contract = _validate_contract(self.health().get("pool_placement"))
        versions = contract.get("worker_decline_local_closeout_versions")
        if not isinstance(versions, list) or not any(type(v) is int and v == 1 for v in versions):
            raise DispatchUpgradeRequired("running coordinator lacks local worker decline closeout")
        binding = {key: decline.get(key) for key in (
            "worker_id", "native_id", "generation", "startup_revision", "reservation_key",
        )}
        if (
            decline.get("task_id") != task_id
            or any(not isinstance(binding[name], str) or not binding[name].strip()
                   for name in ("worker_id", "native_id", "reservation_key"))
            or any(type(binding[name]) is not int or binding[name] < 1
                   for name in ("generation", "startup_revision"))
        ):
            raise DispatchError(400, "worker decline closeout requires its original exact binding")
        result = self._unwrap(self._http.post(
            f"/tasks/{task_id}/pool-decline/closeout",
            json={"version": 1, **binding}, headers=self._control_headers(),
            timeout=self._owner_settlement_timeout(),
        ))
        receipt = result.get("closeout") if isinstance(result, dict) else None
        if (
            not isinstance(result, dict) or result.get("protocol") != "pool_worker_decline/v1"
            or type(result.get("version")) is not int or result["version"] != 1
            or not isinstance(receipt, dict) or receipt.get("task_id") != task_id
            or receipt.get("binding") != binding
            or any(type(receipt["binding"].get(key)) is not type(value) for key, value in binding.items())
            or type(receipt.get("queued_generation")) is not int
            or receipt["queued_generation"] != binding["generation"] + 1
            or receipt.get("process_retirement") is not False
        ):
            raise DispatchError(502, "worker decline closeout response is unconfirmed; custody retained")
        return receipt

    def pool_decline_relationship(self, task_id: str, reservation_key: str) -> dict | None:
        """Return this allocation's bound task claim when readiness ordering applies."""
        contract = _validate_contract(self.health().get("pool_placement"))
        versions = contract.get("worker_decline_readiness_closeout_versions")
        supported = isinstance(versions, list) and any(type(v) is int and v == 1 for v in versions)
        try:
            value = self._unwrap(self._http.get(f"/tasks/{task_id}/relationship"))
        except DispatchError as exc:
            if exc.status_code == 404 and not supported:
                return None
            raise
        except ValueError as exc:
            raise DispatchError(502, "task relationship read is unconfirmed; custody retained") from exc
        request = value.get("request") if isinstance(value, dict) else None
        if value is None:
            return None
        if not isinstance(request, dict) or request.get("reservation_key") != reservation_key:
            # Mirrors coordinator closeout: another allocation's unsettled claim retains custody.
            if isinstance(value, dict) and value.get("state") in {"closed", "absent"}:
                return None
            raise DispatchError(409, "worker decline lacks its original task relationship; custody retained")
        if not supported:
            raise DispatchUpgradeRequired("running coordinator lacks decline readiness ordering; custody retained")
        if value.get("state") != "bound":
            raise DispatchError(409, "worker decline task relationship is unresolved; custody retained")
        return value

    def recover_pool_decline(
        self, task_id: str, worker_id: str, native_id: str, generation: int,
    ) -> tuple[dict, str] | None:
        """Recover this exact incarnation's settled decline after a replacement placement."""
        contract = _validate_contract(self.health().get("pool_placement"))
        versions = contract.get("worker_decline_binding_recovery_versions")
        if not isinstance(versions, list) or not any(type(v) is int and v == 1 for v in versions):
            raise DispatchUpgradeRequired("running coordinator lacks worker decline binding recovery")
        expected = {"worker_id": worker_id, "native_id": native_id, "generation": generation}
        try:
            result = self._unwrap(self._http.get(
                f"/tasks/{task_id}/pool-decline/recovery", params=expected,
            ))
        except DispatchError as exc:
            if exc.status_code == 404:
                return None
            raise
        except ValueError as exc:
            raise DispatchError(502, "worker decline recovery is unconfirmed; custody retained") from exc
        decline = result.get("decline") if isinstance(result, dict) else None
        worktree = result.get("worktree") if isinstance(result, dict) else None
        if (
            not isinstance(result, dict) or result.get("protocol") != "pool_worker_decline/v1"
            or type(result.get("version")) is not int or result["version"] != 1
            or not isinstance(decline, dict) or decline.get("task_id") != task_id
            or any(decline.get(key) != value for key, value in expected.items())
            or type(decline.get("startup_revision")) is not int or decline["startup_revision"] < 1
            or not isinstance(decline.get("reservation_key"), str) or not decline["reservation_key"]
            or not isinstance(worktree, str) or not worktree
        ):
            raise DispatchError(502, "worker decline recovery response is unconfirmed; custody retained")
        return decline, worktree

    def settle_pool_decline(self, task_id: str, decline: dict) -> dict:
        """Converge settlement behind the committed disposition; never requeue here."""
        contract = _validate_contract(self.health().get("pool_placement"))
        versions = contract.get("worker_decline_readiness_closeout_versions")
        if not isinstance(versions, list) or not any(type(v) is int and v == 1 for v in versions):
            raise DispatchUpgradeRequired("running coordinator lacks decline claim settlement")
        binding = {key: decline.get(key) for key in (
            "worker_id", "native_id", "generation", "startup_revision", "reservation_key",
        )}
        try:
            result = self._unwrap(self._http.post(
                f"/tasks/{task_id}/pool-decline/settlement", json={"version": 1, **binding},
                timeout=self._owner_settlement_timeout(),
            ))
        except ValueError as exc:
            raise DispatchError(502, "worker decline settlement is unconfirmed; custody retained") from exc
        receipt = result.get("closeout") if isinstance(result, dict) else None
        relationship = result.get("task_relationship") if isinstance(result, dict) else None
        if (
            not isinstance(result, dict) or result.get("protocol") != "pool_worker_decline/v1"
            or type(result.get("version")) is not int or result["version"] != 1
            or not isinstance(receipt, dict) or receipt.get("task_id") != task_id
            or receipt.get("binding") != binding
            or type(receipt.get("queued_generation")) is not int
            or receipt["queued_generation"] != binding["generation"] + 1
            or receipt.get("process_retirement") is not False
            or (relationship is None) != (receipt.get("task_relationship_event_id") is None)
            or (relationship is not None and (
                not isinstance(relationship, dict)
                or relationship.get("event_id") != receipt["task_relationship_event_id"]
                or not isinstance(relationship.get("state"), str)
            ))
        ):
            raise DispatchError(502, "worker decline settlement response is unconfirmed; custody retained")
        return {"closeout": receipt, "task_relationship": relationship}
