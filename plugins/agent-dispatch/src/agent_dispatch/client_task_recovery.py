"""Version-gated task recovery calls; never fall back to older mutations."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal, cast

import httpx

from .client_errors import DispatchError, DispatchUpgradeRequired

RecoveryOperation = Literal["detach_agent", "reset_proposed", "reset_queued"]
WakeOutbox = Literal["wake_outbox", "run_waiter_wakes"]
RECOVERY_OPERATIONS = ("detach_agent", "reset_proposed", "reset_queued")
WAKE_OUTBOXES = ("wake_outbox", "run_waiter_wakes")
TASK_RECOVERY_PROTOCOL = "task_recovery/v1"


def validate_recovery_fields(**fields: object) -> dict[str, str]:
    """Reject missing or blank fencing/audit fields without contacting a provider."""
    validated: dict[str, str] = {}
    for name, value in fields.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a nonblank string")
        validated[name] = value
    return validated


def strict_reset_request(
    *,
    to: str = "proposed",
    expected_revision: object = None,
    request_id: object = None,
    actor: object = None,
    reason: object = None,
    expected_status: object = None,
) -> tuple[RecoveryOperation, dict[str, str]] | None:
    """Select explicit strict recovery, retaining the legacy Proposed default."""
    if to not in ("proposed", "queued"):
        raise ValueError("reset destination must be proposed or queued")
    if to == "proposed" and all(value is None for value in (expected_revision, request_id, actor)):
        return None
    fields = validate_recovery_fields(
        expected_revision=expected_revision,
        request_id=request_id,
        actor=actor,
        reason=reason,
    )
    if expected_status is not None:
        raise ValueError("strict reset uses expected_revision, not expected_status")
    operation: RecoveryOperation = "reset_queued" if to == "queued" else "reset_proposed"
    return operation, fields


class TaskRecoveryClientMixin:
    """Recovery methods composed with DispatchClient's transport and authority."""

    if TYPE_CHECKING:
        _http: httpx.Client
        _unwrap: Callable[[httpx.Response], Any]
        _control_headers: Callable[[], dict[str, str]]
        health: Callable[[], dict[str, Any]]

    def close_failed_script(self, task_id: str, **expected: object) -> dict[str, Any]:
        """Ask the coordinator to verify exit, never submit a caller's verdict."""
        return cast(dict[str, Any], self._unwrap(self._http.post(
            f"/tasks/{task_id}/script-exit", headers=self._control_headers(), json=expected,
        )))

    def requeue_gone_owner(self, task_id: str, **observed: object) -> None:
        self._unwrap(self._http.post(f"/tasks/{task_id}/gone-owner-requeue",
                                     headers=self._control_headers(), json=observed))

    def reconcile_gone_requeues(self, supervisor: str) -> None:
        headers = self._control_headers()
        if headers:
            self._unwrap(self._http.post("/spawn-reservations/reconcile-gone-requeues",
                                         headers=headers, params={"supervisor": supervisor}))

    def _require_task_recovery(self, operation: str | None = None) -> None:
        # Probe each call: an earlier advertisement cannot authorize a rolled-back
        # or replaced running provider.
        try:
            health = self.health()
        except (DispatchError, httpx.TransportError, ValueError) as exc:
            raise DispatchUpgradeRequired(
                f"cannot confirm running coordinator {TASK_RECOVERY_PROTOCOL} support: {exc}"
            ) from exc
        capability = health.get("task_recovery") if isinstance(health, dict) else None
        if (
            not isinstance(capability, dict)
            or capability.get("protocol") != TASK_RECOVERY_PROTOCOL
        ):
            raise DispatchUpgradeRequired(
                f"running coordinator lacks {TASK_RECOVERY_PROTOCOL}; task recovery refused"
            )
        operations = capability.get("operations")
        if not isinstance(operations, list) or not all(
            isinstance(value, str) and value.strip() for value in operations
        ):
            raise DispatchUpgradeRequired(
                f"running coordinator has malformed {TASK_RECOVERY_PROTOCOL} operations"
            )
        if operation is not None and operation not in operations:
            raise DispatchUpgradeRequired(
                f"running coordinator lacks {TASK_RECOVERY_PROTOCOL} operation {operation}"
            )

    def task_diagnostics(self, task_id: str) -> dict[str, Any]:
        """Read a bounded redacted snapshot and its opaque recovery revision."""
        self._require_task_recovery()
        return cast(dict[str, Any], self._unwrap(self._http.get(f"/tasks/{task_id}/diagnostics")))

    def recover_task(
        self,
        task_id: str,
        *,
        operation: RecoveryOperation,
        expected_revision: str,
        request_id: str,
        actor: str,
        reason: str,
    ) -> dict[str, Any]:
        """Apply one explicit, revision-fenced and idempotent recovery action."""
        if operation not in RECOVERY_OPERATIONS:
            raise ValueError(f"unsupported task recovery operation: {operation!r}")
        body = {
            "expected_revision": expected_revision,
            "request_id": request_id,
            "actor": actor,
            "reason": reason,
        }
        validate_recovery_fields(**body)
        self._require_task_recovery(operation)
        return cast(dict[str, Any], self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/recovery/{operation}",
                json=body,
                headers=self._control_headers(),
            ))
        )

    def reconcile_wake(
        self,
        task_id: str,
        *,
        outbox: WakeOutbox,
        wake_id: str,
        expected_revision: str,
        actor: str,
        reason: str,
        settle_native_send: bool = False,
    ) -> dict[str, Any]:
        """Observe custody, or explicitly select owner settlement without replay."""
        if type(settle_native_send) is not bool:
            raise ValueError("native settlement selection must be boolean")
        if outbox not in WAKE_OUTBOXES:
            raise ValueError(f"unsupported wake outbox: {outbox!r}")
        body: dict[str, str | bool] = {
            "outbox": outbox,
            "wake_id": wake_id,
            "expected_revision": expected_revision,
            "actor": actor,
            "reason": reason,
        }
        validate_recovery_fields(**body)
        if settle_native_send:
            body["settle_native_send"] = True
        self._require_task_recovery("settle_wake" if settle_native_send else "reconcile_wake")
        return cast(dict[str, Any], self._unwrap(
            self._http.post(
                f"/tasks/{task_id}/wakes/reconcile",
                json=body,
                headers=self._control_headers(),
            ))
        )
