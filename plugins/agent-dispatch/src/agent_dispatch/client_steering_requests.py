"""Read-only preparation and exact replay of scope-bound steering request data."""

from __future__ import annotations

from .client_errors import DispatchError, DispatchUpgradeRequired
from .steering_requests import prepared_steering_request, validate_prepared_request


class PreparedSteeringClientMixin:
    def _require_steering_request_context(self, health=None) -> None:
        version = (self.health() if health is None else health).get("steering_request_context")
        if type(version) is not int or version < 1:
            raise DispatchUpgradeRequired("coordinator lacks scope-bound steering requests")

    def prepare_steer(
        self, task_id: str, request_id: str, *, expected_scope: dict,
        fields: dict, sender: str | None = None, wake: bool = True,
        message: str | None = None, expected_status: str, expected_revision: str,
    ) -> dict:
        """Freeze the resolved wire request without submitting, saving or waking."""
        request = prepared_steering_request(
            task_id, request_id, expected_scope, fields=fields, sender=sender,
            wake=wake, message=message, expected_status=expected_status,
            expected_revision=expected_revision,
        )
        self._require_steering_request_context()
        task = self.get(task_id)
        if (task.get("draft_scope") != request["scope"]
                or task.get("steering_revision") != expected_revision
                or task.get("status") != expected_status):
            raise DispatchError(409, "task or steering scope changed; refresh before preparing")
        return request

    def steer_prepared(self, request: dict) -> dict:
        """Retry the original request, never today's identity or message defaults."""
        request = validate_prepared_request(request)
        return self.steer(
            request["task_id"], fields=request["fields"], sender=request["sender"],
            wake=request["wake"], message=request["message"],
            expected_status=request["expected_status"], expected_revision=request["expected_revision"],
            idempotency_key=request["request_id"], expected_scope=request["scope"],
        )

    def prepared_steer_receipt(self, request: dict) -> dict:
        """Read owner evidence and verify it against the retained complete request."""
        request = validate_prepared_request(request)
        receipt = self.steer_receipt(
            request["task_id"], request["request_id"], expected_scope=request["scope"],
        )
        accepted = receipt["acceptance"]
        if (accepted.get("request_fingerprint") != request["fingerprint"]
                or accepted.get("steering_revision") != request["expected_revision"]):
            raise DispatchError(502, "steering receipt does not match the original request")
        return receipt
