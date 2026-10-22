"""Bounded, visible recovery when optional PR observation ownership disappears."""

from __future__ import annotations

from .delegated_waiter_contract import NOTIFICATIONS, RECOVERY_GRACE, validate_delegation


def probe_local_owner() -> dict | None:
    from .delegated_waiter_cli import owner_request
    from .procutil import agent_pull_requests_launch_prefix
    prefix = agent_pull_requests_launch_prefix()
    if prefix is None:
        return None
    try:
        health = owner_request(prefix, "health")
        if (
            not isinstance(health.get("capabilities"), list)
            or NOTIFICATIONS not in health["capabilities"]
            or health.get("persistence_error") or health.get("delivery_error")
        ):
            return None
        status = owner_request(prefix, "status")
        if status.get("error") or not isinstance(status.get("subscribers"), dict):
            return None
        return status["subscribers"]
    except (OSError, RuntimeError, ValueError):
        return None


def recovery_reason(waiter: dict, *, now: float, current_machine: str | None,
                    owner_status: dict | None) -> str | None:
    identity = waiter.get("delegation")
    try:
        validate_delegation(identity, armed=True)
    except ValueError:
        return "invalid delegated identity"
    if now >= float(waiter["created_at"]) + identity["timeout"] + RECOVERY_GRACE:
        return "PR watch delivery deadline expired"
    if waiter.get("host") != current_machine or not current_machine:
        return None
    if now < float(waiter["updated_at"]) + RECOVERY_GRACE:
        return None
    if owner_status is None:
        return "optional PR watch owner unavailable or incompatible"
    if identity["subscriber_id"] not in owner_status.get(
        f'{identity["repo"]}#{identity["number"]}', [],
    ):
        return "PR watch subscription missing"
    return None
