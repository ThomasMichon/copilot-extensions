"""Negotiate exact retry/completion routes without replaying unknown writes."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Literal

from .client_errors import DispatchError
from .client_pool_placement import _positive_integer
from .pool_startup_contract import POOL_STARTUP_PATH
from .pool_startup_retry_contract import retry_invocation


class PoolStartupRetryClientMixin:
    if TYPE_CHECKING:
        _pool_startup_request: Callable[..., object]

    def _pool_retry(
        self, action: Literal["retry", "complete"], task_id: str, key: str, *,
        generation: int, startup_revision: int, native_id: str,
        worktree: str, machine: str, reserved_by: str,
        outcome_kind: str | None = None, detail: str | None = None,
    ) -> dict:
        if (
            any(not isinstance(value, str) or not value.strip() for value in (
                task_id, key, native_id, worktree, machine, reserved_by,
            ))
            or not _positive_integer(generation) or not _positive_integer(startup_revision)
            or action == "complete" and (
                outcome_kind not in {
                    "startup_succeeded", "startup_failed", "startup_rejected", "startup_unknown",
                } or not isinstance(detail, str) or not detail.strip()
            )
        ):
            raise DispatchError(400, "pool retry requires exact caller/native identity and classified completion")
        base = {
            "version": 1, "task_id": task_id, "reservation_key": key, "generation": generation,
            "startup_revision": startup_revision, "native_id": native_id,
            "worktree": worktree, "machine": machine, "reserved_by": reserved_by,
        }
        body = {name: value for name, value in base.items() if name != "task_id"}
        if action == "complete":
            body.update(outcome_kind=outcome_kind, detail=detail)
        result = self._pool_startup_request(
            "POST", f"{POOL_STARTUP_PATH}/tasks/{task_id}/{action}", body,
            required_features=("fenced_same_native_retry", "fenced_retry_completion"),
        )
        expected_binding = {"task_id": task_id, **body, "action": action}
        revision = startup_revision + int(action == "retry")
        if not isinstance(result, dict) or (
            not isinstance(result.get("binding"), dict)
            or result["binding"] != expected_binding
            or any(type(result["binding"].get(name)) is not type(value)
                   for name, value in expected_binding.items())
            or result.get("invocation") != retry_invocation(base, revision)
        ):
            raise DispatchError(502, "pool retry outcome is unknown: response invocation changed")
        reservation = result.get("reservation")
        expected_complete = action == "complete" and outcome_kind != "startup_unknown"
        if not isinstance(reservation, dict) or (
            any(reservation.get(name) != value for name, value in {
                "key": key, "task_id": task_id, "worktree": worktree,
                "execution_machine": machine, "reserved_by": reserved_by,
                "session_handle": native_id, "driver": "cli",
            }.items())
            or type(reservation.get("startup_revision")) is not int
            or reservation["startup_revision"] != revision
            or reservation.get("startup_complete") is not expected_complete
            or reservation.get("state") not in (
                {"spawned"} if action == "retry" else {"spawned", "releasing"}
            )
            or action == "retry" and (
                reservation.get("release_requested") or reservation.get("startup_fenced")
                or reservation.get("conversation_retired") or reservation.get("cooling_token") is not None
            )
        ):
            raise DispatchError(502, "pool retry outcome is unknown: retained reservation changed")
        return result

    def retry_pool_startup(
        self, task_id: str, key: str, *, generation: int, startup_revision: int,
        native_id: str, worktree: str, machine: str, reserved_by: str,
    ) -> dict:
        """Reserve one next invocation; only its validated reply admits transport."""
        return self._pool_retry(
            "retry", task_id, key, generation=generation, startup_revision=startup_revision,
            native_id=native_id, worktree=worktree, machine=machine, reserved_by=reserved_by,
        )

    def complete_pool_startup(
        self, task_id: str, key: str, *, generation: int, startup_revision: int,
        native_id: str, worktree: str, machine: str, reserved_by: str,
        outcome_kind: str, detail: str,
    ) -> dict:
        """Reconcile only the exact original caller; this is never worker acceptance."""
        return self._pool_retry(
            "complete", task_id, key, generation=generation, startup_revision=startup_revision,
            native_id=native_id, worktree=worktree, machine=machine, reserved_by=reserved_by,
            outcome_kind=outcome_kind, detail=detail,
        )
