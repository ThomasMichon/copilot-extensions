"""Negotiated startup control callers with no legacy effectful fallback."""

from __future__ import annotations

from collections.abc import Callable
import math
from typing import TYPE_CHECKING, Any, Literal

from .client_errors import DispatchError, DispatchUpgradeRequired
from .client_pool_placement import _positive_integer, validate_placement_result
from .pool_startup_contract import POOL_STARTUP_PATH, POOL_STARTUP_PROTOCOL
from .client_pool_startup_retry import PoolStartupRetryClientMixin

if TYPE_CHECKING:
    import httpx


def _finite(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _unknown(detail: str) -> DispatchError:
    return DispatchError(502, f"pool startup outcome is unknown: {detail}")


def _budget(value: object, task_id: str) -> dict:
    if not isinstance(value, dict) or (
        value.get("task_id") != task_id
        or not isinstance(value.get("reservation_key"), str) or not value["reservation_key"]
        or not _positive_integer(value.get("generation"))
        or any(
            type(value.get(name)) is not int or value[name] < 0
            for name in ("startup_attempts", "episode_start_attempt", "cooldown_stage")
        )
        or value["episode_start_attempt"] > value["startup_attempts"]
        or value["cooldown_stage"] > 4
        or not _finite(value.get("startup_timeout")) or value["startup_timeout"] <= 0
        or any(
            name not in value or value[name] is not None and not _finite(value[name])
            for name in ("startup_deadline", "reachable_at", "next_nudge_at",
                         "decision_deadline", "exhausted_at")
        )
        or value.get("exhaustion_reason") not in (
            None, "startup_timeout", "decision_timeout", "startup_attempts_exhausted",
        )
        or value.get("outcome_kind") not in (
            None, "startup_failed", "startup_rejected", "startup_unknown",
        )
        or any(name not in value for name in (
            "exhaustion_reason", "outcome_kind", "outcome_detail", "outcome_revision", "outcome_at",
        ))
        or (value["exhaustion_reason"] is None) != (value["exhausted_at"] is None)
        or value["outcome_kind"] is None and any(
            value[name] is not None for name in ("outcome_detail", "outcome_revision", "outcome_at")
        )
        or value["outcome_kind"] is not None and (
            not isinstance(value["outcome_detail"], str) or not value["outcome_detail"].strip()
            or not _positive_integer(value["outcome_revision"]) or not _finite(value["outcome_at"])
        )
    ):
        raise _unknown("invalid budget observation")
    return value


class PoolStartupClientMixin(PoolStartupRetryClientMixin):
    """Control-authorized supervision; ordinary lanes are not opted in."""

    if TYPE_CHECKING:
        _http: httpx.Client
        _unwrap: Callable[[httpx.Response], Any]
        _control_headers: Callable[[], dict[str, str]]

    def pool_startup_contract(self) -> dict:
        """Qualify the executing peer for each explicit startup operation."""
        try:
            result = self._unwrap(self._http.get(
                f"{POOL_STARTUP_PATH}/contract", headers=self._control_headers(),
            ))
        except DispatchError as exc:
            if exc.status_code in {404, 405, 501}:
                raise DispatchUpgradeRequired("running coordinator lacks pool startup supervision v1") from exc
            raise
        except ValueError as exc:
            raise DispatchUpgradeRequired("running coordinator returned an invalid startup contract") from exc
        versions = result.get("supported_versions") if isinstance(result, dict) else None
        drivers = result.get("supported_drivers") if isinstance(result, dict) else None
        cooldowns = result.get("cooldown_seconds") if isinstance(result, dict) else None
        if not isinstance(result, dict) or (
            result.get("protocol") != POOL_STARTUP_PROTOCOL
            or not isinstance(versions, list) or not any(type(v) is int and v == 1 for v in versions)
            or not isinstance(drivers, list) or "cli" not in drivers
            or any(result.get(name) is not True for name in (
                "configured_startup_timeout", "durable_claim_budget",
                "same_native_decision", "exhaustion_retains_capacity",
                "classified_startup_outcomes",
            ))
            or type(result.get("startup_attempt_limit")) is not int or result["startup_attempt_limit"] != 3
            or type(result.get("nudge_seconds")) is not int or result["nudge_seconds"] != 300
            or type(result.get("decision_seconds")) is not int or result["decision_seconds"] != 1800
            or not isinstance(cooldowns, list) or any(type(value) is not int for value in cooldowns)
            or cooldowns != [3600, 10800, 21600, 43200]
        ):
            raise DispatchUpgradeRequired("running coordinator lacks the required bounded startup policy")
        return result

    def _pool_startup_request(
        self, method: str, path: str, body: dict | None = None, *,
        required_features: tuple[str, ...] = (),
    ) -> object:
        contract = self.pool_startup_contract()
        if any(contract.get(feature) is not True for feature in required_features):
            raise DispatchUpgradeRequired("running coordinator lacks exact same-native retry/completion")
        try:
            if method == "GET":
                result = self._unwrap(self._http.get(path, headers=self._control_headers()))
            else:
                result = self._unwrap(self._http.post(path, headers=self._control_headers(), json=body))
        except DispatchError as exc:
            if exc.status_code in {404, 405, 501}:
                raise DispatchUpgradeRequired(
                    "running coordinator no longer supports the requested pool startup operation"
                ) from exc
            raise
        except ValueError as exc:
            raise _unknown("invalid response") from exc
        if not isinstance(result, dict) or (
            result.get("protocol") != POOL_STARTUP_PROTOCOL
            or type(result.get("version")) is not int or result["version"] != 1
            or "result" not in result
        ):
            raise _unknown("invalid supervision envelope")
        return result["result"]

    def place_pool_startup(
        self, task_id: str, *, reserved_by: str, catalog_revision: int, startup_timeout: float,
    ) -> dict:
        """Atomically place a claim with its configured startup deadline."""
        if (
            not isinstance(task_id, str) or not task_id.strip()
            or not isinstance(reserved_by, str) or not reserved_by.strip()
            or type(catalog_revision) is not int or catalog_revision < 0
            or not _finite(startup_timeout) or startup_timeout <= 0
        ):
            raise DispatchError(400, "pool startup placement requires task, allocator, catalog and finite timeout")
        result = self._pool_startup_request("POST", f"{POOL_STARTUP_PATH}/place", {
            "version": 1, "task_id": task_id, "reserved_by": reserved_by,
            "catalog_revision": catalog_revision, "driver": "cli", "startup_timeout": startup_timeout,
        })
        placed = validate_placement_result(
            result, task_id=task_id, reserved_by=reserved_by, catalog_revision=catalog_revision, driver="cli",
        )
        timeout = placed.get("configured_startup_timeout")
        if placed["reserved"] and (not _finite(timeout) or timeout != startup_timeout) or (
            not placed["reserved"] and timeout is not None
        ):
            raise _unknown("configured timeout binding changed")
        return placed

    def pool_startup_snapshot(self, task_id: str) -> dict | None:
        """Observe retained budgets and identities; absence is not native absence."""
        if not isinstance(task_id, str) or not task_id.strip():
            raise DispatchError(400, "pool startup snapshot requires task identity")
        result = self._pool_startup_request("GET", f"{POOL_STARTUP_PATH}/tasks/{task_id}")
        if result is None:
            return None
        if not isinstance(result, dict):
            raise _unknown("invalid startup snapshot")
        budget = _budget(result.get("budget"), task_id)
        reservation, task, native = result.get("reservation"), result.get("task"), result.get("native_id")
        if (
            not isinstance(reservation, dict) or not isinstance(task, dict)
            or reservation.get("key") != budget["reservation_key"]
            or reservation.get("task_id") != task_id or task.get("id") != task_id
            or not _positive_integer(reservation.get("startup_revision"))
            or type(task.get("generation")) is not int
            or task["generation"] < budget["generation"]
            or "native_id" not in result or native is not None and (not isinstance(native, str) or not native)
        ):
            raise _unknown("inconsistent startup snapshot binding")
        invocation = budget.get("retry_invocation")
        if invocation is not None:
            from .pool_startup_retry_contract import retry_invocation
            expected = {
                "version": 1, "task_id": task_id, "reservation_key": reservation["key"],
                "generation": budget["generation"], "startup_revision": reservation["startup_revision"],
                "native_id": native, "worktree": reservation.get("worktree"),
                "machine": reservation.get("execution_machine"), "reserved_by": reservation.get("reserved_by"),
            }
            if not isinstance(invocation, dict) or invocation != retry_invocation(
                expected, reservation["startup_revision"],
            ):
                raise _unknown("retained retry invocation binding changed")
        return result

    def _pool_startup_mutate(
        self, action: Literal["reachable", "nudge", "exhaust", "outcome"], task_id: str, key: str, *,
        generation: int, startup_revision: int, native_id: str | None = None,
        outcome_kind: str | None = None, detail: str | None = None, reason: str = "deadline",
    ) -> dict | None:
        if (
            not isinstance(task_id, str) or not task_id.strip()
            or not isinstance(key, str) or not key.strip()
            or not _positive_integer(generation) or not _positive_integer(startup_revision)
            or action in {"reachable", "nudge"} and (not isinstance(native_id, str) or not native_id.strip())
        ):
            raise DispatchError(400, "pool startup requires exact task, reservation, generation and startup identity")
        body: dict[str, object] = {
            "version": 1, "reservation_key": key, "generation": generation, "startup_revision": startup_revision,
        }
        if action != "exhaust":
            body["native_id"] = native_id
        if action == "outcome":
            body.update(outcome_kind=outcome_kind, detail=detail)
        elif action == "exhaust":
            body["reason"] = reason
        result = self._pool_startup_request("POST", f"{POOL_STARTUP_PATH}/tasks/{task_id}/{action}", body)
        if not isinstance(result, dict):
            raise _unknown("invalid startup result")
        binding = result.get("binding")
        if not isinstance(binding, dict) or any(
            binding.get(name) != value or type(binding.get(name)) is not type(value)
            for name, value in {"task_id": task_id, **body}.items()
        ):
            raise _unknown("startup response incarnation changed")
        value = result.get("observation")
        if action == "exhaust":
            if not isinstance(value, dict) or (
                value.get("id") != task_id or value.get("status") != "queued"
                or type(value.get("generation")) is not int or value["generation"] != generation + 1
                or value.get("owner") is not None or not _finite(value.get("not_before"))
            ):
                raise _unknown("invalid exhausted task generation")
        elif action == "nudge" and value is None:
            if "observation" not in result:
                raise _unknown("missing nudge observation")
        else:
            budget = _budget(value, task_id)
            if budget["reservation_key"] != key or budget["generation"] != generation:
                raise _unknown("budget incarnation changed")
            if action in {"reachable", "nudge"} and any(
                not _finite(budget.get(name)) for name in ("reachable_at", "decision_deadline", "next_nudge_at")
            ):
                raise _unknown("missing current-worker decision clocks")
            if action == "outcome" and (
                budget.get("outcome_kind") != outcome_kind or budget.get("outcome_detail") != detail
                or type(budget.get("outcome_revision")) is not int
                or budget["outcome_revision"] != startup_revision or not _finite(budget.get("outcome_at"))
            ):
                raise _unknown("startup outcome binding changed")
        return value

    def observe_pool_reachable(
        self, task_id: str, key: str, *, generation: int, startup_revision: int, native_id: str,
    ) -> dict:
        """Record an independently observed current worker; never accept its task."""
        result = self._pool_startup_mutate(
            "reachable", task_id, key, generation=generation, startup_revision=startup_revision, native_id=native_id,
        )
        assert result is not None
        return result

    def take_pool_nudge(
        self, task_id: str, key: str, *, generation: int, startup_revision: int, native_id: str,
    ) -> dict | None:
        """Claim at most one due same-native slot; delivery is still outstanding."""
        return self._pool_startup_mutate(
            "nudge", task_id, key, generation=generation, startup_revision=startup_revision, native_id=native_id,
        )

    def exhaust_pool_startup(
        self, task_id: str, key: str, *, generation: int, startup_revision: int,
        reason: str = "deadline",
    ) -> dict:
        """Queue an expired claim without inferring termination or freeing its slot."""
        result = self._pool_startup_mutate(
            "exhaust", task_id, key, generation=generation, startup_revision=startup_revision, reason=reason,
        )
        assert result is not None
        return result

    def record_pool_startup_outcome(
        self, task_id: str, key: str, *, generation: int, startup_revision: int,
        outcome_kind: str, detail: str, native_id: str | None = None,
    ) -> dict:
        """Report the original caller's failed/rejected/unknown startup, not a decline."""
        if outcome_kind not in {"startup_failed", "startup_rejected", "startup_unknown"} or (
            not isinstance(detail, str) or not detail.strip()
        ) or native_id is not None and (not isinstance(native_id, str) or not native_id.strip()):
            raise DispatchError(400, "startup outcome requires a classification, detail and actual native identity")
        result = self._pool_startup_mutate(
            "outcome", task_id, key, generation=generation, startup_revision=startup_revision,
            native_id=native_id, outcome_kind=outcome_kind, detail=detail,
        )
        assert result is not None
        return result
