"""Observational declaration of the running service's selected point owners."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from agent_index.store.source_layout import PointScope

    from .point_hints import PointChangeHints
    from .point_service import PointService


def point_scope_fingerprint(scope: PointScope) -> str:
    from agent_index.store.source_layout import _point_scope_json

    serialized = _point_scope_json(scope)
    assert serialized is not None
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def unavailable_point_service(
    reason: str, *, stopped: bool = False, failed: bool = False,
) -> dict[str, object]:
    return {
        "id": "agent-index.point-service", "version": 1, "paired": False,
        "required_admission_versions": [1],
        "state": "stopped" if stopped else "unavailable", "reason": reason,
        "failed": failed, "execution_admitted": False, "read_admitted": False,
    }


def point_service_contract(
    service: PointService | None, *, passive: bool, draining: bool,
) -> dict[str, object]:
    if passive:
        return unavailable_point_service("passive")
    if service is None:
        return unavailable_point_service("unselected")
    from .point_service import PointService

    if type(service) is not PointService:
        return unavailable_point_service("unselected")
    return service.contract(draining=draining)


def require_point_service(
    health: dict[str, object], scope: PointScope, *, operation: Literal["execute", "read"],
) -> None:
    """Validate the selected pair's optional read or execution requirement."""
    contract = health.get("point_service_contract")
    if isinstance(contract, dict):
        state = contract.get("state")
        versions = contract.get("hints_versions")
        semantics = (
            contract.get("id") == "agent-index.point-service"
            and type(contract.get("version")) is int and contract["version"] == 1
            and contract.get("paired") is True
            and contract.get("scope_fingerprint") == point_scope_fingerprint(scope)
            and contract.get("representation") == "point-components"
            and type(contract.get("representation_version")) is int
            and contract["representation_version"] == 1
            and contract.get("coverage") == "named-target-only"
            and isinstance(versions, list)
            and any(type(version) is int and version == 2 for version in versions)
            and contract.get("read_boundary") == "in-process-only"
            and type(contract.get("failed")) is bool
            and type(contract.get("execution_admitted")) is bool
            and type(contract.get("read_admitted")) is bool
            and (
                state in ("paused", "active") and contract["failed"] is False
                or state == "failed" and contract["failed"] is True
                or state == "draining"
            )
            and (state == "active" or contract["execution_admitted"] is False)
        )
        if semantics and (
            operation == "execute" and state == "active"
            and contract["execution_admitted"] is True and contract["failed"] is False
            or operation == "read" and state in ("paused", "active", "failed")
            and contract["read_admitted"] is True
        ):
            return
    raise RuntimeError(
        "Selected service does not advertise the required agent-index.point-service v1 "
        f"{operation} selection; select a capable service or wait for explicit "
        "retained-state inspection and activation",
    )


def _same_peer(
    payload: dict[str, object], installation_id: str, instance_token: str | None,
) -> bool:
    return (
        isinstance(installation_id, str)
        and isinstance(instance_token, str) and bool(instance_token)
        and payload.get("installationId") == installation_id
        and payload.get("instanceToken") == instance_token
    )


def require_point_admission(
    health: dict[str, object], scope: PointScope, *,
    installation_id: str, instance_token: str | None,
) -> None:
    """Require the selected peer's request/receipt expansion before submission."""
    if not _same_peer(health, installation_id, instance_token):
        raise RuntimeError(
            "Required point admission needs the selected installation and nonempty instance "
            "token; select the intended service without copying identity from health",
        )
    require_point_service(health, scope, operation="execute")
    contract = health["point_service_contract"]
    versions = contract.get("required_admission_versions") if isinstance(contract, dict) else None
    if not isinstance(versions, list) or not any(type(v) is int and v == 1 for v in versions):
        raise RuntimeError(
            "Selected service does not advertise required point admission v1; "
            "select a capable service without downgrading the request",
        )


def validate_point_receipt(
    result: dict[str, object], submitted: PointChangeHints, *,
    installation_id: str, instance_token: str | None,
) -> None:
    """Confirm typed admission, never application or safe replay after failure."""
    from .point_hints import PointChangeHints

    try:
        if type(result.get("accepted")) is not bool:
            raise ValueError("Invalid acceptance")
        if result["accepted"] is False:
            return
        admission = result["point_service_admission"]
        if (
            not _same_peer(result, installation_id, instance_token)
            or not isinstance(admission, dict)
            or admission.get("id") != "agent-index.point-service"
            or type(admission.get("version")) is not int or admission["version"] != 1
            or admission.get("scope_fingerprint") != point_scope_fingerprint(submitted.scope)
        ):
            raise ValueError("Invalid point admission")
        task = result["task"]
        if (
            not isinstance(task, dict)
            or task.get("source") != submitted.source or task.get("full") is not False
        ):
            raise ValueError("Invalid task scope")
        stored = PointChangeHints.from_dict(task["change_hints"])
        if stored.union(submitted) != stored:
            raise ValueError("Missing accepted targets or correlations")
    except (KeyError, TypeError, ValueError):
        raise RuntimeError(
            "Receiver did not confirm required point hints; acceptance outcome unknown; do not replay",
        ) from None
