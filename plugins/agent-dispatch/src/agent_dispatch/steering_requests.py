"""Portable request data for exact steering recovery, never executable authority."""

from __future__ import annotations

import json

from .steering import SteeringError, steer_request_fingerprint, steer_wake_message

_REQUEST_KEYS = {
    "version", "task_id", "request_id", "scope", "fields", "sender", "wake",
    "message", "expected_status", "expected_revision", "fingerprint",
}


def validate_steering_scope(scope: object) -> dict:
    if (not isinstance(scope, dict) or set(scope) != {"queue", "installation", "machine"}
            or any(not isinstance(value, str) or not value for value in scope.values())):
        raise SteeringError("steering recovery requires the complete owner-reported scope")
    return dict(scope)


def request_fingerprint(request: dict) -> str:
    return steer_request_fingerprint(
        fields=request["fields"], sender=request["sender"], wake_requested=request["wake"],
        wake_message=request["message"], expected_status=request["expected_status"],
        expected_revision=request["expected_revision"],
    )


def prepared_steering_request(
    task_id: str, request_id: str, scope: dict, *, fields: dict, sender: str | None,
    wake: bool, message: str | None, expected_status: str, expected_revision: str,
) -> dict:
    request = {
        "version": 1, "task_id": task_id, "request_id": request_id, "scope": scope,
        "fields": fields, "sender": sender, "wake": wake,
        "message": steer_wake_message(task_id, message),
        "expected_status": expected_status, "expected_revision": expected_revision,
    }
    request["fingerprint"] = request_fingerprint(request)
    return validate_prepared_request(request)


def validate_prepared_request(value: object) -> dict:
    if (not isinstance(value, dict) or set(value) != _REQUEST_KEYS
            or type(value["version"]) is not int or value["version"] != 1
            or any(not isinstance(value[key], str) or not value[key] for key in (
                "task_id", "request_id", "message", "expected_status", "expected_revision",
            ))
            or not isinstance(value["fields"], dict) or type(value["wake"]) is not bool
            or (value["sender"] is not None and not isinstance(value["sender"], str))):
        raise SteeringError("invalid or unsupported prepared steering request; retain it for inspection")
    validate_steering_scope(value["scope"])
    if value["fingerprint"] != request_fingerprint(value):
        raise SteeringError("prepared steering request changed; do not reconstruct or retry it")
    # Detached finite JSON data: subsequent caller edits cannot change this invocation.
    return json.loads(json.dumps(value, allow_nan=False))
