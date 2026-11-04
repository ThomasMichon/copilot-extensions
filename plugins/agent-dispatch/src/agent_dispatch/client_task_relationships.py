"""Never send new assignment syntax to an unqualified selected task owner."""

from __future__ import annotations

from typing import Protocol

from .client_errors import DispatchError


class _HealthOwner(Protocol):
    def health(self) -> dict: ...


def require_support(client: _HealthOwner) -> None:
    health = client.health()
    support = health.get("task_relationships") if isinstance(health, dict) else None
    if not (
        isinstance(support, dict)
        and type(support.get("version")) is int
        and support["version"] == 1
        and support.get("enabled") is True
        and support.get("assignment_request") == 2
        and support.get("authority") in {"local", "central"}
    ):
        raise DispatchError(
            409, "selected task owner cannot protect worktree assignment relationships"
        )
